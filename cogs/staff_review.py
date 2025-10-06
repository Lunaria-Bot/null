import discord
from discord.ext import commands
from discord import app_commands

class StaffReview(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="auction-list", description="List auctions pending review.")
    async def auction_list(self, interaction: discord.Interaction):
        rows = await self.bot.pg.fetch("""
            SELECT id, user_id, rarity, queue_type, currency, rate, series, version, title, image_url
            FROM auctions WHERE status='PENDING' ORDER BY id ASC
        """)
        if not rows:
            await interaction.response.send_message("No auctions pending.", ephemeral=True)
            return

        embed = discord.Embed(title="Pending Auctions", color=discord.Color.orange())
        for r in rows:
            name = r["title"] or f"{r['series']} v{r['version']}"
            embed.add_field(
                name=f"#{r['id']} — {name}",
                value=f"User: <@{r['user_id']}> | {r['rarity']} | {r['queue_type']} | {r['currency']} | {r['rate']}",
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="auction-review", description="Open staff review controls.")
    @app_commands.default_permissions(manage_messages=True)
    async def auction_review(self, interaction: discord.Interaction):
        row = await self.bot.pg.fetchrow("""
            SELECT id, user_id, rarity, queue_type, currency, rate, series, version, title, image_url
            FROM auctions WHERE status='PENDING' ORDER BY id ASC LIMIT 1
        """)
        if not row:
            await interaction.response.send_message("Nothing to review.", ephemeral=True)
            return

        # Embed amélioré
        embed = discord.Embed(
            title=row["title"] or f"Auction #{row['id']}",
            description="Review Auction",
            color=discord.Color.blurple()
        )
        embed.add_field(name="Seller", value=f"<@{row['user_id']}>", inline=True)
        embed.add_field(name="Rarity", value=row["rarity"], inline=True)
        embed.add_field(name="Queue", value=row["queue_type"], inline=True)
        embed.add_field(name="Preference", value=row["currency"], inline=True)
        embed.add_field(name="Rate", value=row["rate"], inline=True)
        embed.add_field(name="Version", value=row["version"] or "?", inline=True)
        if row["image_url"]:
            embed.set_image(url=row["image_url"])

        await interaction.response.send_message(
            embed=embed,
            view=ReviewView(self.bot, row["id"], row["user_id"]),
            ephemeral=True
        )


class ReviewView(discord.ui.View):
    def __init__(self, bot, auction_id: int, user_id: int):
        super().__init__(timeout=600)
        self.bot = bot
        self.auction_id = auction_id
        self.user_id = user_id

    @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.bot.pg.execute(
            "INSERT INTO reviews (auction_id, stage, reviewer_id, decision) VALUES ($1,$2,$3,$4)",
            self.auction_id, 1, interaction.user.id, "accept"
        )
        await self.bot.pg.execute("UPDATE auctions SET status='READY' WHERE id=$1", self.auction_id)
        await interaction.response.send_message(f"Auction #{self.auction_id} accepted.", ephemeral=True)

        # Log accept
        await self.log_action(interaction, "Auction accepted", discord.Color.green())

        try:
            user = await self.bot.fetch_user(self.user_id)
            await user.send("✅ Your card has been accepted and moved to the waiting list.")
        except Exception:
            pass
        self.stop()

    @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ReasonModal(self.bot, self.auction_id, self.user_id)
        await interaction.response.send_modal(modal)
        self.stop()

    async def log_action(self, interaction: discord.Interaction, title: str, color: discord.Color, reason: str = None):
        auction = await self.bot.pg.fetchrow("SELECT * FROM auctions WHERE id=$1", self.auction_id)
        if not auction:
            return
        guild = self.bot.get_guild(self.bot.guild_id)
        log_channel = guild.get_channel(self.bot.log_channel_id) if guild else None
        if not log_channel:
            return

        embed = discord.Embed(title=title, color=color)
        if reason:
            embed.description = f"Reason: {reason}"
        embed.add_field(name="Name of the card", value=auction["title"] or f"Auction #{auction['id']}", inline=True)
        embed.add_field(name="Version", value=auction.get("version") or "?", inline=True)
        embed.add_field(name="Queue", value=auction.get("queue_type") or "?", inline=True)
        embed.add_field(name="Seller", value=f"<@{auction['user_id']}>", inline=True)
        embed.add_field(name="Rarity", value=auction.get("rarity") or "?", inline=True)
        embed.add_field(name="Currency", value=auction.get("currency") or "N/A", inline=True)
        embed.add_field(name="Rate", value=auction.get("rate") or "N/A", inline=True)
        if auction.get("image_url"):
            embed.set_image(url=auction["image_url"])
        await log_channel.send(embed=embed)


class ReasonModal(discord.ui.Modal, title="Reason"):
    def __init__(self, bot, auction_id: int, user_id: int):
        super().__init__(title="Reason")
        self.bot = bot
        self.auction_id = auction_id
        self.user_id = user_id
        self.input = discord.ui.TextInput(label="Enter reason", required=False, max_length=200)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        reason = str(self.input.value).strip() or "No reason provided."
        await self.bot.pg.execute(
            "INSERT INTO reviews (auction_id, stage, reviewer_id, decision, reason) VALUES ($1,$2,$3,$4,$5)",
            self.auction_id, 1, interaction.user.id, "deny", reason
        )
        await self.bot.pg.execute("UPDATE auctions SET status='DENIED' WHERE id=$1", self.auction_id)
        await interaction.response.send_message(f"Auction #{self.auction_id} denied.", ephemeral=True)

        # Log deny
        view = ReviewView(self.bot, self.auction_id, self.user_id)
        await view.log_action(interaction, "Auction denied", discord.Color.red(), reason)

        try:
            user = await self.bot.fetch_user(self.user_id)
            await user.send(f"❌ Your card has been refused.\nReason: {reason}")
        except Exception:
            pass
        self.stop()


async def setup(bot: commands.Bot):
    await bot.add_cog(StaffReview(bot))
