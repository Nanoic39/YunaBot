"""OpenAI 兼容的最小 LLM 客户端，用于电脑观察-行动循环。"""
from __future__ import annotations

import base64
import json
import re

import httpx


class AgentLLMError(ValueError):
    pass


def parse_json(text: str) -> dict:
    if not isinstance(text, str):
        raise AgentLLMError('模型输出为空')
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find('{'), text.rfind('}')
        if start < 0 or end <= start:
            raise AgentLLMError('模型没有返回 JSON 对象')
        try:
            value = json.loads(text[start:end + 1])
        except json.JSONDecodeError as error:
            raise AgentLLMError('模型返回的 JSON 无法解析') from error
    if not isinstance(value, dict):
        raise AgentLLMError('模型返回的不是 JSON 对象')
    return value


class AgentLLM:
    def __init__(self, base: str, key: str, model: str, *, transport=None, system=None):
        self.base = (base or '').rstrip('/')
        self.key = key or ''
        self.model = model or ''
        self.transport = transport
        self.system = system

    @property
    def available(self) -> bool:
        return bool(self.base and self.key and self.model)

    async def decide(self, prompt: str, screenshot: bytes | None = None) -> dict:
        if not self.available:
            raise AgentLLMError('未配置电脑 Agent LLM')
        content = [{'type': 'text', 'text': prompt}]
        if screenshot:
            encoded = base64.b64encode(screenshot).decode('ascii')
            content.append({'type': 'image_url',
                            'image_url': {'url': 'data:image/png;base64,' + encoded}})
        payload = {'model': self.model, 'temperature': 0.15, 'max_tokens': 1200,
                   'messages': [{'role': 'system', 'content': self.system or (
                       '你是安卓电脑操作代理。只能输出一个 JSON 对象，不要输出 Markdown。'
                       '不要编造界面内容；看不清就用 ui 再观察。')},
                                {'role': 'user', 'content': content}]}
        headers = {'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'}
        async with httpx.AsyncClient(timeout=90, transport=self.transport) as client:
            # 仅重试尚未执行的决策生成；不会重复电脑动作或重新发送秘密。
            for attempt in range(2):
                if attempt:
                    payload['max_tokens'] = 2400
                    payload['messages'][0]['content'] += ' 上次输出格式无效；请只给出一个完整JSON对象，thought不超过80字。'
                response = await client.post(self.base + '/chat/completions', json=payload, headers=headers)
                if response.status_code != 200:
                    raise AgentLLMError(f'电脑 Agent LLM 请求失败（HTTP {response.status_code}）')
                try:
                    text = response.json()['choices'][0]['message']['content']
                except (ValueError, KeyError, IndexError, TypeError) as error:
                    raise AgentLLMError('电脑 Agent LLM 响应格式无效') from error
                if isinstance(text, list):
                    text = ''.join(str(part.get('text', '')) for part in text if isinstance(part, dict))
                try:
                    return parse_json(text)
                except AgentLLMError:
                    if attempt: raise


class AgentChainLLM:
    """按顺序尝试多个 Agent 节点；保留 AgentLLM.decide 的 JSON 语义。"""

    def __init__(self, nodes, system=None):
        self.nodes = [dict(node) for node in (nodes or []) if isinstance(node, dict)]
        self.system = system

    @property
    def available(self) -> bool:
        return any(node.get('base_url') and node.get('model') for node in self.nodes)

    @property
    def base(self):
        return str((self.nodes[0] if self.nodes else {}).get('base_url') or '')

    @property
    def key(self):
        return str((self.nodes[0] if self.nodes else {}).get('api_key') or '')

    @property
    def model(self):
        return str((self.nodes[0] if self.nodes else {}).get('model') or '')

    async def decide(self, prompt: str, screenshot: bytes | None = None) -> dict:
        errors = []
        for node in self.nodes:
            llm = AgentLLM(node.get('base_url', ''), node.get('api_key') or 'local',
                           node.get('model', ''), system=self.system)
            if not llm.available:
                errors.append(f"{node.get('name') or '节点'}: 未配置")
                continue
            try:
                return await llm.decide(prompt, screenshot=screenshot)
            except Exception as error:
                errors.append(f"{node.get('name') or node.get('model')}: {error}")
        raise AgentLLMError('Agent 链路全部失败：' + '；'.join(errors[-3:]))
