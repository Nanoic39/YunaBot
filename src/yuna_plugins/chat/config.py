from pydantic import BaseModel


class Config(BaseModel):
    # 全局开关
    chat_enabled: bool = True
    chat_semantic_targeting_enabled: bool = True
    chat_bot_max_turns: int = 3
    chat_bot_cooldown_seconds: int = 21600

    # 全局 LLM（默认 DeepSeek，OpenAI 兼容接口）
    chat_llm_api_base: str = "https://api.deepseek.com/v1"
    chat_llm_api_key: str = ""
    chat_llm_model: str = "deepseek-flash"
    chat_llm_temperature: float = 0.8
    chat_llm_max_tokens: int = 1024
    # 聊天请求失败重试次数（内容被审查拒绝时不重试，网络/服务异常才重试）
    chat_retry_times: int = 3

    # 视觉模型（deepseek-flash 原生支持识图）；api_base/api_key 留空时沿用上面的 LLM 配置
    chat_vision_enabled: bool = True
    chat_vision_llm_api_base: str = ""
    chat_vision_llm_api_key: str = ""
    chat_vision_llm_model: str = "deepseek-flash"
    # 图片细节级别：auto / low / high / original
    chat_vision_detail: str = "auto"
    # 视觉模式下是否同时携带近期对话里的历史图片
    chat_vision_carry_history: bool = True
    chat_vision_cache_enabled: bool = True
    chat_vision_cache_ttl_hours: int = 168
    chat_vision_cache_max_entries: int = 800
    # 单次携带图片的数量上限，超过则改为分片识图后以文字携带
    chat_vision_max_images: int = 6
    # 分片识图时每次视觉调用最多处理的图片数
    chat_vision_batch_size: int = 3

    # 自动插话：滑动时间窗口内达到最小活跃消息数后，按概率参与讨论。
    # 概率不宜过高，否则会在每一轮闲聊都插话、并频繁接错消息。
    chat_auto_window_seconds: int = 600
    chat_auto_min_messages: int = 4
    chat_auto_probability: float = 0.5

    # 复读检测（3 人起，每次翻倍 3/6/12/24）
    chat_repeat_min_people: int = 3
    chat_repeat_cooldown_seconds: int = 300

    # 自动插话冷却，防止刷屏
    chat_auto_cooldown_seconds: int = 300

    # 冷场应答：提出问题后无人回应，超过该秒数则更容易针对性回复
    chat_question_idle_seconds: int = 180
    chat_question_reply_probability: float = 0.3

    # 撤回错别字重发识别：同一人在窗口内撤回后又发高度相似消息，视为错别字更正，不按真实撤回处理
    chat_recall_typo_window_seconds: int = 120
    chat_recall_typo_similarity: float = 0.75

    # 记忆
    chat_memory_consolidate_min_turns: int = 20
    chat_memory_consolidate_max_chars: int = 8000
    # 记忆固化时单次 LLM 输出的最大 token，需覆盖 facts+summary+personas+favorability+self_growth 的 JSON
    chat_memory_consolidate_max_tokens: int = 4096
    # 两次固化之间的最小时间间隔（秒），避免活跃群短时间反复固化噪声
    chat_memory_consolidate_min_interval_seconds: int = 1800
    chat_memory_max_entries: int = 100
    # 短期事件详情（episodes）保留天数，超期后压缩提炼为长期 facts
    chat_episode_retention_days: int = 30
    # 每次生成时携带的最近对话上下文条数（滚动窗口，独立于固化缓冲）。
    # 过长会稀释注意力、让模型接错消息，30 条足够覆盖一段连续话题。
    chat_memory_recent_turns: int = 30
    # 普通对话里注入提示词的长期记忆上限：条数与总字数都要限制，
    # 否则记忆越多，模型越容易反复翻旧账、把旧事硬塞进当前话题。
    chat_memory_prompt_facts: int = 12
    chat_memory_prompt_events: int = 6
    chat_memory_prompt_max_chars: int = 1200
    # 按当前消息检索相关经历，不使用无关近期事件补满
    chat_memory_retrieval_enabled: bool = True
    # LLM经历选择独立灰度开关；默认关闭，失败回退轻量检索，不影响回复
    chat_self_memory_semantic_enabled: bool = False
    # 每个用户（QQ号）的初始好感度
    chat_favorability_default: float = 5.0
    chat_favor_ledger_enabled: bool = True
    chat_favor_penalty_window_seconds: int = 600
    chat_favor_penalty_cap: float = 6.0
    # 刷好感检测：同一 QQ 在窗口内重复/高度相似的发言被判为刷好感
    chat_favor_farm_window_seconds: int = 120
    chat_favor_farm_similarity: float = 0.85
    chat_favor_farm_penalty: float = 2.0
    chat_favor_farm_cooldown_seconds: int = 120
    # 成长人格（growth_persona.json）每个分桶最多保留的成长点条数
    chat_growth_max_entries: int = 15
    # 注入提示词的成长点条数上限：成长点里常带具体旧事与梗，全量注入会让模型
    # 反复拿旧事当聊天素材、显得只会翻旧账，只取最近少量即可。
    chat_growth_prompt_lines: int = 6
    # 成长点相似度去重阈值：与已有条目高度相似的新成长点会被跳过，避免近义条目堆满分桶导致成长停滞
    chat_growth_dedup_similarity: float = 0.8

    # 对话评测集：按采样率保存真实触发场景（上下文+触发消息+回复），
    # 供人工标注问题类型，并在提示词/模型变更后回归对比。
    chat_eval_sample_enabled: bool = True
    # 每次成功回复的采样概率（0.15 约等于每 7 条存 1 条）
    chat_eval_sample_rate: float = 0.15
    # 评测集最多保留的样本条数（滚动淘汰最旧的）
    chat_eval_max_samples: int = 400
    # 回复长度控制：按触发类型/关系/时段/消息密度给出目标字数区间并注入提示
    chat_reply_length_control: bool = True
    # 主动行为护栏：静默时段内不主动插话、不冷场应答（被 @/私聊仍正常回复），格式 HH:MM-HH:MM
    chat_proactive_quiet_hours: str = ""
    # 统一自我记忆：群聊里知道自己有电脑/手机、记得真实做过的操作（来自 self_memory）
    chat_self_memory_enabled: bool = True
    # 每天 04:30 从近一周电脑/手机经历提炼稳定自我事实（习惯/偏好/技能）
    chat_self_memory_distill: bool = True
    # 模型路由：记忆固化用更便宜的模型（留空=与主模型相同，base/key 复用主配置）
    chat_consolidate_model: str = ""
    # 模型路由：主模型重试用尽后的降级模型（留空=不降级）
    chat_fallback_model: str = ""
    # 成本上限：每日 Token 消耗上限（0=不限）；超过后停止主动插话/冷场应答，被@仍回复
    chat_daily_token_cap: int = 0
    # 模型路由：意图路由/检索判定等 JSON 小任务用便宜模型（留空=主模型）
    chat_utility_model: str = ""

    # 表情：对话时情绪强烈或语气有歧义可额外发一张表情图，不同情绪分目录管理
    chat_emote_enabled: bool = True

    # 表情包主动学习：从群聊其他用户发的图片里识别表情包并收藏（情绪类存 emotes/，含义类存 misc_emoji/）
    chat_emote_learn_enabled: bool = True
    # 每个群每分钟最多用视觉模型判断的新图片数（同一张图按 hash 去重，不重复判断）
    chat_emote_learn_max_per_minute: int = 6
    # 提示词里最多列出的含义表情包名称数量，避免收藏过多后撑大上下文
    chat_emote_misc_max_prompt: int = 40

    # ---- 拟人化 ----
    # 总开关：关闭后不注入心情/关系，也不做拟人化后处理。
    chat_humanize_enabled: bool = True
    # 回复前模拟真人阅读 + 打字耗时；LLM 生成本身也会占用时间，最终会扣除这部分。
    chat_typing_simulation: bool = True
    chat_typing_chars_per_second: float = 9.0
    chat_typing_max_delay: float = 6.0
    chat_read_delay_min: float = 0.45
    chat_read_delay_max: float = 1.8
    # 长回复自然拆成两条的概率（0 关闭）。只对足够长的单条回复生效。
    chat_split_probability: float = 0.25
    # 与最近几条自己的回复过于相似时，追加一次「换一种说法」的重写。
    chat_anti_repeat_enabled: bool = True
    chat_anti_repeat_threshold: float = 0.78
    chat_anti_repeat_window: int = 6
    # 心情/精力状态：随互动、好感与时间缓慢变化，持久化到 human_state.json。
    chat_mood_enabled: bool = True
    # 生活感：缓慢变化的“此刻在做什么”，只作为语气背景，不主动播报
    chat_activity_enabled: bool = True
    # 连续生活状态：地点、活动阶段、移动、物品、计划与待分享事件
    chat_life_enabled: bool = True
    chat_life_share_enabled: bool = True
    chat_life_share_groups: str = ""
    chat_life_share_interval_minutes: int = 20
    chat_life_share_quiet_minutes: int = 45
    chat_life_share_probability: float = 0.35
    chat_activity_interval_minutes: int = 75
    # 根据聊天内容给心情很小的跟随（夸奖/道歉/攻击性话语）
    chat_mood_cues_enabled: bool = True
    # 联网检索：使用 DeepSeek Search API，仅在明确需要实时信息时触发
    chat_web_search_enabled: bool = True
    chat_web_search_auto_decide: bool = True
    chat_web_search_planner_timeout: int = 12
    chat_web_search_max_results: int = 4
    chat_web_search_timeout: int = 20
    chat_web_search_api_base: str = ""
    # 网络流行语/群黑话/非书面说话习惯的长期记忆
    chat_slang_enabled: bool = True
    chat_slang_learn_hours: int = 2
    # 仅从群聊学习；默认不再主动抓取网络热梗。
    chat_slang_web_learning_enabled: bool = False
    chat_slang_web_hours: int = 3
    # 超过该天数没有在对话中使用/反馈的梗自动删除
    chat_slang_stale_days: int = 180
    # 群聊没有命中现成梗时，偶尔注入少量热门梗作语感参考
    chat_slang_suggestion_probability: float = 0.25
    # 低信息量群聊短句用表情回应代替文字
    chat_reaction_only_enabled: bool = True
    chat_reaction_only_probability: float = 0.12
    # 戳一戳：回戳优先，失败时发一句短文本
    chat_poke_enabled: bool = True
    chat_poke_cooldown_seconds: int = 120
    chat_poke_daily_limit: int = 12
    # 深夜模式：深夜回复更短、更软，但不主动催睡。
    chat_night_quiet_enabled: bool = True
    chat_night_start_hour: int = 0
    chat_night_end_hour: int = 7

    # ---- 资料卡 / 生日 / 点赞 / QQ 空间 ----
    # 资料卡：定时/按需从 OneBot 获取昵称、性别、年龄、签名、等级、群名片等
    chat_profile_enabled: bool = True
    chat_profile_refresh_hours: int = 48
    # 把当前对话者的公开资料注入提示词，让回复更具体
    chat_profile_prompt_enabled: bool = True
    # 生日祝福：每天在指定小时检查一次，只发给最近出现过的群
    chat_birthday_enabled: bool = True
    chat_birthday_hour: int = 9
    # 默认只给「眼熟」及以上关系发送生日祝福
    chat_birthday_min_favorability: float = 10.0
    # 资料卡点赞：手动接口始终可用；这里控制是否在互动中自动点赞
    chat_profile_like_enabled: bool = False
    chat_profile_like_probability: float = 0.15
    chat_profile_like_times: int = 10
    chat_profile_like_cooldown_hours: int = 24
    chat_profile_like_daily_limit: int = 10
    # QQ 空间说说：默认关闭，开启后按概率/每日上限主动发一条
    chat_qzone_enabled: bool = False
    chat_qzone_start_hour: int = 10
    chat_qzone_end_hour: int = 23
    # 时间概率：发布后 2 小时 0%，96 小时后最高 80%
    chat_qzone_min_interval_hours: float = 2.0
    chat_qzone_probability_ramp_hours: float = 96.0
    chat_qzone_max_probability: float = 0.8
    # 保留旧配置项以兼容已有 .env；新逻辑不再用它做固定概率
    chat_qzone_probability: float = 0.25
    chat_qzone_daily_limit: int = 1
    # 事件独立概率
    chat_qzone_home_event_probability: float = 0.08
    chat_qzone_outside_event_probability: float = 0.22
    chat_qzone_sleep_onset_probability: float = 0.12
    chat_qzone_sleep_onset_minutes: int = 30
    chat_qzone_event_max_age_minutes: int = 120
    chat_qzone_image_probability: float = 0.35
    chat_qzone_event_image_probability: float = 0.75

    # ---- AI 配图（OpenAI 兼容 /images/generations，留空则不配图）----
    chat_image_enabled: bool = True
    # qwen 使用千问 OpenAI 兼容生图；openai 可用于其他兼容服务
    chat_image_provider: str = "qwen"
    chat_image_api_base: str = "https://maas.qianwenaiapi.com/compatible-mode/v1"
    chat_image_api_key: str = ""
    chat_image_model: str = "qwen-image-3.0-pro"
    chat_image_size: str = "1024x1024"
    # 二次元画风约束
    chat_image_prompt_suffix: str = (
        "anime style, 2D illustration, cel shading, clean line art, "
        "soft colors, wholesome, no photorealistic, no 3D render"
    )
    chat_image_negative_prompt: str = (
        "photorealistic, realistic photo, 3D render, CGI, text, watermark, logo, "
        "low quality, bad anatomy, extra fingers, nsfw"
    )
    # 结合人设参考图生成Q版表情包；显式要求时生成，普通玩笑按概率偶然生成
    chat_self_sticker_enabled: bool = True
    chat_self_sticker_probability: float = 0.12
    chat_self_sticker_cooldown_seconds: int = 1800
    # 空间评论：轮询自己的说说，发现新评论后公开回复
    chat_qzone_comment_enabled: bool = True
    # 空闲轮询间隔；新说说发布后进入 hot window，用更短的间隔
    chat_qzone_comment_interval_minutes: int = 2
    chat_qzone_comment_hot_minutes: int = 10
    chat_qzone_comment_hot_interval_seconds: int = 30
    # 0 表示不限制；需要限流时再填正整数
    chat_qzone_comment_max_per_tick: int = 0
    chat_qzone_comment_max_per_day: int = 0
