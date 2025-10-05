import discord
from discord.ext import commands
from discord import app_commands
from .utils import redis_json_load, queue_display_to_type

class Submit(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="auction-submit", description="Submit your Mazoku card to Auction.")
    async def auction_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        cached = await self.bot.redis.get(f"mazoku:card:{user_id}")
        if not cached:
            await interaction.response.send_message(
                "No Mazoku card detected for you recently.", ephemeral=True
            )
            return

        data = redis_json_load(cached)
        title = data.get("title") or "Unknown Card"
        series = data.get("series") or "Unknown Series"
        version = data.get("version") or "?"
        batch_no = data.get("batch")
        rarity = data.get("rarity") or "COMMON"
        image_url = data.get("image_url")

        embed = discord.Embed(title=title, color=discord.Color.blurple())
        embed.add_field(name="Series", value=series, inline=True)
        embed.add_field(name="Version", value=str(version), inline=True)
        embed.add_field(name="Batch", value=str(batch_no or "?"), inline=True)
        embed.add_field(name="Owned by", value=f"<@{user_id}>", inline=False)
        embed.set_footer(text=f"Rarity: {rarity}")
        if image_url:
            embed.set_image(url=image_url)

        view = ConfigView(self.bot, user_id, data)
        try:
            await interaction.user.send(
                content="Complete your auction submission:",
                embed=embed,
                view=view
            )
            await interaction.response.send_message(
                "I sent you a private message to complete the submission.",
                ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "Enable your DMs so I can send you the form.",
                ephemeral=True
            )

class ConfigView(discord.ui.View):
    def __init__(self, bot, user_id, data):
        super().__init__(timeout=600)
        self.bot = bot
        self.user_id = user_id
        self.data = data
        self.queue_display = None
        self.currency = None
        self.rate = None

        queue_select = discord.ui.Select(
            placeholder="Select your queue",
            options=[
                discord.SelectOption(label="Normal queue", value="Normal queue"),
                discord.SelectOption(label="Skip queue", value="Skip queue"),
                discord.SelectOption(label="Card Maker", value="Card Maker"),
            ]
        )
        queue_select.callback = self.on_queue_select
        self.add_item(queue_select)

        currency_btn = discord.ui.Button(style=discord.ButtonStyle.secondary, label="Set Currency")
        currency_btn.callback = self.on_currency
        self.add_item(currency_btn)

        rate_btn = discord.ui.Button(style=discord.ButtonStyle.secondary, label="Set Rate")
        rate_btn.callback = self.on_rate
        self.add_item(rate_btn)

        submit_btn = discord.ui.Button(style=discord.ButtonStyle.primary, label="Submit Auction")
        submit_btn.callback = self.on_submit
        self.add_item(submit_btn)

        cancel_btn = discord.ui.Button(style=discord.ButtonStyle.danger, label="Cancel")
        cancel_btn.callback = self.on_cancel
        self.add_item(cancel_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def on_queue_select(self, interaction: discord.Interaction):
        self.queue_display = interaction.data["values"][0]
        await interaction.response.send_message(
            f"Queue selected: {self.queue_display}", ephemeral=True
        )

    async def on_currency(self, interaction: discord.Interaction):
        modal = SimpleInputModal(title="Currency", label="Enter currency (BS/MS or Paypal if CM)", key="currency")
        await interaction.response.send_modal(modal)
        await modal.wait()
        self.currency = modal.value
        await interaction.followup.send("Currency set.", ephemeral=True)

    async def on_rate(self, interaction: discord.Interaction):
        modal = SimpleInputModal(title="Rate", label="Enter rate (ex 200:1)", key="rate")
        await interaction.response.send_modal(modal)
        await modal.wait()
        self.rate = modal.value
        await interaction.followup.send("Rate set.", ephemeral=True)

    async def on_submit(self, interaction: discord.Interaction):
        if not all([self.queue_display, self.currency, self.rate]):
            await interaction.response.send_message(
                "Please complete Queue, Currency and Rate before submitting.", ephemeral=True
            )
            return
        qtype = queue_display_to_type(self.queue_display)

        rec = await self.bot.pg.fetchrow("""
            INSERT INTO auctions (user_id, series, version, batch_no, owner_id, rarity, queue_type, currency, rate, status, title, image_url)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'PENDING',$10,$11)
            RETURNING id
        """,
        self.user_id,
        self.data.get("series"),
        self.data.get("version"),
        self.data.get("batch"),
        self.data.get("owner_id"),
        (self.data.get("rarity") or "COMMON"),
        qtype,
        self.currency,
        self.rate,
        self.data.get("title"),
        self.data.get("image_url"))

        await interaction.response.send_message(
            f"Auction #{rec['id']} submitted. Waiting for staff review.", ephemeral=True
        )
        self.stop()

    async def on_cancel(self, interaction: discord.Interaction):
        await interaction.response.send_message("Submission cancelled.", ephemeral=True)
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
        await interaction.response.send_message(f"{self.key.capitalize()} received.", ephemeral=True)
        self.stop()

async def setup(bot: commands.Bot):
    await bot.add_cog(Submit(bot))
