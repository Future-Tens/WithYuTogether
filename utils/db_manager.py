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

            # 4. Valorant 帳號綁定表
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