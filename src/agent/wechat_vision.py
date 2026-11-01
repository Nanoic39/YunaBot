"""Vision-guided WeChat UI actions for Android WeChat, which hides text from uiautomator."""
from __future__ import annotations

import asyncio
import json

from .phone import PhoneClient, PhoneError


class WeChatVision:
    def __init__(self, phone: PhoneClient, llm):
        self.phone = phone
        self.llm = llm

    async def _shot(self) -> bytes:
        return await self.phone.screenshot()

    async def _decide(self, prompt: str, screenshot: bytes) -> dict:
        import base64, io, json as _json
        try:
            from PIL import Image
            image = Image.open(io.BytesIO(screenshot)).convert('RGB')
            image.thumbnail((900, 1900))
            out = io.BytesIO(); image.save(out, format='JPEG', quality=78, optimize=True)
            screenshot = out.getvalue()
            mime = 'image/jpeg'
        except Exception:
            mime = 'image/png'
        if callable(getattr(self.llm, 'chat', None)):
            messages = [
                {'role': 'system', 'content': prompt},
                {'role': 'user', 'content': [
                    {'type': 'text', 'text': '请识别这张微信截图并返回 JSON。'},
                    {'type': 'image_url', 'image_url': {
                        'url': 'data:' + mime + ';base64,' + base64.b64encode(screenshot).decode('ascii'),
                        'detail': 'high'}},
                ]},
            ]
            text = await asyncio.wait_for(self.llm.chat(messages, temperature=0.1,
                                                        max_tokens=500, disable_thinking=True,
                                                        json_mode=True), 75)
            text = str(text or '').strip()
            from .llm import parse_json
            try:
                data = parse_json(text)
            except Exception as error:
                raise PhoneError('微信视觉规划不是 JSON') from error
        else:
            data = await asyncio.wait_for(self.llm.decide(prompt, screenshot=screenshot), 75)
        if not isinstance(data, dict):
            raise PhoneError('微信视觉规划无效')
        return data

    async def send(self, contact: str, message: str) -> bool:
        contact = str(contact or '').strip()
        message = str(message or '').strip()
        if not contact or not message:
            return False
        await self.phone.action({'action': 'launch', 'package': 'com.tencent.mm'})
        await asyncio.sleep(1.2)
        base = (
            '你在看安卓微信截图。只能输出一个 JSON 对象，不要解释。'
            '目标：给联系人发送一条消息。'
            f'联系人：{contact}；消息：{message}。'
            '返回格式：{"state":"home|search|results|chat|other|login|fail","action":"tap|text|done|fail",'
            '"x":0,"y":0,"text":"要输入的文字","reason":"简短原因"}。'
            '坐标使用截图原始像素；输入文字时 action=text，x/y 可省略。'
            '找不到联系人、处于登录页、或不确定时返回 state=fail，不能猜测。'
        )
        for _ in range(9):
            shot = await self._shot()
            data = await self._decide(base, shot)
            state = str(data.get('state') or '')
            action = str(data.get('action') or '')
            if state in ('login', 'fail') or action == 'fail':
                raise PhoneError('微信界面无法可靠定位：' + str(data.get('reason') or state)[:120])
            if action == 'done':
                return True
            if action == 'tap':
                try:
                    x, y = int(data.get('x')), int(data.get('y'))
                except (TypeError, ValueError):
                    raise PhoneError('微信视觉坐标无效')
                if not (0 <= x <= 2000 and 0 <= y <= 3000):
                    raise PhoneError('微信视觉坐标越界')
                await self.phone.action({'action': 'tap', 'x': x, 'y': y})
                await asyncio.sleep(0.7)
            elif action == 'text':
                value = str(data.get('text') or '')
                if not value:
                    raise PhoneError('微信视觉文本为空')
                await self.phone.action({'action': 'text', 'text': value})
                await asyncio.sleep(0.5)
            else:
                await asyncio.sleep(0.5)
        raise PhoneError('微信视觉步骤超限')
