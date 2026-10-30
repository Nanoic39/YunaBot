from typing import Optional

from pydantic import BaseModel


class Config(BaseModel):
    """用户画像（pigimage）插件配置，可通过环境变量 PIGIMAGE_* 覆盖。"""

    pigimage_enabled: bool = True

    # LLM 独立配置：留空则复用 chat 插件的全局 LLM 配置
    pigimage_llm_api_base: Optional[str] = None
    pigimage_llm_api_key: Optional[str] = None
    pigimage_llm_model: Optional[str] = None
    pigimage_llm_temperature: float = 0.4
    pigimage_llm_max_tokens: int = 4096

    # 数据采集
    pigimage_max_messages: int = 150  # 最多分析最近多少条发言
    pigimage_days: int = 30  # 只分析最近 N 天的发言

    # 事实依据引用条数范围
    pigimage_quotes_min: int = 15
    pigimage_quotes_max: int = 20

    # 画像缓存有效期（秒），同一用户数据未更新时直接复用
    pigimage_cache_ttl: int = 3600
