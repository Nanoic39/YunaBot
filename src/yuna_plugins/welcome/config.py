from pydantic import BaseModel

# 配置
class Config(BaseModel):
    # 全局开关(默认：开)
    plugins_welcome_enabled: bool = True
    whitelist_enabled: bool = True # 白名单限制(默认：开) 关闭后bot所在的所有群聊都会触发入群欢迎，不建议关闭
    default_text: str = "欢迎新人加入本群~" # 默认欢迎文本，不使用模板时降级为这里定义的文本
    image_suffix: list[str] = ["gif", "jpg", "jpeg", "png"]

