"""Small, bounded WeChat UI driver using the restricted phone bridge."""
from __future__ import annotations

import asyncio
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

    async def send(self, contact: str, message: str) -> bool:
        contact = str(contact or '').strip()
        message = str(message or '').strip()
        if not contact or not message:
            return False
        await self.phone.action({'action': 'launch', 'package': 'com.tencent.mm'})
        await asyncio.sleep(1.2)
        ui = await self._ui()
        if await self._login_screen(ui):
            raise PhoneError('微信未登录')
        # 搜索入口：优先按 content-desc/搜索文字，找不到则用右上角固定位置。
        opened = False
        for node in self._nodes(ui):
            desc = (node.get('content-desc') or '') + (node.get('text') or '')
            if '搜索' in desc:
                await self._tap_node(node)
                opened = True
                break
        if not opened:
            await self.phone.action({'action': 'tap', 'x': 1000, 'y': 225})
            await asyncio.sleep(0.6)
        ui = await self._ui()
        edit = next((n for n in self._nodes(ui) if 'EditText' in (n.get('class') or '')), None)
        if not edit:
            raise PhoneError('微信搜索框未找到')
        await self._tap_node(edit)
        await self._type(contact)
        await asyncio.sleep(1.0)
        if not await self._tap_text(contact, contains=True):
            # 搜索结果可能只显示带编号的备注，尝试编号/昵称前缀。
            if not await self._tap_text(contact.split('-')[-1], contains=True):
                raise PhoneError('微信联系人搜索结果未找到')
        await asyncio.sleep(1.0)
        ui = await self._ui()
        if await self._login_screen(ui):
            raise PhoneError('微信未登录')
        edit = next((n for n in self._nodes(ui) if 'EditText' in (n.get('class') or '') and int(re.findall(r'\d+', n.get('bounds', '0,0,0,0'))[1] or 0) > 1500), None)
        if not edit:
            raise PhoneError('微信聊天输入框未找到')
        await self._tap_node(edit)
        await self._type(message)
        # 发送按钮优先精确文字；找不到时点右下角。
        if not await self._tap_text('发送'):
            await self.phone.action({'action': 'tap', 'x': 1100, 'y': 2520})
        await asyncio.sleep(0.8)
        return True
