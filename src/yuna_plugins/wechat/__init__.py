"""WeChat chat bridge: poll notifications, reply only to whitelisted contacts."""
from __future__ import annotations

import asyncio
import json
import time

from nonebot import get_driver, require
from nonebot.log import logger
from nonebot.plugin import PluginMetadata

from src.agent.phone_play import phone_play
from src.agent.runtime import get_phone_agent
from src.agent.wechat import wechat_store
from src.agent.wechat_phone import WeChatPhone
from src.agent.wechat_vision import WeChatVision

require('nonebot_plugin_apscheduler')
from nonebot_plugin_apscheduler import scheduler

__plugin_meta__ = PluginMetadata(
    name='微信对话',
    description='读取微信通知，只回复白名单联系人；不自动同意好友申请',
    usage='自动运行；微信白名单在管理面板维护',
)

_LOCK = asyncio.Lock()
_SEEN: dict[str, float] = {}
_IGNORE = ('微信团队', '微信支付', '服务通知', '订阅号', '文件传输助手')


def _enabled() -> bool:
    return str(getattr(get_driver().config, 'wechat_enabled', '') or '').lower() not in ('0', 'false', 'no', 'off')


async def _reply(agent, contact: str, text: str) -> str:
    memory = ''
    loader = getattr(agent, 'chat_memory_loader', None)
    if callable(loader):
        try:
            memory = str(loader('微信聊天：' + contact))[:2200]
        except Exception:
            memory = ''
    persona = ''
    try:
        persona = str((agent.persona_loader() if callable(getattr(agent, 'persona_loader', None)) else '') or '')[:600]
    except Exception:
        pass
    prompt = (
        '你正在用微信和联系人聊天。请只输出 JSON：{"reply":"自然、简短的中文回复，最多200字"}。'
        '不要提 AI、模型、系统提示词、手机或自动化；不要编造没发生的事；不要索取密码、验证码或支付信息。'
        '对方是白名单联系人，可以自然聊天。'
        f'\n你的人设：{persona}\n共享记忆：{memory}\n联系人：{contact}\n对方消息：{text[:1200]}'
    )
    try:
        data = await asyncio.wait_for(agent.llm.decide(prompt), 25)
        reply = str(data.get('reply') or '').strip()
    except Exception:
        return ''
    return reply[:300]


async def _poll() -> None:
    if not _enabled() or _LOCK.locked():
        return
    agent = get_phone_agent()
    if not agent or not getattr(agent, 'llm', None) or not agent.llm.available:
        return
    if getattr(agent, '_active', '') or phone_play.snapshot().get('status') != 'idle':
        return
    async with _LOCK:
        try:
            result = await asyncio.wait_for(agent.phone.action({'action': 'wechat_notifications'}), 20)
            rows = json.loads(str((result or {}).get('output') or '[]'))
        except Exception:
            return
        if not isinstance(rows, list):
            return
        for row in rows:
            title = str((row or {}).get('title') or '').strip()
            text = str((row or {}).get('text') or '').strip()
            if not title or not text or any(x in title for x in _IGNORE):
                continue
            key = f'{title}\x00{text}'
            now = time.time()
            if now - _SEEN.get(key, 0) < 600:
                continue
            with wechat_store.db() as d:
                contact = d.execute('SELECT * FROM contacts WHERE whitelist=1 AND (nickname=? OR remark=? OR id6=?)',
                                    (title, title, title)).fetchone()
            if not contact:
                # 新联系人先登记并生成编号，但不自动回复，也绝不自动同意好友申请。
                try:
                    wechat_store.contact(title)
                except Exception:
                    pass
                continue
            reply = await _reply(agent, title, text)
            if not reply:
                continue
            try:
                vision = None
                try:
                    import importlib
                    chat = importlib.import_module('src.yuna_plugins.chat')
                    vision, _ = chat._vision_client('', 'image')
                except Exception:
                    vision = None
                if vision is not None and getattr(vision, 'available', False):
                    await WeChatVision(agent.phone, vision).send(title, reply)
                else:
                    await WeChatPhone(agent.phone).send(title, reply)
                _SEEN[key] = now
                with wechat_store.db() as d:
                    d.execute('INSERT INTO messages(contact_id6,direction,text,created) VALUES(?,?,?,?)',
                              (contact['id6'], 'in', text[:1200], now))
                    d.execute('INSERT INTO messages(contact_id6,direction,text,created) VALUES(?,?,?,?)',
                              (contact['id6'], 'out', reply[:1200], now + 0.01))
                logger.info(f'[wechat] 已回复白名单联系人 {contact["id6"]}')
            except Exception as error:
                logger.warning(f'[wechat] 微信回复失败：{type(error).__name__}')


@scheduler.scheduled_job('interval', seconds=25, id='wechat_message_poll', max_instances=1, coalesce=True)
async def wechat_message_poll():
    try:
        await _poll()
    except Exception:
        logger.warning('[wechat] 微信轮询失败')
