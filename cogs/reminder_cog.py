import discord
from discord import app_commands
from discord.ext import commands, tasks
import json
import os
import datetime

class ReminderCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.path = "data/reminders.json"
        self.check_reminders.start()

    def load_data(self):
        if not os.path.exists(self.path): return {}
        with open(self.path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def save_data(self, data):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

    @tasks.loop(minutes=1.0)
    async def check_reminders(self):
        now = datetime.datetime.now()
        current_time = now.strftime("%H:%M")
        weekday = str(now.weekday()) # 0-6
        
        data = self.load_data()
        for guild_id, reminders in data.items():
            for rem in reminders:
                if rem['time'] == current_time:
                    # 檢查頻率 (daily 或 指定星期)
                    if rem['freq'] == 'daily' or weekday in rem['freq']:
                        channel = self.bot.get_channel(rem['channel_id'])
                        if channel:
                            await channel.send(f"⏰ **提醒時間到！**\n內容：{rem['content']}")

    @app_commands.command(name="設定提醒", description="設定每日或指定星期的提醒")
    @app_commands.describe(時間="格式為 HH:MM (例如 08:30)", 頻率="daily 或 0,1,2 (代表星期幾)", 內容="提醒文字")
    @app_commands.checks.has_permissions(administrator=True)
    async def set_reminder(self, interaction: discord.Interaction, 頻道: discord.TextChannel, 時間: str, 頻率: str, 內容: str):
        data = self.load_data()
        guild_id = str(interaction.guild_id)
        if guild_id not in data: data[guild_id] = []
        
        new_rem = {
            "id": len(data[guild_id]) + 1,
            "channel_id": 頻道.id,
            "time": 時間,
            "freq": 頻率,
            "content": 內容
        }
        data[guild_id].append(new_rem)
        self.save_data(data)
        await interaction.response.send_message(f"✅ 已成功設定提醒：每當 {時間} ({頻率}) 會在 {頻道.mention} 提醒。")

    @app_commands.command(name="提醒列表", description="查看本伺服器所有提醒")
    async def list_reminders(self, interaction: discord.Interaction):
        data = self.load_data()
        guild_id = str(interaction.guild_id)
        rems = data.get(guild_id, [])
        if not rems:
            return await interaction.response.send_message("目前沒有任何提醒設定。")
        
        msg = "\n".join([f"#{r['id']} | {r['time']} ({r['freq']}) -> {r['content']}" for r in rems])
        await interaction.response.send_message(f"📋 **目前提醒列表：**\n```{msg}```")

    @app_commands.command(name="移除提醒", description="根據編號移除提醒")
    @app_commands.checks.has_permissions(administrator=True)
    async def remove_reminder(self, interaction: discord.Interaction, 編號: int):
        data = self.load_data()
        guild_id = str(interaction.guild_id)
        if guild_id in data:
            original_len = len(data[guild_id])
            data[guild_id] = [r for r in data[guild_id] if r['id'] != 編號]
            if len(data[guild_id]) < original_len:
                self.save_data(data)
                return await interaction.response.send_message(f"✅ 已移除編號 #{編號} 的提醒。")
        await interaction.response.send_message("找不到該編號的提醒。")

async def setup(bot):
    await bot.add_cog(ReminderCog(bot))