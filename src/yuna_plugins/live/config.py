from pydantic import BaseModel


class Config(BaseModel):
    # 全局开关
    live_enabled: bool = True
    # 白名单限制：开启后仅 streamers.json 中列出的群生效
    live_whitelist_enabled: bool = True
    # 开播状态轮询间隔（秒）
    live_check_interval_seconds: int = 60
    # 是否启用录播姬 Webhook 接收（POST live_webhook_path）
    live_webhook_enabled: bool = True
    # 录播姬 Webhook 接收路径
    live_webhook_path: str = "/live/webhook"
    # 请求 B 站接口使用的 User-Agent
    live_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    # 本地模板图片文件后缀
    live_image_suffix: list[str] = ["gif", "jpg", "jpeg", "png", "webp", "bmp"]
