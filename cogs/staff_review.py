import discord
from discord.ext import commands
from discord import app_commands, ui

# IDs des canaux de log par queue
QUEUE_CHANNELS = {
    "NORMAL": 1304100031388844114,
    "SKIP": 1308385490931810434,
    "CM": 1395404596230361209,
}

# Channel de log global (actions staff)
LOG_CHANNEL_ID = 1424688704584286248


class StaffReview(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # Cette commande n’est plus vraiment utile, mais on peut la garder pour debug
    @app_commands.command(name="auction-list", description="List auctions pending review.")
    async def auction_list(self, interaction: discord.Interaction):
        rows = await self.bot.pg.fetch("""
            SELECT id, user_id, rarity, queue_type, currency, rate, series, version, title, image_url, status
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
                value=f"User: <@{r['user_id']}> | {r['rarity']} | {r['queue_type']} | {r['currency']} | {r['rate']} | Status: {r['status']}",
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
        embed.add_field(name="Rate", value=auction["rate"] or "N/A", inline=True)
        embed.add_field(name="Version", value=auction["version"] or "?", inline=True)
        if auction["image_url"]:
            embed.set_image(url=auction["image_url"])

        view = ReviewButtons(self.bot, auction["id"])
        await channel.send(embed=embed, view=view)


# --- Boutons Accept / Deny / Fee Paid ---
class ReviewButtons(discord.ui.View):
    def __init__(self, bot, auction_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.auction_id = auction_id

    @discord.ui.button(label="✅ Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Ouvre un modal avec raison facultative et référence au message à modifier
        modal = ReasonModal(self.bot, self.auction_id, "ACCEPT", interaction.message)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="❌ Deny", style=discord.ButtonStyle.danger)
    async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = ReasonModal(self.bot, self.auction_id, "DENY", interaction.message)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="💰 Fee paid", style=discord.ButtonStyle.secondary)
    async def fee_paid(self, interaction: discord.Interaction, button: discord.ui.Button):
        msg = interaction.message
        if not msg.embeds:
            return await interaction.response.send_message("No embed to update.", ephemeral=True)

        embed = msg.embeds[0]

        # Mettre à jour/ajouter le champ Fees: Paid (éviter les doublons)
        updated_fields = []
        fees_updated = False
        for f in embed.fields:
            if f.name.lower() == "fees":
                updated_fields.append(("Fees", "Paid", False))
                fees_updated = True
            else:
                updated_fields.append((f.name, f.value, f.inline))
        if not fees_updated:
            updated_fields.append(("Fees", "Paid", False))

        # Rebuild l'embed proprement
        new_embed = discord.Embed(title=embed.title, description=embed.description, color=embed.color)
        if embed.author:
            new_embed.set_author(name=embed.author.name, icon_url=getattr(embed.author, "icon_url", discord.Embed.Empty))
        if embed.thumbnail:
            new_embed.set_thumbnail(url=embed.thumbnail.url)
        if embed.image:
            new_embed.set_image(url=embed.image.url)
        if embed.footer:
            new_embed.set_footer(text=embed.footer.text, icon_url=getattr(embed.footer, "icon_url", discord.Embed.Empty))
        for name, value, inline in updated_fields:
            new_embed.add_field(name=name, value=value, inline=inline)

        # Mettre le bouton en vert et le désactiver
        button.style = discord.ButtonStyle.success
        button.disabled = True

        await msg.edit(embed=new_embed, view=self)
        await interaction.response.send_message("Marked as fees paid.", ephemeral=True)


# --- Modal pour raison facultative (Accept / Deny) ---
class ReasonModal(discord.ui.Modal):
    def __init__(self, bot, auction_id: int, action: str, message: discord.Message):
        super().__init__(title=f"{action} Auction")
        self.bot = bot
        self.auction_id = auction_id
        self.action = action
        self.message = message
        self.reason = discord.ui.TextInput(
            label="Reason (optional)",
            required=False,
            style=discord.TextStyle.paragraph
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction):
        reason = (self.reason.value or "No reason provided.").strip()
        log_channel = interaction.guild.get_channel(LOG_CHANNEL_ID)

        # Charger l'embed du message original
        if not self.message.embeds:
            return await interaction.response.send_message("No embed to update.", ephemeral=True)
        embed = self.message.embeds[0]

        if self.action == "ACCEPT":
            await self.bot.pg.execute("UPDATE auctions SET status='READY' WHERE id=$1", self.auction_id)
            # Modifier l'embed
            new_embed = discord.Embed(
                title=embed.title,
                description=f"✅ Submission approved\nReason: {reason}",
                color=discord.Color.green()
            )
            # Copie des champs existants
            for f in embed.fields:
                # On peut garder les champs Seller, Currency, etc.
                new_embed.add_field(name=f.name, value=f.value, inline=f.inline)
            # Image / footer / thumbnail
            if embed.image:
                new_embed.set_image(url=embed.image.url)
            if embed.thumbnail:
                new_embed.set_thumbnail(url=embed.thumbnail.url)
            if embed.footer:
                new_embed.set_footer(text=embed.footer.text)

            # Supprimer les boutons (view=None)
            await self.message.edit(embed=new_embed, view=None)
            await interaction.response.send_message(f"Auction #{self.auction_id} approved.", ephemeral=True)

            # Log
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

            new_embed = discord.Embed(
                title=embed.title,
                description=f"❌ Submission denied\nReason: {reason}",
                color=discord.Color.red()
            )
            for f in embed.fields:
                new_embed.add_field(name=f.name, value=f.value, inline=f.inline)
            if embed.image:
                new_embed.set_image(url=embed.image.url)
            if embed.thumbnail:
                new_embed.set_thumbnail(url=embed.thumbnail.url)
            if embed.footer:
                new_embed.set_footer(text=embed.footer.text)

            await self.message.edit(embed=new_embed, view=None)
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
