import discord
from discord.ext import commands
from discord import app_commands
from .admin_guard import is_staff  # ✅ Import du décorateur staff

# IDs des canaux de log par queue
QUEUE_CHANNELS = {
    "NORMAL": 1304100031388844114,
    "SKIP": 1308385490931810434,
    "CARD_MAKER": 1395404596230361209,
}

# Channel de log global (actions staff)
LOG_CHANNEL_ID = 1424688704584286248


class StaffReview(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot


    async def log_submission(self, auction: dict):
        """Appelé depuis submit.py pour log la soumission dans le bon canal"""
        qtype = auction["queue_type"]
        channel_id = QUEUE_CHANNELS.get(qtype)
        if not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return

        embed = discord.Embed(
            title=f"Auction #{auction['id']} submitted",
            description="Pending review",
            color=discord.Color.orange()
        )
        embed.add_field(name="Seller", value=f"<@{auction['user_id']}>", inline=True)
        embed.add_field(name="Rarity", value=auction["rarity"], inline=True)
        embed.add_field(name="Queue", value=auction["queue_type"], inline=True)
        embed.add_field(name="Currency", value=auction["currency"], inline=True)
        embed.add_field(name="Rate", value=auction["rate"] or "—", inline=True)
        embed.add_field(name="Version", value=auction["version"] or "?", inline=True)
        if auction["image_url"]:
            embed.set_image(url=auction["image_url"])

        view = ReviewButtons(self.bot, auction["id"])
        await channel.send(embed=embed, view=view)


# --- Boutons persistants Accept / Deny / Fee Paid ---
class ReviewButtons(discord.ui.View):
    def __init__(self, bot, auction_id: int):
        super().__init__(timeout=None)  # ✅ View persistante
        self.bot = bot
        self.auction_id = auction_id

    @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.success, custom_id="review:accept")
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ReasonModal(self.bot, self.auction_id, "ACCEPT", interaction.message)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.danger, custom_id="review:deny")
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ReasonModal(self.bot, self.auction_id, "DENY", interaction.message)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="💰 Fee paid", style=discord.ButtonStyle.secondary, custom_id="review:fee")
    async def fee_paid(self, interaction: discord.Interaction, button: discord.ui.Button):
        msg = interaction.message
        if not msg.embeds:
            return await interaction.response.send_message("No embed to update.", ephemeral=True)

        embed = msg.embeds[0]
        embed.add_field(name="Fees", value="Paid", inline=False)

        button.style = discord.ButtonStyle.success
        button.disabled = True

        await msg.edit(embed=embed, view=self)
        await interaction.response.send_message("Marked as fees paid.", ephemeral=True)


# --- Modal pour raison facultative ---
class ReasonModal(discord.ui.Modal):
    def __init__(self, bot, auction_id: int, action: str, message: discord.Message):
        super().__init__(title=f"{action} Auction")
        self.bot = bot
        self.auction_id = auction_id
        self.action = action
        self.message = message
        self.reason = discord.ui.TextInput(label="Reason (optional)", required=False, style=discord.TextStyle.paragraph)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        reason = (self.reason.value or "No reason provided.").strip()
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)

        if not self.message.embeds:
            return await interaction.response.send_message("No embed to update.", ephemeral=True)
        embed = self.message.embeds[0]

        if self.action == "ACCEPT":
            await self.bot.pg.execute("UPDATE auctions SET status='READY' WHERE id=$1", self.auction_id)
            embed.description = f"✅ Submission approved\nReason: {reason}"
            embed.color = discord.Color.green()
            await self.message.edit(embed=embed, view=None)
            await interaction.response.send_message(f"Auction #{self.auction_id} approved.", ephemeral=True)

            if log_channel:
                log_embed = discord.Embed(
                    title="✅ Auction approved",
                    description=f"Auction #{self.auction_id} approved by {interaction.user.mention}",
                    color=discord.Color.green()
                )
                log_embed.add_field(name="Reason", value=reason, inline=False)
                await log_channel.send(embed=log_embed)

        elif self.action == "DENY":
            await self.bot.pg.execute("UPDATE auctions SET status='DENIED' WHERE id=$1", self.auction_id)
            embed.description = f"❌ Submission denied\nReason: {reason}"
            embed.color = discord.Color.red()
            await self.message.edit(embed=embed, view=None)
            await interaction.response.send_message(f"Auction #{self.auction_id} denied.", ephemeral=True)

            if log_channel:
                log_embed = discord.Embed(
                    title="❌ Auction denied",
                    description=f"Auction #{self.auction_id} denied by {interaction.user.mention}",
                    color=discord.Color.red()
                )
                log_embed.add_field(name="Reason", value=reason, inline=False)
                await log_channel.send(embed=log_embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(StaffReview(bot))

    # ✅ Réattacher les Views persistantes au redémarrage
    bot.add_view(ReviewButtons(bot, auction_id=0))
