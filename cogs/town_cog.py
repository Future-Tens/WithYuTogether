import time
import traceback

import discord
from discord import app_commands
from discord.ext import commands

from utils import town_service as svc
from utils.town_db import TownDB
from utils.town_service import Content, TownError

# 訪客專用（擁有者不能對自己用）、雙方都能用、其餘皆為擁有者專用
VISITOR_ACTIONS = {"water", "like"}
SHARED_ACTIONS = {"pest"}

RANK_KINDS = {
    "assets": ("💰", "總資產", "風幣"),
    "weekly": ("📈", "本週收入", "風幣"),
    "likes": ("❤️", "人氣", "讚"),
    "rare": ("🌈", "稀有收藏", "株"),
}
MEDALS = ["🥇", "🥈", "🥉"]
GREEN = discord.Color.from_rgb(87, 168, 75)
NO_PINGS = discord.AllowedMentions.none()
GUIDE_URL = "https://future-tens.github.io/WithYuTogether/"


def fmt_duration(seconds):
    seconds = int(seconds)
    h, m = divmod(seconds // 60, 60)
    if h and m:
        return f"{h} 小時 {m} 分"
    if h:
        return f"{h} 小時"
    return f"{max(m, 1)} 分鐘"


# ==========================================
# 🔘 持久化元件：custom_id 帶著動作與農場主人，機器人重啟後仍可使用
# ==========================================

class TownButton(discord.ui.DynamicItem[discord.ui.Button],
                 template=r"town:(?P<action>[a-z_]+):(?P<owner>[0-9]+)(?::(?P<arg>[a-z0-9_]+))?"):
    def __init__(self, action, owner, arg=None, *, label=None, emoji=None,
                 style=discord.ButtonStyle.secondary, disabled=False, row=None):
        custom_id = f"town:{action}:{owner}" + (f":{arg}" if arg else "")
        super().__init__(discord.ui.Button(label=label, emoji=emoji, style=style, disabled=disabled,
                                           row=row, custom_id=custom_id))
        self.action, self.owner, self.arg = action, int(owner), arg

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["action"], match["owner"], match["arg"])

    async def callback(self, interaction):
        await interaction.client.get_cog("TownCog").on_action(interaction, self.action, self.owner, self.arg)


class TownSelect(discord.ui.DynamicItem[discord.ui.Select],
                 template=r"townsel:(?P<action>[a-z_]+):(?P<owner>[0-9]+)(?::(?P<arg>[a-z0-9_]+))?"):
    def __init__(self, action, owner, arg=None, *, options=None, placeholder=None, row=None):
        custom_id = f"townsel:{action}:{owner}" + (f":{arg}" if arg else "")
        super().__init__(discord.ui.Select(custom_id=custom_id, options=options or [],
                                           placeholder=placeholder, row=row))
        self.action, self.owner, self.arg = action, int(owner), arg

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["action"], match["owner"], match["arg"])

    async def callback(self, interaction):
        values = interaction.data.get("values", [])
        await interaction.client.get_cog("TownCog").on_action(
            interaction, self.action, self.owner, self.arg, values[0] if values else None)


# ==========================================
# 🏡 小鎮
# ==========================================

class TownCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.content = Content()
        self.db = TownDB(self.content)
        s = self.content.settings
        self.max_plots = s["start"]["plots"] + len(s["plots"])

    async def cog_load(self):
        self.bot.add_dynamic_items(TownButton, TownSelect)

    async def cog_unload(self):
        self.bot.remove_dynamic_items(TownButton, TownSelect)

    # ---------- 指令 ----------

    @app_commands.command(name="小鎮", description="打開你的小鎮農場，或拜訪別人的農場")
    @app_commands.guild_only()
    @app_commands.rename(member="成員")
    @app_commands.describe(member="要拜訪的成員（不填就是打開自己的農場）")
    async def town(self, interaction: discord.Interaction, member: discord.Member | None = None):
        now = int(time.time())
        g = interaction.guild_id
        notice = None
        if member and member.id != interaction.user.id:
            if member.bot or not self.db.player_exists(g, member.id):
                await interaction.response.send_message(
                    f"{member.display_name} 還沒有開始經營小鎮。", ephemeral=True)
                return
            owner = member.id
        else:
            owner = interaction.user.id
            if self.db.ensure_player(g, owner, interaction.user.display_name, now):
                start = self.content.settings["start"]
                notice = (f"🎉 歡迎來到小鎮！你拿到了 {start['plots']} 塊田和 {start['gold']} 風幣，"
                          "先按「📅 簽到」，再去「🌱 農田」種下第一株作物吧。")
        embed, view = self.render("home", g, owner, now, notice=notice)
        await interaction.response.send_message(embed=embed, view=view, allowed_mentions=NO_PINGS)

    @app_commands.command(name="小鎮說明", description="打開小鎮的玩法指南網頁")
    async def town_guide(self, interaction: discord.Interaction):
        embed = discord.Embed(title="📖 小鎮玩法指南", url=GUIDE_URL, color=GREEN,
                              description="種田、挖礦、稀有作物、拜訪朋友的完整說明都在這裡。")
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="打開玩法指南", emoji="📖", url=GUIDE_URL))
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    # ---------- 按鈕與選單 ----------

    async def on_action(self, interaction: discord.Interaction, action, owner, arg, value=None):
        if interaction.guild_id is None:
            return
        g, user, now = interaction.guild_id, interaction.user.id, int(time.time())
        try:
            if action in VISITOR_ACTIONS or action in SHARED_ACTIONS:
                await self._helper_action(interaction, action, g, owner, user, arg, now)
                return
            if user != owner:
                raise TownError(f"這是 <@{owner}> 的農場喔！輸入 `/小鎮` 打開你自己的。")
            if action == "bag":
                await interaction.response.send_message(embed=self.render_bag(g, owner), ephemeral=True)
                return
            page, page_arg, notice, announces = self._owner_action(action, g, owner, arg, value, now)
            embed, view = self.render(page, g, owner, now, arg=page_arg, notice=notice)
            await interaction.response.edit_message(embed=embed, view=view, allowed_mentions=NO_PINGS)
            for text in announces:
                await interaction.channel.send(text, allowed_mentions=NO_PINGS)
        except TownError as e:
            await self._reply_error(interaction, str(e))
        except Exception:
            traceback.print_exc()
            await self._reply_error(interaction, "發生了一點問題，請稍後再試，或重新輸入 `/小鎮`。")

    async def _reply_error(self, interaction, text):
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.send_message(text, ephemeral=True)

    async def _helper_action(self, interaction, action, g, owner, user, arg, now):
        """澆水、按讚（訪客）與除蟲（雙方）。"""
        is_owner = user == owner
        if action in VISITOR_ACTIONS and is_owner:
            raise TownError("這是幫朋友用的按鈕喔！去別人的農場按吧。")
        if not is_owner:
            self.db.ensure_player(g, user, interaction.user.display_name, now)

        if action == "water":
            r = self.db.water(g, owner, user, now)
            crop = self.content.crops[r["crop_id"]]
            text = (f"💧 你幫 <@{owner}> 第 {r['slot']} 格的{crop['emoji']}{crop['name']}澆了水，"
                    f"提早了 {fmt_duration(r['saved'])}！你們各得 {r['reward']} 風幣。今天還能幫忙 {r['remaining']} 次。")
        elif action == "like":
            likes = self.db.like(g, owner, user, now)
            text = f"❤️ 你幫 <@{owner}> 的農場按了讚！目前人氣 {likes}。"
        else:  # pest
            r = self.db.clear_pests(g, owner, user, now)
            n = len(r["slots"])
            text = (f"🐛 除掉了 {n} 隻蟲！" if is_owner
                    else f"🐛 你幫 <@{owner}> 除掉了 {n} 隻蟲，得到 {r['reward']} 風幣！")

        page = arg if arg in ("home", "farm") else "home"
        embed, view = self.render(page, g, owner, now, notice=text if is_owner else None)
        if is_owner:
            await interaction.response.edit_message(embed=embed, view=view, allowed_mentions=NO_PINGS)
        else:
            await interaction.response.send_message(text, ephemeral=True)
            await interaction.message.edit(embed=embed, view=view, allowed_mentions=NO_PINGS)

    def _owner_action(self, action, g, owner, arg, value, now):
        """回傳 (頁面, 頁面參數, 通知文字, 要公告的訊息)。"""
        db, content = self.db, self.content
        if action in ("home", "refresh"):
            return "home", None, None, []
        if action in ("farm", "mine", "shop"):
            return action, None, None, []
        if action == "rank":
            return "rank", arg or "assets", None, []

        if action == "checkin":
            r = db.checkin(g, owner, now)
            text = f"📅 簽到成功！+{r['daily']} 風幣（累計 {r['total']} 天）"
            if r["milestone"]:
                text += f"\n🎁 累計 {r['total']} 天里程碑：額外 +{r['milestone']} 風幣！"
            return "home", None, text, []

        if action == "plantpick":
            return "plant", arg, None, []
        if action == "plant":
            slot = int(arg)
            crop = db.plant(g, owner, slot, value, now)
            ready = now + int(crop["grow_hours"] * svc.HOUR)
            return "farm", None, f"🌱 在第 {slot} 格種下了{crop['emoji']}{crop['name']}，<t:{ready}:R> 成熟。", []
        if action in ("harvest", "harvestall"):
            results = db.harvest(g, owner, now, int(arg) if action == "harvest" else None)
            lines, announces = [], []
            name = db.snapshot(g, owner)["player"]["name"]
            for r in results:
                info = svc.item_info(content, r["item_id"])
                lines.append(f"🧺 第 {r['slot']} 格：{info['emoji']}{info['name']} ×{r['qty']}（價值 {info['price'] * r['qty']} 風幣）")
                if content.mutations[r["mutation"]].get("announce"):
                    announces.append(f"🌈 **{name}** 收成了一株{info['emoji']}{info['name']}！快去 `/小鎮` 看看吧！")
            return "farm", None, "\n".join(lines), announces
        if action == "clearwither":
            slots = db.clear_withered(g, owner, now)
            return "farm", None, f"🥀 清除了第 {'、'.join(map(str, slots))} 格的枯萎作物。", []

        if action == "minego":
            trip = content.mines[arg]
            ready = db.start_mine(g, owner, arg, now)
            return "mine", None, f"⛏️ 出發前往{trip['name']}礦坑！<t:{ready}:R> 挖完。", []
        if action == "minecollect":
            r = db.collect_mine(g, owner, now)
            parts = [f"{svc.item_info(content, i)['emoji']}{svc.item_info(content, i)['name']} ×{q}"
                     for i, q in sorted(r["loot"].items(), key=lambda kv: -svc.item_info(content, kv[0])["price"])]
            text = "📦 收取礦石：" + ("、".join(parts) if parts else "什麼都沒挖到")
            if r["chest"]:
                text += f"\n🎁 挖到寶箱！+{r['chest']} 風幣"
            return "mine", None, text, []

        if action in ("sell", "sellall"):
            r = db.sell(g, owner, now, value if action == "sell" else None)
            return "shop", None, f"💰 賣出 {r['count']} 件物品，獲得 {r['gold']:,} 風幣！", []
        if action == "unlock":
            r = db.unlock_plot(g, owner, now)
            return "shop", None, f"🟫 花了 {r['price']:,} 風幣，解鎖第 {r['plot_count']} 塊田！", []
        if action == "pickaxe":
            r = db.upgrade_pickaxe(g, owner, now)
            return "shop", None, f"⛏️ 花了 {r['price']:,} 風幣，十字鎬升級到 Lv{r['level']}！", []

        raise TownError("這個按鈕已經失效了，請重新輸入 `/小鎮`。")

    # ==========================================
    # 🖼️ 畫面
    # ==========================================

    def render(self, page, g, owner, now, arg=None, notice=None):
        snap = self.db.snapshot(g, owner)
        renderer = {
            "home": self._render_home, "farm": self._render_farm, "plant": self._render_plant,
            "mine": self._render_mine, "shop": self._render_shop, "rank": self._render_rank,
        }[page]
        embed, items = renderer(snap, g, owner, now, arg)
        if notice:
            embed.description = f"{notice}\n\n{embed.description or ''}".strip()
        view = discord.ui.View(timeout=None)
        for item in items:
            view.add_item(item)
        return embed, view

    def _btn(self, action, owner, arg=None, **kw):
        return TownButton(action, owner, arg, **kw)

    def _back(self, owner, row=4):
        return self._btn("home", owner, label="返回", emoji="⬅️", row=row)

    def _plot_line(self, slot, plot, now, plot_count):
        if slot > plot_count:
            price = svc.next_plot_price(self.content, plot_count) if slot == plot_count + 1 else None
            return f"`{slot}` 🔒 未解鎖" + (f"（商店 {price:,} 風幣）" if price else "")
        if not plot:
            return f"`{slot}` 🟫 空地"
        crop = self.content.crops[plot["crop_id"]]
        state, nxt = svc.plot_state(self.content, plot, now)
        name = f"{crop['emoji']} {crop['name']}"
        tags = (" 💧" if plot["watered_by"] else "") + (" 🐛有蟲" if svc.has_pest(plot, now) else "")
        if state == svc.GROWING:
            return f"`{slot}` {name} — <t:{nxt}:R> 成熟{tags}"
        mut = self.content.mutations[svc.effective_mutation(self.content, plot, now)]
        label = f" {mut['emoji']}**{mut['label']}**" if mut["label"] else ""
        if state == svc.FRESH:
            return f"`{slot}` {name}{label} — ✅ 可收成（<t:{nxt}:R> 過熟）{tags}"
        if state == svc.OVERRIPE:
            return f"`{slot}` {name}{label} — 🟡 過熟，只能賣一半（<t:{nxt}:R> 枯萎）{tags}"
        return f"`{slot}` {name} — 🥀 枯萎了，請清除"

    def _plot_lines(self, snap, now):
        p = snap["player"]
        return "\n".join(self._plot_line(s, snap["plots"].get(s), now, p["plot_count"])
                         for s in range(1, self.max_plots + 1))

    def _mine_line(self, snap, now):
        m = snap["mining"]
        if not m:
            return "閒置中，按「⛏️ 礦坑」出發吧"
        trip = self.content.mines[m["trip_id"]]
        if now < m["ready_at"]:
            return f"{trip['emoji']} {trip['name']} — <t:{m['ready_at']}:R> 挖完"
        return f"{trip['emoji']} {trip['name']} — ✅ 挖完了，可以收取"

    def _render_home(self, snap, g, owner, now, arg):
        p = snap["player"]
        checked = p["last_checkin"] == svc.day_key(now)
        e = discord.Embed(title=f"🏡 {p['name']} 的小鎮", color=GREEN)
        e.add_field(name="💰 風幣", value=f"{p['gold']:,}")
        e.add_field(name="📅 簽到", value=f"累計 {p['checkin_total']} 天" + (" · 今天已簽" if checked else ""))
        e.add_field(name="❤️ 人氣", value=str(p["likes"]))
        e.add_field(name="🌱 農田", value=self._plot_lines(snap, now), inline=False)
        e.add_field(name="⛏️ 礦坑", value=self._mine_line(snap, now), inline=False)
        e.set_footer(text="朋友可以用最下排按鈕幫忙 · 每天 00:00（台灣時間）換日")
        items = [
            self._btn("checkin", owner, label="已簽到" if checked else "簽到", emoji="📅",
                      style=discord.ButtonStyle.success, disabled=checked, row=0),
            self._btn("farm", owner, label="農田", emoji="🌱", row=0),
            self._btn("mine", owner, label="礦坑", emoji="⛏️", row=0),
            self._btn("bag", owner, label="背包", emoji="🎒", row=0),
            self._btn("shop", owner, label="商店", emoji="🏪", row=0),
            self._btn("rank", owner, "assets", label="排行榜", emoji="🏆", row=1),
            self._btn("refresh", owner, label="重新整理", emoji="🔄", row=1),
            self._btn("water", owner, label="幫忙澆水", emoji="💧", style=discord.ButtonStyle.primary, row=2),
            self._btn("pest", owner, "home", label="除蟲", emoji="🐛", style=discord.ButtonStyle.primary, row=2),
            self._btn("like", owner, label="按讚", emoji="❤️", style=discord.ButtonStyle.primary, row=2),
        ]
        return e, items

    def _render_farm(self, snap, g, owner, now, arg):
        p = snap["player"]
        e = discord.Embed(title=f"🌱 {p['name']} 的農田", color=GREEN,
                          description=self._plot_lines(snap, now))
        e.set_footer(text=f"💰 {p['gold']:,} 風幣 · 成熟後放太久會過熟、枯萎；有蟲沒除會降價且稀有變異失效")
        items, any_ready, any_wither, any_pest = [], False, False, False
        for slot in range(1, p["plot_count"] + 1):
            plot = snap["plots"].get(slot)
            if not plot:
                items.append(self._btn("plantpick", owner, str(slot), label=f"種第 {slot} 格", emoji="🌱",
                                       style=discord.ButtonStyle.success, row=0))
                continue
            state, _ = svc.plot_state(self.content, plot, now)
            any_pest |= svc.has_pest(plot, now)
            if state in (svc.FRESH, svc.OVERRIPE):
                any_ready = True
                items.append(self._btn("harvest", owner, str(slot), label=f"收成第 {slot} 格", emoji="🧺",
                                       style=discord.ButtonStyle.primary, row=0))
            elif state == svc.WITHERED:
                any_wither = True
                items.append(self._btn("plantpick", owner, str(slot), label=f"第 {slot} 格枯萎", emoji="🥀",
                                       disabled=True, row=0))
            else:
                items.append(self._btn("plantpick", owner, str(slot), label=f"第 {slot} 格生長中", emoji="⏳",
                                       disabled=True, row=0))
        items += [
            self._btn("harvestall", owner, label="全部收成", emoji="🧺", disabled=not any_ready, row=1),
            self._btn("pest", owner, "farm", label="除蟲", emoji="🐛", disabled=not any_pest, row=1),
            self._btn("clearwither", owner, label="清除枯萎", emoji="🥀", disabled=not any_wither, row=1),
            self._btn("farm", owner, label="重新整理", emoji="🔄", row=1),
            self._back(owner, row=1),
        ]
        return e, items

    def _render_plant(self, snap, g, owner, now, arg):
        p = snap["player"]
        lines = [f"{c['emoji']} **{c['name']}** — 種子 {c['seed_price']} 風幣 · {c['grow_hours']} 小時 · 賣 {c['sell_price']} 風幣"
                 for c in self.content.crops.values()]
        e = discord.Embed(title=f"🌱 第 {arg} 格要種什麼？", color=GREEN, description="\n".join(lines))
        e.set_footer(text=f"💰 你有 {p['gold']:,} 風幣 · 短作物每小時賺比較多，長作物適合過夜")
        options = [discord.SelectOption(label=f"{c['name']}（{c['seed_price']} 風幣）", value=cid, emoji=c["emoji"],
                                        description=f"{c['grow_hours']} 小時成熟 · 賣 {c['sell_price']} 風幣")
                   for cid, c in self.content.crops.items()]
        return e, [
            TownSelect("plant", owner, arg, options=options, placeholder="選擇要種的作物", row=0),
            self._btn("farm", owner, label="返回農田", emoji="⬅️", row=1),
        ]

    def _render_mine(self, snap, g, owner, now, arg):
        p = snap["player"]
        lv = p["pickaxe_lv"]
        e = discord.Embed(title=f"⛏️ {p['name']} 的礦坑", color=discord.Color.from_rgb(88, 101, 140))
        e.add_field(name="目前行程", value=self._mine_line(snap, now), inline=False)
        lines = [f"{t['emoji']} **{t['name']}** — {fmt_duration(svc.mine_duration(self.content, tid, lv))} · 挖 {t['rolls']} 次"
                 for tid, t in self.content.mines.items()]
        e.add_field(name="路線", value="\n".join(lines), inline=False)
        e.set_footer(text=f"十字鎬 Lv{lv} · 礦石不會壞，可以慢慢存 · 深層才挖得到寶石")
        if snap["mining"]:
            items = [self._btn("minecollect", owner, label="收取", emoji="📦", style=discord.ButtonStyle.success, row=0)]
        else:
            items = [self._btn("minego", owner, tid, label=f"{t['name']} {t['hours']}h", emoji=t["emoji"],
                               style=discord.ButtonStyle.primary, row=0)
                     for tid, t in self.content.mines.items()]
        items.append(self._back(owner, row=1))
        return e, items

    def _render_shop(self, snap, g, owner, now, arg):
        p = snap["player"]
        inv = self.db.inventory(g, owner)
        total = sum(i["total"] for i in inv)
        e = discord.Embed(title="🏪 商店", color=discord.Color.gold())
        if inv:
            lines = [f"{i['emoji']} {i['name']} ×{i['qty']} — {i['total']:,} 風幣" for i in inv[:15]]
            if len(inv) > 15:
                lines.append(f"…還有 {len(inv) - 15} 種物品")
            e.add_field(name=f"🎒 可賣出（共 {total:,} 風幣）", value="\n".join(lines), inline=False)
        else:
            e.add_field(name="🎒 可賣出", value="背包是空的，去收成或挖礦吧！", inline=False)
        plot_price = svc.next_plot_price(self.content, p["plot_count"])
        nxt_pick = svc.next_pickaxe(self.content, p["pickaxe_lv"])
        e.add_field(name="🟫 田地", value=f"{p['plot_count']} / {self.max_plots} 塊"
                    + (f"，下一塊 {plot_price:,} 風幣" if plot_price
                       else "（已全部解鎖）" if p["plot_count"] >= self.max_plots else "（目前無法解鎖）"))
        e.add_field(name="⛏️ 十字鎬", value=f"Lv{p['pickaxe_lv']}"
                    + (f"，升級 {nxt_pick['price']:,} 風幣" if nxt_pick else "（最高等級）"))
        e.set_footer(text=f"💰 你有 {p['gold']:,} 風幣")
        items = []
        if inv:
            options = [discord.SelectOption(label=f"{i['name']} ×{i['qty']}"[:100], value=i["item_id"], emoji=i["emoji"],
                                            description=f"賣出全部，共 {i['total']:,} 風幣")
                       for i in inv[:25]]
            items.append(TownSelect("sell", owner, options=options, placeholder="選擇要賣出的物品", row=0))
        items += [
            self._btn("sellall", owner, label="全部賣出", emoji="💰", style=discord.ButtonStyle.success,
                      disabled=not inv, row=1),
            self._btn("unlock", owner, label=f"解鎖田地（{plot_price:,} 風幣）" if plot_price else "田地已滿",
                      emoji="🟫", disabled=plot_price is None, row=1),
            self._btn("pickaxe", owner, label=f"十字鎬 Lv{nxt_pick['level']}（{nxt_pick['price']:,} 風幣）" if nxt_pick else "十字鎬已滿級",
                      emoji="⛏️", disabled=nxt_pick is None, row=1),
            self._back(owner, row=2),
        ]
        return e, items

    def _render_rank(self, snap, g, owner, now, arg):
        kind = arg if arg in RANK_KINDS else "assets"
        emoji, title, unit = RANK_KINDS[kind]
        rows = self.db.leaderboard(g, kind, now)
        lines = [f"{MEDALS[i] if i < 3 else f'`{i + 1}.`'} <@{r['user_id']}> — {r['v']:,} {unit}" for i, r in enumerate(rows)]
        e = discord.Embed(title=f"🏆 {title}排行榜", color=discord.Color.gold(),
                          description="\n".join(lines) if lines else "還沒有人上榜，快去努力吧！")
        if kind == "weekly":
            e.set_footer(text="本週收入每週一 00:00（台灣時間）重新計算")
        items = [self._btn("rank", owner, k, label=t, emoji=em,
                           style=discord.ButtonStyle.primary if k == kind else discord.ButtonStyle.secondary,
                           disabled=k == kind, row=0)
                 for k, (em, t, _) in RANK_KINDS.items()]
        items.append(self._back(owner, row=1))
        return e, items

    def render_bag(self, g, owner):
        inv = self.db.inventory(g, owner)
        e = discord.Embed(title="🎒 你的背包", color=GREEN)
        if not inv:
            e.description = "背包是空的。收成和挖礦得到的東西都會放在這裡。"
            return e
        lines = [f"{i['emoji']} {i['name']} ×{i['qty']} — 單價 {i['price']:,} 風幣" for i in inv]
        e.description = "\n".join(lines)[:4000]
        e.set_footer(text=f"全部賣出可得 {sum(i['total'] for i in inv):,} 風幣 · 到「🏪 商店」賣出")
        return e


async def setup(bot):
    await bot.add_cog(TownCog(bot))
