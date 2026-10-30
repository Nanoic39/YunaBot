"""模块化二次元早报插件（编排层）。

职责：
- 汇总各内容模块（modules.py 注册）的取数与渲染；
- 按群配置的启用项与顺序采集、缓存、组装成图片 / 文本；
- 群级定时调度（时间 / 时区 / 开关）、启动补发、失败重试、执行日志与历史归档；
- 提供管理员配置命令。

新增板块只需在 modules.py 注册一个 MorningModule，无需改动本文件。
"""

import asyncio
import base64
import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from nonebot import get_bot, get_driver, get_plugin_config, logger, on_command, require
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupMessageEvent,
    Message,
    MessageEvent,
    MessageSegment,
)
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

require("src.yuna_plugins.weather")

from .config import Config
from .module_base import (
    MODULES,
    ModuleContext,
    cache_get,
    cache_set,
    default_modules,
)
from .modules import daily_image_path, register_all
from .render import CACHE_DIR, render_report
from .sources import get_banner_bg

__plugin_meta__ = PluginMetadata(
    name="每日早报",
    description="模块化二次元早报：天气、群聊统计、寄语、生日、运势、每日一图、热榜等模块可插拔组装",
    usage=(
        "/morning（或 早报）再次查看当日早报；/morning 本群配置 查看本群配置\n"
        "/morning on|off|test|mode|weather|stats|text\n"
        "/morning modules|enable|disable|up|down|time|title|history\n"
        "/morning 每日一图（或 日图 / dayimage）获取单独的每日一图"
    ),
    type="application",
    config=Config,
    supported_adapters={"onebot.v11"},
)

cfg = get_plugin_config(Config)
driver = get_driver()

PLUGIN_DIR = Path(__file__).parent
GROUPS_FILE = PLUGIN_DIR / "groups.json"
ARCHIVE_DIR = PLUGIN_DIR / "archive"

_WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
# 允许「当天无内容」的模块：这些模块即便为空（如无新番 / 无活动 / 无生日）也不影响成品早报缓存，
# 只要核心模块（天气、运势、每日一图、热榜、一言等）正常产出即可。国内兜底源成功但当天恰好无数据时同样适用。
_OPTIONAL_EMPTY_MODULES = {
    "stats",
    "wordcloud",
    "birthday",
    "anniversary",
    "bangumi",
    "news",
    "games",
    "music",
}

# 注册全部内置模块（幂等）
register_all()


# ---------- 群配置 ----------
def _load_groups() -> list:
    if not GROUPS_FILE.exists():
        return []
    try:
        data = json.loads(GROUPS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.error(f"【早报】读取 groups.json 失败：{e}")
        return []


def _save_groups(data: list):
    GROUPS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _find_group(gid: str) -> dict | None:
    for item in _load_groups():
        if str(item.get("group_id")) == str(gid):
            return item
    return None


def _default_group(gid: str) -> dict:
    return {
        "group_id": str(gid),
        "enabled": True,
        "display": cfg.morning_default_display,
        "time": cfg.morning_time,
        "timezone": "",
        "title": "早安",
        # 启用的模块顺序；新群采用各模块 default_enabled
        "modules": list(cfg.morning_modules) or default_modules(),
        "module_titles": {},
        "weather": {"city": ""},
        "text": {"enabled": False, "content": ""},
        "last_sent": "",
    }


def _ensure_group(gid: str) -> dict:
    item = _find_group(gid)
    if item is None:
        item = _default_group(gid)
        data = _load_groups()
        data.append(item)
        _save_groups(data)
        return item
    # 兼容旧配置：补齐缺失字段
    changed = False
    for key in ("modules", "module_titles", "last_sent"):
        if key not in item:
            item[key] = _default_group(gid)[key]
            changed = True
    if not item.get("time"):
        item["time"] = cfg.morning_time
        changed = True
    if changed:
        _save_group(gid, item)
    return item


def _save_group(gid: str, gc: dict):
    data = _load_groups()
    for i, item in enumerate(data):
        if str(item.get("group_id")) == str(gid):
            data[i] = gc
            _save_groups(data)
            return
    data.append(gc)
    _save_groups(data)


def _mark_sent(gid: str, date_str: str):
    gc = _find_group(gid)
    if gc is None:
        return
    gc["last_sent"] = date_str
    _save_group(gid, gc)


# ---------- 模块开关 / 排序 ----------
def _ensure_modules(gc: dict) -> list[str]:
    mods = gc.get("modules")
    if not isinstance(mods, list) or not mods:
        mods = default_modules()
        gc["modules"] = mods
    return [n for n in mods if n in MODULES]


def _set_module(gc: dict, name: str, on: bool) -> bool:
    mods = _ensure_modules(gc)
    if on and name not in mods:
        mods.append(name)
    elif not on and name in mods:
        mods.remove(name)
    else:
        return False
    gc["modules"] = mods
    return True


def _move_module(gc: dict, name: str, up: bool) -> bool:
    mods = _ensure_modules(gc)
    if name not in mods:
        return False
    i = mods.index(name)
    j = i - 1 if up else i + 1
    if j < 0 or j >= len(mods):
        return False
    mods[i], mods[j] = mods[j], mods[i]
    gc["modules"] = mods
    return True


# ---------- 时间 / 时区 ----------
def _parse_hhmm(raw: str, default=(7, 0)) -> tuple[int, int]:
    parts = (raw or "").strip().split(":")
    h = int(parts[0]) if parts and parts[0].isdigit() else default[0]
    m = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else default[1]
    return h % 24, m % 60


def _group_time(gc: dict) -> tuple[int, int]:
    return _parse_hhmm(gc.get("time") or cfg.morning_time)


def _tz(gc: dict) -> ZoneInfo:
    name = (gc.get("timezone") or cfg.morning_timezone or "Asia/Shanghai").strip()
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Asia/Shanghai")


# ---------- 采集 / 组装 ----------
async def _collect_one(name: str, ctx: ModuleContext):
    """采集单个模块，返回 (name, data, 耗时)；单模块失败不抛出。"""
    mod = MODULES[name]
    t0 = time.time()
    ck = f"{name}:{mod.cache_key(ctx)}"
    data = None
    if mod.cache_ttl != 0:
        data = cache_get(ck, mod.cache_ttl)
    if data is None:
        try:
            data = await mod.collect(ctx)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"【早报】模块 {name} 采集失败：{e}")
            data = None
        if mod.cache_ttl != 0 and data is not None:
            cache_set(ck, data)
    return name, data, time.time() - t0


async def _collect_modules(gid: str, gc: dict, ctx: ModuleContext) -> list:
    """并发采集各模块数据，返回 [(module, data), ...]（保持配置顺序）；单模块失败自动跳过。"""
    names = _ensure_modules(gc)
    results = await asyncio.gather(*[_collect_one(n, ctx) for n in names])
    collected = []
    logs = []
    for name, data, cost in results:
        if data is None or (isinstance(data, (list, dict)) and not data):
            logs.append(f"{name}:空({cost:.2f}s)")
            continue
        collected.append((MODULES[name], data))
        logs.append(f"{name}:成功({cost:.2f}s)")
    logger.info(f"【早报】群 {gid} 模块采集：{'，'.join(logs) or '无'}")
    return collected


def _compose_signature(gc: dict) -> str:
    """早报配置签名：配置不同则成品缓存互不干扰。"""
    payload = {
        "display": (gc.get("display") or cfg.morning_default_display).strip().lower(),
        "title": (gc.get("title") or "早安").strip() or "早安",
        "modules": _ensure_modules(gc),
        "module_titles": gc.get("module_titles") or {},
        "weather": gc.get("weather") or {},
        "text": gc.get("text") or {},
    }
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _compose_cache_key(gid: str, ctx: ModuleContext, sig: str) -> str:
    """成品早报内存缓存键：同群、同日、同配置共用一份。"""
    return f"report:{gid}:{ctx.date_str}:{sig}"


def _report_cache_paths(gid: str, date_str: str, sig: str) -> tuple[Path, Path]:
    """成品早报本地缓存文件：图片 .png 与文本 .txt 同名成对。"""
    h = hashlib.md5(sig.encode("utf-8")).hexdigest()[:10]
    base = CACHE_DIR / f"report_{gid}_{date_str}_{h}"
    return base.with_suffix(".png"), base.with_suffix(".txt")


def _read_report_cache(gid: str, date_str: str, sig: str, display: str) -> dict | None:
    """读取本地缓存的成品早报（内存缓存丢失后的兜底，如重启后当天复用）。"""
    png, txt = _report_cache_paths(gid, date_str, sig)
    text_body = txt.read_text(encoding="utf-8") if txt.exists() else ""
    if display == "image":
        if not png.exists():
            return None
        payload = base64.b64encode(png.read_bytes()).decode()
    else:
        if not text_body:
            return None
        payload = text_body
    return {"payload": payload, "text_body": text_body}


def _write_report_cache(
    gid: str, date_str: str, sig: str, display: str, payload: str, text_body: str
):
    """把成品早报落地到本地 cache 目录：图片模式存 png，文本单独存 txt。"""
    png, txt = _report_cache_paths(gid, date_str, sig)
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        if display == "image":
            png.write_bytes(base64.b64decode(payload))
        if text_body:
            txt.write_text(text_body, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"【早报】写入本地缓存失败（群 {gid}）：{e}")


def _build_cached_message(display: str, payload: str) -> Message:
    if display == "image":
        return Message(MessageSegment.image(f"base64://{payload}"))
    return Message(MessageSegment.text(payload))


def _can_cache_report(gc: dict, collected: list) -> bool:
    """判断是否可写入同日成品缓存。

    核心模块必须全部产出；已在 _OPTIONAL_EMPTY_MODULES 中的「可为空」模块
    （含国内兜底源成功但当天无数据的情况）不计入必需项，避免因个别模块
    （如 Bangumi 新番）失败导致整份早报无法缓存。
    """
    enabled = _ensure_modules(gc)
    collected_names = {mod.name for mod, _ in collected}
    required = [name for name in enabled if name not in _OPTIONAL_EMPTY_MODULES]
    return all(name in collected_names for name in required)


async def _compose(gid: str, gc: dict, now: datetime | None = None):
    """组装早报，返回 (Message | None, 文本内容)。"""
    now = now or datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    weekday = _WEEKDAYS[now.weekday()]
    ctx = ModuleContext(gid, gc, now, date_str, weekday)
    sig = _compose_signature(gc)
    cache_key = _compose_cache_key(gid, ctx, sig)
    display = (gc.get("display") or cfg.morning_default_display).strip().lower()
    cached = cache_get(cache_key, -1)
    if cached is None:
        # 内存缓存丢失时回退本地缓存（重启后当天仍可秒发）
        cached = _read_report_cache(gid, date_str, sig, display)
        if cached is not None:
            cache_set(cache_key, cached)
    if cached is not None:
        logger.info(f"【早报】群 {gid} 命中同日成品缓存")
        return _build_cached_message(display, cached["payload"]), cached["text_body"]

    collected = await _collect_modules(gid, gc, ctx)
    if not collected:
        return None, ""

    title = (gc.get("title") or "早安").strip() or "早安"
    footer = ""
    if gc.get("text", {}).get("enabled"):
        footer = (gc.get("text", {}).get("content") or "").strip()

    # 文本版本（用于归档与 text 模式）
    lines = [f"{title} · {date_str} {weekday}"]
    for mod, data in collected:
        try:
            lines.extend(mod.text(data, ctx))
        except Exception:  # noqa: BLE001
            continue
    if footer:
        lines.append(footer)
    text_body = "\n".join(lines)

    payload = text_body
    if display == "image":
        banner_bg = await get_banner_bg()
        img = render_report(
            ctx, collected, footer=footer, banner_bg=banner_bg, title=title
        )
        payload = base64.b64encode(img).decode()
        msg = _build_cached_message(display, payload)
    else:
        msg = _build_cached_message(display, payload)
    if _can_cache_report(gc, collected):
        cache_set(cache_key, {"payload": payload, "text_body": text_body})
        _write_report_cache(gid, date_str, sig, display, payload, text_body)
        logger.info(f"【早报】群 {gid} 写入同日成品缓存")
    return msg, text_body


# ---------- 归档 ----------
def _archive(gid: str, date_str: str, text_body: str):
    if not text_body:
        return
    try:
        d = ARCHIVE_DIR / str(gid)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{date_str}.txt").write_text(text_body, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"【早报】归档失败（群 {gid}）：{e}")


# ---------- 推送 ----------
async def _push(gid: str, gc: dict, now: datetime) -> bool:
    try:
        bot = get_bot()
    except Exception as e:  # noqa: BLE001
        logger.error(f"【早报】获取 bot 失败：{e}")
        return False

    msg, text_body = await _compose(gid, gc, now)
    date_str = now.strftime("%Y-%m-%d")
    if msg is None:
        logger.info(f"【早报】群 {gid} 今日无可推送内容，跳过")
        _mark_sent(gid, date_str)
        return False

    total = cfg.morning_send_retries + 1
    for attempt in range(total):
        try:
            await bot.send_group_msg(group_id=int(gid), message=msg)
            _mark_sent(gid, date_str)
            _archive(gid, date_str, text_body)
            logger.info(f"【早报】群 {gid} 推送成功")
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"【早报】群 {gid} 推送失败（{attempt + 1}/{total}）：{e}")
            if attempt < cfg.morning_send_retries:
                await asyncio.sleep(2 * (attempt + 1))
    logger.error(f"【早报】群 {gid} 推送最终失败")
    return False


# ---------- 定时调度 ----------
@scheduler.scheduled_job("cron", minute="*", second=0, timezone=cfg.morning_timezone)
async def _tick():
    """每分钟检查一次，命中群级推送时间且当天未发送时推送。"""
    if not cfg.morning_enabled:
        return
    for gc in _load_groups():
        if not gc.get("enabled", True):
            continue
        gid = str(gc.get("group_id") or "")
        if not gid:
            continue
        now = datetime.now(_tz(gc))
        hh, mm = _group_time(gc)
        if (
            now.hour == hh
            and now.minute == mm
            and gc.get("last_sent") != now.strftime("%Y-%m-%d")
        ):
            await _push(gid, gc, now)


_catchup_done = False


@driver.on_bot_connect
async def _on_bot_connect(bot: Bot):
    """机器人连接后补发当天错过的早报（窗口内）。"""
    global _catchup_done
    if _catchup_done:
        return
    _catchup_done = True
    if not cfg.morning_enabled or cfg.morning_catchup_minutes <= 0:
        return
    for gc in _load_groups():
        if not gc.get("enabled", True):
            continue
        gid = str(gc.get("group_id") or "")
        if not gid:
            continue
        now = datetime.now(_tz(gc))
        date_str = now.strftime("%Y-%m-%d")
        if gc.get("last_sent") == date_str:
            continue
        hh, mm = _group_time(gc)
        scheduled = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        delta = (now - scheduled).total_seconds()
        if 0 <= delta <= cfg.morning_catchup_minutes * 60:
            logger.info(f"【早报】补发群 {gid}（错过 {int(delta // 60)} 分钟）")
            await _push(gid, gc, now)


# ---------- 权限 ----------
async def _is_editor(bot: Bot, event: GroupMessageEvent) -> bool:
    if str(event.user_id) in {str(s) for s in driver.config.superusers}:
        return True
    try:
        info = await bot.get_group_member_info(
            group_id=event.group_id, user_id=event.user_id
        )
        return str(info.get("role")) in ("owner", "admin")
    except Exception:
        return False


def _flag(v) -> str:
    return "开" if v else "关"


async def _daily_image_message(gid: str, gc: dict) -> Message | None:
    """获取当天每日一图，返回可直接发送的图片消息；优先本地缓存。"""
    now = datetime.now(_tz(gc))
    date_str = now.strftime("%Y-%m-%d")
    ctx = ModuleContext(gid, gc or {}, now, date_str, _WEEKDAYS[now.weekday()])
    p = daily_image_path(date_str)
    if not p.exists():
        # 复用模块缓存；首次采集会顺带落地到本地 cache
        _, data, _ = await _collect_one("daily_image", ctx)
        if data is not None and not p.exists():
            try:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                data.save(str(p), format="PNG")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"【早报·每日一图】保存本地缓存失败：{e}")
    if not p.exists():
        return None
    try:
        raw = p.read_bytes()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"【早报·每日一图】读取本地缓存失败：{e}")
        return None
    return Message(MessageSegment.image(f"base64://{base64.b64encode(raw).decode()}"))


morning_cmd = on_command("morning", aliases={"早报"}, priority=5, block=True)


@morning_cmd.handle()
async def _handle_morning(
    bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()
):
    if not cfg.morning_enabled:
        await matcher.finish("早报功能未开启")
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish("该指令仅在群聊中可用")

    text = args.extract_plain_text().strip()
    parts = text.split()
    cmd = parts[0].lower() if parts else ""

    # 获取单独的每日一图：群内成员均可使用，不要求管理权限
    if cmd in ("每日一图", "日图", "dayimage"):
        msg = await _daily_image_message(
            str(event.group_id), _find_group(str(event.group_id)) or {}
        )
        if msg is None:
            await matcher.finish("暂时获取不到今日每日一图，请稍后再试")
        await matcher.finish(msg)

    # 再次查看早报：不带参数时重新组装当日早报并发送，群内成员均可使用
    if not parts:
        gid = str(event.group_id)
        gc = _ensure_group(gid)
        msg, _ = await _compose(gid, gc, datetime.now(_tz(gc)))
        if msg is None:
            await matcher.finish("当前无可推送内容")
        await matcher.finish(msg)

    if not await _is_editor(bot, event):
        await matcher.finish("你没有权限配置早报（需要 bot 管理员或群主/管理员）")

    gid = str(event.group_id)
    gc = _ensure_group(gid)
    cmd = parts[0].lower()

    if cmd in ("本群配置", "配置", "list"):
        mods = _ensure_modules(gc)
        names = " ".join(f"{n}{'' if n in mods else '(关)'}" for n in MODULES)
        lines = [
            f"早报状态：{_flag(gc.get('enabled', True))}",
            f"推送时间：{gc.get('time') or cfg.morning_time}（{gc.get('timezone') or cfg.morning_timezone}）",
            f"展示方式：{gc.get('display') or cfg.morning_default_display}",
            f"标题：{gc.get('title') or '早安'}",
            f"天气城市：{(gc.get('weather') or {}).get('city') or '默认'}",
            f"自定义文本：{_flag((gc.get('text') or {}).get('enabled'))}",
            f"模块（顺序即展示顺序）：{' → '.join(mods) or '无'}",
            f"全部模块：{names}",
        ]
        await matcher.finish("本群早报配置：\n" + "\n".join(lines))

    if cmd == "on":
        gc["enabled"] = True
        _save_group(gid, gc)
        await matcher.finish("已开启本群早报")
    if cmd == "off":
        gc["enabled"] = False
        _save_group(gid, gc)
        await matcher.finish("已关闭本群早报")

    if cmd == "test":
        target = parts[1] if len(parts) > 1 else ""
        if target:
            if target not in MODULES:
                await matcher.finish(f"未知模块：{target}")
            gc_test = dict(gc)
            gc_test["modules"] = [target]
            msg, _ = await _compose(gid, gc_test, datetime.now(_tz(gc)))
        else:
            msg, _ = await _compose(gid, gc, datetime.now(_tz(gc)))
        if msg is None:
            await matcher.finish("当前无可推送内容")
        await matcher.finish(msg)

    if cmd == "mode":
        if len(parts) < 2 or parts[1] not in ("image", "text"):
            await matcher.finish("用法：/morning mode <image|text>")
        gc["display"] = parts[1]
        _save_group(gid, gc)
        await matcher.finish(f"已设置展示方式为 {parts[1]}")

    if cmd == "time":
        if len(parts) < 2:
            await matcher.finish("用法：/morning time <HH:MM>")
        hh, mm = _parse_hhmm(parts[1])
        gc["time"] = f"{hh:02d}:{mm:02d}"
        _save_group(gid, gc)
        await matcher.finish(f"已设置推送时间 {gc['time']}")

    if cmd == "title":
        if len(parts) < 2:
            await matcher.finish("用法：/morning title <标题>")
        gc["title"] = text.split(None, 1)[1].strip()
        _save_group(gid, gc)
        await matcher.finish(f"已设置早报标题：{gc['title']}")

    if cmd in ("modules", "enable", "disable", "up", "down"):
        if cmd == "modules":
            mods = _ensure_modules(gc)
            lines = [f"{i}. {n} · {MODULES[n].title}" for i, n in enumerate(mods, 1)]
            off = [n for n in MODULES if n not in mods]
            body = "已启用：\n" + ("\n".join(lines) or "无")
            if off:
                body += "\n未启用：" + " ".join(off)
            await matcher.finish(body)
        if len(parts) < 2 or parts[1] not in MODULES:
            await matcher.finish("用法：/morning enable|disable|up|down <模块名>")
        name = parts[1]
        if cmd == "enable":
            ok = _set_module(gc, name, True)
            _save_group(gid, gc)
            await matcher.finish(
                "已启用模块 " + name if ok else f"模块 {name} 已是启用状态"
            )
        if cmd == "disable":
            ok = _set_module(gc, name, False)
            _save_group(gid, gc)
            await matcher.finish(
                "已关闭模块 " + name if ok else f"模块 {name} 已是关闭状态"
            )
        if cmd == "up":
            ok = _move_module(gc, name, True)
            _save_group(gid, gc)
            await matcher.finish("已上移 " + name if ok else f"模块 {name} 已无法上移")
        if cmd == "down":
            ok = _move_module(gc, name, False)
            _save_group(gid, gc)
            await matcher.finish("已下移 " + name if ok else f"模块 {name} 已无法下移")

    if cmd == "weather":
        if len(parts) < 2:
            await matcher.finish(
                "用法：/morning weather <城市>，或 /morning weather off 关闭"
            )
        wc = gc.setdefault("weather", {"city": ""})
        if parts[1].lower() == "off":
            wc["enabled"] = False
            _set_module(gc, "weather", False)
            _save_group(gid, gc)
            await matcher.finish("已关闭天气模块")
        else:
            wc["enabled"] = True
            wc["city"] = text.split(None, 1)[1].strip()
            _set_module(gc, "weather", True)
            _save_group(gid, gc)
            await matcher.finish(f"已设置天气城市为 {wc['city']}")

    if cmd == "stats":
        if len(parts) < 2 or parts[1] not in ("on", "off"):
            await matcher.finish("用法：/morning stats <on|off>")
        on = parts[1] == "on"
        _set_module(gc, "stats", on)
        _save_group(gid, gc)
        await matcher.finish(f"昨日统计模块已{_flag(on)}")

    if cmd == "text":
        if len(parts) < 2:
            await matcher.finish(
                "用法：/morning text <内容>，或 /morning text off 关闭"
            )
        tc = gc.setdefault("text", {"enabled": False, "content": ""})
        if parts[1].lower() == "off":
            tc["enabled"] = False
            _save_group(gid, gc)
            await matcher.finish("已关闭自定义文本")
        else:
            tc["enabled"] = True
            tc["content"] = text.split(None, 1)[1].strip()
            _save_group(gid, gc)
            await matcher.finish(f"已设置自定义文本：{tc['content']}")

    if cmd == "history":
        d = ARCHIVE_DIR / gid
        if not d.is_dir():
            await matcher.finish("暂无历史归档")
        if len(parts) > 1:
            fp = d / f"{parts[1]}.txt"
            if not fp.exists():
                await matcher.finish(f"未找到 {parts[1]} 的归档")
            await matcher.finish(fp.read_text(encoding="utf-8"))
        dates = sorted((f.stem for f in d.glob("*.txt")), reverse=True)[:10]
        await matcher.finish(
            "最近归档：\n"
            + "\n".join(dates)
            + "\n发送 /morning history <日期> 查看内容"
        )

    await matcher.finish("未知指令，发送 /morning 本群配置 查看本群配置")
