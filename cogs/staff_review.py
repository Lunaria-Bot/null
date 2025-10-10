import discord
from discord.ext import commands

# IDs des canaux de log par queue
QUEUE_CHANNELS = {
    "NORMAL": 1304100031388844114,
    "SKIP": 1308385490931810434,
    "CARD_MAKER": 1395404596230361209,
}

LOG_CHANNEL_ID = 1424688704584286248


class StaffReview(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def log_submission(self, auction: dict):
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
        embed.add_field(name="Status", value="PENDING ⏳", inline=True)  # ✅ Ajout
        if auction["image_url"]:
            embed.set_image(url=auction["image_url"])

        view = ReviewButtons(self.bot, auction["id"])
        await channel.send(embed=embed, view=view)


class ReviewButtons(discord.ui.View):
    def __init__(self, bot, auction_id: int):
        super().__init__(timeout=None)
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
            self._update_status(embed, "READY ✅")

        elif self.action == "DENY":
            await self.bot.pg.execute("UPDATE auctions SET status='DENIED' WHERE id=$1", self.auction_id)
            embed.description = f"❌ Submission denied\nReason: {reason}"
            embed.color = discord.Color.red()
            self._update_status(embed, "DENIED ❌")

        await self.message.edit(embed=embed, view=None)
        await interaction.response.send_message(f"Auction #{self.auction_id} {self.action.lower()}ed.", ephemeral=True)

        if log_channel:
            log_embed = discord.Embed(
                title=f"{'✅' if self.action=='ACCEPT' else '❌'} Auction {self.action.lower()}ed",
                description=f"Auction #{self.auction_id} {self.action.lower()}ed by {interaction.user.mention}",
                color=embed.color
            )
            log_embed.add_field(name="Reason", value=reason, inline=False)
            await log_channel.send(embed=log_embed)

    def _update_status(self, embed: discord.Embed, status: str):
        for i, field in enumerate(embed.fields):
            if field.name == "Status":
                embed.set_field_at(i, name="Status", value=status, inline=True)
                return
        embed.add_field(name="Status", value=status, inline=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(StaffReview(bot))

    # ✅ Réattacher toutes les Views persistantes au redémarrage
    rows = await bot.pg.fetch("SELECT id FROM auctions WHERE status='PENDING'")
    for row in rows:
        bot.add_view(ReviewButtons(bot, row["id"]))
