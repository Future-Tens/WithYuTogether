import discord
import datetime
import unicodedata
from discord import app_commands
from discord.ext import commands
import requests
from bs4 import BeautifulSoup

def get_visual_width(text):
    """計算字串的視覺顯示寬度 (中文=2, 英文/空格=1)"""
    width = 0
    for char in text:
        if unicodedata.east_asian_width(char) in ('W', 'F', 'A'):
            width += 2
        else:
            width += 1
    return width

def format_line(label, value, color="\u001b[36m", target_width=10):
    """標籤對齊工具"""
    current_width = get_visual_width(label)
    padding = " " * max(0, target_width - current_width)
    return f"\u001b[37m{label}{padding} : {color}{value}\u001b[0m"

class HoroscopeCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.astro_map = {
            "牡羊座": "0", "金牛座": "1", "雙子座": "2", "巨蟹座": "3",
            "獅子座": "4", "處女座": "5", "天秤座": "6", "天蠍座": "7",
            "射手座": "8", "摩羯座": "9", "水瓶座": "10", "雙魚座": "11"
        }

    
    @app_commands.command(name="星座運勢", description="查看今日星座運勢")
    async def horoscope(self, interaction: discord.Interaction):
        view = discord.ui.View()
        select = discord.ui.Select(placeholder="選擇你的星座...")
        
        for name in self.astro_map.keys():
            select.add_option(label=name, value=name)
        
        async def select_callback(inter: discord.Interaction):
            astro_name = select.values[0]
            idx = self.astro_map[astro_name]
            url = f"https://astro.click108.com.tw/daily_{idx}.php?iAstro={idx}"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7',
                'Referer': 'https://astro.click108.com.tw/',
            }
            # 執行爬蟲 (不檢查 SSL)
            try:
                resp = requests.get(url, headers=headers, verify=False, timeout=10)
                resp.encoding = 'utf-8'
                soup = BeautifulSoup(resp.text, 'html.parser')
                
                short_content = soup.find('div', class_='TODAY_WORD')
                item = short_content.find('p') #今日短評

                lucky_contents = soup.find('div', class_='TODAY_LUCKY')
                lucky_col_name = ['幸運數字', '幸運顏色', '開運方位', '今日吉時', '幸運星座']
                lucky_items = lucky_contents.find_all('h4')
                lucky_ansi = "```ansi\n"
                for i in range(len(lucky_items)):
                    if i < len(lucky_col_name):
                        label = lucky_col_name[i]
                        val = lucky_items[i].text.strip()
                        # 針對不同的開運項目可以給予不同顏色
                        color = "\u001b[33m" if "數字" in label else "\u001b[32m"
                        lucky_ansi += format_line(label, val, color) + "\n"
                lucky_ansi += "```"

                embed = discord.Embed(title=f"🌟 {astro_name} 今日運勢", url=url, color=discord.Color.gold())
                embed.add_field(name=f"📌 今日短評", value=f"**{item.text}**", inline=False)

                embed.add_field(name="🍀 今日開運指南", value=lucky_ansi, inline=False)
                embed.set_footer(text=f"查詢時間 {datetime.datetime.now().strftime('%H:%M')}")

                await inter.response.edit_message(content=None, embed=embed, view=None)
            except Exception as e:
                await inter.response.edit_message(content=f"抓取資料失敗：{e}", view=None)

        select.callback = select_callback
        view.add_item(select)
        await interaction.response.send_message("請選擇星座：", view=view, ephemeral=True)

async def setup(bot):
    await bot.add_cog(HoroscopeCog(bot))