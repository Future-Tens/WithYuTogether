import discord
from discord import app_commands
from discord.ext import commands

from utils.db_manager import DatabaseManager


class ClearAnnounceView(discord.ui.View):
    def __init__(self, guild_id):
        super().__init__(timeout=120)
        self.guild_id = guild_id

    @discord.ui.button(label="取消公告頻道", emoji="🔕", style=discord.ButtonStyle.danger)
    async def clear(self, interaction: discord.Interaction, button: discord.ui.Button):
        DatabaseManager().set_announce_channel(self.guild_id, None)
        await interaction.response.edit_message(content="🔕 已取消公告頻道，之後不會再主動發公告。", view=None)


class SettingsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="設定公告", description="設定機器人發公告的頻道（小鎮任務、稀有作物等）")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.rename(channel="頻道")
    @app_commands.describe(channel="公告要發在哪個頻道；不填則查看目前的設定")
    async def set_announce(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None):
        db = DatabaseManager()
        if channel is None:
            current = db.get_announce_channel(interaction.guild_id)
            if not current:
                await interaction.response.send_message(
                    "📢 目前沒有設定公告頻道。用 `/設定公告 頻道:#頻道` 設定。", ephemeral=True)
                return
            await interaction.response.send_message(
                f"📢 目前的公告頻道是 <#{current}>。", view=ClearAnnounceView(interaction.guild_id), ephemeral=True)
            return

        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.view_channel and perms.send_messages):
            await interaction.response.send_message(
                f"❌ 我在 {channel.mention} 沒有發言權限，請先調整頻道權限。", ephemeral=True)
            return
        db.set_announce_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(
            f"✅ 之後的公告都會發在 {channel.mention}。", ephemeral=True)


async def setup(bot):
    await bot.add_cog(SettingsCog(bot))
