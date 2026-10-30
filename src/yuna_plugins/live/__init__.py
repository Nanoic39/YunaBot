import asyncio
import base64
import json
import random
import re
import time
from datetime import datetime
from pathlib import Path

import httpx
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

from .config import Config

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

__plugin_meta__ = PluginMetadata(
    name="直播推送",
    description="检测 B 站主播开播/下播状态，按群配置推送文本与图片",
    usage="/live list|add|remove|enable|disable|name|toggle",
    type="application",
    config=Config,
    supported_adapters={"onebot.v11"},
)

live_config = get_plugin_config(Config)
driver = get_driver()

PLUGIN_DIR = Path(__file__).parent
STREAMERS_FILE = PLUGIN_DIR / "streamers.json"
STATE_FILE = PLUGIN_DIR / "state.json"
ON_TEXT_DIR = PLUGIN_DIR / "template" / "on" / "text"
ON_IMAGE_DIR = PLUGIN_DIR / "template" / "on" / "image"
OFF_TEXT_DIR = PLUGIN_DIR / "template" / "off" / "text"
OFF_IMAGE_DIR = PLUGIN_DIR / "template" / "off" / "image"

BILI_ROOM_API = "https://api.live.bilibili.com/room/v1/Room/get_info"
BILI_USER_API = "https://api.live.bilibili.com/live_user/v1/Master/info"

# room_id(str) -> {"status": int, "started": float, "live_time": str}
_state: dict[str, dict] = {}
# uid(int) -> 昵称
_nickname_cache: dict[int, str] = {}


# ---------- 默认配置 ----------
def _default_on() -> dict:
    return {
        "text_enabled": True,
        "text_mode": "random",
        "text_range": [],
        "text_exclude": [],
        "text_round_pointer": 0,
        "image_enabled": True,
        "image_mode": "random",
        "image_range": [],
        "image_exclude": [],
        "image_round_pointer": 0,
        "cover_enabled": True,
    }


def _default_off() -> dict:
    return {
        "text_enabled": True,
        "text_mode": "random",
        "text_range": [],
        "text_exclude": [],
        "text_round_pointer": 0,
        "image_enabled": True,
        "image_mode": "random",
        "image_range": [],
        "image_exclude": [],
        "image_round_pointer": 0,
    }


# ---------- 配置读写 ----------
def _load_streamers() -> list:
    if not STREAMERS_FILE.exists():
        return []
    try:
        data = json.loads(STREAMERS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            logger.error("【直播推送】streamers.json 格式错误(非列表)")
            return []
        return data
    except Exception as e:
        logger.error(f"【直播推送】读取 streamers.json 异常：{e}")
        return []


def _save_streamers(data: list):
    STREAMERS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8"
    )


def _find_group(data: list, gid) -> dict | None:
    for item in data:
        if str(item.get("group_id")) == str(gid):
            return item
    return None


def _find_streamer(group: dict, room_id) -> dict | None:
    for s in group.get("streamers", []):
        if str(s.get("room_id")) == str(room_id):
            return s
    return None


def _update_pointer(gid, room_id, kind, field, ptr):
    data = _load_streamers()
    group = _find_group(data, gid)
    if not group:
        return
    s = _find_streamer(group, room_id)
    if not s:
        return
    s.setdefault(kind, {})[field] = ptr
    _save_streamers(data)


# ---------- 状态持久化 ----------
def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_state():
    STATE_FILE.write_text(
        json.dumps(_state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------- 模板文件 ----------
def _file_index(path: Path):
    return path.stem if path.stem.isdigit() else None


def _list_files(directory: Path, suffixes: list) -> list:
    if not directory.exists():
        return []
    files = [
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower().lstrip(".") in suffixes
    ]
    files.sort(key=lambda p: int(_file_index(p) or 0))
    return files


def _apply_range_exclude(files: list, range_list: list, exclude_list: list) -> list:
    range_set = {str(x) for x in (range_list or [])}
    exclude_set = {str(x) for x in (exclude_list or [])}
    result = []
    for f in files:
        idx = _file_index(f)
        if idx is not None and idx in exclude_set:
            continue
        if range_set and (idx is None or idx not in range_set):
            continue
        result.append(f)
    return result


def _select_file(files: list, mode: str, pointer):
    if not files:
        return None, None
    if mode == "round_robin":
        idx = (pointer or 0) % len(files)
        return files[idx], (idx + 1) % len(files)
    return random.choice(files), None


def _render_text(template: str, values: dict) -> str:
    return re.sub(
        r"\{(name|title|area|room_id|url|live_time|duration|description)\}",
        lambda m: str(values.get(m.group(1), m.group(0))),
        template,
    )


def _select_text(tcfg: dict, text_dir: Path, values: dict):
    if not tcfg.get("text_enabled", True):
        return [], None
    files = _list_files(text_dir, ["txt"])
    files = _apply_range_exclude(
        files, tcfg.get("text_range", []), tcfg.get("text_exclude", [])
    )
    path, ptr = _select_file(
        files, tcfg.get("text_mode", "random"), tcfg.get("text_round_pointer")
    )
    if not path:
        return [], None
    text = _render_text(path.read_text(encoding="utf-8").strip(), values)
    segs = [MessageSegment.text(text)] if text else []
    return segs, ptr


def _select_image(tcfg: dict, image_dir: Path):
    if not tcfg.get("image_enabled", True):
        return [], None
    files = _list_files(image_dir, live_config.live_image_suffix)
    files = _apply_range_exclude(
        files, tcfg.get("image_range", []), tcfg.get("image_exclude", [])
    )
    path, ptr = _select_file(
        files, tcfg.get("image_mode", "random"), tcfg.get("image_round_pointer")
    )
    if not path:
        return [], None
    b64 = base64.b64encode(path.read_bytes()).decode()
    return [MessageSegment.image(f"base64://{b64}")], ptr


# ---------- B 站接口 ----------
async def _fetch_room_info(room_id: str) -> dict | None:
    headers = {
        "User-Agent": live_config.live_user_agent,
        "Referer": "https://live.bilibili.com/",
    }
    try:
        async with httpx.AsyncClient(timeout=10, headers=headers) as client:
            resp = await client.get(BILI_ROOM_API, params={"room_id": room_id})
            if resp.status_code != 200:
                return None
            payload = resp.json()
    except Exception as e:
        logger.error(f"【直播推送】获取房间 {room_id} 信息失败：{e}")
        return None
    if payload.get("code") != 0:
        return None
    return payload.get("data") or {}


async def _fetch_uid_name(uid) -> str | None:
    if not uid:
        return None
    uid = int(uid)
    if uid in _nickname_cache:
        return _nickname_cache[uid]
    headers = {
        "User-Agent": live_config.live_user_agent,
        "Referer": "https://live.bilibili.com/",
    }
    try:
        async with httpx.AsyncClient(timeout=10, headers=headers) as client:
            resp = await client.get(BILI_USER_API, params={"uid": uid})
            if resp.status_code != 200:
                return None
            payload = resp.json()
    except Exception:
        return None
    if payload.get("code") != 0:
        return None
    name = (payload.get("data", {}).get("info") or {}).get("uname")
    if name:
        _nickname_cache[uid] = str(name)
        return str(name)
    return None


async def _download_image(url: str) -> MessageSegment | None:
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None
            data = resp.content
        if not data:
            return None
        return MessageSegment.image(f"base64://{base64.b64encode(data).decode()}")
    except Exception:
        return None


# ---------- 值渲染 ----------
async def _build_values(room_id: str, info: dict, scfg: dict, live_time=None) -> dict:
    name = str(scfg.get("name") or "").strip()
    if not name:
        name = (
            str(info.get("name") or "").strip()
            or await _fetch_uid_name(info.get("uid"))
            or str(room_id)
        )
    parent = str(info.get("parent_area_name") or "")
    area = str(info.get("area_name") or "")
    area_full = f"{parent}·{area}" if parent and area else (parent or area)
    ltime = live_time if live_time is not None else str(info.get("live_time") or "")
    return {
        "name": name,
        "title": str(info.get("title") or ""),
        "area": area_full,
        "room_id": str(room_id),
        "url": f"https://live.bilibili.com/{room_id}",
        "live_time": ltime,
        "description": str(info.get("description") or ""),
        "cover_url": info.get("user_cover") or info.get("keyframe"),
    }


def _parse_live_time(raw) -> float | None:
    if raw in (None, "", 0, "0"):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if s.isdigit():
        return float(s)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            continue
    return None


def _format_event_time(raw: str) -> str:
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(str(raw)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(raw)


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}小时{m}分钟"
    if m:
        return f"{m}分钟"
    return f"{s}秒"


# ---------- 轮询与推送 ----------
def _collect_rooms() -> dict[str, list[tuple[int, dict]]]:
    result: dict[str, list[tuple[int, dict]]] = {}
    for group in _load_streamers():
        if not group.get("enabled", True):
            continue
        gid = group.get("group_id")
        for s in group.get("streamers", []):
            if not s.get("enabled", True):
                continue
            rid = str(s.get("room_id"))
            result.setdefault(rid, []).append((gid, s))
    return result


async def _send_group(bot: Bot, gid, segments: list):
    if not gid or not segments:
        return
    try:
        await bot.send_group_msg(group_id=int(gid), message=Message(segments))
    except Exception as e:
        logger.error(f"【直播推送】发送到群 {gid} 失败：{e}")


async def _push_on(bot: Bot, room_id: str, info: dict, targets: list):
    for gid, scfg in targets:
        on = scfg.get("on", {})
        values = await _build_values(room_id, info, scfg)
        text_segs, text_ptr = _select_text(on, ON_TEXT_DIR, values)
        img_segs, img_ptr = _select_image(on, ON_IMAGE_DIR)
        cover_seg = None
        if on.get("cover_enabled", True) and values.get("cover_url"):
            cover_seg = await _download_image(values["cover_url"])
        segments = text_segs + ([cover_seg] if cover_seg else []) + img_segs
        if not segments:
            segments = [MessageSegment.text(f"{values['name']} 开播啦：{values['url']}")]
        await _send_group(bot, gid, segments)
        if text_ptr is not None:
            _update_pointer(gid, room_id, "on", "text_round_pointer", text_ptr)
        if img_ptr is not None:
            _update_pointer(gid, room_id, "on", "image_round_pointer", img_ptr)


async def _push_off(bot: Bot, room_id: str, info: dict, started: float, live_time: str, targets: list):
    for gid, scfg in targets:
        off = scfg.get("off", {})
        values = await _build_values(room_id, info, scfg, live_time=live_time)
        values["duration"] = _format_duration(time.time() - started)
        text_segs, text_ptr = _select_text(off, OFF_TEXT_DIR, values)
        img_segs, img_ptr = _select_image(off, OFF_IMAGE_DIR)
        segments = text_segs + img_segs
        if not segments:
            segments = [MessageSegment.text(f"{values['name']} 下播了")]
        await _send_group(bot, gid, segments)
        if text_ptr is not None:
            _update_pointer(gid, room_id, "off", "text_round_pointer", text_ptr)
        if img_ptr is not None:
            _update_pointer(gid, room_id, "off", "image_round_pointer", img_ptr)


async def _poll_room(bot: Bot, room_id: str, targets: list):
    info = await _fetch_room_info(room_id)
    if info is None:
        return
    status = int(info.get("live_status", 0))
    prev = _state.get(room_id, {}).get("status", -1)
    if status == prev:
        return

    if status == 1 and prev != 1:
        started = _parse_live_time(info.get("live_time")) or time.time()
        live_time = str(info.get("live_time") or "")
        _state[room_id] = {"status": 1, "started": started, "live_time": live_time}
        _save_state()
        await _push_on(bot, room_id, info, targets)
    elif prev == 1 and status != 1:
        entry = _state.get(room_id, {})
        started = entry.get("started") or time.time()
        live_time = entry.get("live_time") or ""
        _state[room_id] = {"status": status, "started": None, "live_time": ""}
        _save_state()
        await _push_off(bot, room_id, info, started, live_time, targets)
    else:
        _state[room_id] = {
            "status": status,
            "started": time.time() if status == 1 else None,
            "live_time": str(info.get("live_time") or ""),
        }
        _save_state()


@driver.on_startup
async def _load_state_on_startup():
    global _state
    _state = _load_state()


@scheduler.scheduled_job("interval", seconds=live_config.live_check_interval_seconds)
async def _check_live():
    if not live_config.live_enabled:
        return
    rooms = _collect_rooms()
    if not rooms:
        return
    try:
        bot = get_bot()
    except Exception as e:
        logger.error(f"【直播推送】获取 bot 失败：{e}")
        return
    for room_id, targets in rooms.items():
        await _poll_room(bot, room_id, targets)


# ---------- 录播姬 Webhook ----------
_seen_webhook_events: set[str] = set()


def _is_duplicate(event_id: str) -> bool:
    if event_id in _seen_webhook_events:
        return True
    _seen_webhook_events.add(event_id)
    if len(_seen_webhook_events) > 10000:
        _seen_webhook_events.clear()
    return False


def _webhook_info(data: dict) -> dict:
    return {
        "uid": None,
        "name": str(data.get("Name") or ""),
        "title": str(data.get("Title") or ""),
        "parent_area_name": str(data.get("AreaNameParent") or ""),
        "area_name": str(data.get("AreaNameChild") or ""),
        "live_time": "",
        "description": "",
        "user_cover": None,
        "keyframe": None,
    }


async def _process_webhook_event(
    room_id: str, event_type: str, info: dict, event_ts: str, targets: list
):
    try:
        bot = get_bot()
    except Exception as e:
        logger.error(f"【直播推送】webhook 获取 bot 失败：{e}")
        return

    if event_type == "StreamStarted":
        started = time.time()
        live_time = _format_event_time(event_ts)
        info["live_time"] = live_time
        _state[room_id] = {"status": 1, "started": started, "live_time": live_time}
        _save_state()
        await _push_on(bot, room_id, info, targets)
    elif event_type == "StreamEnded":
        entry = _state.get(room_id, {})
        started = entry.get("started") or time.time()
        live_time = entry.get("live_time") or ""
        _state[room_id] = {"status": 0, "started": None, "live_time": ""}
        _save_state()
        await _push_off(bot, room_id, info, started, live_time, targets)


if live_config.live_webhook_enabled:
    app = getattr(driver, "server_app", None)
    if app is None:
        logger.warning("【直播推送】当前驱动无 server_app，无法注册录播姬 Webhook 端点")
    else:
        from fastapi import Request
        from fastapi.responses import JSONResponse

        @app.post(live_config.live_webhook_path)
        async def _live_webhook(request: Request):
            try:
                payload = await request.json()
            except Exception:
                return JSONResponse({"code": 1, "msg": "invalid json"}, status_code=400)

            if not isinstance(payload, dict):
                return JSONResponse({"code": 1, "msg": "invalid payload"}, status_code=400)

            event_type = payload.get("EventType")
            event_id = str(payload.get("EventId") or "")
            event_ts = payload.get("EventTimestamp") or ""
            data = payload.get("EventData") or {}

            if event_type not in ("StreamStarted", "StreamEnded"):
                return JSONResponse({"code": 0}, status_code=200)
            if not event_id or _is_duplicate(event_id):
                return JSONResponse({"code": 0}, status_code=200)

            room_id = str(data.get("RoomId") or "")
            targets = _collect_rooms().get(room_id, []) if room_id else []
            if not targets:
                return JSONResponse({"code": 0}, status_code=200)

            info = _webhook_info(data)
            asyncio.create_task(
                _process_webhook_event(room_id, event_type, info, event_ts, targets)
            )
            return JSONResponse({"code": 0}, status_code=200)


# ---------- 群内指令 ----------
async def _is_editor(bot: Bot, event: GroupMessageEvent) -> bool:
    qq = str(event.user_id)
    if qq in {str(s) for s in driver.config.superusers}:
        return True
    try:
        info = await bot.get_group_member_info(
            group_id=event.group_id, user_id=event.user_id
        )
        return str(info.get("role")) == "owner"
    except Exception:
        return False


def _flag(cfg: dict, key: str) -> str:
    return "开" if cfg.get(key, True) else "关"


def _usage() -> str:
    return (
        "直播推送指令：\n"
        "/live list\n"
        "/live add <房间号> [昵称]\n"
        "/live remove <房间号>\n"
        "/live enable <房间号>\n"
        "/live disable <房间号>\n"
        "/live name <房间号> <昵称>\n"
        "/live toggle <房间号> <text|image|cover> <on|off>"
    )


live_cmd = on_command("live", aliases={"直播"}, priority=5, block=True)


@live_cmd.handle()
async def handle_live(
    bot: Bot, event: MessageEvent, matcher: Matcher, args: Message = CommandArg()
):
    if not live_config.live_enabled:
        await matcher.finish("直播推送功能未开启")
    if not isinstance(event, GroupMessageEvent):
        await matcher.finish("该指令仅在群聊中可用")

    if not await _is_editor(bot, event):
        await matcher.finish("你没有权限编辑直播配置（需要 bot 管理员或群主）")

    gid = str(event.group_id)
    data = _load_streamers()
    group = _find_group(data, gid)
    if live_config.live_whitelist_enabled and (
        group is None or not group.get("enabled", True)
    ):
        await matcher.finish("当前群不在直播推送白名单中")
    if group is None:
        group = {"group_id": gid, "enabled": True, "streamers": []}
        data.append(group)
        _save_streamers(data)

    parts = args.extract_plain_text().strip().split()
    if not parts:
        await matcher.finish(_usage())

    cmd = parts[0].lower()

    if cmd == "list":
        streamers = group.get("streamers", [])
        if not streamers:
            await matcher.finish("当前群暂无监控主播")
        lines = []
        for s in streamers:
            rid = s.get("room_id")
            nm = s.get("name") or f"房间 {rid}"
            en = "启用" if s.get("enabled", True) else "停用"
            on = s.get("on", {})
            off = s.get("off", {})
            lines.append(
                f"{nm}（{rid}）[{en}]\n"
                f"  开播：text {_flag(on, 'text_enabled')} / image {_flag(on, 'image_enabled')} / cover {_flag(on, 'cover_enabled')}\n"
                f"  下播：text {_flag(off, 'text_enabled')} / image {_flag(off, 'image_enabled')}"
            )
        await matcher.finish("本群直播监控：\n" + "\n".join(lines))

    if cmd == "add":
        if len(parts) < 2:
            await matcher.finish("用法：/live add <房间号> [昵称]")
        room_id = parts[1]
        if not room_id.isdigit():
            await matcher.finish("房间号必须是数字")
        if _find_streamer(group, room_id):
            await matcher.finish(f"房间 {room_id} 已在监控列表中")
        name = " ".join(parts[2:]).strip()
        group.setdefault("streamers", []).append(
            {
                "room_id": int(room_id),
                "name": name,
                "enabled": True,
                "on": _default_on(),
                "off": _default_off(),
            }
        )
        _save_streamers(data)
        await matcher.finish(f"已添加主播 {room_id}" + (f"（{name}）" if name else ""))

    if cmd == "remove":
        if len(parts) < 2:
            await matcher.finish("用法：/live remove <房间号>")
        room_id = parts[1]
        if not _find_streamer(group, room_id):
            await matcher.finish(f"房间 {room_id} 不在监控列表中")
        group["streamers"] = [
            x
            for x in group.get("streamers", [])
            if str(x.get("room_id")) != str(room_id)
        ]
        _save_streamers(data)
        await matcher.finish(f"已移除房间 {room_id}")

    if cmd in ("enable", "disable"):
        if len(parts) < 2:
            await matcher.finish(f"用法：/live {cmd} <房间号>")
        room_id = parts[1]
        s = _find_streamer(group, room_id)
        if not s:
            await matcher.finish(f"房间 {room_id} 不在监控列表中")
        s["enabled"] = cmd == "enable"
        _save_streamers(data)
        await matcher.finish(f"房间 {room_id} 已{'启用' if cmd == 'enable' else '停用'}")

    if cmd == "name":
        if len(parts) < 2:
            await matcher.finish("用法：/live name <房间号> <昵称>")
        room_id = parts[1]
        s = _find_streamer(group, room_id)
        if not s:
            await matcher.finish(f"房间 {room_id} 不在监控列表中")
        name = " ".join(parts[2:]).strip()
        s["name"] = name
        _save_streamers(data)
        await matcher.finish(
            f"房间 {room_id} 显示名已设为「{name or '空（自动获取）'}」"
        )

    if cmd == "toggle":
        if len(parts) < 4:
            await matcher.finish("用法：/live toggle <房间号> <text|image|cover> <on|off>")
        room_id = parts[1]
        target = parts[2].lower()
        value = parts[3].lower()
        if target not in ("text", "image", "cover"):
            await matcher.finish("可切换项：text / image / cover")
        if value not in ("on", "off"):
            await matcher.finish("开关值只能为 on / off")
        s = _find_streamer(group, room_id)
        if not s:
            await matcher.finish(f"房间 {room_id} 不在监控列表中")
        enabled = value == "on"
        if target == "text":
            s.setdefault("on", {}).setdefault("text_enabled", True)
            s["on"]["text_enabled"] = enabled
            s.setdefault("off", {}).setdefault("text_enabled", True)
            s["off"]["text_enabled"] = enabled
        elif target == "image":
            s.setdefault("on", {}).setdefault("image_enabled", True)
            s["on"]["image_enabled"] = enabled
            s.setdefault("off", {}).setdefault("image_enabled", True)
            s["off"]["image_enabled"] = enabled
        else:
            s.setdefault("on", {}).setdefault("cover_enabled", True)
            s["on"]["cover_enabled"] = enabled
        _save_streamers(data)
        await matcher.finish(f"房间 {room_id} 的 {target} 已{'开启' if enabled else '关闭'}")

    await matcher.finish(_usage())
