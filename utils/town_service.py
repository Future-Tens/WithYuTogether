"""小鎮遊戲規則：擲骰、作物狀態、計價。不碰 Discord、不碰 DB。"""
import json
import os
import random
from datetime import datetime, timedelta, timezone

# 台灣沒有日光節約時間，用固定 UTC+8 即可（不需要 tzdata）
TPE = timezone(timedelta(hours=8))
HOUR = 3600

# 作物狀態
GROWING, FRESH, OVERRIPE, WITHERED = "growing", "fresh", "overripe", "withered"


class TownError(Exception):
    """可以直接顯示給玩家看的錯誤訊息。"""


class Content:
    """data/town/*.json 的遊戲內容。"""

    def __init__(self, content_dir="data/town"):
        def load(name):
            with open(os.path.join(content_dir, name), "r", encoding="utf-8") as f:
                return json.load(f)

        self.crops = load("crops.json")
        self.mutations = load("mutations.json")
        self.ores = load("ores.json")
        self.mines = load("mines.json")
        self.settings = load("settings.json")


# ==========================================
# 🕒 時間
# ==========================================

def day_key(now):
    """台灣時間的日期字串，例如 2026-11-10。"""
    return datetime.fromtimestamp(now, TPE).date().isoformat()


def week_start(now):
    """本週一 00:00（台灣時間）的 Unix 秒數。"""
    d = datetime.fromtimestamp(now, TPE)
    monday = (d - timedelta(days=d.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(monday.timestamp())


# ==========================================
# 🌱 作物
# ==========================================

def _weighted(rng, pairs):
    """pairs: [(value, weight), ...]"""
    total = sum(w for _, w in pairs)
    r = rng.uniform(0, total)
    for value, w in pairs:
        r -= w
        if r <= 0:
            return value
    return pairs[-1][0]


def roll_mutation(content, rng=random):
    return _weighted(rng, [(k, m["weight"]) for k, m in content.mutations.items()])


def better_mutation(content, a, b):
    return a if content.mutations[a]["rank"] >= content.mutations[b]["rank"] else b


def roll_planting(content, crop_id, now, rng=random):
    """種下時一次決定所有隨機結果。"""
    crop = content.crops[crop_id]
    grow = int(crop["grow_hours"] * HOUR)
    pest_at = None
    if rng.random() < crop["pest_chance"]:
        pest_at = now + rng.randint(0, int(grow * crop["pest_window"]))
    return {
        "crop_id": crop_id,
        "planted_at": now,
        "ready_at": now + grow,
        "mutation": roll_mutation(content, rng),
        "mutation_alt": roll_mutation(content, rng),
        "pest_at": pest_at,
        "pest_cleared": 0,
        "watered_by": None,
    }


def grace_seconds(content, crop):
    return max(content.settings["freshness"]["min_grace_hours"], crop["grow_hours"]) * HOUR


def has_pest(plot, now):
    return plot["pest_at"] is not None and plot["pest_at"] <= now and not plot["pest_cleared"]


def effective_mutation(content, plot, now):
    """實際變異：澆過水取兩次較好的；有蟲則變異作廢。"""
    if has_pest(plot, now):
        return "normal"
    m = plot["mutation"]
    if plot["watered_by"]:
        m = better_mutation(content, m, plot["mutation_alt"])
    return m


def plot_state(content, plot, now):
    """回傳 (狀態, 下一個階段的時間點)。plot 必須有種東西。"""
    crop = content.crops[plot["crop_id"]]
    grace = grace_seconds(content, crop)
    ready = plot["ready_at"]
    if now < ready:
        return GROWING, ready
    if now < ready + grace:
        return FRESH, ready + grace
    if now < ready + 2 * grace:
        return OVERRIPE, ready + 2 * grace
    return WITHERED, None


def harvest_result(content, plot, now):
    """回傳 (item_id, 數量, 實際變異)。只能在新鮮或過熟時呼叫。"""
    state, _ = plot_state(content, plot, now)
    if state not in (FRESH, OVERRIPE):
        raise TownError("這格現在不能收成。")
    fresh = content.settings["freshness"]
    pct = 100
    if state == OVERRIPE:
        pct = pct * fresh["overripe_pct"] // 100
    if has_pest(plot, now):
        pct = pct * fresh["pest_pct"] // 100
    mut = effective_mutation(content, plot, now)
    crop = content.crops[plot["crop_id"]]
    qty = crop["yield"] * content.mutations[mut]["yield_mult"]
    return f"crop:{plot['crop_id']}:{mut}:{pct}", qty, mut


# ==========================================
# 🎒 物品
# ==========================================

def item_info(content, item_id):
    """回傳 {name, emoji, price}；price 是單價。"""
    parts = item_id.split(":")
    if parts[0] == "crop":
        _, crop_id, mut, pct = parts
        crop = content.crops[crop_id]
        m = content.mutations[mut]
        name = crop["name"]
        if m["label"]:
            name = f"{m['emoji']}{m['label']}{name}"
        if int(pct) < 100:
            name += f"（{pct}%）"
        price = crop["sell_price"] * m["price_mult"] * int(pct) // 100
        return {"name": name, "emoji": crop["emoji"], "price": price}
    if parts[0] == "ore":
        ore = content.ores[parts[1]]
        return {"name": ore["name"], "emoji": ore["emoji"], "price": ore["sell_price"]}
    raise KeyError(item_id)


# ==========================================
# ⛏️ 礦坑
# ==========================================

def pickaxe(content, level):
    return next(p for p in content.settings["pickaxe"] if p["level"] == level)


def next_pickaxe(content, level):
    return next((p for p in content.settings["pickaxe"] if p["level"] == level + 1), None)


def mine_duration(content, trip_id, pickaxe_lv):
    return int(content.mines[trip_id]["hours"] * HOUR * pickaxe(content, pickaxe_lv)["time_mult"])


def roll_mine_loot(content, trip_id, pickaxe_lv, rng=random):
    """回傳 ({item_id: 數量}, 寶箱風幣)。"""
    trip = content.mines[trip_id]
    rare_mult = pickaxe(content, pickaxe_lv)["rare_mult"]
    pairs = [(e["item"], e["weight"] * (rare_mult if e.get("rare") else 1)) for e in trip["loot"]]
    loot, chest_gold = {}, 0
    chest = content.settings["chest"]
    for _ in range(trip["rolls"]):
        if rng.random() < trip["chest_chance"]:
            chest_gold += rng.randint(chest["min"], chest["max"])
            continue
        item = f"ore:{_weighted(rng, pairs)}"
        loot[item] = loot.get(item, 0) + 1
    return loot, chest_gold


# ==========================================
# 📅 簽到與升級
# ==========================================

def checkin_reward(content, new_total):
    """回傳 (每日風幣, 里程碑風幣)。"""
    c = content.settings["checkin"]
    return c["daily"], c["milestones"].get(str(new_total), 0)


def next_plot_price(content, plot_count):
    """下一塊田的價格；已滿回傳 None。"""
    return next((p["price"] for p in content.settings["plots"] if p["level"] == plot_count + 1), None)
