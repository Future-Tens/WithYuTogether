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

VERSION = "1.1.0"
AUTHOR = "波貝小語"


class TRPGCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.users_path = "data/trpg_users.json"
        self.leaderboard_path = "data/trpg_leaderboard.json"
        self.jobs_path = "data/trpg_jobs.json"

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
            self.jobs_data = cog.load_json(cog.jobs_path)
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
                    view=view
                )
            return callback

    # --- 進階配點介面 ---
    class StatAllocationView(discord.ui.View):
        def __init__(self, user_id, cog, job_data):
            super().__init__(timeout=180)
            self.user_id = str(user_id)
            self.cog = cog
            self.job = job_data["job"]
            self.stats = job_data["base_stats"].copy()
            self.points = job_data["bonus_points"]
            self.max_hp = job_data["max_hp"]

        def create_embed(self):
            embed = discord.Embed(title=f"🏹 角色建立 - {self.job}", color=discord.Color.blue())
            embed.description = f"剩餘可分配點數：**{self.points}**"
            for stat, val in self.stats.items():
                embed.add_field(name=stat, value=str(val), inline=True)
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

        # --- 第二行: +5 快捷按鈕 ---
        @discord.ui.button(label="STR +5", style=discord.ButtonStyle.secondary, row=1)
        async def str_5(self, interaction, button): await self.add_val(interaction, "STR", 5)
        
        @discord.ui.button(label="DEX +5", style=discord.ButtonStyle.secondary, row=1)
        async def dex_5(self, interaction, button): await self.add_val(interaction, "DEX", 5)
        
        @discord.ui.button(label="INT +5", style=discord.ButtonStyle.secondary, row=1)
        async def int_5(self, interaction, button): await self.add_val(interaction, "INT", 5)
        
        @discord.ui.button(label="PER +5", style=discord.ButtonStyle.secondary, row=1)
        async def per_5(self, interaction, button): await self.add_val(interaction, "PER", 5)

        # --- 第三行: 控制按鈕 ---
        @discord.ui.button(label="♻️ 重設", style=discord.ButtonStyle.danger, row=2)
        async def reset(self, interaction, button):
            self.points = 20
            self.stats = {"STR": 10, "DEX": 10, "INT": 10, "PER": 10}
            await self.update_message(interaction)

        @discord.ui.button(label="✅ 確認角色", style=discord.ButtonStyle.success)
        async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
            if self.points > 0:
                return await interaction.response.send_message(f"你還有 {self.points} 點沒用完！", ephemeral=True)

            import hashlib, time
            run_id = hashlib.md5(f"{self.user_id}-{time.time()}".encode()).hexdigest()[:8]

            users = self.cog.load_json(self.cog.users_path)
            users[self.user_id] = {
                "job": self.job, # 儲存職業
                "health": self.max_hp,
                "max_health": self.max_hp,
                "level": 1,
                "exp": 0,
                "skill_points": 0,
                "attributes": self.stats,
                "stage": 0, # 從第 0 關開始，第一步會變成第 1 關
                "turns": 0,
                "inventory": [],
                "active_monster": None,
                "run_id": run_id,
                "version": VERSION,
                "logs": [f"[{datetime.datetime.now().strftime('%H:%M:%S')}] 冒險開始！職業：{self.job}"]
            }
            self.cog.save_json(self.cog.users_path, users)
            await interaction.response.edit_message(content=f"⚔️ **冒險開始！**\n你的 Run ID 為 `{run_id}`，請點擊 `/探索` 開始你的傳奇冒險。", embed=None, view=None)

    @app_commands.command(name="開始冒險", description="選擇職業並開啟你的深淵之旅")
    async def start_adventure(self, interaction: discord.Interaction):
        users = self.load_json(self.users_path)
        user_id = str(interaction.user.id)

        if user_id in users:
            return await interaction.response.send_message("你已經在深淵中了。若要重新開始，請先在 `/狀態` 中放棄冒險。", ephemeral=True)
        _description = ""
        jobs_data = self.load_json(self.jobs_path)
        for job_name, config in jobs_data.items():
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
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="排行榜", description="查看英雄榜")
    async def leaderboard(self, interaction: discord.Interaction):
        records = self.load_json(self.leaderboard_path)
        if not records:
            return await interaction.response.send_message("目前英雄榜空空如也。")

        # 排序：關卡由大到小，回合由小到大
        sorted_records = sorted(records, key=lambda x: (-x['max_stage'], x['total_turns']))[:10]
        
        embed = discord.Embed(title=f"🏆永恆深淵英雄榜(當前版本{VERSION})", color=discord.Color.gold())
        for i, r in enumerate(sorted_records, 1):
            embed.add_field(
                name=f"第 {i} 名: {r['user_name']} ({r['job']})",
                value=f"關卡: {r['max_stage']} | 回合: {r['total_turns']}\n死因: {r['cause_of_death']}\n`run_id`: `{r['run_id']}`\n遊戲版本: {r['version']}",
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
        users = self.load_json(self.users_path)
        if user_id in users:
            del users[user_id]
        self.save_json(self.users_path, users)

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
            self.content = cog.load_json("data/trpg_content.json")
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
            available = [m for m in self.content["monsters"] if m["min_stage"] <= stage]
            monster = random.choice(available).copy()

            # 1. 取得關卡縮放倍率 (例如每層 +2%)
            stage_scaling = 1 + (stage * 0.02)
            
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
            monster["def"] = int(monster.get("def", 0) * stage_scaling)
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
                # 立即存檔，防止玩家透過刷新指令來洗掉這兩條路
                users = self.cog.load_json(self.cog.users_path) # 先讀取完整存檔
                users[self.user_id] = self.data
                self.cog.save_json(self.cog.users_path, users)

            # 如果沒有戰鬥，顯示雙路線選擇
            routes = self.data["pending_routes"]
            self.route_a_type = routes["a"]
            self.route_b_type = routes["b"]

            jobs_data = self.cog.load_json(self.cog.jobs_path)
            passive = jobs_data.get(self.data["job"], {}).get("passive_config", {})
            per_mod = passive.get("per_threshold_mod", 0)
            # 能力預知
            if self.data["attributes"]['PER'] > (7 + per_mod + self.data["stage"]//2):
                label_a = f"🛤️ 路線甲 ({self.route_a_type})"
            else:
                label_a = "🛤️ 路線甲 (???)"
            # 職業預知
            if passive.get("can_see_future") or self.data["attributes"]['PER'] > (7 + per_mod + self.data["stage"]//4):
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
            rand = random.random()
            if rand < 0.85:
                # 休息事件: 恢復已損失生命 25% (向上取整)
                lost_hp = self.data["max_health"] - self.data["health"]
                heal = math.ceil(lost_hp * 0.25)
                self.data["health"] += heal
                msg = f"🌿 **第 {self.data['stage']} 關：林間休息點**\n你在這裡稍作喘息，恢復了 {heal} 點 HP。"
                self.cog.add_log(self.data, f"休息點回復了 {heal} HP")
            else:
                # 稀有事件: 全屬性 +1
                msg = f"✨ **第 {self.data['stage']} 關：神祕祭壇**\n古老的力量湧入體內，你的全屬性永久提升了！"
                for s in self.data["attributes"]: self.data["attributes"][s] += 1
                self.cog.add_log(self.data, "觸發稀有事件：全屬性 +1")

            embed = discord.Embed(title="🔔 特殊事件", description=msg, color=discord.Color.green())
            await self.finish_turn(interaction, embed)

        async def resolve_normal_room(self, interaction):
            """處理普通房間: 80% 怪物, 17% 事件, 3% 遺產 (依你 code 的權重)"""
            rand = random.random()
            stage = self.data["stage"]
            
            if rand < 0.8: # 怪物
                self.data["active_monster"] = self.generate_monster("普通")
                self.cog.add_log(self.data, f"遭遇怪物: {self.data['active_monster']['name']}")
                await self.attack_callback(interaction) # 直接進入第一回合戰鬥
            
            elif rand < 0.97: # 事件
                event = random.choice(self.content["events"])
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
            await self.attack_callback(interaction)

        async def resolve_boss_room(self, interaction):
            """遭遇魔王"""
            self.data["active_monster"] = self.generate_monster("魔王")
            self.cog.add_log(self.data, f"💀 遭遇魔王: {self.data['active_monster']['name']}")
            await self.attack_callback(interaction)

        async def attack_callback(self, interaction: discord.Interaction):
            """戰鬥邏輯核心"""
            monster = self.data["active_monster"]
            jobs_data = self.cog.load_json(self.cog.jobs_path)
            job_config = jobs_data.get(self.data["job"], {}).get("combat_config", {})

            # 傳入 job_config 到 Engine
            p_dmg, m_dmg = TRPGEngine.calculate_combat(self.data, monster, job_config)
            
            m_hp_bar = self.cog.get_progress_bar(max(0, monster['hp']), monster['max_hp'])
            result = f"你對 **{monster['name']}** 造成 {p_dmg} 傷害。\n"
            
            if monster["hp"] <= 0:
                result += f"✨ 擊敗了怪物！獲得了 {monster.get('exp', 10)} 經驗。"
                self.data["active_monster"] = None
                # 經驗與升級處理
                self.data["exp"] += monster.get("exp", 10)
                if TRPGEngine.check_level_up(self.data):
                    self.data["level"] += 1
                    sp = jobs_data.get(self.data["job"], {}).get("sp_per_level", 2)
                    hp = jobs_data.get(self.data["job"], {}).get("hp_per_level", 2)
                    maxhp = jobs_data.get(self.data["job"], {}).get("max_hp_per_level", 2)
                    self.data["skill_points"] += sp
                    self.data["max_health"] += maxhp 
                    self.data["health"] = min(self.data["max_health"], self.data["health"] + hp + maxhp)
                    result += f"\n🎊 **等級提升至 Lv.{self.data['level']}！**"
                    result += f"\n🎊 **血量上限提升了{maxhp}， 並恢復了 {hp}點！**"
                # 掉落處理
                if random.random() < monster.get("drop_rate", 0.1):
                    items = self.content.get("items", [])
                    drop = random.choices(items, weights=[i["rarity"] for i in items], k=1)[0]
                    if len(self.data.get("inventory", [])) < 5:
                        self.data["inventory"].append(drop["id"])
                        result += f"\n🎁 獲得道具: **{drop['name']}**"
            else:
                result += f"🏮 遭受反擊，失去 {m_dmg} HP。\n👾 怪物血量: {m_hp_bar}"
            
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
            users = self.cog.load_json(self.cog.users_path)
            users[self.user_id] = self.data
            self.cog.save_json(self.cog.users_path, users)

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
        users = self.load_json(self.users_path)
        user_id = str(interaction.user.id)

        if user_id not in users:
            return await interaction.response.send_message("你還沒有角色！請先使用 `/開始冒險`。", ephemeral=True)

        user_data = users[user_id]
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

        # --- 第二行: +5 快捷按鈕 ---
        @discord.ui.button(label="STR +5", style=discord.ButtonStyle.secondary, row=1)
        async def str_5(self, interaction, button): await self.add_val(interaction, "STR", 5)
        
        @discord.ui.button(label="DEX +5", style=discord.ButtonStyle.secondary, row=1)
        async def dex_5(self, interaction, button): await self.add_val(interaction, "DEX", 5)
        
        @discord.ui.button(label="INT +5", style=discord.ButtonStyle.secondary, row=1)
        async def int_5(self, interaction, button): await self.add_val(interaction, "INT", 5)
        
        @discord.ui.button(label="PER +5", style=discord.ButtonStyle.secondary, row=1)
        async def per_5(self, interaction, button): await self.add_val(interaction, "PER", 5)

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
            
            users = self.cog.load_json(self.cog.users_path)
            users[self.user_id] = self.data
            self.cog.save_json(self.cog.users_path, users)
            
            await interaction.response.edit_message(content="🎉 **角色強化完成！** 你的新力量已覺醒。", embed=None, view=None)

            # ... (以此類推重複 DEX, INT, PER 的按鈕) ...

            @discord.ui.button(label="✅ 確認分配", style=discord.ButtonStyle.success)
            async def confirm(self, interaction, button):
                # 更新存檔
                self.data["skill_points"] = self.points
                self.data["attributes"] = self.stats
                users = self.cog.load_json(self.cog.users_path)
                users[self.user_id] = self.data
                self.cog.save_json(self.cog.users_path, users)
                
                await interaction.response.edit_message(content="✅ 屬性已強化！", embed=None, view=None)

    # 在 TRPGCog 中新增指令
    @app_commands.command(name="分配屬性", description="使用升級獲得的點數強化你的各項能力")
    async def allocate_points(self, interaction: discord.Interaction):
        # 1. 讀取玩家資料
        users = self.load_json(self.users_path)
        user_id = str(interaction.user.id)

        if user_id not in users:
            return await interaction.response.send_message("你還沒有建立角色，請先使用 `/開始冒險`。", ephemeral=True)

        user_data = users[user_id]

        # 2. 檢查是否有剩餘點數
        if user_data.get("skill_points", 0) <= 0:
            return await interaction.response.send_message(
                f"你目前沒有可分配的點數 (Lv.{user_data['level']})。請透過戰鬥升級來獲取點數！", 
                ephemeral=True
            )

        # 3. 呼叫配點介面
        view = self.LevelUpView(interaction.user.id, self, user_data)
        await interaction.response.send_message(embed=view.create_embed(), view=view)

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
        users = self.load_json(self.users_path)
        user_id = str(interaction.user.id)
        if user_id not in users:
            return await interaction.response.send_message("目前沒有進行中的冒險。", ephemeral=True)
        
        d = users[user_id]
        req_exp = TRPGEngine.get_required_exp(d['level']) # 確保這裡呼叫正確
        exp_bar = self.get_progress_bar(d['exp'], req_exp)
        hp_bar = self.get_progress_bar(d['health'], d['max_health'])
        
        embed = discord.Embed(title=f"🛡️ {interaction.user.display_name} (Lv.{d['level']})", color=discord.Color.green())
        embed.add_field(name="❤️ HP 狀態", value=hp_bar, inline=False)
        embed.add_field(name="🔷 經驗進度", value=exp_bar, inline=False)
        
        attr_text = "\n".join([f"**{k}**: `{v}`" for k, v in d['attributes'].items()])
        embed.add_field(name="📊 角色屬性", value=attr_text, inline=True)
        embed.add_field(name="🚩 進度", value=f"第 {d['stage']} 層\n共 {d['turns']} 回合", inline=True)
        
        # 呼叫我們剛剛建立的 StatusView
        view = self.StatusView(interaction.user.id, self, d)
        await interaction.response.send_message(embed=embed, view=view)

    class InventoryView(discord.ui.View):
        def __init__(self, user_id, cog, user_data):
            super().__init__(timeout=60)
            self.user_id = str(user_id)
            self.cog = cog
            self.data = user_data
            self.content = cog.load_json("data/trpg_content.json")
            self.items_db = {i["id"]: i for i in self.content.get("items", [])}

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
                # --- 修正圖片讀取邏輯 ---
                # 抓取背包裡第一個物品的 ID
                first_item_id = inv[0] 
                # 從字典中查找該物品的詳細資料
                first_item_data = self.items_db.get(first_item_id)
                if first_item_data and "icon_file" in first_item_data:
                    file_url = self.items_db[first_item_id]["icon_file"]
                    embed.set_thumbnail(url=file_url)
                        
            embed.set_footer(text=f"容量限制：{len(inv)} / 5")
            return embed

        @discord.ui.button(label="使用第一個道具", style=discord.ButtonStyle.success)
        async def use_item(self, interaction: discord.Interaction, button: discord.ui.Button):
            users = self.cog.load_json(self.cog.users_path)
            self.data = users.get(self.user_id)
            
            if not self.data:
                return await interaction.response.send_message("找不到你的冒險紀錄。", ephemeral=True)
            inv = self.data.get("inventory", [])
            if not inv:
                return await interaction.response.send_message("你沒有道具可以動用。", ephemeral=True)

            item_id = inv.pop(0) # 為了簡化，每次使用背包的第一個
            item = self.items_db.get(item_id)
            msg = f"你使用了 **{item['name']}**。"

            if item["type"] == "heal":
                heal_val = item["value"]
                if self.data.get("job") == "血色祭司":
                    original_val = heal_val
                    heal_val = int(heal_val * 0.6) # 效果降低 40%
                    msg = f"使用了 {item['name']}，但因**貧血體質**，效果僅剩 {heal_val} (原 {original_val})。"
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
                msg += f" 能力值 {attr} 因應卷軸出現了奇妙的變化"

            # 儲存更新後的玩家資料
            users = self.cog.load_json(self.cog.users_path)
            users[self.user_id] = self.data
            self.cog.save_json(self.cog.users_path, users)
            self.cog.add_log(self.data, msg)
            await interaction.response.edit_message(embed=self.generate_embed(), view=self)
            await interaction.followup.send(msg, ephemeral=True)

    # 在 TRPGCog 類別下新增指令
    @app_commands.command(name="背包", description="查看並使用你收集到的道具")
    async def open_inventory(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        users = self.load_json(self.users_path)
        user_id = str(interaction.user.id)
        if user_id not in users:
            return await interaction.response.send_message("死人不需要背包，請先 `/開始冒險`。", ephemeral=True)

        view = self.InventoryView(interaction.user.id, self, users[user_id])
        await interaction.followup.send(embed=view.generate_embed(), view=view)

    @app_commands.command(name="冒險回顧", description="使用 Run ID 查看過往的冒險日誌")
    async def review_log(self, interaction: discord.Interaction, run_id: str):
        file_path = f"data/logs/run_{run_id}.txt"
        
        if not os.path.exists(file_path):
            return await interaction.response.send_message("找不到該識別碼的日誌，可能已被系統自動清理或輸入錯誤。", ephemeral=True)
        
        # 以「檔案」形式發送，避免 Discord 字數限制 (2000字)
        await interaction.response.send_message(
            content=f"📜 這是冒險編號 `{run_id}` 的詳細紀錄：",
            file=discord.File(file_path)
        )

    @app_commands.command(name="關於trpg", description="查看《永恆深淵的餘燼》遊戲說明與版本資訊")
    async def about_game(self, interaction: discord.Interaction):
        # 遊戲基本資訊
        version = "v1.1.0"  # 你可以隨時手動更新版本號
        author = "波貝小語"  # 這裡放你的大名
        _description = ""
        jobs_data = self.load_json(self.jobs_path)
        for job_name, config in jobs_data.items():
            _description+=f"**{config['icon']} {job_name}**：{config['description']}\n"

        embed = discord.Embed(
            title="🔥 永恆深淵的餘燼 (Embers of the Eternal Abyss)",
            description=(
                "這是一款極高難度的 **Roguelike TRPG**。\n"
                "死亡不是終點，而是下一位冒險者起點的餘燼。"
            ),
            color=discord.Color.dark_gold()
        )

        # 1. 🎭 職業指南 (核心內容)
        embed.add_field(
            name="🎭 職業指南 (Professions)",
            value=(
                _description
            ),
            inline=False
        )

        # 2. 🚩 關卡與生存 (Mechanics)
        embed.add_field(
            name="🚩 關卡機制",
            value=(
                "**雙路選擇**：每層提供兩條路徑，權重為：普通(85%)、精英(13.5%)、魔王(1.5%)。\n"
                "**補給點**：每 5 關觸發一次補給點事件。休息點恢復 **25% 已損失生命**，祭壇則永久提升全屬性。\n"
                "**戰鬥鎖定**：遭遇怪物後無法逃跑，必須決戰到一方倒下為止。"
            ),
            inline=False
        )
        
        # 3. 🕯️ 系統特色
        embed.add_field(
            name="💀 永久死亡", 
            value="HP 歸零即刪檔，但紀錄會寫入排行榜與 `.txt` 冒險日誌。", 
            inline=True
        )
        embed.add_field(
            name="🕯️ 遺產池", 
            value="死者有機率留下道具，供後續探險者在「遺跡房間」拾取。", 
            inline=True
        )
        
        # 4. 指令清單
        embed.add_field(
            name="🛠️ 冒險指令",
            value=(
                "`/開始冒險` - 選擇職業並建立角色\n"
                "`/探索` - 進入深淵房間（雙路線）\n"
                "`/狀態` - 檢查數值、升級進度或**放棄冒險**\n"
                "`/分配屬性` - 強化等級提升獲得的點數\n"
                "`/背包` - 使用藥水與神祕道具\n"
                "`/冒險回顧` - 輸入 Run ID 讀取過往日誌"
            ),
            inline=False
        )
        
        # 頁尾資訊
        embed.set_footer(text=f"版本：{version} | 作者：{author} | 祝你好運，冒險者。")
        
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="trpg版本日誌", description="查看《永恆深淵的餘燼》版本日誌")
    async def game_version(self, interaction: discord.Interaction):
        
        embed = discord.Embed(
            title="📖 歷版本改版資訊",
            description=(
                "🔥《永恆深淵的餘燼》🔥\n"
            ),
            color=discord.Color.dark_gold()
        )
        # V1.1.0
        embed.add_field(
            name="📜 Version 1.1.0",
            value=(
                "`職業` - 新增四種職業`破壁者`、`幻影行者`、`血色祭司`、`求道者`\n"
                "`探索` - 改為雙路線機制\n"
                "`怪物` - 新增更多怪物，有機率遇到高難度怪物\n"
                "`記錄` - 可以查看過關詳細資訊\n"
                "`狀態` - 現在可以透過狀態放棄該場遊戲\n"
                "`機制` - 修改經驗值公式`<50*(LVL^LVL)>`\n"
                "`Bug-fixed` - \n"
                "| 修復喝藥水會觸發異常不死無法結算的情形\n"
                "| 修復遺物異常無法掉落的情形\n"
                "| 修復怪物不會隨著關卡變強的情形\n"
                "\t- 因應上述修復修正怪物基礎能力值\n"

            ),
            inline=False
        )
        # V1.0.0
        embed.add_field(
            name="📜 Version 1.0.0",
            value=(
                "`/開始冒險` - 選擇職業並建立角色\n"
                "`/探索` - 進入深淵房間（雙路線）\n"
                "`/狀態` - 檢查數值、升級進度或**放棄冒險**\n"
                "`/分配屬性` - 強化等級提升獲得的點數\n"
                "`/背包` - 使用藥水與神祕道具\n"
                "`/排行榜` - 查看深淵中最偉大的先行者n"
                "`/冒險回顧` - 輸入 Run ID 讀取過往日誌"
            ),
            inline=False
        )
        # 頁尾資訊
        embed.set_footer(text=f"版本：{VERSION} | 作者：{AUTHOR} | 有任何問題歡迎找我。")
        
        await interaction.response.send_message(embed=embed)

async def setup(bot):
    await bot.add_cog(TRPGCog(bot))