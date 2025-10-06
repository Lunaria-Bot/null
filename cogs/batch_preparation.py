import discord
from discord.ext import commands
from discord import app_commands
import datetime
from .auction_core import get_or_create_today_batch

class BatchPreparation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

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

        # Normal queue : max 15
        normals = await self.bot.pg.fetch("""
            SELECT id FROM auctions
            WHERE status='READY' AND queue_type='NORMAL'
            ORDER BY id ASC
            LIMIT 15
        """)

        # Skip queue : illimité
        skips = await self.bot.pg.fetch("""
            SELECT id FROM auctions
            WHERE status='READY' AND queue_type='SKIP'
            ORDER BY id ASC
        """)

        # Card Maker : illimité
        cms = await self.bot.pg.fetch("""
            SELECT id FROM auctions
            WHERE status='READY' AND queue_type='CARD_MAKER'
            ORDER BY id ASC
        """)

        position = 1
        for row in normals:
            await self.bot.pg.execute(
                "INSERT INTO batch_items (batch_id, auction_id, position) VALUES ($1,$2,$3)",
                bid, row["id"], position
            )
            position += 1

        for row in skips:
            await self.bot.pg.execute(
                "INSERT INTO batch_items (batch_id, auction_id, position) VALUES ($1,$2,$3)",
                bid, row["id"], position
            )
            position += 1

        for row in cms:
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

    @app_commands.command(name="batch-lock", description="Lock all posts of today's batch.")
    @app_commands.default_permissions(manage_messages=True)
    async def batch_lock(self, interaction: discord.Interaction):
        bid = await get_or_create_today_batch(self.bot.pg)

        rows = await self.bot.pg.fetch("""
            SELECT a.thread_id
            FROM batch_items bi
            JOIN auctions a ON bi.auction_id = a.id
            WHERE bi.batch_id = $1 AND a.thread_id IS NOT NULL
        """, bid)

        if not rows:
            return await interaction.response.send_message(f"No threads found for batch #{bid}.", ephemeral=True)

        locked_count = 0
        for row in rows:
            thread_id = row["thread_id"]
            thread = interaction.guild.get_thread(thread_id)
            if thread:
                try:
                    await thread.edit(locked=True, archived=True)
                    locked_count += 1
                except Exception as e:
                    print(f"Failed to lock thread {thread_id}: {e}")

        await interaction.response.send_message(
            f"🔒 Locked {locked_count} threads for batch #{bid}.",
            ephemeral=True
        )


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
            embed.add_field(
                name=f"#{row['id']} — {row['title']}",
                value=f"Pos: {row['position']} | Rarity: {row['rarity']} | Currency: {row['currency']} ({row['rate']})",
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
