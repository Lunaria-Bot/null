# cogs/submit.py
import json
import discord
from discord import app_commands
from discord.ext import commands
from cogs.utils import Utils
from config.settings import CHANNEL_SKIP, CHANNEL_NORMAL, CHANNEL_CARD_MAKER

QUEUE_CHOICES = [
    app_commands.Choice(name="Normal Queue", value="Normal"),
    app_commands.Choice(name="Skip queue", value="Skip"),
    app_commands.Choice(name="Card Maker", value="Card Maker"),
]

class SubmitView(discord.ui.View):
    def __init__(self, ctx_user_id: int, card_embed: dict):
        super().__init__(timeout=300)
        self.ctx_user_id = ctx_user_id
        self.card_embed = card_embed
        self.currency_value = None
        self.rate_value = None
        self.queue_value = None

    @discord.ui.button(label="Currency: BS/MS or PayPal", style=discord.ButtonStyle.gray, custom_id="currency_btn")
    async def currency_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx_user_id:
            return await interaction.response.send_message("Cette interaction ne t'appartient pas.", ephemeral=True)
        modal = CurrencyModal(self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Rate: ex 200:1", style=discord.ButtonStyle.gray, custom_id="rate_btn")
    async def rate_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx_user_id:
            return await interaction.response.send_message("Cette interaction ne t'appartient pas.", ephemeral=True)
        modal = RateModal(self)
        await interaction.response.send_modal(modal)

    @discord.ui.select(
        placeholder="Select your queue",
        options=[
            discord.SelectOption(label="Card Maker", value="Card Maker"),
            discord.SelectOption(label="Normal Queue", value="Normal"),
            discord.SelectOption(label="Skip queue", value="Skip"),
        ],
        custom_id="queue_select",
    )
    async def queue_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        if interaction.user.id != self.ctx_user_id:
            return await interaction.response.send_message("Cette interaction ne t'appartient pas.", ephemeral=True)
        self.queue_value = select.values[0]
        await interaction.response.send_message(f"Queue sélectionnée: {self.queue_value}", ephemeral=True)

    @discord.ui.button(label="Submit Auction", style=discord.ButtonStyle.green, custom_id="submit_btn")
    async def submit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx_user_id:
            return await interaction.response.send_message("Cette interaction ne t'appartient pas.", ephemeral=True)
        if not self.currency_value or not self.rate_value or not self.queue_value:
            return await interaction.response.send_message("Complète Currency, Rate et Queue avant de soumettre.", ephemeral=True)

        # Persist card + submission
        async with self._persist_submission(interaction) as submission_id:
            # Route to selected channel with embed
            channel_id = {
                "Skip": CHANNEL_SKIP,
                "Normal": CHANNEL_NORMAL,
                "Card Maker": CHANNEL_CARD_MAKER,
            }[self.queue_value]
            channel = interaction.client.get_channel(channel_id)
            if not channel:
                return await interaction.response.send_message("Channel introuvable, contacte un admin.", ephemeral=True)

            embed = self._build_submission_embed()
            msg = await channel.send(embed=embed, view=ReviewButtons(submission_id))
            # Optionally attach image if available
            await interaction.response.send_message("Ton enchère a été soumise pour revue.", ephemeral=True)

    def _build_submission_embed(self) -> discord.Embed:
        title = Utils.build_card_title(self.card_embed)
        rarity = Utils.detect_rarity_from_embed(self.card_embed) or "Unknown"
        version = self.card_embed.get("fields", [{}])[0].get("value", "") if self.card_embed.get("fields") else ""
        image_url = Utils.extract_image_url(self.card_embed)

        e = discord.Embed(title=f"{title} • {version}", description=f"Rarity: {rarity}")
        e.add_field(name="Currency", value=self.currency_value, inline=True)
        e.add_field(name="Rate", value=self.rate_value, inline=True)
        e.add_field(name="Queue", value=self.queue_value, inline=True)
        if image_url:
            e.set_image(url=image_url)
        return e

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _persist_submission(self, interaction: discord.Interaction):
        # Upsert user
        await interaction.client.pg.execute(
            "INSERT INTO users(user_id, username) VALUES($1, $2) ON CONFLICT (user_id) DO UPDATE SET username = EXCLUDED.username",
            interaction.user.id, interaction.user.name
        )
        # Store card
        title = Utils.build_card_title(self.card_embed)
        rarity = Utils.detect_rarity_from_embed(self.card_embed)
        image_url = Utils.extract_image_url(self.card_embed)
        card_id = await interaction.client.pg.fetchval(
            """INSERT INTO cards(owner_id, title, series, version, batch, rarity, image_url, raw_embed)
               VALUES($1,$2,$3,$4,$5,$6,$7,$8)
               RETURNING id""",
            interaction.user.id, title, self.card_embed.get("footer", {}).get("text"), None, None, rarity, image_url, json.dumps(self.card_embed)
        )
        submission_id = await interaction.client.pg.fetchval(
            """INSERT INTO submissions(user_id, card_id, currency, rate, queue, status)
               VALUES($1,$2,$3,$4,$5,'Pending')
               RETURNING id""",
            interaction.user.id, card_id, self.currency_value, self.rate_value, self.queue_value
        )
        try:
            yield submission_id
        finally:
            pass

class CurrencyModal(discord.ui.Modal, title="Set Currency"):
    def __init__(self, parent_view: SubmitView):
        super().__init__()
        self.parent_view = parent_view
        self.currency = discord.ui.TextInput(label="Currency (BS/MS or PayPal)", placeholder="BS/MS", max_length=32)
        self.add_item(self.currency)

    async def on_submit(self, interaction: discord.Interaction):
        self.parent_view.currency_value = self.currency.value.strip()
        # Turn button green by editing label
        for child in self.parent_view.children:
            if isinstance(child, discord.ui.Button) and child.custom_id == "currency_btn":
                child.label = f"Currency: {self.parent_view.currency_value}"
                child.style = discord.ButtonStyle.green
        await interaction.response.edit_message(view=self.parent_view)

class RateModal(discord.ui.Modal, title="Set Rate"):
    def __init__(self, parent_view: SubmitView):
        super().__init__()
        self.parent_view = parent_view
        self.rate = discord.ui.TextInput(label="Rate (ex 200:1)", placeholder="200:1", max_length=32)
        self.add_item(self.rate)

    async def on_submit(self, interaction: discord.Interaction):
        self.parent_view.rate_value = self.rate.value.strip()
        for child in self.parent_view.children:
            if isinstance(child, discord.ui.Button) and child.custom_id == "rate_btn":
                child.label = f"Rate: {self.parent_view.rate_value}"
                child.style = discord.ButtonStyle.green
        await interaction.response.edit_message(view=self.parent_view)

class ReviewButtons(discord.ui.View):
    def __init__(self, submission_id: int):
        super().__init__(timeout=None)  # persistent view
        self.submission_id = submission_id

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green, custom_id="review_accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.client.pg.execute(
            "UPDATE submissions SET status='Accepted', moderator_id=$1, updated_at=NOW() WHERE id=$2",
            interaction.user.id, self.submission_id
        )
        await interaction.response.send_message("Submission accepted.", ephemeral=True)

    @discord.ui.button(label="Deny", style=discord.ButtonStyle.red, custom_id="review_deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = DenyReasonModal(self.submission_id)
        await interaction.response.send_modal(modal)

class DenyReasonModal(discord.ui.Modal, title="Reason for denial"):
    def __init__(self, submission_id: int):
        super().__init__()
        self.submission_id = submission_id
        self.reason = discord.ui.TextInput(label="Reason", style=discord.TextStyle.paragraph, required=False, max_length=500)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        rec = await interaction.client.pg.fetchrow("SELECT user_id FROM submissions WHERE id=$1", self.submission_id)
        await interaction.client.pg.execute(
            "UPDATE submissions SET status='Denied', moderator_id=$1, moderator_reason=$2, updated_at=NOW() WHERE id=$3",
            interaction.user.id, self.reason.value.strip() or None, self.submission_id
        )
        # DM user
        try:
            user = await interaction.client.fetch_user(rec["user_id"])
            msg = "Your card has been refused"
            if self.reason.value.strip():
                msg += f": {self.reason.value.strip()}"
            await user.send(msg)
        except Exception:
            pass
        await interaction.response.send_message("Submission denied and user notified.", ephemeral=True)

class Submit(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="auction_submit", description="Submit your detected Mazoku card for auction review.")
    async def auction_submit(self, interaction: discord.Interaction):
        # Fetch embed data from Redis using owner_id (the user)
        raw = await self.bot.redis.get(f"mazoku:card:{interaction.user.id}")
        if not raw:
            return await interaction.response.send_message(
                "Aucune carte Mazoku détectée pour toi dans les 10 dernières minutes.", ephemeral=True
            )

        card_embed = json.loads(raw)
        view = SubmitView(interaction.user.id, card_embed)
        # DM with buttons
        try:
            await interaction.user.send(
                "Complete les informations de ton enchère ci-dessous.",
                view=view
            )
            await interaction.response.send_message("Regarde tes MP pour compléter la soumission.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("Je ne peux pas t'envoyer de MP. Active-les puis re-essaie.", ephemeral=True)
