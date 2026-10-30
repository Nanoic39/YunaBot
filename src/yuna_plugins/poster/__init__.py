import asyncio
import json
import re
import time
import uuid
from pathlib import Path
from typing import Optional

from nonebot import get_plugin_config, logger, on, on_message
from nonebot.adapters.telegram import Bot
from nonebot.adapters.telegram.event import (
    CallbackQueryEvent,
    GroupMessageEvent,
    PrivateMessageEvent,
)
from nonebot.adapters.telegram.message import Entity, File, Message, UnCombinFile
from nonebot.adapters.telegram.model import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMedia,
    MessageEntity,
)
from nonebot.matcher import Matcher
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule
from pydantic import TypeAdapter

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="投稿推送",
    description="接收 Telegram 用户投稿，预处理后推送管理员审核，通过后发布到频道并附带标签与投稿人署名",
    usage="私聊发送 /post 开始投稿；或 /post 内容 直接投稿",
    config=Config,
    supported_adapters={"~telegram"},
)

poster_config = get_plugin_config(Config)

PLUGIN_DIR = Path(__file__).parent
DATA_FILE = PLUGIN_DIR / "submissions.json"
LISTS_FILE = PLUGIN_DIR / "lists.json"
USERS_FILE = PLUGIN_DIR / "users.json"

# 内存态
_awaiting_content: dict[int, dict] = {}  # 投稿人 -> 等待发送内容
_awaiting_tag: dict[int, str] = {}  # 投稿人 -> 等待输入自定义标签
_awaiting_append: dict[int, str] = {}  # 投稿人 -> 等待输入追加文字
_pending_reason: dict[int, str] = {}  # 管理员 -> 等待输入拒绝理由
_pending_tag: dict[int, str] = {}  # 管理员 -> 等待输入标签
_media_buffer: dict[str, dict] = {}  # media_group_id -> 媒体组缓冲

TAG_RE = re.compile(r"#[\w]+|\[[^\]]+\]")
_SPOILER_TYPES = {"photo", "video", "animation"}


# ---------- 持久化 ----------
def _load_subs() -> dict:
    if not DATA_FILE.exists():
        return {}
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.error(f"【投稿】读取投稿记录失败：{e}")
        return {}


def _save_subs(data: dict):
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _load_lists() -> dict:
    if not LISTS_FILE.exists():
        return {"whitelist": [], "blacklist": []}
    try:
        data = json.loads(LISTS_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"【投稿】读取名单失败：{e}")
        return {"whitelist": [], "blacklist": []}
    if not isinstance(data, dict):
        return {"whitelist": [], "blacklist": []}
    data.setdefault("whitelist", [])
    data.setdefault("blacklist", [])
    return data


def _save_lists(data: dict):
    LISTS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _load_users() -> dict:
    if not USERS_FILE.exists():
        return {}
    try:
        data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.error(f"【投稿】读取用户记录失败：{e}")
        return {}


def _save_users(data: dict):
    USERS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _eff_whitelist() -> list[int]:
    merged = list(poster_config.poster_whitelist_ids) + _load_lists().get(
        "whitelist", []
    )
    return list(dict.fromkeys(int(x) for x in merged))


def _eff_blacklist() -> list[int]:
    merged = list(poster_config.poster_blacklist_ids) + _load_lists().get(
        "blacklist", []
    )
    return list(dict.fromkeys(int(x) for x in merged))


def _touch_user(user_id: int, user_name: str):
    users = _load_users()
    key = str(user_id)
    u = users.get(key)
    if not u:
        u = {
            "id": user_id,
            "name": user_name,
            "total": 0,
            "approved": 0,
            "rejected": 0,
            "first_seen": int(time.time()),
            "last_active": int(time.time()),
        }
        users[key] = u
    u["name"] = user_name
    u["total"] = u.get("total", 0) + 1
    u["last_active"] = int(time.time())
    _save_users(users)


def _record_verdict(user_id: int, status: str):
    users = _load_users()
    u = users.get(str(user_id))
    if not u:
        return
    if status == "approved":
        u["approved"] = u.get("approved", 0) + 1
    elif status == "rejected":
        u["rejected"] = u.get("rejected", 0) + 1
    _save_users(users)


def _build_stats_text() -> str:
    users = _load_users()
    if not users:
        return "暂无用户投稿记录。"
    lines = ["用户投稿统计："]
    for key, u in sorted(users.items(), key=lambda kv: -int(kv[1].get("total", 0))):
        lines.append(
            f"· {u.get('name', key)}（ID {u.get('id', key)}）"
            f"：投稿 {u.get('total', 0)}，通过 {u.get('approved', 0)}，拒绝 {u.get('rejected', 0)}"
        )
    return "\n".join(lines)


# ---------- 工具 ----------
def _is_admin(user_id) -> bool:
    return str(user_id) in [str(a) for a in poster_config.poster_admin_ids]


def _user_name(user) -> str:
    full = " ".join(p for p in [user.first_name, user.last_name] if p).strip()
    if full:
        return full
    if user.username:
        return f"@{user.username}"
    return str(user.id)


def _extract_tags(text: str) -> tuple[list[str], str]:
    """提取正文中的 #标签 / [标签]，返回 (标签列表, 去除标签后的文本)。
    移除标签时保留换行、空行与缩进，仅清理行尾残留空白。"""
    tags: list[str] = []
    for m in TAG_RE.finditer(text):
        tag = m.group(0)
        tag = tag[1:] if tag.startswith("#") else tag[1:-1]
        tag = tag.strip()
        if tag and tag not in tags:
            tags.append(tag)
    cleaned = TAG_RE.sub("", text)
    cleaned = "\n".join(ln.rstrip() for ln in cleaned.splitlines()).strip()
    return tags, cleaned


def _parse_tag_input(text: str) -> list[str]:
    """解析用户/管理员直接输入的标签（逗号/顿号/空格分隔）"""
    tags: list[str] = []
    for part in re.split(r"[,\s、，]+", text):
        tag = part.strip().lstrip("#").strip("[]").strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def _parse_anon_flag(text: str) -> tuple[bool, str]:
    tokens = text.split(" ", 1)
    if tokens and tokens[0].strip() in poster_config.poster_anon_flags:
        return True, (tokens[1].strip() if len(tokens) > 1 else "")
    return False, text


def _split_command(text: str) -> tuple[str, str]:
    parts = text.split(" ", 1)
    token = parts[0]
    cmd = token[1:].split("@")[0].lower() if token.startswith("/") else token.lower()
    args = parts[1].strip() if len(parts) > 1 else ""
    return cmd, args


def _normalize_cmd(raw: str) -> str:
    return raw.strip().lstrip("/").lower()


def _configured() -> bool:
    return bool(
        poster_config.poster_channel_id
        and (poster_config.poster_admin_ids or poster_config.poster_review_chat_id)
    )


def _is_review_chat(chat_id) -> bool:
    return bool(poster_config.poster_review_chat_id) and str(chat_id) == str(
        poster_config.poster_review_chat_id
    )


def _serialize_files(message: Message) -> list[dict]:
    result = []
    for seg in message:
        if isinstance(seg, File):
            data = {k: v for k, v in seg.data.items() if v is not None}
            result.append({"type": seg.type, "data": data})
    return result


def _deserialize_files(files: list[dict]) -> list[File]:
    segs = []
    for f in files:
        cls = UnCombinFile if f["type"] in ("sticker", "video_note") else File
        segs.append(cls(f["type"], f["data"]))
    return segs


def _content_kind(sub: dict) -> str:
    files = sub.get("files", [])
    if not files:
        return "text"
    if len(files) == 1 and files[0]["type"] in ("sticker", "video_note"):
        return "single_uncombin"
    if len(files) == 1:
        return "single_file"
    return "media_group"


def _is_text_only(sub: dict) -> bool:
    return not sub.get("files")


def _strip_nsfw_if_text(sub: dict) -> bool:
    """纯文本不允许 NSFW，移除后返回 True"""
    if _is_text_only(sub) and "NSFW" in sub.get("tags", []):
        sub["tags"] = [t for t in sub["tags"] if t != "NSFW"]
        return True
    return False


def _tag_summary(sub: dict) -> str:
    tags = sub.get("tags", [])
    return "、".join(f"#{t}" for t in tags) if tags else "无"


def _review_header(sub: dict) -> str:
    return f"新投稿 | via {sub['user_name']} | 标签：{_tag_summary(sub)}"


def _utf16_len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def _publish_caption(sub: dict) -> tuple[str, list[MessageEntity]]:
    parts: list[str] = []
    if sub.get("content_text"):
        parts.append(sub["content_text"])
    if sub.get("tags"):
        parts.append(" ".join(f"#{t}" for t in sub["tags"]))

    name = sub["user_name"]
    extra = sub.get("append_text", "").strip()
    parts.append(f"via. {name}" + (f" | {extra}" if extra else ""))

    text = "\n\n".join(parts)

    name_offset = _utf16_len("\n\n".join(parts[:-1]))
    if len(parts) > 1:
        name_offset += 2  # footer 前的 "\n\n"
    name_offset += _utf16_len("via. ")

    entities = [
        MessageEntity(type="bold", offset=name_offset, length=_utf16_len(name)),
        MessageEntity(type="italic", offset=name_offset, length=_utf16_len(name)),
        MessageEntity(type="underline", offset=name_offset, length=_utf16_len(name)),
    ]
    return text, entities


def _tag_message_text(sub: dict) -> str:
    anon = "开" if sub.get("anonymous") else "关"
    lines = [
        "请选择投稿标签（可多选），确认后提交审核：",
        f"当前标签：{_tag_summary(sub)}",
        f"匿名投稿：{anon}",
    ]
    append = sub.get("append_text", "").strip()
    if append:
        lines.append(f"追加文字：{append}")
    return "\n".join(lines)


# ---------- 键盘 ----------
def _build_tag_keyboard(sub_id: str, sub: dict) -> InlineKeyboardMarkup:
    selected = sub.get("tags", [])
    rows = []
    for i, tag in enumerate(poster_config.poster_tags):
        label = f"[x] {tag}" if tag in selected else f"[ ] {tag}"
        rows.append(
            [InlineKeyboardButton(text=label, callback_data=f"poster:tag:{sub_id}:{i}")]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="自定义标签", callback_data=f"poster:custom:{sub_id}"
            )
        ]
    )
    anon = "开" if sub.get("anonymous") else "关"
    rows.append(
        [
            InlineKeyboardButton(
                text=f"匿名投稿：{anon}", callback_data=f"poster:anon:{sub_id}"
            )
        ]
    )
    rows.append(
        [InlineKeyboardButton(text="追加文字", callback_data=f"poster:append:{sub_id}")]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="提交审核", callback_data=f"poster:submit:{sub_id}"
            ),
            InlineKeyboardButton(text="取消", callback_data=f"poster:cancel:{sub_id}"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _build_review_keyboard(sub_id: str, sub: dict) -> InlineKeyboardMarkup:
    nsfw = "NSFW" in sub.get("tags", [])
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="[x] NSFW" if nsfw else "[ ] NSFW",
                    callback_data=f"poster:review:nsfw:{sub_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="添加标签", callback_data=f"poster:review:custom:{sub_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="通过", callback_data=f"poster:review:ok:{sub_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="重复投稿", callback_data=f"poster:review:dup:{sub_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="拒绝（填写理由）",
                    callback_data=f"poster:review:reject:{sub_id}",
                )
            ],
        ]
    )


# ---------- 投稿提交 ----------
async def _submit_payload(
    bot: Bot,
    user_id: int,
    chat_id: int,
    user_name: str,
    tags: list[str],
    content_text: str,
    files: list[dict],
    anonymous: bool = False,
):
    if not content_text and not files:
        await bot.send_message(chat_id=chat_id, text="未识别到投稿内容，请重试。")
        return

    if user_id in _eff_blacklist():
        await bot.send_message(chat_id=chat_id, text="你已被列入黑名单，无法投稿。")
        return

    sub = {
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "real_name": user_name,
        "user_name": "匿名" if anonymous else user_name,
        "anonymous": anonymous,
        "chat_id": chat_id,
        "content_text": content_text,
        "append_text": "",
        "files": files,
        "tags": tags,
        "status": "tagging",
        "created_at": int(time.time()),
    }
    stripped = _strip_nsfw_if_text(sub)
    _save_subs({**_load_subs(), sub["id"]: sub})
    _touch_user(user_id, user_name)

    if stripped:
        await bot.send_message(
            chat_id=chat_id, text="纯文本投稿不支持 NSFW 标签，已忽略。"
        )
    msg = await bot.send_message(
        chat_id=chat_id,
        text=_tag_message_text(sub),
        reply_markup=_build_tag_keyboard(sub["id"], sub),
    )
    sub["tag_chat_id"] = chat_id
    sub["tag_message_id"] = msg.message_id
    _save_subs({**_load_subs(), sub["id"]: sub})


async def _submit(
    bot: Bot,
    event: PrivateMessageEvent,
    user_id: int,
    chat_id: int,
    tags: list[str],
    content_text: Optional[str] = None,
    anonymous: bool = False,
):
    if content_text is None:
        content_text = event.message.extract_plain_text().strip()
        body_tags, content_text = _extract_tags(content_text)
        tags = list(dict.fromkeys(tags + body_tags))
    await _submit_payload(
        bot,
        user_id,
        chat_id,
        _user_name(event.from_),
        tags,
        content_text,
        _serialize_files(event.message),
        anonymous=anonymous,
    )


async def _edit_tag_message(bot: Bot, sub: dict):
    try:
        await bot.edit_message_text(
            chat_id=sub["tag_chat_id"],
            message_id=sub["tag_message_id"],
            text=_tag_message_text(sub),
            reply_markup=_build_tag_keyboard(sub["id"], sub),
        )
    except Exception as e:
        logger.debug(f"【投稿】更新标签选择失败：{e}")


async def _add_user_tags(bot: Bot, chat_id: int, sub_id: str, text: str):
    subs = _load_subs()
    sub = subs.get(sub_id)
    if not sub or sub["status"] != "tagging":
        await bot.send_message(chat_id=chat_id, text="该投稿已失效。")
        return
    new_tags = _parse_tag_input(text)
    if not new_tags:
        await bot.send_message(chat_id=chat_id, text="未识别到标签，请重试。")
        return
    if "NSFW" in new_tags and _is_text_only(sub):
        await bot.send_message(chat_id=chat_id, text="纯文本投稿不能携带 NSFW 标签。")
        new_tags = [t for t in new_tags if t != "NSFW"]
    if not new_tags:
        return
    sub["tags"] = list(dict.fromkeys(sub.get("tags", []) + new_tags))
    _save_subs(subs)
    await _edit_tag_message(bot, sub)
    await bot.send_message(
        chat_id=chat_id, text=f"已添加标签：{'、'.join(f'#{t}' for t in new_tags)}"
    )


async def _add_user_append(bot: Bot, chat_id: int, sub_id: str, text: str):
    subs = _load_subs()
    sub = subs.get(sub_id)
    if not sub or sub["status"] != "tagging":
        await bot.send_message(chat_id=chat_id, text="该投稿已失效。")
        return
    sub["append_text"] = text.strip()
    _save_subs(subs)
    await _edit_tag_message(bot, sub)
    await bot.send_message(chat_id=chat_id, text="已更新追加文字。")


# ---------- 审核 / 发布 ----------
async def _send_review(bot: Bot, target: int | str, sub: dict) -> Optional[int]:
    content_text = sub.get("content_text", "")
    files = _deserialize_files(sub.get("files", []))
    kind = _content_kind(sub)

    try:
        if kind == "text":
            await bot.send_message(chat_id=target, text=content_text)
        elif kind == "single_file":
            f = files[0]
            await bot.call_api(
                f"send_{f.type}",
                chat_id=target,
                **{f.type: f.data.get("file")},
                caption=content_text or None,
            )
        elif kind == "single_uncombin":
            f = files[0]
            await bot.call_api(
                f"send_{f.type}", chat_id=target, **{f.type: f.data.get("file")}
            )
        else:
            segs = [*files]
            if content_text:
                segs.append(Entity.text(content_text))
            await bot.send_to(target, Message(segs))
    except Exception as e:
        logger.error(f"【投稿】推送审核内容失败（target={target}）：{e}")
        return None

    try:
        control = await bot.send_message(
            chat_id=target,
            text=_review_header(sub),
            reply_markup=_build_review_keyboard(sub["id"], sub),
        )
        return control.message_id
    except Exception as e:
        logger.error(f"【投稿】推送审核按钮失败（target={target}）：{e}")
        return None


async def _push_review(bot: Bot, sub: dict):
    if poster_config.poster_review_chat_id:
        targets = [poster_config.poster_review_chat_id]
    else:
        targets = [str(a) for a in poster_config.poster_admin_ids]

    review_messages = []
    for target in targets:
        mid = await _send_review(bot, target, sub)
        if mid is not None:
            review_messages.append({"chat_id": target, "message_id": mid})

    sub["review_messages"] = review_messages
    _save_subs({**_load_subs(), sub["id"]: sub})


async def _edit_review_controls(bot: Bot, sub: dict):
    for m in sub.get("review_messages", []):
        try:
            await bot.edit_message_text(
                chat_id=m["chat_id"],
                message_id=m["message_id"],
                text=_review_header(sub),
                reply_markup=_build_review_keyboard(sub["id"], sub),
            )
        except Exception as e:
            logger.debug(f"【投稿】更新审核按钮失败：{e}")


async def _finish_review_controls(bot: Bot, sub: dict, status_line: str):
    for m in sub.get("review_messages", []):
        try:
            await bot.edit_message_text(
                chat_id=m["chat_id"],
                message_id=m["message_id"],
                text=f"{_review_header(sub)}\n{status_line}",
                reply_markup=None,
            )
        except Exception as e:
            logger.debug(f"【投稿】更新审核状态失败：{e}")


async def _publish(bot: Bot, sub: dict):
    channel = poster_config.poster_channel_id
    text, entities = _publish_caption(sub)
    files = _deserialize_files(sub.get("files", []))
    kind = _content_kind(sub)
    spoiler = "NSFW" in sub.get("tags", [])

    if kind == "text":
        await bot.send_message(chat_id=channel, text=text, entities=entities)
    elif kind == "single_file":
        f = files[0]
        kwargs = {
            f.type: f.data.get("file"),
            "caption": text,
            "caption_entities": entities,
        }
        if spoiler and f.type in _SPOILER_TYPES:
            kwargs["has_spoiler"] = True
        await bot.call_api(f"send_{f.type}", chat_id=channel, **kwargs)
    elif kind == "single_uncombin":
        f = files[0]
        await bot.call_api(
            f"send_{f.type}", chat_id=channel, **{f.type: f.data.get("file")}
        )
        await bot.send_message(chat_id=channel, text=text, entities=entities)
    else:
        medias = []
        for i, f in enumerate(files):
            media = {"type": f.type, "media": f.data.get("file")}
            if i == 0:
                media["caption"] = text
                media["caption_entities"] = entities
            if spoiler and f.type in _SPOILER_TYPES:
                media["has_spoiler"] = True
            medias.append(TypeAdapter(InputMedia).validate_python(media))
        await bot.send_media_group(chat_id=channel, media=medias)


async def _reject(
    bot: Bot,
    admin_id: int,
    subs: dict,
    sub: dict,
    reason: str,
    reply_chat_id: Optional[int | str] = None,
):
    sub["status"] = "rejected"
    sub["reject_reason"] = reason
    sub["reviewed_by"] = admin_id
    _save_subs(subs)
    _record_verdict(sub["user_id"], "rejected")
    await _finish_review_controls(bot, sub, f"已拒绝：{reason}")
    if reply_chat_id is not None:
        await bot.send_message(
            chat_id=reply_chat_id, text=f"已拒绝投稿（理由：{reason}）。"
        )
    try:
        await bot.send_message(chat_id=sub["chat_id"], text=f"你的投稿被拒绝：{reason}")
    except Exception as e:
        logger.debug(f"【投稿】通知投稿人失败：{e}")


async def _do_approve(bot: Bot, event: CallbackQueryEvent, subs: dict, sub: dict):
    _strip_nsfw_if_text(sub)
    try:
        await _publish(bot, sub)
    except Exception as e:
        logger.error(f"【投稿】发布失败：{e}")
        await _answer(bot, event, "发布失败，请稍后重试", alert=True)
        return
    sub["status"] = "approved"
    sub["reviewed_by"] = event.from_.id
    _save_subs(subs)
    _record_verdict(sub["user_id"], "approved")
    await _finish_review_controls(bot, sub, "已通过并发布")
    await _answer(bot, event, "已通过并发布")
    try:
        await bot.send_message(
            chat_id=sub["chat_id"], text="你的投稿已通过并发布到频道。"
        )
    except Exception as e:
        logger.debug(f"【投稿】通知投稿人失败：{e}")


async def _do_reject(
    bot: Bot, event: CallbackQueryEvent, subs: dict, sub: dict, reason: str
):
    await _reject(bot, event.from_.id, subs, sub, reason)
    await _answer(bot, event, "已拒绝")


async def _finalize_reject_reason(
    bot: Bot, admin_id: int, chat_id: int | str, sub_id: str, reason: str
):
    subs = _load_subs()
    sub = subs.get(sub_id)
    if not sub or sub["status"] != "review":
        await bot.send_message(chat_id=chat_id, text="该投稿已处理。")
        return
    await _reject(
        bot, admin_id, subs, sub, reason or "未说明理由", reply_chat_id=chat_id
    )


async def _add_admin_tags(
    bot: Bot, admin_id: int, chat_id: int | str, sub_id: str, text: str
):
    subs = _load_subs()
    sub = subs.get(sub_id)
    if not sub or sub["status"] != "review":
        await bot.send_message(chat_id=chat_id, text="该投稿已处理。")
        return
    new_tags = _parse_tag_input(text)
    if not new_tags:
        await bot.send_message(chat_id=chat_id, text="未识别到标签。")
        return
    if "NSFW" in new_tags and _is_text_only(sub):
        await bot.send_message(chat_id=chat_id, text="纯文本投稿不能携带 NSFW 标签。")
        new_tags = [t for t in new_tags if t != "NSFW"]
    if not new_tags:
        return
    sub["tags"] = list(dict.fromkeys(sub.get("tags", []) + new_tags))
    _save_subs(subs)
    await _edit_review_controls(bot, sub)
    await bot.send_message(
        chat_id=chat_id, text=f"已添加标签：{'、'.join(f'#{t}' for t in new_tags)}"
    )


async def _handle_list_cmd(bot: Bot, chat_id: int, list_name: str, args: str):
    label = "白名单" if list_name == "whitelist" else "黑名单"
    parts = args.split()
    if not parts:
        ids = _load_lists().get(list_name, [])
        text = f"当前{label}：" + (", ".join(str(i) for i in ids) if ids else "空")
        await bot.send_message(chat_id=chat_id, text=text)
        return
    action = parts[0].lower()
    if action not in ("add", "del", "rm", "remove"):
        await bot.send_message(
            chat_id=chat_id,
            text=f"用法：/{'postwl' if list_name == 'whitelist' else 'postbl'} add|del <用户ID>",
        )
        return
    if len(parts) < 2:
        await bot.send_message(chat_id=chat_id, text="请提供用户 ID。")
        return
    try:
        uid = int(parts[1])
    except ValueError:
        await bot.send_message(chat_id=chat_id, text="用户 ID 必须是数字。")
        return
    lists = _load_lists()
    cur = [int(x) for x in lists.get(list_name, [])]
    if action == "add":
        if uid not in cur:
            cur.append(uid)
        done = "已加入"
    else:
        cur = [x for x in cur if x != uid]
        done = "已移除"
    lists[list_name] = cur
    _save_lists(lists)
    await bot.send_message(
        chat_id=chat_id,
        text=f"{done}{label}（ID {uid}）。当前：{', '.join(map(str, cur)) or '空'}",
    )


async def _draft_reminder(bot: Bot, user_id: int, chat_id: int, ts: int):
    timeout = poster_config.poster_timeout_seconds
    lead = max(1, int(timeout * 0.3))
    await asyncio.sleep(max(1, timeout - lead))
    state = _awaiting_content.get(user_id)
    if state and state.get("ts") == ts:
        await bot.send_message(
            chat_id=chat_id, text="投稿草稿即将超时，请尽快发送内容，或 /cancel 取消。"
        )


# ---------- 事件处理 ----------
def _is_private(event) -> bool:
    return isinstance(event, PrivateMessageEvent)


def _is_callback(event) -> bool:
    return isinstance(event, CallbackQueryEvent)


def _is_review_group_msg(event) -> bool:
    return isinstance(event, GroupMessageEvent) and _is_review_chat(event.chat.id)


private_matcher = on_message(rule=Rule(_is_private), priority=10, block=False)
group_matcher = on_message(rule=Rule(_is_review_group_msg), priority=10, block=False)
callback_matcher = on(type="inline", rule=Rule(_is_callback), priority=10, block=False)


async def _answer(
    bot: Bot,
    event: CallbackQueryEvent,
    text: Optional[str] = None,
    alert: bool = False,
):
    try:
        await bot.answer_callback_query(
            callback_query_id=event.id, text=text, show_alert=alert
        )
    except Exception as e:
        logger.debug(f"【投稿】answer_callback_query 失败：{e}")


@private_matcher.handle()
async def _handle_private(bot: Bot, event: PrivateMessageEvent, matcher: Matcher):
    if not poster_config.poster_enabled:
        await matcher.finish()
        return

    user_id = int(event.from_.id)
    chat_id = event.chat.id
    text = event.message.extract_plain_text().strip()

    # 管理员拒绝理由（私信审核场景）
    if user_id in _pending_reason:
        sub_id = _pending_reason.pop(user_id)
        await _finalize_reject_reason(bot, user_id, chat_id, sub_id, text)
        await matcher.finish()
        return

    # 管理员添加标签（私信审核场景）
    if user_id in _pending_tag:
        sub_id = _pending_tag.pop(user_id)
        await _add_admin_tags(bot, user_id, chat_id, sub_id, text)
        await matcher.finish()
        return

    # 投稿人追加文字
    if user_id in _awaiting_append:
        sub_id = _awaiting_append.pop(user_id)
        await _add_user_append(bot, chat_id, sub_id, text)
        await matcher.finish()
        return

    # 投稿人添加自定义标签
    if user_id in _awaiting_tag:
        sub_id = _awaiting_tag.pop(user_id)
        await _add_user_tags(bot, chat_id, sub_id, text)
        await matcher.finish()
        return

    # 指令
    if text.startswith("/"):
        cmd, args = _split_command(text)

        if cmd == _normalize_cmd(poster_config.poster_cancel_command):
            _awaiting_content.pop(user_id, None)
            await bot.send_message(chat_id=chat_id, text="已取消投稿。")
            await matcher.finish()
            return

        if cmd == _normalize_cmd(poster_config.poster_command):
            if not _configured():
                await bot.send_message(
                    chat_id=chat_id, text="投稿功能未配置，请联系管理员。"
                )
                await matcher.finish()
                return
            if user_id in _eff_blacklist():
                await bot.send_message(
                    chat_id=chat_id, text="你已被列入黑名单，无法投稿。"
                )
                await matcher.finish()
                return
            anonymous, args = _parse_anon_flag(args)
            tags, content = _extract_tags(args)
            if content:
                await _submit(
                    bot,
                    event,
                    user_id,
                    chat_id,
                    tags,
                    content_text=content,
                    anonymous=anonymous,
                )
            else:
                ts = int(time.time())
                _awaiting_content[user_id] = {
                    "chat_id": chat_id,
                    "tags": tags,
                    "anonymous": anonymous,
                    "ts": ts,
                }
                asyncio.create_task(_draft_reminder(bot, user_id, chat_id, ts))
                await bot.send_message(
                    chat_id=chat_id,
                    text="请发送投稿内容（文字/图片/文件等），发送 /cancel 取消。",
                )
            await matcher.finish()
            return

        # 管理员管理命令
        if _is_admin(user_id):
            if cmd == _normalize_cmd(poster_config.poster_stats_command):
                await bot.send_message(chat_id=chat_id, text=_build_stats_text())
                await matcher.finish()
                return
            if cmd == _normalize_cmd(poster_config.poster_wl_command):
                await _handle_list_cmd(bot, chat_id, "whitelist", args)
                await matcher.finish()
                return
            if cmd == _normalize_cmd(poster_config.poster_bl_command):
                await _handle_list_cmd(bot, chat_id, "blacklist", args)
                await matcher.finish()
                return

        await matcher.finish()
        return

    # 正在缓冲的媒体组，继续收集后续消息
    if event.media_group_id and event.media_group_id in _media_buffer:
        _buffer_media_group(bot, event, user_id, chat_id, [])
        await matcher.finish()
        return

    # 等待内容
    state = _awaiting_content.pop(user_id, None)
    if state:
        if int(time.time()) - state.get("ts", 0) > poster_config.poster_timeout_seconds:
            await bot.send_message(
                chat_id=chat_id, text="投稿超时，请重新发送 /post 开始。"
            )
            await matcher.finish()
            return
        tags = state.get("tags", [])
        anonymous = state.get("anonymous", False)
        if event.media_group_id:
            _buffer_media_group(bot, event, user_id, chat_id, tags, anonymous)
        else:
            await _submit(bot, event, user_id, chat_id, tags, anonymous=anonymous)
        await matcher.finish()
        return

    await matcher.finish()


@group_matcher.handle()
async def _handle_group(bot: Bot, event: GroupMessageEvent, matcher: Matcher):
    admin_id = int(event.from_.id)
    text = event.message.extract_plain_text().strip()

    if admin_id in _pending_reason:
        sub_id = _pending_reason.pop(admin_id)
        await _finalize_reject_reason(bot, admin_id, event.chat.id, sub_id, text)
    elif admin_id in _pending_tag:
        sub_id = _pending_tag.pop(admin_id)
        await _add_admin_tags(bot, admin_id, event.chat.id, sub_id, text)
    await matcher.finish()


def _buffer_media_group(
    bot: Bot,
    event: PrivateMessageEvent,
    user_id: int,
    chat_id: int,
    tags: list[str],
    anonymous: bool = False,
):
    gid = event.media_group_id
    if gid not in _media_buffer:
        _media_buffer[gid] = {
            "bot": bot,
            "user_id": user_id,
            "chat_id": chat_id,
            "user_name": _user_name(event.from_),
            "anonymous": anonymous,
            "tags": tags,
            "files": [],
            "caption": "",
            "task": asyncio.create_task(_finalize_media_group(gid)),
        }
    buf = _media_buffer[gid]
    buf["files"].extend(_serialize_files(event.message))
    caption = event.message.extract_plain_text().strip()
    if caption and not buf["caption"]:
        buf["caption"] = caption


async def _finalize_media_group(gid: str):
    await asyncio.sleep(1.0)
    buf = _media_buffer.pop(gid, None)
    if not buf:
        return
    await _submit_payload(
        buf["bot"],
        buf["user_id"],
        buf["chat_id"],
        buf["user_name"],
        buf["tags"],
        buf["caption"],
        buf["files"],
        anonymous=buf["anonymous"],
    )


@callback_matcher.handle()
async def _handle_callback(bot: Bot, event: CallbackQueryEvent, matcher: Matcher):
    data = event.data or ""
    if not data.startswith("poster:"):
        await matcher.finish()
        return

    parts = data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    subs = _load_subs()
    user_id = int(event.from_.id)

    if action == "tag":
        sub_id = parts[2]
        idx = int(parts[3])
        sub = subs.get(sub_id)
        if not sub or sub["status"] != "tagging":
            await _answer(bot, event, "该投稿已失效", alert=True)
            await matcher.finish()
            return
        tag = poster_config.poster_tags[idx]
        if tag == "NSFW" and tag not in sub["tags"] and _is_text_only(sub):
            await _answer(bot, event, "纯文本投稿不能携带 NSFW 标签", alert=True)
            await matcher.finish()
            return
        if tag in sub["tags"]:
            sub["tags"].remove(tag)
        else:
            sub["tags"].append(tag)
        _save_subs(subs)
        await _edit_tag_message(bot, sub)
        await _answer(bot, event)
        await matcher.finish()
        return

    if action == "custom":
        sub_id = parts[2]
        sub = subs.get(sub_id)
        if not sub or sub["status"] != "tagging":
            await _answer(bot, event, "该投稿已失效", alert=True)
            await matcher.finish()
            return
        _awaiting_append.pop(user_id, None)
        _awaiting_tag[user_id] = sub_id
        await _answer(bot, event, "请直接回复标签（可用逗号/空格分隔）", alert=True)
        await matcher.finish()
        return

    if action == "anon":
        sub_id = parts[2]
        sub = subs.get(sub_id)
        if not sub or sub["status"] != "tagging":
            await _answer(bot, event, "该投稿已失效", alert=True)
            await matcher.finish()
            return
        sub["anonymous"] = not sub.get("anonymous", False)
        sub["user_name"] = (
            "匿名" if sub["anonymous"] else sub.get("real_name", sub["user_name"])
        )
        _save_subs(subs)
        await _edit_tag_message(bot, sub)
        await _answer(bot, event)
        await matcher.finish()
        return

    if action == "append":
        sub_id = parts[2]
        sub = subs.get(sub_id)
        if not sub or sub["status"] != "tagging":
            await _answer(bot, event, "该投稿已失效", alert=True)
            await matcher.finish()
            return
        _awaiting_tag.pop(user_id, None)
        _awaiting_append[user_id] = sub_id
        await _answer(bot, event, "请直接回复要追加的文字", alert=True)
        await matcher.finish()
        return

    if action == "submit":
        sub_id = parts[2]
        sub = subs.get(sub_id)
        if not sub or sub["status"] != "tagging":
            await _answer(bot, event, "该投稿已失效", alert=True)
            await matcher.finish()
            return

        # 白名单直接发布
        if sub["user_id"] in _eff_whitelist():
            sub["status"] = "approved"
            sub["reviewed_by"] = "whitelist"
            _save_subs(subs)
            await bot.edit_message_reply_markup(
                chat_id=sub["tag_chat_id"],
                message_id=sub["tag_message_id"],
                reply_markup=None,
            )
            await _answer(bot, event, "已直接发布（白名单）")
            try:
                await _publish(bot, sub)
            except Exception as e:
                logger.error(f"【投稿】白名单直发失败：{e}")
                await bot.send_message(
                    chat_id=sub["chat_id"], text="发布失败，请联系管理员。"
                )
                await matcher.finish()
                return
            _record_verdict(sub["user_id"], "approved")
            await bot.send_message(
                chat_id=sub["chat_id"], text="你的投稿已直接发布到频道（白名单）。"
            )
            await matcher.finish()
            return

        sub["status"] = "review"
        _save_subs(subs)
        await bot.edit_message_reply_markup(
            chat_id=sub["tag_chat_id"],
            message_id=sub["tag_message_id"],
            reply_markup=None,
        )
        await _answer(bot, event, "已提交审核")
        await bot.send_message(
            chat_id=sub["chat_id"], text="投稿已提交，等待管理员审核。"
        )
        await _push_review(bot, sub)
        await matcher.finish()
        return

    if action == "cancel":
        sub_id = parts[2]
        sub = subs.get(sub_id)
        if sub and sub["status"] == "tagging":
            sub["status"] = "cancelled"
            _save_subs(subs)
        await bot.edit_message_reply_markup(
            chat_id=sub["tag_chat_id"],
            message_id=sub["tag_message_id"],
            reply_markup=None,
        )
        await _answer(bot, event, "已取消")
        await bot.send_message(chat_id=sub["chat_id"], text="投稿已取消。")
        await matcher.finish()
        return

    if action == "review":
        verdict = parts[2]
        sub_id = parts[3]
        sub = subs.get(sub_id)
        if not sub or sub["status"] != "review":
            await _answer(bot, event, "该投稿已被处理", alert=True)
            await matcher.finish()
            return
        if not _is_admin(user_id):
            await _answer(bot, event, "无审核权限", alert=True)
            await matcher.finish()
            return

        if verdict == "ok":
            await _do_approve(bot, event, subs, sub)
        elif verdict == "dup":
            await _do_reject(bot, event, subs, sub, "重复投稿")
        elif verdict == "nsfw":
            if "NSFW" in sub["tags"]:
                sub["tags"].remove("NSFW")
            else:
                if _is_text_only(sub):
                    await _answer(
                        bot, event, "纯文本投稿不应携带 NSFW 标签", alert=True
                    )
                    await matcher.finish()
                    return
                sub["tags"].append("NSFW")
            _save_subs(subs)
            await _edit_review_controls(bot, sub)
            await _answer(bot, event)
        elif verdict == "custom":
            _pending_tag[user_id] = sub_id
            await _answer(bot, event, "请在审核频道回复要添加的标签", alert=True)
        elif verdict == "reject":
            _pending_reason[user_id] = sub_id
            await _answer(bot, event, "请在审核频道回复拒绝理由", alert=True)
        await matcher.finish()
        return

    await matcher.finish()
