import json
import discord
from discord.ext import commands
from discord import app_commands

from .auctions_core import GUILD_ID
from .auctions_utils import (
    RARITY_CHANNELS,
    CARDMAKER_CHANNEL_ID,
    extract_first_emoji_id,
    strip_discord_emojis,
)

class AuctionsSubmit(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="auction-submit", description="Submit your Mazoku card for auction")
    @app_commands.guilds(discord.Object(id=GUILD_ID))
    async def auction_submit(self, interaction: discord.Interaction):
        core = self.bot.get_cog("AuctionsCore")
        if core is None or core.redis is None:
            return await interaction.response.send_message("❌ Core not ready.", ephemeral=True)

        data = await core.redis.get(f"mazoku:card:{interaction.user.id}")
        if not data:
            return await interaction.response.send_message(
                "❌ No Mazoku card detected. Use `/inventory` with Mazoku first.",
                ephemeral=True
            )
        card_embed = discord.Embed.from_dict(json.loads(data))
        if card_embed.title:
            card_embed.title = strip_discord_emojis(card_embed.title)

        dm = await interaction.user.create_dm()
        await dm.send(
            "Please complete your auction submission:",
            embed=card_embed,
            view=self.AuctionSetupView(self.bot, interaction.user, card_embed)
        )
        await interaction.response.send_message("📩 Check your DMs to finish your auction submission.", ephemeral=True)

    class AuctionSetupView(discord.ui.View):
        def __init__(self, bot: commands.Bot, user: discord.User, card_embed: discord.Embed):
            super().__init__(timeout=600)
            self.bot = bot
            self.user = user
            self.card_embed = card_embed
            self.currency: str | None = None
            self.rate: str | None = None
            self.queue_choice: str | None = None

        @discord.ui.select(
            placeholder="Choose a queue",
            options=[
                discord.SelectOption(label="Card Maker Queue", value="cardmaker"),
                discord.SelectOption(label="Normal Queue", value="normal"),
                discord.SelectOption(label="Skip Queue", value="skip"),
            ]
        )
        async def select_queue(self, interaction: discord.Interaction, select: discord.ui.Select):
            if interaction.user != self.user:
                return await interaction.response.send_message("Not your form.", ephemeral=True)
            self.queue_choice = select.values[0]
            await interaction.response.edit_message(view=self)

        @discord.ui.button(label="Set Currency", style=discord.ButtonStyle.blurple)
        async def set_currency(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user != self.user:
                return await interaction.response.send_message("Not your form.", ephemeral=True)
            await interaction.response.send_modal(AuctionsSubmit.CurrencyModal(self))

        @discord.ui.button(label="Set Rate", style=discord.ButtonStyle.gray)
        async def set_rate(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user != self.user:
                return await interaction.response.send_message("Not your form.", ephemeral=True)
            await interaction.response.send_modal(AuctionsSubmit.RateModal(self))

        @discord.ui.button(label="Submit Auction", style=discord.ButtonStyle.green)
        async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user != self.user:
                return await interaction.response.send_message("Not your form.", ephemeral=True)
            if not self.queue_choice or not self.currency or not self.rate:
                return await interaction.response.send_message("❌ Please fill all fields.", ephemeral=True)

            core = self.bot.get_cog("AuctionsCore")
            if core is None or core.pg_pool is None:
                return await interaction.response.send_message("❌ Core not ready.", ephemeral=True)

            # Insert into DB
            async with core.pg_pool.acquire() as conn:
                row = await conn.fetchrow(
                    "INSERT INTO submissions(user_id, card, currency, rate, queue, status) "
                    "VALUES($1,$2::jsonb,$3,$4,$5,'submitted') RETURNING id",
                    self.user.id,
                    json.dumps(self.card_embed.to_dict()),
                    self.currency,
                    self.rate,
                    self.queue_choice
                )
                submission_id = row["id"]

            # Determine target channel by rarity or cardmaker queue
            if self.queue_choice == "cardmaker":
                channel_id = CARDMAKER_CHANNEL_ID
            else:
                rarity_id = extract_first_emoji_id(self.card_embed.description)
                channel_id = RARITY_CHANNELS.get(rarity_id)

            channel = self.bot.get_channel(channel_id) if channel_id else None
            if not channel:
                return await interaction.response.send_message("❌ Target channel not found (rarity unknown).", ephemeral=True)

            # Post to queue and create thread
            from .auctions_staff import StaffReviewView  # local import to avoid circular at load time
            msg = await channel.send(
                embed=self.card_embed,
                view=StaffReviewView(self.bot, submission_id, self.queue_choice)
            )
            thread = await msg.create_thread(name=f"Auction #{submission_id} – {self.card_embed.title or 'Card'}")

            # Save message/thread refs
            async with core.pg_pool.acquire() as conn:
                await conn.execute(
                    "UPDATE submissions SET queue_message_id=$1, queue_channel_id=$2, queue_thread_id=$3 WHERE id=$4",
                    msg.id, channel.id, thread.id, submission_id
                )

            await interaction.response.send_message("✅ Submission sent.", ephemeral=True)
            self.stop()

    class CurrencyModal(discord.ui.Modal, title="Set Currency"):
        currency = discord.ui.TextInput(label="Currency", required=True)

        def __init__(self, parent_view: "AuctionsSubmit.AuctionSetupView"):
            super().__init__()
            self.parent_view = parent_view

        async def on_submit(self, interaction: discord.Interaction):
            self.parent_view.currency = self.currency.value
            for child in self.parent_view.children:
                if isinstance(child, discord.ui.Button) and child.label.startswith("Set Currency"):
                    child.label = f"Currency: {self.currency.value}"
            await interaction.response.edit_message(view=self.parent_view)

    class RateModal(discord.ui.Modal, title="Set Auction Rate"):
        rate = discord.ui.TextInput(label="Rate (e.g. 175:1)", required=True)

        def __init__(self, parent_view: "AuctionsSubmit.AuctionSetupView"):
            super().__init__()
            self.parent_view = parent_view

        async def on_submit(self, interaction: discord.Interaction):
            self.parent_view.rate = self.rate.value
            for child in self.parent_view.children:
                if isinstance(child, discord.ui.Button) and child.label.startswith("Set Rate"):
                    child.label = f"Rate: {self.rate.value}"
            await interaction.response.edit_message(view=self.parent_view)


async def setup(bot: commands.Bot):
    await bot.add_cog(AuctionsSubmit(bot))
