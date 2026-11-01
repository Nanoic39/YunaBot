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

    async def _input_point(self):
        rows = await self._ocr()
        candidates = []
        for row in rows:
            text = str(row.get('text') or '')
            y = int(row.get('y') or 0)
            if 1100 < y < 2300 and any(k in text for k in ('轻触说话', '输入', '按住说话', '发送')):
                candidates.append(row)
        if not candidates:
            candidates = [r for r in rows if 1400 < int(r.get('y') or 0) < 2300]
        if not candidates:
            return 500, 1640
        target = max(candidates, key=lambda r: int(r.get('y') or 0))
        return int(target['x']) + int(target['w']) // 2, int(target['y']) + int(target['h']) // 2

    async def _green_send(self):
        """定位输入栏右侧的绿色发送按钮。"""
        try:
            import io
            from PIL import Image
            raw = await self.phone.screenshot()
            pic = Image.open(io.BytesIO(raw)).convert('RGB')
            width, height = pic.size
            pix = pic.load()
            xs, ys = [], []
            for yy in range(int(height * 0.55), height, 2):
                for xx in range(int(width * 0.60), width, 2):
                    r, g, b = pix[xx, yy]
                    if g > 120 and g > r * 1.35 and g > b * 1.20:
                        xs.append(xx); ys.append(yy)
            if len(xs) < 120:
                return None
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            if x1 - x0 < 35 or y1 - y0 < 25:
                return None
            return (x0 + x1) // 2, (y0 + y1) // 2
        except Exception:
            return None

    async def _verify_sent(self, message: str) -> bool:
        probe = message.replace(' ', '')[:4]
        for _ in range(3):
            rows = await self._ocr()
            still_in_input = any(
                probe and probe in str(r.get('text') or '').replace(' ', '') and int(r.get('y') or 0) > 2300
                for r in rows)
            if not still_in_input:
                return True
            await asyncio.sleep(0.7)
        return False

    async def _open_existing_chat(self, contact: str) -> bool:
        rows = await self._ocr()
        probe = contact.split('|')[0].strip()[:4]
        if not probe:
            return False
        candidates = [r for r in rows if 250 < int(r.get('y') or 0) < 2300 and probe.lower() in str(r.get('text') or '').lower()]
        if not candidates:
            return False
        target = min(candidates, key=lambda r: int(r.get('y') or 0))
        await self.phone.action({'action': 'tap', 'x': int(target['x']) + int(target['w']) // 2,
                                 'y': int(target['y']) + int(target['h']) // 2})
        await asyncio.sleep(0.8)
        return True

    async def send(self, contact: str, message: str) -> bool:
        contact = str(contact or '').strip()
        message = str(message or '').strip()
        if not contact or not message:
            return False
        # 先退回聊天首页再重新启动，避免停留在上一次搜索/聊天页。
        try:
            await self.phone.action({'action': 'force_stop', 'package': 'com.tencent.mm'})
            await asyncio.sleep(0.5)
        except Exception:
            for _ in range(5):
                await self.phone.action({'action': 'key', 'keycode': 4})
                await asyncio.sleep(0.2)
        await self.phone.action({'action': 'launch', 'package': 'com.tencent.mm'})
        await asyncio.sleep(1.0)
        # 已存在的会话优先从聊天列表进入，避免搜索页跳到“选择联系人”。
        if await self._open_existing_chat(contact):
            await asyncio.sleep(0.5)
            point = await self._input_point()
            await self.phone.action({'action': 'tap', 'x': point[0], 'y': point[1]})
            await asyncio.sleep(0.3)
            await self._type(message)
            await asyncio.sleep(0.5)
            green = await self._green_send()
            if green:
                await self.phone.action({'action': 'tap', 'x': green[0], 'y': green[1]})
                await asyncio.sleep(0.8)
                if await self._verify_sent(message):
                    return True
            rows = await self._ocr()
            send = next((r for r in rows if '发送' in str(r.get('text') or '')), None)
            if send:
                x = int(send['x']) + int(send['w']) // 2
                y = int(send['y']) + int(send['h']) // 2
            else:
                text_row = next((r for r in rows if message[:2] in str(r.get('text') or '')), None)
                y = 1640 if text_row and int(text_row.get('y') or 0) < 2000 else 2580
                x = 1105
            await self.phone.action({'action': 'tap', 'x': x, 'y': y})
            await asyncio.sleep(0.7)
            if not await self._verify_sent(message):
                raise PhoneError('微信发送未在聊天区出现')
            return True
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
        point = await self._input_point()
        await self.phone.action({'action': 'tap', 'x': point[0], 'y': point[1]})
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
            point = (1105, 2580)
        await self.phone.action({'action': 'tap', 'x': point[0], 'y': point[1]})
        await asyncio.sleep(1.0)
        if not await self._verify_sent(message):
            raise PhoneError('微信发送未在聊天区出现')
        return True
