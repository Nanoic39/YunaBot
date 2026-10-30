"""群聊数据统计（datadetail）插件。

按群监听消息，以「日」为最小粒度记录发言统计（条数 / 字数 / 活跃人数 /
用户明细 / 小时分布），并持久化到 data/{群号}.json。其他插件（早报、词云、
周报等）通过本模块的查询函数读取或聚合，无需重复采集。
"""

import asyncio
import base64
import io
import json
from datetime import datetime, timedelta
from pathlib import Path

from nonebot import get_plugin_config, logger, on_command, on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, Message, MessageEvent, MessageSegment
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from . import words
from .config import Config
from .wordcloud import render_wordcloud

__plugin_meta__ = PluginMetadata(
    name="群聊数据统计",
    description="按日记录各群发言统计，供其他插件查询聚合（早报/词云/周报）",
    usage="/datadetail [today|昨日|week|month|year|top|日期(YYYY-MM-DD)] 查看统计；/词云 [today|昨日|week|month|year|日期] 生成群聊词云",
    type="application",
    config=Config,
    supported_adapters={"onebot.v11"},
)

cfg = get_plugin_config(Config)

PLUGIN_DIR = Path(__file__).parent
DATA_DIR = PLUGIN_DIR / "data"
RAW_DIR = DATA_DIR / "raw"

_lock = asyncio.Lock()

_stopwords_cache: set[str] | None = None


def _stopwords() -> set[str]:
    """内置停用词 + 配置补充，进程内缓存。"""
    global _stopwords_cache
    if _stopwords_cache is None:
        _stopwords_cache = words.build_stopwords(cfg.datadetail_stopwords)
    return _stopwords_cache


# ---------- 文件读写 ----------
def _data_file(gid) -> Path:
    return DATA_DIR / f"{gid}.json"


def _empty(gid) -> dict:
    return {"gid": str(gid), "daily": {}}


def _load(gid) -> dict:
    path = _data_file(gid)
    if not path.exists():
        return _empty(gid)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty(gid)
    if not isinstance(data, dict):
        return _empty(gid)
    data.setdefault("daily", {})
    return data


def _save(gid, data: dict):
    path = _data_file(gid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _day_str(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _default_day() -> dict:
    return {
        "total": 0,
        "chars": 0,
        "users": {},
        "hours": [0] * 24,
        "words": {},
        # 消息类型分布（文字/图片/表情/@/回复/语音/视频/文件/其他）
        "types": {},
        # 表情（face/mface）使用排行：face_id -> 次数
        "faces": {},
        "first_ts": 0,
        "last_ts": 0,
        # 复读检测所需的跨消息状态
        "last_text": "",
        "last_qq": "",
        "streak": 0,
        "max_streak": 0,
        "repeat_count": 0,
    }


def _ensure_day(d: dict) -> dict:
    """补齐旧数据文件缺失的字段，保证后续读写一致。"""
    for k, v in _default_day().items():
        d.setdefault(k, v)
    return d


def _ensure_user(u: dict, nick: str) -> dict:
    u.setdefault("count", 0)
    u.setdefault("chars", 0)
    u.setdefault("nickname", nick)
    u.setdefault("images", 0)
    u.setdefault("faces", 0)
    u.setdefault("ats", 0)
    u.setdefault("replies", 0)
    u.setdefault("repeats", 0)
    u.setdefault("max_chars", 0)
    u.setdefault("night", 0)
    return u


# ---------- 原文按天分片读写（raw/{群号}/{日期}.json） ----------
def _raw_file(gid, date_str: str) -> Path:
    return RAW_DIR / str(gid) / f"{date_str}.json"


def _raw_load(gid, date_str: str) -> list[str]:
    path = _raw_file(gid, date_str)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def _raw_save(gid, date_str: str, msgs: list[str]):
    path = _raw_file(gid, date_str)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(msgs, ensure_ascii=False), encoding="utf-8")


def _purge_raw(gid, now: datetime):
    """清理超出原文保留期的分片文件。"""
    days = cfg.datadetail_raw_retention_days
    if days <= 0:
        return
    gdir = RAW_DIR / str(gid)
    if not gdir.exists():
        return
    cutoff = _day_str(now - timedelta(days=days))
    for f in gdir.glob("*.json"):
        if f.stem < cutoff:
            try:
                f.unlink()
            except OSError:
                pass


# ---------- 查询接口（供其他插件调用） ----------
def get_daily(gid, date_str: str | None = None) -> dict | None:
    """某天（默认昨天）的统计，无数据返回 None。"""
    key = date_str or _day_str(datetime.now() - timedelta(days=1))
    return _load(gid).get("daily", {}).get(key)


def get_range_summary(gid, start: str, end: str) -> dict:
    """聚合闭区间 [start, end] 内的每日统计，返回条数/字数/时段/类型/复读/用户明细。"""
    agg = {
        "total": 0,
        "chars": 0,
        "active_days": 0,
        "users": {},
        "hours": [0] * 24,
        "types": {},
        "faces": {},
        "first_ts": 0,
        "last_ts": 0,
        "max_streak": 0,
        "repeat_count": 0,
    }
    daily = _load(gid).get("daily", {})
    for key in sorted(daily):
        if start <= key <= end:
            d = daily[key]
            agg["total"] += d.get("total", 0)
            agg["chars"] += d.get("chars", 0)
            agg["active_days"] += 1
            hours = d.get("hours") or []
            for i in range(min(24, len(hours))):
                agg["hours"][i] += hours[i]
            for t, c in (d.get("types") or {}).items():
                agg["types"][t] = agg["types"].get(t, 0) + c
            for fid, c in (d.get("faces") or {}).items():
                agg["faces"][fid] = agg["faces"].get(fid, 0) + c
            agg["max_streak"] = max(agg["max_streak"], d.get("max_streak", 0))
            agg["repeat_count"] += d.get("repeat_count", 0)
            ft = d.get("first_ts", 0)
            if ft and (not agg["first_ts"] or ft < agg["first_ts"]):
                agg["first_ts"] = ft
            lt = d.get("last_ts", 0)
            if lt > agg["last_ts"]:
                agg["last_ts"] = lt
            for qq, u in (d.get("users") or {}).items():
                eu = agg["users"].setdefault(
                    str(qq),
                    {
                        "count": 0,
                        "chars": 0,
                        "nickname": "",
                        "images": 0,
                        "faces": 0,
                        "ats": 0,
                        "replies": 0,
                        "repeats": 0,
                        "max_chars": 0,
                        "night": 0,
                        "active_days": 0,
                    },
                )
                eu["count"] += u.get("count", 0)
                eu["chars"] += u.get("chars", 0)
                eu["images"] += u.get("images", 0)
                eu["faces"] += u.get("faces", 0)
                eu["ats"] += u.get("ats", 0)
                eu["replies"] += u.get("replies", 0)
                eu["repeats"] += u.get("repeats", 0)
                eu["night"] += u.get("night", 0)
                eu["active_days"] += 1
                eu["max_chars"] = max(eu["max_chars"], u.get("max_chars", 0))
                if u.get("nickname"):
                    eu["nickname"] = u["nickname"]
    return agg


def _period_range(period: str, ref_date: str | None = None) -> tuple[str, str]:
    """返回自然周/月/年的闭区间 [start, end]（YYYY-MM-DD）。"""
    base = datetime.strptime(ref_date, "%Y-%m-%d") if ref_date else datetime.now()
    if period == "week":
        start = base - timedelta(days=base.weekday())
        end = start + timedelta(days=6)
    elif period == "month":
        start = base.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    else:
        start = base.replace(month=1, day=1)
        end = base.replace(month=12, day=31)
    return _day_str(start), _day_str(end)


def get_period(gid, period: str, ref_date: str | None = None) -> dict:
    """按自然周/月/年聚合，period 取值 week / month / year。"""
    start, end = _period_range(period, ref_date)
    return get_range_summary(gid, start, end)


def top_users(gid, date_str: str | None = None, top_n: int = 5) -> list[tuple[str, int, int]]:
    """某天（默认昨天）发言 Top N 用户，返回 [(昵称, 条数, 字数)]。"""
    d = get_daily(gid, date_str) or {}
    ranked = sorted(
        (d.get("users") or {}).items(),
        key=lambda kv: kv[1].get("count", 0),
        reverse=True,
    )[:top_n]
    return [
        (u.get("nickname") or qq, u.get("count", 0), u.get("chars", 0))
        for qq, u in ranked
    ]


def _top_words(freq: dict, top_n: int = 0) -> dict[str, int]:
    items = sorted((freq or {}).items(), key=lambda kv: kv[1], reverse=True)
    if top_n > 0:
        items = items[:top_n]
    return dict(items)


def get_words(gid, date_str: str | None = None, top_n: int = 0) -> dict[str, int]:
    """某天（默认昨天）的词频；top_n>0 时只返回前 top_n 个高频词。"""
    d = get_daily(gid, date_str) or {}
    return _top_words(d.get("words") or {}, top_n)


def get_words_range(gid, start: str, end: str, top_n: int = 0) -> dict[str, int]:
    """聚合闭区间 [start, end] 内的词频。"""
    agg: dict[str, int] = {}
    daily = _load(gid).get("daily", {})
    for key in sorted(daily):
        if start <= key <= end:
            for w, c in (daily[key].get("words") or {}).items():
                agg[w] = agg.get(w, 0) + c
    return _top_words(agg, top_n)


def get_words_period(
    gid, period: str, ref_date: str | None = None, top_n: int = 0
) -> dict[str, int]:
    """按自然周/月/年聚合词频，period 取值 week / month / year。"""
    start, end = _period_range(period, ref_date)
    return get_words_range(gid, start, end, top_n)


def get_hours(gid, date_str: str | None = None) -> list[int]:
    """某天（默认昨天）0-23 时的发言条数分布。"""
    d = get_daily(gid, date_str) or {}
    return d.get("hours") or [0] * 24


def get_types(gid, date_str: str | None = None) -> dict[str, int]:
    """某天（默认昨天）的消息类型分布。"""
    d = get_daily(gid, date_str) or {}
    return d.get("types") or {}


def get_faces(gid, date_str: str | None = None, top_n: int = 0) -> dict[str, int]:
    """某天（默认昨天）表情/表情包使用排行（face_id -> 次数）。"""
    d = get_daily(gid, date_str) or {}
    return _top_words(d.get("faces") or {}, top_n)


def get_repeat(gid, date_str: str | None = None) -> dict:
    """某天（默认昨天）的复读统计：最长连击与复读次数。"""
    d = get_daily(gid, date_str) or {}
    return {
        "max_streak": d.get("max_streak", 0),
        "repeat_count": d.get("repeat_count", 0),
    }


# ---------- 消息记录 ----------
msg_matcher = on_message(block=False)


def _analyze_message(msg: Message) -> dict:
    """解析消息段，统计类型分布以及图片/表情/@/回复数量。"""
    out = {
        "types": {},
        "images": 0,
        "faces": 0,
        "ats": 0,
        "replies": 0,
        "face_ids": [],
    }

    def bump(t: str):
        out["types"][t] = out["types"].get(t, 0) + 1

    for seg in msg:
        st = seg.type
        if st == "text":
            if str(seg.data.get("text", "")).strip():
                bump("text")
        elif st == "image":
            bump("image")
            out["images"] += 1
        elif st in ("face", "mface"):
            bump("face")
            out["faces"] += 1
            fid = str(seg.data.get("id") or seg.data.get("emoji_id") or "")
            if fid:
                out["face_ids"].append(fid)
        elif st == "at":
            bump("at")
            out["ats"] += 1
        elif st == "reply":
            bump("reply")
            out["replies"] += 1
        elif st in ("record", "voice"):
            bump("voice")
        elif st == "video":
            bump("video")
        elif st == "file":
            bump("file")
        else:
            bump("other")
    return out


@msg_matcher.handle()
async def _record(event: MessageEvent):
    if not cfg.datadetail_enabled or not isinstance(event, GroupMessageEvent):
        return
    gid = str(event.group_id)
    qq = str(event.user_id)
    nick = ""
    if event.sender:
        nick = event.sender.card or event.sender.nickname or qq
    msg = event.get_message()
    text = msg.extract_plain_text().strip()
    seg = _analyze_message(msg)
    now = datetime.now()
    day = _day_str(now)
    ts = int(now.timestamp())

    async with _lock:
        data = _load(gid)
        d = _ensure_day(data["daily"].setdefault(day, _default_day()))
        d["total"] += 1
        d["chars"] += len(text)
        if 0 <= now.hour < 24:
            d["hours"][now.hour] += 1
        if not d["first_ts"] or ts < d["first_ts"]:
            d["first_ts"] = ts
        if ts > d["last_ts"]:
            d["last_ts"] = ts
        for t, c in seg["types"].items():
            d["types"][t] = d["types"].get(t, 0) + c
        for fid in seg["face_ids"]:
            d["faces"][fid] = d["faces"].get(fid, 0) + 1

        u = _ensure_user(d["users"].setdefault(qq, {}), nick)
        u["count"] += 1
        u["chars"] += len(text)
        u["images"] += seg["images"]
        u["faces"] += seg["faces"]
        u["ats"] += seg["ats"]
        u["replies"] += seg["replies"]
        if len(text) > u["max_chars"]:
            u["max_chars"] = len(text)
        if 0 <= now.hour < 6:
            u["night"] += 1
        if nick:
            u["nickname"] = nick

        # 复读检测：与上一条相同且非空的文本视为一次复读
        prev = d.get("last_text", "")
        if text and prev and text == prev:
            d["streak"] = d.get("streak", 1) + 1
            d["repeat_count"] += 1
            u["repeats"] += 1
            if d["streak"] > d["max_streak"]:
                d["max_streak"] = d["streak"]
        else:
            d["streak"] = 1 if text else 0
        d["last_text"] = text
        d["last_qq"] = qq

        if cfg.datadetail_wordcloud_enabled and text:
            wfreq = d.setdefault("words", {})
            for w in words.tokenize(text, _stopwords()):
                wfreq[w] = wfreq.get(w, 0) + 1

        if cfg.datadetail_retention_days > 0:
            cutoff = _day_str(now - timedelta(days=cfg.datadetail_retention_days))
            for k in [k for k in data["daily"] if k < cutoff]:
                del data["daily"][k]

        _save(gid, data)

        if cfg.datadetail_raw_enabled and text:
            msgs = _raw_load(gid, day)
            msgs.append(text)
            _raw_save(gid, day, msgs)
            _purge_raw(gid, now)


# ---------- 手动查询 ----------
def _top_lines(users: dict, top_n: int = 5) -> list[str]:
    ranked = sorted(
        (users or {}).items(), key=lambda kv: kv[1].get("count", 0), reverse=True
    )[:top_n]
    return [
        f"{(u.get('nickname') or qq)} {u.get('count', 0)}条" for qq, u in ranked
    ]


def _daily_text(gid: str, date_str: str) -> str:
    d = get_daily(gid, date_str)
    if not d:
        return f"{date_str} 暂无统计数据"
    users = d.get("users") or {}
    lines = [
        f"{date_str} 群聊统计",
        f"发言 {d.get('total', 0)} 条 · 活跃 {len(users)} 人 · 总字数 {d.get('chars', 0)}",
    ]
    if top := _top_lines(users):
        lines.append("Top: " + " · ".join(top))
    return "\n".join(lines)


def _period_text(gid: str, period: str) -> str:
    label = {"week": "本周", "month": "本月", "year": "本年"}[period]
    agg = get_period(gid, period)
    users = agg.get("users") or {}
    lines = [
        f"{label}群聊统计",
        f"发言 {agg.get('total', 0)} 条 · 总字数 {agg.get('chars', 0)} · 活跃 {len(users)} 人",
    ]
    if top := _top_lines(users):
        lines.append("Top: " + " · ".join(top))
    return "\n".join(lines)


def _top_text(d: dict, label: str, top_n: int = 5) -> str:
    ranked = sorted(
        (d.get("users") or {}).items(),
        key=lambda kv: kv[1].get("count", 0),
        reverse=True,
    )[:top_n]
    ranked = [(qq, u) for qq, u in ranked if u.get("count", 0) > 0]
    if not ranked:
        return f"{label}暂无统计数据"
    lines = [f"{label}发言 Top:"]
    lines += [
        f"{i}. {(u.get('nickname') or qq)} {u.get('count', 0)}条 / {u.get('chars', 0)}字"
        for i, (qq, u) in enumerate(ranked, 1)
    ]
    return "\n".join(lines)


_TYPE_LABELS = {
    "text": "文字",
    "image": "图片",
    "face": "表情",
    "at": "@",
    "reply": "回复",
    "voice": "语音",
    "video": "视频",
    "file": "文件",
    "other": "其他",
}


def _fmt_ts(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M") if ts else "—"


def _parse_scope(tokens: list[str]) -> tuple[str, str | None, str | None]:
    """解析可选的时间范围参数，返回 (label, 单日日期, 周期)；默认昨日。"""
    default = ("昨日", _day_str(datetime.now() - timedelta(days=1)), None)
    if not tokens:
        return default
    tok = tokens[0].lower()
    if tok in ("today", "今日", "今天"):
        return "今日", _day_str(datetime.now()), None
    if tok in ("yesterday", "昨日", "昨天"):
        return default
    if tok in ("week", "周", "本周"):
        return "本周", None, "week"
    if tok in ("month", "月", "本月"):
        return "本月", None, "month"
    if tok in ("year", "年", "本年"):
        return "本年", None, "year"
    try:
        datetime.strptime(tokens[0], "%Y-%m-%d")
        return tokens[0], tokens[0], None
    except ValueError:
        return default


def _scope_stats(gid, date_str: str | None, period: str | None) -> dict:
    """按范围取统计数据：周期走聚合，否则取单日。"""
    if period:
        return get_period(gid, period)
    return get_daily(gid, date_str) or {}


def _hours_text(d: dict, label: str) -> str:
    hours = d.get("hours") or [0] * 24
    if not any(hours):
        return f"{label}暂无发言时段数据"
    mx = max(hours)
    peak = max(range(24), key=lambda h: hours[h])
    bars = "\n".join(
        f"{h:02d}时 {'▇' * max(1, round(hours[h] / mx * 12))} {hours[h]}"
        for h in range(24)
        if hours[h]
    )
    return (
        f"{label}发言时段\n"
        f"高峰 {peak} 时（{hours[peak]} 条）\n"
        f"最早 {_fmt_ts(d.get('first_ts', 0))} · 最晚 {_fmt_ts(d.get('last_ts', 0))}\n"
        f"{bars}"
    )


def _types_text(d: dict, label: str) -> str:
    types = d.get("types") or {}
    if not types:
        return f"{label}暂无消息类型数据"
    total = sum(types.values()) or 1
    ranked = sorted(types.items(), key=lambda kv: kv[1], reverse=True)
    lines = [f"{label}消息类型（共 {total} 条）"]
    lines += [
        f"{_TYPE_LABELS.get(t, t)} {c} 条 · {c * 100 // total}%" for t, c in ranked
    ]
    return "\n".join(lines)


def _user_text(d: dict, label: str, key: str) -> str:
    users = d.get("users") or {}
    found = (key, users[key]) if key in users else None
    if not found:
        for qq, u in users.items():
            if key == qq or (u.get("nickname") and key in u["nickname"]):
                found = (qq, u)
                break
    if not found:
        return f"{label}没有找到「{key}」的发言记录"
    qq, u = found
    name = u.get("nickname") or qq
    return (
        f"{name}（{qq}）{label}发言\n"
        f"发言 {u.get('count', 0)} 条 · 字数 {u.get('chars', 0)}\n"
        f"图片 {u.get('images', 0)} · 表情 {u.get('faces', 0)} · "
        f"@ {u.get('ats', 0)} · 回复 {u.get('replies', 0)}\n"
        f"最长单条 {u.get('max_chars', 0)} 字 · 深夜 {u.get('night', 0)} 条 · "
        f"复读 {u.get('repeats', 0)} 次"
    )


def _repeat_text(d: dict, label: str) -> str:
    cnt = d.get("repeat_count", 0)
    if not cnt:
        return f"{label}暂无复读记录"
    ranked = sorted(
        (d.get("users") or {}).items(),
        key=lambda kv: kv[1].get("repeats", 0),
        reverse=True,
    )
    king = next(
        ((u.get("nickname") or qq) for qq, u in ranked if u.get("repeats", 0) > 0),
        "—",
    )
    return (
        f"{label}复读统计\n"
        f"复读 {cnt} 次 · 最长连击 {d.get('max_streak', 0)}\n"
        f"复读王：{king}"
    )


def _faces_text(d: dict, label: str, top_n: int = 10) -> str:
    faces = _top_words(d.get("faces") or {}, top_n)
    if not faces:
        return f"{label}暂无表情使用数据"
    lines = [f"{label}表情/表情包 Top{len(faces)}"]
    lines += [f"{i}. 表情{fid} × {c}" for i, (fid, c) in enumerate(faces.items(), 1)]
    return "\n".join(lines)


dd_cmd = on_command("datadetail", aliases={"群统计", "数据统计"}, priority=5, block=True)


@dd_cmd.handle()
async def _handle_cmd(event: MessageEvent, matcher: Matcher, args=CommandArg()):
    if not cfg.datadetail_enabled:
        await matcher.finish("数据统计功能未开启")
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish("该指令仅在群聊中可用")

    gid = str(event.group_id)
    parts = args.extract_plain_text().strip().split()
    cmd = parts[0].lower() if parts else ""

    if cmd in ("", "yesterday", "昨日"):
        await matcher.finish(_daily_text(gid, _day_str(datetime.now() - timedelta(days=1))))
    if cmd in ("today", "今日"):
        await matcher.finish(_daily_text(gid, _day_str(datetime.now())))
    if cmd in ("week", "周"):
        await matcher.finish(_period_text(gid, "week"))
    if cmd in ("month", "月"):
        await matcher.finish(_period_text(gid, "month"))
    if cmd in ("year", "年"):
        await matcher.finish(_period_text(gid, "year"))
    if cmd == "top":
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 5
        rest = parts[2:] if len(parts) > 1 and parts[1].isdigit() else parts[1:]
        label, date_str, period = _parse_scope(rest)
        await matcher.finish(_top_text(_scope_stats(gid, date_str, period), label, n))
    if cmd in ("活跃", "时段", "hours", "hour", "active"):
        label, date_str, period = _parse_scope(parts[1:])
        await matcher.finish(_hours_text(_scope_stats(gid, date_str, period), label))
    if cmd in ("类型", "types", "type"):
        label, date_str, period = _parse_scope(parts[1:])
        await matcher.finish(_types_text(_scope_stats(gid, date_str, period), label))
    if cmd in ("复读", "repeat"):
        label, date_str, period = _parse_scope(parts[1:])
        await matcher.finish(_repeat_text(_scope_stats(gid, date_str, period), label))
    if cmd in ("表情", "face", "faces", "emoji"):
        label, date_str, period = _parse_scope(parts[1:])
        await matcher.finish(_faces_text(_scope_stats(gid, date_str, period), label))
    if cmd in ("用户", "user"):
        if len(parts) < 2:
            await matcher.finish(
                "用法：/群统计 用户 <昵称或QQ> [today|昨日|week|month|year|日期]"
            )
        key = parts[1]
        label, date_str, period = _parse_scope(parts[2:])
        await matcher.finish(_user_text(_scope_stats(gid, date_str, period), label, key))

    try:
        datetime.strptime(parts[0], "%Y-%m-%d")
        await matcher.finish(_daily_text(gid, parts[0]))
    except ValueError:
        await matcher.finish(
            "用法：/群统计 [today|昨日|week|month|year|top [n]|活跃|类型|复读|表情|"
            "用户 <昵称或QQ>|日期(YYYY-MM-DD)]"
        )


# ---------- 词云指令 ----------
wc_cmd = on_command("词云", aliases={"wordcloud"}, priority=5, block=True)


@wc_cmd.handle()
async def _handle_wordcloud(event: MessageEvent, matcher: Matcher, args=CommandArg()):
    if not cfg.datadetail_enabled or not cfg.datadetail_wordcloud_enabled:
        await matcher.finish("词云功能未开启")
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish("该指令仅在群聊中可用")

    gid = str(event.group_id)
    parts = args.extract_plain_text().strip().split()
    cmd = parts[0].lower() if parts else ""
    top_n = cfg.datadetail_wordcloud_max_words

    if cmd in ("", "yesterday", "昨日"):
        label = "昨日"
        freq = get_words(gid, _day_str(datetime.now() - timedelta(days=1)), top_n)
    elif cmd in ("today", "今日"):
        label = "今日"
        freq = get_words(gid, _day_str(datetime.now()), top_n)
    elif cmd in ("week", "周"):
        label = "本周"
        freq = get_words_period(gid, "week", top_n=top_n)
    elif cmd in ("month", "月"):
        label = "本月"
        freq = get_words_period(gid, "month", top_n=top_n)
    elif cmd in ("year", "年"):
        label = "本年"
        freq = get_words_period(gid, "year", top_n=top_n)
    else:
        try:
            datetime.strptime(parts[0], "%Y-%m-%d")
        except ValueError:
            await matcher.finish(
                "用法：/词云 [today|昨日|week|month|year|日期(YYYY-MM-DD)]"
            )
        label = parts[0]
        freq = get_words(gid, parts[0], top_n)

    if not freq:
        await matcher.finish(f"{label}暂无足够聊天内容生成词云")

    img = render_wordcloud(freq, max_words=top_n)
    if img is None:
        hot = " · ".join(f"{w}×{c}" for w, c in list(freq.items())[:20])
        await matcher.finish(f"{label}热词（词云渲染不可用，需安装 wordcloud / 配置中文字体）：\n{hot}")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    await matcher.finish(Message(MessageSegment.image(f"base64://{b64}")))
