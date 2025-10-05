import discord
from discord.ext import commands
from discord import app_commands
from .utils import parse_cached_embed_str, queue_display_to_type

class Submit(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="auction-submit", description="Submit your Mazoku card to Auction.")
    async def auction_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        cached = await self.bot.redis.get(f"mazoku:card:{user_id}")
        if not cached:
            await interaction.response.send_message(
                "Aucune carte Mazoku détectée récemment pour toi. Réagis à ta carte dans le canal Mazoku pour l’actualiser.",
                ephemeral=True
            )
            return

        data = parse_cached_embed_str(cached)
        title = data.get("title") or "Unknown Card"
        desc = data.get("description") or ""
        rarity = data.get("parsed_rarity") or "COMMON"

        embed = discord.Embed(title=title, description=desc, color=discord.Color.blurple())
        embed.add_field(name="Series", value="Alien Stage", inline=True)  # Ajuste si disponible
        embed.add_field(name="Version", value="3", inline=True)           # Ajuste si disponible
        embed.add_field(name="Batch", value="?", inline=True)
        embed.add_field(name="Owned by", value=f"<@{user_id}>", inline=False)
        embed.set_footer(text=f"Rarity: {rarity}")

        # Components
        queue_select = discord.ui.Select(
            placeholder="Select your queue",
            options=[
                discord.SelectOption(label="Normal queue", value="Normal queue"),
                discord.SelectOption(label="Skip queue", value="Skip queue"),
                discord.SelectOption(label="Card Maker", value="Card Maker"),
            ]
        )

        currency_btn = discord.ui.Button(style=discord.ButtonStyle.secondary, label="Set Currency")
        rate_btn = discord.ui.Button(style=discord.ButtonStyle.secondary, label="Set Rate")
        submit_btn = discord.ui.Button(style=discord.ButtonStyle.primary, label="Submit Auction")
        cancel_btn = discord.ui.Button(style=discord.ButtonStyle.danger, label="Cancel")

        view = ConfigView(self.bot, queue_select, currency_btn, rate_btn, submit_btn, cancel_btn, user_id, data)
        try:
            await interaction.user.send(content="Complete your auction submission:", embed=embed, view=view)
            await interaction.response.send_message("Je t’ai envoyé un message privé pour compléter la soumission.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("Active tes MP pour que je puisse t’envoyer le formulaire.", ephemeral=True)

class ConfigView(discord.ui.View):
    def __init__(self, bot, queue_select, currency_btn, rate_btn, submit_btn, cancel_btn, user_id, data):
        super().__init__(timeout=600)
        self.bot = bot
        self.user_id = user_id
        self.data = data
        self.queue_display = None
        self.currency = None
        self.rate = None

        queue_select.callback = self.on_queue_select
        currency_btn.callback = self.on_currency
        rate_btn.callback = self.on_rate
        submit_btn.callback = self.on_submit
        cancel_btn.callback = self.on_cancel

        self.add_item(queue_select)
        self.add_item(currency_btn)
        self.add_item(rate_btn)
        self.add_item(submit_btn)
        self.add_item(cancel_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def on_queue_select(self, interaction: discord.Interaction):
        self.queue_display = interaction.data["values"][0]
        await interaction.response.send_message(f"Queue sélectionnée: {self.queue_display}", ephemeral=True)

    async def on_currency(self, interaction: discord.Interaction):
        modal = SimpleInputModal(title="Currency", label="Enter currency (BS/MS or Paypal if CM)", key="currency")
        await interaction.response.send_modal(modal)
        await modal.wait()
        self.currency = modal.value
        # Turn button green
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.label == "Set Currency":
                item.style = discord.ButtonStyle.success
                item.label = f"Currency: {self.currency}"
        await interaction.followup.send("Currency défini.", ephemeral=True)

    async def on_rate(self, interaction: discord.Interaction):
        modal = SimpleInputModal(title="Rate", label="Enter rate (ex 200:1)", key="rate")
        await interaction.response.send_modal(modal)
        await modal.wait()
        self.rate = modal.value
        for item in self.children:
            if isinstance(item, discord.ui.Button) and item.label.startswith("Set Rate"):
                item.style = discord.ButtonStyle.success
                item.label = f"Rate: {self.rate}"
        await interaction.followup.send("Rate défini.", ephemeral=True)

    async def on_submit(self, interaction: discord.Interaction):
        if not all([self.queue_display, self.currency, self.rate]):
            await interaction.response.send_message("Complète Queue, Currency et Rate avant de soumettre.", ephemeral=True)
            return
        qtype = queue_display_to_type(self.queue_display)
        rarity = self.data.get("parsed_rarity") or "COMMON"

        # Insert auction
        rec = await self.bot.pg.fetchrow("""
            INSERT INTO auctions (user_id, series, version, owner_id, rarity, queue_type, currency, rate, status)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'PENDING') RETURNING id
        """, self.user_id, "Alien Stage", "3", self.user_id, rarity, qtype, self.currency, self.rate)
        auction_id = rec["id"]

        await interaction.response.send_message(f"Auction #{auction_id} soumis. En attente de review staff.", ephemeral=True)

    async def on_cancel(self, interaction: discord.Interaction):
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
