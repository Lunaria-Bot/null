import discord
from discord.ext import commands
from discord import app_commands
from .utils import redis_json_load, queue_display_to_type

class Submit(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="auction-submit", description="Submit your Mazoku card to Auction.")
    async def auction_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        cached = await self.bot.redis.get(f"mazoku:card:{user_id}")
        if not cached:
            await interaction.response.send_message("Aucune carte Mazoku détectée récemment pour toi.", ephemeral=True)
            return

        data = redis_json_load(cached)
        title = data.get("title") or "Unknown Card"
        series = data.get("series") or "Unknown Series"
        version = data.get("version") or "?"
        batch_no = data.get("batch")
        rarity = data.get("rarity") or "COMMON"
        image_url = data.get("image_url")

        embed = discord.Embed(title=title, description="", color=discord.Color.blurple())
        embed.add_field(name="Series", value=series, inline=True)
        embed.add_field(name="Version", value=str(version), inline=True)
        embed.add_field(name="Batch", value=str(batch_no or "?"), inline=True)
        embed.add_field(name="Owned by", value=f"<@{user_id}>", inline=False)
        embed.set_footer(text=f"Rarity: {rarity}")
        if image_url:
            embed.set_image(url=image_url)

        view = ConfigView(self.bot, user_id, data)
        try:
            await interaction.user.send(content="Complete your auction submission:", embed=embed, view=view)
            await interaction.response.send_message("Je t’ai envoyé un message privé pour compléter la soumission.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("Active tes MP pour recevoir le formulaire.", ephemeral=True)

class ConfigView(discord.ui.View):
    def __init__(self, bot, user_id, data):
        super().__init__(timeout=600)
        self.bot = bot
        self.user_id = user_id
        self.data = data
        self.queue_display = None
        self.currency = None
        self.rate = None

        self.add_item(discord.ui.Select(
            placeholder="Select your queue",
            options=[
                discord.SelectOption(label="Normal queue", value="Normal queue"),
                discord.SelectOption(label="Skip queue", value="Skip queue"),
                discord.SelectOption(label="Card Maker", value="Card Maker"),
            ],
            custom_id="queue_select"
        ))
        self.add_item(discord.ui.Button(style=discord.ButtonStyle.secondary, label="Set Currency", custom_id="set_currency"))
        self.add_item(discord.ui.Button(style=discord.ButtonStyle.secondary, label="Set Rate", custom_id="set_rate"))
        self.add_item(discord.ui.Button(style=discord.ButtonStyle.primary, label="Submit Auction", custom_id="submit"))
        self.add_item(discord.ui.Button(style=discord.ButtonStyle.danger, label="Cancel", custom_id="cancel"))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    @discord.ui.select(custom_id="queue_select")
    async def on_queue_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.queue_display = interaction.data["values"][0]
        await interaction.response.send_message(f"Queue sélectionnée: {self.queue_display}", ephemeral=True)

    @discord.ui.button(custom_id="set_currency")
    async def on_currency(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SimpleInputModal(title="Currency", label="Enter currency (BS/MS or Paypal if CM)", key="currency")
        await interaction.response.send_modal(modal)
        await modal.wait()
        self.currency = modal.value
        button.style = discord.ButtonStyle.success
        button.label = f"Currency: {self.currency}"
        await interaction.followup.send("Currency défini.", ephemeral=True)

    @discord.ui.button(custom_id="set_rate")
    async def on_rate(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SimpleInputModal(title="Rate", label="Enter rate (ex 200:1)", key="rate")
        await interaction.response.send_modal(modal)
        await modal.wait()
        self.rate = modal.value
        button.style = discord.ButtonStyle.success
        button.label = f"Rate: {self.rate}"
        await interaction.followup.send("Rate défini.", ephemeral=True)

    @discord.ui.button(custom_id="submit")
    async def on_submit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not all([self.queue_display, self.currency, self.rate]):
            await interaction.response.send_message("Complète Queue, Currency et Rate avant de soumettre.", ephemeral=True)
            return
        qtype = queue_display_to_type(self.queue_display)

        rec = await self.bot.pg.fetchrow("""
            INSERT INTO auctions (user_id, series, version, batch_no, owner_id, rarity, queue_type, currency, rate, status, title, image_url)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'PENDING',$10,$11)
            RETURNING id
        """,
        self.user_id,
        self.data.get("series"),
        self.data.get("version"),
        self.data.get("batch"),
        self.data.get("owner_id"),
        (self.data.get("rarity") or "COMMON"),
        qtype,
        self.currency,
        self.rate,
        self.data.get("title"),
        self.data.get("image_url"))

        await interaction.response.send_message(f"Auction #{rec['id']} soumis. En attente de review staff.", ephemeral=True)

    @discord.ui.button(custom_id="cancel")
    async def on_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Soumission annulée.", ephemeral=True)
        self.stop()

class SimpleInputModal(discord.ui.Modal, title="Input"):
    def __init__(self, title: str, label: str, key: str):
        super().__init__(title=title)
        self.input = discord.ui.TextInput(label=label, required=True, max_length=100)
        self.add_item(self.input)
        self.key = key
        self.value = None

    async def on_submit(self, interaction: discord.Interaction):
        self.value = str(self.input.value).strip()
        await interaction.response.send_message(f"{self.key.capitalize()} reçu.", ephemeral=True)
        self.stop()

async def setup(bot: commands.Bot):
    await bot.add_cog(Submit(bot))
