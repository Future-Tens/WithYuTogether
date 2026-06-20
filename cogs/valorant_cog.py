import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta
import asyncio
import json
from utils.valorant_api import ValorantAPI

class ValorantCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # 💡 共享 WithYouTogether 的中央資料庫大腦
        self.db = bot.get_cog("TRPGCog").db if bot.get_cog("TRPGCog") else None

    def __lazy_init_db(self):
        """確保 db 連線安全，並在必要時對現有 SQLite 表進行自動遷移補齊"""
        if not self.db:
            from utils.db_manager import DatabaseManager
            self.db = DatabaseManager()
        
        # 💡 自動遷移大腦：動態檢查並追加缺失的快取欄位，防止 sqlite3.OperationalError 崩潰
        try:
            conn = self.db.get_connection()
            cursor = conn.cursor()
            
            # 取得 valorant_accounts 資料表當前的所有欄位資訊
            cursor.execute("PRAGMA table_info(valorant_accounts)")
            existing_columns = [row["name"] for row in cursor.fetchall()]
            
            # 定義新架構所需的追加欄位
            required_columns = {
                "peak_rank": "TEXT",
                "cached_mmr": "TEXT",
                "cached_matches": "TEXT",
                "last_updated": "TEXT"
            }
            
            migrated = False
            for col_name, col_type in required_columns.items():
                if col_name not in existing_columns:
                    cursor.execute(f"ALTER TABLE valorant_accounts ADD COLUMN {col_name} {col_type}")
                    migrated = True
            
            if migrated:
                conn.commit()
                print("💡 [WithYouTogether] 偵測到舊版資料庫，已成功為 valorant_accounts 資料表補齊緩存欄位！")
            
            conn.close()
        except Exception as e:
            print(f"⚠️ [WithYouTogether] 執行資料庫自動遷移時發生異常: {e}")

    def is_cache_valid(self, last_updated_str, minutes=15):
        """檢查本地快取時間戳是否仍在 15 分鐘有效期內"""
        if not last_updated_str:
            return False
        try:
            last_updated = datetime.fromisoformat(last_updated_str)
            return datetime.now() - last_updated < timedelta(minutes=minutes)
        except Exception:
            return False
        
    async def force_sync_user_data(self, v_acc):
        """底層同步大腦：強制呼叫 API 並將 MMR 與戰績寫入資料庫快取"""
        puuid = v_acc["puuid"]
        region = v_acc["region"]

        # 同時非同步請求兩組高階數據
        mmr_task = ValorantAPI.get_mmr_details(puuid, region)
        matches_task = ValorantAPI.get_match_history(puuid, region, limit=5, mode_filter="all") # 快取 5 場
        mmr_data, match_data = await asyncio.gather(mmr_task, matches_task)

        # 寫入 SQLite 本地快取
        if mmr_data and mmr_data.get("status") == 200:
            self.db.update_v_cache(v_acc["user_id"], cached_mmr=mmr_data)
        if match_data and match_data.get("status") == 200:
            self.db.update_v_cache(v_acc["user_id"], cached_matches=match_data)
        
        return mmr_data, match_data

    # ==========================================
    # 📌 指令一：/v綁定
    # ==========================================
    @app_commands.command(name="v綁定", description="綁定你的 Valorant 帳號，開啟一鍵查詢與榮譽榜系統")
    @app_commands.describe(riot_id="你的遊戲內名稱 (例如: 波貝)", tagline="你的 Tag (例如: TW1)")
    async def v_bind(self, interaction: discord.Interaction, riot_id: str, tagline: str):
        await interaction.response.defer(ephemeral=True)
        self.__lazy_init_db()

        # 去 API 驗證帳號是否存在並抓取 PUUID
        account_data = await ValorantAPI.fetch_puuid(riot_id, tagline)

        if not account_data:
            return await interaction.followup.send("❌ 找不到該特戰帳號，請確認 ID 與 Tag 輸入正確。")
        if "error" in account_data:
            return await interaction.followup.send(f"❌ {account_data['error']}")

        # 寫入 SQLite 綁定表
        self.db.bind_v_account(
            user_id=interaction.user.id,
            riot_id=account_data["riot_id"],
            tagline=account_data["tagline"],
            puuid=account_data["puuid"],
            region=account_data["region"]
        )

        # 💡 立即觸發首次同步，避免玩家首次查詢時快取為空
        v_acc = self.db.get_v_account(interaction.user.id)
        await self.force_sync_user_data(v_acc)

        embed = discord.Embed(
            title="🎯 特戰核心模組：帳號綁定成功",
            description=f"歡迎加入 WithYouTogether 特戰戰線！",
            color=discord.Color.from_rgb(250, 68, 85) # 特戰經典紅
        )
        embed.add_field(name="👤 綁定特務", value=f"`{account_data['riot_id']} #{account_data['tagline']}`", inline=True)
        embed.add_field(name="🌐 伺服器區域", value=f"`{account_data['region'].upper()}`", inline=True)
        embed.add_field(name="🆔 唯一識別碼 (PUUID)", value=f"`{account_data['puuid'][:12]}...`", inline=False)
        embed.set_footer(text="現在你可以直接輸入 `/v生涯` 或 `/v近期戰績` 來檢閱數據！")

        await interaction.followup.send(embed=embed)


    # ==========================================
    # 📌 指令二：/v更新 (冷卻防刷：5分鐘)
    # ==========================================
    @app_commands.command(name="v更新", description="手動強制與 Riot 伺服器同步你最新的生涯段位與近期戰績數據")
    @app_commands.checks.cooldown(1, 300, key=lambda i: i.user.id) # 💡 5分鐘防刷冷卻
    async def v_update(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        self.__lazy_init_db()

        v_acc = self.db.get_v_account(interaction.user.id)
        if not v_acc:
            return await interaction.followup.send("❌ 你尚未綁定帳號，請先使用 `/v綁定`。")

        await self.force_sync_user_data(v_acc)
        
        embed = discord.Embed(
            title="⚡ 數據庫強制同步成功",
            description=f"已成功刷新特務 **{v_acc['riot_id']} #{v_acc['tagline']}** 的最新數據快取！",
            color=discord.Color.green()
        )
        embed.set_footer(text="現在輸入 /v生涯 或 /v近期戰績 將會呈現 100% 精準的即時面板！")
        await interaction.followup.send(embed=embed)

    @v_update.error
    async def v_update_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        """手動更新冷卻錯誤攔截"""
        if isinstance(error, app_commands.CommandOnCooldown):
            minutes = int(error.retry_after // 60)
            seconds = int(error.retry_after % 60)
            await interaction.response.send_message(f"❌ 技能冷卻中！請等待 `{minutes}分 {seconds}秒` 再進行更新，避免遭到 Riot 防刷鎖定。", ephemeral=True)


    # ==========================================
    # 📌 指令三：/v生涯 (優先自本地快取讀取)
    # ==========================================
    @app_commands.command(name="v生涯", description="檢閱精準的當季積分 ELO、勝率與特務專精")
    @app_commands.describe(target="想要查詢的隊友 (不輸入則預設查詢自己)")
    async def v_career(self, interaction: discord.Interaction, target: discord.User = None):
        await interaction.response.defer()
        self.__lazy_init_db()

        search_user = target if target else interaction.user
        v_acc = self.db.get_v_account(search_user.id)
        if not v_acc:
            msg = "你尚未綁定帳號！請先使用 `/v綁定`。" if not target else f"{search_user.display_name} 尚未綁定特戰帳號。"
            return await interaction.followup.send(msg)

        # 💡 檢查快取是否有效 (15 分鐘)
        mmr_data = None
        is_cached = self.is_cache_valid(v_acc.get("last_updated"))
        
        if is_cached and v_acc.get("cached_mmr"):
            mmr_data = json.loads(v_acc["cached_mmr"])

        # 快取失效或沒有快取時，自動呼叫 API 進行同步
        if not mmr_data:
            mmr_data, _ = await self.force_sync_user_data(v_acc)
            # 同步完重新撈取最新 db
            v_acc = self.db.get_v_account(search_user.id)

        if not mmr_data or mmr_data.get("status") != 200:
            return await interaction.followup.send("⚠️ 該帳號本賽季可能尚未進行過積分賽，或 API 讀取異常。")

        data = mmr_data.get("data", {})
        current_data = data.get("current_data", {})
        
        # 💡 正確解法：不盲猜，直接使用 API 提供的最新官方徽章網址
        rank_icon_url = current_data.get("images", {}).get("large")
        rank_name = current_data.get("currenttierpatched", "未排名 (Unranked)")
        elo = current_data.get("elo", 0)
        rr = current_data.get("ranking_in_tier", 0)
        mmr_change = current_data.get("mmr_change_to_last_game", 0)
        
        arrow = "🔺" if mmr_change >= 0 else "🔻"
        change_text = f"{arrow} `{mmr_change}`" if mmr_change != 0 else "➖ `0`"

        embed = discord.Embed(
            title=f"👤 {v_acc['riot_id']} #{v_acc['tagline']} | 特務檔案",
            color=discord.Color.from_rgb(4, 212, 140)
        )
        if rank_icon_url:
            embed.set_thumbnail(url=rank_icon_url)

        embed.add_field(name="🎖️ 當前階級", value=f"**{rank_name}**", inline=True)
        embed.add_field(name="📊 競技分數 (RR)", value=f"`{rr} / 100` ({change_text})", inline=True)
        embed.add_field(name="⚡ 總體 ELO", value=f"`{elo}`", inline=True)

        latest_season = data.get("by_season", {})
        if latest_season:
            current_season_key = list(latest_season.keys())[-1]
            season_info = latest_season[current_season_key]
            if not season_info.get("error"):
                wins = season_info.get("wins", 0)
                games = season_info.get("number_of_games", 0)
                win_rate = (wins / games * 100) if games > 0 else 0
                embed.add_field(name="🚩 本季戰績", value=f"`{win_rate:.1f}%` ({wins}勝 / {games}場)", inline=False)

        # 標明本數據來源
        cache_status = "⚡ 本地緩存數據" if is_cached else "🌐 即時同步數據"
        embed.set_footer(text=f"數據由 WithYouTogether 提供 | {cache_status}", icon_url=search_user.display_avatar.url)
        await interaction.followup.send(embed=embed)

    # ==========================================
    # 📌 指令三：/v近期戰績
    # ==========================================
    @app_commands.command(name="v近期戰績", description="查看最近對局的數據")
    @app_commands.choices(模式=[
        app_commands.Choice(name="⚔️ 競技模式", value="competitive"),
        app_commands.Choice(name="🛡️ 一般模式", value="unrated"),
        app_commands.Choice(name="🔫 死鬥模式", value="deathmatch"),
        app_commands.Choice(name="⚡ 超速衝點", value="spikerush"),
        app_commands.Choice(name="🌐 全部對局", value="all")
    ])
    async def v_matches(self, interaction: discord.Interaction, 模式: app_commands.Choice[str] = None):
        await interaction.response.defer()
        self.__lazy_init_db()

        v_acc = self.db.get_v_account(interaction.user.id)
        if not v_acc:
            return await interaction.followup.send("你尚未綁定帳號！請先使用 `/v綁定`。")

        # 💡 核心優化：抓取篩選值（若未輸入則預設為 "all" 顯示所有對局）
        selected_mode = 模式.value if 模式 else "all"
        display_name = 模式.name if 模式 else "全部對局"

        # 💡 優先讀取本地快取
        match_data = None
        is_cached = self.is_cache_valid(v_acc.get("last_updated"))
        
        if is_cached and v_acc.get("cached_matches"):
            match_data = json.loads(v_acc["cached_matches"])

        # 快取失效自動同步
        if not match_data:
            _, match_data = await self.force_sync_user_data(v_acc)
            # 同步完重新撈取最新 db
            v_acc = self.db.get_v_account(interaction.user.id)

        if not match_data or match_data.get("status") != 200:
            return await interaction.followup.send("⚠️ 戰報讀取失敗，Riot 伺服器目前可能忙碌中。")

        matches = match_data.get("data", [])
        
        # 💡 核心優化：從快取的高階 5 場戰報中，直接在記憶體中過濾出指定模式
        if selected_mode != "all":
            matches = [m for m in matches if m.get("metadata", {}).get("mode", "").lower() == selected_mode.lower()]

        if not matches:
            return await interaction.followup.send(
                f"💡 本地快取最近的對局中沒有發現 **{display_name}** 模式。\n"
                f"這可能是因為你近期沒有玩該模式，或者快取未刷新。你可以輸入 `/v更新` 強制向 Riot 同步！",
                ephemeral=True
            )

        # 只顯示最多 3 場
        matches = matches[:3]

        embed = discord.Embed(
            title=f"⚔️ {v_acc['riot_id']} 的特戰高階戰報 ({display_name})",
            description="分析你最近對局的關鍵影響力指標",
            color=discord.Color.from_rgb(250, 68, 85)
        )

        for idx, match in enumerate(matches, 1):
            meta = match.get("metadata", {})
            mode = meta.get("mode", "未知模式")
            map_name = meta.get("map", "未知地圖")
            rounds_played = meta.get("rounds_played", 25)
            
            players = match.get("players", {}).get("all_players", [])
            me = next((p for p in players if p["puuid"] == v_acc["puuid"]), None)
            if not me: continue

            stats = me.get("stats", {})
            k = stats.get("kills", 0)
            d = stats.get("deaths", 0)
            a = stats.get("assists", 0)
            kda = (k + a) / d if d > 0 else k + a
            
            score = stats.get("score", 0)
            damage = stats.get("damage", 0)
            acs = int(score / rounds_played) if rounds_played > 0 else 0
            adr = int(damage / rounds_played) if rounds_played > 0 else 0
            
            hs = stats.get("headshots", 0)
            bs = stats.get("bodyshots", 0)
            ls = stats.get("legshots", 0)
            total_shots = hs + bs + ls
            hs_rate = (hs / total_shots * 100) if total_shots > 0 else 0

            my_team = me.get("team", "").lower()
            teams = match.get("teams", {})
            my_score, enemy_score = 0, 0
            if teams and my_team in teams:
                my_score = teams[my_team].get("rounds_won", 0)
                enemy_team = "blue" if my_team == "red" else "red"
                enemy_score = teams.get(enemy_team, {}).get("rounds_won", 0) if teams.get(enemy_team) else 0

            is_win = my_score > enemy_score
            result_tag = "🟢 WIN" if is_win else "🔴 LOSS"
            if mode == "Deathmatch": result_tag = "🔫 死鬥模式"

            score_text = f" `{my_score} : {enemy_score}`" if mode != "Deathmatch" else ""

            embed.add_field(
                name=f"第 {idx} 場：{result_tag} | {mode} ({map_name}){score_text}",
                value=f"• 常用特務: **{me.get('character', '未知')}**\n"
                      f"• 戰績表現: `{k}/{d}/{a}` (KDA: `{kda:.2f}`)\n"
                      f"• 平均 ACS / ADR: `{acs}` / `{adr}`\n"
                      f"• 精準爆頭率 (HS%): `{hs_rate:.1f}%`",
                inline=False
            )

        cache_status = "⚡ 本地緩存數據" if is_cached else "🌐 即時同步數據"
        embed.set_footer(text=f"數據由 WithYouTogether 提供 | {cache_status}", icon_url=interaction.user.display_avatar.url)
        await interaction.followup.send(embed=embed)

async def setup(bot):
    await bot.add_cog(ValorantCog(bot))