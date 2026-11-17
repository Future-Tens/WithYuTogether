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
        self.quests = load("quests.json")
        self.achievements = load("achievements.json")


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


NOTIFY_MODES = ["off", "each", "all"]  # 通知按鈕依序切換
NOTIFY_READY, NOTIFY_WARN = 1, 2       # town_plots.notified 的位元


def notify_events(content, mode, plots, mining, now):
    """找出該私訊的事件。plots 是 {slot: plot}（只含有種東西的田），plot["notified"] 是已通知的位元。
    回傳 {"ready": [slot...], "all_ready": bool, "warn": [(slot, 過熟時間)...], "mine": bool, "flags": {slot: 新位元}}。
    mode="all" 時要所有種了作物（未枯萎）的田都成熟才通知成熟；快過熟與礦坑兩種模式都會通知。"""
    warn_secs = content.settings["notify"]["overripe_warn_minutes"] * 60
    ready, grown, warn, flags, all_grown, alive = [], [], [], {}, True, 0
    for s, p in sorted(plots.items()):
        bits = p.get("notified") or 0
        new = bits
        state, nxt = plot_state(content, p, now)
        if state == WITHERED:
            new = NOTIFY_READY | NOTIFY_WARN
        else:
            alive += 1
            if state == GROWING:
                all_grown = False
            else:
                grown.append(s)
                if not bits & NOTIFY_READY:
                    ready.append(s)
            if state == OVERRIPE:
                new |= NOTIFY_WARN  # 已經過熟就不必再提醒
            elif state == FRESH and now >= nxt - warn_secs and not bits & NOTIFY_WARN:
                warn.append((s, nxt))
                new |= NOTIFY_WARN
        flags[s] = new
    if mode == "all":
        # 全部模式：等所有田都成熟，有新成熟的才發，訊息列出全部成熟的田
        ready = grown if all_grown and ready else []
    for s in ready:
        flags[s] |= NOTIFY_READY
    mine = bool(mining and not mining.get("notified") and now >= mining["ready_at"])
    return {"ready": ready, "all_ready": mode == "all" and bool(ready), "warn": warn, "mine": mine,
            "flags": {s: f for s, f in flags.items() if f != (plots[s].get("notified") or 0)}}


def harvest_result(content, plot, now):
    """回傳 ([(item_id, 數量), ...], 實際變異)，第一項是主要收成。只能在新鮮或過熟時呼叫。
    升級過的田（中型 ×2、大型 ×3）多出來的份量一律是普通作物，稀有作物的數量跟一般田一樣。"""
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
    tier = plot_tier(content, plot.get("tier") or 1)  # 以收成當下的田地等級為準
    base = crop["yield"] * content.mutations[mut]["yield_mult"]   # 跟一般田一樣（巨大作物照樣 ×2）
    extra = crop["yield"] * (tier["yield_mult"] - 1)               # 升級田多出來的份量
    if mut == "normal":
        return [(f"crop:{plot['crop_id']}:normal:{pct}", base + extra)], mut
    items = [(f"crop:{plot['crop_id']}:{mut}:{pct}", base)]
    if extra:
        items.append((f"crop:{plot['crop_id']}:normal:{pct}", extra))
    return items, mut


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


def rare_ores(content):
    """在任何礦坑被標成 rare 的礦物（金礦、寶石、秘銀、星辰石）。"""
    return {e["item"] for trip in content.mines.values() for e in trip["loot"] if e.get("rare")}


def is_rare_item(content, item_id):
    """稀有物品：有變異的作物（金色、巨大、彩虹），或稀有礦物。一鍵賣出時會保留。"""
    parts = item_id.split(":")
    if parts[0] == "crop":
        return parts[2] != "normal"
    return parts[1] in rare_ores(content)


# ==========================================
# ⛏️ 礦坑
# ==========================================

def pickaxe(content, level):
    return next(p for p in content.settings["pickaxe"] if p["level"] == level)


def next_pickaxe(content, level):
    return next((p for p in content.settings["pickaxe"] if p["level"] == level + 1), None)


def mine_duration(content, trip_id, pickaxe_lv):
    return int(content.mines[trip_id]["hours"] * HOUR * pickaxe(content, pickaxe_lv)["time_mult"])


def trip_min_pickaxe(content, trip_id):
    """這條路線需要的十字鎬等級。"""
    return content.mines[trip_id].get("min_pickaxe", 1)


def ore_min_pickaxe(content):
    """有等級限制的礦物：{ore_id: 最低十字鎬等級}（取所有路線中最低的要求）。"""
    need = {}
    for trip in content.mines.values():
        for e in trip["loot"]:
            lv = max(e.get("min_pickaxe", 1), trip.get("min_pickaxe", 1))
            need[e["item"]] = min(need.get(e["item"], lv), lv)
    return {k: v for k, v in need.items() if v > 1}


def roll_mine_loot(content, trip_id, pickaxe_lv, rng=random):
    """回傳 ({item_id: 數量}, 寶箱風幣)。十字鎬等級不夠的礦物不會出現。"""
    trip = content.mines[trip_id]
    rare_mult = pickaxe(content, pickaxe_lv)["rare_mult"]
    pairs = [(e["item"], e["weight"] * (rare_mult if e.get("rare") else 1))
             for e in trip["loot"] if e.get("min_pickaxe", 1) <= pickaxe_lv]
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


BASE_TIER = {"tier": 1, "name": "", "emoji": "", "yield_mult": 1}


def plot_tier(content, tier):
    """田地等級資訊；1 是一般田。"""
    if tier <= 1:
        return BASE_TIER
    return next(t for t in content.settings["plot_tiers"]["tiers"] if t["tier"] == tier)


def next_plot_tier(content, tier):
    """下一級田地；已是最高級回傳 None。"""
    return next((t for t in content.settings["plot_tiers"]["tiers"] if t["tier"] == tier + 1), None)


def next_plot_price(content, plot_count):
    """下一塊田的價格；已滿回傳 None。"""
    return next((p["price"] for p in content.settings["plots"] if p["level"] == plot_count + 1), None)


# ==========================================
# 📜 任務牆
# ==========================================
# 需求的 match 格式：ore:<id>、crop:<crop>:<mutation>、crop:*:<mutation>（任意作物）。
# 作物只收 100% 新鮮的（過熟、有蟲的不算）。

def requirement_info(content, match):
    """回傳 {name, emoji, price}；萬用作物的 price 取最便宜的那種。"""
    parts = match.split(":")
    if parts[0] == "ore":
        return item_info(content, match)
    _, crop_id, mut = parts
    if crop_id != "*":
        return item_info(content, f"crop:{crop_id}:{mut}:100")
    m = content.mutations[mut]
    price = min(item_info(content, f"crop:{c}:{mut}:100")["price"] for c in content.crops)
    return {"name": f"任意{m['emoji']}{m['label']}作物", "emoji": "🌾", "price": price}


def requirement_matches(match, item_id):
    if match.startswith("ore:"):
        return item_id == match
    m, i = match.split(":"), item_id.split(":")
    return (i[0] == "crop" and len(i) == 4 and m[1] in ("*", i[1])
            and m[2] == i[2] and i[3] == "100")


def allocate(content, requirements, inventory):
    """inventory: {item_id: qty}。回傳 (是否足夠, 每項需求擁有的數量, 要扣的 {item_id: qty})。
    指定作物先扣，萬用需求最後扣，且從最便宜的開始。"""
    left = dict(inventory)
    have, take = [0] * len(requirements), {}
    order = sorted(range(len(requirements)), key=lambda k: "*" in requirements[k]["match"])
    for k in order:
        req = requirements[k]
        items = sorted((i for i in left if left[i] > 0 and requirement_matches(req["match"], i)),
                       key=lambda i: item_info(content, i)["price"])
        need = req["qty"]
        for i in items:
            n = min(need, left[i])
            left[i] -= n
            take[i] = take.get(i, 0) + n
            have[k] += n
            need -= n
            if need == 0:
                break
    ok = all(h >= r["qty"] for h, r in zip(have, requirements))
    return ok, have, take


def roll_quest(content, now, rng=random):
    """抽稀有度、需求與獎勵。回傳 {rarity, requirements, reward, created_at, expires_at}。"""
    q = content.quests
    rarity = _weighted(rng, [(k, r["weight"]) for k, r in q["rarities"].items()])
    template = rng.choice(q["pools"][rarity])
    merged = {}
    for spec in template:
        match = spec["match"] if isinstance(spec["match"], str) else rng.choice(spec["match"])
        merged[match] = merged.get(match, 0) + rng.randint(*spec["qty"])
    requirements = [{"match": m, "qty": n} for m, n in merged.items()]
    value = sum(requirement_info(content, r["match"])["price"] * r["qty"] for r in requirements)
    reward = _round_reward(value, q["rarities"][rarity]["reward_mult"])
    return {"rarity": rarity, "requirements": requirements, "reward": reward, "created_at": now,
            "expires_at": now + int(q["rarities"][rarity]["expire_hours"] * HOUR)}


def _round_reward(value, mult):
    return -(-int(value * mult) // 10) * 10  # 無條件進位到 10


def has_wildcard(requirements):
    return any("*" in r["match"] for r in requirements)


def quest_reward(content, quest, take):
    """實際獎勵：依交出物品的市價計算。任務上標示的 reward 是用最便宜作物算的最低值。"""
    value = sum(item_info(content, i)["price"] * n for i, n in take.items())
    mult = content.quests["rarities"][quest["rarity"]]["reward_mult"]
    return max(quest["reward"], _round_reward(value, mult))


def quest_cooldown(content):
    return int(content.quests["cooldown_minutes"] * 60)


def earned_achievements(content, stats):
    """stats: {統計名稱: 數值}。回傳已達成的成就 id。"""
    return [aid for aid, a in content.achievements.items() if stats.get(a["stat"], 0) >= a["count"]]
