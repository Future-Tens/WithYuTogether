import discord
from discord import app_commands
from discord.ext import commands

class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="幫助", description="顯示機器人功能列表")
    async def help_command(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🤖 機器人指令說明",
            description="這裡是目前可用的功能清單：",
            color=discord.Color.green()
        )
        embed.add_field(name="⏰ 提醒功能 (管理員)", value="`/設定提醒`、`/提醒列表`、`/移除提醒`", inline=False)
        embed.add_field(name="🔮 命理功能", value="`/塔羅占卜` (每日一次)\n`/星座運勢` (選單查詢)", inline=False)
        embed.add_field(name="🍱 隨機抽餐", value="`/午餐推薦`、`/晚餐推薦`", inline=False)
        embed.set_footer(text="如有問題請聯絡管理員")
        
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(HelpCog(bot))