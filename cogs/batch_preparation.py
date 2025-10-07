import discord
from discord.ext import commands
from discord import app_commands
import datetime
from .auction_core import get_or_create_today_batch

# Forums d'enchères à scanner pour /auction-lock
AUCTION_FORUMS = [
    1304507540645740666,  # Common
    1304507516423766098,  # Rare
    1304536219677626442,  # SR
    1304502617472503908,  # SSR
    1304052056109350922,  # UR
    1395405043431116871,  # CM
]

# Channel de log
LOG_CHANNEL_ID = 1424688704584286248


class BatchPreparation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # -------------------------
    # BATCH COMMANDS
    # -------------------------

    @app_commands.command(name="batch-new", description="Create or get today's batch.")
    async def batch_new(self, interaction: discord.Interaction):
        bid = await get_or_create_today_batch(self.bot.pg)
        await interaction.response.send_message(f"Today's batch: #{bid}", ephemeral=True)

    @app_commands.command(name="batch-clear", description="Clear items in today's batch.")
    @app_commands.default_permissions(manage_messages=True)
    async def batch_clear(self, interaction: discord.Interaction):
        bid = await get_or_create_today_batch(self.bot.pg)
        await self.bot.pg.execute("DELETE FROM batch_items WHERE batch_id=$1", bid)
        await interaction.response.send_message(f"Batch #{bid} cleared.", ephemeral=True)

    @app_commands.command(
        name="batch-fill",
        description="Fill batch with READY auctions (15 Normal max, unlimited Skip & CardMaker)."
    )
    @app_commands.default_permissions(manage_messages=True)
    async def batch_fill(self, interaction: discord.Interaction):
        bid = await get_or_create_today_batch(self.bot.pg)

        normals = await self.bot.pg.fetch("""
            SELECT id FROM auctions
            WHERE status='READY' AND queue_type='NORMAL'
            ORDER BY id ASC
            LIMIT 15
        """)

        skips = await self.bot.pg.fetch("""
            SELECT id FROM auctions
            WHERE status='READY' AND queue_type='SKIP'
            ORDER BY id ASC
        """)

        cms = await self.bot.pg.fetch("""
            SELECT id FROM auctions
            WHERE status='READY' AND queue_type='CARD_MAKER'
            ORDER BY id ASC
        """)

        position = 1
        for row in normals + skips + cms:
            await self.bot.pg.execute(
                "INSERT INTO batch_items (batch_id, auction_id, position) VALUES ($1,$2,$3)",
                bid, row["id"], position
            )
            position += 1

        await interaction.response.send_message(
            f"Batch #{bid} filled with {position-1} items "
            f"({len(normals)} Normal, {len(skips)} Skip, {len(cms)} CardMaker).",
            ephemeral=True
        )

    @app_commands.command(name="batch-view", description="View the cards in today's batch (with pagination).")
    @app_commands.describe(date="Optional date (YYYY-MM-DD)")
    async def batch_view(self, interaction: discord.Interaction, date: str = None):
        if date:
            try:
                batch_date = datetime.datetime.strptime(date, "%Y-%m-%d").date()
            except ValueError:
                return await interaction.response.send_message("❌ Invalid date format. Use YYYY-MM-DD.", ephemeral=True)
        else:
            batch_date = datetime.date.today()

        bid = await self.bot.pg.fetchval("SELECT id FROM batches WHERE batch_date=$1", batch_date)
        if not bid:
            return await interaction.response.send_message(f"No batch found for `{batch_date}`.", ephemeral=True)

        rows = await self.bot.pg.fetch("""
            SELECT a.id, a.title, a.rarity, a.currency, a.rate, a.image_url, bi.position
            FROM batch_items bi
            JOIN auctions a ON bi.auction_id = a.id
            WHERE bi.batch_id = $1
            ORDER BY bi.position
        """, bid)

        if not rows:
            return await interaction.response.send_message(f"Batch #{bid} is empty.", ephemeral=True)

        view = BatchPaginationView(rows, batch_date, interaction.user.id)
        embed = view.build_page()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="batch-status", description="Show how many cards are still waiting in each queue.")
    async def batch_status(self, interaction: discord.Interaction):
        normals = await self.bot.pg.fetchval("""
            SELECT COUNT(*) FROM auctions WHERE status='READY' AND queue_type='NORMAL'
        """)
        skips = await self.bot.pg.fetchval("""
            SELECT COUNT(*) FROM auctions WHERE status='READY' AND queue_type='SKIP'
        """)
        cms = await self.bot.pg.fetchval("""
            SELECT COUNT(*) FROM auctions WHERE status='READY' AND queue_type='CARD_MAKER'
        """)

        embed = discord.Embed(
            title="📊 Batch Status",
            description="Auctions still waiting to be batched",
            color=discord.Color.gold()
        )
        embed.add_field(name="Normal queue", value=f"{normals} (max 15 per batch)", inline=False)
        embed.add_field(name="Skip queue", value=f"{skips} (no limit)", inline=False)
        embed.add_field(name="Card Maker", value=f"{cms} (no limit)", inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # -------------------------
    # AUCTION LOCK COMMAND
    # -------------------------

    @app_commands.command(name="auction-lock", description="Lock and archive all open auction threads in auction forums.")
    @app_commands.default_permissions(manage_messages=True)
    async def auction_lock(self, interaction: discord.Interaction):
        locked_count = 0
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)

        for forum_id in AUCTION_FORUMS:
            forum = interaction.guild.get_channel(forum_id)
            if not forum or not isinstance(forum, discord.ForumChannel):
                continue

            for thread in forum.threads:
                if not thread.locked or not thread.archived:
                    try:
                        await thread.edit(locked=True, archived=True)
                        locked_count += 1

                        # Log dans le channel de log
                        if log_channel:
                            embed = discord.Embed(
                                title="🔒 Auction thread locked",
                                description=f"Thread: {thread.mention}\nForum: {forum.name}",
                                color=discord.Color.red()
                            )
                            embed.set_footer(text=f"Thread ID: {thread.id} | Action by {interaction.user}")
                            await log_channel.send(embed=embed)

                    except Exception as e:
                        print(f"Failed to lock thread {thread.id}: {e}")

        await interaction.response.send_message(
            f"🔒 {locked_count} auction threads have been locked and archived.",
            ephemeral=True
        )
# -------------------------
# PAGINATION VIEW
# -------------------------

class BatchPaginationView(discord.ui.View):
    def __init__(self, rows, batch_date, owner_id: int):
        super().__init__(timeout=180)
        self.rows = rows
        self.batch_date = batch_date
        self.page = 0
        self.per_page = 5
        self.owner_id = owner_id

    def build_page(self):
        start = self.page * self.per_page
        end = start + self.per_page
        chunk = self.rows[start:end]

        embed = discord.Embed(
            title=f"Batch of {self.batch_date}",
            description=f"Showing {start+1}-{min(end, len(self.rows))} of {len(self.rows)} cards",
            color=discord.Color.blurple()
        )
        for row in chunk:
            title = row['title'] or f"Card #{row['id']}"
            rate_display = row['rate'] if row['rate'] else "—"
            embed.add_field(
                name=f"#{row['id']} — {title}",
                value=f"Pos: {row['position']} | Rarity: {row['rarity']} | Currency: {row['currency']} ({rate_display})",
                inline=False
            )
        if chunk and chunk[0]["image_url"]:
            embed.set_thumbnail(url=chunk[0]["image_url"])
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("❌ Only the command invoker can use these buttons.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="⬅️ Prev", style=discord.ButtonStyle.secondary)
    async def prev_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.page > 0:
            self.page -= 1
        embed = self.build_page()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next ➡️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if (self.page + 1) * self.per_page < len(self.rows):
            self.page += 1
        embed = self.build_page()
        await interaction.response.edit_message(embed=embed, view=self)


async def setup(bot: commands.Bot):
    await bot.add_cog(BatchPreparation(bot))
