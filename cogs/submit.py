# cogs/submit.py
import json
import discord
from discord import app_commands
from discord.ext import commands
from config import settings


class SubmitView(discord.ui.View):
    def __init__(self, ctx_user_id: int, card_embed: dict, bot):
        super().__init__(timeout=300)
        self.ctx_user_id = ctx_user_id
        self.card_embed = card_embed
        self.bot = bot
        self.currency_value = None
        self.rate_value = None
        self.queue_value = None

    @discord.ui.button(label="Currency: ?", style=discord.ButtonStyle.gray, custom_id="currency_btn")
    async def currency_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx_user_id:
            return await interaction.response.send_message("❌ Cette interaction ne t'appartient pas.", ephemeral=True)
        modal = CurrencyModal(self)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Rate: ?", style=discord.ButtonStyle.gray, custom_id="rate_btn")
    async def rate_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx_user_id:
            return await interaction.response.send_message("❌ Cette interaction ne t'appartient pas.", ephemeral=True)
        modal = RateModal(self)
        await interaction.response.send_modal(modal)

    @discord.ui.select(
        placeholder="Choisis ta file",
        options=[
            discord.SelectOption(label="Card Maker", value="Card Maker"),
            discord.SelectOption(label="Normal Queue", value="Normal"),
            discord.SelectOption(label="Skip Queue", value="Skip"),
        ],
        custom_id="queue_select",
    )
    async def queue_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        if interaction.user.id != self.ctx_user_id:
            return await interaction.response.send_message("❌ Cette interaction ne t'appartient pas.", ephemeral=True)
        self.queue_value = select.values[0]
        await interaction.response.send_message(f"✅ Queue sélectionnée: {self.queue_value}", ephemeral=True)

    @discord.ui.button(label="Submit Auction", style=discord.ButtonStyle.green, custom_id="submit_btn")
    async def submit_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.ctx_user_id:
            return await interaction.response.send_message("❌ Cette interaction ne t'appartient pas.", ephemeral=True)
        if not self.currency_value or not self.rate_value or not self.queue_value:
            return await interaction.response.send_message("⚠️ Complète Currency, Rate et Queue avant de soumettre.", ephemeral=True)

        # Construire l’embed final
        embed = discord.Embed(
            title=self.card_embed.get("title", "Carte détectée"),
            description=f"Série: {self.card_embed.get('series')} | Version: {self.card_embed.get('version')}",
            color=discord.Color.green()
        )
        embed.set_image(url=self.card_embed.get("image_url"))
        embed.add_field(name="Currency", value=self.currency_value, inline=True)
        embed.add_field(name="Rate", value=self.rate_value, inline=True)
        embed.add_field(name="Queue", value=self.queue_value, inline=True)
        embed.set_footer(text=f"Soumise par {interaction.user.display_name}")

        # Choisir le salon cible
        if self.queue_value == "Skip":
            channel_id = settings.CHANNEL_SKIP
        elif self.queue_value == "Card Maker":
            channel_id = settings.CHANNEL_CARD_MAKER
        else:
            channel_id = settings.CHANNEL_NORMAL

        channel = interaction.client.get_channel(channel_id)
        if channel:
            await channel.send(embed=embed)
            await interaction.response.send_message("✅ Ton enchère a été soumise pour revue.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ Impossible de trouver le salon cible.", ephemeral=True)


class CurrencyModal(discord.ui.Modal, title="Set Currency"):
    def __init__(self, parent_view: SubmitView):
        super().__init__()
        self.parent_view = parent_view
        self.currency = discord.ui.TextInput(label="Currency (BS/MS ou PayPal)", placeholder="BS/MS", max_length=32)
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

    @app_commands.command(name="auction-submit", description="Soumettre ta carte Mazoku pour revue d'enchère.")
    async def auction_submit(self, interaction: discord.Interaction):
        # Récupérer la carte depuis Redis
        card_data = await self.bot.redis.get(f"detected_card:{interaction.user.id}")
        if not card_data:
            return await interaction.response.send_message(
                "⚠️ Aucune carte détectée pour toi. Utilise `/inventory` ou la commande de détection avant.",
                ephemeral=True
            )

        card_embed = json.loads(card_data)

        # Embed de prévisualisation
        embed = discord.Embed(
            title=card_embed.get("title", "Carte détectée"),
            description=f"Série: {card_embed.get('series')} | Version: {card_embed.get('version')}",
            color=discord.Color.blurple()
        )
        embed.set_image(url=card_embed.get("image_url"))
        embed.set_footer(text=f"Soumise par {interaction.user.display_name}")

        # Vue interactive
        view = SubmitView(interaction.user.id, card_embed, self.bot)
        await interaction.response.send_message(
            "Complète les infos ci-dessous puis clique sur **Submit Auction** :",
            embed=embed,
            view=view,
            ephemeral=True
        )
