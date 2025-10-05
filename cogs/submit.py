# cogs/submit.py
import json
import discord
from discord import app_commands
from discord.ext import commands
from cogs.utils import Utils
from config.settings import CHANNEL_SKIP, CHANNEL_NORMAL, CHANNEL_CARD_MAKER

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

        # Ici tu enverras l’embed dans le bon channel (déjà codé dans ta version précédente)
        await interaction.response.send_message("Ton enchère a été soumise pour revue.", ephemeral=True)


class CurrencyModal(discord.ui.Modal, title="Set Currency"):
    def __init__(self, parent_view: SubmitView):
        super().__init__()
        self.parent_view = parent_view
        self.currency = discord.ui.TextInput(label="Currency (BS/MS or PayPal)", placeholder="BS/MS", max_length=32)
        self.add_item(self.currency)

    async def on_submit(self, interaction: discord.Interaction):
        self.parent_view.currency_value = self.currency.value.strip()
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


class Submit(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="auction-submit", description="Submit your detected Mazoku card for auction review.")
    async def auction_submit(self, interaction: discord.Interaction):
        # Ici tu récupères la carte depuis Redis et tu envoies la vue
        await interaction.response.send_message("Commande /auction-submit détectée (test).", ephemeral=True)
