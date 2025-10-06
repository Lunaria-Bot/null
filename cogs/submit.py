import discord
from discord.ext import commands
from discord import app_commands
from .utils import redis_json_load, queue_display_to_type

QUEUE_OPTIONS = [
    discord.SelectOption(label="Normal queue", value="Normal queue", emoji="🟩", description="Standard posting order"),
    discord.SelectOption(label="Skip queue", value="Skip queue", emoji="⏭️", description="Skip ahead in the queue"),
    discord.SelectOption(label="Card Maker", value="Card Maker", emoji="🛠️", description="Custom card by CM"),
]

CURRENCY_OPTIONS = [
    discord.SelectOption(label="BS", value="BS", emoji="🪙", description="BloodStone"),
    discord.SelectOption(label="MS", value="MS", emoji="💎", description="Moonstone"),
    discord.SelectOption(label="BS & MS", value="BS+MS", emoji="⚖️", description="Both currencies, requires rate"),
    discord.SelectOption(label="PayPal (CM only)", value="PAYPAL", emoji="💳", description="Only valid for Card Maker"),
]

def make_progress_footer(queue: str | None, currency: str | None, rate: str | None) -> str:
    done = 0
    total = 3
    if queue: done += 1
    if currency: done += 1
    if currency and currency not in {"MS", "BS+MS"}:
        done += 1
    elif currency in {"MS", "BS+MS"} and rate:
        done += 1
    return f"Setup progress: {done}/{total}"

def build_preview_embed(user_id: int, data: dict, queue_display: str | None, currency: str | None, rate: str | None) -> discord.Embed:
    title = data.get("title") or "Unknown Card"
    series = data.get("series") or "Unknown Series"
    version = data.get("version") or "?"
    batch_no = data.get("batch")
    rarity = (data.get("rarity") or "COMMON")
    image_url = data.get("image_url")

    embed = discord.Embed(title=title, color=discord.Color.blurple())
    embed.add_field(name="Series", value=series, inline=True)
    embed.add_field(name="Version", value=str(version), inline=True)
    embed.add_field(name="Batch", value=str(batch_no or "?"), inline=True)
    embed.add_field(name="Owned by", value=f"<@{user_id}>", inline=False)
    embed.add_field(name="Rarity", value=rarity, inline=True)

    q_display = queue_display or "—"
    cur_display = currency or "—"
    if currency in {"MS", "BS+MS"}:
        rate_display = rate or "—"
    elif currency in {"BS", "PAYPAL"}:
        rate_display = rate or "N/A"
    else:
        rate_display = "—"

    embed.add_field(name="Queue", value=q_display, inline=True)
    embed.add_field(name="Currency", value=cur_display, inline=True)
    embed.add_field(name="Rate", value=rate_display, inline=True)

    if image_url:
        embed.set_image(url=image_url)

    embed.set_footer(text=make_progress_footer(queue_display, currency, rate))
    return embed


class Submit(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="auction-submit", description="Submit your Mazoku card to Auction.")
    async def auction_submit(self, interaction: discord.Interaction):
        user_id = interaction.user.id
        cached = await self.bot.redis.get(f"mazoku:card:{user_id}")
        if not cached:
            await interaction.response.send_message("No Mazoku card detected for you recently.", ephemeral=True)
            return

        data = redis_json_load(cached)

        view = ConfigView(self.bot, user_id, data)
        embed = build_preview_embed(user_id, data, view.queue_display, view.currency, view.rate)

        try:
            await interaction.user.send(
                content="Setup your auction below. Pick queue, currency, and rate if needed, then submit.",
                embed=embed,
                view=view
            )
            await interaction.response.send_message("I sent you a private message to complete the submission.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("Enable your DMs so I can send you the form.", ephemeral=True)


class QueueSelect(discord.ui.Select):
    def __init__(self, parent_view: "ConfigView"):
        super().__init__(placeholder="Select your queue", min_values=1, max_values=1, options=QUEUE_OPTIONS)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.queue_display = self.values[0]
        await self.parent_view.refresh(interaction, note=f"Queue selected: {self.parent_view.queue_display}")


class CurrencySelect(discord.ui.Select):
    def __init__(self, parent_view: "ConfigView"):
        super().__init__(placeholder="Select currency", min_values=1, max_values=1, options=CURRENCY_OPTIONS)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.currency = self.values[0]
        await self.parent_view.refresh(interaction, note=f"Currency set: {self.parent_view.currency}")


class ConfigView(discord.ui.View):
    def __init__(self, bot, user_id, data):
        super().__init__(timeout=600)
        self.bot = bot
        self.user_id = user_id
        self.data = data

        self.queue_display: str | None = None
        self.currency: str | None = None
        self.rate: str | None = None

        self.queue_select = QueueSelect(self)
        self.currency_select = CurrencySelect(self)
        self.rate_button = discord.ui.Button(style=discord.ButtonStyle.secondary, label="Set rate", emoji="📝")
        self.submit_button = discord.ui.Button(style=discord.ButtonStyle.success, label="Submit", emoji="✅", disabled=True)
        self.cancel_button = discord.ui.Button(style=discord.ButtonStyle.danger, label="Cancel", emoji="🛑")

        self.rate_button.callback = self.on_rate
        self.submit_button.callback = self.on_submit
        self.cancel_button.callback = self.on_cancel

        self.add_item(self.queue_select)
        self.add_item(self.currency_select)
        self.add_item(self.rate_button)
        self.add_item(self.submit_button)
        self.add_item(self.cancel_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def refresh(self, interaction: discord.Interaction, note: str | None = None):
        qtype = queue_display_to_type(self.queue_display) if self.queue_display else None
        if qtype == "CM" and self.currency not in {None, "PAYPAL"}:
            self.currency = None
            note = (note or "") + "\nCurrency reset. Only PayPal is allowed for Card Maker."
        if qtype in {"NORMAL", "SKIP"} and self.currency not in {None, "BS", "MS", "BS+MS"}:
            self.currency = None
            note = (note or "") + "\nCurrency reset. Use BS, MS or BS & MS for Normal/Skip."

        if self.currency not in {"MS", "BS+MS"} and not self.rate:
            self.rate = None

        ready = self.is_ready()
        self.submit_button.disabled = not ready

        embed = build_preview_embed(self.user_id, self.data, self.queue_display, self.currency, self.rate)
        content = note if note else None

        try:
            if interaction.message:
                await interaction.response.edit_message(content=content, embed=embed, view=self)
            else:
                await interaction.response.send_message(content=content or "Updated.", embed=embed, view=self)
        except discord.InteractionResponded:
            await interaction.followup.send(content=content or "Updated.", embed=embed, view=self)

    def is_ready(self) -> bool:
        if not self.queue_display or not self.currency:
            return False
        if self.currency in {"MS", "BS+MS"} and not self.rate:
            return False
        return True

    async def on_rate(self, interaction: discord.Interaction):
        modal = RateModal(self)
        await interaction.response.send_modal(modal)

    async def on_submit(self, interaction: discord.Interaction):
        qtype = queue_display_to_type(self.queue_display)
        if qtype == "CM":
            if self.currency != "PAYPAL":
                return await interaction.response.send_message("Only PayPal is allowed for Card Maker.", ephemeral=True)
        else:
            if self.currency not in {"BS", "MS", "BS+MS"}:
                return await interaction.response.send_message("Use BS, MS or BS & MS for Normal/Skip.", ephemeral=True)

        rate_value = self.rate if self.rate else ("N/A" if self.currency in {"BS", "PAYPAL"} else None)
        if self.currency in {"MS", "BS+MS"} and not rate_value:
            return await interaction.response.send_message("Rate is required when choosing MS or BS & MS.", ephemeral=True)

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
        rate_value,
        self.data.get("title"),
        self.data.get("image_url"))

        confirm = discord.Embed(
            title=f"Auction #{rec['id']} submitted",
            description="Your auction is now pending staff review.",
            color=discord.Color.green()
        )
        confirm.add_field(name="Queue", value=self.queue_display, inline=True)
        confirm.add_field(name="Currency", value=self.currency, inline=True)
        confirm.add_field(name="Rate", value=rate_value, inline=True)
        confirm.set_footer(text="Thank you! You’ll receive a DM after review.")

        fee_msg = None
        if self.queue_display == "Normal queue":
            fee_msg = "💰 Do not forget to pay fees à <@723441401211256842>\nNormal Queue: 500bs"
        elif self.queue_display == "Skip queue":
            fee_msg = "💰 Do not forget to pay fees à <@723441401211256842>\nSkip Queue: 2000bs"
        elif self.queue_display == "Card Maker":
            fee_msg = "⚠️ Card Maker queue selected.\nThank you for submitting your card on Lilac."

        try:
            await interaction.response.edit_message(content=fee_msg, embed=confirm, view=None)
        except discord.InteractionResponded:
            await interaction.followup.send(content=fee_msg, embed=confirm, view=None)
        self.stop()

    async def on_cancel(self, interaction: discord.Interaction):
        cancel = discord.Embed(
            title="Submission cancelled",
            description="No data was saved. You can run /auction-submit again anytime.",
            color=discord.Color.red()
        )
        try:
            await interaction.response.edit_message(content=None, embed=cancel, view=None)
        except discord.InteractionResponded:
            await interaction.followup.send(embed=cancel, view=None)
        self.stop()


class RateModal(discord.ui.Modal, title="Set rate"):
    def __init__(self, parent_view: ConfigView):
        super().__init__(title="Set rate")
        self.parent_view = parent_view
        self.input = discord.ui.TextInput(
            label="Rate",
            placeholder="Ex: 200:1 (empty for BS/PayPal)",
            required=False,
            max_length=50
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        self.parent_view.rate = (self.input.value or "").strip()
        await self.parent_view.refresh(interaction, note="Rate updated.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Submit(bot))
