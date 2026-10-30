from pydantic import BaseModel


class Config(BaseModel):
    # 全局开关
    poster_enabled: bool = False
    # 发布目标频道（Telegram channel，支持 @username 或 -100xxxx）
    poster_channel_id: int | str = ""
    # 管理员用户 ID 列表（可审核投稿、执行管理命令）
    poster_admin_ids: list[int] = []
    # 审核消息发送目标（群/频道 ID 或 @username）；留空则私发给每位管理员
    poster_review_chat_id: int | str = ""
    # 投稿 / 取消指令（不含 /）
    poster_command: str = "post"
    poster_cancel_command: str = "cancel"
    # 管理指令（不含 /）
    poster_stats_command: str = "poststats"
    poster_wl_command: str = "postwl"
    poster_bl_command: str = "postbl"
    # 可选标签（投稿人可按钮多选，管理员也可自行输入）
    poster_tags: list[str] = ["NSFW", "文案", "文章"]
    # 匿名投稿标志（/post 后跟这些词即匿名）
    poster_anon_flags: list[str] = ["匿名", "anon"]
    # 白名单（直发）/ 黑名单（直接拒绝），与运行时名单合并
    poster_whitelist_ids: list[int] = []
    poster_blacklist_ids: list[int] = []
    # 投稿流程超时（秒）
    poster_timeout_seconds: int = 600
