import base64
import json
import random
import re
from pathlib import Path

import httpx
from nonebot import get_driver, get_plugin_config, logger, on_notice
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupDecreaseNoticeEvent,
    Message,
    MessageSegment,
)
from nonebot.matcher import Matcher
from nonebot.plugin import PluginMetadata

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="退群提醒",
    description="指定群聊中成员退出时发送退群提醒（含退群者头像）",
    usage="无指令，白名单群聊自动触发",
    type="application",
    config=Config,
    supported_adapters={"onebot.v11"},
)

exit_config = get_plugin_config(Config)
driver = get_driver()

PLUGIN_DIR = Path(__file__).parent
WHITELIST_FILE = PLUGIN_DIR / "whitelist_group.json"
TEXT_DIR = PLUGIN_DIR / "template" / "text"
CHAT_USERS_FILE = PLUGIN_DIR.parent / "chat" / "users.json"

exit_matcher = on_notice(block=False)


# ---------- 白名单 ----------
def _load_config() -> list:
    if not WHITELIST_FILE.exists():
        return []
    try:
        data = json.loads(WHITELIST_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            logger.error("【退群提醒】白名单数据格式错误(非列表)")
            return []
        return data
    except Exception as e:
        logger.error(f"【退群提醒】白名单配置读取异常：{e}")
        return []


def _save_config(data: list):
    WHITELIST_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=4), encoding="utf-8"
    )


def _update_group_config(gid: str, update: dict):
    all_config = _load_config()
    for item in all_config:
        if str(item.get("id_group")) == str(gid):
            item.setdefault("config", {}).update(update)
            _save_config(all_config)
            return


def _find_group_config(gid: str) -> dict | None:
    for item in _load_config():
        if str(item.get("id_group")) == str(gid) and item.get("enabled") is True:
            return item.get("config", {}) or {}
    return None


# ---------- 模板文件 ----------
def _file_index(path: Path):
    """提取文件名数字编号，如 1.txt -> '1'；非纯数字返回 None"""
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
    """替换 {nickname}/{operator}/{qq}/{way} 占位符，值内容不会被二次替换。"""
    return re.sub(
        r"\{(nickname|operator|qq|way)\}",
        lambda m: values.get(m.group(1), m.group(0)),
        template,
    )


# ---------- 昵称 / 头像 ----------
def _get_cached_nickname(qq: int) -> str | None:
    """从 chat 插件缓存读退群者历史昵称，退群后成员已不在群，API 拿不到。"""
    try:
        data = json.loads(CHAT_USERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    info = data.get(str(qq))
    if isinstance(info, dict) and info.get("nickname"):
        return str(info["nickname"])
    return None


async def _get_member_nickname(bot: Bot, group_id: int, user_id: int) -> str | None:
    try:
        info = await bot.get_group_member_info(group_id=group_id, user_id=user_id)
        name = info.get("card") or info.get("nickname")
        return str(name) if name else None
    except Exception:
        return None


async def _get_avatar(qq: int) -> MessageSegment | None:
    """用 QQ 号拼头像地址下载，失败返回 None（不显示）。"""
    url = f"https://q1.qlogo.cn/g?b=qq&nk={qq}&s=640"
    try:
        async with httpx.AsyncClient(timeout=4) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None
            data = resp.content
        if not data:
            return None
        return MessageSegment.image(f"base64://{base64.b64encode(data).decode()}")
    except Exception:
        return None


async def _notify_superusers(bot: Bot, event: GroupDecreaseNoticeEvent, operator: str):
    """机器人自己被踢出群，无法在群里发，改为私聊主人。"""
    group_name = ""
    try:
        info = await bot.get_group_info(group_id=event.group_id)
        group_name = str(info.get("group_name") or "")
    except Exception:
        pass
    text = (
        "【退群提醒】机器人被移出了群聊\n"
        f"群：{group_name or '未知'}\n"
        f"群号：{event.group_id}\n"
        f"操作者：{operator}"
    )
    for su in driver.config.superusers:
        try:
            await bot.send_private_msg(user_id=int(su), message=text)
        except Exception as e:
            logger.warning(f"【退群提醒】私聊主人 {su} 失败：{e}")


@exit_matcher.handle()
async def handle_exit(bot: Bot, event: GroupDecreaseNoticeEvent, matcher: Matcher):
    if not exit_config.plugins_exit_enabled:
        logger.info("【退群提醒】全局开关已关闭，本次退群不触发提示")
        return

    gid = str(event.group_id)
    group_config = _find_group_config(gid)
    if exit_config.whitelist_enabled and group_config is None:
        return
    if group_config is None:
        group_config = {}

    sub_type = event.sub_type
    nickname = _get_cached_nickname(event.user_id) or str(event.user_id)

    operator = ""
    if sub_type == "leave":
        way = "退出了群聊"
    else:
        operator = (
            await _get_member_nickname(bot, event.group_id, event.operator_id)
        ) or str(event.operator_id)
        way = f"被 {operator} 移出了群聊"

    # 机器人自己被踢，无法在群里发消息，私聊主人
    if sub_type == "kick_me":
        await _notify_superusers(bot, event, operator)
        return

    segments: list[MessageSegment] = []
    if exit_config.avatar_enabled:
        avatar = await _get_avatar(event.user_id)
        if avatar is not None:
            segments.append(avatar)

    text = ""
    if group_config.get("template_enabled", True) and group_config.get(
        "text_enabled", True
    ):
        files = _list_files(TEXT_DIR, ["txt"])
        files = _apply_range_exclude(
            files,
            group_config.get("text_range", []),
            group_config.get("text_exclude", []),
        )
        path, ptr = _select_file(
            files,
            group_config.get("text_mode", "random"),
            group_config.get("text_round_pointer"),
        )
        if path:
            content = path.read_text(encoding="utf-8").strip()
            text = _render_text(
                content,
                {
                    "nickname": nickname,
                    "operator": operator,
                    "qq": str(event.user_id),
                    "way": way,
                },
            )
            if ptr is not None:
                _update_group_config(gid, {"text_round_pointer": ptr})

    if not text:
        text = f"{nickname} {way}"

    segments.append(MessageSegment.text(text))
    await matcher.finish(Message(segments))
