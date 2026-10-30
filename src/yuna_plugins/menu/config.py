from pydantic import BaseModel


class Config(BaseModel):
    # 全局开关（默认开）
    menu_enabled: bool = True
