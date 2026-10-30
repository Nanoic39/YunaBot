"""早报内容模块集合。

每个模块自行负责「取数(collect) / 文本渲染(text) / 图片渲染(draw)」，
主流程只按群配置的启用与顺序调用，新增板块无需改动主流程。
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageDraw

from nonebot import get_plugin_config, logger

from src.yuna_plugins.datadetail.wordcloud import render_wordcloud
from src.yuna_plugins.weather import draw_weather_icon, get_weather

from .config import Config
from .module_base import MorningModule, ModuleContext, register
from .render import (
    CACHE_DIR,
    PLUGIN_DIR,
    _ACCENT,
    _ACCENT_SOFT,
    _LINE,
    _RADIUS,
    _RANK_COLORS,
    _SUB_COLOR,
    _TEXT_COLOR,
    _TITLE_COLOR,
    _center,
    _ellipsize,
    _fmt_chars,
    _hbar,
    _load_font,
    _soft_card,
    _wrap,
    draw_fortune_card,
)
from .sources import (
    _blocked,
    fetch_anime_image,
    fetch_bangumi_calendar,
    fetch_bangumi_subjects,
    fetch_bilibili_timeline,
    fetch_hotlist,
    fetch_netease_albums,
    fetch_news,
)

cfg = get_plugin_config(Config)

DATADETAIL_DIR = PLUGIN_DIR.parent / "datadetail" / "data"
DATA_DIR = PLUGIN_DIR / "data"
BIRTHDAYS_FILE = DATA_DIR / "birthdays.json"
ANNIVERSARIES_FILE = DATA_DIR / "anniversaries.json"
GAMES_FILE = DATA_DIR / "games.json"
MUSIC_FILE = DATA_DIR / "music.json"


def _header(draw, ix, ir, y, title, right=""):
    draw.text((ix, y), title, font=_load_font(23, bold=True), fill=_TITLE_COLOR)
    if right:
        f = _load_font(17)
        draw.text((ir - f.getlength(right), y + 7), right, font=f, fill=_SUB_COLOR)


def _mod_title(ctx: ModuleContext, name: str, default: str) -> str:
    """取群级自定义模块标题，未配置时回退默认标题。"""
    return (ctx.titles or {}).get(name) or default


def _col_box(ctx: ModuleContext) -> tuple[int, int]:
    """取当前渲染列的内容区 (左边界, 右边界)：由渲染层按单列/整行宽度注入。"""
    x0 = ctx.col_x0
    return x0, x0 + ctx.col_w


# ==================== 天气 ====================
class WeatherModule(MorningModule):
    name = "weather"
    title = "天气"
    default_enabled = True
    cache_ttl = 1800
    # 首行与统计卡等高对齐：对方更高时本卡增高补齐（见 render.render_report）
    align_top = True

    async def collect(self, ctx: ModuleContext) -> dict | None:
        city = (ctx.gc.get("weather") or {}).get("city") or ""
        return await get_weather(city)

    def text(self, data, ctx) -> list[str]:
        w = data
        lines = [f"【天气 · {w.get('city') or ''}】", f"{w.get('temp', '--')}°C  {w.get('desc', '')}"]
        extra = []
        if w.get("feels_like"):
            extra.append(f"体感 {w['feels_like']}°C")
        if w.get("humidity"):
            extra.append(f"湿度 {w['humidity']}%")
        if w.get("wind"):
            extra.append(str(w["wind"]))
        if extra:
            lines.append(" · ".join(extra))
        return lines

    def draw(self, img, y, data, ctx) -> int:
        w = data
        x0, x1 = _col_box(ctx)
        ix, ir = x0 + 22, x1 - 22
        # 基准高度 150；渲染层通过 ctx.min_height 注入目标高度时按需增高（顶部内容居中偏移）。
        height = max(150, int(getattr(ctx, "min_height", 0) or 0))
        extra = height - 150
        shift = extra // 2
        _soft_card(img, [x0, y, x1, y + height], _RADIUS)
        draw = ImageDraw.Draw(img)

        draw_weather_icon(
            draw, ix + 40, y + 54 + shift, 78, w.get("icon"),
            cloud=(150, 166, 190, 255), sun=(255, 178, 64, 255), rain=(110, 165, 230, 255),
            snow=(150, 175, 205, 255), fog=(160, 170, 185, 255),
        )

        tx = ix + 92
        temp = w.get("temp")
        temp_txt = f"{temp}°" if temp not in (None, "") else "--"
        f_temp = _load_font(42, bold=True)
        draw.text((ir - f_temp.getlength(temp_txt), y + 22 + shift), temp_txt, font=f_temp, fill=_ACCENT)
        draw.text((tx, y + 28 + shift), str(w.get("city") or ""), font=_load_font(26, bold=True), fill=_TITLE_COLOR)
        line_y = y + 96 + extra
        draw.line([(ix, line_y), (ir, line_y)], fill=_LINE, width=1)

        parts = []
        if w.get("desc"):
            parts.append(str(w["desc"]))
        if w.get("feels_like"):
            parts.append(f"体感 {w['feels_like']}°")
        if w.get("humidity"):
            parts.append(f"湿度 {w['humidity']}%")
        if w.get("wind"):
            parts.append(str(w["wind"]))
        draw.text(
            (ix, line_y + 16),
            _ellipsize(" · ".join(parts), _load_font(19), ir - ix),
            font=_load_font(19),
            fill=_SUB_COLOR,
        )
        return y + height


# ==================== 群聊数据统计 ====================
def _read_daily(gid: str, date_str: str) -> dict | None:
    path = DATADETAIL_DIR / f"{gid}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return (data.get("daily") or {}).get(date_str)


def _read_words(gid: str, date_str: str) -> dict[str, int]:
    """读取某天的词频（datadetail 采集），过滤非正数项。"""
    d = _read_daily(gid, date_str) or {}
    out: dict[str, int] = {}
    for k, v in (d.get("words") or {}).items():
        if isinstance(v, (int, float)) and v > 0:
            out[str(k)] = int(v)
    return out


class StatsModule(MorningModule):
    name = "stats"
    title = "群聊数据统计"
    default_enabled = True
    cache_ttl = 0

    async def collect(self, ctx: ModuleContext) -> dict | None:
        date_str = (ctx.now - timedelta(days=1)).strftime("%Y-%m-%d")
        d = _read_daily(ctx.gid, date_str)
        if not d:
            return None
        users = d.get("users") or {}
        ranked = sorted(users.items(), key=lambda kv: kv[1].get("count", 0), reverse=True)[
            : cfg.morning_top_users
        ]
        top = [
            {"name": u.get("nickname") or qq, "count": u.get("count", 0), "chars": u.get("chars", 0)}
            for qq, u in ranked
            if u.get("count", 0) > 0
        ]
        hours = d.get("hours") or [0] * 24
        peak = max(range(24), key=lambda h: hours[h]) if any(hours) else None
        return {
            "date": date_str,
            "total": d.get("total", 0),
            "chars": d.get("chars", 0),
            "active": len(users),
            "top": top,
            "peak_hour": peak,
        }

    def text(self, data, ctx) -> list[str]:
        s = data
        lines = [
            f"【群聊数据统计 · {s['date']}】",
            f"发言 {s['total']} 条 · 活跃 {s['active']} 人 · 总字数 {s['chars']}",
        ]
        if s["top"]:
            lines.append(" · ".join(f"{t['name']} {t['count']}条" for t in s["top"]))
        return lines

    def draw(self, img, y, data, ctx) -> int:
        s = data
        x0, x1 = _col_box(ctx)
        ix, ir = x0 + 22, x1 - 22
        top = s.get("top") or []
        height = 84 + len(top) * 42 + 20
        _soft_card(img, [x0, y, x1, y + height], _RADIUS)
        draw = ImageDraw.Draw(img)

        _header(draw, ix, ir, y + 20, _mod_title(ctx, "stats", "群聊数据统计"), s["date"])
        summ = (
            f"发言 {s['total']} 条 · 活跃 {s['active']} 人"
            f" · 共计 {_fmt_chars(s['chars'])}"
        )
        if s.get("peak_hour") is not None:
            summ += f" · 最活跃 {s['peak_hour']:02d}:00"
        draw.text((ix, y + 52), _ellipsize(summ, _load_font(18), ir - ix), font=_load_font(18), fill=_SUB_COLOR)

        ry = y + 84
        if top:
            maxc = max(t["count"] for t in top) or 1
            f_name = _load_font(20)
            f_cnt = _load_font(19, bold=True)
            for i, t in enumerate(top, 1):
                color = _RANK_COLORS[i - 1] if i - 1 < len(_RANK_COLORS) else _ACCENT
                draw.ellipse([ix, ry + 4, ix + 24, ry + 28], fill=color)
                _center(draw, ix + 12, ry + 8, str(i), _load_font(16, bold=True), (255, 255, 255, 255))
                name_x = ix + 34
                draw.text((name_x, ry + 7), _ellipsize(str(t["name"]), f_name, 130), font=f_name, fill=_TEXT_COLOR)
                bar_x = name_x + 138
                bar_w = ir - 66 - bar_x
                _hbar(draw, bar_x, ry + 10, bar_w, 14, t["count"] / maxc, _ACCENT)
                cnt = f"{t['count']} 条"
                draw.text((ir - f_cnt.getlength(cnt), ry + 6), cnt, font=f_cnt, fill=_SUB_COLOR)
                ry += 42
        return y + height


# ==================== 群聊词云 ====================
class WordCloudModule(MorningModule):
    name = "wordcloud"
    title = "群聊词云"
    # 宽幅大图，默认开启；群内可用 /morning disable wordcloud 关闭
    default_enabled = True
    cache_ttl = 0
    full_width = True

    async def collect(self, ctx: ModuleContext) -> dict | None:
        date_str = (ctx.now - timedelta(days=1)).strftime("%Y-%m-%d")
        words = _read_words(ctx.gid, date_str)
        if not words:
            return None
        ranked = sorted(words.items(), key=lambda kv: kv[1], reverse=True)[
            : cfg.morning_wordcloud_size
        ]
        return {"date": date_str, "words": dict(ranked)}

    def text(self, data, ctx) -> list[str]:
        words = data.get("words") or {}
        hot = " · ".join(f"{w}×{c}" for w, c in list(words.items())[:15])
        return [f"【群聊词云 · {data['date']}】", hot or "暂无热词"]

    def draw(self, img, y, data, ctx) -> int:
        words = data.get("words") or {}
        if not words:
            return y
        x0, x1 = _col_box(ctx)
        ix, ir = x0 + 22, x1 - 22
        iw = ir - ix
        header_h = 58
        wc_h = 440
        height = header_h + wc_h + 20
        _soft_card(img, [x0, y, x1, y + height], _RADIUS)
        draw = ImageDraw.Draw(img)
        _header(
            draw, ix, ir, y + 16, _mod_title(ctx, "wordcloud", "群聊词云"), data["date"]
        )
        wc_img = render_wordcloud(
            words, width=iw, height=wc_h, max_words=cfg.morning_wordcloud_size
        )
        if wc_img is not None:
            img.paste(wc_img, (ix, y + header_h), wc_img)
        else:
            # 缺少 wordcloud 依赖 / 中文字体时降级为纯文本热词
            hot = "  ".join(f"{w}×{c}" for w, c in list(words.items())[:40])
            ty = y + header_h + 10
            for ln in _wrap(hot, _load_font(20), iw)[:14]:
                draw.text((ix, ty), ln, font=_load_font(20), fill=_TEXT_COLOR)
                ty += 28
        return y + height


# ==================== 每日寄语（hitokoto） ====================
_QUOTES = [
    "今天也要元气满满哦～",
    "愿你所求皆如愿，所行化坦途。",
    "别睡啦，起来征服今天吧！",
    "今天也是充满希望的一天呢！",
    "一起加油，笨蛋是不会认输的！",
    "清晨的风，捎来今天的温柔。",
    "愿你今天遇到的都是温柔与善意。",
    "新的冒险开始了，准备好了吗？",
    "慢慢来，比较快，一切都会好起来的。",
    "记得吃早饭，也要照顾好心情呀～",
]
_HITOKOTO_CATS = ("a", "b", "c")
_HITOKOTO_URL = "https://v1.hitokoto.cn/"


def _pick_quote(date_str: str) -> str:
    return _QUOTES[sum(ord(c) for c in date_str) % len(_QUOTES)]


async def _fetch_quote() -> str:
    """从 hitokoto.cn 获取一条 ACG 向一言；失败时回退本地寄语。"""
    params = [("encode", "json")] + [("c", c) for c in _HITOKOTO_CATS]
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(
                _HITOKOTO_URL, params=params, headers={"User-Agent": "yuna-bot/morninginfo"}
            )
            resp.raise_for_status()
            text = str(resp.json().get("hitokoto") or "").strip()
            if text:
                return text
    except Exception as e:  # noqa: BLE001
        logger.warning(f"【早报】获取一言失败，改用本地寄语：{e}")
    return _pick_quote(datetime.now().strftime("%Y-%m-%d"))


class QuoteModule(MorningModule):
    name = "quote"
    title = "每日寄语"
    default_enabled = True
    cache_ttl = 0

    async def collect(self, ctx: ModuleContext) -> str | None:
        return await _fetch_quote()

    def text(self, data, ctx) -> list[str]:
        return [str(data)]

    def draw(self, img, y, data, ctx) -> int:
        quote = str(data)
        x0, x1 = _col_box(ctx)
        ix, ir = x0 + 30, x1 - 30
        f = _load_font(22)
        lines = _wrap(quote, f, ir - ix - 18)
        height = 28 + len(lines) * 32 + 24
        _soft_card(img, [x0, y, x1, y + height], _RADIUS, fill=_ACCENT_SOFT, shadow_alpha=22)
        draw = ImageDraw.Draw(img)
        draw.text((x0 + 14, y + 6), "“", font=_load_font(54, bold=True), fill=(198, 213, 248, 255))
        ty = y + 28
        for ln in lines:
            draw.text((ix + 16, ty), ln, font=f, fill=(70, 96, 170, 255))
            ty += 32
        return y + height


# ==================== 角色 / 声优生日 ====================
def _load_birthdays() -> dict:
    if not BIRTHDAYS_FILE.exists():
        return {}
    try:
        data = json.loads(BIRTHDAYS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"【早报·生日】读取 birthdays.json 失败：{e}")
        return {}


class BirthdayModule(MorningModule):
    name = "birthday"
    title = "今日生日"
    default_enabled = True
    cache_ttl = 0

    async def collect(self, ctx: ModuleContext) -> list[dict] | None:
        key = ctx.now.strftime("%m-%d")
        entries = _load_birthdays().get(key) or []
        if not isinstance(entries, list) or not entries:
            return None
        return entries

    def text(self, data, ctx) -> list[str]:
        lines = ["【今日生日】"]
        for e in data:
            tag = e.get("type") or "角色"
            who = e.get("name") or ""
            frm = e.get("from") or ""
            lines.append(f"{tag}：{who}" + (f"（{frm}）" if frm else ""))
        return lines

    def draw(self, img, y, data, ctx) -> int:
        x0, x1 = _col_box(ctx)
        ix, ir = x0 + 22, x1 - 22
        shown = data[:6]
        height = 66 + len(shown) * 30 + 14
        _soft_card(img, [x0, y, x1, y + height], _RADIUS)
        draw = ImageDraw.Draw(img)
        _header(draw, ix, ir, y + 20, _mod_title(ctx, "birthday", "今日生日"), ctx.now.strftime("%m-%d"))
        ty = y + 58
        f = _load_font(19)
        for e in shown:
            tag = e.get("type") or "角色"
            who = e.get("name") or ""
            frm = e.get("from") or ""
            txt = f"· {tag} {who}" + (f"（{frm}）" if frm else "")
            draw.text((ix, ty), _ellipsize(txt, f, ir - ix), font=f, fill=_TEXT_COLOR)
            ty += 30
        return y + height


# ==================== 今日运势 ====================
_FORTUNE_LEVELS = [("大吉", 5), ("中吉", 12), ("小吉", 22), ("吉", 28), ("末吉", 23), ("凶", 10)]
_FORTUNE_WORDS = {
    "大吉": "运势爆棚，适合大胆迈出一步！",
    "中吉": "顺风顺水，稳步推进会有好结果。",
    "小吉": "小确幸接连出现，保持好心情。",
    "吉": "平稳踏实的一天，别急，慢慢来。",
    "末吉": "低调行事，谨言慎行为上。",
    "凶": "今天宜放慢脚步，多关照自己。",
}
# 等级 -> (星级, 主色)
_FORTUNE_STYLE = {
    "大吉": (5, (236, 120, 168, 255)),
    "中吉": (4, (240, 150, 120, 255)),
    "小吉": (3, (232, 176, 92, 255)),
    "吉": (3, (120, 176, 224, 255)),
    "末吉": (2, (156, 156, 210, 255)),
    "凶": (1, (150, 158, 176, 255)),
}
# 等级 -> 综合评分区间，保证评分与评级一致
_FORTUNE_SCORE_RANGE = {
    "大吉": (90, 100), "中吉": (82, 92), "小吉": (74, 84),
    "吉": (66, 78), "末吉": (58, 70), "凶": (46, 58),
}
_FORTUNE_COLORS = ["朱红", "樱粉", "天青", "月白", "藤紫", "鎏金", "翠绿", "橘"]
_FORTUNE_COLOR_RGB = {
    "朱红": (224, 92, 92, 255), "樱粉": (242, 150, 184, 255), "天青": (116, 190, 214, 255),
    "月白": (206, 216, 230, 255), "藤紫": (168, 148, 214, 255), "鎏金": (222, 178, 92, 255),
    "翠绿": (112, 192, 142, 255), "橘": (240, 164, 96, 255),
}
_FORTUNE_ITEMS = ["奶茶", "耳机", "御守", "手办", "猫", "热拿铁", "新番", "游戏手柄", "草莓蛋糕"]
_FORTUNE_GOOD = ["早起", "散步", "读书", "听歌", "整理", "复盘", "冥想", "早睡",
                 "浇花", "写日记", "喝热饮", "换壁纸"]
_FORTUNE_BAD = ["熬夜", "拖延", "久坐", "贪凉", "争执", "暴食", "冲动", "刷屏"]
_FORTUNE_MESSAGES = [
    "今天也请慢慢来，我会一直陪着你的。",
    "清晨的风很温柔，愿你也被温柔以待。",
    "无论发生什么，都记得先照顾好自己呀。",
    "如果累了就歇一会儿，没关系的。",
    "小小的好运会悄悄来找你的，相信我。",
    "愿你今天每一步都走得踏实又安心。",
    "记得好好吃饭，这是我唯一的叮嘱。",
    "把烦恼交给风，把好心情留给自己。",
]
# (分类名称, 中性/偏高/偏低时的简短解读)
_FORTUNE_INDEX_META = [
    ("财运", ("量入为出，小额储蓄会有惊喜。", "有机会捡到小便宜，别太贪心。", "看到心动的先放一放再决定。")),
    ("学业", ("把难点拆开来，会豁然开朗。", "适合复习旧知识，温故知新。", "专注一小段时间，效率更高。")),
    ("事业", ("按计划推进，节奏稳一点。", "适合沟通协作，别一个人硬扛。", "灵感不错，记得记下好点子。")),
    ("社交", ("温柔回应他人，关系会更近。", "适合约老朋友聊聊天。", "安静一点也没关系，做自己就好。")),
    ("健康", ("记得多喝水，早点休息。", "适合出门走走，晒晒太阳。", "别久坐，起来活动一下吧。")),
]


class FortuneModule(MorningModule):
    name = "fortune"
    title = "今日运势"
    default_enabled = True
    cache_ttl = 0
    # 运势卡为宽幅三栏设计，横跨整行展示更舒展
    full_width = True

    async def collect(self, ctx: ModuleContext) -> dict | None:
        # 以「群号 + 日期」为种子确定性生成：同一天重复执行结果一致，跨天自动变化
        rng = random.Random(f"{ctx.gid}-{ctx.date_str}")
        levels = [lv for lv, wgt in _FORTUNE_LEVELS for _ in range(wgt)]
        level = rng.choice(levels)
        stars, accent = _FORTUNE_STYLE[level]
        lo, hi = _FORTUNE_SCORE_RANGE[level]
        score = rng.randint(lo, hi)
        indices = []
        for name, tips in _FORTUNE_INDEX_META:
            s = max(0, min(100, score + rng.randint(-16, 16)))
            indices.append({"name": name, "score": s, "tip": rng.choice(tips)})
        color = rng.choice(_FORTUNE_COLORS)
        return {
            "title": _mod_title(ctx, "fortune", "今日运势"),
            "level": level,
            "stars": stars,
            "accent": accent,
            "score": score,
            "summary": _FORTUNE_WORDS[level],
            "date": ctx.date_str,
            "indices": indices,
            "good": rng.sample(_FORTUNE_GOOD, 4),
            "bad": rng.sample(_FORTUNE_BAD, 4),
            "lucky_color": color,
            "lucky_color_rgb": _FORTUNE_COLOR_RGB.get(color, accent),
            "lucky_number": rng.randint(1, 9),
            "lucky_item": rng.choice(_FORTUNE_ITEMS),
            "message": rng.choice(_FORTUNE_MESSAGES),
        }

    def text(self, data, ctx) -> list[str]:
        idx = " ".join(f"{i['name']}{i['score']}" for i in data["indices"])
        return [
            f"【今日运势】{data['level']} {data['score']}/100 · {data['summary']}",
            f"分类指数：{idx}",
            f"宜：{'、'.join(data['good'][:3])}；忌：{'、'.join(data['bad'][:3])}",
            f"幸运色 {data['lucky_color']} · 幸运数字 {data['lucky_number']} · 幸运物 {data['lucky_item']}",
            f"Yuna：{data['message']}",
        ]

    def draw(self, img, y, data, ctx) -> int:
        if data is None:
            return y
        height = 486
        x0, x1 = _col_box(ctx)
        draw_fortune_card(img, [x0, y, x1, y + height], data)
        return y + height


# ==================== 每日一图 ====================
def daily_image_path(date_str: str) -> Path:
    """每日一图本地缓存路径：按日期固定，供早报插图与单独发送复用。"""
    return CACHE_DIR / f"dayimage_{date_str}.png"


class DailyImageModule(MorningModule):
    name = "daily_image"
    title = "每日一图"
    default_enabled = True
    # 按日期 key 缓存、不设 TTL：同一自然日内固定一张，过 0 点跨天立即刷新
    cache_ttl = -1
    # 大图作为视觉主图，横跨整行展示
    full_width = True

    async def collect(self, ctx: ModuleContext):
        img = await fetch_anime_image()
        if img is not None:
            # 落地到本地 cache 目录，当天可直接复用（含单独发送每日一图）
            try:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                img.save(str(daily_image_path(ctx.date_str)), format="PNG")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"【早报·每日一图】保存本地缓存失败：{e}")
        return img

    def cache_key(self, ctx: ModuleContext) -> str:
        return f"global-{ctx.date_str}"

    def text(self, data, ctx) -> list[str]:
        return ["【每日一图】已附上今日份二次元图片～"]

    def draw(self, img, y, data, ctx) -> int:
        if data is None:
            return y
        x0, x1 = _col_box(ctx)
        ix, ir = x0 + 22, x1 - 22
        iw = ir - ix
        # 仅允许横版：等比缩放到 [iw 宽, 最大高] 内，横向宽度限制最大宽高，不拉伸、不裁切
        src = data.convert("RGBA")
        src_w, src_h = src.size
        if src_w <= 0 or src_h <= 0:
            return y
        scale = min(iw / src_w, 460 / src_h)
        pic_w = max(1, int(src_w * scale))
        pic_h = max(1, int(src_h * scale))
        pic = src.resize((pic_w, pic_h), Image.LANCZOS)
        header_h = 58
        height = header_h + pic_h + 20
        _soft_card(img, [x0, y, x1, y + height], _RADIUS)
        draw = ImageDraw.Draw(img)
        _header(draw, ix, ir, y + 16, "每日一图")
        mask = Image.new("L", (pic_w, pic_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, pic_w - 1, pic_h - 1], radius=14, fill=255)
        img.paste(pic, (ix + (iw - pic_w) // 2, y + header_h), mask)
        return y + height


# ==================== 平台热榜 ====================
_BOARD_NAMES = {
    "bilibili": "B站",
    "acfun": "AcFun",
    "zhihu": "知乎",
    "weibo": "微博",
    "douyin": "抖音",
}


class HotlistModule(MorningModule):
    name = "hotlist"
    title = "ACG 热榜"
    default_enabled = True
    cache_ttl = 1800

    async def collect(self, ctx: ModuleContext) -> list[dict] | None:
        boards = cfg.morning_hotlist_boards or ["bilibili"]
        result = []
        for board in boards:
            items = await fetch_hotlist(board, cfg.morning_hotlist_size)
            if items:
                result.append({"board": board, "name": _BOARD_NAMES.get(board, board), "items": items})
        return result or None

    def cache_key(self, ctx: ModuleContext) -> str:
        return "global"

    def text(self, data, ctx) -> list[str]:
        lines = ["【ACG 热榜】"]
        for b in data:
            lines.append(f"· {b['name']}")
            for i, it in enumerate(b["items"], 1):
                lines.append(f"  {i}. {it['title']}")
        return lines

    def draw(self, img, y, data, ctx) -> int:
        x0, x1 = _col_box(ctx)
        ix, ir = x0 + 22, x1 - 22
        rows = sum(len(b["items"]) for b in data) + len(data)
        height = 60 + rows * 30 + 14
        _soft_card(img, [x0, y, x1, y + height], _RADIUS)
        draw = ImageDraw.Draw(img)
        _header(draw, ix, ir, y + 18, _mod_title(ctx, "hotlist", "ACG 热榜"))
        ty = y + 54
        f_item = _load_font(18)
        f_board = _load_font(19, bold=True)
        for b in data:
            draw.text((ix, ty), f"{b['name']}", font=f_board, fill=_ACCENT)
            ty += 28
            for i, it in enumerate(b["items"], 1):
                txt = f"{i}. {it['title']}"
                draw.text((ix + 10, ty), _ellipsize(txt, f_item, ir - ix - 10), font=f_item, fill=_TEXT_COLOR)
                ty += 30
        return y + height


# ==================== 今日新番（B站时间表 / Bangumi 日历兜底） ====================
def _fmt_count(n) -> str:
    """把数量压缩为 '1.2万' / '3.4千' 之类的简短串，非法值返回空串。"""
    try:
        n = int(n)
    except Exception:  # noqa: BLE001
        return ""
    if n >= 10000:
        return f"{n / 10000:.1f}万"
    if n >= 1000:
        return f"{n / 1000:.1f}千"
    return str(n)


def _anime_suffix(it: dict) -> str:
    """今日新番条目后缀：评分 / 更新集数 / 追番人数，拼成 '  ★9.1 · 第5话 · 追番12.3万'。"""
    parts = []
    score = it.get("score")
    if isinstance(score, (int, float)) and score:
        parts.append(f"★{score:.1f}")
    ep = it.get("episode")
    if ep:
        parts.append(str(ep))
    follows = _fmt_count(it.get("follows"))
    if follows:
        parts.append(f"追番{follows}")
    return ("  " + " · ".join(parts)) if parts else ""


# 番剧「版本后缀」：B站时间表把正片与配音版拆成两条独立条目，标题仅差结尾的版本词
_ANIME_VERSION_SUFFIXES = (
    "中文配音", "国语配音", "台配国语", "中文配音版", "普通话版",
    "中文版", "国语版", "国配版", "中配版", "國語版",
)


def _strip_version_suffix(title: str) -> tuple[str, bool]:
    """剥离标题结尾的配音版本后缀，返回 (主标题, 是否为配音版)。"""
    t = title.strip()
    for suf in _ANIME_VERSION_SUFFIXES:
        if t.endswith(suf) and len(t) > len(suf):
            return t[: -len(suf)].rstrip(" -·—（）()").strip(), True
    return t, False


def _anime_dedup(items: list[dict]) -> list[dict]:
    """按「主标题」合并同一番剧的多版本条目（正片 / 中文配音 等）。

    B站时间表把「正片」与「中文配音」作为两条独立条目返回，标题仅差版本后缀，
    直接展示会出现「同一部番重复两条」。这里归并为一个主标题、只保留一条，
    优先采用非配音版；若某番只有配音版，则保留其主标题。
    """
    reps: dict[str, dict] = {}
    dub_flag: dict[str, bool] = {}
    for it in items:
        title = str(it.get("title") or "").strip()
        base, is_dub = _strip_version_suffix(title)
        key = base or title
        if key not in reps:
            reps[key] = dict(it)
            reps[key]["title"] = base if is_dub else title
            dub_flag[key] = is_dub
        elif dub_flag[key] and not is_dub:
            reps[key]["title"] = title
            dub_flag[key] = False
    return list(reps.values())


class BangumiModule(MorningModule):
    name = "bangumi"
    title = "今日新番"
    default_enabled = True
    cache_ttl = 21600

    async def collect(self, ctx: ModuleContext) -> list[dict] | None:
        # 优先国内源 B站番剧时间表，失败再回退 Bangumi 日历
        # weekday_id：1=周一 … 7=周日
        weekday_id = ctx.now.weekday() + 1
        # 多取一些：合并正片 / 配音版后仍能凑满展示条数
        fetch_n = cfg.morning_bangumi_size * 2
        items = await fetch_bilibili_timeline(weekday_id, fetch_n)
        if not items:
            items = await fetch_bangumi_calendar(weekday_id, fetch_n)
        items = _anime_dedup(items)[: cfg.morning_bangumi_size]
        return items or None

    def text(self, data, ctx) -> list[str]:
        lines = [f"【{_mod_title(ctx, 'bangumi', '今日新番')}】"]
        for it in data:
            lines.append(f"· {it['title']}{_anime_suffix(it)}")
        return lines

    def draw(self, img, y, data, ctx) -> int:
        x0, x1 = _col_box(ctx)
        ix, ir = x0 + 22, x1 - 22
        shown = data[:8]
        height = 62 + len(shown) * 30 + 14
        _soft_card(img, [x0, y, x1, y + height], _RADIUS)
        draw = ImageDraw.Draw(img)
        _header(draw, ix, ir, y + 18, _mod_title(ctx, "bangumi", "今日新番"), ctx.weekday)
        ty = y + 54
        f = _load_font(19)
        for it in shown:
            txt = f"· {it['title']}{_anime_suffix(it)}"
            draw.text((ix, ty), _ellipsize(txt, f, ir - ix), font=f, fill=_TEXT_COLOR)
            ty += 30
        return y + height


# ==================== 动漫资讯（RSS） ====================
def _news_source(url: str) -> str:
    """从资讯链接提取来源域名（去掉常见前缀），失败返回空串。"""
    try:
        host = urlsplit(url or "").netloc.lower()
    except Exception:  # noqa: BLE001
        return ""
    host = host.split(":")[0]
    for pre in ("www.", "m.", "rss."):
        if host.startswith(pre):
            host = host[len(pre):]
            break
    return host


def _news_date(raw: str) -> str:
    """把 RSS / Atom 日期解析为 'MM-DD'，失败返回空串。"""
    s = (raw or "").strip()
    if not s:
        return ""
    try:
        dt = parsedate_to_datetime(s)
        if dt is not None:
            return dt.strftime("%m-%d")
    except Exception:  # noqa: BLE001
        pass
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).strftime("%m-%d")
    except Exception:  # noqa: BLE001
        return ""


class AnimeNewsModule(MorningModule):
    name = "news"
    title = "动漫资讯"
    default_enabled = True
    cache_ttl = 1800

    async def collect(self, ctx: ModuleContext) -> list[dict] | None:
        items = await fetch_news(cfg.morning_news_rss, cfg.morning_news_size)
        return items or None

    def text(self, data, ctx) -> list[str]:
        lines = [f"【{_mod_title(ctx, 'news', '动漫资讯')}】"]
        for i, it in enumerate(data, 1):
            meta = " · ".join(
                x for x in (_news_source(it.get("url", "")), _news_date(it.get("date", ""))) if x
            )
            lines.append(f"{i}. {it['title']}" + (f"（{meta}）" if meta else ""))
        return lines

    def draw(self, img, y, data, ctx) -> int:
        x0, x1 = _col_box(ctx)
        ix, ir = x0 + 22, x1 - 22
        shown = data[:6]
        f_title = _load_font(18)
        f_meta = _load_font(15)
        badge_r = 11
        text_x = ix + badge_r * 2 + 14
        text_w = ir - text_x
        line_h, meta_h, gap = 25, 22, 14

        # 预排版：标题最多 2 行（超出省略），附带「来源 · 日期」元信息
        blocks: list[tuple[list[str], str]] = []
        for it in shown:
            lines = _wrap(str(it.get("title") or ""), f_title, text_w)
            if len(lines) > 2:
                lines = [lines[0], _ellipsize(lines[1] + "…", f_title, text_w)]
            if not lines:
                lines = [""]
            meta = " · ".join(
                x for x in (_news_source(it.get("url", "")), _news_date(it.get("date", ""))) if x
            )
            blocks.append((lines, meta))

        body = sum(len(b[0]) * line_h + (meta_h if b[1] else 0) + gap for b in blocks)
        height = 62 + body + 6
        _soft_card(img, [x0, y, x1, y + height], _RADIUS)
        draw = ImageDraw.Draw(img)
        _header(draw, ix, ir, y + 18, _mod_title(ctx, "news", "动漫资讯"), f"{len(shown)} 条")

        ty = y + 56
        f_badge = _load_font(14, bold=True)
        for i, (lines, meta) in enumerate(blocks, 1):
            cy = ty + 10
            color = _ACCENT if i <= 3 else (176, 184, 205, 255)
            draw.ellipse([ix, cy - badge_r, ix + badge_r * 2, cy + badge_r], fill=color)
            _center(draw, ix + badge_r, cy - 8, str(i), f_badge, (255, 255, 255, 255))
            for ln in lines:
                draw.text((text_x, ty), ln, font=f_title, fill=_TITLE_COLOR)
                ty += line_h
            if meta:
                draw.text(
                    (text_x, ty - 1), _ellipsize(meta, f_meta, text_w), font=f_meta, fill=_SUB_COLOR
                )
                ty += meta_h
            ty += gap
        return y + height


# ==================== 群纪念日 ====================
def _load_anniversaries() -> dict:
    if not ANNIVERSARIES_FILE.exists():
        return {}
    try:
        data = json.loads(ANNIVERSARIES_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"【早报·纪念日】读取 anniversaries.json 失败：{e}")
        return {}


def _md(date_str: str) -> str:
    """提取 'MM-DD'，兼容 'YYYY-MM-DD' 与 'MM-DD' 两种写法，失败返回空串。"""
    s = date_str.strip()
    for fmt in ("%Y-%m-%d", "%m-%d", "%Y/%m/%d", "%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%m-%d")
        except Exception:  # noqa: BLE001
            continue
    return ""


def _years_since(date_str: str, now) -> int:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return 0
    return now.year - d.year


def _event_line(e: dict) -> str:
    """把一条纪念日事件渲染为一行文本，如 '建群1周年纪念日' / '群友生日：小明'。"""
    head = str(e.get("type") or "")
    who = str(e.get("who") or "")
    note = str(e.get("note") or "")
    text = f"{head}：{who}" if who else head
    if note:
        text += f"（{note}）"
    return text


class AnniversaryModule(MorningModule):
    name = "anniversary"
    title = "群纪念日"
    default_enabled = True
    cache_ttl = 0

    async def collect(self, ctx: ModuleContext) -> list[dict] | None:
        info = _load_anniversaries().get(str(ctx.gid))
        if not isinstance(info, dict):
            return None
        today = ctx.now.strftime("%m-%d")
        events: list[dict] = []

        found = str(info.get("found") or "")
        if found and _md(found) == today:
            n = _years_since(found, ctx.now)
            label = f"建群{n}周年纪念日" if n > 0 else "建群纪念日"
            events.append({"type": label, "who": "", "note": ""})

        members = info.get("members") or {}
        if isinstance(members, dict):
            for qq, m in members.items():
                if not isinstance(m, dict):
                    continue
                name = m.get("name") or qq
                bday = str(m.get("birthday") or "")
                if bday and _md(bday) == today:
                    events.append({"type": "群友生日", "who": name, "note": ""})
                join = str(m.get("join") or "")
                if join and _md(join) == today:
                    n = _years_since(join, ctx.now)
                    events.append(
                        {"type": "入群纪念", "who": name, "note": f"{n} 周年" if n > 0 else "今天入群"}
                    )
        return events or None

    def text(self, data, ctx) -> list[str]:
        lines = [f"【{_mod_title(ctx, 'anniversary', '群纪念日')}】"]
        for e in data:
            lines.append(f"· {_event_line(e)}")
        return lines

    def draw(self, img, y, data, ctx) -> int:
        x0, x1 = _col_box(ctx)
        ix, ir = x0 + 22, x1 - 22
        height = 62 + len(data) * 30 + 14
        _soft_card(img, [x0, y, x1, y + height], _RADIUS)
        draw = ImageDraw.Draw(img)
        _header(draw, ix, ir, y + 18, _mod_title(ctx, "anniversary", "群纪念日"), ctx.now.strftime("%m-%d"))
        ty = y + 54
        f = _load_font(19)
        for e in data:
            txt = f"· {_event_line(e)}"
            draw.text((ix, ty), _ellipsize(txt, f, ir - ix), font=f, fill=_TEXT_COLOR)
            ty += 30
        return y + height


# ==================== 手游与企划活动 ====================
def _load_json_list(path: Path) -> list:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:  # noqa: BLE001
        logger.warning(f"【早报】读取 {path.name} 失败：{e}")
        return []


def _to_date(value: str):
    """把 'YYYY-MM-DD' 解析为 date，失败返回 None。"""
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except Exception:  # noqa: BLE001
        return None


class GamesModule(MorningModule):
    name = "games"
    title = "手游活动"
    default_enabled = True
    cache_ttl = 0

    async def collect(self, ctx: ModuleContext) -> list[dict] | None:
        today = ctx.now.date()
        out: list[dict] = []
        # 手游活动暂无稳定的国内公共源，统一使用本地 JSON 维护，
        # 当天没有活动时返回空（由编排层视作「可为空」模块，不影响成品缓存）。
        for e in _load_json_list(GAMES_FILE):
            if not isinstance(e, dict):
                continue
            name = str(e.get("name") or "").strip()
            if not name or _blocked(name):
                continue
            start = _to_date(str(e.get("start") or ""))
            end = _to_date(str(e.get("end") or "")) or start
            if start and start <= today <= end:
                out.append(
                    {
                        "name": name,
                        "game": str(e.get("game") or ""),
                        "note": str(e.get("note") or ""),
                        "url": str(e.get("url") or ""),
                    }
                )
        return out or None

    def text(self, data, ctx) -> list[str]:
        lines = [f"【{_mod_title(ctx, 'games', '手游活动')}】"]
        for e in data:
            head = f"{e['game']}·" if e.get("game") else ""
            tail = f"（{e['note']}）" if e.get("note") else ""
            lines.append(f"· {head}{e['name']}{tail}")
        return lines

    def draw(self, img, y, data, ctx) -> int:
        x0, x1 = _col_box(ctx)
        ix, ir = x0 + 22, x1 - 22
        shown = data[:6]
        height = 62 + len(shown) * 30 + 14
        _soft_card(img, [x0, y, x1, y + height], _RADIUS)
        draw = ImageDraw.Draw(img)
        _header(draw, ix, ir, y + 18, _mod_title(ctx, "games", "手游活动"))
        ty = y + 54
        f = _load_font(19)
        for e in shown:
            head = f"{e['game']}·" if e.get("game") else ""
            tail = f"（{e['note']}）" if e.get("note") else ""
            txt = f"· {head}{e['name']}{tail}"
            draw.text((ix, ty), _ellipsize(txt, f, ir - ix), font=f, fill=_TEXT_COLOR)
            ty += 30
        return y + height


# ==================== 音乐与周边 ====================
class MusicModule(MorningModule):
    name = "music"
    title = "音乐与周边"
    default_enabled = True
    cache_ttl = 0

    async def collect(self, ctx: ModuleContext) -> list[dict] | None:
        today = ctx.now.date()
        recent = cfg.morning_music_recent_days
        floor = today - timedelta(days=recent)
        out: list[dict] = []
        # 优先国内源：网易云音乐新碟
        for it in await fetch_netease_albums(cfg.morning_music_size):
            d = _to_date(str(it.get("date") or ""))
            if not d or not (floor <= d <= today):
                continue
            out.append(
                {
                    "title": it["title"],
                    "artist": it.get("artist") or "",
                    "type": it.get("type") or "",
                    "date": d.strftime("%m-%d"),
                    "url": it.get("url") or "",
                }
            )
        # 兜底一：Bangumi 音乐条目（国内常不可达，仅在网易云无结果时尝试）
        if not out and cfg.morning_bangumi_subject_url:
            for it in await fetch_bangumi_subjects(
                3, today.year, today.month, cfg.morning_music_size
            ):
                d = _to_date(it["date"])
                if not d or not (floor <= d <= today):
                    continue
                out.append(
                    {
                        "title": it["title"],
                        "artist": "",
                        "type": it["platform"],
                        "date": d.strftime("%m-%d"),
                        "url": it["url"],
                    }
                )
        # 兜底二：本地 JSON
        if not out:
            for it in _load_json_list(MUSIC_FILE):
                if not isinstance(it, dict):
                    continue
                name = str(it.get("title") or "").strip()
                if not name or _blocked(name):
                    continue
                d = _to_date(str(it.get("date") or ""))
                if d and floor <= d <= today:
                    out.append(
                        {
                            "title": name,
                            "artist": str(it.get("artist") or ""),
                            "type": str(it.get("type") or ""),
                            "date": d.strftime("%m-%d"),
                            "url": str(it.get("url") or ""),
                        }
                    )
        out.sort(key=lambda x: x["date"], reverse=True)
        return out or None

    def text(self, data, ctx) -> list[str]:
        lines = [f"【{_mod_title(ctx, 'music', '音乐与周边')}】"]
        for it in data:
            tag = f"[{it['type']}]" if it.get("type") else ""
            who = f" - {it['artist']}" if it.get("artist") else ""
            lines.append(f"· {tag}{it['title']}{who}")
        return lines

    def draw(self, img, y, data, ctx) -> int:
        x0, x1 = _col_box(ctx)
        ix, ir = x0 + 22, x1 - 22
        shown = data[:6]
        height = 62 + len(shown) * 30 + 14
        _soft_card(img, [x0, y, x1, y + height], _RADIUS)
        draw = ImageDraw.Draw(img)
        _header(draw, ix, ir, y + 18, _mod_title(ctx, "music", "音乐与周边"))
        ty = y + 54
        f = _load_font(19)
        for it in shown:
            tag = f"[{it['type']}]" if it.get("type") else ""
            who = f" - {it['artist']}" if it.get("artist") else ""
            txt = f"· {tag}{it['title']}{who}"
            draw.text((ix, ty), _ellipsize(txt, f, ir - ix), font=f, fill=_TEXT_COLOR)
            ty += 30
        return y + height


def register_all():
    """注册所有内置模块（幂等）。"""
    for mod in (
        WeatherModule(),
        StatsModule(),
        WordCloudModule(),
        BirthdayModule(),
        FortuneModule(),
        DailyImageModule(),
        HotlistModule(),
        BangumiModule(),
        AnimeNewsModule(),
        AnniversaryModule(),
        GamesModule(),
        MusicModule(),
        QuoteModule(),
    ):
        register(mod)
