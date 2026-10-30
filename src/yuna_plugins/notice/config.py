from pydantic import BaseModel


class Config(BaseModel):
    # 全局开关（默认开）：监听群内禁言/称号/管理员变动并上报
    notice_enabled: bool = True
    # 额外接收上报的 QQ（superusers 恒接收，此列表追加）
    notice_managers: list[str] = []
    # 不上报这些群（群号）
    notice_exception_groups: list[str] = []
