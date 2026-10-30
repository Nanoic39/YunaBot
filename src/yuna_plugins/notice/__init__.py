"""操作提示（notice）插件。

监听群内管理类通知事件，并私聊上报给超级管理员与插件管理名单：

- 禁言 / 解禁（GroupBanNoticeEvent）
- 群荣誉称号（HonorNotifyEvent：龙王 / 群聊之火 / 快乐源泉）
- 管理员变动（GroupAdminNoticeEvent）

上报对象：driver.config.superusers + notice_managers（仿 limit 插件的 _manager_ids）。
"""

from nonebot import get_driver, get_plugin_config, logger, on_notice
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupAdminNoticeEvent,
    GroupBanNoticeEvent,
    HonorNotifyEvent,
)
from nonebot.plugin import PluginMetadata

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="操作提示",
    description="监听群内禁言、称号、管理员变动并私聊上报给管理员",
    usage="无需指令，事件触发；上报对象为超管与 notice_managers",
    config=Config,
    supported_adapters=None,
)

cfg = get_plugin_config(Config)
driver = get_driver()

# 荣誉类型 -> 中文名
_HONOR_LABELS = {
    "talkative": "龙王",
    "performer": "群聊之火",
    "emotion": "快乐源泉",
}


def _manager_ids() -> set[str]:
    ids = set()
    try:
        ids |= {str(s) for s in driver.config.superusers}
    except Exception:
        pass
    ids |= set(cfg.notice_managers)
    return {i for i in ids if i}


def _skip_group(gid: str) -> bool:
    return gid in {str(g) for g in cfg.notice_exception_groups}


async def _notify_managers(bot: Bot, gid: str, text: str) -> None:
    for mid in _manager_ids():
        try:
            await bot.send_private_msg(user_id=int(mid), message=f"[群:{gid}] {text}")
        except Exception:
            logger.warning(f"【操作提示】通知管理 {mid} 失败")


async def _member_name(bot: Bot, gid: str, uid: str) -> str:
    try:
        info = await bot.get_group_member_info(group_id=int(gid), user_id=int(uid))
        name = str(info.get("card") or info.get("nickname") or uid)
    except Exception:
        name = uid
    return f"{name}({uid})"


async def _group_name(bot: Bot, gid: str) -> str:
    try:
        info = await bot.call_api("get_group_info", group_id=int(gid))
        return str(info.get("group_name") or gid)
    except Exception:
        return gid


def _fmt_duration(seconds: int) -> str:
    seconds = int(seconds)
    if seconds <= 0:
        return "0 秒"
    parts: list[str] = []
    for unit, size in (("天", 86400), ("小时", 3600), ("分钟", 60), ("秒", 1)):
        if seconds >= size:
            val, seconds = divmod(seconds, size)
            parts.append(f"{val}{unit}")
    return " ".join(parts)


notice_matcher = on_notice(priority=7, block=False)


@notice_matcher.handle()
async def _handle_notice(bot: Bot, event):
    if not cfg.notice_enabled:
        return

    if isinstance(event, GroupBanNoticeEvent):
        gid = str(event.group_id)
        if _skip_group(gid):
            return
        gname = await _group_name(bot, gid)
        if str(event.user_id) == "0":
            target = "全体成员"
        else:
            target = await _member_name(bot, gid, str(event.user_id))
        operator = await _member_name(bot, gid, str(event.operator_id))
        if event.sub_type == "lift_ban":
            text = f"【解除禁言】{operator} 解除了 {target} 的禁言"
        else:
            text = (
                f"【禁言】{operator} 禁言了 {target}，"
                f"时长 {_fmt_duration(event.duration)}"
            )
        await _notify_managers(bot, f"{gname}({gid})", text)
        return

    if isinstance(event, HonorNotifyEvent):
        gid = str(event.group_id)
        if _skip_group(gid):
            return
        gname = await _group_name(bot, gid)
        target = await _member_name(bot, gid, str(event.user_id))
        label = _HONOR_LABELS.get(event.honor_type, event.honor_type)
        await _notify_managers(
            bot, f"{gname}({gid})", f"【称号】{target} 获得了「{label}」"
        )
        return

    if isinstance(event, GroupAdminNoticeEvent):
        gid = str(event.group_id)
        if _skip_group(gid):
            return
        gname = await _group_name(bot, gid)
        target = await _member_name(bot, gid, str(event.user_id))
        action = "被设为本群管理员" if event.sub_type == "set" else "被取消管理员"
        await _notify_managers(
            bot, f"{gname}({gid})", f"【管理员变动】{target} {action}"
        )
        return
