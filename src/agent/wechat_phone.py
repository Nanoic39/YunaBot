"""Small, bounded WeChat UI driver using the restricted phone bridge."""
from __future__ import annotations

import asyncio
import json
import re
import time
import xml.etree.ElementTree as ET

from .phone import PhoneClient, PhoneError


class WeChatPhone:
    def __init__(self, phone: PhoneClient):
        self.phone = phone

    async def _ui(self) -> str:
        result = await self.phone.action({'action': 'ui'})
        ui = str((result or {}).get('output') or '')
        if not ui.strip():
            raise PhoneError('微信界面为空')
        return ui

    @staticmethod
    def _nodes(ui: str):
        try:
            return list(ET.fromstring(ui).iter('node'))
        except ET.ParseError as error:
            raise PhoneError('微信界面结构无效') from error

    @staticmethod
    def _center(node):
        values = [int(x) for x in re.findall(r'\d+', node.get('bounds', ''))]
        if len(values) != 4:
            return None
        return (values[0] + values[2]) // 2, (values[1] + values[3]) // 2

    async def _tap_node(self, node):
        point = self._center(node)
        if not point:
            raise PhoneError('微信控件位置无效')
        await self.phone.action({'action': 'tap', 'x': point[0], 'y': point[1]})
        await asyncio.sleep(0.5)

    async def _tap_text(self, text: str, *, contains: bool = False):
        ui = await self._ui()
        for node in self._nodes(ui):
            value = (node.get('text') or '').strip()
            if (text in value if contains else value == text) and value:
                await self._tap_node(node)
                return True
        return False

    async def _type(self, text: str):
        result = await self.phone.action({'action': 'text', 'text': text[:2000]})
        if not (result or {}).get('ok'):
            raise PhoneError(str((result or {}).get('error') or '微信输入失败'))
        await asyncio.sleep(0.4)

    async def _tap_key(self, keycode: int):
        await self.phone.action({'action': 'key', 'keycode': int(keycode)})
        await asyncio.sleep(0.5)

    async def _login_screen(self, ui: str) -> bool:
        return '手机号登录' in ui or '请填写微信密码' in ui

    async def _ocr(self) -> list[dict]:
        result = await self.phone.action({'action': 'wechat_ocr'})
        raw = str((result or {}).get('output') or '[]')
        try:
            rows = json.loads(raw)
        except Exception:
            return []
        return [r for r in rows if isinstance(r, dict) and r.get('text')]

    async def send(self, contact: str, message: str) -> bool:
        contact = str(contact or '').strip()
        message = str(message or '').strip()
        if not contact or not message:
            return False
        # 先退回聊天首页再重新启动，避免停留在上一次搜索/聊天页。
        for _ in range(3):
            await self.phone.action({'action': 'key', 'keycode': 4})
            await asyncio.sleep(0.2)
        await self.phone.action({'action': 'launch', 'package': 'com.tencent.mm'})
        await asyncio.sleep(1.0)
        # 微信搜索页不暴露可读文本，使用固定入口 + 搜索结果首行。
        await self.phone.action({'action': 'tap', 'x': 1000, 'y': 225})
        await asyncio.sleep(0.5)
        await self.phone.action({'action': 'tap', 'x': 400, 'y': 160})
        await asyncio.sleep(0.3)
        await self._type(contact)
        await asyncio.sleep(1.0)
        # 搜索页不暴露 XML 文本，改用 OCR 找第一个包含联系人前两字的可见结果。
        probe = contact.replace(' ', '')[:2]
        rows = await self._ocr()
        candidates = [r for r in rows if r.get('y', 0) > 420 and probe and probe in str(r.get('text') or '')]
        if not candidates:
            candidates = [r for r in rows if r.get('y', 0) > 420 and contact[-2:] in str(r.get('text') or '')]
        if not candidates:
            raise PhoneError('微信搜索结果 OCR 未找到联系人')
        target = min(candidates, key=lambda r: int(r.get('y') or 0))
        await self.phone.action({'action': 'tap', 'x': int(target['x']) + int(target['w']) // 2,
                                 'y': int(target['y']) + int(target['h']) // 2})
        await asyncio.sleep(1.0)
        verify = await self._ocr()
        verify_text = ' '.join(str(r.get('text') or '') for r in verify)
        if 'AI搜索' in verify_text or '搜索网络结果' in verify_text:
            raise PhoneError('微信搜索结果点击后仍在搜索页')
        await self.phone.action({'action': 'tap', 'x': 500, 'y': 2580})
        await asyncio.sleep(0.3)
        await self._type(message)
        await asyncio.sleep(0.5)
        # 输入法回车会换行，必须点屏幕上的「发送」。
        send_rows = await self._ocr()
        send = next((r for r in send_rows if '发送' in str(r.get('text') or '')), None)
        if send:
            point = (int(send['x']) + int(send['w']) // 2,
                     int(send['y']) + int(send['h']) // 2)
        else:
            # 键盘弹出时输入栏上方，发送键位置固定；OCR 失败时兜底。
            point = (1105, 1640)
        await self.phone.action({'action': 'tap', 'x': point[0], 'y': point[1]})
        await asyncio.sleep(1.0)
        return True
