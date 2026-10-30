from pydantic import BaseModel


class Config(BaseModel):
    # 全局开关
    morning_enabled: bool = True
    # 默认每日推送时间，格式 "HH:MM:SS" 或 "HH:MM"，默认 07:00:00
    morning_time: str = "07:00:00"
    # 定时任务时区
    morning_timezone: str = "Asia/Shanghai"
    # 白名单限制：开启后仅 groups.json 中启用的群生效
    morning_whitelist_enabled: bool = True
    # 昨日统计活跃用户 Top 显示条数
    morning_top_users: int = 5
    # 默认展示方式：image（图片卡片）/ text（纯文本）
    morning_default_display: str = "image"

    # ---------- 横幅背景图 ----------
    # 横幅背景图开关（图片模式下生效）
    morning_bg_enabled: bool = True
    # 本地背景图目录，留空则使用插件内 backgrounds 目录
    morning_bg_dir: str = ""
    # 本地无图时是否回退到在线二次元图源
    morning_bg_online: bool = True
    # 国内横版二次元图源（优先使用；直链本身即横版，响应快，按序尝试）
    morning_image_urls: list[str] = [
        # UApiPro 电脑端二次元（横版）
        "https://uapis.cn/api/v1/random/image?category=acg&type=pc",
        # 晓文 横屏（h）
        "https://eopfapi.xiaowon.cn/pic?img=h",
    ]

    # ---------- 模块系统 ----------
    # 新群默认启用的模块列表，留空则采用各模块自身的 default_enabled
    morning_modules: list[str] = []
    # 群聊词云最多参与排布的词汇数
    morning_wordcloud_size: int = 200
    # 启动补发窗口（分钟）：机器人离线错过推送后，在此时间内补发；0 表示关闭
    morning_catchup_minutes: int = 60
    # 推送失败重试次数
    morning_send_retries: int = 2

    # ---------- 数据源 ----------
    # 数据源请求超时（秒）
    morning_http_timeout: int = 8
    # 数据源请求重试次数
    morning_http_retries: int = 1
    # 平台热榜 API 地址（DailyHotApi 兼容格式）
    morning_hotlist_url: str = "https://api-hot.imsyy.top"
    # 热榜榜单：bilibili / acfun / zhihu / weibo / douyin 等
    morning_hotlist_boards: list[str] = ["bilibili", "acfun", "douyin"]
    # 每个榜单展示条数
    morning_hotlist_size: int = 5
    # 热榜国内兜底 API（主源失败时按 ?type=<board> 请求），留空则关闭兜底
    # 支持 bilibili / acfun / zhihu / weibo / douyin / toutiao / baidu / 36kr 等
    morning_hotlist_fallback_url: str = "https://uapis.cn/api/v1/misc/hotboard"

    # ---------- 内容模块数据源 ----------
    # 今日新番：B站番剧时间表（国内源，优先使用）
    morning_bilibili_timeline_url: str = "https://api.bilibili.com/pgc/web/timeline"
    # 今日新番兜底：Bangumi 每日放送日历（国内常不可达，仅作兜底）
    morning_bangumi_calendar_url: str = "https://api.bgm.tv/calendar"
    # 音乐与周边：网易云音乐新碟（国内源，优先使用）
    morning_netease_album_url: str = "https://music.163.com/api/album/new"
    # Bangumi v0 条目 API（音乐 type=3），国内兜底，留空则仅使用本地 JSON
    morning_bangumi_subject_url: str = "https://api.bgm.tv/v0/subjects"
    # 每日放送展示条数
    morning_bangumi_size: int = 8
    # 动漫资讯 RSS 源列表
    morning_news_rss: list[str] = []
    # 资讯展示总条数
    morning_news_size: int = 5
    # 资讯国内兜底 RSS 源（主 RSS 全部失败时使用），留空则关闭兜底
    morning_news_rss_fallback: list[str] = [
        "https://rss.injahow.cn/acg17/post/all",
        "https://rss.injahow.cn/acfun/article/74/hotScore/oneWeek",
    ]
    # 音乐与周边：展示最近 N 天内的新碟 / 周边
    morning_music_recent_days: int = 7
    # 手游活动展示条数
    morning_games_size: int = 6
    # 音乐与周边展示条数
    morning_music_size: int = 6

    # ---------- 内容过滤 ----------
    # 命中即过滤的关键词（不区分大小写，作用于热榜标题等文本）
    morning_block_keywords: list[str] = ["r18", "里番", "广告", "推广"]
