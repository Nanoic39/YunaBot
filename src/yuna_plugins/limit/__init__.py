"""发言限制（limit）插件。

按群号独立配置（data/<群号>.json），实现对刷屏的分级自动处罚：

处罚规则（rules，按 消息数→重复比例 从重到轻自动匹配最严那条）：
- min_messages     窗口内该用户消息条数下限
- min_repeat_ratio 窗口内重复内容比例下限（0~1）
- action           warn（提醒）/ mute（禁言）/ kick（踢出）
- 各通知/处罚模板见具体 JSON 配置

配套能力：
- 触发禁言/踢出时，同时向 bot 管理（群号、群名、处罚人、原因、结果、实际数据）
  与当事人（原因、结果、实际数据、申诉提醒）发送通知。
- 被踢出的用户再次申请入群后，自动按「72小时 - 距处罚开始时间」补执行剩余禁言。
- 被处罚用户可 /申诉，申诉内容转发给 bot 管理；60 分钟内同一用户最多申诉 2 次。
"""

import json
import random
import re
import time
from pathlib import Path

from nonebot import (
    get_driver,
    get_plugin_config,
    logger,
    on_command,
    on_message,
    on_notice,
)
from nonebot.adapters.onebot.v11 import (
    Bot,
    GroupDecreaseNoticeEvent,
    GroupIncreaseNoticeEvent,
    GroupMessageEvent,
    Message,
    MessageEvent,
    PrivateMessageEvent,
)
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from .config import ACTION_LABELS, Config

__plugin_meta__ = PluginMetadata(
    name="发言限制",
    description="按群独立配置刷屏处罚规则，支持提醒/禁言/踢出与二次入群补禁言、申诉",
    usage=(
        "群管理 / 超管发送：\n"
        "/限流         查看当前群规则与开关\n"
        "/限流开关     切换当前群是否启用\n"
        "被处罚用户：/申诉 [群号] 原因\n"
        "精细规则：编辑插件 data/<群号>.json"
    ),
    config=Config,
    supported_adapters=None,
)

cfg = get_plugin_config(Config)
driver = get_driver()

PLUGIN_DIR = Path(__file__).resolve().parent
DATA_DIR = PLUGIN_DIR / cfg.limit_data_dir
KICKED_FILE = PLUGIN_DIR / cfg.limit_kicked_file
APPEALS_FILE = PLUGIN_DIR / cfg.limit_appeals_file
APPEAL_PENDING_FILE = PLUGIN_DIR / cfg.limit_appeal_pending_file

_IMAGE_PLACEHOLDER_RE = re.compile(r"\[[^\]]+\]")


def _now() -> float:
    return time.time()


def _is_superuser(qq) -> bool:
    try:
        return str(qq) in {str(s) for s in driver.config.superusers}
    except Exception:
        return False


# ---------------- 群配置 ----------------
def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"【发言限制】读取 {path.name} 失败：{e}")
        return default


def _save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _group_rules(gid: str) -> list:
    """返回该群启用的处罚规则列表（已按 消息数→重复比例 从重到轻排序）。"""
    file = DATA_DIR / f"{gid}.json"
    data = _load_json(file, {})
    if not isinstance(data, dict) or not data.get("enabled", True):
        return []
    rules = data.get("rules") or []
    rules = [r for r in rules if isinstance(r, dict) and r.get("action")]
    rules.sort(
        key=lambda r: (
            int(r.get("min_messages", 0)),
            float(r.get("min_repeat_ratio", 0)),
        ),
        reverse=True,
    )
    return rules


def _group_enabled(gid: str) -> bool:
    file = DATA_DIR / f"{gid}.json"
    data = _load_json(file, {})
    if not isinstance(data, dict):
        return False
    return data.get("enabled", True)


def _group_template_mode(gid: str) -> str:
    file = DATA_DIR / f"{gid}.json"
    data = _load_json(file, {})
    mode = data.get("template_mode") if isinstance(data, dict) else None
    if mode not in ("random", "sequence"):
        mode = cfg.limit_template_mode
    if mode not in ("random", "sequence"):
        mode = "random"
    return mode


# ---------------- 模板（独立文件） ----------------
# 模板目录：data/<limit_template_dir>/<表单槽位>/<变体>.txt
# 槽位内存在多个变体时，按模板选择模式（random 默认 / sequence）选取其一。
TEMPLATE_ROOT = DATA_DIR / cfg.limit_template_dir
_template_cache: dict[str, list[str]] = {}
_seq_counter: dict[str, int] = {}


def _load_template_slot(slot: str) -> list[str]:
    """返回某槽位的全部模板文本（懒加载并缓存），无则返回 []。"""
    if slot in _template_cache:
        return _template_cache[slot]
    files = sorted((TEMPLATE_ROOT / slot).glob("*.txt"))
    texts = []
    for f in files:
        try:
            texts.append(f.read_text(encoding="utf-8").rstrip("\n"))
        except Exception as e:
            logger.warning(f"【发言限制】读取模板 {f} 失败：{e}")
    _template_cache[slot] = texts
    return texts


def _pick_template(gid: str, slot: str) -> str | None:
    """按模式从槽位选取一条模板文本；槽位为空时返回 None（走内置默认）。"""
    texts = _load_template_slot(slot)
    if not texts:
        return None
    mode = _group_template_mode(gid)
    if mode == "sequence":
        n = _seq_counter.get(slot, 0)
        _seq_counter[slot] = n + 1
        return texts[n % len(texts)]
    return random.choice(texts)


def _rule_template(
    gid: str, rule: dict, field: str, default_slot: str, fallback: str, ctx: dict
) -> str:
    """渲染规则模板字段：取配置槽位（或默认槽位），命中则渲染，否则用内置 fallback。"""
    slot = rule.get(field) or default_slot
    text = _pick_template(gid, slot)
    if text is None:
        text = fallback
    return _render(text, ctx)


# ---------------- 运行态：消息累积 ----------------
# _user_msgs[(gid, uid)] -> list[(ts, normalized)]
_user_msgs: dict[tuple[str, str], list[tuple[float, str]]] = {}
_MAX_WINDOW = 300  # 最多保留的秒数，防止内存膨胀
_last_action: dict[tuple[str, str], float] = {}


def _normalize(text: str) -> str:
    text = _IMAGE_PLACEHOLDER_RE.sub(" ", text)
    return re.sub(r"\s+", "", text).lower()


def _prune_by(msgs, window, now):
    cutoff = now - window
    return [(t, n) for t, n in msgs if t > cutoff]


def _stats(msgs: list) -> tuple[int, int, float]:
    """返回 (总条数, 去重条数, 重复比例 0~1)。重复比例=(总-去重)/总。"""
    total = len(msgs)
    if total <= 1:
        return total, total, 0.0
    unique = len({n for _, n in msgs})
    ratio = (total - unique) / total
    return total, unique, ratio


def _ratio_hit(unique: int, ratio: float, min_ratio: float) -> bool:
    """判定重复比例是否达到阈值。min_ratio>=1 表示“全部相同”（unique==1），
    因为 (总-去重)/总 恒 <1，无法用比例直接表达 100% 重复。"""
    if min_ratio >= 1.0:
        return unique <= 1
    return ratio >= min_ratio


# ---------------- 被踢出 / 申诉 持久化 ----------------
def _load_kicked() -> dict:
    data = _load_json(KICKED_FILE, {})
    return data if isinstance(data, dict) else {}


def _save_kicked(data: dict):
    _save_json(KICKED_FILE, data)


def _load_appeals() -> dict:
    data = _load_json(APPEALS_FILE, {})
    return data if isinstance(data, dict) else {}


def _save_appeals(data: dict):
    _save_json(APPEALS_FILE, data)


def _load_pending() -> dict:
    data = _load_json(APPEAL_PENDING_FILE, {})
    return data if isinstance(data, dict) else {}


def _save_pending(data: dict):
    _save_json(APPEAL_PENDING_FILE, data)


# ---------------- 工具/通知 ----------------
async def _member_name(bot: Bot, gid: str, uid: str, cache=None) -> str:
    if cache is not None:
        hit = cache.get((gid, uid))
        if hit:
            return hit
    name = uid
    try:
        info = await bot.get_group_member_info(group_id=int(gid), user_id=int(uid))
        name = str(info.get("card") or info.get("nickname") or uid)
    except Exception:
        pass
    if cache is not None:
        cache[(gid, uid)] = name
    return name


async def _group_name(bot: Bot, gid: str) -> str:
    try:
        info = await bot.call_api("get_group_info", group_id=int(gid))
        return str(info.get("group_name") or gid)
    except Exception:
        return gid


async def _role_of(bot: Bot, gid: str, uid: str) -> str:
    try:
        info = await bot.get_group_member_info(group_id=int(gid), user_id=int(uid))
        return str(info.get("role") or "")
    except Exception:
        return ""


def _render(template: str, ctx: dict) -> str:
    try:
        return template.format(**ctx)
    except Exception:
        return template


async def _notify_user(bot: Bot, gid: str, uid: str, text: str) -> None:
    """优先私聊，失败则归回在群里 @ 当事人。"""
    try:
        await bot.send_private_msg(user_id=int(uid), message=text)
        return
    except Exception:
        pass
    try:
        from nonebot.adapters.onebot.v11 import MessageSegment as MS

        await bot.send_group_msg(
            group_id=int(gid), message=MS.at(int(uid)) + " " + text
        )
    except Exception:
        pass


def _manager_ids() -> set[str]:
    ids = set()
    try:
        ids |= {str(s) for s in driver.config.superusers}
    except Exception:
        pass
    ids |= set(cfg.limit_managers)
    return {i for i in ids if i}


async def _notify_managers(bot: Bot, gid: str, text: str) -> None:
    for mid in _manager_ids():
        try:
            await bot.send_private_msg(user_id=int(mid), message=f"[群:{gid}] {text}")
        except Exception:
            logger.warning(f"【发言限制】通知管理 {mid} 失败")


async def _notify_appeal(bot: Bot, text: str) -> dict[str, dict[str, str]]:
    """发送申诉通知给各管理，返回 {管理QQ: {message_id, real_id}}，用于「回复本消息」处理。"""
    sent: dict[str, dict[str, str]] = {}
    for mid in _manager_ids():
        try:
            resp = await bot.send_private_msg(user_id=int(mid), message=text)
            msg_id = resp.get("message_id") if isinstance(resp, dict) else None
            logger.info(f"【发言限制】send_private_msg 返回: {resp!r}")
            if not msg_id:
                continue
            ids = {"message_id": str(msg_id)}
            # NapCat 下 [CQ:reply,id=xxx] 引用的可能是 real_id 而非 message_id，反查一次拿到 real_id
            try:
                info = await bot.get_msg(message_id=int(msg_id))
                logger.info(f"【发言限制】get_msg 返回: {info!r}")
                if isinstance(info, dict) and info.get("real_id") is not None:
                    ids["real_id"] = str(info["real_id"])
            except Exception as e:
                logger.warning(f"【发言限制】反查 real_id 失败: {e}")
            sent[mid] = ids
        except Exception:
            logger.warning(f"【发言限制】通知管理 {mid} 失败")
    return sent


# ---------------- 处罚执行 ----------------
async def _execute(bot: Bot, event: GroupMessageEvent, rule: dict, ctx: dict) -> None:
    action = rule.get("action")
    gid = str(event.group_id)
    uid = str(event.user_id)

    if action == "warn":
        text = _rule_template(
            gid,
            rule,
            "warn_template",
            "warn_self",
            "{nickname} 刷屏提醒：{data}",
            ctx,
        )
        await bot.send(event, text)
        if _as_bool(rule.get("notify_self"), True):
            await _notify_user(bot, gid, uid, text)
        if _as_bool(rule.get("notify_manager"), False):
            mgr = _rule_template(
                gid,
                rule,
                "manager_template",
                "manager",
                "群：{group_name}({group_id})\n处罚人：{nickname}({qq})\n"
                "处罚原因：{reason}\n处罚结果：{result}\n实际数据：{data}",
                ctx,
            )
            await _notify_managers(bot, gid, mgr)
        return

    # 处罚前检查是否跳过管理员
    if cfg.limit_skip_admin and action in ("mute", "kick"):
        role = await _role_of(bot, gid, uid)
        if role in ("owner", "admin"):
            logger.info(
                f"【发言限制】群{gid} {uid} 为{role}，跳过{ACTION_LABELS.get(action)}"
            )
            return

    result = ""
    if action == "mute":
        duration = int(ctx.get("duration_sec", 0))
        if duration > 0:
            try:
                await bot.set_group_ban(
                    group_id=int(gid), user_id=int(uid), duration=duration
                )
                result = f"禁言 {_fmt_duration(duration)}"
                logger.info(f"【发言限制】群{gid} 禁言 {uid} {duration}s")
            except Exception as e:
                logger.warning(f"【发言限制】禁言失败：{e}")
                # 无管理员权限等导致禁言失败时，提醒管理确认权限或关闭处罚
                await _notify_managers(
                    bot,
                    gid,
                    f"禁言 {uid} 失败：{e}\n"
                    f"请确认机器人具备该群管理员权限（可下群主/管理员）；"
                    f"若无法提供，请关闭禁言/踢出规则或 /限流开关 关闭本功能。",
                )
                return
    elif action == "kick":
        try:
            await bot.set_group_kick(
                group_id=int(gid), user_id=int(uid), reject_add_request=False
            )
            result = "移出群聊"
            # 记录被踢时间，用于二次入群补禁言
            kicked = _load_kicked()
            kicked.setdefault(gid, {})[uid] = _now()
            _save_kicked(kicked)
            logger.info(f"【发言限制】群{gid} 踢出 {uid}")
        except Exception as e:
            logger.warning(f"【发言限制】踢出失败：{e}")
            await _notify_managers(
                bot,
                gid,
                f"踢出 {uid} 失败：{e}\n"
                f"请确认机器人具备该群管理员权限；若无法提供，请关闭踢出规则。",
            )
            return

    # 群内处罚通知（让群友引以为戒）
    if action == "kick":
        notice = _rule_template(
            gid,
            rule,
            "kick_notice_template",
            "kick_notice",
            "{nickname}({qq}) 因刷屏被移出群聊。\n处罚原因：{reason}\n实际数据：{data}",
            ctx,
        )
        if notice:
            await bot.send(event, notice)
    elif action == "mute":
        notice = _rule_template(
            gid,
            rule,
            "mute_notice_template",
            "mute_notice",
            "{nickname}({qq}) 因刷屏被{result}。\n处罚原因：{reason}\n实际数据：{data}",
            ctx,
        )
        if notice:
            await bot.send(event, notice)

    # 通知当事人（含申诉提醒）
    if _as_bool(rule.get("notify_self"), True):
        self_text = _rule_template(
            gid,
            rule,
            "punish_self_template",
            "punish_self",
            "{nickname}，你因{reason}被{result}。可发送 /申诉 进行申诉。",
            ctx,
        )
        await _notify_user(bot, gid, uid, self_text)

    # 通知管理
    if _as_bool(rule.get("notify_manager"), True):
        mgr_text = _rule_template(
            gid,
            rule,
            "manager_template",
            "manager",
            "群：{group_name}({group_id})\n处罚人：{nickname}({qq})\n"
            "处罚原因：{reason}\n处罚结果：{result}\n实际数据：{data}",
            ctx,
        )
        await _notify_managers(bot, gid, mgr_text)


def _as_bool(v, default=False) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    return str(v).lower() in ("1", "true", "yes", "on")


def _fmt_duration(sec: int) -> str:
    """将秒数格式化为人性化时长：如 1800 -> 「30分钟」，86400 -> 「24小时」。"""
    sec = int(sec or 0)
    if sec <= 0:
        return ""
    days, rem = divmod(sec, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}天")
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分钟")
    if seconds and not parts:
        parts.append(f"{seconds}秒")
    return "".join(parts)


# ---------------- 主检测 ----------------
def _build_reason(total: int, unique: int, ratio: float, window: int) -> str:
    return (
        f"{window}秒内发言{total}条（其中不同内容{unique}条），"
        f"重复内容约 {round(ratio * 100)}%"
    )


async def _check(bot: Bot, event: GroupMessageEvent) -> None:
    gid = str(event.group_id)
    uid = str(event.user_id)
    if _is_superuser(uid):
        return
    if not cfg.limit_enabled:
        return
    if gid in set(cfg.limit_exception_groups):
        return
    rules = _group_rules(gid)
    if not rules:
        return

    now = _now()
    text = _normalize(str(event.message or ""))
    msgs = _user_msgs.get((gid, uid), [])
    msgs = _prune_by(msgs, _MAX_WINDOW, now)
    msgs.append((now, text))
    _user_msgs[(gid, uid)] = msgs

    matched_rule = None
    for rule in rules:
        window = int(rule.get("window_sec", 60))
        total, unique, ratio = _stats(_prune_by(msgs, window, now))
        if total < int(rule.get("min_messages", 0)):
            continue
        if not _ratio_hit(unique, ratio, float(rule.get("min_repeat_ratio", 0))):
            continue
        matched_rule = rule
        break

    if not matched_rule:
        return

    # 动作冷却
    key = (gid, uid)
    last = _last_action.get(key)
    if last and now - last < cfg.limit_action_cooldown:
        return
    _last_action[key] = now

    window = int(matched_rule.get("window_sec", 60))
    total, unique, ratio = _stats(_prune_by(msgs, window, now))
    duration_sec = int(matched_rule.get("duration_sec", 0))
    action = matched_rule.get("action")
    result = {
        "kick": "移出群聊",
        "mute": f"禁言 {_fmt_duration(duration_sec)}",
        "warn": "提醒警告",
    }.get(action, action)

    reason = _build_reason(total, unique, ratio, window)
    ctx = {
        "nickname": await _member_name(bot, gid, uid),
        "qq": uid,
        "group_id": gid,
        "group_name": await _group_name(bot, gid),
        "reason": reason,
        "result": result,
        "data": f"共计 {total} 条消息，其中不同内容 {unique} 条，重复约 {round(ratio*100)}%",
        "total": total,
        "unique": unique,
        "ratio": f"{round(ratio*100)}%",
        "window": window,
        "duration_sec": duration_sec,
        "duration_h": _fmt_duration(duration_sec),
        "messages": total,
    }
    try:
        await _execute(bot, event, matched_rule, ctx)
    except Exception as e:
        logger.warning(f"【发言限制】执行处罚失败：{e}")


# 消息监听：不 block，避免影响其他插件
_limit_matcher = on_message(priority=10, block=False)


@_limit_matcher.handle()
async def _handle_msg(bot: Bot, event: GroupMessageEvent):
    await _check(bot, event)


# ---------------- 二次入群补禁言 & 被踢记录 ----------------
@on_notice(priority=7, block=False).handle()
async def _handle_notice(bot: Bot, event):
    if isinstance(event, GroupDecreaseNoticeEvent):
        if event.subtype == "kick" and not _is_superuser(event.user_id):
            gid = str(event.group_id)
            uid = str(event.user_id)
            kicked = _load_kicked()
            kicked.setdefault(gid, {})[uid] = _now()
            _save_kicked(kicked)
        return
    if isinstance(event, GroupIncreaseNoticeEvent):
        gid = str(event.group_id)
        uid = str(event.user_id)
        kicked = _load_kicked()
        rec = (kicked.get(gid) or {}).get(uid)
        if not rec:
            return
        # 从处罚开始时间补剩余禁言
        total = cfg.limit_rejoin_mute_hours * 3600
        remaining = int(total - (_now() - float(rec)))
        # 移除记录（一次性补禁言）
        kicked.get(gid, {}).pop(uid, None)
        if not kicked.get(gid):
            kicked.pop(gid, None)
        _save_kicked(kicked)
        if remaining > 0:
            try:
                await bot.set_group_ban(
                    group_id=int(gid), user_id=int(uid), duration=remaining
                )
                logger.info(
                    f"【发言限制】{uid} 二次入群{event.group_id}，补禁言 {remaining}s"
                )
                await _notify_managers(
                    bot,
                    gid,
                    f"{uid} 二次入群，已按处罚记录补禁言 {remaining/3600:g} 小时。",
                )
            except Exception as e:
                logger.warning(f"【发言限制】二次入群补禁言失败：{e}")
        return


# ---------------- 管理命令 ----------------
def _render_help(gid: str, rules: list) -> str:
    head = f"【发言限制】群 {gid}：{'已启用' if _group_enabled(gid) else '已关闭'}，规则 {len(rules)} 条"
    lines = [
        head,
        "处罚动作：" + "、".join(f"{k}：{v}" for k, v in ACTION_LABELS.items()),
        "",
    ]
    if rules:
        for r in rules:
            w = r.get("window_sec", 60)
            lines.append(
                f"· {w}秒内≥{r.get('min_messages')}条且重复≥{round(float(r.get('min_repeat_ratio',0))*100)}%"
                f" → {ACTION_LABELS.get(r.get('action'), r.get('action'))}"
                + (
                    f"（禁言 {_fmt_duration(r.get('duration_sec',0))}）"
                    if r.get("action") == "mute"
                    else ""
                )
                + ("（踢出群聊）" if r.get("action") == "kick" else "")
            )
    else:
        lines.append("未启用任何规则。")
    lines.append("")
    lines.append("指令：/限流、/限流开关；被处罚用户：/申诉")
    return "\n".join(lines)


_limit_cmd = on_command("限流", aliases={"发言限制"}, priority=5, block=True)


@_limit_cmd.handle()
async def _cmd_help(bot: Bot, event: MessageEvent, matcher: Matcher):
    if isinstance(event, GroupMessageEvent):
        if not await _is_manager(bot, event):
            await matcher.finish("没有管理权限。")
        gid = str(event.group_id)
        await matcher.finish(_render_help(gid, _group_rules(gid)))
    await matcher.finish("请在群内使用 /限流。")


_toggle_cmd = on_command("限流开关", aliases={"发言限制开关"}, priority=5, block=True)


@_toggle_cmd.handle()
async def _cmd_toggle(bot: Bot, event: MessageEvent, matcher: Matcher):
    if isinstance(event, GroupMessageEvent):
        if not await _is_manager(bot, event):
            await matcher.finish("没有管理权限。")
        gid = str(event.group_id)
        file = DATA_DIR / f"{gid}.json"
        data = _load_json(file, {"id_group": gid, "enabled": False, "rules": []})
        data["enabled"] = not data.get("enabled", True)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _save_json(file, data)
        await matcher.finish(
            f"已将群 {gid} 的发言限制调整为：{'已启用' if data['enabled'] else '已关闭'}。"
        )
    await matcher.finish("请在群内使用 /限流开关。")


async def _is_manager(bot: Bot, event: GroupMessageEvent) -> bool:
    uid = str(event.user_id)
    if _is_superuser(uid) or uid in set(cfg.limit_managers):
        return True
    role = await _role_of(bot, str(event.group_id), uid)
    return role in ("owner", "admin")


# ---------------- 申诉 ----------------
_appeal_cmd = on_command("申诉", aliases={"appeal"}, priority=5, block=True)


@_appeal_cmd.handle()
async def _cmd_appeal(
    bot: Bot, event: MessageEvent, matcher: Matcher, arg: Message = CommandArg()
):
    args = (arg.extract_plain_text() or "").strip()
    gid = None
    reason = args
    if isinstance(event, GroupMessageEvent):
        gid = str(event.group_id)
    else:
        # 私聊：/申诉 <群号> <原因>
        parts = args.split(maxsplit=1)
        if len(parts) == 2 and parts[0].isdigit():
            gid = parts[0]
            reason = parts[1]
        else:
            await matcher.finish("私聊申诉格式：/申诉 <群号> <申诉原因>")
    if not gid:
        await matcher.finish("无法确定所在群，请私聊使用 /申诉 <群号> <原因>")
    if not reason:
        await matcher.finish("请附上申诉原因：/申诉 <原因>")

    uid = str(event.user_id)
    now = _now()
    window = cfg.limit_appeal_window_minutes * 60
    appeals = _load_appeals()
    key = f"{gid}:{uid}"
    hist = [t for t in appeals.get(key, []) if now - t < window]
    if len(hist) >= cfg.limit_appeal_max:
        wait_min = int((window - (now - min(hist))) / 60) + 1
        await matcher.finish(
            f"申诉频率过高，请在约 {wait_min} 分钟后再试（{cfg.limit_appeal_window_minutes}分钟内最多{cfg.limit_appeal_max}次）。"
        )
    hist.append(now)
    appeals[key] = hist
    _save_appeals(appeals)

    # 通知管理（带处理指引，并记录消息ID用于「回复本消息」处理）
    name = await _member_name(bot, gid, uid)
    gname = await _group_name(bot, gid)
    appeal_text = (
        f"【申诉】{name}({uid})\n"
        f"群：{gname}({gid})\n"
        f"原因：{reason}\n"
        f"（本次窗口 {cfg.limit_appeal_window_minutes} 分钟内第 {len(hist)} 次申诉）\n"
        f"————\n"
        f"处理：回复本条消息发送「/解封」解除禁言，或「/忽略 [理由]」驳回申诉。"
    )
    sent = await _notify_appeal(bot, appeal_text)
    if sent:
        pending = _load_pending()
        record_id = f"{gid}:{uid}:{now}"
        base = {
            "gid": gid,
            "uid": uid,
            "reason": reason,
            "time": now,
            "result": None,
            "ignore_reason": "",
            "record_id": record_id,
        }
        # 同时用 message_id 与 real_id 建索引，适配 NapCat 回复段引用 id 的差异
        for ids in sent.values():
            for idv in set(ids.values()):
                pending[str(idv)] = dict(base)
        _save_pending(pending)
    await matcher.finish("申诉已提交，管理员会尽快处理。")


# ---------------- 申诉处理（回复申诉通知：/解封、/忽略） ----------------
def _is_appeal_manager(event: MessageEvent) -> bool:
    uid = str(event.user_id)
    return _is_superuser(uid) or uid in set(cfg.limit_managers)


def _reply_id(event: MessageEvent) -> str | None:
    """提取被回复消息的 ID（优先原始 reply 段，兜底适配器解析的 event.reply）。

    NapCat 下 get_msg API 常失败导致 event.reply 为 None，故优先从消息 reply 段读取。
    """
    original = getattr(event, "original_message", None)
    for seg in original if original is not None else event.message:
        if seg.type == "reply" and seg.data.get("id"):
            return str(seg.data["id"])
    reply = getattr(event, "reply", None)
    if reply is not None and getattr(reply, "message_id", None) is not None:
        return str(reply.message_id)
    return None


async def _resolve_pending(bot: Bot, rid: str) -> dict | None:
    """按回复段 id 解析申诉记录；rid 可能是 message_id 或 real_id，用 get_msg 桥接。"""
    pending = _load_pending()
    if rid in pending:
        return pending[rid]
    try:
        info = await bot.get_msg(message_id=int(rid))
    except Exception:
        return None
    if isinstance(info, dict):
        for key in ("message_id", "real_id"):
            alt = info.get(key)
            if alt is not None and str(alt) in pending:
                return pending[str(alt)]
    return None


def _pending_update(record_id, **fields) -> None:
    """按记录 ID 更新某条申诉的全部索引副本（持久化处理结果，重启不丢）。"""
    pending = _load_pending()
    for rec in pending.values():
        if rec.get("record_id") == record_id:
            rec.update(fields)
    _save_pending(pending)


_unban_cmd = on_command("解封", aliases={"解禁", "unban"}, priority=5, block=True)


@_unban_cmd.handle()
async def _cmd_unban(bot: Bot, event: MessageEvent, matcher: Matcher):
    if not _is_appeal_manager(event):
        await matcher.finish("没有处理权限。")
    if not isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在私聊中回复申诉通知消息使用。")
    rid = _reply_id(event)
    if not rid:
        await matcher.finish("请回复申诉通知消息，再发送 /解封。")
    rec = await _resolve_pending(bot, rid)
    if not rec:
        logger.warning(
            f"【发言限制】解封未命中: rid={rid}, pending_keys={list(_load_pending().keys())}"
        )
        await matcher.finish("未找到对应的申诉记录，可能已过期。")
    gid, uid = rec["gid"], rec["uid"]
    if rec.get("result") == "unban":
        await matcher.finish("该申诉已解封，无需重复处理。")
    try:
        await bot.set_group_ban(group_id=int(gid), user_id=int(uid), duration=0)
    except Exception as e:
        await matcher.finish(f"解封失败：{e}")
    _pending_update(rec["record_id"], result="unban", ignore_reason="")
    await _notify_user(bot, gid, uid, f"你的申诉已通过，已解除你在群「{gid}」的禁言。")
    await matcher.finish(f"已解封 {uid}（群 {gid}）。")


def _appeal_ignore_rule(event: MessageEvent) -> bool:
    # 裸“忽略2”是好友/私聊请求的编号操作，交给好友授权插件处理；
    # 申诉忽略必须回复申诉通知，或带非纯编号理由。
    text = str(event.get_plaintext() or "").strip()
    return not bool(re.fullmatch(r"忽略\s*[#＃]?\s*\d+", text))


_ignore_cmd = on_command("忽略", aliases={"驳回", "ignore"}, priority=5, block=True, rule=_appeal_ignore_rule)


@_ignore_cmd.handle()
async def _cmd_ignore(
    bot: Bot, event: MessageEvent, matcher: Matcher, arg: Message = CommandArg()
):
    if not _is_appeal_manager(event):
        await matcher.finish("没有处理权限。")
    if not isinstance(event, PrivateMessageEvent):
        await matcher.finish("请在私聊中回复申诉通知消息使用。")
    rid = _reply_id(event)
    if not rid:
        await matcher.finish("请回复申诉通知消息，再发送 /忽略。")
    rec = await _resolve_pending(bot, rid)
    if not rec:
        logger.warning(
            f"【发言限制】忽略未命中: rid={rid}, pending_keys={list(_load_pending().keys())}"
        )
        await matcher.finish("未找到对应的申诉记录，可能已过期。")
    if rec.get("result") == "unban":
        await matcher.finish("该申诉已解封，不能再忽略。")
    gid, uid = rec["gid"], rec["uid"]
    reason = (arg.extract_plain_text() or "").strip()
    _pending_update(rec["record_id"], result="ignore", ignore_reason=reason)
    text = "你的申诉未通过，维持原处罚。"
    if reason:
        text += f"\n拒绝理由：{reason}"
    await _notify_user(bot, gid, uid, text)
    await matcher.finish(f"已忽略 {uid}（群 {gid}）的申诉。")
