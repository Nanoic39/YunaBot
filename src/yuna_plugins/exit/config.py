from pydantic import BaseModel


class Config(BaseModel):
    plugins_exit_enabled: bool = True  # 全局开关
    whitelist_enabled: bool = True  # 白名单限制，关闭后所有群都会触发
    avatar_enabled: bool = True  # 退群者头像，获取不到则不显示
