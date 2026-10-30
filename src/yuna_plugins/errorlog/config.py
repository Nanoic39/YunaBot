from pydantic import BaseModel


class Config(BaseModel):
    """报错收集（errorlog）插件配置，可通过环境变量 ERRORLOG_* 覆盖。"""

    errorlog_enabled: bool = True  # 是否启用报错收集
    errorlog_max_entries: int = 300  # 最多保留多少条报错记录
    errorlog_view_count: int = 10  # /errlog 默认列出条数
    errorlog_file: str = "errors.json"  # 数据文件（相对插件目录）
    errorlog_dedup_seconds: int = 300  # 相同错误在 N 秒内合并计数
    errorlog_traceback_limit: int = 4000  # 堆栈保存的最大字符数
