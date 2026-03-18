import discord
from discord.ext import commands
import os
import json
import asyncio

class MyBot(commands.Bot):
    def __init__(self):
        # 這裡的 Intents 可以根據需求調整
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        # 確保資料夾存在
        if not os.path.exists('data'):
            os.makedirs('data')
            
        # 載入所有 Cog
        for filename in os.listdir('./cogs'):
            if filename.endswith('.py'):
                try:
                    await self.load_extension(f'cogs.{filename[:-3]}')
                    print(f"✅ 已載入模組: {filename}")
                except Exception as e:
                    print(f"❌ 無法載入模組 {filename}: {e}")
        
        # 同步 Slash 指令到 Discord
        await self.tree.sync()
        print(f"🚀 Slash 指令已全球同步完成")

    async def on_ready(self):
        print(f'---')
        print(f'Logged in as {self.user} (ID: {self.user.id})')
        print(f'服務伺服器數量: {len(self.guilds)}')
        print(f'---')

# --- 讀取設定檔並啟動 ---
def run_bot():
    config_path = 'config.json'
    
    if not os.path.exists(config_path):
        print(f"錯誤：找不到 {config_path} 檔案！請參考範例建立。")
        return

    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    
    token = config.get("token")
    
    if not token or token == "你的_DISCORD_BOT_TOKEN_在這邊":
        print("錯誤：config.json 內的 token 為空或未設定。")
        return

    bot = MyBot()
    bot.remove_command('help') # 移除舊的 help 指令以使用自訂版本
    bot.run(token)

if __name__ == "__main__":
    run_bot()