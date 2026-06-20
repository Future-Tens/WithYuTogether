import sqlite3
import os
import json
from datetime import datetime

class DatabaseManager:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(DatabaseManager, cls).__new__(cls, *args, **kwargs)
            cls._instance._initialized = False
        return cls._instance

    def __init__(cls, db_path="data/with_you_together.db"):
        if cls._initialized:
            return
        cls.db_path = db_path
        os.makedirs(os.path.dirname(cls.db_path), exist_ok=True)
        cls.init_db()
        cls._initialized = True

    def get_connection(cls):
        """取得資料庫連接，並讓查詢結果可以像字典一樣用欄位名讀取"""
        conn = sqlite3.connect(cls.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(cls):
        """初始化全局資料表 (架構完整性的核心)"""
        with cls.get_connection() as conn:
            cursor = conn.cursor()
            
            # 1. 伺服器控制台設定表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id TEXT PRIMARY KEY,
                    enable_trpg INTEGER DEFAULT 1,
                    enable_tarot INTEGER DEFAULT 1,
                    enable_horoscope INTEGER DEFAULT 1,
                    welcome_channel TEXT,
                    welcome_msg TEXT
                )
            """)

            # 2. 全局使用者與平台經濟表 (整合 tarot_users.json)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    global_gold INTEGER DEFAULT 0,
                    last_daily TEXT,
                    tarot_history_count INTEGER DEFAULT 0
                )
            """)

            # 3. 提醒系統表 (整合 reminders.json)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS reminders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    channel_id TEXT,
                    remind_time TEXT,
                    content TEXT,
                    is_completed INTEGER DEFAULT 0
                )
            """)

            # 4. TRPG 玩家狀態主表 (整合原本 users/{user_id}.json 的基本欄位)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trpg_players (
                    user_id TEXT PRIMARY KEY,
                    run_id TEXT,
                    job TEXT,
                    health INTEGER,
                    max_health INTEGER,
                    level INTEGER DEFAULT 1,
                    exp INTEGER DEFAULT 0,
                    gold INTEGER DEFAULT 0,
                    stress INTEGER DEFAULT 0,
                    stage INTEGER DEFAULT 0,
                    turns INTEGER DEFAULT 0,
                    event_ready INTEGER DEFAULT 0,
                    version TEXT,
                    active_monster TEXT, -- 儲存序列化後的 JSON 字串
                    pending_routes TEXT, -- 儲存序列化後的 JSON 字串
                    equips TEXT          -- 儲存序列化後的 JSON 字串
                )
            """)

            # 5. TRPG 玩家屬性副表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trpg_player_attributes (
                    user_id TEXT PRIMARY KEY,
                    str_val INTEGER,
                    dex_val INTEGER,
                    int_val INTEGER,
                    per_val INTEGER,
                    luk_val INTEGER,
                    skill_points INTEGER DEFAULT 0,
                    FOREIGN KEY(user_id) REFERENCES trpg_players(user_id) ON DELETE CASCADE
                )
            """)

            # 6. TRPG 玩家背包關聯表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trpg_inventory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    item_id TEXT,
                    slot_position INTEGER,
                    FOREIGN KEY(user_id) REFERENCES trpg_players(user_id) ON DELETE CASCADE
                )
            """)

            # 7. TRPG 英雄榜歷史紀錄表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trpg_leaderboard (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    user_name TEXT,
                    job TEXT,
                    level INTEGER,
                    max_stage INTEGER,
                    total_turns INTEGER,
                    cause_of_death TEXT,
                    version TEXT,
                    record_date TEXT
                )
            """)

            # 8. TRPG 遺物
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trpg_legacy (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id TEXT,
                    hero_name TEXT,
                    death_stage INTEGER
                )
            """)

            # 9. TRPG logs
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trpg_logs (
                    run_id TEXT PRIMARY KEY,
                    user_id TEXT,
                    user_name TEXT,
                    final_stage INTEGER,
                    cause_of_death TEXT,
                    full_log TEXT -- 所有 logs
                )
            """)

            # 10. Valorant 帳號綁定表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS valorant_accounts (
                    user_id TEXT PRIMARY KEY,
                    riot_id TEXT,
                    tagline TEXT,
                    puuid TEXT,
                    region TEXT DEFAULT 'ap',
                    peak_rank TEXT,
                    cached_mmr TEXT,
                    cached_matches TEXT,
                    last_updated TEXT
                )
            """)

            conn.commit()
        print("💾 [WithYouTogether] SQLite 所有中央模組資料表初始化成功！")

    # ==========================================
    # ⚔️ TRPG 
    # ==========================================

    def load_player_db(cls, user_id):
            """讀取玩家資料庫，並自動組合還原成舊系統相容的 dict 格式"""
            user_id = str(user_id)
            with cls.get_connection() as conn:
                cursor = conn.cursor()
                
                # 讀取主表
                cursor.execute("SELECT * FROM trpg_players WHERE user_id = ?", (user_id,))
                p_row = cursor.fetchone()
                if not p_row:
                    return None
                
                # 讀取屬性副表
                cursor.execute("SELECT * FROM trpg_player_attributes WHERE user_id = ?", (user_id,))
                a_row = cursor.fetchone()
                
                # 讀取背包
                cursor.execute("SELECT item_id FROM trpg_inventory WHERE user_id = ? ORDER BY slot_position", (user_id,))
                inv_rows = cursor.fetchall()
                inventory = [row["item_id"] for row in inv_rows]

                # 💡 資料庫還原為相容 dict 結構
                player_data = {
                    "job": p_row["job"],
                    "health": p_row["health"],
                    "max_health": p_row["max_health"],
                    "level": p_row["level"],
                    "exp": p_row["exp"],
                    "skill_points": a_row["skill_points"] if a_row else 0,
                    "attributes": {
                        "STR": a_row["str_val"] if a_row else 10,
                        "DEX": a_row["dex_val"] if a_row else 10,
                        "INT": a_row["int_val"] if a_row else 10,
                        "PER": a_row["per_val"] if a_row else 10,
                        "LUK": a_row["luk_val"] if a_row else 10
                    },
                    "stage": p_row["stage"],
                    "turns": p_row["turns"],
                    "gold": p_row["gold"],
                    "stress": p_row["stress"],
                    "event_ready": bool(p_row["event_ready"]),
                    "run_id": p_row["run_id"],
                    "version": p_row["version"],
                    # 反序列化 JSON 字串
                    "equips": json.loads(p_row["equips"]) if p_row["equips"] else {"weapon": None, "armor": None, "helmet": None, "accessory": None},
                    "active_monster": json.loads(p_row["active_monster"]) if p_row["active_monster"] else None,
                    "pending_routes": json.loads(p_row["pending_routes"]) if p_row["pending_routes"] else None,
                    "inventory": inventory,
                    "logs": [] # 日誌改採文字檔流，這裡給空陣列
                }
                return player_data

    def save_player_db(cls, user_id, data):
        """將完整的玩家字典拆解，安全寫入 SQLite 各關聯表中"""
        user_id = str(user_id)
        with cls.get_connection() as conn:
            cursor = conn.cursor()
            
            # 1. 寫入或更新主表
            cursor.execute("""
                INSERT INTO trpg_players (
                    user_id, run_id, job, health, max_health, level, exp, gold, stress, stage, turns, event_ready, version, active_monster, pending_routes, equips
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    run_id=excluded.run_id, job=excluded.job, health=excluded.health, max_health=excluded.max_health,
                    level=excluded.level, exp=excluded.exp, gold=excluded.gold, stress=excluded.stress,
                    stage=excluded.stage, turns=excluded.turns, event_ready=excluded.event_ready, version=excluded.version,
                    active_monster=excluded.active_monster, pending_routes=excluded.pending_routes, equips=excluded.equips
            """, (
                user_id, data["run_id"], data["job"], data["health"], data["max_health"], data["level"], data["exp"], data["gold"], data["stress"], data["stage"], data["turns"],
                1 if data.get("event_ready") else 0, data["version"],
                json.dumps(data.get("active_monster"), ensure_ascii=False) if data.get("active_monster") else None,
                json.dumps(data.get("pending_routes"), ensure_ascii=False) if data.get("pending_routes") else None,
                json.dumps(data.get("equips"), ensure_ascii=False) if data.get("equips") else None
            ))

            # 2. 寫入或更新屬性副表
            attr = data["attributes"]
            cursor.execute("""
                INSERT INTO trpg_player_attributes (user_id, str_val, dex_val, int_val, per_val, luk_val, skill_points)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    str_val=excluded.str_val, dex_val=excluded.dex_val, int_val=excluded.int_val,
                    per_val=excluded.per_val, luk_val=excluded.luk_val, skill_points=excluded.skill_points
            """, (user_id, attr["STR"], attr["DEX"], attr["INT"], attr["PER"], attr["LUK"], data["skill_points"]))

            # 3. 更新背包表 (先刪除舊的再重新寫入，確保 slot 順序)
            cursor.execute("DELETE FROM trpg_inventory WHERE user_id = ?", (user_id,))
            for idx, item_id in enumerate(data.get("inventory", [])):
                cursor.execute("""
                    INSERT INTO trpg_inventory (user_id, item_id, slot_position)
                    VALUES (?, ?, ?)
                """, (user_id, item_id, idx + 1))
            
            conn.commit()

    def delete_player_db(cls, user_id):
        """玩家戰死或放棄時，觸發級聯刪除 (Cascade)"""
        user_id = str(user_id)
        with cls.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM trpg_players WHERE user_id = ?", (user_id,))
            conn.commit()

    def add_leaderboard_record(self, record):
            """新增一筆死亡/通關紀錄到英雄榜"""
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO trpg_leaderboard (
                        run_id, user_name, job, level, max_stage, total_turns, cause_of_death, version, record_date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    record["run_id"], record["user_name"], record["job"], record["level"],
                    record["max_stage"], record["total_turns"], record["cause_of_death"],
                    record["version"], record["date"]
                ))
                conn.commit()

    def get_top_records(self, limit=15):
        """精準撈出前 X 名的英雄 (關卡由大到小，回合由小到大)"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM trpg_leaderboard 
                ORDER BY max_stage DESC, total_turns ASC 
                LIMIT ?
            """, (limit,))
            return [dict(row) for row in cursor.fetchall()]
        
    def add_legacy(self, item_id, hero_name, stage):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # 限制遺產池最多 10 筆 (防膨脹)
            cursor.execute("SELECT COUNT(*) as count FROM trpg_legacy")
            if cursor.fetchone()["count"] >= 10:
                cursor.execute("DELETE FROM trpg_legacy WHERE id = (SELECT MIN(id) FROM trpg_legacy)")
            cursor.execute("INSERT INTO trpg_legacy (item_id, hero_name, death_stage) VALUES (?, ?, ?)", (item_id, hero_name, stage))
            conn.commit()

    def get_random_legacy(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM trpg_legacy ORDER BY RANDOM() LIMIT 1")
            row = cursor.fetchone()
            return dict(row) if row else None
        
    def save_run_log(self, run_id, user_id, user_name, stage, cause, full_log_text):
        """將詳細日誌文字直接寫入資料庫"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO trpg_logs (run_id, user_id, user_name, final_stage, cause_of_death, full_log)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (run_id, user_id, user_name, stage, cause, full_log_text))
            conn.commit()

    def get_run_log(self, run_id):
        """讀取特定 Run ID 的詳細日誌"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT full_log FROM trpg_logs WHERE run_id = ?", (run_id,))
            row = cursor.fetchone()
            return row["full_log"] if row else None
        
    # ==========================================
    # 🎯 Valorant 帳號綁定與快取管理操作
    # ==========================================

    def bind_v_account(self, user_id, riot_id, tagline, puuid, region='ap'):
        """綁定或更新 Discord 使用者的 Valorant 帳號"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO valorant_accounts (user_id, riot_id, tagline, puuid, region)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    riot_id=excluded.riot_id,
                    tagline=excluded.tagline,
                    puuid=excluded.puuid,
                    region=excluded.region
            """, (str(user_id), riot_id, tagline, puuid, region))
            conn.commit()

    def get_v_account(self, user_id):
        """取得使用者綁定的 Valorant 帳號資料 (包含快取)"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM valorant_accounts WHERE user_id = ?", (str(user_id),))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_v_cache(self, user_id, cached_mmr=None, cached_matches=None):
        """將高階戰績與生涯 JSON 序列化，寫入本地 SQLite，並更新同步時間戳"""
        now_str = datetime.now().isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if cached_mmr and cached_matches:
                cursor.execute("""
                    UPDATE valorant_accounts 
                    SET cached_mmr = ?, cached_matches = ?, last_updated = ?
                    WHERE user_id = ?
                """, (
                    json.dumps(cached_mmr, ensure_ascii=False), 
                    json.dumps(cached_matches, ensure_ascii=False), 
                    now_str, 
                    str(user_id)
                ))
            elif cached_mmr:
                cursor.execute("""
                    UPDATE valorant_accounts SET cached_mmr = ?, last_updated = ? WHERE user_id = ?
                """, (json.dumps(cached_mmr, ensure_ascii=False), now_str, str(user_id)))
            elif cached_matches:
                cursor.execute("""
                    UPDATE valorant_accounts SET cached_matches = ?, last_updated = ? WHERE user_id = ?
                """, (json.dumps(cached_matches, ensure_ascii=False), now_str, str(user_id)))
            conn.commit()