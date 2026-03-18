import discord
from discord import app_commands
from discord.ext import commands
import random

class FoodCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.foods = ["拉麵", "滷肉飯", "麥當勞", "義大利麵", "火鍋", "壽司", "健康餐盒", "泰式料理", "牛肉麵"]
        self.styles = [
            "✨ 今天適合吃 {food}，感覺會很有元氣！", # 認真推薦
            "😏 隨便啦，去吃 {food} 好了，反正你吃什麼都沒差。", #嘴砲風格
            "🔥 就是現在！給我衝去吃 {food}，不准猶豫！", #激進風格
            "🤔 緣分到了，{food} 自然會出現在你面前。" #佛系建議
        ]

    @app_commands.command(name="午餐推薦", description="不知道午餐吃什麼？")
    async def lunch(self, interaction: discord.Interaction):
        food = random.choice(self.foods)
        text = random.choice(self.styles).format(food=food)
        embed = discord.Embed(title="🍴 午餐提案", description=text, color=discord.Color.blue())
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="晚餐推薦", description="不知道晚餐吃什麼？")
    async def dinner(self, interaction: discord.Interaction):
        food = random.choice(self.foods)
        text = random.choice(self.styles).format(food=food)
        embed = discord.Embed(title="🍽️ 晚餐提案", description=text, color=discord.Color.dark_blue())
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(FoodCog(bot))