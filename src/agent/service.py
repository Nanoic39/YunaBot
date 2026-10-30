"""角色规划器后续可调用的提案入口；不提供审批、提额或执行方法。"""
from pathlib import Path
from .control import AgentControl

ROOT = Path(__file__).resolve().parents[2]


def propose_task(task: dict, idempotency_key: str) -> dict:
    """可信内部调用；幂等键应来源于稳定生活事件 ID，而非每次重试随机生成。"""
    return AgentControl(ROOT / 'data' / 'agent' / 'control.sqlite3').propose(
        task, idempotency_key, actor='bot-planner'
    )
