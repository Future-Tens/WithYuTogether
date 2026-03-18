import discord
from discord import app_commands
from discord.ext import commands
import json
import random
import datetime
import os

class TarotCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.records_path = "data/tarot_users.json"
        self.cards_path = "data/tarot_cards.json"

    def load_tarot_cards(self):
        """從 JSON 讀取所有塔羅牌資訊"""
        if not os.path.exists(self.cards_path):
            return []
        with open(self.cards_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def check_daily_limit(self, user_id):
        """檢查使用者今天是否已經占卜過"""
        if not os.path.exists(self.records_path):
            return False
        
        with open(self.records_path, 'r', encoding='utf-8') as f:
            records = json.load(f)
            
        today = str(datetime.date.today())
        return records.get(str(user_id)) == today

    def save_user_record(self, user_id):
        """儲存使用者今日占卜紀錄"""
        records = {}
        if os.path.exists(self.records_path):
            with open(self.records_path, 'r', encoding='utf-8') as f:
                records = json.load(f)
        
        records[str(user_id)] = str(datetime.date.today())
        with open(self.records_path, 'w', encoding='utf-8') as f:
            json.dump(records, f, indent=4)

    @app_commands.command(name="塔羅占卜", description="每日一次的塔羅牌占卜")
    @app_commands.choices(主題=[
        app_commands.Choice(name="整體運勢", value="整體"),
        app_commands.Choice(name="感情發展", value="愛情"),
        app_commands.Choice(name="事業工作", value="事業"),
        app_commands.Choice(name="財運狀況", value="財運")
    ])
    async def tarot(self, interaction: discord.Interaction, 主題: app_commands.Choice[str]):
        # 1. 檢查每日限制
        if self.check_daily_limit(interaction.user.id):
            await interaction.response.send_message(
                f"🌙 {interaction.user.mention}，你今天已經進行過占卜了。明天再來吧！", 
                ephemeral=True
            )
            return

        # 2. 載入卡牌
        cards = self.load_tarot_cards()
        if not cards:
            await interaction.response.send_message("❌ 錯誤：找不到塔羅牌資料庫 (tarot_cards.json)。", ephemeral=True)
            return

        # 3. 隨機抽牌與判斷正逆位
        card = random.choice(cards)
        is_upright = random.choice([True, False])
        orientation = "正位" if is_upright else "逆位"
        meaning = card['meaning_up'] if is_upright else card['meaning_rev']

        # 4. 建立 Embed
        embed = discord.Embed(
            title=f"🔮 塔羅占卜結果 - {主題.name}",
            description=f"專注於你的問題，這就是塔羅牌給你的啟示：",
            color=discord.Color.dark_magenta()
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name="抽取牌項", value=f"✨ **{card['name']}** ({orientation})", inline=False)
        embed.add_field(name="牌意解析", value=meaning, inline=False)
        
        # 設置塔羅牌圖片
        if "img" in card and card["img"]:
            embed.set_image(url=card["img"])
        
        embed.set_footer(text=f"占卜者：{interaction.user.display_name} • 日期：{datetime.date.today()}")

        # 5. 儲存紀錄並送出
        self.save_user_record(interaction.user.id)
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(TarotCog(bot))