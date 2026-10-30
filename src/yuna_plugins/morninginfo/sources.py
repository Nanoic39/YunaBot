"""早报数据源封装：统一超时、重试、User-Agent 与内容过滤。

对外提供：
- fetch_hotlist(board, size)：平台热榜（DailyHotApi 兼容格式，失败回退国内热榜 API）
- fetch_news(feeds, size)：动漫资讯 RSS（主源全部失败回退国内 RSS）
- fetch_bilibili_timeline(weekday_id, size)：B站番剧时间表当天更新（国内源，Bangumi 日历兜底）
- fetch_netease_albums(size)：网易云音乐新碟（国内源，Bangumi 条目兜底）
- fetch_anime_image()：在线二次元图片（优先支持横版筛选的 lolicon，国内图源兜底）
- get_banner_bg()：横幅背景图（本地目录优先，在线兜底）
"""

from __future__ import annotations

import asyncio
import io
import random
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

import httpx
from PIL import Image

from nonebot import get_plugin_config, logger

from .config import Config
from .render import PLUGIN_DIR

cfg = get_plugin_config(Config)

_UA = {"User-Agent": "yuna-bot/morninginfo"}
_LOLICON_URL = "https://api.lolicon.app/setu/v2"
# B站番剧单集播放页：ep{episode_id}
_BILI_EP_URL = "https://www.bilibili.com/bangumi/play/ep{}"
# 网易云部分接口需要 Referer，否则返回空
_NETEASE_HEADERS = {"Referer": "https://music.163.com/"}
DEFAULT_BG_DIR = PLUGIN_DIR / "backgrounds"
_BG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


async def _get(
    client: httpx.AsyncClient, url: str, headers: dict | None = None, **kw
) -> httpx.Response:
    """带重试的 GET，失败抛出最后一次异常。

    headers 传入时会与默认 User-Agent 合并（用于需要 Referer 等额外头的源）。
    """
    merged = dict(_UA)
    if headers:
        merged.update(headers)
    last = None
    for attempt in range(cfg.morning_http_retries + 1):
        try:
            resp = await client.get(url, headers=merged, **kw)
            resp.raise_for_status()
            return resp
        except Exception as e:  # noqa: BLE001
            last = e
            if attempt < cfg.morning_http_retries:
                await asyncio.sleep(0.8 * (attempt + 1))
    raise last


def _blocked(title: str) -> bool:
    low = title.lower()
    return any(k and k.lower() in low for k in cfg.morning_block_keywords)


def _normalize_hot(items: list, size: int) -> list[dict]:
    """把各热榜源的条目统一成 [{title, url, hot}]，兼容 DailyHotApi / uapis 字段。"""
    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title") or it.get("name") or "").strip()
        if not title or _blocked(title):
            continue
        out.append(
            {
                "title": title,
                "url": it.get("url") or it.get("mobileUrl") or "",
                "hot": it.get("hot") or it.get("num") or it.get("hot_value"),
            }
        )
        if len(out) >= size:
            break
    return out


async def _fetch_hotlist_fallback(board: str, size: int) -> list[dict]:
    """主热榜源失败时的国内兜底（uapis 热榜，?type=<board>）。"""
    base = cfg.morning_hotlist_fallback_url
    if not base:
        return []
    try:
        async with httpx.AsyncClient(
            timeout=cfg.morning_http_timeout, follow_redirects=True
        ) as client:
            resp = await _get(client, base.rstrip("/"), params={"type": board})
            data = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"【早报·热榜】{board} 兜底源获取失败：{e}")
        return []

    if isinstance(data, dict):
        items = data.get("list") or data.get("data")
    elif isinstance(data, list):
        items = data
    else:
        items = None
    if not isinstance(items, list):
        return []
    return _normalize_hot(items, size)


async def fetch_hotlist(board: str, size: int) -> list[dict]:
    """抓取单个平台热榜，返回 [{title, url, hot}]。

    优先主源 cfg.morning_hotlist_url，失败或结果为空时回退国内兜底源。
    """
    url = f"{cfg.morning_hotlist_url.rstrip('/')}/{board}" if cfg.morning_hotlist_url else ""
    if url:
        try:
            async with httpx.AsyncClient(
                timeout=cfg.morning_http_timeout, follow_redirects=True
            ) as client:
                resp = await _get(client, url)
                data = resp.json()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"【早报·热榜】{board} 主源获取失败：{e}")
        else:
            items = data.get("data") if isinstance(data, dict) else data
            if isinstance(items, list):
                out = _normalize_hot(items, size)
                if out:
                    return out

    return await _fetch_hotlist_fallback(board, size)


async def fetch_bilibili_timeline(weekday_id: int, size: int) -> list[dict]:
    """抓取 B站番剧时间表当天的更新（国内源，替代 Bangumi 日历）。

    weekday_id：1=周一 … 7=周日（与 Bangumi 日历字段一致）。
    返回 [{title, original, url, score, episode, follows}]，失败返回空列表。
    """
    if not cfg.morning_bilibili_timeline_url:
        return []
    params = {"types": 1, "before": 6, "after": 6}
    try:
        async with httpx.AsyncClient(
            timeout=cfg.morning_http_timeout, follow_redirects=True
        ) as client:
            resp = await _get(client, cfg.morning_bilibili_timeline_url, params=params)
            data = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"【早报·新番】B站时间表获取失败：{e}")
        return []

    days = data.get("result") if isinstance(data, dict) else data
    if not isinstance(days, list):
        return []

    out: list[dict] = []
    for day in days:
        if not isinstance(day, dict):
            continue
        # 优先当天标记；否则按星期匹配
        if not day.get("is_today") and day.get("day_of_week") != weekday_id:
            continue
        for ep in day.get("episodes") or []:
            if not isinstance(ep, dict):
                continue
            name = str(ep.get("title") or "").strip()
            if not name or _blocked(name):
                continue
            episode_id = ep.get("episode_id")
            out.append(
                {
                    "title": name,
                    "original": "",
                    "url": _BILI_EP_URL.format(episode_id) if episode_id else "",
                    "score": None,
                    "episode": str(ep.get("pub_index") or "").strip(),
                    "follows": ep.get("follows"),
                }
            )
            if len(out) >= size:
                return out
    return out


async def fetch_netease_albums(size: int) -> list[dict]:
    """抓取网易云音乐新碟（国内源，替代 Bangumi 音乐条目）。

    返回 [{title, artist, type, date, url}]，date 为 'YYYY-MM-DD'，失败返回空列表。
    """
    if not cfg.morning_netease_album_url:
        return []
    # 多取一些，交由上层按「最近 N 天」过滤（避免因新碟均不在窗口内而误触发兜底）
    params = {"area": "ALL", "limit": max(size * 4, 30)}
    try:
        async with httpx.AsyncClient(
            timeout=cfg.morning_http_timeout, follow_redirects=True
        ) as client:
            resp = await _get(
                client,
                cfg.morning_netease_album_url,
                params=params,
                headers=_NETEASE_HEADERS,
            )
            data = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"【早报·音乐】网易云新碟获取失败：{e}")
        return []

    albums = data.get("albums") if isinstance(data, dict) else data
    if not isinstance(albums, list):
        return []

    out: list[dict] = []
    for it in albums:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        if not name or _blocked(name):
            continue
        artist = ""
        artists = it.get("artists")
        if isinstance(artists, list) and artists and isinstance(artists[0], dict):
            artist = str(artists[0].get("name") or "").strip()
        if not artist and isinstance(it.get("artist"), dict):
            artist = str(it["artist"].get("name") or "").strip()
        date = ""
        ts = it.get("publishTime")
        if isinstance(ts, (int, float)) and ts > 0:
            try:
                date = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
            except Exception:  # noqa: BLE001
                date = ""
        album_id = it.get("id")
        out.append(
            {
                "title": name,
                "artist": artist,
                "type": str(it.get("type") or it.get("subType") or "").strip(),
                "date": date,
                "url": f"https://music.163.com/#/album?id={album_id}" if album_id else "",
            }
        )
        if len(out) >= size:
            break
    return out


async def fetch_bangumi_calendar(weekday_id: int, size: int) -> list[dict]:
    """抓取 Bangumi 每日放送日历中指定星期的番剧。

    weekday_id：1=周一 … 7=周日（与 Bangumi 日历字段一致）。
    返回 [{title, original, url, score}]，失败返回空列表。
    """
    try:
        async with httpx.AsyncClient(
            timeout=cfg.morning_http_timeout, follow_redirects=True
        ) as client:
            resp = await _get(client, cfg.morning_bangumi_calendar_url)
            data = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"【早报·新番】Bangumi 日历获取失败：{e}")
        return []

    if not isinstance(data, list):
        return []

    out: list[dict] = []
    for day in data:
        if not isinstance(day, dict):
            continue
        weekday = day.get("weekday") or {}
        if weekday.get("id") != weekday_id:
            continue
        for it in day.get("items") or []:
            if not isinstance(it, dict):
                continue
            name = str(it.get("name_cn") or it.get("name") or "").strip()
            if not name or _blocked(name):
                continue
            subject_id = it.get("id")
            out.append(
                {
                    "title": name,
                    "original": str(it.get("name") or "").strip(),
                    "url": it.get("url")
                    or (f"https://bgm.tv/subject/{subject_id}" if subject_id else ""),
                    "score": (it.get("rating") or {}).get("score"),
                }
            )
            if len(out) >= size:
                return out
    return out


async def fetch_bangumi_subjects(
    subject_type: int, year: int, month: int, size: int
) -> list[dict]:
    """抓取 Bangumi v0 指定年月的新条目（3=音乐，4=游戏）。

    返回 [{title, original, date, platform, url}]，失败返回空列表。
    """
    if not cfg.morning_bangumi_subject_url:
        return []
    url = cfg.morning_bangumi_subject_url.rstrip("/")
    params = {
        "type": subject_type,
        "sort": "date",
        "year": year,
        "month": month,
        "limit": size,
    }
    try:
        async with httpx.AsyncClient(
            timeout=cfg.morning_http_timeout, follow_redirects=True
        ) as client:
            resp = await _get(client, url, params=params)
            data = resp.json()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"【早报】Bangumi 条目获取失败(type={subject_type})：{e}")
        return []

    items = data.get("data") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []

    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name_cn") or it.get("name") or "").strip()
        if not name or _blocked(name):
            continue
        subject_id = it.get("id")
        out.append(
            {
                "title": name,
                "original": str(it.get("name") or "").strip(),
                "date": str(it.get("date") or "").strip(),
                "platform": str(it.get("platform") or "").strip(),
                "url": f"https://bgm.tv/subject/{subject_id}" if subject_id else "",
            }
        )
        if len(out) >= size:
            break
    return out


def _parse_feed(text: str) -> list[dict]:
    """解析 RSS2.0 / Atom 文本，返回 [{title, url, date}]。"""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
        try:
            root = ET.fromstring(cleaned)
        except ET.ParseError:
            return []

    out: list[dict] = []
    for node in root.iter():
        if node.tag.split("}")[-1] not in ("item", "entry"):
            continue
        title = link = date = ""
        for child in node:
            ctag = child.tag.split("}")[-1]
            if ctag == "title" and not title:
                title = (child.text or "").strip()
            elif ctag == "link" and not link:
                link = (child.text or "").strip() or child.get("href", "")
            elif ctag in ("pubDate", "published", "updated", "date") and not date:
                date = (child.text or "").strip()
        if title:
            out.append({"title": title, "url": link, "date": date})
    return out


async def _fetch_feeds(feeds: list[str], size: int) -> list[dict]:
    """逐个抓取 RSS 源，合并后取前 size 条；解析失败或命中过滤词的条目跳过。"""
    out: list[dict] = []
    async with httpx.AsyncClient(
        timeout=cfg.morning_http_timeout, follow_redirects=True
    ) as client:
        for feed in feeds:
            try:
                resp = await _get(client, feed)
                items = _parse_feed(resp.text)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"【早报·资讯】RSS 获取失败 {feed}：{e}")
                continue
            for it in items:
                if _blocked(it["title"]):
                    continue
                out.append(it)
                if len(out) >= size:
                    return out
    return out


async def fetch_news(feeds: list[str], size: int) -> list[dict]:
    """抓取动漫资讯：主 RSS 源全部失败时回退国内兜底 RSS 源。"""
    out = await _fetch_feeds(feeds, size) if feeds else []
    if out:
        return out
    if cfg.morning_news_rss_fallback:
        out = await _fetch_feeds(cfg.morning_news_rss_fallback, size)
    return out


async def _download_image(
    client: httpx.AsyncClient, url: str, landscape_only: bool = False
) -> Image.Image | None:
    """下载单张图片并转为 RGBA，失败或超时返回 None。

    landscape_only=True 时只接受横版图片（宽 > 高），非横版返回 None。
    单次请求并用 asyncio.wait_for 限制总时长：httpx 的 read 超时只作用于两次
    数据之间的间隔，无法限制「慢速但持续」的连接，否则会长时间占用。
    """
    try:
        resp = await asyncio.wait_for(
            client.get(url, headers=_UA), timeout=cfg.morning_http_timeout
        )
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"【早报】下载图片失败 {url}：{e}")
        return None
    if landscape_only and img.width <= img.height:
        logger.info(f"【早报】跳过非横版图片 {img.width}x{img.height} {url}")
        return None
    return img


async def fetch_anime_image(landscape_only: bool = True) -> Image.Image | None:
    """在线获取一张二次元图片。

    优先使用国内横版直链图源（UApiPro category=acg&type=pc 等，本身即横版、响应快），
    失败时回退到支持横版筛选的 lolicon（aspectRatio=gt1）。
    各图源按序尝试，命中即返回；累计失败（下载失败或非横版）达到 2 次即放弃，
    不再重试后续图源。

    默认只返回横版图片（宽 > 高），用于每日一图与横幅背景。
    """
    # 失败/非横版累计上限：达到即放弃，避免慢源长时间拖累采集
    max_attempts = 2
    attempts = 0

    async def _try(u: str) -> Image.Image | None:
        nonlocal attempts
        img = await _download_image(client, u, landscape_only)
        if img is None:
            attempts += 1
        return img

    async with httpx.AsyncClient(
        timeout=cfg.morning_http_timeout, follow_redirects=True
    ) as client:
        # 主源：国内横版直链（本身即横版，响应快）
        for u in cfg.morning_image_urls:
            img = await _try(u)
            if img is not None:
                return img
            if attempts >= max_attempts:
                logger.info("【早报】取图失败已达上限，放弃")
                return None

        # 兜底：lolicon（可筛选横版，取首个候选；图床较慢，仅在前述源失败时使用）
        params = {"r18": 0, "num": 5, "size": "regular"}
        if landscape_only:
            # lolicon 用 aspectRatio=gt1 筛选横图（宽高比 > 1）；注意参数为驼峰命名
            params["aspectRatio"] = "gt1"
        try:
            resp = await _get(client, _LOLICON_URL, params=params)
            for it in resp.json().get("data") or []:
                urls = it.get("urls") or {}
                u = urls.get("regular") or urls.get("original")
                if not u:
                    continue
                img = await _try(u)
                if img is not None:
                    return img
                if attempts >= max_attempts:
                    logger.info("【早报】取图失败已达上限，放弃")
                    return None
        except Exception as e:  # noqa: BLE001
            logger.warning(f"【早报】lolicon 取图失败：{e}")
        return None


def _bg_dir() -> Path:
    if cfg.morning_bg_dir:
        p = Path(cfg.morning_bg_dir)
        return p if p.is_absolute() else PLUGIN_DIR / p
    return DEFAULT_BG_DIR


def _local_bg() -> Image.Image | None:
    d = _bg_dir()
    if not d.is_dir():
        return None
    files = [f for f in d.iterdir() if f.is_file() and f.suffix.lower() in _BG_EXTS]
    if not files:
        return None
    try:
        return Image.open(random.choice(files)).convert("RGBA")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"【早报】读取本地背景图失败：{e}")
        return None


async def get_banner_bg() -> Image.Image | None:
    """横幅背景图：本地目录优先，无本地图时回退在线二次元图。"""
    if not cfg.morning_bg_enabled:
        return None
    bg = _local_bg()
    if bg is not None:
        return bg
    if cfg.morning_bg_online:
        return await fetch_anime_image()
    return None
