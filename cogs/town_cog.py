import time
import traceback

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import town_service as svc
from utils.announce import send_announcement
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
    "quests": ("📜", "任務", "個"),
}
MEDALS = ["🥇", "🥈", "🥉"]
ACH_CATEGORIES = {"checkin": "📅 簽到", "farm": "🌱 農田", "mine": "⛏️ 礦坑",
                  "social": "🤝 社交", "wealth": "💰 財富", "quest": "📜 任務"}
# 只是切換頁面的動作，不需要檢查成就（"ach" 不在這裡：打開成就頁時順便補發舊存檔的成就）
NOTIFY_LABELS = {"off": "通知：關", "each": "通知：每塊", "all": "通知：全部"}
NAV_ACTIONS = {"home", "refresh", "farm", "mine", "shop", "rank", "plantpick", "quest"}
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
        self.quest_loop.start()
        self.notify_loop.start()

    async def cog_unload(self):
        self.quest_loop.cancel()
        self.notify_loop.cancel()
        self.bot.remove_dynamic_items(TownButton, TownSelect)

    # ---------- 任務牆排程與公告 ----------

    @tasks.loop(minutes=1)
    async def quest_loop(self):
        """每分鐘補一次各伺服器的任務牆，傳說以上的新任務發公告。"""
        now = int(time.time())
        for gid in self.db.quest_guilds():
            await self._refresh_quests(self.bot.get_guild(int(gid)), int(gid), now)

    @quest_loop.before_loop
    async def _before_quest_loop(self):
        await self.bot.wait_until_ready()

    @quest_loop.error
    async def _quest_loop_error(self, error):
        traceback.print_exception(error)

    # ---------- 私訊通知 ----------

    @tasks.loop(minutes=1)
    async def notify_loop(self):
        """每分鐘檢查開了通知的玩家，田成熟／快過熟、礦坑挖完就私訊（同一分鐘的事件合併成一則）。"""
        now = int(time.time())
        for ev in self.db.pending_notifications(now):
            guild = self.bot.get_guild(int(ev["guild_id"]))
            if guild is None:
                continue
            try:
                user = self.bot.get_user(int(ev["user_id"])) or await self.bot.fetch_user(int(ev["user_id"]))
                await user.send(self._notify_text(guild, ev))
            except discord.Forbidden:
                self.db.disable_notify(ev["guild_id"], ev["user_id"])
            except discord.HTTPException:
                traceback.print_exc()

    @notify_loop.before_loop
    async def _before_notify_loop(self):
        await self.bot.wait_until_ready()

    @notify_loop.error
    async def _notify_loop_error(self, error):
        traceback.print_exception(error)

    def _notify_text(self, guild, ev):
        def plot_name(s):
            crop = self.content.crops[ev["plots"][s]["crop_id"]]
            return f"第 {s} 格{crop['emoji']}{crop['name']}"
        lines = [f"🔔 你在 **{guild.name}** 的小鎮有新動態！"]
        if ev["ready"]:
            head = "🌾 田全部成熟了" if ev["all_ready"] else "🌾 成熟了"
            lines.append(f"{head}：{'、'.join(plot_name(s) for s in ev['ready'])}")
        for s, at in ev["warn"]:
            lines.append(f"⏰ {plot_name(s)} <t:{at}:R> 就會過熟（過熟只能賣一半），快去收成！")
        if ev["mining"]:
            trip = self.content.mines[ev["mining"]["trip_id"]]
            lines.append(f"⛏️ {trip['emoji']}{trip['name']} 挖完了，可以收取")
        lines.append(f"到伺服器輸入 `/小鎮` 看看吧～ https://discord.com/channels/{guild.id}")
        lines.append("-# 不想收到通知可以在小鎮首頁按「🔔 通知」切換或關閉")
        return "\n".join(lines)

    async def _refresh_quests(self, guild, guild_id, now):
        for q in self.db.refresh_quests(guild_id, now):
            rar = self.content.quests["rarities"][q["rarity"]]
            if rar.get("announce") and guild is not None:
                # 新任務出現只發到公告頻道；沒設定就不發
                await send_announcement(
                    guild, f"📜 任務牆出現了{rar['emoji']} **{rar['name']}任務**！"
                           f"需要 {self._req_text(q['requirements'])}，獎勵 **{self._reward_text(q)}**，"
                           f"<t:{q['expires_at']}:R> 過期。輸入 `/小鎮` 搶先完成吧！",
                    allowed_mentions=NO_PINGS)

    def _achievement_notes(self, g, user, now):
        """發放新成就，回傳 (通知文字行, 公告)。"""
        new = self.db.check_achievements(g, user, now)
        if not new:
            return [], []
        name = self.db.snapshot(g, user)["player"]["name"]
        lines, announces = [], []
        for aid in new:
            a = self.content.achievements[aid]
            lines.append(f"🏅 解鎖成就「{a['emoji']} {a['name']}」！可以到「🏅 成就」裝備稱號。")
            if a.get("announce"):
                announces.append(f"🏅 **{name}** 解鎖了稀有成就「{a['emoji']} {a['name']}」：{a['desc']}！")
        return lines, announces

    @staticmethod
    def _reward_text(q):
        """任意作物的任務依實際交出的作物計價，完成前只能顯示最低值。"""
        wild = q["status"] != "done" and svc.has_wildcard(q["requirements"])
        return f"{q['reward']:,} 風幣" + ("起" if wild else "")

    def _req_text(self, requirements):
        parts = []
        for r in requirements:
            info = svc.requirement_info(self.content, r["match"])
            parts.append(f"{info['emoji']}{info['name']} ×{r['qty']}")
        return "、".join(parts)

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
            # 拜訪別人：只有自己看得到，只留訪客按鈕和重新整理
            if member.bot or not self.db.player_exists(g, member.id):
                await interaction.response.send_message(
                    f"{member.display_name} 還沒有開始經營小鎮。", ephemeral=True)
                return
            embed, view = self.render("visit", g, member.id, now)
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True,
                                                    allowed_mentions=NO_PINGS)
            return
        else:
            owner = interaction.user.id
            if self.db.ensure_player(g, owner, interaction.user.display_name, now):
                start = self.content.settings["start"]
                notice = (f"🎉 歡迎來到小鎮！你拿到了 {start['plots']} 塊田和 {start['gold']} 風幣，"
                          "先按「📅 簽到」，再去「🌱 農田」種下第一株作物吧。")
            elif self.db.pop_notify_failed(g, owner):
                notice = "⚠️ 私訊失敗，通知已關閉，請開啟伺服器私訊後再按「🔕 通知」打開。"
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
            if action == "visit":  # 訪客畫面的重新整理
                embed, view = self.render("visit", g, owner, now)
                await interaction.response.edit_message(embed=embed, view=view, allowed_mentions=NO_PINGS)
                return
            if user != owner:
                raise TownError(f"這是 <@{owner}> 的農場喔！輸入 `/小鎮` 打開你自己的。")
            if action == "bag":
                await interaction.response.send_message(embed=self.render_bag(g, owner), ephemeral=True)
                return
            if action in ("quest", "questgo"):
                await self._refresh_quests(interaction.guild, g, now)
            page, page_arg, notice, announces = self._owner_action(action, g, owner, arg, value, now)
            if action not in NAV_ACTIONS:
                lines, ach_announces = self._achievement_notes(g, owner, now)
                notice = "\n".join(filter(None, [notice, *lines])) or None
                announces += ach_announces
            embed, view = self.render(page, g, owner, now, arg=page_arg, notice=notice)
            await interaction.response.edit_message(embed=embed, view=view, allowed_mentions=NO_PINGS)
            for text in announces:
                await send_announcement(interaction.guild, text, fallback=interaction.channel,
                                        allowed_mentions=NO_PINGS)
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

        lines, ach_announces = self._achievement_notes(g, user, now)
        text = "\n".join([text, *lines])
        for a in ach_announces:
            await send_announcement(interaction.guild, a, fallback=interaction.channel, allowed_mentions=NO_PINGS)
        if arg == "visit":
            # 訪客自己的訪客畫面（ephemeral）：結果寫在畫面上方
            embed, view = self.render("visit", g, owner, now, notice=text)
            await interaction.response.edit_message(embed=embed, view=view, allowed_mentions=NO_PINGS)
            return
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
        if action in ("farm", "mine", "shop", "quest", "ach"):
            return action, None, None, []
        if action == "rank":
            return "rank", arg or "assets", None, []

        if action == "checkin":
            r = db.checkin(g, owner, now)
            text = f"📅 簽到成功！+{r['daily']} 風幣（累計 {r['total']} 天）"
            if r["milestone"]:
                text += f"\n🎁 累計 {r['total']} 天里程碑：額外 +{r['milestone']} 風幣！"
            return "home", None, text, []

        if action == "notify":
            mode = db.cycle_notify(g, owner, now)
            text = {
                "off": "🔕 已關閉私訊通知。",
                "each": "🔔 通知：**每塊**。每塊田成熟、作物快過熟、礦坑挖完都會私訊你。",
                "all": "🔔 通知：**全部**。所有種了作物的田都成熟才私訊一次；作物快過熟、礦坑挖完也會私訊你。",
            }[mode]
            if mode != "off":
                text += "\n-# 記得開啟這個伺服器的私訊，否則收不到通知。"
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
                tier = svc.plot_tier(content, r["tier"])
                bonus = f" · {tier['emoji']}{tier['name']}農田" if r["tier"] > 1 else ""
                parts, value = [], 0
                for item_id, n in r["items"]:
                    it = svc.item_info(content, item_id)
                    parts.append(f"{it['emoji']}{it['name']} ×{n}")
                    value += it["price"] * n
                lines.append(f"🧺 第 {r['slot']} 格：{' ＋ '.join(parts)}（價值 {value:,} 風幣{bonus}）")
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
        if action in ("sellcrops", "sellores", "sellcommon"):  # sellcommon：舊訊息上的按鈕
            kind = {"sellcrops": "crop", "sellores": "ore"}.get(action)
            label = {"crop": "作物", "ore": "礦物"}.get(kind, "物品")
            r = db.sell(g, owner, now, common_only=True, kind=kind)
            text = f"💰 賣出 {r['count']} 件普通{label}，獲得 {r['gold']:,} 風幣！"
            if r["kept"]:
                text += f"\n⭐ 保留了 {r['kept']} 件稀有{label}，可以留著交任務。"
            return "shop", None, text, []
        if action == "unlock":
            r = db.unlock_plot(g, owner, now)
            return "shop", None, f"🟫 花了 {r['price']:,} 風幣，解鎖第 {r['plot_count']} 塊田！", []
        if action == "plotup":
            r = db.upgrade_plot(g, owner, int(value), now)
            return "shop", None, (f"🌾 花了 {r['price']:,} 風幣，第 {r['slot']} 格升級成{r['emoji']}{r['name']}農田！"
                                  f"之後收成 ×{r['yield_mult']}。"), []
        if action == "pickaxe":
            r = db.upgrade_pickaxe(g, owner, now)
            text = f"⛏️ 花了 {r['price']:,} 風幣，十字鎬升級到 Lv{r['level']}！"
            if r.get("unlocks"):
                text += f"\n🔓 {r['unlocks']}"
            return "shop", None, text, []

        if action == "questgo":
            r = db.deliver_quest(g, owner, int(arg), now)
            q = r["quest"]
            rar = content.quests["rarities"][q["rarity"]]
            gave = "、".join(f"{svc.item_info(content, i)['emoji']}{svc.item_info(content, i)['name']} ×{n}"
                             for i, n in r["taken"].items())
            text = f"📜 完成了{rar['emoji']}{rar['name']}任務！交出 {gave}，獲得 **{r['reward']:,} 風幣**！"
            announces = []
            if rar.get("announce"):
                name = db.snapshot(g, owner)["player"]["name"]
                announces.append(f"🎉 **{name}** 完成了{rar['emoji']} **{rar['name']}任務**，獲得 {r['reward']:,} 風幣！")
            return "quest", None, text, announces
        if action == "title":
            aid = None if value == "none" else value
            if aid is not None and aid not in content.achievements:
                raise TownError("沒有這個稱號。")
            db.set_title(g, owner, aid)
            if aid is None:
                return "ach", None, "🏅 已取下稱號。", []
            a = content.achievements[aid]
            return "ach", None, f"🏅 裝備了稱號「{a['emoji']} {a['name']}」！", []

        raise TownError("這個按鈕已經失效了，請重新輸入 `/小鎮`。")

    # ==========================================
    # 🖼️ 畫面
    # ==========================================

    def render(self, page, g, owner, now, arg=None, notice=None):
        snap = self.db.snapshot(g, owner)
        renderer = {
            "home": self._render_home, "farm": self._render_farm, "plant": self._render_plant,
            "mine": self._render_mine, "shop": self._render_shop, "rank": self._render_rank,
            "quest": self._render_quest, "ach": self._render_ach, "visit": self._render_visit,
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

    def _tier_tag(self, tier):
        """升級過的田才顯示等級標籤。"""
        t = svc.plot_tier(self.content, tier)
        return f" {t['emoji']}{t['name']}" if tier > 1 else ""

    def _plot_line(self, slot, plot, now, plot_count, tier=1):
        if slot > plot_count:
            price = svc.next_plot_price(self.content, plot_count) if slot == plot_count + 1 else None
            return f"`{slot}` 🔒 未解鎖" + (f"（商店 {price:,} 風幣）" if price else "")
        if not plot:
            return f"`{slot}` 🟫 空地{self._tier_tag(tier)}"
        crop = self.content.crops[plot["crop_id"]]
        state, nxt = svc.plot_state(self.content, plot, now)
        name = f"{crop['emoji']} {crop['name']}{self._tier_tag(tier)}"
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
        return "\n".join(self._plot_line(s, snap["plots"].get(s), now, p["plot_count"], snap["tiers"].get(s, 1))
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
        title = self.content.achievements.get(p.get("title") or "")
        e = discord.Embed(title=f"🏡 {p['name']} 的小鎮" + (f" 〔{title['emoji']}{title['name']}〕" if title else ""),
                          color=GREEN)
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
            self._btn("quest", owner, label="任務牆", emoji="📜", style=discord.ButtonStyle.primary, row=1),
            self._btn("rank", owner, "assets", label="排行榜", emoji="🏆", row=1),
            self._btn("ach", owner, label="成就", emoji="🏅", row=1),
            self._btn("refresh", owner, label="重新整理", emoji="🔄", row=1),
            self._btn("notify", owner, label=NOTIFY_LABELS.get(p.get("notify"), NOTIFY_LABELS["off"]),
                      emoji="🔕" if p.get("notify", "off") == "off" else "🔔", row=1),
            self._btn("water", owner, label="幫忙澆水", emoji="💧", style=discord.ButtonStyle.primary, row=2),
            self._btn("pest", owner, "home", label="除蟲", emoji="🐛", style=discord.ButtonStyle.primary, row=2),
            self._btn("like", owner, label="按讚", emoji="❤️", style=discord.ButtonStyle.primary, row=2),
        ]
        return e, items

    def _render_visit(self, snap, g, owner, now, arg):
        """拜訪別人的小鎮：同樣的農場畫面，只有訪客按鈕和重新整理。按鈕的 arg 都是 visit，結果會更新這則訊息。"""
        e, _ = self._render_home(snap, g, owner, now, arg)
        e.set_footer(text="你正在拜訪這座小鎮，只有你看得到這則訊息 · 每天 00:00（台灣時間）換日")
        items = [
            self._btn("water", owner, "visit", label="幫忙澆水", emoji="💧", style=discord.ButtonStyle.primary, row=0),
            self._btn("pest", owner, "visit", label="除蟲", emoji="🐛", style=discord.ButtonStyle.primary, row=0),
            self._btn("like", owner, "visit", label="按讚", emoji="❤️", style=discord.ButtonStyle.primary, row=0),
            self._btn("visit", owner, label="重新整理", emoji="🔄", row=0),
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
        lines = []
        for tid, t in self.content.mines.items():
            need = svc.trip_min_pickaxe(self.content, tid)
            line = f"{t['emoji']} **{t['name']}** — {fmt_duration(svc.mine_duration(self.content, tid, lv))} · 挖 {t['rolls']} 次"
            lines.append(line + (f" · 🔒 需要 Lv{need} 十字鎬" if lv < need else ""))
        e.add_field(name="路線", value="\n".join(lines), inline=False)
        ores = [f"{'✅' if lv >= need else '🔒'} {self.content.ores[o]['emoji']} {self.content.ores[o]['name']}：Lv{need} 十字鎬以上才挖得到"
                for o, need in sorted(svc.ore_min_pickaxe(self.content).items(), key=lambda kv: kv[1])]
        if ores:
            e.add_field(name="稀有礦物", value="\n".join(ores), inline=False)
        e.set_footer(text=f"十字鎬 Lv{lv} · 礦石不會壞，可以慢慢存 · 到商店升級十字鎬")
        if snap["mining"]:
            items = [self._btn("minecollect", owner, label="收取", emoji="📦", style=discord.ButtonStyle.success, row=0)]
        else:
            items = []
            for tid, t in self.content.mines.items():
                need = svc.trip_min_pickaxe(self.content, tid)
                locked = lv < need
                items.append(self._btn("minego", owner, tid,
                                       label=f"{t['name']} {t['hours']}h" + (f"（Lv{need}）" if locked else ""),
                                       emoji="🔒" if locked else t["emoji"], disabled=locked,
                                       style=discord.ButtonStyle.primary, row=0))
        items.append(self._back(owner, row=1))
        return e, items

    def _render_shop(self, snap, g, owner, now, arg):
        p = snap["player"]
        inv = self.db.inventory(g, owner)
        common = [i for i in inv if not i["rare"]]
        rare = [i for i in inv if i["rare"]]
        common_total = sum(i["total"] for i in common)
        crop_total = sum(i["total"] for i in common if i["item_id"].startswith("crop:"))
        ore_total = common_total - crop_total
        e = discord.Embed(title="🏪 商店", color=discord.Color.gold())

        def listing(items, limit=10):
            lines = [f"{i['emoji']} {i['name']} ×{i['qty']} — {i['total']:,} 風幣" for i in items[:limit]]
            if len(items) > limit:
                lines.append(f"…還有 {len(items) - limit} 種物品")
            return "\n".join(lines)

        if not inv:
            e.add_field(name="🎒 可賣出", value="背包是空的，去收成或挖礦吧！", inline=False)
        if crop_total:
            e.add_field(name=f"🧺 普通作物（共 {crop_total:,} 風幣）",
                        value=listing([i for i in common if i["item_id"].startswith("crop:")]), inline=False)
        if ore_total:
            e.add_field(name=f"⛏️ 普通礦物（共 {ore_total:,} 風幣）",
                        value=listing([i for i in common if i["item_id"].startswith("ore:")]), inline=False)
        if rare:
            e.add_field(name=f"⭐ 稀有物品（共 {sum(i['total'] for i in rare):,} 風幣）", value=listing(rare), inline=False)
        plot_price = svc.next_plot_price(self.content, p["plot_count"])
        nxt_pick = svc.next_pickaxe(self.content, p["pickaxe_lv"])
        e.add_field(name="🟫 田地", value=f"{p['plot_count']} / {self.max_plots} 塊"
                    + (f"，下一塊 {plot_price:,} 風幣" if plot_price
                       else "（已全部解鎖）" if p["plot_count"] >= self.max_plots else "（目前無法解鎖）"))
        e.add_field(name="⛏️ 十字鎬", value=f"Lv{p['pickaxe_lv']}"
                    + (f"，升級 {nxt_pick['price']:,} 風幣" + (f"\n下一級：{nxt_pick['unlocks']}" if nxt_pick.get("unlocks") else "")
                       if nxt_pick else "（最高等級）"))
        tiers_cfg = self.content.settings["plot_tiers"]
        upgrades = []  # (slot, 目前等級, 下一級)
        if p["plot_count"] >= tiers_cfg["require_plots"]:
            lines = []
            for slot in range(1, p["plot_count"] + 1):
                cur = snap["tiers"].get(slot, 1)
                nxt = svc.next_plot_tier(self.content, cur)
                cur_name = svc.plot_tier(self.content, cur)["name"] or "一般"
                if nxt:
                    upgrades.append((slot, cur_name, nxt))
                    lines.append(f"`{slot}` {cur_name} → {nxt['emoji']}{nxt['name']}（收成 ×{nxt['yield_mult']}）{nxt['price']:,} 風幣")
                else:
                    lines.append(f"`{slot}` {self._tier_tag(cur).strip()}（已滿級）")
            e.add_field(name="🌾 農田升級", value="\n".join(lines), inline=False)
        else:
            e.add_field(name="🌾 農田升級", inline=False,
                        value=f"解鎖全部 {tiers_cfg['require_plots']} 塊田後開放（目前 {p['plot_count']} 塊）："
                              + "、".join(f"{t['name']} ×{t['yield_mult']}" for t in tiers_cfg["tiers"]))
        e.set_footer(text=f"💰 你有 {p['gold']:,} 風幣 · 稀有物品：金色／巨大／彩虹作物、金礦、寶石、秘銀、星辰石")
        items = []
        if inv:
            options = [discord.SelectOption(label=f"{'⭐' if i['rare'] else ''}{i['name']} ×{i['qty']}"[:100],
                                            value=i["item_id"], emoji=i["emoji"],
                                            description=f"賣出全部，共 {i['total']:,} 風幣")
                       for i in inv[:25]]
            items.append(TownSelect("sell", owner, options=options, placeholder="選擇要賣出的物品", row=0))
        items += [
            self._btn("sellcrops", owner,
                      label=f"賣出普通作物（{crop_total:,} 風幣）" if crop_total else "沒有普通作物",
                      emoji="🧺", style=discord.ButtonStyle.success, disabled=not crop_total, row=1),
            self._btn("sellores", owner,
                      label=f"賣出普通礦物（{ore_total:,} 風幣）" if ore_total else "沒有普通礦物",
                      emoji="⛏️", style=discord.ButtonStyle.success, disabled=not ore_total, row=1),
            self._btn("sellall", owner, label="全部賣出（含稀有）", emoji="💰", disabled=not inv, row=1),
        ]
        if upgrades:
            options = [discord.SelectOption(label=f"第 {slot} 格：{cur} → {nxt['name']}（{nxt['price']:,} 風幣）",
                                            value=str(slot), emoji=nxt["emoji"],
                                            description=f"之後這塊田收成 ×{nxt['yield_mult']}")
                       for slot, cur, nxt in upgrades]
            items.append(TownSelect("plotup", owner, options=options, placeholder="🌾 選擇要升級的農田", row=2))
        items += [
            self._btn("unlock", owner, label=f"解鎖田地（{plot_price:,} 風幣）" if plot_price else "田地已滿",
                      emoji="🟫", disabled=plot_price is None, row=3),
            self._btn("pickaxe", owner, label=f"十字鎬 Lv{nxt_pick['level']}（{nxt_pick['price']:,} 風幣）" if nxt_pick else "十字鎬已滿級",
                      emoji="⛏️", disabled=nxt_pick is None, row=3),
            self._back(owner, row=3),
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

    def _render_quest(self, snap, g, owner, now, arg):
        quests = self.content.quests
        rars = quests["rarities"]
        board = self.db.quest_board(g)
        inv = self.db.inventory_map(g, owner)
        odds = " · ".join(f"{r['emoji']}{r['name']} {r['weight']:g}%" for r in rars.values())
        e = discord.Embed(title="📜 任務牆", color=discord.Color.from_rgb(166, 124, 82),
                          description=f"全伺服器共用，**誰先交付誰拿獎勵**！\n{odds}")
        buttons = []
        for slot in range(1, quests["slots"] + 1):
            q = board.get(slot)
            row = 0 if slot <= 3 else 1
            can = False
            if not q or q["status"] == "expired":
                e.add_field(name=f"{slot}. ⏳ 準備中", value="新任務馬上出現", inline=False)
            else:
                rar = rars[q["rarity"]]
                head = f"{slot}. {rar['emoji']} {rar['name']}任務 · 🪙 {self._reward_text(q)}"
                if q["status"] == "done":
                    nxt = q["completed_at"] + svc.quest_cooldown(self.content)
                    value = f"✅ 已被 <@{q['completed_by']}> 完成 · 新任務 <t:{nxt}:R> 出現"
                elif now >= q["expires_at"]:
                    value = "⌛ 已過期，新任務馬上出現"
                else:
                    ok, have, take = svc.allocate(self.content, q["requirements"], inv)
                    can = ok
                    lines = []
                    for r, h in zip(q["requirements"], have):
                        info = svc.requirement_info(self.content, r["match"])
                        mark = "✅" if h >= r["qty"] else "▫️"
                        lines.append(f"{mark} {info['emoji']} {info['name']} ×{r['qty']}（你有 {min(h, r['qty'])}）")
                    if ok and svc.has_wildcard(q["requirements"]):
                        lines.append(f"💰 你現在交付可得 **{svc.quest_reward(self.content, q, take):,} 風幣**")
                    value = "\n".join(lines) + f"\n⌛ <t:{q['expires_at']}:R> 過期"
                e.add_field(name=head, value=value, inline=False)
            buttons.append(self._btn("questgo", owner, str(q["id"]) if q else None, label=f"交付 {slot}", emoji="📦",
                                     style=discord.ButtonStyle.success if can else discord.ButtonStyle.secondary,
                                     disabled=not can, row=row) if q else
                           self._btn("quest", owner, f"wait{slot}", label=f"交付 {slot}", emoji="📦",
                                     disabled=True, row=row))
        e.set_footer(text=f"被完成的格子 {quests['cooldown_minutes']} 分鐘後補上新任務 · 作物只收 100% 新鮮的 · "
                          f"「任意」需求依實際交出的作物計價，先用最便宜的")
        return e, buttons + [
            self._btn("quest", owner, label="重新整理", emoji="🔄", row=2),
            self._back(owner, row=2),
        ]

    def _render_ach(self, snap, g, owner, now, arg):
        p = snap["player"]
        earned = self.db.achievements(g, owner)
        stats = self.db.player_stats(g, owner)
        achs = self.content.achievements
        cur = achs.get(p.get("title") or "")
        e = discord.Embed(title=f"🏅 {p['name']} 的成就", color=discord.Color.gold(),
                          description="目前稱號：" + (f"{cur['emoji']} **{cur['name']}**" if cur else "（未裝備）"))
        for cat, label in ACH_CATEGORIES.items():
            lines = []
            for aid, a in achs.items():
                if a["category"] != cat:
                    continue
                if aid in earned:
                    lines.append(f"✅ {a['emoji']} **{a['name']}** — {a['desc']}")
                else:
                    lines.append(f"🔒 {a['emoji']} {a['name']} — {a['desc']}"
                                 f"（{min(stats.get(a['stat'], 0), a['count']):,}/{a['count']:,}）")
            if lines:
                done = sum(1 for aid, a in achs.items() if a["category"] == cat and aid in earned)
                total = sum(1 for a in achs.values() if a["category"] == cat)
                e.add_field(name=f"{label}（{done}/{total}）", value="\n".join(lines)[:1024], inline=False)
        e.set_footer(text=f"已解鎖 {len(earned)} / {len(achs)} · 裝備的稱號會顯示在你的小鎮標題")
        items = []
        if earned:
            # Select 最多 25 個選項：「不顯示」+ 清單中排在最後（通常最難）的 24 個
            mine = [aid for aid in achs if aid in earned][-24:]
            options = [discord.SelectOption(label="不顯示稱號", value="none", emoji="➖")]
            options += [discord.SelectOption(label=achs[aid]["name"], value=aid, emoji=achs[aid]["emoji"],
                                             description=achs[aid]["desc"], default=aid == p.get("title"))
                        for aid in mine]
            items.append(TownSelect("title", owner, options=options, placeholder="選擇要裝備的稱號", row=0))
        items.append(self._back(owner, row=1))
        return e, items

    def render_bag(self, g, owner):
        inv = self.db.inventory(g, owner)
        e = discord.Embed(title="🎒 你的背包", color=GREEN)
        if not inv:
            e.description = "背包是空的。收成和挖礦得到的東西都會放在這裡。"
            return e
        lines = [f"{i['emoji']} {'⭐' if i['rare'] else ''}{i['name']} ×{i['qty']} — 單價 {i['price']:,} 風幣" for i in inv]
        e.description = "\n".join(lines)[:4000]
        e.set_footer(text=f"全部賣出可得 {sum(i['total'] for i in inv):,} 風幣 · 到「🏪 商店」賣出")
        return e


async def setup(bot):
    await bot.add_cog(TownCog(bot))
