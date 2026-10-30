"""共享的手机 Agent 运行时。"""
from __future__ import annotations

from .phone_play import phone_play

_phone_agent = None


def set_phone_agent(agent):
    global _phone_agent
    _phone_agent = agent


def get_phone_agent():
    return _phone_agent
