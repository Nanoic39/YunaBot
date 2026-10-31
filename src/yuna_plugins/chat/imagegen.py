"""可选的 OpenAI 兼容图片生成客户端。

只用于 QQ 空间日常动态的配图；未配置时不发图，不影响文字动态。
"""

from __future__ import annotations

import base64
import hashlib
import time
from pathlib import Path

import httpx
from nonebot.log import logger


class ImageGenerator:
    def __init__(
        self, base: str, key: str, model: str, size: str, directory: Path,
        enabled: bool = True, provider: str = "qwen",
        prompt_suffix: str = "", negative_prompt: str = "",
    ):
        self.base = str(base or "").rstrip("/")
        self.key = str(key or "")
        self.model = str(model or "")
        self.size = str(size or "1024x1024")
        self.directory = Path(directory)
        self.enabled = bool(enabled)
        self.provider = str(provider or "qwen").strip().lower()
        self.prompt_suffix = str(prompt_suffix or "")
        self.negative_prompt = str(negative_prompt or "")

    @property
    def available(self) -> bool:
        return bool(self.enabled and self.base and self.key and self.model)

    def _url(self) -> str:
        if self.base.endswith("/images/generations"):
            return self.base
        if self.base.endswith("/images/edits"):
            return self.base[:-len("/edits")] + "/generations"
        return f"{self.base}/images/generations"

    def _edit_url(self) -> str:
        if self.base.endswith("/images/edits"):
            return self.base
        if self.base.endswith("/images/generations"):
            return self.base[:-len("/generations")] + "/edits"
        return f"{self.base}/images/edits"

    @staticmethod
    def _ext(data: bytes) -> str:
        if data.startswith(b"\x89PNG"):
            return ".png"
        if data.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
            return ".webp"
        return ".png"

    def _save(self, data: bytes) -> str:
        if not data or len(data) > 10 * 1024 * 1024:
            raise RuntimeError("图片为空或超过 10MB")
        self.directory.mkdir(parents=True, exist_ok=True)
        name = f"qzone-{int(time.time())}-{hashlib.sha1(data).hexdigest()[:10]}"
        path = self.directory / f"{name}{self._ext(data)}"
        path.write_bytes(data)
        # 限制缓存目录体积，避免长期运行占满磁盘
        files = sorted(
            (p for p in self.directory.glob("qzone-*") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        for old in files[:-100]:
            try:
                old.unlink()
            except OSError:
                pass
        return str(path)

    @staticmethod
    async def _read_response(response, client) -> str | None:
        if response.status_code >= 400:
            logger.warning(
                f"[imagegen] 图片生成失败 HTTP {response.status_code}: "
                f"{response.text[:180]}"
            )
            return None
        data = response.json()
        item = (data.get("data") or [{}])[0] if isinstance(data, dict) else {}
        if item.get("b64_json"):
            try:
                return base64.b64decode(item["b64_json"]).decode("latin1")
            except Exception:
                return None
        url = item.get("url")
        if url:
            try:
                downloaded = await client.get(url)
                if downloaded.status_code >= 400:
                    return None
                return downloaded.content.decode("latin1")
            except Exception:
                return None
        return None

    async def _generate_gpt(self, text: str, reference_image: str | None, transparent: bool = False) -> str | None:
        headers = {"Authorization": f"Bearer {self.key}"}
        timeout = 300.0
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                ref = Path(reference_image) if reference_image else None
                if ref is not None and ref.is_file() and ref.stat().st_size <= 20 * 1024 * 1024:
                    raw = ref.read_bytes()
                    fields = {
                        "model": self.model,
                        "prompt": text,
                        "n": "1",
                        "size": self.size,
                        **({"background": "transparent", "output_format": "png"} if transparent else {}),
                    }
                    files = {"image": ("reference.png", raw, "image/png")}
                    response = await client.post(self._edit_url(), data=fields, files=files, headers=headers)
                    if response.status_code in (400, 422):
                        fields.pop("background", None)
                        fields.pop("output_format", None)
                        files = {"image": ("reference.png", raw, "image/png")}
                        response = await client.post(self._edit_url(), data=fields, files=files, headers=headers)
                else:
                    payload = {
                        "model": self.model,
                        "prompt": text,
                        "n": 1,
                        "size": self.size,
                        **({"background": "transparent", "output_format": "png"} if transparent else {}),
                    }
                    response = await client.post(self._url(), json=payload, headers=headers)
                    if response.status_code in (400, 422):
                        payload.pop("background", None)
                        payload.pop("output_format", None)
                        response = await client.post(self._url(), json=payload, headers=headers)
                if response.status_code >= 400:
                    logger.warning(f"[imagegen] GPT 图片生成失败 HTTP {response.status_code}: {response.text[:180]}")
                    return None
                data = response.json()
                item = (data.get("data") or [{}])[0] if isinstance(data, dict) else {}
                raw = None
                if item.get("b64_json"):
                    raw = base64.b64decode(item["b64_json"])
                elif item.get("url"):
                    downloaded = await client.get(item["url"])
                    if downloaded.status_code < 400:
                        raw = downloaded.content
                return self._save(raw) if raw else None
        except Exception as error:
            logger.warning(f"[imagegen] GPT 图片生成异常：{error}")
            return None

    async def generate(self, prompt: str, reference_image: str | None = None, transparent: bool = False) -> str | None:
        if not self.available:
            return None
        text = str(prompt or "").strip()[:1200]
        if self.prompt_suffix:
            text = f"{text}, {self.prompt_suffix}"[:1600]
        if not text:
            return None
        headers = {
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        if self.provider in ("gpt", "openai", "gpt-image", "kitool"):
            return await self._generate_gpt(text, reference_image, transparent=transparent)
        payload = {
            "model": self.model,
            "prompt": text,
            "size": self.size,
            "n": 1,
        }
        if self.provider == "qwen":
            payload["negative_prompt"] = self.negative_prompt
            payload["prompt_extend"] = True
            payload["watermark"] = False
            if reference_image:
                try:
                    ref = Path(reference_image)
                    if ref.is_file():
                        raw = ref.read_bytes()
                        if len(raw) <= 10 * 1024 * 1024:
                            b64ref = base64.b64encode(raw).decode("ascii")
                            payload["image"] = [f"data:image/png;base64,{b64ref}"]
                except Exception as error:
                    logger.warning(f"[imagegen] 读取人设参考图失败：{error}")
        else:
            payload["response_format"] = "b64_json"
        try:
            async with httpx.AsyncClient(timeout=600.0 if self.provider == 'qwen' else 240.0, follow_redirects=True) as client:
                response = await client.post(self._url(), json=payload, headers=headers)
                if response.status_code in (400, 422):
                    # 部分服务不支持 response_format，退化为默认 URL 返回
                    payload.pop("response_format", None)
                    response = await client.post(self._url(), json=payload, headers=headers)
                if response.status_code >= 400:
                    logger.warning(
                        f"[imagegen] 图片生成失败 HTTP {response.status_code}: "
                        f"{response.text[:160]}"
                    )
                    return None
                data = response.json()
                item = (data.get("data") or [{}])[0] if isinstance(data, dict) else {}
                if item.get("b64_json"):
                    raw = base64.b64decode(item["b64_json"])
                    return self._save(raw)
                url = item.get("url")
                if url:
                    downloaded = await client.get(url)
                    if downloaded.status_code >= 400:
                        return None
                    return self._save(downloaded.content)
        except Exception as error:
            logger.warning(f"[imagegen] 图片生成异常：{error}")
        return None
