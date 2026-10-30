"""聊天插件的社会化能力：资料卡、生日、点赞与 QQ 空间。

只通过 OneBot V11 / NapCat 的扩展接口工作；不依赖 Telegram 或任何第三方平台。
所有能力都是 best-effort：接口不支持时记录错误并继续聊天，绝不阻塞正常回复。
"""

from __future__ import annotations

import html
from src.qq_bridge import is_snowluma
from src import qq_qzone

import asyncio
import json
import os
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from nonebot.log import logger

TZ = timezone(timedelta(hours=8))


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def _atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def parse_birthday(value) -> tuple[str, int]:
    """解析多种生日格式，返回 (MM-DD, 年份)；无法识别返回 ("", 0)。"""
    if value is None:
        return "", 0
    if isinstance(value, dict):
        month = value.get("month") or value.get("birthday_month")
        day = value.get("day") or value.get("birthday_day")
        year = value.get("year") or value.get("birthday_year") or 0
        try:
            month, day, year = int(month), int(day), int(year or 0)
        except (TypeError, ValueError):
            return "", 0
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{month:02d}-{day:02d}", year
        return "", 0
    text = str(value).strip()
    if not text:
        return "", 0
    match = re.search(r"(?:(19|20)\d{2}[-/.年])?\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})", text)
    if match:
        year = int(match.group(0)[:4]) if re.match(r"(19|20)\d{2}", match.group(0)) else 0
        month, day = int(match.group(2)), int(match.group(3))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{month:02d}-{day:02d}", year
    match = re.fullmatch(r"(\d{1,2})\s*[-/.月]\s*(\d{1,2})\s*日?", text)
    if match:
        month, day = int(match.group(1)), int(match.group(2))
        if 1 <= month <= 12 and 1 <= day <= 31:
            return f"{month:02d}-{day:02d}", 0
    return "", 0


def _clean_text(value, limit: int = 200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def merge_onebot_info(profile: dict, info: dict, source: str) -> dict:
    """把 get_stranger_info / get_friend_list / get_group_member_info 合并进档案。"""
    if not isinstance(info, dict):
        return profile
    mapping = {
        "nickname": ("nickname", "nick"),
        "remark": ("remark",),
        "sex": ("sex",),
        "age": ("age",),
        "qid": ("qid",),
        "sign": ("long_nick", "sign", "personal_note"),
        "level": ("qqLevel", "level"),
        "region": ("area", "city", "country"),
        "card": ("card",),
        "role": ("role",),
        "join_time": ("join_time",),
        "last_sent_time": ("last_sent_time",),
    }
    for target, keys in mapping.items():
        for key in keys:
            value = info.get(key)
            if value not in (None, "", 0, "unknown"):
                profile[target] = _clean_text(value) if target in ("nickname", "remark", "sign", "card") else value
                break
    for key in ("birthday", "birthday_month", "birthday_day", "birthday_year"):
        if key in info and info.get(key) not in (None, "", 0):
            profile[key] = info[key]
    birthday, year = parse_birthday(info)
    if birthday:
        profile["birthday"] = birthday
        if year:
            profile["birth_year"] = year
    profile["source"] = source
    profile["updated"] = int(time.time())
    return profile


# ---------------------------------------------------------------------------
# 档案存储
# ---------------------------------------------------------------------------
class ProfileStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._last_save = 0.0
        self._data = self._load()

    def _load(self) -> dict:
        data = {"profiles": {}, "wished": {}, "updated": 0}
        if not self.path.exists():
            return data
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as error:
            logger.warning(f"[social] 读取资料档案失败：{error}")
            return data
        if not isinstance(raw, dict):
            return data
        profiles = raw.get("profiles") if isinstance(raw.get("profiles"), dict) else {}
        data["profiles"] = profiles
        data["wished"] = raw.get("wished") if isinstance(raw.get("wished"), dict) else {}
        data["updated"] = int(raw.get("updated") or 0)
        return data

    def reload(self) -> None:
        with self._lock:
            self._data = self._load()
            self._last_save = 0.0

    def _save(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_save < 20:
            return
        self._last_save = now
        self._data["updated"] = int(now)
        try:
            _atomic_json(self.path, self._data)
        except OSError as error:
            logger.warning(f"[social] 保存资料档案失败：{error}")

    def get(self, qq: str) -> dict:
        with self._lock:
            profile = self._data["profiles"].get(str(qq))
            return dict(profile) if isinstance(profile, dict) else {}

    def all(self) -> dict:
        with self._lock:
            return {str(k): dict(v) for k, v in self._data["profiles"].items() if isinstance(v, dict)}

    def upsert(self, qq: str, fields: dict, source: str = "manual") -> dict:
        qq = str(qq)
        if not qq.isdigit():
            return {}
        with self._lock:
            profile = self._data["profiles"].setdefault(qq, {})
            profile.setdefault("qq", qq)
            for key, value in (fields or {}).items():
                if value not in (None, "", 0):
                    profile[key] = value
            profile["source"] = source
            profile["updated"] = int(time.time())
            self._save(force=True)
            return dict(profile)

    def merge_onebot(self, qq: str, info: dict, source: str) -> dict:
        with self._lock:
            profile = self._data["profiles"].setdefault(str(qq), {"qq": str(qq)})
            merge_onebot_info(profile, info, source)
            self._save(force=True)
            return dict(profile)

    def merge_batch(self, entries: list[tuple[str, dict]], source: str) -> int:
        count = 0
        with self._lock:
            for qq, info in entries:
                if not str(qq).isdigit():
                    continue
                profile = self._data["profiles"].setdefault(str(qq), {"qq": str(qq)})
                merge_onebot_info(profile, info, source)
                count += 1
            if count:
                self._save(force=True)
        return count

    def mark_seen(self, qq: str, gid: str | None, name: str = "") -> None:
        qq = str(qq)
        if not qq.isdigit():
            return
        now = int(time.time())
        with self._lock:
            profile = self._data["profiles"].setdefault(qq, {"qq": qq})
            if name and not profile.get("nickname"):
                profile["nickname"] = _clean_text(name)
            profile["last_seen"] = now
            if gid:
                groups = profile.setdefault("groups", {})
                groups[str(gid)] = now
            else:
                profile["private_seen"] = now
            self._save()

    def needs_refresh(self, qq: str, hours: int) -> bool:
        profile = self.get(qq)
        if not profile:
            return True
        if not profile.get("updated"):
            return True
        return time.time() - float(profile["updated"]) > max(1, hours) * 3600

    def groups(self, qq: str, max_age_days: int = 45) -> list[str]:
        profile = self.get(qq)
        groups = profile.get("groups") if isinstance(profile.get("groups"), dict) else {}
        cutoff = time.time() - max(1, max_age_days) * 86400
        return [str(gid) for gid, seen in groups.items() if float(seen or 0) >= cutoff]

    def birthday_of(self, profile: dict) -> str:
        manual = profile.get("manual") if isinstance(profile.get("manual"), dict) else {}
        return str(manual.get("birthday") or profile.get("birthday") or "")

    def today_birthdays(self, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now(TZ)
        key = now.strftime("%m-%d")
        result = []
        for qq, profile in self.all().items():
            if self.birthday_of(profile) == key:
                result.append(profile)
        return result

    def upcoming_birthdays(self, days: int = 30, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now(TZ)
        if now.tzinfo is None:
            now = now.replace(tzinfo=TZ)
        result = []
        for qq, profile in self.all().items():
            birthday = self.birthday_of(profile)
            if not birthday:
                continue
            try:
                month, day = map(int, birthday.split("-"))
                candidate = datetime(now.year, month, day, tzinfo=TZ)
                if candidate < now.replace(hour=0, minute=0, second=0, microsecond=0):
                    candidate = datetime(now.year + 1, month, day, tzinfo=TZ)
                delta = (candidate.date() - now.date()).days
                if 0 <= delta <= days:
                    result.append({**profile, "days_left": delta, "birthday": birthday})
            except (ValueError, TypeError):
                continue
        result.sort(key=lambda item: item["days_left"])
        return result

    def mark_wished(self, qq: str, date_key: str) -> None:
        with self._lock:
            self._data["wished"][str(qq)] = date_key
            self._save(force=True)

    def was_wished(self, qq: str, date_key: str) -> bool:
        with self._lock:
            return self._data["wished"].get(str(qq)) == date_key

    def set_manual(self, qq: str, birthday: str = "", notes: str = "", tags: list | None = None) -> dict:
        with self._lock:
            profile = self._data["profiles"].setdefault(str(qq), {"qq": str(qq)})
            manual = profile.setdefault("manual", {})
            if birthday:
                parsed, year = parse_birthday(birthday)
                if not parsed:
                    raise ValueError("生日格式应为 MM-DD 或 YYYY-MM-DD")
                manual["birthday"] = parsed
                if year:
                    manual["birth_year"] = year
            elif "birthday" in manual:
                manual.pop("birthday", None)
            if notes is not None:
                manual["notes"] = _clean_text(notes, 500)
            if tags is not None:
                manual["tags"] = [_clean_text(tag, 24) for tag in tags if _clean_text(tag, 24)][:12]
            profile["updated"] = int(time.time())
            self._save(force=True)
            return dict(profile)

    def like_state(self, qq: str, cap: int, now: datetime | None = None) -> dict:
        """返回今日点赞额度；QQ 资料卡每天最多 10 个，关系上限只可更低。"""
        now = now or datetime.now(TZ)
        if now.tzinfo is None:
            now = now.replace(tzinfo=TZ)
        today = now.strftime("%Y-%m-%d")
        profile = self.get(qq)
        if str(profile.get("like_day") or "") != today:
            used = 0
        else:
            try:
                used = max(0, int(profile.get("like_today") or 0))
            except (TypeError, ValueError):
                used = 0
        hard_cap = min(10, max(0, int(cap)))
        return {
            "day": today,
            "cap": hard_cap,
            "used": used,
            "remaining": max(0, hard_cap - used),
        }

    def mark_liked(self, qq: str, times: int = 1) -> None:
        qq = str(qq)
        times = max(1, int(times))
        now = datetime.now(TZ)
        today = now.strftime("%Y-%m-%d")
        with self._lock:
            profile = self._data["profiles"].setdefault(qq, {"qq": qq})
            if str(profile.get("like_day") or "") != today:
                profile["like_day"] = today
                profile["like_today"] = 0
            profile["liked_at"] = int(time.time())
            profile["like_count"] = int(profile.get("like_count") or 0) + times
            profile["like_today"] = int(profile.get("like_today") or 0) + times
            self._save(force=True)

    def can_like(self, qq: str, cooldown_hours: int = 24, cap: int = 10) -> bool:
        liked_at = float(self.get(qq).get("liked_at") or 0)
        if time.time() - liked_at < max(1, cooldown_hours) * 3600:
            return False
        return self.like_state(qq, cap)["remaining"] > 0

    def prompt(self, qq: str) -> str:
        profile = self.get(qq)
        if not profile:
            return ""
        bits = []
        for label, key in (
            ("昵称", "nickname"), ("性别", "sex"), ("年龄", "age"), ("签名", "sign"),
            ("QQ等级", "level"), ("群名片", "card"), ("群角色", "role"), ("地区", "region"),
        ):
            value = profile.get(key)
            if value not in (None, "", 0, "unknown"):
                bits.append(f"{label}={value}")
        birthday = self.birthday_of(profile)
        if birthday:
            bits.append(f"生日={birthday}")
            if birthday == datetime.now(TZ).strftime("%m-%d"):
                bits.append("今天正好是他的生日")
        notes = (profile.get("manual") or {}).get("notes") if isinstance(profile.get("manual"), dict) else ""
        if notes:
            bits.append(f"备注={notes}")
        tags = (profile.get("manual") or {}).get("tags") if isinstance(profile.get("manual"), dict) else []
        if tags:
            bits.append("标签=" + "、".join(str(tag) for tag in tags))
        return "；".join(bits)


# ---------------------------------------------------------------------------
# OneBot 资料/点赞/空间
# ---------------------------------------------------------------------------
async def fetch_profile(bot, qq: str, gid: str | None = None, include_friend: bool = False) -> dict:
    """调用 OneBot 接口获取资料；返回合并后的字段，失败时返回空字典。"""
    qq = str(qq)
    if not qq.isdigit():
        return {}
    merged: dict = {}
    try:
        info = await bot.call_api("get_stranger_info", user_id=int(qq), no_cache=False)
        if isinstance(info, dict):
            merged.update(info)
    except Exception as error:
        logger.debug(f"[social] get_stranger_info {qq} 失败：{error}")
    if gid:
        try:
            member = await bot.call_api(
                "get_group_member_info",
                group_id=int(gid),
                user_id=int(qq),
                no_cache=False,
            )
            if isinstance(member, dict):
                merged.update(member)
        except Exception as error:
            logger.debug(f"[social] get_group_member_info {qq}@{gid} 失败：{error}")
    if include_friend:
        try:
            friends = await bot.call_api("get_friend_list")
            if isinstance(friends, list):
                for friend in friends:
                    if isinstance(friend, dict) and str(friend.get("user_id")) == qq:
                        merged.update(friend)
                        merged["is_friend"] = True
                        break
        except Exception as error:
            logger.debug(f"[social] get_friend_list 失败：{error}")
    return merged


async def refresh_friend_birthdays(bot, store: ProfileStore) -> int:
    """刷新好友列表里的生日字段（好友接口才稳定提供 birthday_*）。"""
    try:
        friends = await bot.call_api("get_friend_list")
    except Exception as error:
        logger.warning(f"[social] 刷新好友生日失败：{error}")
        return 0
    if not isinstance(friends, list):
        return 0
    entries = []
    for friend in friends:
        if not isinstance(friend, dict):
            continue
        qq = str(friend.get("user_id") or "")
        if qq.isdigit():
            entries.append((qq, {**friend, "is_friend": True}))
    return store.merge_batch(entries, "friend")


async def like_profile(bot, qq: str, times: int = 10) -> bool:
    try:
        await bot.call_api("send_like", user_id=int(qq), times=max(1, min(20, int(times))))
        return True
    except Exception as error:
        logger.warning(f"[social] 给 {qq} 的资料卡点赞失败：{error}")
        return False


async def publish_qzone(bot, content: str, ugc_right: int = 1, images: list | None = None) -> dict:
    try:
        payload = {"content": content, "ugc_right": int(ugc_right)}
        if images:
            payload["images"] = list(images)
        result = await bot.call_api("send_qzone_msg", **payload)
        return result if isinstance(result, dict) else {"result": result}
    except Exception as error:
        logger.warning(f"[social] 发表 QQ 空间说说失败：{error}")
        return {"error": str(error)}


# ---------------------------------------------------------------------------
# QQ 空间评论读取/回复（通过 NapCat 的 get_cookies 换取 Qzone Cookie）
# ---------------------------------------------------------------------------
QZONE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
QZONE_FEED_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qq.com/cgi-bin/emotion_cgi_msglist_v6"
QZONE_COMMENT_URL = "https://user.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"
QZONE_REPLY_URL = "https://h5.qzone.qq.com/proxy/domain/taotao.qzone.qq.com/cgi-bin/emotion_cgi_re_feeds"


def _qzone_bkn(p_skey: str) -> str:
    value = 5381
    for ch in str(p_skey or ""):
        value += (value << 5) + ord(ch)
    return str(value & 0x7FFFFFFF)


def _qzone_cookie_value(cookies: str, name: str) -> str:
    match = re.search(rf"(?:^|;\s*){re.escape(name)}=([^;]+)", cookies or "")
    return match.group(1) if match else ""


def _qzone_json(text: str) -> dict:
    raw = (text or "").strip()
    if not raw:
        return {}
    if raw.startswith("_preloadCallback"):
        start, end = raw.find("("), raw.rfind(")")
        raw = raw[start + 1 : end] if start >= 0 and end > start else raw
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _qzone_clean_comment(text, limit: int = 180) -> str:
    cleaned = html.unescape(re.sub(r"<[^>]+>", "", str(text or "")))
    cleaned = re.sub(
        r"@\{uin:\d+,nick:([^,}]+),who:\d+,auto:\d+\}", r"@\1", cleaned
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:limit]


def _qzone_comment_key(post_tid: str, item: dict, parent_tid: str = "") -> str:
    prefix = f"{post_tid}:{parent_tid}" if parent_tid else str(post_tid)
    cid = (
        item.get("commentid") or item.get("commentId") or item.get("cid")
        or item.get("id") or item.get("tid")
    )
    if cid:
        return f"{prefix}:{cid}"
    import hashlib
    digest = hashlib.sha1(
        f"{prefix}|{item.get('uin')}|{item.get('content')}".encode("utf-8", "ignore")
    ).hexdigest()[:20]
    return f"{prefix}:sha1:{digest}"


def _qzone_comment_item(
    post_tid: str, item: dict, index: int = 0,
    parent_comment_id: str = "", parent_uin: str = "",
) -> dict | None:
    if not isinstance(item, dict):
        return None
    content = _qzone_clean_comment(item.get("content") or item.get("con") or "")
    if not content:
        return None
    uin = str(item.get("uin") or item.get("fuin") or "").strip().lstrip("oO")
    name = _qzone_clean_comment(item.get("name") or item.get("nickname") or "", 40)
    try:
        created = int(item.get("create_time") or item.get("created_time") or 0)
    except (TypeError, ValueError):
        created = 0
    return {
        "key": _qzone_comment_key(post_tid, item, parent_comment_id),
        "index": int(index or 0),
        "comment_id": str(
            item.get("commentid") or item.get("commentId") or item.get("cid")
            or item.get("id") or item.get("tid") or ""
        ),
        "parent_comment_id": str(parent_comment_id or ""),
        "parent_uin": str(parent_uin or ""),
        "uin": uin,
        "name": name,
        "content": content,
        "created": created,
        "raw": item,
    }


async def qzone_credentials(bot, retries: int = 2) -> dict:
    """通过 NapCat get_cookies 获取 Qzone Cookie 与 bkn；不落盘、不打印。

    NapCat 的 WebSocket 偶发瞬断（retcode=1200 socket hang up）会让 call_api
    抛 ActionFailed；等两秒重试通常即可恢复，重试仍失败才向调用方报错。
    """
    last_error: Exception | None = None
    for attempt in range(max(1, retries) + 1):
        try:
            result = await bot.call_api("get_cookies", domain="qzone.qq.com")
        except Exception as error:
            last_error = error
            if attempt < max(1, retries):
                await asyncio.sleep(2)
                continue
            raise
        if not isinstance(result, dict):
            raise RuntimeError("get_cookies 返回格式异常")
        cookies = str(result.get("cookies") or "")
        if not cookies:
            raise RuntimeError("未获取到 Qzone Cookie")
        bkn = str(result.get("bkn") or "")
        uin = _qzone_cookie_value(cookies, "uin").lstrip("oO")
        if not uin:
            uin = str(getattr(bot, "self_id", "") or "")
        if not bkn:
            p_skey = _qzone_cookie_value(cookies, "p_skey") or _qzone_cookie_value(cookies, "skey")
            bkn = _qzone_bkn(p_skey)
        return {"cookies": cookies, "bkn": bkn, "uin": uin}
    raise last_error or RuntimeError("get_cookies 失败")


async def qzone_fetch_own_posts(bot, num: int = 3) -> dict:
    """优先使用 SnowLuma 原生空间接口；其他实现保留原有 Cookie 路径。"""
    if await is_snowluma(bot):
        try:
            return await qq_qzone.fetch_posts(bot, num)
        except Exception as error:
            return {"ok": False, "error": str(error), "posts": [], "source": "snowluma"}
    try:
        auth = await qzone_credentials(bot)
    except Exception as error:
        return {"ok": False, "error": str(error), "posts": []}
    params = {
        "g_tk": auth["bkn"],
        "uin": auth["uin"],
        "ftype": 0,
        "sort": 0,
        "pos": 0,
        "num": max(1, min(10, int(num))),
        "replynum": 100,
        "callback": "_preloadCallback",
        "code_version": 1,
        "format": "json",
        "need_comment": 1,
        "need_private_comment": 1,
    }
    headers = {
        "User-Agent": QZONE_UA,
        "Cookie": auth["cookies"],
        "Referer": f"https://user.qzone.qq.com/{auth['uin']}",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            response = await client.get(QZONE_FEED_URL, params=params)
        if response.status_code >= 400:
            return {"ok": False, "error": f"HTTP {response.status_code}", "posts": []}
        payload = _qzone_json(response.text)
    except Exception as error:
        return {"ok": False, "error": str(error), "posts": []}
    if not payload:
        return {"ok": False, "error": "空间返回内容无法解析", "posts": []}
    code = payload.get("code") or payload.get("ret") or 0
    if str(code) not in ("0", ""):
        return {
            "ok": False,
            "error": str(payload.get("message") or payload.get("msg") or f"code={code}"),
            "posts": [],
        }
    posts = []
    for msg in payload.get("msglist") or []:
        if not isinstance(msg, dict):
            continue
        if str(msg.get("uin") or "") != str(auth["uin"]):
            continue
        post_tid = str(msg.get("tid") or "")
        comments = []
        for index, item in enumerate(msg.get("commentlist") or [], 1):
            parsed = _qzone_comment_item(post_tid, item, index)
            if parsed:
                comments.append(parsed)
            parent_tid = str(item.get("tid") or "")
            parent_uin = str(item.get("uin") or "")
            for reply in (item.get("list_3") if isinstance(item, dict) else []) or []:
                parsed_reply = _qzone_comment_item(
                    post_tid, reply, index,
                    parent_comment_id=parent_tid, parent_uin=parent_uin,
                )
                if parsed_reply:
                    comments.append(parsed_reply)
        posts.append({
            "tid": post_tid,
            "uin": str(msg.get("uin") or auth["uin"]),
            "name": _qzone_clean_comment(msg.get("name") or "", 40),
            "content": _qzone_clean_comment(msg.get("content") or "", 240),
            "created": int(msg.get("created_time") or 0),
            "comments": comments,
        })
    return {"ok": True, "uin": auth["uin"], "posts": posts}


async def qzone_reply_comment(bot, post_tid: str, post_uin: str,
                            comment: dict, content: str) -> dict:
    """回复某条评论：使用 Qzone Web 的 commentId/commentUin + paramstr=2。"""
    text = _qzone_clean_comment(content, 120)
    if not text:
        raise RuntimeError("回复内容为空")
    if await is_snowluma(bot):
        try:
            return await qq_qzone.reply_comment(bot, post_tid, post_uin, comment, text)
        except Exception as error:
            return {"ok": False, "error": str(error), "source": "snowluma"}
    try:
        auth = await qzone_credentials(bot)
    except Exception as error:
        return {"ok": False, "error": str(error)}
    # 二级回复挂到父评论上：commentId/commentUin 用父评论的 tid 与作者
    comment_uin = str(comment.get("parent_uin") or comment.get("uin") or "")
    raw_index = comment.get("index") or 0
    raw_id = str(comment.get("comment_id") or "")
    comment_id = str(comment.get("parent_comment_id") or raw_id or raw_index)
    if not comment_id:
        return {"ok": False, "error": "缺少评论 ID"}
    data = {
        "topicId": f"{post_uin}_{post_tid}__1",
        "uin": auth["uin"],
        "hostUin": post_uin or auth["uin"],
        "feedsType": 100,
        "inCharset": "utf-8",
        "outCharset": "utf-8",
        "plat": "qzone",
        "source": "ic",
        "platformid": 52,
        "format": "fs",
        "ref": "feeds",
        "content": text,
        "commentId": comment_id,
        "commentUin": comment_uin,
        "richval": "",
        "richtype": "",
        "private": "0",
        "paramstr": "2",
        "qzreferrer": f"https://user.qzone.qq.com/{auth['uin']}/main",
    }
    headers = {
        "User-Agent": QZONE_UA,
        "Cookie": auth["cookies"],
        "Referer": f"https://user.qzone.qq.com/{post_uin}/mood/{post_tid}",
        "Origin": "https://user.qzone.qq.com",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            response = await client.post(
                QZONE_REPLY_URL, params={"g_tk": auth["bkn"]}, data=data
            )
        if response.status_code >= 400:
            return {"ok": False, "error": f"HTTP {response.status_code}"}
        payload = _qzone_json(response.text)
    except Exception as error:
        return {"ok": False, "error": str(error)}
    code = payload.get("code") or payload.get("ret") or 0
    if str(code) not in ("0", ""):
        return {
            "ok": False,
            "error": str(payload.get("message") or payload.get("msg") or f"code={code}"),
        }
    return {
        "ok": True, "content": text, "comment_id": comment_id,
        "comment_uin": comment_uin,
    }


async def qzone_post_comment(bot, post_tid: str, post_uin: str, content: str,
                             comment_id: str = "") -> dict:
    """在说说下发表评论；comment_id 存在时一并提交，兼容服务端的回复定位。"""
    text = _qzone_clean_comment(content, 120)
    if not text:
        raise RuntimeError("评论内容为空")
    if await is_snowluma(bot):
        try:
            if comment_id:
                detail = await bot.call_api("get_qzone_msg_detail", tid=str(post_tid),
                                            target_uin=int(post_uin or bot.self_id), pos=0, num=100)
                parent = next((row for row in detail.get("comments", [])
                               if str(row.get("comment_id")) == str(comment_id)), None)
                if parent is None:
                    return {"ok": False, "error": "本页未找到对应父评论，未发送回复", "source": "snowluma"}
                return await qq_qzone.reply_comment(bot, post_tid, post_uin,
                                                    {"comment_id": str(comment_id), "uin": parent.get("uin")}, text)
            result = await bot.call_api("comment_qzone", tid=str(post_tid),
                                        target_uin=int(post_uin or bot.self_id), content=text)
            return {"ok": True, "content": text, "comment_id": str((result or {}).get("comment_id") or ""), "source": "snowluma"}
        except Exception as error:
            return {"ok": False, "error": str(error), "source": "snowluma"}
    try:
        auth = await qzone_credentials(bot)
    except Exception as error:
        return {"ok": False, "error": str(error)}
    data = {
        "topicId": f"{post_uin}_{post_tid}__1",
        "uin": auth["uin"],
        "hostUin": post_uin or auth["uin"],
        "feedsType": 100,
        "inCharset": "utf-8",
        "outCharset": "utf-8",
        "plat": "qzone",
        "source": "ic",
        "platformid": 52,
        "format": "fs",
        "ref": "feeds",
        "content": text,
    }
    if comment_id:
        data["commentId"] = comment_id
        data["commentid"] = comment_id
        data["parentTid"] = comment_id
    headers = {
        "User-Agent": QZONE_UA,
        "Cookie": auth["cookies"],
        "Referer": f"https://user.qzone.qq.com/{post_uin}/mood/{post_tid}",
        "Origin": "https://user.qzone.qq.com",
        "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
            response = await client.post(
                QZONE_COMMENT_URL, params={"g_tk": auth["bkn"]}, data=data
            )
        if response.status_code >= 400:
            return {"ok": False, "error": f"HTTP {response.status_code}"}
        payload = _qzone_json(response.text)
    except Exception as error:
        return {"ok": False, "error": str(error)}
    code = payload.get("code") or payload.get("ret") or 0
    if str(code) not in ("0", ""):
        return {
            "ok": False,
            "error": str(payload.get("message") or payload.get("msg") or f"code={code}"),
        }
    return {"ok": True, "content": text, "comment_id": comment_id}


# ---------------------------------------------------------------------------
# QQ 空间发布状态
# ---------------------------------------------------------------------------
def qzone_time_probability(
    last_post: float,
    now: float | None = None,
    *,
    max_probability: float = 0.8,
    min_hours: float = 2.0,
    ramp_hours: float = 96.0,
) -> float:
    """说说时间概率：刚发完 2 小时 0%，96 小时后最高 80%，中间线性增长。"""
    now = time.time() if now is None else float(now)
    last = float(last_post or 0)
    max_probability = max(0.0, min(1.0, float(max_probability)))
    if last <= 0:
        return max_probability
    hours = max(0.0, (now - last) / 3600.0)
    if hours < max(0.0, float(min_hours)):
        return 0.0
    if hours >= max(float(min_hours), float(ramp_hours)):
        return max_probability
    span = max(1e-6, float(ramp_hours) - float(min_hours))
    return max_probability * (hours - float(min_hours)) / span


class QzoneState:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._last_save = 0.0
        self._data = self._load()

    def _load(self) -> dict:
        data = {
            "last_post": 0, "posts": [], "last_error": "", "last_error_time": 0,
            "comment_initialized": False, "comment_seen": [], "comment_source": "",
            "last_comment_check": 0, "last_comment_reply": 0,
            "last_comment_error": "", "comment_reply_day": "",
            "comment_reply_count": 0, "last_comment_text": "",
            "last_comment_reply_text": "", "comment_hot_until": 0,
            "last_event_key": "", "last_event_time": 0,
        }
        if not self.path.exists():
            return data
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return data
        if isinstance(raw, dict):
            data.update({key: raw.get(key, data[key]) for key in data})
        return data

    def reload(self) -> None:
        with self._lock:
            self._data = self._load()

    def save(self) -> None:
        try:
            _atomic_json(self.path, self._data)
        except OSError as error:
            logger.warning(f"[social] 保存空间状态失败：{error}")

    def can_post(self, config, now: datetime | None = None) -> bool:
        """只检查硬性条件：开关、活跃时段、每日上限、错误退避。"""
        now = now or datetime.now(TZ)
        if not bool(getattr(config, "chat_qzone_enabled", False)):
            return False
        start = int(getattr(config, "chat_qzone_start_hour", 10))
        end = int(getattr(config, "chat_qzone_end_hour", 23))
        if start <= end:
            if not (start <= now.hour < end):
                return False
        elif not (now.hour >= start or now.hour < end):
            return False
        if time.time() - float(self._data.get("last_error_time") or 0) < 3600:
            return False
        daily_limit = max(0, int(getattr(config, "chat_qzone_daily_limit", 1)))
        if daily_limit <= 0:
            return False
        today = now.strftime("%Y-%m-%d")
        today_count = sum(
            1 for item in self._data.get("posts", [])
            if isinstance(item, dict) and str(item.get("date")) == today
        )
        return today_count < daily_limit

    def time_probability(self, config, now: datetime | None = None) -> float:
        now = now or datetime.now(TZ)
        return qzone_time_probability(
            float(self._data.get("last_post") or 0),
            now.timestamp(),
            max_probability=float(getattr(config, "chat_qzone_max_probability", 0.8)),
            min_hours=float(getattr(config, "chat_qzone_min_interval_hours", 2)),
            ramp_hours=float(getattr(config, "chat_qzone_probability_ramp_hours", 96)),
        )

    def should_post(self, config, now: datetime | None = None) -> bool:
        if not self.can_post(config, now):
            return False
        probability = self.time_probability(config, now)
        return random.random() < probability

    def event_seen(self, key: str) -> bool:
        return bool(key) and str(self._data.get("last_event_key") or "") == str(key)

    def mark_event(self, key: str) -> None:
        with self._lock:
            self._data["last_event_key"] = str(key or "")
            self._data["last_event_time"] = int(time.time())
            self.save()

    def record(self, content: str, result: dict, kind: str = "normal", event_key: str = "") -> None:
        now = datetime.now(TZ)
        with self._lock:
            self._data["last_post"] = int(time.time())
            self._data["last_error"] = ""
            if event_key:
                self._data["last_event_key"] = str(event_key)
                self._data["last_event_time"] = int(time.time())
            posts = self._data.setdefault("posts", [])
            posts.append({
                "time": int(time.time()),
                "date": now.strftime("%Y-%m-%d"),
                "content": content,
                "kind": kind,
                "image_count": int(result.get("image_count") or 0),
                "tid": str(result.get("tid") or result.get("data", {}).get("tid") or ""),
            })
            self._data["posts"] = posts[-50:]
            self.save()

    def error(self, message: str) -> None:
        with self._lock:
            self._data["last_error"] = _clean_text(message, 300)
            self._data["last_error_time"] = int(time.time())
            self.save()

    def comment_seen(self) -> set[str]:
        with self._lock:
            return {str(item) for item in self._data.get("comment_seen", []) if item}

    def mark_comment_hot(self, minutes: int = 10) -> None:
        minutes = max(1, int(minutes))
        with self._lock:
            target = int(time.time() + minutes * 60)
            self._data["comment_hot_until"] = max(
                int(self._data.get("comment_hot_until") or 0), target
            )
            self.save()

    def mark_comment_seen(self, keys: list[str]) -> None:
        with self._lock:
            seen = list(self._data.get("comment_seen", []))
            for key in keys:
                if key and key not in seen:
                    seen.append(str(key))
            self._data["comment_seen"] = seen[-800:]
            self._data["comment_initialized"] = True
            self._data["last_comment_check"] = int(time.time())
            self.save()

    def migrate_comment_source(self, source: str, keys: list[str]) -> bool:
        """When IDs change provider, establish a baseline rather than replying to old comments."""
        if not source:
            return False
        with self._lock:
            if self._data.get("comment_source") == source:
                return False
            self._data["comment_source"] = source
            self.mark_comment_seen(keys)
            return True

    def comment_state(self) -> dict:
        with self._lock:
            today = datetime.now(TZ).strftime("%Y-%m-%d")
            used = int(self._data.get("comment_reply_count") or 0)
            if str(self._data.get("comment_reply_day") or "") != today:
                used = 0
            return {
                "source": self._data.get("comment_source") or "legacy",
                "initialized": bool(self._data.get("comment_initialized")),
                "seen": len(self._data.get("comment_seen", [])),
                "last_check": int(self._data.get("last_comment_check") or 0),
                "last_reply": int(self._data.get("last_comment_reply") or 0),
                "last_error": self._data.get("last_comment_error") or "",
                "replied_today": used,
                "last_comment": self._data.get("last_comment_text") or "",
                "last_reply_text": self._data.get("last_comment_reply_text") or "",
                "hot_until": int(self._data.get("comment_hot_until") or 0),
            }

    def record_comment_reply(self, comment: str, reply: str) -> None:
        with self._lock:
            today = datetime.now(TZ).strftime("%Y-%m-%d")
            if str(self._data.get("comment_reply_day") or "") != today:
                self._data["comment_reply_day"] = today
                self._data["comment_reply_count"] = 0
            self._data["comment_reply_count"] = int(self._data.get("comment_reply_count") or 0) + 1
            self._data["last_comment_reply"] = int(time.time())
            self._data["last_comment_error"] = ""
            self._data["last_comment_text"] = _clean_text(comment, 180)
            self._data["last_comment_reply_text"] = _clean_text(reply, 120)
            self.save()

    def comment_error(self, message: str) -> None:
        with self._lock:
            self._data["last_comment_check"] = int(time.time())
            self._data["last_comment_error"] = _clean_text(message, 300)
            self.save()

    def status(self) -> dict:
        with self._lock:
            data = json.loads(json.dumps(self._data, ensure_ascii=False))
        return {
            "last_post": data.get("last_post", 0),
            "posts": list(reversed(data.get("posts", [])))[:20],
            "last_error": data.get("last_error", ""),
            "last_error_time": data.get("last_error_time", 0),
            "last_event_key": data.get("last_event_key", ""),
            "last_event_time": data.get("last_event_time", 0),
            "comments": self.comment_state(),
        }


def birthday_wish(nickname: str, is_brother: bool = False) -> str:
    name = nickname or "你"
    if is_brother:
        return random.choice([
            f"哥哥，生日快乐。今天也要把最喜欢的东西留给你。",
            f"哥哥，生日快乐呀。新的一岁也要平平安安、顺顺利利。",
            f"生日快乐，哥哥。今天可以多许一个愿望，我帮你记着。",
        ])
    return random.choice([
        f"生日快乐呀，{name}！新的一岁也要顺顺利利～",
        f"{name}，生日快乐！今天记得吃蛋糕，许个愿。",
        f"诶，今天是{name}生日呀。生日快乐，要开心一点。",
    ])
