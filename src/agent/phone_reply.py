"""自然语言手机时间回复的轻量意图判断。"""
from __future__ import annotations

import re

_APPROVE = re.compile(r'(可以|行|好|同意|批准|准了|玩吧|去玩|ok|OK|嗯嗯|没问题|只能玩|玩一会|就玩)')
_DENY = re.compile(r'(不行|不可以|不同意|不好|不要|不准|不能|拒绝|不想|没空|现在不行)')
_DEFER = re.compile(r'(稍等|等一?会|等会|等一下|晚点|待会|过会|过一会|先(去|干|做)|有事|忙|有空再|再说)')
_LIMIT = re.compile(r'(十分钟|10分钟|一刻钟|十五分钟|15分钟|二十分钟|20分钟|半小时|三十分钟|30分钟|一小时|60分钟|一会|一会儿)')


def _minutes(text: str, default: int):
    match = _LIMIT.search(text)
    if not match:
        return default
    value = match.group(1)
    table = {'十分钟': 10, '10分钟': 10, '一刻钟': 15, '十五分钟': 15, '15分钟': 15,
             '二十分钟': 20, '20分钟': 20, '半小时': 30, '三十分钟': 30, '30分钟': 30,
             '一小时': 60, '60分钟': 60, '一会': 15, '一会儿': 15}
    return table.get(value, default)


def classify_phone_reply(text: str) -> dict | None:
    """返回 approve/deny/defer；无法判断返回 None，不擅自批准。"""
    if not isinstance(text, str):
        return None
    text = re.sub(r'\s+', '', text.replace('，', ',').replace('。', ''))
    if not text:
        return None
    # 明确的推迟优先于“行/不行”，因为“现在不行，等会给你玩”是推迟。
    if _DEFER.search(text):
        if re.search(r'(现在有事|现在忙|先(去|干|做)|忙完|有空再)', text):
            minutes = 45
        elif '半小时' in text:
            minutes = 30
        elif '十分钟' in text:
            minutes = 10
        else:
            minutes = 20
        return {'intent': 'defer', 'minutes': minutes}
    if _DENY.search(text):
        retry = 60 if re.search(r'(没空|现在不行|忙)', text) else None
        return {'intent': 'deny', 'retry_minutes': retry}
    if _APPROVE.search(text) or (_LIMIT.search(text) and not _DENY.search(text)):
        return {'intent': 'approve', 'minutes': _minutes(text, 0)}
    return None
