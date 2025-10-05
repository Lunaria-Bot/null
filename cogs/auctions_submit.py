import json
import discord
from discord.ext import commands
from discord import app_commands

from .auctions_core import GUILD_ID
from .auctions_utils import strip_discord_emojis

# --- Queue channel IDs ---
QUEUE_CHANNELS = {
    "skip": 1308385490931810434,
    "normal": 1304100031388844114,
    "cardmaker": 1395404596230361209,
}


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

        try:
            dm = await interaction.user.create_dm()
            view = self.AuctionSetupView(self.bot, interaction.user, card_embed)
            dm_msg = await dm.send(
                content="Please complete your auction submission:",
                embed=view.build_summary_embed(),
                view=view
            )
            view.message = dm_msg
            await interaction.response.send_message("📩 Check your DMs to finish your auction submission.", ephemeral=True)

        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I couldn't send you a DM. Please enable DMs from server members.",
                ephemeral=True
            )

    class AuctionSetupView(discord.ui.View):
        def __init__(self, bot: commands.Bot, user: discord.User, card_embed: discord.Embed):
            super().__init__(timeout=600)  # 10 minutes timeout
            self.bot = bot
            self.user = user
            self.card_embed = card_embed
            self.currency: str | None = None
            self.rate: str | None = None
            self.queue_choice: str | None = None
            self.message: discord.Message | None = None

        def build_summary_embed(self) -> discord.Embed:
            embed = self.card_embed.copy()
            summary = (
                f"📦 Queue: {self.queue_choice or 'Not set'}\n"
                f"💰 Currency: {self.currency or 'Not set'}\n"
                f"📊 Rate: {self.rate or 'Not set'}"
            )
            # Update or add the summary field inside the card embed
            found_index = None
            for i, f in enumerate(embed.fields):
                if f.name == "Auction Submission Summary":
                    found_index = i
                    break
            if found_index is not None:
                embed.set_field_at(found_index, name="Auction Submission Summary", value=summary, inline=False)
            else:
                embed.add_field(name="Auction Submission Summary", value=summary, inline=False)
            return embed

        async def refresh_view(self):
            # Dynamically adjust button labels/styles and update the embed
            for child in self.children:
                if isinstance(child, discord.ui.Button):
                    if child.custom_id == "currency":
                        child.label = f"Currency: {self.currency}" if self.currency else "Set Currency"
                        child.style = discord.ButtonStyle.green if self.currency else discord.ButtonStyle.blurple
                    elif child.custom_id == "rate":
                        child.label = f"Rate: {self.rate}" if self.rate else "Set Rate"
                        child.style = discord.ButtonStyle.green if self.rate else discord.ButtonStyle.gray
                    elif child.custom_id == "submit":
                        label = "Submit Auction"
                        if self.queue_choice:
                            label = f"Submit to {self.queue_choice.capitalize()} Queue"
                        child.label = label
                        child.disabled = not (self.queue_choice and self.currency and self.rate)

            if self.message:
                await self.message.edit(embed=self.build_summary_embed(), view=self)

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            if interaction.user != self.user:
                await interaction.response.send_message("Not your form.", ephemeral=True)
                return False
            return True

        @discord.ui.select(
            placeholder="Choose a queue",
            options=[
                discord.SelectOption(label="Card Maker Queue", value="cardmaker"),
                discord.SelectOption(label="Normal Queue", value="normal"),
                discord.SelectOption(label="Skip Queue", value="skip"),
            ]
        )
        async def select_queue(self, interaction: discord.Interaction, select: discord.ui.Select):
            if not await self.interaction_check(interaction):
                return
            self.queue_choice = select.values[0]
            await interaction.response.defer(ephemeral=True)
            await interaction.followup.send(f"📂 Queue set to **{self.queue_choice}**", ephemeral=True)
            await self.refresh_view()

        @discord.ui.button(label="Set Currency", style=discord.ButtonStyle.blurple, custom_id="currency")
        async def set_currency(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not await self.interaction_check(interaction):
                return
            await interaction.response.send_modal(AuctionsSubmit.CurrencyModal(self))

        @discord.ui.button(label="Set Rate", style=discord.ButtonStyle.gray, custom_id="rate")
        async def set_rate(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not await self.interaction_check(interaction):
                return
            await interaction.response.send_modal(AuctionsSubmit.RateModal(self))

        @discord.ui.button(label="Submit Auction", style=discord.ButtonStyle.green, custom_id="submit", disabled=True)
        async def submit(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not await self.interaction_check(interaction):
                return
            if not self.queue_choice or not self.currency or not self.rate:
                return await interaction.response.send_message("❌ Please fill all fields.", ephemeral=True)

            core = self.bot.get_cog("AuctionsCore")
            if core is None or core.pg_pool is None:
                return await interaction.response.send_message("❌ Core not ready.", ephemeral=True)

            try:
                await interaction.response.defer(ephemeral=True)

                # Insert submission in DB
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

                # Send to queue channel for staff review
                queue_channel_id = QUEUE_CHANNELS.get(self.queue_choice)
                queue_channel = self.bot.get_channel(queue_channel_id)
                if not queue_channel:
                    return await interaction.followup.send("❌ Queue channel not found.", ephemeral=True)

                from .auctions_staff import StaffReviewView
                msg = await queue_channel.send(
                    embed=self.card_embed,
                    view=StaffReviewView(self.bot, submission_id, self.queue_choice)
                )
                thread = await msg.create_thread(name=f"Auction #{submission_id} – {self.card_embed.title or 'Card'}")

                # Store queue message references in DB
                async with core.pg_pool.acquire() as conn:
                    await conn.execute(
                        "UPDATE submissions SET queue_message_id=$1, queue_channel_id=$2, queue_thread_id=$3 WHERE id=$4",
                        msg.id, queue_channel.id, thread.id, submission_id
                    )

                await interaction.followup.send("✅ Submission sent to queue successfully!", ephemeral=True)

                # Remove buttons after submission
                if self.message:
                    await self.message.edit(embed=self.build_summary_embed(), view=None)
                self.stop()

            except Exception as e:
                print("❌ Error in Submit Auction:", e)
                if not interaction.response.is_done():
                    await interaction.response.send_message("❌ An error occurred.", ephemeral=True)

        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red, custom_id="cancel")
        async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
            if not await self.interaction_check(interaction):
                return
            await interaction.response.send_message("❌ Submission cancelled.", ephemeral=True)
            if self.message:
                await self.message.edit(embed=self.build_summary_embed(), view=None)
            self.stop()

        async def on_timeout(self):
            if self.message:
                await self.message.edit(content="⏰ Submission timed out.", view=None)

    class CurrencyModal(discord.ui.Modal, title="Set Currency"):
        currency = discord.ui.TextInput(label="Currency", required=True)

        def __init__(self, parent_view: "AuctionsSubmit.AuctionSetupView"):
            super().__init__()
            self.parent_view = parent_view

        async def on_submit(self, interaction: discord.Interaction):
            try:
                await interaction.response.defer(ephemeral=True)
                self.parent_view.currency = self.currency.value
                await interaction.followup.send(f"💰 Currency set to **{self.currency.value}**", ephemeral=True)
                await self.parent_view.refresh_view()
            except Exception as e:
                print("❌ Error in CurrencyModal:", e)
                if not interaction.response.is_done():
                    await interaction.response.send_message("❌ Error setting currency.", ephemeral=True)

    class RateModal(discord.ui.Modal, title="Set Auction Rate"):
        rate = discord.ui.TextInput(label="Rate (e.g. 200:1)", required=True)

        def __init__(self, parent_view: "AuctionsSubmit.AuctionSetupView"):
            super().__init__()
            self.parent_view = parent_view

        async def on_submit(self, interaction: discord.Interaction):
            try:
                await interaction.response.defer(ephemeral=True)
                self.parent_view.rate = self.rate.value
                await interaction.followup.send(f"📊 Rate set to **{self.rate.value}**", ephemeral=True)
                await self.parent_view.refresh_view()
            except Exception as e:
                print("❌ Error in RateModal:", e)
                if not interaction.response.is_done():
                    await interaction.response.send_message("❌ Error setting rate.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AuctionsSubmit(bot))
