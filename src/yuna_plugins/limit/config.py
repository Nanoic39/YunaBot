from pydantic import BaseModel

# 动作类型 -> 中文名（帮助/日志用）
ACTION_LABELS = {
    "warn": "提醒警告",
    "mute": "禁言",
    "kick": "踢出群聊",
}

# 全局配置
class Config(BaseModel):
    # 全局总开关（默认：开）
    limit_enabled: bool = True
    # 额外允许管理本插件的 QQ（superusers 恒有权限，此列表追加）
    limit_managers: list[str] = []
    # 永不检测的群
    limit_exception_groups: list[str] = []
    # 群配置目录（相对本插件目录），每个群一个 <群号>.json
    limit_data_dir: str = "data"
    # 模板文件目录（相对 limit_data_dir），子目录名=模板槽位，槽位内 .txt 为各变体
    limit_template_dir: str = "templates"
    # 模板选择模式（全局默认，可由群 JSON 的 template_mode 覆盖）：
    #   random = 每次随机（默认）；sequence = 按顺序轮换
    limit_template_mode: str = "random"
    # 同一用户触发处罚后的冷却（秒），避免反复禁言/通知
    limit_action_cooldown: int = 60
    # 是否跳过群内的 owner/admin（不处罚管理）
    limit_skip_admin: bool = True
    # 被踢出后再次入群需补禁言的总时长（小时）
    limit_rejoin_mute_hours: int = 72
    # 申诉窗口（分钟）与窗口内最多申诉次数
    limit_appeal_window_minutes: int = 60
    limit_appeal_max: int = 2
    # 被踢出记录 / 申诉记录 / 待处理申诉文件（相对本插件目录）
    limit_kicked_file: str = "kicked.json"
    limit_appeals_file: str = "appeals.json"
    limit_appeal_pending_file: str = "appeal_pending.json"