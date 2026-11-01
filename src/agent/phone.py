"""电脑控制桥客户端。桥本身只提供受限的 ADB 动作，不提供任意 shell。"""
from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx

from .aid import aid_store


class PhoneError(ValueError):
    pass


class PhoneClient:
    def __init__(self, url: str | None = None, token: str | None = None, *, transport=None):
        self.url = (url or os.getenv("AGENT_PHONE_URL") or "http://127.0.0.1:8765").rstrip("/")
        self.token = token if token is not None else os.getenv("AGENT_PHONE_TOKEN", "")
        parsed = urlparse(self.url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise PhoneError("电脑桥地址无效")
        if parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise PhoneError("电脑桥只允许本机回环地址")
        self.transport = transport

    def _client(self):
        return httpx.AsyncClient(timeout=120, transport=self.transport, trust_env=False)

    def _headers(self):
        if not self.token:
            raise PhoneError("未配置电脑桥令牌")
        return {"X-Phone-Token": self.token}

    async def health(self) -> dict:
        async with self._client() as client:
            response = await client.get(self.url + "/health", headers=self._headers())
        if response.status_code != 200:
            raise PhoneError(f"电脑桥不可用（HTTP {response.status_code}）")
        data = response.json()
        return {"ok": True, **data, "url": self.url}

    async def observe(self, fast: bool = False) -> dict:
        """一次请求同时取截图、UI 和尺寸；fast=True 时先跳过慢速截图。"""
        import base64
        async with self._client() as client:
            response = await client.get(self.url + "/observe" + ("?fast=1" if fast else ""),
                                        headers=self._headers(), timeout=45)
        if response.status_code != 200:
            raise PhoneError(f"手机观察失败（HTTP {response.status_code}）")
        data = response.json()
        try:
            image = base64.b64decode(data.get("screenshot") or "", validate=True)
            if image and not image.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("invalid observation")
            if not isinstance(data.get("ui"), str):
                raise ValueError("invalid observation")
        except (ValueError, TypeError) as error:
            raise PhoneError("手机观察数据无效") from error
        return {"ok": True, "online": True, "screenshot": image, "ui": data["ui"],
                "size": data.get("size") or "", "activity": data.get("activity") or ""}

    async def screenshot(self) -> bytes:
        async with self._client() as client:
            response = await client.get(self.url + "/screenshot", headers=self._headers())
        if response.status_code != 200:
            raise PhoneError(f"截图失败（HTTP {response.status_code}）")
        if not response.content.startswith(b"\x89PNG\r\n\x1a\n"):
            raise PhoneError("电脑桥返回的不是 PNG")
        return response.content

    async def type_aid(self, aid: str) -> dict:
        """消费一次性协助内容并输入到当前焦点；不在日志/返回值中回显内容。"""
        meta = aid_store.get(aid)
        if not meta or meta['kind'] not in ('sms', 'phone', 'password'):
            raise PhoneError('仅验证码、手机号或密码协助可作为输入使用')
        value = aid_store.take(aid)
        if len(value) > 2000:
            raise PhoneError('协助内容过长')
        result = await self.action({'action': 'text', 'text': value})
        return {'ok': True, 'aid': aid, 'typed': True, **{k: v for k, v in result.items() if k not in ('output',)}}

    async def action(self, action: dict) -> dict:
        if not isinstance(action, dict) or not action.get("action"):
            raise PhoneError("电脑动作无效")
        async with self._client() as client:
            response = await client.post(self.url + "/action", json=action, headers=self._headers())
        try:
            data = response.json()
        except Exception as error:
            raise PhoneError("电脑桥响应无效") from error
        if response.status_code != 200 or not data.get("ok"):
            raise PhoneError(str(data.get("error") or f"电脑动作失败（HTTP {response.status_code}）"))
        return data
