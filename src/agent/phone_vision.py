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
    def __init__(self, phone: PhoneClient, vision, skill: str = 'phone', grounding: bool = False):
        self.phone = phone
        self.vision = vision
        self.skill = skill
        self.grounding = grounding
        self._scale = (1.0, 1.0)
        self._elements = []

    async def _ground(self) -> tuple[bytes, list[dict]]:
        """OCR/颜色候选 + Set-of-Mark；视觉模型只选编号。"""
        import base64
        from PIL import Image, ImageDraw
        raw = await self.phone.screenshot()
        rows = []
        try:
            result = await self.phone.action({'action': 'wechat_ocr'})
            rows = json.loads(str((result or {}).get('output') or '[]'))
        except Exception:
            rows = []
        elements = []
        for row in rows if isinstance(rows, list) else []:
            try:
                x, y, w, h = (int(row['x']), int(row['y']), int(row['w']), int(row['h']))
                text = str(row.get('text') or '').strip()
            except Exception:
                continue
            if not text or y > 2380 or w < 10 or h < 10:
                continue
            elements.append({'id': len(elements) + 1, 'text': text[:60], 'x': x, 'y': y, 'w': w, 'h': h,
                             'kind': 'text'})
        # 底部绿色发送按钮：绿色背景 + 白色文字，限定输入栏附近。
        try:
            pic = Image.open(io.BytesIO(raw)).convert('RGB')
            width, height = pic.size
            pix = pic.load()
            xs, ys = [], []
            for yy in range(int(height * 0.55), height, 3):
                for xx in range(0, width, 3):
                    r, g, b = pix[xx, yy]
                    if g > 120 and g > r * 1.35 and g > b * 1.25:
                        xs.append(xx); ys.append(yy)
            if len(xs) > 200:
                x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
                if x1 - x0 > 40 and y1 - y0 > 25:
                    elements.append({'id': len(elements) + 1, 'text': '发送（绿色按钮）',
                                     'x': x0, 'y': y0, 'w': x1 - x0, 'h': y1 - y0, 'kind': 'send'})
        except Exception:
            pass
        elements = elements[-35:]
        image = Image.open(io.BytesIO(raw)).convert('RGB')
        draw = ImageDraw.Draw(image)
        for element in elements:
            box = [element['x'], element['y'], element['x'] + element['w'], element['y'] + element['h']]
            draw.rectangle(box, outline=(255, 0, 0), width=4)
            draw.text((element['x'], max(0, element['y'] - 18)), str(element['id']), fill=(255, 0, 0))
        out = io.BytesIO(); image.save(out, format='JPEG', quality=86, optimize=True)
        self._scale = (1.0, 1.0)
        self._elements = elements
        return out.getvalue(), elements

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

    async def _plan(self, goal: str, hint: str, screenshot: bytes, history: list[str], elements=None) -> dict:
        candidates = ''
        if elements:
            candidates = '\n截图上的红框编号候选（优先选择编号，不要猜坐标）：\n' + '\n'.join(
                f"{e['id']}. {e['text']} ({e['x']},{e['y']},{e['w']},{e['h']})" for e in elements[-30:]
            ) + '\n返回格式可改为 {"action":"tap","element":编号,"reason":"..."}。'
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
                + candidates
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
        except Exception:
            pass
        # Gemini 偶尔返回多个 JSON 或夹杂说明；按字段兜底提取一个动作。
        import re
        action = re.search(r'"action"\s*:\s*"([a-z]+)"', raw)
        if not action:
            raise PhoneError('视觉规划不是 JSON')
        out = {'action': action.group(1), 'reason': 'regex-fallback'}
        for key in ('x', 'y'):
            match = re.search(r'"' + key + r'"\s*:\s*"?(-?\d+)(?:\.\d+)?"?', raw)
            if match:
                out[key] = int(match.group(1))
        match = re.search(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
        if match:
            try:
                out['text'] = json.loads('"' + match.group(1) + '"')
            except Exception:
                out['text'] = match.group(1)
        return out

    async def run(self, goal: str, max_steps: int = 14, verify=None) -> list[str]:
        hint = skill_store.hint(self.skill)
        history: list[str] = []
        steps: list[str] = []
        for _ in range(max_steps):
            elements = []
            if self.grounding:
                shot, elements = await self._ground()
            else:
                shot = await self._shot()
            plan = await self._plan(goal, hint, shot, history, elements)
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
                chosen = None
                if plan.get('element') is not None:
                    try:
                        wanted = int(plan.get('element'))
                        chosen = next((e for e in elements if int(e.get('id')) == wanted), None)
                    except (TypeError, ValueError):
                        chosen = None
                if chosen:
                    x = int(chosen['x']) + int(chosen['w']) // 2
                    y = int(chosen['y']) + int(chosen['h']) // 2
                else:
                    try:
                        x, y = int(plan.get('x')), int(plan.get('y'))
                    except (TypeError, ValueError):
                        raise PhoneError('视觉坐标无效：' + str(plan)[:300])
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
