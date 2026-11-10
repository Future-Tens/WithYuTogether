"""小鎮的資料表與所有 SQL。每個動作都在一個 BEGIN IMMEDIATE 交易裡完成，避免連點重複領取。"""
import random
import sqlite3
from contextlib import contextmanager

from utils import town_service as svc
from utils.town_service import TownError

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS town_players (
        guild_id TEXT, user_id TEXT, name TEXT,
        gold INTEGER NOT NULL, plot_count INTEGER NOT NULL, pickaxe_lv INTEGER NOT NULL DEFAULT 1,
        last_checkin TEXT, checkin_total INTEGER NOT NULL DEFAULT 0,
        likes INTEGER NOT NULL DEFAULT 0, total_harvest INTEGER NOT NULL DEFAULT 0,
        rare_found INTEGER NOT NULL DEFAULT 0, created_at INTEGER,
        PRIMARY KEY (guild_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS town_plots (
        guild_id TEXT, user_id TEXT, slot INTEGER,
        crop_id TEXT, planted_at INTEGER, ready_at INTEGER,
        mutation TEXT, mutation_alt TEXT, pest_at INTEGER,
        pest_cleared INTEGER NOT NULL DEFAULT 0, watered_by TEXT,
        PRIMARY KEY (guild_id, user_id, slot))""",
    """CREATE TABLE IF NOT EXISTS town_mining (
        guild_id TEXT, user_id TEXT, trip_id TEXT, started_at INTEGER, ready_at INTEGER,
        PRIMARY KEY (guild_id, user_id))""",
    """CREATE TABLE IF NOT EXISTS town_inventory (
        guild_id TEXT, user_id TEXT, item_id TEXT, qty INTEGER NOT NULL,
        PRIMARY KEY (guild_id, user_id, item_id))""",
    """CREATE TABLE IF NOT EXISTS town_social (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT, actor_id TEXT, target_id TEXT, action TEXT, day TEXT, created_at INTEGER,
        UNIQUE (guild_id, actor_id, target_id, action, day))""",
    """CREATE TABLE IF NOT EXISTS town_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id TEXT, user_id TEXT, delta INTEGER, reason TEXT, created_at INTEGER)""",
    "CREATE INDEX IF NOT EXISTS idx_town_ledger_week ON town_ledger (guild_id, created_at)",
]

EMPTY_PLOT = dict(crop_id=None, planted_at=None, ready_at=None, mutation=None, mutation_alt=None,
                  pest_at=None, pest_cleared=0, watered_by=None)


class TownDB:
    def __init__(self, content, db_path=None):
        self.content = content
        if db_path is None:
            from utils.db_manager import DatabaseManager
            db_path = DatabaseManager().db_path
        self.db_path = db_path
        with self.tx() as conn:
            for stmt in SCHEMA:
                conn.execute(stmt)
            # 起始田數調高後，把舊存檔補到新的起始值（解鎖價格表是從起始田數 +1 開始）
            conn.execute("UPDATE town_players SET plot_count = ? WHERE plot_count < ?",
                         (content.settings["start"]["plots"], content.settings["start"]["plots"]))

    # ---------- 連線 ----------

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def tx(self):
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    @contextmanager
    def read(self):
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    # ---------- 內部工具 ----------

    @staticmethod
    def _ids(guild_id, user_id):
        return str(guild_id), str(user_id)

    def _player(self, conn, g, u):
        row = conn.execute("SELECT * FROM town_players WHERE guild_id=? AND user_id=?", (g, u)).fetchone()
        if not row:
            raise TownError("這位玩家還沒有開始經營小鎮。輸入 `/小鎮` 開始吧！")
        return dict(row)

    def _add_gold(self, conn, g, u, delta, reason, now):
        if delta == 0:
            return
        conn.execute("UPDATE town_players SET gold = gold + ? WHERE guild_id=? AND user_id=?", (delta, g, u))
        conn.execute("INSERT INTO town_ledger (guild_id, user_id, delta, reason, created_at) VALUES (?,?,?,?,?)",
                     (g, u, delta, reason, now))

    def _spend(self, conn, g, u, cost, reason, now):
        cur = conn.execute("UPDATE town_players SET gold = gold - ? WHERE guild_id=? AND user_id=? AND gold >= ?",
                           (cost, g, u, cost))
        if cur.rowcount != 1:
            gold = self._player(conn, g, u)["gold"]
            raise TownError(f"風幣不夠：需要 {cost} 風幣，你只有 {gold} 風幣。")
        conn.execute("INSERT INTO town_ledger (guild_id, user_id, delta, reason, created_at) VALUES (?,?,?,?,?)",
                     (g, u, -cost, reason, now))

    @staticmethod
    def _add_item(conn, g, u, item_id, qty):
        conn.execute("""INSERT INTO town_inventory (guild_id, user_id, item_id, qty) VALUES (?,?,?,?)
                        ON CONFLICT (guild_id, user_id, item_id) DO UPDATE SET qty = qty + excluded.qty""",
                     (g, u, item_id, qty))

    @staticmethod
    def _plots(conn, g, u):
        rows = conn.execute("SELECT * FROM town_plots WHERE guild_id=? AND user_id=? AND crop_id IS NOT NULL",
                            (g, u)).fetchall()
        return {r["slot"]: dict(r) for r in rows}

    @staticmethod
    def _set_plot(conn, g, u, slot, fields):
        cols = list(fields)
        conn.execute(
            f"""INSERT INTO town_plots (guild_id, user_id, slot, {", ".join(cols)})
                VALUES (?,?,?,{", ".join("?" * len(cols))})
                ON CONFLICT (guild_id, user_id, slot) DO UPDATE SET {", ".join(f"{c}=excluded.{c}" for c in cols)}""",
            (g, u, slot, *fields.values()))

    # ---------- 讀取 ----------

    def player_exists(self, guild_id, user_id):
        g, u = self._ids(guild_id, user_id)
        with self.read() as conn:
            return conn.execute("SELECT 1 FROM town_players WHERE guild_id=? AND user_id=?", (g, u)).fetchone() is not None

    def ensure_player(self, guild_id, user_id, name, now):
        g, u = self._ids(guild_id, user_id)
        start = self.content.settings["start"]
        with self.tx() as conn:
            created = conn.execute(
                """INSERT OR IGNORE INTO town_players (guild_id, user_id, name, gold, plot_count, created_at)
                   VALUES (?,?,?,?,?,?)""", (g, u, name, start["gold"], start["plots"], now)).rowcount == 1
            conn.execute("UPDATE town_players SET name=? WHERE guild_id=? AND user_id=?", (name, g, u))
        return created

    def snapshot(self, guild_id, user_id):
        """主畫面需要的所有資料。"""
        g, u = self._ids(guild_id, user_id)
        with self.read() as conn:
            player = self._player(conn, g, u)
            mining = conn.execute("SELECT * FROM town_mining WHERE guild_id=? AND user_id=?", (g, u)).fetchone()
            return {"player": player, "plots": self._plots(conn, g, u), "mining": dict(mining) if mining else None}

    def inventory(self, guild_id, user_id):
        g, u = self._ids(guild_id, user_id)
        with self.read() as conn:
            rows = conn.execute("SELECT item_id, qty FROM town_inventory WHERE guild_id=? AND user_id=? AND qty > 0",
                                (g, u)).fetchall()
        items = []
        for r in rows:
            info = svc.item_info(self.content, r["item_id"])
            items.append({"item_id": r["item_id"], "qty": r["qty"], **info, "total": info["price"] * r["qty"]})
        items.sort(key=lambda i: -i["total"])
        return items

    def leaderboard(self, guild_id, kind, now, limit=10):
        g = str(guild_id)
        queries = {
            "assets": ("SELECT user_id, name, gold AS v FROM town_players WHERE guild_id=? ORDER BY v DESC LIMIT ?", (g, limit)),
            "likes": ("SELECT user_id, name, likes AS v FROM town_players WHERE guild_id=? AND likes > 0 ORDER BY v DESC LIMIT ?", (g, limit)),
            "rare": ("SELECT user_id, name, rare_found AS v FROM town_players WHERE guild_id=? AND rare_found > 0 ORDER BY v DESC LIMIT ?", (g, limit)),
            "weekly": ("""SELECT l.user_id, p.name, SUM(l.delta) AS v FROM town_ledger l
                          JOIN town_players p ON p.guild_id = l.guild_id AND p.user_id = l.user_id
                          WHERE l.guild_id=? AND l.delta > 0 AND l.created_at >= ?
                          GROUP BY l.user_id ORDER BY v DESC LIMIT ?""", (g, svc.week_start(now), limit)),
        }
        sql, args = queries[kind]
        with self.read() as conn:
            return [dict(r) for r in conn.execute(sql, args).fetchall()]

    # ---------- 簽到 ----------

    def checkin(self, guild_id, user_id, now):
        g, u = self._ids(guild_id, user_id)
        today = svc.day_key(now)
        with self.tx() as conn:
            p = self._player(conn, g, u)
            if p["last_checkin"] == today:
                raise TownError("今天已經簽到過了，明天（台灣時間 00:00 後）再來吧！")
            total = p["checkin_total"] + 1
            daily, milestone = svc.checkin_reward(self.content, total)
            conn.execute("UPDATE town_players SET last_checkin=?, checkin_total=? WHERE guild_id=? AND user_id=?",
                         (today, total, g, u))
            self._add_gold(conn, g, u, daily, "checkin", now)
            self._add_gold(conn, g, u, milestone, "milestone", now)
        return {"total": total, "daily": daily, "milestone": milestone}

    # ---------- 農田 ----------

    def plant(self, guild_id, user_id, slot, crop_id, now, rng=random):
        g, u = self._ids(guild_id, user_id)
        crop = self.content.crops.get(crop_id)
        if not crop:
            raise TownError("沒有這種作物。")
        with self.tx() as conn:
            p = self._player(conn, g, u)
            if not 1 <= slot <= p["plot_count"]:
                raise TownError("這塊田還沒解鎖。")
            if slot in self._plots(conn, g, u):
                raise TownError("這塊田已經種了東西。")
            self._spend(conn, g, u, crop["seed_price"], "seed", now)
            self._set_plot(conn, g, u, slot, svc.roll_planting(self.content, crop_id, now, rng))
        return crop

    def harvest(self, guild_id, user_id, now, slot=None):
        """收成指定格子；slot=None 時收成所有可收的格子。回傳收成清單。"""
        g, u = self._ids(guild_id, user_id)
        results = []
        with self.tx() as conn:
            plots = self._plots(conn, g, u)
            targets = [slot] if slot is not None else sorted(plots)
            for s in targets:
                plot = plots.get(s)
                if not plot:
                    continue
                state, _ = svc.plot_state(self.content, plot, now)
                if state not in (svc.FRESH, svc.OVERRIPE):
                    continue
                item_id, qty, mut = svc.harvest_result(self.content, plot, now)
                self._set_plot(conn, g, u, s, EMPTY_PLOT)
                self._add_item(conn, g, u, item_id, qty)
                conn.execute("""UPDATE town_players SET total_harvest = total_harvest + ?,
                                rare_found = rare_found + ? WHERE guild_id=? AND user_id=?""",
                             (qty, 1 if mut != "normal" else 0, g, u))
                results.append({"slot": s, "item_id": item_id, "qty": qty, "mutation": mut, "crop_id": plot["crop_id"]})
        if not results:
            raise TownError("目前沒有可以收成的作物。")
        return results

    def clear_withered(self, guild_id, user_id, now):
        g, u = self._ids(guild_id, user_id)
        with self.tx() as conn:
            cleared = [s for s, p in self._plots(conn, g, u).items()
                       if svc.plot_state(self.content, p, now)[0] == svc.WITHERED]
            for s in cleared:
                self._set_plot(conn, g, u, s, EMPTY_PLOT)
        if not cleared:
            raise TownError("沒有枯萎的作物需要清除。")
        return cleared

    def clear_pests(self, guild_id, owner_id, actor_id, now):
        """除掉農場上所有的蟲。訪客幫忙時每隻蟲給獎勵。"""
        g, owner = self._ids(guild_id, owner_id)
        actor = str(actor_id)
        reward = 0
        with self.tx() as conn:
            self._player(conn, g, owner)
            slots = [s for s, p in self._plots(conn, g, owner).items() if svc.has_pest(p, now)]
            if not slots:
                raise TownError("這座農場現在沒有蟲。")
            for s in slots:
                conn.execute("UPDATE town_plots SET pest_cleared=1 WHERE guild_id=? AND user_id=? AND slot=?", (g, owner, s))
            if actor != owner:
                self._player(conn, g, actor)
                reward = self.content.settings["social"]["pest_reward"] * len(slots)
                self._add_gold(conn, g, actor, reward, "pest_reward", now)
        return {"slots": slots, "reward": reward}

    # ---------- 社交 ----------

    def water(self, guild_id, owner_id, actor_id, now):
        """幫別人澆水：自動選剩餘時間最長、還沒澆過的作物。"""
        g, owner = self._ids(guild_id, owner_id)
        actor = str(actor_id)
        social = self.content.settings["social"]
        today = svc.day_key(now)
        with self.tx() as conn:
            self._player(conn, g, owner)
            self._player(conn, g, actor)
            candidates = [p for p in self._plots(conn, g, owner).values()
                          if p["watered_by"] is None and now < p["ready_at"]]
            if not candidates:
                raise TownError("這座農場目前沒有需要澆水的作物。")
            used = conn.execute("SELECT COUNT(*) FROM town_social WHERE guild_id=? AND actor_id=? AND action='water' AND day=?",
                                (g, actor, today)).fetchone()[0]
            if used >= social["water_daily_cap"]:
                raise TownError(f"你今天已經幫忙澆水 {used} 次了，明天再來吧！")
            try:
                conn.execute("INSERT INTO town_social (guild_id, actor_id, target_id, action, day, created_at) VALUES (?,?,?,?,?,?)",
                             (g, actor, owner, "water", today, now))
            except sqlite3.IntegrityError:
                raise TownError("你今天已經幫這位朋友澆過水了。") from None
            plot = max(candidates, key=lambda p: p["ready_at"])
            saved = int((plot["ready_at"] - now) * social["water_speedup"])
            conn.execute("UPDATE town_plots SET watered_by=?, ready_at = ready_at - ? WHERE guild_id=? AND user_id=? AND slot=?",
                         (actor, saved, g, owner, plot["slot"]))
            reward = social["water_reward"]
            self._add_gold(conn, g, actor, reward, "water_reward", now)
            self._add_gold(conn, g, owner, reward, "water_reward", now)
        return {"slot": plot["slot"], "crop_id": plot["crop_id"], "saved": saved, "reward": reward,
                "remaining": social["water_daily_cap"] - used - 1}

    def like(self, guild_id, owner_id, actor_id, now):
        g, owner = self._ids(guild_id, owner_id)
        actor = str(actor_id)
        with self.tx() as conn:
            self._player(conn, g, owner)
            try:
                conn.execute("INSERT INTO town_social (guild_id, actor_id, target_id, action, day, created_at) VALUES (?,?,?,?,?,?)",
                             (g, actor, owner, "like", svc.day_key(now), now))
            except sqlite3.IntegrityError:
                raise TownError("你今天已經幫這座農場按過讚了。") from None
            conn.execute("UPDATE town_players SET likes = likes + 1 WHERE guild_id=? AND user_id=?", (g, owner))
            return self._player(conn, g, owner)["likes"]

    # ---------- 礦坑 ----------

    def start_mine(self, guild_id, user_id, trip_id, now):
        g, u = self._ids(guild_id, user_id)
        if trip_id not in self.content.mines:
            raise TownError("沒有這條礦坑路線。")
        with self.tx() as conn:
            p = self._player(conn, g, u)
            if conn.execute("SELECT 1 FROM town_mining WHERE guild_id=? AND user_id=?", (g, u)).fetchone():
                raise TownError("你已經有一趟挖礦行程了，先把它收取吧。")
            ready = now + svc.mine_duration(self.content, trip_id, p["pickaxe_lv"])
            conn.execute("INSERT INTO town_mining (guild_id, user_id, trip_id, started_at, ready_at) VALUES (?,?,?,?,?)",
                         (g, u, trip_id, now, ready))
        return ready

    def collect_mine(self, guild_id, user_id, now, rng=random):
        g, u = self._ids(guild_id, user_id)
        with self.tx() as conn:
            p = self._player(conn, g, u)
            row = conn.execute("SELECT * FROM town_mining WHERE guild_id=? AND user_id=?", (g, u)).fetchone()
            if not row:
                raise TownError("你現在沒有挖礦行程。")
            if now < row["ready_at"]:
                raise TownError(f"還在挖！<t:{row['ready_at']}:R> 完成。")
            conn.execute("DELETE FROM town_mining WHERE guild_id=? AND user_id=?", (g, u))
            loot, chest_gold = svc.roll_mine_loot(self.content, row["trip_id"], p["pickaxe_lv"], rng)
            for item_id, qty in loot.items():
                self._add_item(conn, g, u, item_id, qty)
            self._add_gold(conn, g, u, chest_gold, "chest", now)
        return {"trip_id": row["trip_id"], "loot": loot, "chest": chest_gold}

    # ---------- 商店 ----------

    def sell(self, guild_id, user_id, now, item_id=None):
        """賣出指定物品的全部數量；item_id=None 時全部賣出。"""
        g, u = self._ids(guild_id, user_id)
        with self.tx() as conn:
            self._player(conn, g, u)
            sql = "SELECT item_id, qty FROM town_inventory WHERE guild_id=? AND user_id=? AND qty > 0"
            args = (g, u)
            if item_id:
                sql += " AND item_id=?"
                args += (item_id,)
            rows = conn.execute(sql, args).fetchall()
            if not rows:
                raise TownError("背包裡沒有可以賣的東西。")
            total = count = 0
            for r in rows:
                total += svc.item_info(self.content, r["item_id"])["price"] * r["qty"]
                count += r["qty"]
                conn.execute("DELETE FROM town_inventory WHERE guild_id=? AND user_id=? AND item_id=?", (g, u, r["item_id"]))
            self._add_gold(conn, g, u, total, "sell", now)
        return {"gold": total, "count": count}

    def unlock_plot(self, guild_id, user_id, now):
        g, u = self._ids(guild_id, user_id)
        with self.tx() as conn:
            p = self._player(conn, g, u)
            price = svc.next_plot_price(self.content, p["plot_count"])
            if price is None:
                raise TownError("田地已經全部解鎖了。")
            self._spend(conn, g, u, price, "unlock_plot", now)
            conn.execute("UPDATE town_players SET plot_count = plot_count + 1 WHERE guild_id=? AND user_id=?", (g, u))
        return {"plot_count": p["plot_count"] + 1, "price": price}

    def upgrade_pickaxe(self, guild_id, user_id, now):
        g, u = self._ids(guild_id, user_id)
        with self.tx() as conn:
            p = self._player(conn, g, u)
            nxt = svc.next_pickaxe(self.content, p["pickaxe_lv"])
            if nxt is None:
                raise TownError("十字鎬已經是最高等級了。")
            self._spend(conn, g, u, nxt["price"], "pickaxe", now)
            conn.execute("UPDATE town_players SET pickaxe_lv=? WHERE guild_id=? AND user_id=?", (nxt["level"], g, u))
        return nxt
