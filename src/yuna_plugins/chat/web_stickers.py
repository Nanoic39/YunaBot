"""按用户语义请求联网寻找表情包。

不是关键词表：先让模型结合上下文/称呼/梗语言还原对象，再检索图片，
下载后用视觉模型核对是否真是该对象且适合作为表情包。
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import html as html_lib
import json
import re
import urllib.parse
from io import BytesIO
from typing import Any

import httpx
from nonebot import logger

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
_IMAGE_MAGIC = (
    (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
    (b"GIF87a", "image/gif", ".gif"),
    (b"GIF89a", "image/gif", ".gif"),
)
_CLIENT: httpx.AsyncClient | None = None


async def _client() -> httpx.AsyncClient:
    global _CLIENT
    if _CLIENT is None or _CLIENT.is_closed:
        _CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(20, connect=8),
            follow_redirects=True,
            headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9,ja;q=0.8,en;q=0.6"},
        )
    return _CLIENT


def _image_info(data: bytes) -> tuple[str, str]:
    for magic, mime, ext in _IMAGE_MAGIC:
        if data.startswith(magic):
            return mime, ext
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return "", ""


def _clean_query(value: str) -> str:
    value = re.sub(r"https?://\S+|@\S+|\d{5,}", " ", str(value or ""))
    return re.sub(r"\s+", " ", value).strip()[:80]


async def plan_request(llm, text: str, recent: list[dict] | None = None) -> dict | None:
    """判断是不是“帮我找个表情包”，并还原梗语言指向的对象。"""
    if not llm or not getattr(llm, "available", False):
        return None
    question = str(text or "").strip()[:800]
    if not question:
        return None
    context = []
    for row in (recent or [])[-8:]:
        content = str(row.get("content") or "").strip()
        if content:
            context.append({"who": str(row.get("name") or row.get("qq") or ""), "text": content[:160]})
    prompt = (
        "判断用户是不是在要求你从网上找/要一张表情包、贴纸、meme图发给TA。"
        "用户可能使用谐音、黑话、昵称、错字或只有群友懂的叫法；必须结合上下文和公开知识还原真实对象，"
        "不能只按字面查，也不能依靠固定词表。"
        "如果不是要找图，need=false。"
        "如果是要找图：canonical 写最可能的标准角色/作品/主体名（例如昵称可能指向某个角色）；"
        "aliases 写原文里的叫法；queries 给2到4个适合公开图片检索的短查询，"
        "要包含标准名 + 表情包/スタンプ/sticker 等不同写法；style 描述想要的风格。"
        "只输出 JSON：{\"need\":true/false,\"canonical\":\"\",\"aliases\":[],\"queries\":[],"
        "\"style\":\"\",\"confidence\":0.0}"
    )
    try:
        raw = await asyncio.wait_for(
            llm.chat(
                [{"role": "system", "content": prompt},
                 {"role": "user", "content": json.dumps({"message": question, "recent": context}, ensure_ascii=False)}],
                temperature=0.0,
                max_tokens=300,
                disable_thinking=True,
                json_mode=True,
            ),
            timeout=10,
        )
        raw = re.sub(r"^```(?:json)?|```$", "", str(raw or "").strip(), flags=re.I).strip()
        data = json.loads(raw)
    except Exception as error:
        logger.debug(f"[web-sticker] 语义判断失败：{error}")
        return None
    if not isinstance(data, dict) or not data.get("need"):
        return None
    canonical = _clean_query(data.get("canonical") or data.get("subject") or "")
    aliases = [_clean_query(x) for x in (data.get("aliases") or []) if _clean_query(x)][:4]
    queries = [_clean_query(x) for x in (data.get("queries") or []) if _clean_query(x)]
    # 只信任模型给出的对象名，不从用户原句里硬拆关键词。
    if canonical:
        for base in (canonical, *aliases):
            for suffix in (" 表情包", " スタンプ", " sticker"):
                q = _clean_query(base + suffix)
                if q and q not in queries:
                    queries.append(q)
    if not queries:
        return None
    return {
        "canonical": canonical,
        "aliases": aliases,
        "queries": queries[:6],
        "style": str(data.get("style") or "")[:80],
        "confidence": float(data.get("confidence") or 0),
    }


async def refine_plan(llm, plan: dict[str, Any], search_fn) -> dict[str, Any]:
    """用一次公开文本检索把谐音/昵称还原为标准角色或作品名。"""
    if not plan or not search_fn:
        return plan
    subject = str(plan.get("canonical") or "").strip()
    aliases = [str(x).strip() for x in (plan.get("aliases") or []) if str(x).strip()]
    needle = subject or (aliases[0] if aliases else "")
    if not needle:
        return plan
    try:
        context = await search_fn(f"{needle} 是什么角色 昵称 梗")
    except Exception as error:
        logger.debug(f"[web-sticker] 昵称检索失败：{error}")
        return plan
    if not context:
        return plan
    prompt = (
        "用户想找表情包，但用了谐音/错字/昵称/黑话。根据公开检索资料还原标准角色、作品或主体名；"
        '不要把检索到的无关名字硬套。只输出 JSON：{"canonical":"标准名","queries":["检索词"]}。'
        "queries 给2到4个适合找图的短词，可含中文/日文/英文。"
    )
    try:
        raw = await asyncio.wait_for(
            llm.chat(
                [{"role": "system", "content": prompt},
                 {"role": "user", "content": json.dumps({"request": needle, "aliases": aliases, "search": context[:3000]}, ensure_ascii=False)}],
                temperature=0.0, max_tokens=240, disable_thinking=True, json_mode=True,
            ), timeout=12,
        )
        raw = re.sub(r"^```(?:json)?|```$", "", str(raw or "").strip(), flags=re.I).strip()
        obj = json.loads(raw)
        if isinstance(obj, dict) and obj.get("canonical"):
            result = dict(plan)
            result["canonical"] = _clean_query(obj.get("canonical"))
            qs = [_clean_query(x) for x in (obj.get("queries") or []) if _clean_query(x)]
            result["queries"] = list(dict.fromkeys(qs + list(plan.get("queries") or [])))[:6]
            return result
    except Exception as error:
        logger.debug(f"[web-sticker] 昵称还原失败：{error}")
    return plan


async def search_image_urls(query: str, limit: int = 20) -> list[str]:
    """用公开图片搜索页取候选 URL；只取图片直链，不执行页面脚本。"""
    q = urllib.parse.quote(str(query or "").strip())
    if not q:
        return []
    urls: list[str] = []
    endpoints = (
        f"https://yandex.ru/images/search?text={q}&safe=strict",
        f"https://cn.bing.com/images/search?q={q}&form=HDRSC2&first=1&tsc=ImageHoverTitle&safeSearch=Strict",
        f"https://www.bing.com/images/search?q={q}&form=HDRSC2&first=1&tsc=ImageHoverTitle&safeSearch=Strict",
    )
    for endpoint in endpoints:
        try:
            client = await _client()
            resp = await client.get(endpoint)
            resp.raise_for_status()
            body = resp.text
            patterns = (
                r"&quot;image&quot;:&quot;(//avatars\.mds\.yandex\.net/.*?)&quot;",
                r'"image":"(//avatars\.mds\.yandex\.net/.*?)"',
                r"thumb\":\{\"url\":\"(//avatars\.mds\.yandex\.net/.*?)\"",
                r'"thumb":\{"url":"(//avatars\.mds\.yandex\.net/.*?)"',
                r"img_href&quot;:&quot;(https?://.*?)&quot;",
                r"murl&quot;:&quot;(https?://.*?)&quot;",
                r'"murl":"(https?://.*?)"',
            )
            for pattern in patterns:
                for match in re.findall(pattern, body):
                    url = html_lib.unescape(match).replace("\\/", "/")
                    if url.startswith("//"):
                        url = "https:" + url
                    if _valid_image_url(url) and url not in urls:
                        urls.append(url)
                        if len(urls) >= limit:
                            break
                if len(urls) >= limit:
                    break
            if len(urls) >= limit:
                break
        except Exception as error:
            logger.debug(f"[web-sticker] 图片检索失败 {query}: {error}")
    return urls[:limit]


def _valid_image_url(url: str) -> bool:
    low = url.lower()
    if not low.startswith(("http://", "https://")):
        return False
    if any(x in low for x in ("logo", "sprite", "tracking", "pixel.")):
        return False
    return not any(x in low for x in (".svg", ".ico"))


async def download_image(url: str, max_bytes: int = 8 * 1024 * 1024) -> tuple[bytes, str, str]:
    try:
        client = await _client()
        resp = await client.get(url, headers={"Referer": "https://www.bing.com/"})
        resp.raise_for_status()
        data = resp.content
        if not data or len(data) > max_bytes or len(data) < 2048:
            return b"", "", ""
        mime, ext = _image_info(data)
        if not mime:
            return b"", "", ""
        return data, mime, ext
    except Exception as error:
        logger.debug(f"[web-sticker] 图片下载失败：{error}")
        return b"", "", ""


def _frame_for_vision(data: bytes, mime: str) -> str:
    payload = data
    if mime in ("image/gif", "image/webp"):
        try:
            from PIL import Image
            with Image.open(BytesIO(data)) as image:
                image.seek(0)
                frame = image.convert("RGB")
                out = BytesIO()
                frame.save(out, format="PNG")
                payload = out.getvalue()
                mime = "image/png"
        except Exception:
            pass
    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"


async def judge_candidate(vision_llm, data: bytes, mime: str, subject: str, style: str = "") -> bool:
    if not vision_llm or not getattr(vision_llm, "available", False) or not subject:
        return True
    prompt = (
        "判断这张图是否与指定对象相关、且适合作为聊天表情包发给群友。"
        "只输出 JSON：{\"match\":true/false,\"sticker\":true/false,\"reason\":\"简短理由\"}。"
        "对象可能是角色/作品/人物；如果图中是该对象的同人、截图或表情包即可 match=true。"
        "明显无关、纯文字广告、真人照片、色情或攻击性内容 match=false。"
    )
    try:
        raw = await asyncio.wait_for(
            vision_llm.chat(
                [{"role": "system", "content": prompt},
                 {"role": "user", "content": [
                     {"type": "text", "text": f"对象：{subject[:100]}；期望风格：{style[:60]}"},
                     {"type": "image_url", "image_url": {"url": _frame_for_vision(data, mime), "detail": "auto"}},
                 ]}],
                temperature=0.0,
                max_tokens=180,
                disable_thinking=True,
                json_mode=True,
            ),
            timeout=25,
        )
        raw = re.sub(r"^```(?:json)?|```$", "", str(raw or "").strip(), flags=re.I).strip()
        obj = json.loads(raw)
        return bool(obj.get("match")) and bool(obj.get("sticker", True))
    except Exception as error:
        logger.debug(f"[web-sticker] 候选图核对失败：{error}")
        return False


async def find_sticker(planner_llm, vision_llm, text: str, recent: list[dict] | None = None,
                        plan: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if plan is None:
        plan = await plan_request(planner_llm, text, recent)
    if not plan:
        return None
    subject = str(plan.get("canonical") or "").strip()
    if not subject:
        subject = str((plan.get("aliases") or [""])[0])
    style = str(plan.get("style") or "")
    seen: set[str] = set()
    candidates = 0
    for query in plan.get("queries") or []:
        for url in await search_image_urls(str(query), limit=12):
            if url in seen:
                continue
            seen.add(url)
            data, mime, ext = await download_image(url)
            if not data:
                continue
            candidates += 1
            if candidates > 10:
                return None
            if await judge_candidate(vision_llm, data, mime, subject, style):
                return {"data": data, "mime": mime, "ext": ext, "subject": subject,
                        "query": str(query), "source": url, "plan": plan}
    return None


async def close_client() -> None:
    global _CLIENT
    if _CLIENT is not None and not _CLIENT.is_closed:
        await _CLIENT.aclose()
    _CLIENT = None
