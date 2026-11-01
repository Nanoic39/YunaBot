"""Vision-planned phone actions with back-navigation recovery and learned skill hints."""
from __future__ import annotations

import asyncio
import base64
import io
import json
import time

from .phone import PhoneClient, PhoneError
from .skills import skill_store


class PhoneVisionSkill:
    def __init__(self, phone: PhoneClient, vision, skill: str = 'phone'):
        self.phone = phone
        self.vision = vision
        self.skill = skill
        self._scale = (1.0, 1.0)

    async def _shot(self) -> bytes:
        image = await self.phone.screenshot()
        try:
            from PIL import Image
            pic = Image.open(io.BytesIO(image)).convert('RGB')
            original = pic.size
            pic.thumbnail((900, 1900))
            self._scale = (original[0] / max(1, pic.width), original[1] / max(1, pic.height))
            out = io.BytesIO()
            pic.save(out, format='JPEG', quality=80, optimize=True)
            return out.getvalue()
        except Exception:
            self._scale = (1.0, 1.0)
            return image

    async def _plan(self, goal: str, hint: str, screenshot: bytes, history: list[str]) -> dict:
        messages = [
            {'role': 'system', 'content': (
                '你在操作一台真实安卓手机。只能输出 JSON 对象，不要解释。'
                '看截图决定下一步动作。动作：{"action":"tap","x":像素,"y":像素,"reason":"..."}、'
                '{"action":"text","text":"...","reason":"..."}、{"action":"back","reason":"..."}、'
                '{"action":"wait","reason":"..."}、{"action":"done","reason":"..."}、'
                '{"action":"fail","reason":"..."}。'
                '如果当前页面不是目标页面，优先 back 回到上一页，然后再找目标；不要因为一次找不到就放弃。'
                '微信发送：输入框在屏幕底部；输入文字前没有发送按钮；发送按钮在输入框右侧，'
                '绿色背景、白色文字。输入后如果看不到绿色发送按钮，先确认输入框是否有文字，再滚动或点输入框。'
                '不要点击联系人选择页的复选框；联系人选择页不是聊天页，应 back。'
                + ('\n历史成功经验（只作参考）：\n' + hint if hint else '')
            )},
            {'role': 'user', 'content': [
                {'type': 'text', 'text': '当前目标：' + goal + '\n最近动作：' + ' | '.join(history[-6:]) + '\n请给出下一步 JSON。'},
                {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(screenshot).decode('ascii'), 'detail': 'high'}},
            ]},
        ]
        text = await asyncio.wait_for(self.vision.chat(messages, temperature=0.1, max_tokens=350,
                                                       disable_thinking=True, json_mode=True), 75)
        raw = str(text or '').strip()
        from .llm import parse_json
        try:
            return parse_json(raw)
        except Exception as error:
            raise PhoneError('视觉规划不是 JSON') from error

    async def run(self, goal: str, max_steps: int = 14, verify=None) -> list[str]:
        hint = skill_store.hint(self.skill)
        history: list[str] = []
        steps: list[str] = []
        for _ in range(max_steps):
            shot = await self._shot()
            plan = await self._plan(goal, hint, shot, history)
            action = str(plan.get('action') or '')
            reason = str(plan.get('reason') or '')[:160]
            if action == 'done':
                if callable(verify) and not await verify():
                    history.append('done-verify-failed')
                    await self.phone.action({'action': 'key', 'keycode': 4})
                    await asyncio.sleep(0.5)
                    continue
                skill_store.record(self.skill, goal, steps)
                return steps
            if action == 'fail':
                raise PhoneError('视觉操作失败：' + reason)
            if action == 'back':
                await self.phone.action({'action': 'key', 'keycode': 4})
                await asyncio.sleep(0.5)
                history.append('back：' + reason)
                steps.append('back')
            elif action == 'tap':
                try:
                    x, y = int(plan.get('x')), int(plan.get('y'))
                except (TypeError, ValueError):
                    raise PhoneError('视觉坐标无效')
                x = int(round(x * self._scale[0]))
                y = int(round(y * self._scale[1]))
                if not (0 <= x <= 2000 and 0 <= y <= 3000):
                    raise PhoneError('视觉坐标越界')
                await self.phone.action({'action': 'tap', 'x': x, 'y': y})
                await asyncio.sleep(0.7)
                history.append(f'tap({x},{y})：{reason}')
                steps.append(f'tap({x},{y})')
            elif action == 'text':
                value = str(plan.get('text') or '')
                if not value:
                    raise PhoneError('视觉输入为空')
                await self.phone.action({'action': 'text', 'text': value})
                await asyncio.sleep(0.5)
                history.append('text：' + value[:30])
                steps.append('text')
            elif action == 'wait':
                await asyncio.sleep(1.0)
                history.append('wait')
            else:
                await asyncio.sleep(0.5)
                history.append('unknown')
        raise PhoneError('视觉步骤超限')
