import hashlib
import time
import math
import discord
from discord import app_commands
from discord.ext import commands
import json
import os
import datetime
import random
from utils.trpg_engine import TRPGEngine

VERSION = "1.2.0"
AUTHOR = "波貝小語"


class TRPGCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.users_dir = "data/users"
        self.leaderboard_path = "data/trpg_leaderboard.json"
        self.content_dir = "data/content"

        # 💡 實作快取：初始化時載入
        self.load_all_content()
        
        os.makedirs(self.users_dir, exist_ok=True)
        os.makedirs("data/logs", exist_ok=True)

    def load_all_content(self):
        """讀取所有內容檔案並存入記憶體"""
        # 1. 快取職業內容
        self.jobs_cache = self.load_json(f"data/trpg_jobs.json", default_type=dict)
        # 2. 怪物與事件使用清單格式
        self.monsters_cache = self.load_json(f"{self.content_dir}/monsters.json", default_type=list)
        self.events_cache = self.load_json(f"{self.content_dir}/events.json", default_type=list)
        # 3. 道具與裝備合併成一個字典，方便用 ID 直接查詢 (O(1) 速度)
        items_list = self.load_json(f"{self.content_dir}/items.json", default_type=list)
        equips_list = self.load_json(f"{self.content_dir}/equips.json", default_type=list)
        
        # 合併並轉換格式：{ "item_id": {資料...} }
        self.items_cache = {i["id"]: i for i in (items_list + equips_list)}
        
        print(f"✅ TRPG 內容載入完成：{len(self.monsters_cache)} 種怪物, {len(self.items_cache)} 件物品")

    def get_user_path(self, user_id):
        return os.path.join(self.users_dir, f"{user_id}.json")


    def load_player(self, user_id):
        path = self.get_user_path(user_id)
        if not os.path.exists(path): return None
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)


    def save_player(self, user_id, data):
        path = self.get_user_path(user_id)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    
    def load_json(self, path, default_type=dict):
        if not os.path.exists(path): return default_type()
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def save_json(self, path, data):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)


    # --- 職業選擇---
    class JobSelectionView(discord.ui.View):
        def __init__(self, user_id, cog):
            super().__init__(timeout=120)
            self.user_id = user_id
            self.cog = cog
            self.jobs_data = self.cog.jobs_cache
            self.create_buttons()
        
        def create_buttons(self):
            """根據 JSON 動態產生職業按鈕"""
            for job_name, config in self.jobs_data.items():
                btn = discord.ui.Button(
                    label=f"{config['icon']}{job_name}", 
                    style=discord.ButtonStyle.primary,
                    custom_id=f"job_{job_name}"
                )
                btn.callback = self.make_callback(job_name, config)
                self.add_item(btn)

        def make_callback(self, name, config):
            async def callback(interaction: discord.Interaction):
                from_job_data = {
                    "job": name,
                    "base_stats": config["init_stats"],
                    "max_hp": 50 + config["hp_mod"],
                    "bonus_points": config["init_bonus_points"]
                }
                view = self.cog.StatAllocationView(self.user_id, self.cog, from_job_data)
                await interaction.response.edit_message(
                    content=f"你選擇了 **{name}**！{config['description']}\n現在請分配獎勵點數：",
                    embed=view.create_embed(), 
                    view=view,
                )
            return callback

    # --- 進階配點介面 ---
    class StatAllocationView(discord.ui.View):
        def __init__(self, user_id, cog, job_data):
            super().__init__(timeout=180)
            self.user_id = str(user_id)
            self.cog = cog
            self.job = job_data["job"]
            self.max_hp = job_data["max_hp"]
            self.initial_stats = job_data["base_stats"].copy()
            self.initial_points = job_data["bonus_points"]

            self.stats = self.initial_stats.copy()
            self.points = self.initial_points

        def create_embed(self):
            embed = discord.Embed(title=f"⚔️ 角色建立 - {self.job}", color=discord.Color.blue())
            embed.description = f"剩餘可分配點數：**{self.points}**"
            for k, v in self.stats.items():
                embed.add_field(name=f"📜 {k}", value=f"**{v}**", inline=True)
            embed.set_footer(text="點數分配完畢後點擊確認。")
            return embed

        async def update_message(self, interaction):
            """更新 Embed 內容與按鈕狀態"""
            embed = discord.Embed(
                title="⚔️ 角色初始化 - 屬性分配", 
                description=f"請分配點數來塑造你的英雄。剩餘點數: **{self.points}**",
                color=discord.Color.blue()
            )
            for k, v in self.stats.items():
                embed.add_field(name=f"📜 {k}", value=f"**{v}**", inline=True)
            
            embed.set_footer(text="點擊按鈕增加屬性，+5 按鈕可快速分配。")
            await interaction.response.edit_message(embed=embed, view=self)

        async def add_val(self, interaction, stat, amount):
            """通用加點邏輯"""
            if self.points >= amount:
                self.stats[stat] += amount
                self.points -= amount
                await self.update_message(interaction)
            else:
                await interaction.response.send_message(f"❌ 剩餘點數不足 {amount} 點！", ephemeral=True)

        # --- 第一行: +1 按鈕 ---
        @discord.ui.button(label="STR +1", style=discord.ButtonStyle.primary, row=0)
        async def str_1(self, interaction, button): await self.add_val(interaction, "STR", 1)
        
        @discord.ui.button(label="DEX +1", style=discord.ButtonStyle.primary, row=0)
        async def dex_1(self, interaction, button): await self.add_val(interaction, "DEX", 1)
        
        @discord.ui.button(label="INT +1", style=discord.ButtonStyle.primary, row=0)
        async def int_1(self, interaction, button): await self.add_val(interaction, "INT", 1)
        
        @discord.ui.button(label="PER +1", style=discord.ButtonStyle.primary, row=0)
        async def per_1(self, interaction, button): await self.add_val(interaction, "PER", 1)

        @discord.ui.button(label="LUK +1", style=discord.ButtonStyle.primary, row=0)
        async def luk_1(self, interaction, button): await self.add_val(interaction, "LUK", 1)

        # --- 第二行: +5 快捷按鈕 ---
        @discord.ui.button(label="STR +5", style=discord.ButtonStyle.secondary, row=1)
        async def str_5(self, interaction, button): await self.add_val(interaction, "STR", 5)
        
        @discord.ui.button(label="DEX +5", style=discord.ButtonStyle.secondary, row=1)
        async def dex_5(self, interaction, button): await self.add_val(interaction, "DEX", 5)
        
        @discord.ui.button(label="INT +5", style=discord.ButtonStyle.secondary, row=1)
        async def int_5(self, interaction, button): await self.add_val(interaction, "INT", 5)
        
        @discord.ui.button(label="PER +5", style=discord.ButtonStyle.secondary, row=1)
        async def per_5(self, interaction, button): await self.add_val(interaction, "PER", 5)

        @discord.ui.button(label="LUK +5", style=discord.ButtonStyle.secondary, row=1)
        async def luk_5(self, interaction, button): await self.add_val(interaction, "LUK", 5)

        # --- 第三行: 控制按鈕 ---
        @discord.ui.button(label="♻️ 重設", style=discord.ButtonStyle.danger, row=2)
        async def reset(self, interaction, button):
            # 💡 改為使用 initial 變數
            self.points = self.initial_points
            self.stats = self.initial_stats.copy()
            await self.update_message(interaction)

        @discord.ui.button(label="✅ 確認角色", style=discord.ButtonStyle.success)
        async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.points > 0:
                return await interaction.response.send_message(f"你還有 {self.points} 點沒用完！", ephemeral=True)

            import hashlib, time
            run_id = hashlib.md5(f"{self.user_id}-{time.time()}".encode()).hexdigest()[:8]

            new_player_data = {
                "job": self.job, # 儲存職業
                "health": self.max_hp,
                "max_health": self.max_hp,
                "level": 1,
                "exp": 0,
                "skill_points": 0,
                "attributes": self.stats,
                "stage": 0, # 從第 0 關開始，第一步會變成第 1 關
                "turns": 0,
                "gold": 0, # 新增金幣
                "equips": {
                    "weapon": None, "armor": None, "helmet": None, "accessory": None
                },
                "inventory": [],
                "active_monster": None,
                "run_id": run_id,
                "version": VERSION,
                "logs": [f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 冒險開始！職業：{self.job}"]
            }
            self.cog.save_player(self.user_id, new_player_data)
            await interaction.response.edit_message(content=f"⚔️ **冒險開始！**\n你的 Run ID 為 `{run_id}`，請點擊 `/探索` 開始你的傳奇冒險。", embed=None, view=None)

    @app_commands.command(name="開始冒險", description="選擇職業並開啟你的深淵之旅")
    async def start_adventure(self, interaction: discord.Interaction):

        if user_data := self.load_player(str(interaction.user.id)):
            return await interaction.response.send_message("你已經在深淵中了。若要重新開始，請先在 `/狀態` 中放棄冒險。", ephemeral=True)
        _description = ""
        for job_name, config in self.jobs_cache.items():
            _description+=f"**{config['icon']} {job_name}**：{config['description']}\n"
        # 顯示職業選擇說明 Embed
        embed = discord.Embed(
            title="🎭 選擇你的職業",
            description=(
                _description
            ),
            color=discord.Color.dark_purple()
        )
        
        view = self.JobSelectionView(interaction.user.id, self)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="排行榜", description="查看英雄榜")
    async def leaderboard(self, interaction: discord.Interaction):
        records = self.load_json(self.leaderboard_path)
        if not records:
            return await interaction.response.send_message("目前英雄榜空空如也。")

        # 排序：關卡由大到小，回合由小到大
        sorted_records = sorted(records, key=lambda x: (-x['max_stage'], x['total_turns']))[:15]
        
        embed = discord.Embed(title=f"🏆永恆深淵英雄榜(當前版本{VERSION})", color=discord.Color.gold())
        for i, r in enumerate(sorted_records, 1):
            embed.add_field(
                name=f"第 {i} 名: {r['user_name']} (LV{r['level']} {r['job']})",
                value=f"[{r['run_id']}]\n關卡: {r['max_stage']} | 回合: {r['total_turns']}\n死因: {r['cause_of_death']}\n遊戲版本: {r['version']}",
                inline=False
            )
        await interaction.response.send_message(embed=embed)

    def add_log(self, user_data, message):
        """將訊息加入當前冒險日誌"""
        if "logs" not in user_data:
            user_data["logs"] = []
        
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] 第 {user_data['stage']} 層: {message}"
        user_data["logs"].append(log_entry)
    

    def get_progress_bar(self, current, maximum, length=10):
            """產生視覺化進度條"""
            percent = max(0, min(current / maximum, 1)) # 確保在 0~1 之間
            filled_length = int(length * percent)
            bar = "█" * filled_length + "░" * (length - filled_length)
            return f"`[{bar}]` {current}/{maximum}"
    # 在 TRPGCog 類別內
    async def end_adventure(self, interaction, user_data, cause):
        """通用結算邏輯：處理玩家死亡或放棄"""
        run_id = user_data["run_id"]
        user_id = str(interaction.user.id)
        user_name = interaction.user.display_name
        
        # 1. 儲存詳細日誌
        log_content = "\n".join(user_data["logs"])
        log_filename = f"data/logs/run_{run_id}.txt"
        with open(log_filename, "w", encoding="utf-8") as f:
            f.write(f"角色名稱: {user_name}\n最終關卡: {user_data['stage']}\n死因/結算原因: {cause}\n")
            f.write("-" * 30 + "\n")
            f.write(log_content)

        # 2. 紀錄排行榜
        leaderboard = self.load_json(self.leaderboard_path, default_type=list)
        record = {
            "run_id": run_id,
            "user_name": user_name,
            "attr": user_data['stat'],
            "level": user_data['levle'],
            "job": user_data["job"],
            "max_stage": user_data["stage"],
            "total_turns": user_data["turns"],
            "cause_of_death": cause,
            "version": user_data["version"],
            "date": str(datetime.date.today())
        }
        leaderboard.append(record)
        self.save_json(self.leaderboard_path, leaderboard)

        # 3. 遺產處理
        inv = user_data.get("inventory", [])
        if inv:
            legacy_pool = self.load_json("data/trpg_legacy.json", default_type=list)
            legacy_pool.append({
                "item_id": random.choice(inv),
                "hero_name": user_name,
                "death_stage": user_data["stage"]
            })
            if len(legacy_pool) > 10: legacy_pool.pop(0)
            self.save_json("data/trpg_legacy.json", legacy_pool)

        # 4. 刪除存檔 (Permadeath)
        path = self.get_user_path(user_id)
        if os.path.exists(path):
            os.remove(path)

        embed = discord.Embed(
            title="🏁 冒險紀錄結算", 
            description=f"**{user_name}** 在第 {user_data['stage']} 層結束了旅程。\n**原因：{cause}**",
            color=discord.Color.gold() if "放棄" in cause else discord.Color.dark_red()
        )
        # 注意：這裡使用 followup 因為按鈕互動通常需要先 response
        await interaction.followup.send(content=f"冒險已正式終結。Run ID: `{run_id}`", embed=embed)


    # --- 探索與戰鬥介面 ---
    class ExplorationView(discord.ui.View):
        def __init__(self, user_id, cog, user_data):
            super().__init__(timeout=300)
            self.user_id = str(user_id)
            self.cog = cog
            self.data = user_data
            self.update_buttons() # 初始化按鈕狀態

        def get_room_type(self):
            """根據權重決定房間類型"""
            rand = random.random() * 100
            if rand < 85:
                return "普通"
            elif rand < 98.5:  # 85 + 13.5
                return "精英"
            else:
                return "魔王"
        
        def generate_monster(self, m_type="普通"):
            """
            統一產生怪物物件並套用倍率
            """
            stage = self.data["stage"]
            available = [m for m in self.cog.monsters_cache if m["min_stage"] <= stage]
            monster = random.choice(available).copy()

            # 1. 取得關卡縮放倍率 (例如每層 +2%)
            stage_scaling = 1 + (stage * 0.025)
            
            # 2. 取得房間類型倍率
            type_scaling_hp = 1.0
            type_scaling_atk = 1.0
            exp_mult = 1.0
            drop_mult = 1.0

            if m_type == "精英":
                monster["name"] = f"🔶 精英·{monster['name']}"
                type_scaling_hp = 1.5
                type_scaling_atk = 1.2
                exp_mult = 1.5
                drop_mult = 2.0
            elif m_type == "魔王":
                monster["name"] = f"💀 魔王·{monster['name']}"
                type_scaling_hp = 3.0
                type_scaling_atk = 1.5
                exp_mult = 3.0
                drop_mult = 10.0 # 魔王必定掉落

            # 3. 【核心優化】一次性寫入最終數值
            # 最終數值 = 基礎值 * 關卡縮放 * 房間類型縮放
            monster["hp"] = int(monster["hp"] * stage_scaling * type_scaling_hp)
            monster["max_hp"] = monster["hp"] # 同步設定最大血量供 UI 使用
            monster["atk"] = int(monster["atk"] * stage_scaling * type_scaling_atk)
            monster["def"] = monster.get("def", 0)
            monster["exp"] = int(monster.get("exp", 10) * stage_scaling * exp_mult)
            monster["drop_rate"] = min(1.0, monster.get("drop_rate", 0.1) * drop_mult)

            return monster


        def update_buttons(self):
            """根據狀態動態調整按鈕"""
            self.clear_items()
            
            # 如果正在戰鬥中，只顯示攻擊按鈕
            if self.data.get("active_monster"):
                btn = discord.ui.Button(label="⚔️ 繼續攻擊", style=discord.ButtonStyle.danger, custom_id="attack")
                btn.callback = self.attack_callback
                self.add_item(btn)
                return
            # 每 5 關觸發特殊事件 (不包含在普通關卡類型中)
            if self.data.get("event_ready"):
                route = discord.ui.Button(label="⛲ 休息點/特殊事件", style=discord.ButtonStyle.secondary)
                # 這裡 route_type 傳入 "特殊"
                async def callback(interaction): await self.route_callback(interaction, "特殊")
                route.callback = callback
                self.add_item(route)
                return
                
            if "pending_routes" not in self.data or self.data["pending_routes"] is None:
                self.data["pending_routes"] = {
                    "a": self.get_room_type(),
                    "b": self.get_room_type()
                }
                self.cog.save_player(self.user_id, self.data)

            # 如果沒有戰鬥，顯示雙路線選擇
            routes = self.data["pending_routes"]
            self.route_a_type = routes["a"]
            self.route_b_type = routes["b"]

            jobs_data = self.cog.jobs_cache
            passive = jobs_data.get(self.data["job"], {}).get("passive_config", {})
            per_mod = passive.get("per_threshold_mod", 0)
            # 能力預知
            if self.data["attributes"]['PER'] > (7 + per_mod + self.data["stage"]//2.5):
                label_a = f"🛤️ 路線甲 ({self.route_a_type})"
            else:
                label_a = "🛤️ 路線甲 (???)"
            # 職業預知
            if passive.get("can_see_future") or self.data["attributes"]['PER'] > (7 + per_mod + self.data["stage"]//1.5):
                label_b = f"🛤️ 路線乙 ({self.route_b_type})"
            else:
                label_b = "🛤️ 路線乙 (???)"


            route_a = discord.ui.Button(label=label_a, style=discord.ButtonStyle.secondary, custom_id="route_a")
            route_b = discord.ui.Button(label=label_b, style=discord.ButtonStyle.secondary, custom_id="route_b")
            async def callback_a(interaction): await self.route_callback(interaction, self.route_a_type)
            async def callback_b(interaction): await self.route_callback(interaction, self.route_b_type)
            route_a.callback = callback_a
            route_b.callback = callback_b
            self.add_item(route_a)
            self.add_item(route_b)

        async def route_callback(self, interaction: discord.Interaction, route_type):
            """點擊路線按鈕的回呼"""
            # 每次選擇更新user狀態
            self.data = self.cog.load_player(self.user_id)
            self.data["turns"] += 1

            # 如果是特殊事件，執行完後要關閉 Flag
            if route_type == "特殊":
                self.data["event_ready"] = False # 💡 結束事件，回到正常流程
                return await self.resolve_special_event(interaction)

            # 一般房間：增加關卡數並清除預選
            self.data["stage"] += 1
            self.data["pending_routes"] = None
            
            # 💡 重要判定：如果新關卡是 5 的倍數，標記「下次要進特殊事件」
            if self.data["stage"] % 5 == 0:
                self.data["event_ready"] = True

            if route_type =="特殊":
                return await self.resolve_special_event(interaction)
            elif route_type =="普通": 
                await self.resolve_normal_room(interaction)
            elif route_type =="精英":
                await self.resolve_elite_room(interaction)
            elif route_type =="魔王":
                await self.resolve_boss_room(interaction)

        async def resolve_special_event(self, interaction):
            """每 5 關的休息點/稀有事件"""
            # 1. 執行事件邏輯 (Rest vs Altar)
            rand = random.random()
            if rand < 0.85:
                lost_hp = self.data["max_health"] - self.data["health"]
                heal = math.ceil(lost_hp * 0.25)
                self.data["health"] += heal
                msg = f"🌿 **林間休息點**：你稍作喘息，恢復了 {heal} 點 HP。"
                self.cog.add_log(self.data, f"休息點回復了 {heal} HP")
            else:
                msg = f"✨ **神祕祭壇**：古老力量湧入，全屬性永久提升了！"
                for s in self.data["attributes"]: 
                    self.data["attributes"][s] += 1
                self.cog.add_log(self.data, "觸發稀有事件：全屬性 +1")

            # 2. 商店初始化 (如果還沒生成過)
            if not self.data.get("current_shop"):
                stage = self.data["stage"]
                available_ids = [
                    i_id for i_id, i_data in self.cog.items_cache.items() 
                    if i_data.get("min_stage", 0) <= stage
                ]
                sample_count = min(3, len(available_ids))
                rolled_items = random.sample(available_ids, sample_count)
                
                self.data["current_shop"] = {
                    "items": rolled_items,
                    "sold": [False] * sample_count
                }
            
            # 3. 儲存玩家狀態
            self.cog.save_player(self.user_id, self.data)

            # 4. 關鍵修正：將事件結果 msg 傳給 ShopView 的 Embed
            shop_view = self.cog.ShopView(self.user_id, self.cog, self.data)
            
            # 我們直接修改 generate_shop_embed 的內容，把事件訊息塞進去
            shop_embed = shop_view.generate_shop_embed()
            shop_embed.insert_field_at(0, name="🔔 事件結果", value=msg, inline=False)
            
            # 顯示商店介面，這裡不呼叫 finish_turn
            await interaction.response.edit_message(embed=shop_embed, view=shop_view)

        async def resolve_normal_room(self, interaction):
            """處理普通房間: 80% 怪物, 17% 事件, 3% 遺產 (依你 code 的權重)"""
            rand = random.random()
            stage = self.data["stage"]
            
            if rand < 0.8: # 怪物
                self.data["active_monster"] = self.generate_monster("普通")
                self.cog.add_log(self.data, f"遭遇怪物: {self.data['active_monster']['name']}")
                await self.attack_callback(interaction, reload=False) # 直接進入第一回合戰鬥
            
            elif rand < 0.97: # 事件
                event = random.choice(self.cog.events_cache)
                option = random.choice(event["options"])
                roll = TRPGEngine.roll_d20(self.data["attributes"][option["stat"]])
                success = roll >= option["dc"]
                res = option["success_text"] if success else option["fail_text"]
                if not success: self.data["health"] -= 2
                self.cog.add_log(self.data, f"事件: {event['id']} ({'成功' if success else '失敗'})")
                embed = discord.Embed(title="📜 隨機事件", description=f"**{event['id']}**\n{res}", color=discord.Color.blue())
                await self.finish_turn(interaction, embed)
                
            else: # 遺產
                await self.resolve_legacy(interaction)

        async def resolve_elite_room(self, interaction):
            """遭遇精英怪"""
            self.data["active_monster"] = self.generate_monster("精英")
            self.cog.add_log(self.data, f"⚠️ 遭遇精英怪: {self.data['active_monster']['name']}")
            await self.attack_callback(interaction, reload=False)

        async def resolve_boss_room(self, interaction):
            """遭遇魔王"""
            self.data["active_monster"] = self.generate_monster("魔王")
            self.cog.add_log(self.data, f"💀 遭遇魔王: {self.data['active_monster']['name']}")
            await self.attack_callback(interaction, reload=False)

        async def attack_callback(self, interaction: discord.Interaction, reload=True):
            """戰鬥邏輯核心"""
            # 每次戰鬥更新user狀態
            if reload: self.data = self.cog.load_player(self.user_id)

            self.data["turns"] += 1

            monster = self.data["active_monster"]
            jobs_data = self.cog.jobs_cache
            job_config = jobs_data.get(self.data["job"], {}).get("combat_config", {})

            # 💡 核心修正：計算總屬性並傳入
            items_db = self.cog.items_cache
            total_stats = TRPGEngine.get_total_stats(self.data, items_db)
            
            # 傳入 job_config 到 Engine
            p_dmg, m_dmg = TRPGEngine.calculate_combat(self.data, monster, job_config, total_stats)
            
            m_hp_bar = self.cog.get_progress_bar(max(0, monster['hp']), monster['max_hp'])
            result = f"你對 **{monster['name']}** 造成 {p_dmg} 傷害。\n"
            self.cog.add_log(self.data, f"你對 {monster['name']} 造成 {p_dmg} 傷害。怪物血量: {monster['hp']}/{monster['max_hp']}")
            if monster["hp"] <= 0:
                result += f"✨ 擊敗了怪物！獲得了 {monster.get('exp', 10)} 經驗以及 {monster.get('gold',0)} 金幣。"
                self.cog.add_log(self.data, f"勝利！獲得了 {monster.get('exp', 10)} 經驗以及 {monster.get('gold',0)} 金幣。")
                self.data["active_monster"] = None
                # 經驗與升級處理
                self.data["exp"] += monster.get("exp", 10)
                self.data["gold"] += monster.get("gold", 0)
                if TRPGEngine.check_level_up(self.data):
                    self.data["level"] += 1
                    sp = jobs_data.get(self.data["job"], {}).get("sp_per_level", 2)
                    hp = jobs_data.get(self.data["job"], {}).get("hp_per_level", 2)
                    maxhp = jobs_data.get(self.data["job"], {}).get("max_hp_per_level", 2)
                    self.data["skill_points"] += sp
                    self.data["max_health"] += maxhp 
                    self.data["health"] = int(min(self.data["max_health"], self.data["health"] + hp + maxhp))
                    result += f"\n🎊 **等級提升至 Lv.{self.data['level']}！**"
                    result += f"\n🎊 **血量上限提升了 {maxhp} ， 並恢復了 {hp}點！**"
                    result += f"\n🎊 **獲得了技能點數 {sp} 點，你還有 {self.data['skill_points']} 點未使用！！**"
                    self.cog.add_log(self.data, f"等級提升至 Lv.{self.data['level']}，血量上限提升了{maxhp}， 並恢復了 {hp}點！ 玩家血量:{self.data['health']}/{self.data['max_health']}")
                # 掉落處理
                if random.random() < monster.get("drop_rate", 0.1):
                    # 將快取字典的值轉為列表，並過濾掉不可掉落物品
                    dropable_pool = [i for i in self.cog.items_cache.values() if i.get("dropable", True)]
                    
                    if dropable_pool:
                        # 使用過濾後的列表與對應權重進行隨機選取
                        drop = random.choices(
                            dropable_pool, 
                            weights=[i.get("rarity", 0.1) for i in dropable_pool], 
                            k=1
                        )[0]
                        
                        if len(self.data.get("inventory", [])) < 5:
                            self.data["inventory"].append(drop["id"])
                            result += f"\n🎁 獲得道具: **{drop['name']}**"
            else:
                result += f"🏮 遭受反擊，失去 {m_dmg} HP。\n👾 怪物血量: {m_hp_bar}"
                self.cog.add_log(self.data, f"遭受反擊，失去 {m_dmg} HP。 玩家血量:{self.data['health']}/{self.data['max_health']}")
            embed = discord.Embed(title=f"⚔️ 第 {self.data['stage']} 層 - 戰鬥", description=result, color=discord.Color.red())
            await self.finish_turn(interaction, embed)

        async def resolve_legacy(self, interaction):
            """處理遺產房間"""
            pool = self.cog.load_json("data/trpg_legacy.json", default_type=list)
            if pool:
                legacy = pool.pop(random.randint(0, len(pool)-1))
                self.cog.save_json("data/trpg_legacy.json", pool)
                self.data.setdefault("inventory", []).append(legacy["item_id"])
                msg = f"🕯️ 發現英雄 **{legacy['hero_name']}** 的遺物！"
            else:
                msg = "空蕩蕩的房間，你在角落撿到一點乾糧 (HP+5)"
                self.data["health"] = min(self.data["max_health"], self.data["health"] + 5)
            log = msg.replace("*","")
            self.cog.add_log(self.data, log)
            embed = discord.Embed(title="🕯️ 英雄遺跡", description=msg, color=discord.Color.gold())
            await self.finish_turn(interaction, embed)

        async def finish_turn(self, interaction, embed):
            """統整更新 UI 與存檔"""
            # 更新血條與狀態顯示
            p_hp_bar = self.cog.get_progress_bar(self.data['health'], self.data['max_health'])
            embed.add_field(name="❤️ 你的狀態", value=p_hp_bar, inline=False)
            embed.add_field(name="⏳ 總回合", value=f"`{self.data['turns']}`", inline=True)
            embed.add_field(name="🚩 進度", value=f"`第 {self.data['stage']} 層`", inline=True)

            # 存檔
            self.cog.save_player(self.user_id, self.data)

            # 判斷生死
            if self.data["health"] <= 0:
                self.clear_items()
                await interaction.response.edit_message(embed=embed, view=self)
                await self.cog.end_adventure(interaction, self.data, f"在第 {self.data['stage']} 層戰死")
            else:
                self.update_buttons() # 重新根據有無怪物刷新按鈕
                await interaction.response.edit_message(embed=embed, view=self)
            

    # --- 新增探索指令 ---
    @app_commands.command(name="探索", description="深入深淵的一個房間")
    async def explore_command(self, interaction: discord.Interaction):

        if not(user_data := self.load_player(str(interaction.user.id))):
            return await interaction.response.send_message("你還沒有角色！請先使用 `/開始冒險`。", ephemeral=True)

        view = self.ExplorationView(interaction.user.id, self, user_data)
        
        embed = discord.Embed(
            title="🔦 準備進入房間", 
            description=f"當前層數：**第 {user_data['stage']} 層**\n點擊下方按鈕踏入未知...",
            color=discord.Color.light_gray()
        )
        await interaction.response.send_message(embed=embed, view=view)


    class LevelUpView(discord.ui.View):
        def __init__(self, user_id, cog, user_data):
            super().__init__(timeout=180)
            self.user_id = str(user_id)
            self.cog = cog
            self.data = user_data
            
            # 紀錄進入介面時的原始狀態 (用於重設功能)
            self.initial_points = user_data["skill_points"]
            self.initial_stats = user_data["attributes"].copy()
            
            # 當前操作中的數值
            self.current_points = self.initial_points
            self.current_stats = self.initial_stats.copy()

        def create_embed(self):
            embed = discord.Embed(
                title="🆙 屬性強化中心",
                description=f"目前剩餘點數：**{self.current_points}**",
                color=discord.Color.blue()
            )
            for stat, val in self.current_stats.items():
                # 顯示原始數值 -> 加點後的數值
                diff = val - self.initial_stats[stat]
                change_text = f"(+{diff})" if diff > 0 else ""
                embed.add_field(name=f"📜 {stat}", value=f"**{val}** {change_text}", inline=True)
            
            embed.set_footer(text="點擊下方按鈕進行強化，確認後將永久保存。")
            return embed

        async def add_val(self, interaction, stat, amount):
            if self.current_points >= amount:
                self.current_stats[stat] += amount
                self.current_points -= amount
                await interaction.response.edit_message(embed=self.create_embed(), view=self)
            else:
                await interaction.response.send_message(f"❌ 剩餘點數不足 {amount} 點！", ephemeral=True)

        # --- 第一行: +1 按鈕 ---
        @discord.ui.button(label="STR +1", style=discord.ButtonStyle.primary, row=0)
        async def str_1(self, interaction, button): await self.add_val(interaction, "STR", 1)
        
        @discord.ui.button(label="DEX +1", style=discord.ButtonStyle.primary, row=0)
        async def dex_1(self, interaction, button): await self.add_val(interaction, "DEX", 1)
        
        @discord.ui.button(label="INT +1", style=discord.ButtonStyle.primary, row=0)
        async def int_1(self, interaction, button): await self.add_val(interaction, "INT", 1)
        
        @discord.ui.button(label="PER +1", style=discord.ButtonStyle.primary, row=0)
        async def per_1(self, interaction, button): await self.add_val(interaction, "PER", 1)

        @discord.ui.button(label="LUK +1", style=discord.ButtonStyle.primary, row=0)
        async def luk_1(self, interaction, button): await self.add_val(interaction, "LUK", 1)

        # --- 第二行: +5 快捷按鈕 ---
        @discord.ui.button(label="STR +5", style=discord.ButtonStyle.secondary, row=1)
        async def str_5(self, interaction, button): await self.add_val(interaction, "STR", 5)
        
        @discord.ui.button(label="DEX +5", style=discord.ButtonStyle.secondary, row=1)
        async def dex_5(self, interaction, button): await self.add_val(interaction, "DEX", 5)
        
        @discord.ui.button(label="INT +5", style=discord.ButtonStyle.secondary, row=1)
        async def int_5(self, interaction, button): await self.add_val(interaction, "INT", 5)
        
        @discord.ui.button(label="PER +5", style=discord.ButtonStyle.secondary, row=1)
        async def per_5(self, interaction, button): await self.add_val(interaction, "PER", 5)

        @discord.ui.button(label="LUK +5", style=discord.ButtonStyle.secondary, row=1)
        async def luk_5(self, interaction, button): await self.add_val(interaction, "LUK", 5)

        # --- 第三行: 功能按鈕 ---
        @discord.ui.button(label="♻️ 重設", style=discord.ButtonStyle.danger, row=2)
        async def reset(self, interaction, button):
            self.current_points = self.initial_points
            self.current_stats = self.initial_stats.copy()
            await interaction.response.edit_message(embed=self.create_embed(), view=self)

        @discord.ui.button(label="✅ 確認強化", style=discord.ButtonStyle.success, row=2)
        async def confirm(self, interaction, button):
            # 將結果寫回玩家資料
            self.data["skill_points"] = self.current_points
            self.data["attributes"] = self.current_stats
            # 儲存玩家資料
            self.cog.save_player(self.user_id, self.data)
            
            await interaction.response.edit_message(content="🎉 **角色強化完成！** 你的新力量已覺醒。", embed=None, view=None)



    # 在 TRPGCog 中新增指令
    @app_commands.command(name="分配屬性", description="使用升級獲得的點數強化你的各項能力")
    async def allocate_points(self, interaction: discord.Interaction):
        # 1. 讀取玩家資料
        if not(user_data:=self.load_player(str(interaction.user.id))):
            return await interaction.response.send_message("你還沒有建立角色，請先使用 `/開始冒險`。", ephemeral=True)


        # 2. 檢查是否有剩餘點數
        if user_data.get("skill_points", 0) <= 0:
            return await interaction.response.send_message(
                f"你目前沒有可分配的點數 (Lv.{user_data['level']})。請透過戰鬥升級來獲取點數！", 
                ephemeral=True
            )

        # 3. 呼叫配點介面
        view = self.LevelUpView(interaction.user.id, self, user_data)
        await interaction.response.send_message(embed=view.create_embed(), view=view, ephemeral=True)

    # --- 狀態管理介面---
    class StatusView(discord.ui.View):
        def __init__(self, user_id, cog, user_data):
            super().__init__(timeout=60)
            self.user_id = str(user_id)
            self.cog = cog
            self.data = user_data

        @discord.ui.button(label="🏳️ 放棄冒險", style=discord.ButtonStyle.gray)
        async def give_up(self, interaction: discord.Interaction, button: discord.ui.Button):
            # 為了防止誤觸，我們發送一個確認訊息
            confirm_view = discord.ui.View()
            
            # 定義確認按鈕
            async def yes_callback(itn):
                await itn.response.defer()
                # 呼叫剛剛搬到 Cog 的結算邏輯
                await self.cog.end_adventure(itn, self.data, "主動放棄冒險")
                # 結算後停用原本的狀態介面
                self.stop()

            async def no_callback(itn):
                await itn.response.edit_message(content="繼續你的旅程吧，冒險者！", view=None)

            btn_yes = discord.ui.Button(label="確定放棄", style=discord.ButtonStyle.danger)
            btn_no = discord.ui.Button(label="點錯了", style=discord.ButtonStyle.secondary)
            btn_yes.callback = yes_callback
            btn_no.callback = no_callback
            
            confirm_view.add_item(btn_yes)
            confirm_view.add_item(btn_no)

            await interaction.response.send_message("⚠️ **確定要放棄目前的冒險嗎？**\n這將會刪除存檔並進行結算（包含遺產與日誌）。", view=confirm_view, ephemeral=True)

    # --- 新增狀態指令 ---
    @app_commands.command(name="狀態", description="查看當前冒險狀態與詳細數據")
    async def status(self, interaction: discord.Interaction):
        if not (user_data := self.load_player(str(interaction.user.id))):
            return await interaction.response.send_message("目前沒有進行中的冒險。", ephemeral=True)
        
        items_db = self.items_cache

        # 1. 準備數值文字
        req_exp = TRPGEngine.get_required_exp(user_data['level'])
        exp_bar = self.get_progress_bar(user_data['exp'], req_exp)
        hp_bar = self.get_progress_bar(user_data['health'], user_data['max_health'])
        
        # 💡 裝備文字
        eq = user_data.get("equips", {})
        eq_text = ""
        for slot, i_id in eq.items():
            name = items_db.get(i_id, {"name": "`(空)`"})["name"]
            slot_name = {"weapon": "⚔️ 武器", "armor": "🛡️ 防具", "helmet": "🪖 頭盔", "accessory": "💍 飾品"}.get(slot, slot)
            eq_text += f"{slot_name}: {name}\n"
        
        # 💡 屬性文字 (必須在 Embed 之前定義)
        attr_text = "\n".join([f"**{k}**: `{v}`" for k, v in user_data['attributes'].items()])

        # 2. 建立 Embed
        embed = discord.Embed(title=f"🛡️ {interaction.user.display_name} (Lv.{user_data['level']})", color=discord.Color.green())
        embed.add_field(name="❤️ HP 狀態", value=hp_bar, inline=False)
        embed.add_field(name="🔷 經驗進度", value=exp_bar, inline=False)
        embed.add_field(name="📊 角色屬性", value=attr_text, inline=True)
        embed.add_field(name="🛡️ 當前武裝", value=eq_text, inline=True)
        embed.add_field(name="🪙 持有金幣", value=user_data["gold"], inline=False)
        embed.add_field(name="🚩 進度", value=f"第 {user_data['stage']} 層\n共 {user_data['turns']} 回合", inline=True)
        
        view = self.StatusView(interaction.user.id, self, user_data)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    class InventoryView(discord.ui.View):
        def __init__(self, user_id, cog, user_data):
            super().__init__(timeout=60)
            self.user_id = str(user_id)
            self.cog = cog
            self.data = user_data
            self.items_db = cog.items_cache

        def generate_embed(self):
            embed = discord.Embed(title="🎒 冒險者的背包", color=discord.Color.dark_green())
            inv = self.data.get("inventory", [])
            if not inv:
                embed.description = "你的背包空空如也，只有幾隻蜘蛛在那裡結網。"
            else:
                item_desc = ""
                for i_id in inv:
                    item = self.items_db.get(i_id)
                    item_desc += f"• **{item['name']}**\n└ {item['description']}\n"
                embed.description = item_desc
                # 抓取背包裡第一個物品的 ID
                first_item_id = inv[0] 
                # 從字典中查找該物品的詳細資料
                first_item_data = self.items_db.get(first_item_id)
                if first_item_data and "icon_file" in first_item_data:
                    file_url = self.items_db[first_item_id]["icon_file"]
                    embed.set_thumbnail(url=file_url)
                        
            embed.set_footer(text=f"容量限制：{len(inv)} / 5")
            return embed

        @discord.ui.button(label="⚡ 使用/裝備第一格", style=discord.ButtonStyle.success)
        async def use_first(self, interaction: discord.Interaction, button: discord.ui.Button):
            # 同步最新存檔
            self.data = self.cog.load_player(self.user_id)
            inv = self.data.get("inventory", [])
            if not inv: return await interaction.response.send_message("背包空空如也。", ephemeral=True)

            item_id = inv[0] # 核心策略：只能用第一個
            item = self.items_db.get(item_id)
            
            # 裝備類型判定
            if item["type"] in ["weapon", "armor", "helmet", "accessory"]:
                slot = item["type"]
                old_item = self.data.get("equips", {}).get(slot)
                
                inv.pop(0) # 穿上
                self.data.setdefault("equips", {})[slot] = item_id
                if old_item: inv.append(old_item) # 舊裝備排隊到最後面
                msg = f"🛡️ 裝備了 **{item['name']}**。"
            else:
                # 消耗品邏輯
                inv.pop(0)
                item = self.items_db.get(item_id)
                msg = f"你使用了 **{item['name']}**。"

                if item["type"] == "heal":
                    heal_val = item["value"]
                    passive = self.cog.jobs_cache.get(self.data.get("job"), {}).get('passive_config')
                    heal_efficiency = passive.get('heal_efficiency')
                    if heal_efficiency != 1:
                        heal_val = int(heal_val * heal_efficiency) # 效果降低 40%
                        msg = f"使用了 {item['name']}，但因被動效果回復了 {heal_val} 點。"
                    else:
                        msg = f"使用了 {item['name']}，回復了 {heal_val} 點。"
                    self.data["health"] = min(self.data["max_health"], self.data["health"] + heal_val)
                elif item["type"] == "stamina":
                    self.data["stamina"] += item["value"]
                    msg += f" 回復了 {item['value']} 點體力。"
                elif item["type"] == "boost":
                    attr = random.choice(["STR", "DEX", "INT", "PER"])
                    self.data["attributes"][attr] += item["value"]
                    #self.data["stress"] += 20
                    #msg += f" 永久提升了 {attr}，但感到一股精神壓力..."
                    msg += f"🧪 使用了 **{item['name']}**。"
                    msg += f" 能力值 {attr} 因應卷軸出現了奇妙的變化"
                elif item["type"] == "boost-all":
                    for attr in self.data["attributes"]:
                        attr += item["value"]
                    msg += f"🧪 使用了 **{item['name']}**。"
                    msg += f" 所有能力值回應了卷軸的力量"

            self.cog.add_log(self.data, f" 使用了 {item['name']}。")
            self.cog.save_player(self.user_id, self.data)
            await interaction.response.edit_message(embed=self.generate_embed(), view=self)
            await interaction.followup.send(msg, ephemeral=True)

        @discord.ui.button(label="🗑️ 處置裝備", style=discord.ButtonStyle.secondary)
        async def discard_equip(self, interaction: discord.Interaction, button: discord.ui.Button):
            inv = self.data.get("inventory", [])
            # 僅過濾出裝備
            options = []
            for idx, i_id in enumerate(inv):
                it = self.items_db.get(i_id)
                if it and it["type"] in ["weapon", "armor", "helmet", "accessory"]:
                    options.append(discord.SelectOption(label=it["name"], value=str(idx), description=it["description"]))

            if not options:
                return await interaction.response.send_message("❌ 背包中沒有可丟棄的裝備（消耗品無法丟棄）！", ephemeral=True)

            select = discord.ui.Select(placeholder="選擇要毀棄的裝備...", options=options)

            async def select_callback(itn: discord.Interaction):
                target_idx = int(select.values[0])
                removed_id = inv.pop(target_idx)
                self.cog.save_player(self.user_id, self.data)
                await itn.response.edit_message(content=f"已將 **{self.items_db[removed_id]['name']}** 丟棄。", view=None)

            select.callback = select_callback
            view = discord.ui.View(); view.add_item(select)
            await interaction.response.send_message("⚠️ 請選擇要永久移除的裝備：", view=view, ephemeral=True)

    # 在 TRPGCog 類別下新增指令
    @app_commands.command(name="背包", description="查看並使用你收集到的道具")
    async def open_inventory(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        if not (user_data := self.load_player(str(interaction.user.id))):
            return await interaction.response.send_message("死人不需要背包，請先 `/開始冒險`。", ephemeral=True)

        view = self.InventoryView(interaction.user.id, self, user_data)
        await interaction.followup.send(embed=view.generate_embed(), view=view)

    @app_commands.command(name="冒險回顧", description="使用 Run ID 查看過往的冒險日誌")
    async def review_log(self, interaction: discord.Interaction, run_id: str):
        file_path = f"data/logs/run_{run_id}.txt"
        
        if not os.path.exists(file_path):
            return await interaction.response.send_message("找不到該識別碼的日誌，可能已被系統自動清理或輸入錯誤。", ephemeral=True)
        
        # 以「檔案」形式發送，避免 Discord 字數限制 (2000字)
        await interaction.response.send_message(
            content=f"📜 這是冒險編號 `{run_id}` 的詳細紀錄：",
            file=discord.File(file_path),
            ephemeral=True
        )

    ##!!--xx 商店系統<1.2.0> xx--!!##
    class ShopView(discord.ui.View):
        def __init__(self, user_id, cog, user_data):
            super().__init__(timeout=300)
            self.user_id = str(user_id)
            self.cog = cog
            self.data = user_data
            self.items_cache = cog.items_cache
            self.shop_data = self.data.get("current_shop", {"items": [], "sold": []})
            self.create_buttons()

        def get_random_greeting(self):
            greetings = [
                "「只要代價足夠，深淵能提供你任何東西...」",
                "「金幣在死人手裡只是廢鐵，但在我這裡...它是命。」",
                "「歡迎來到深淵的唯一綠洲。別擔心，我收的稅比死神輕一點。」",
                "「看看我的收藏，或許能讓你多活五分鐘。」"
            ]
            return random.choice(greetings)
        
        def create_buttons(self):
            self.clear_items()
            stage = self.data["stage"]
            
            # 建立商品按鈕
            for idx, item_id in enumerate(self.shop_data["items"]):
                is_sold = self.shop_data["sold"][idx]
                item = self.items_cache.get(item_id)
                price = TRPGEngine.get_shop_price(item, stage)
                
                label = f"{item['name']} ({price}G)"
                style = discord.ButtonStyle.secondary
                if is_sold:
                    label = f"已售罄 - {item['name']}"
                    style = discord.ButtonStyle.danger
                
                btn = discord.ui.Button(label=label, style=style, disabled=is_sold, row=idx // 2)
                btn.callback = self.make_purchase_callback(idx, item, price)
                self.add_item(btn)

            # 前往下一關按鈕
            next_btn = discord.ui.Button(label="離開商店，繼續前行", style=discord.ButtonStyle.primary, row=2)
            next_btn.callback = self.leave_shop
            self.add_item(next_btn)

        def make_purchase_callback(self, idx, item, price):
            async def callback(interaction: discord.Interaction):
                # 檢查金幣與背包空間
                if self.data["gold"] < price:
                    taunts = [
                        "❌ 「窮鬼？在深淵裡，貧窮比怪物更致命。滾去多殺幾隻史萊姆吧。」",
                        "❌ 「你手裡的那些銅板連灰塵都買不起。別浪費我的時間。」"
                    ]
                    return await interaction.response.send_message(random.choice(taunts), ephemeral=True)
                if len(self.data["inventory"]) >= 5:
                    return await interaction.response.send_message("❌ 背包已滿！你拿不下更多東西了。", ephemeral=True)

                # 執行購買
                self.data["gold"] -= price
                self.data["inventory"].append(item["id"])
                self.shop_data["sold"][idx] = True
                self.data["current_shop"] = self.shop_data # 更新狀態
                
                self.cog.save_player(self.user_id, self.data)
                self.cog.add_log(self.data, f"從商人處購買了 {item['name']} (花費 {price}G)")
                
                self.create_buttons() # 重新整理 UI
                embed = self.generate_shop_embed()
                await interaction.response.edit_message(embed=embed, view=self)
                await interaction.followup.send(f"✅ 成功購買 **{item['name']}**！", ephemeral=True)
            return callback

        async def leave_shop(self, interaction: discord.Interaction):
            self.data["current_shop"] = None # 清空商店，下次重新生成
            self.data["event_ready"] = False
            self.cog.save_player(self.user_id, self.data)
            taunts = [
                        "「走吧，走吧...黑暗在前面等著你。」",
                        "「下次見面時，希望你帶了更多的金幣...或者更有趣的靈魂。」",
                        "「祝你好運，冒險者。雖然在深淵，好運通常是昂貴的。」",
                        "「別回頭，死神不喜歡猶豫不決的人。」",
                        "你告別了商人，沒入黑暗的走廊..."
                    ]
            goodbye_msg = random.choice(taunts)

            # 3. 💡 建立新的探索介面，讓按鈕重新出現
            # 這裡我們直接利用 ExplorationView 的初始化邏輯來產生下一層的按鈕
            next_view = self.cog.ExplorationView(self.user_id, self.cog, self.data)

            # 4. 產生回歸探索的 Embed
            embed = discord.Embed(
                title="🔦 繼續前行",
                description=f"{goodbye_msg}\n\n當前層數：**第 {self.data['stage']} 層**",
                color=discord.Color.light_gray()
            )

            # 5. 更新訊息：顯示告別語並「換回」探索按鈕
            await interaction.response.edit_message(content=None, embed=embed, view=next_view)

        def generate_shop_embed(self):
            embed = discord.Embed(title="🏮 深淵祕寶商人", color=discord.Color.dark_orange())
            embed.description = self.get_random_greeting()
            embed.add_field(name="💰 你的錢包", value=f"`{self.data['gold']} G`", inline=False)
            embed.set_footer(text=f"目前層數：{self.data['stage']} | 消耗品價格每 5 層翻倍")
            return embed


    # 在 TRPGCog 類別內建立一個簡單的連結 View
    class AboutLinksView(discord.ui.View):
        def __init__(self):
            super().__init__()
            # 官方網站按鈕
            self.add_item(discord.ui.Button(
                label="🌐 前往官方網站", 
                url="https://future-tens.github.io/WithYuTogether/",
                style=discord.ButtonStyle.link
            ))
            # 更新日誌按鈕
            self.add_item(discord.ui.Button(
                label="📜 查看更新日誌", 
                url="https://future-tens.github.io/WithYuTogether/updates.html",
                style=discord.ButtonStyle.link
            ))

    @app_commands.command(name="關於trpg", description="查看《永恆深淵的餘燼》官方網站與資訊")
    async def about_game(self, interaction: discord.Interaction):
        # 建立你指定的精簡版 Embed
        embed = discord.Embed(
            title="🔥 永恆深淵的餘燼 (Embers of the Eternal Abyss)",
            description=(
                "這是一款極高難度的 **Roguelike-lite TRPG**。\n"
                "死亡不是終點，而是下一位冒險者起點的餘燼。"
            ),
            color=discord.Color.dark_gold()
        )
        
        # 加入一個提示訊息，引導玩家點擊按鈕
        embed.add_field(
            name="📍 獲取更多資訊",
            value="詳細的職業介紹、關卡機制、裝備數據與開發進度，請至我們的官方網站查閱。",
            inline=False
        )
        
        # 頁尾資訊
        embed.set_footer(text=f"版本：v{VERSION} | 作者：{AUTHOR} | 願你在深淵中生存。")
        
        # 呼叫連結按鈕 View
        view = self.AboutLinksView()
        
        await interaction.response.send_message(embed=embed, view=view)

async def setup(bot):
    await bot.add_cog(TRPGCog(bot))