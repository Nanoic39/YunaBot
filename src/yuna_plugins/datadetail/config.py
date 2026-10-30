from pydantic import BaseModel


class Config(BaseModel):
    # 全局开关
    datadetail_enabled: bool = True
    # 每日统计数据保留天数，超过自动清理；0 表示永久保留
    datadetail_retention_days: int = 365
    # 词频统计开关（词云的数据来源）；关闭后不再分词，仅记录条数/字数
    datadetail_wordcloud_enabled: bool = True
    # 词云最多展示 / 参与排布的词汇数量
    datadetail_wordcloud_max_words: int = 200
    # 额外停用词，多个词用空格 / 逗号 / 顿号 / 分号分隔
    datadetail_stopwords: str = ""
    # 是否保存聊天原文（按天分片，供重建词频 / 后续分析）
    datadetail_raw_enabled: bool = True
    # 原文保留天数，超过自动清理；0 表示永久保留（通常应短于统计保留期）
    datadetail_raw_retention_days: int = 60
