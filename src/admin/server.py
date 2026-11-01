from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import time
from datetime import datetime
from collections import deque
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx
import nonebot
import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol
from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from nonebot.exception import IgnoredException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.message import event_preprocessor, run_preprocessor
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.agent.control import AgentControl, ControlError
from src.agent.providers import PROVIDERS, deepseek_balance
from src.agent.phone import PhoneClient, PhoneError
from src.agent.computer import ComputerClient, ComputerAgent
from src.agent.phone_agent import PhoneAgent, RealPhoneAgent, PhoneTaskError
from src.agent.phone_play import PhonePlayError, phone_play
from src.agent.wechat import wechat_store
from src.agent.runtime import set_phone_agent
from src.agent.social import social_store, SocialError
from src.agent.social_runtime import request_setup as request_social_setup, start_setup as start_social_setup
from src.agent.llm import AgentLLM
from src.agent.aid import AidError, aid_store
from src.agent.nodes import NodeQueue
from src.agent.payments import Payments, is_payment
from src.qq_games import GameStore, send_game_reminder, game_phase, phase_name

from .alerts import AlertManager
from .backup import BackupError, BackupManager
from .media import IMAGE_SUFFIXES, MediaCache
from .quality import analyze_chat

VERSION = '2.3.0'
ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / 'data' / 'admin'
STATIC = Path(__file__).parent / 'static'
DOCS = ROOT / 'docs'
ROADMAP_FILE = DOCS / 'ROADMAP.md'
SECRET = re.compile(r'password|token|secret|api_key|credential', re.I)
BOT_NAME_RE = re.compile(r'(机器人|bot|robot|gpt|assistant|骰娘|助手|claude|deepseek|copilot|小冰|豆包|kimi|通义|文心)', re.I)
CHAT_DIR = ROOT / 'src' / 'yuna_plugins' / 'chat'
REJECTED_EMOTE_DIR = CHAT_DIR / 'emotes_rejected'

# 各插件的配置项说明：键为大写环境变量名（与各插件 config.py 的 pydantic 字段一致）。
# 仅用于面板展示与输入校验；元组结构为 (中文名, 帮助, 类型, 分组)。
# 类型：bool / int / float / str / secret；list 型字段在 .env 中为 JSON 文本，按 str 处理。
PLUGIN_CONFIG_META = {
    'chat': {
        'CHAT_ENABLED': ('总开关', '关闭后 chat 插件不再响应任何消息', 'bool', '基础'),
        'CHAT_LLM_API_BASE': ('LLM 接口地址', 'OpenAI 兼容接口的 base_url', 'str', '模型'),
        'CHAT_LLM_API_KEY': ('LLM 密钥', '留空沿用原值', 'secret', '模型'),
        'CHAT_LLM_MODEL': ('LLM 模型名', 'deepseek-flash 当前指向 V4.1-Flash-1017', 'str', '模型'),
        'CHAT_LLM_TEMPERATURE': ('采样温度', '0~2，越大越发散', 'float', '模型'),
        'CHAT_LLM_MAX_TOKENS': ('最大回复长度', '单次生成的最大 token 数', 'int', '模型'),
        'CHAT_RETRY_TIMES': ('失败重试次数', '网络/服务异常时的重试次数', 'int', '模型'),
        'CHAT_VISION_ENABLED': ('启用视觉', '识图能力总开关', 'bool', '视觉'),
        'CHAT_VISION_LLM_API_BASE': ('视觉接口地址', '留空沿用 LLM 配置', 'str', '视觉'),
        'CHAT_VISION_LLM_API_KEY': ('视觉密钥', '留空沿用 LLM 配置', 'secret', '视觉'),
        'CHAT_VISION_LLM_MODEL': ('视觉模型名', '需支持图片输入', 'str', '视觉'),
        'CHAT_VISION_DETAIL': ('图片细节级别', 'auto / low / high / original', 'str', '视觉'),
        'CHAT_VISION_CARRY_HISTORY': ('携带历史图片', '是否携带近期对话里的图片', 'bool', '视觉'),
        'CHAT_VISION_MAX_IMAGES': ('单次图片上限', '超过则改为分片识图', 'int', '视觉'),
        'CHAT_VISION_BATCH_SIZE': ('分片批量大小', '每次视觉调用处理的图片数', 'int', '视觉'),
        'CHAT_AUTO_WINDOW_SECONDS': ('插话统计窗口', '滑动窗口秒数', 'int', '自动插话'),
        'CHAT_AUTO_MIN_MESSAGES': ('插话最小消息数', '窗口内达到该数量后才考虑插话', 'int', '自动插话'),
        'CHAT_AUTO_PROBABILITY': ('插话概率', '0~1', 'float', '自动插话'),
        'CHAT_AUTO_COOLDOWN_SECONDS': ('插话冷却', '两次插话之间的最小间隔秒数', 'int', '自动插话'),
        'CHAT_REPEAT_MIN_PEOPLE': ('复读参与人数', '达到该人数判定为复读', 'int', '复读'),
        'CHAT_REPEAT_COOLDOWN_SECONDS': ('复读冷却', '秒', 'int', '复读'),
        'CHAT_QUESTION_IDLE_SECONDS': ('提问冷场判定', '无人回应超过该秒数', 'int', '问答'),
        'CHAT_QUESTION_REPLY_PROBABILITY': ('冷场回复概率', '0~1', 'float', '问答'),
        'CHAT_RECALL_TYPO_WINDOW_SECONDS': ('错别字判定窗口', '秒', 'int', '撤回'),
        'CHAT_RECALL_TYPO_SIMILARITY': ('错别字相似度', '0~1', 'float', '撤回'),
        'CHAT_MEMORY_CONSOLIDATE_MIN_TURNS': ('固化最小轮数', '达到该轮数触发记忆固化', 'int', '记忆'),
        'CHAT_MEMORY_CONSOLIDATE_MAX_CHARS': ('固化最大字数', '触发固化的字数阈值', 'int', '记忆'),
        'CHAT_MEMORY_CONSOLIDATE_MAX_TOKENS': ('固化输出上限', '固化时 LLM 最大输出 token', 'int', '记忆'),
        'CHAT_MEMORY_CONSOLIDATE_MIN_INTERVAL_SECONDS': ('固化最小间隔', '两次固化之间的秒数', 'int', '记忆'),
        'CHAT_MEMORY_MAX_ENTRIES': ('记忆最大条数', '单个记忆文件保留的条数上限', 'int', '记忆'),
        'CHAT_EPISODE_RETENTION_DAYS': ('事件保留天数', '超过后压缩为长期事实', 'int', '记忆'),
        'CHAT_MEMORY_RECENT_TURNS': ('上下文轮数', '生成时携带的最近对话条数', 'int', '记忆'),
        'CHAT_MEMORY_PROMPT_FACTS': ('提示词事实条数', '注入提示词的长期事实条数上限', 'int', '记忆'),
        'CHAT_MEMORY_PROMPT_EVENTS': ('提示词事件条数', '注入提示词的近期事件条数上限', 'int', '记忆'),
        'CHAT_MEMORY_PROMPT_MAX_CHARS': ('提示词记忆字数', '注入提示词的记忆总字数上限', 'int', '记忆'),
        'CHAT_MEMORY_RETRIEVAL_ENABLED': ('记忆检索', '按当前消息关键词挑出相关近期事件', 'bool', '记忆'),
        'CHAT_FAVORABILITY_DEFAULT': ('初始好感度', '新用户的默认好感度', 'float', '好感度'),
        'CHAT_FAVOR_FARM_WINDOW_SECONDS': ('刷好感窗口', '秒', 'int', '好感度'),
        'CHAT_FAVOR_FARM_SIMILARITY': ('刷好感相似度', '0~1', 'float', '好感度'),
        'CHAT_FAVOR_FARM_PENALTY': ('刷好感惩罚', '好感度扣减量', 'float', '好感度'),
        'CHAT_FAVOR_FARM_COOLDOWN_SECONDS': ('刷好感冷却', '秒', 'int', '好感度'),
        'CHAT_GROWTH_MAX_ENTRIES': ('成长点上限', '每个分桶最多保留的条数', 'int', '成长'),
        'CHAT_GROWTH_PROMPT_LINES': ('提示词成长条数', '注入提示词的成长点条数上限', 'int', '成长'),
        'CHAT_GROWTH_DEDUP_SIMILARITY': ('成长去重阈值', '0~1', 'float', '成长'),
        'CHAT_EMOTE_ENABLED': ('启用表情', '对话中可附发表情图', 'bool', '表情'),
        'CHAT_EMOTE_LEARN_ENABLED': ('表情包学习', '从群聊图片中识别并收藏表情包', 'bool', '表情'),
        'CHAT_EMOTE_LEARN_MAX_PER_MINUTE': ('学习频率上限', '每群每分钟最多判断的新图片数', 'int', '表情'),
        'CHAT_EMOTE_MISC_MAX_PROMPT': ('含义表情上限', '提示词里最多列出的名称数', 'int', '表情'),
        'CHAT_HUMANIZE_ENABLED': ('拟人化总开关', '关闭后不注入心情/关系，也不做拟人化后处理', 'bool', '拟人'),
        'CHAT_TYPING_SIMULATION': ('打字延迟', '回复前模拟真人阅读与打字耗时', 'bool', '拟人'),
        'CHAT_TYPING_CHARS_PER_SECOND': ('打字速度', '每秒输入字数，越小越慢', 'float', '拟人'),
        'CHAT_TYPING_MAX_DELAY': ('最大延迟', '单条回复最多等待的秒数', 'float', '拟人'),
        'CHAT_READ_DELAY_MIN': ('最短阅读延迟', '看到消息后至少停顿的秒数', 'float', '拟人'),
        'CHAT_READ_DELAY_MAX': ('最长阅读延迟', '看到消息后最多停顿的秒数', 'float', '拟人'),
        'CHAT_SPLIT_PROBABILITY': ('自然分条概率', '长回复自动拆成两条的概率，0 关闭', 'float', '拟人'),
        'CHAT_ANTI_REPEAT_ENABLED': ('防重复', '与最近回复过于相似时重写一次', 'bool', '拟人'),
        'CHAT_ANTI_REPEAT_THRESHOLD': ('重复阈值', '0~1，越高越宽松', 'float', '拟人'),
        'CHAT_ANTI_REPEAT_WINDOW': ('重复窗口', '比较最近多少条自己的回复', 'int', '拟人'),
        'CHAT_MOOD_ENABLED': ('心情状态', '根据互动、好感与时间维护心情', 'bool', '拟人'),
        'CHAT_NIGHT_QUIET_ENABLED': ('深夜模式', '深夜回复更短、更软', 'bool', '拟人'),
        'CHAT_NIGHT_START_HOUR': ('深夜开始', '0~23，含该小时', 'int', '拟人'),
        'CHAT_NIGHT_END_HOUR': ('深夜结束', '0~23，不含该小时', 'int', '拟人'),
        'CHAT_LIFE_ENABLED': ('连续生活系统', '地点、活动阶段、移动、物品与计划', 'bool', '拟人'),
        'CHAT_LIFE_SHARE_ENABLED': ('生活事件分享', '安静时把完成的小事作为话题素材', 'bool', '拟人'),
        'CHAT_LIFE_SHARE_GROUPS': ('分享群号', '逗号分隔；留空不主动分享', 'str', '拟人'),
        'CHAT_LIFE_SHARE_INTERVAL_MINUTES': ('分享检查间隔', '分钟', 'int', '拟人'),
        'CHAT_LIFE_SHARE_QUIET_MINUTES': ('群安静阈值', '分钟没有消息才分享', 'int', '拟人'),
        'CHAT_LIFE_SHARE_PROBABILITY': ('分享概率', '0~1', 'float', '拟人'),
        'CHAT_PROFILE_ENABLED': ('资料卡同步', '定时/按需获取公开资料', 'bool', '资料与社交'),
        'CHAT_PROFILE_REFRESH_HOURS': ('资料刷新间隔', '小时；避免频繁调用接口', 'int', '资料与社交'),
        'CHAT_PROFILE_PROMPT_ENABLED': ('资料注入提示词', '让回复能自然使用对方公开资料', 'bool', '资料与社交'),
        'CHAT_BIRTHDAY_ENABLED': ('生日祝福', '当天在最近出现过的群发送祝福', 'bool', '资料与社交'),
        'CHAT_BIRTHDAY_HOUR': ('生日检查时间', '0~23 点', 'int', '资料与社交'),
        'CHAT_BIRTHDAY_MIN_FAVORABILITY': ('生日最低好感', '默认 10（眼熟以上）', 'float', '资料与社交'),
        'CHAT_FAVOR_LEDGER_ENABLED': ('好感扣分账本', '记录扣分原因、阈值与申诉', 'bool', '资料与社交'),
        'CHAT_FAVOR_PENALTY_WINDOW_SECONDS': ('扣分窗口', '同一用户短期去重窗口，秒', 'int', '资料与社交'),
        'CHAT_FAVOR_PENALTY_CAP': ('窗口扣分次数', '同一用户窗口内最多扣几次，默认 6', 'float', '资料与社交'),
        'CHAT_PROFILE_LIKE_ENABLED': ('自动点赞', '互动时按概率给资料卡点赞', 'bool', '资料与社交'),
        'CHAT_PROFILE_LIKE_PROBABILITY': ('点赞概率', '0~1', 'float', '资料与社交'),
        'CHAT_PROFILE_LIKE_TIMES': ('点赞次数', '单次点赞次数，1~20', 'int', '资料与社交'),
        'CHAT_PROFILE_LIKE_COOLDOWN_HOURS': ('点赞冷却', '同一用户多少小时内不重复点赞', 'int', '资料与社交'),
        'CHAT_PROFILE_LIKE_DAILY_LIMIT': ('自动点赞每日上限', '仅约束自动点赞；手动/用户请求按关系额度，QQ 每天最多 10 个', 'int', '资料与社交'),
        'CHAT_QZONE_ENABLED': ('QQ空间说说', '默认关闭；开启后主动发空间动态', 'bool', '资料与社交'),
        'CHAT_QZONE_START_HOUR': ('空间活跃开始', '0~23', 'int', '资料与社交'),
        'CHAT_QZONE_END_HOUR': ('空间活跃结束', '0~23', 'int', '资料与社交'),
        'CHAT_QZONE_PROBABILITY': ('旧版固定概率', '已由时间概率取代，仅保留兼容', 'float', '资料与社交'),
        'CHAT_QZONE_DAILY_LIMIT': ('空间每日上限', '每天最多几条', 'int', '资料与社交'),
        'CHAT_QZONE_MIN_INTERVAL_HOURS': ('空间概率起点', '发布后多少小时内概率为 0', 'float', '资料与社交'),
        'CHAT_QZONE_PROBABILITY_RAMP_HOURS': ('空间概率爬升', '多少小时后达到最高概率', 'float', '资料与社交'),
        'CHAT_QZONE_MAX_PROBABILITY': ('空间最高概率', '默认 0.8，即 80%', 'float', '资料与社交'),
        'CHAT_QZONE_HOME_EVENT_PROBABILITY': ('在家事件动态', '写作/绘画等结束后的小概率作品动态', 'float', '资料与社交'),
        'CHAT_QZONE_OUTSIDE_EVENT_PROBABILITY': ('外出事件动态', '外出/吃饭等中等概率动态', 'float', '资料与社交'),
        'CHAT_QZONE_SLEEP_ONSET_PROBABILITY': ('睡前动态概率', '刚准备睡的一小段时间内', 'float', '资料与社交'),
        'CHAT_QZONE_SLEEP_ONSET_MINUTES': ('睡前宽限', '准备睡后多少分钟内仍可能发', 'int', '资料与社交'),
        'CHAT_QZONE_EVENT_MAX_AGE_MINUTES': ('事件有效期', '事件结束后多久内可发作品', 'int', '资料与社交'),
        'CHAT_QZONE_IMAGE_PROBABILITY': ('普通动态配图', '普通说说带 AI 图的概率', 'float', '资料与社交'),
        'CHAT_QZONE_EVENT_IMAGE_PROBABILITY': ('事件动态配图', '作品/外出事件带 AI 图的概率', 'float', '资料与社交'),
        'CHAT_IMAGE_ENABLED': ('AI 配图', '开启后按概率生成配图', 'bool', '资料与社交'),
        'CHAT_IMAGE_PROVIDER': ('图片服务', 'qwen 或 openai 兼容', 'str', '资料与社交'),
        'CHAT_IMAGE_API_BASE': ('图片 API 地址', '千问默认已填，其他服务自行替换', 'str', '资料与社交'),
        'CHAT_IMAGE_API_KEY': ('图片 API Key', '留空则不启用；千问用 DashScope Key', 'secret', '资料与社交'),
        'CHAT_IMAGE_MODEL': ('图片模型', '默认 qwen-image-3.0-pro', 'str', '资料与社交'),
        'CHAT_IMAGE_SIZE': ('图片尺寸', '例如 1024x1024', 'str', '资料与社交'),
        'CHAT_IMAGE_PROMPT_SUFFIX': ('画风后缀', '追加到所有配图提示词，默认二次元', 'str', '资料与社交'),
        'CHAT_IMAGE_NEGATIVE_PROMPT': ('反向提示词', '排除写实/3D/文字/水印', 'str', '资料与社交'),
        'CHAT_QZONE_COMMENT_ENABLED': ('空间评论回复', '轮询自己说说，发现新评论后公开回复', 'bool', '资料与社交'),
        'CHAT_QZONE_COMMENT_INTERVAL_MINUTES': ('评论空闲轮询', '分钟；默认 2 分钟', 'int', '资料与社交'),
        'CHAT_QZONE_COMMENT_HOT_MINUTES': ('评论快速轮询时长', '新说说发布后多少分钟内加速检查', 'int', '资料与社交'),
        'CHAT_QZONE_COMMENT_HOT_INTERVAL_SECONDS': ('评论快速间隔', '秒；hot window 内的轮询间隔', 'int', '资料与社交'),
        'CHAT_QZONE_COMMENT_MAX_PER_TICK': ('单轮最多回复', '0 表示不限制；正整数才启用限流', 'int', '资料与社交'),
        'CHAT_QZONE_COMMENT_MAX_PER_DAY': ('每日最多回复', '0 表示不限制；正整数才启用限流', 'int', '资料与社交'),
    },
    'limit': {
        'LIMIT_ENABLED': ('总开关', '关闭后 limit 插件不再检测', 'bool', '基础'),
        'LIMIT_MANAGERS': ('附加管理员', '额外允许管理本插件的 QQ，JSON 数组', 'str', '基础'),
        'LIMIT_EXCEPTION_GROUPS': ('豁免群', '永不检测的群号，JSON 数组', 'str', '基础'),
        'LIMIT_DATA_DIR': ('群配置目录', '相对插件目录，每群一个 JSON', 'str', '基础'),
        'LIMIT_TEMPLATE_DIR': ('模板目录', '相对 data 目录，子目录为模板槽位', 'str', '基础'),
        'LIMIT_TEMPLATE_MODE': ('模板模式', 'random 每次随机 / sequence 顺序轮换', 'str', '基础'),
        'LIMIT_ACTION_COOLDOWN': ('处罚冷却', '同一用户触发处罚后的冷却秒数', 'int', '处罚'),
        'LIMIT_SKIP_ADMIN': ('跳过管理', '是否跳过群内 owner/admin', 'bool', '处罚'),
        'LIMIT_REJOIN_MUTE_HOURS': ('再入群补禁言', '被踢后再入群补禁言总时长（小时）', 'int', '处罚'),
        'LIMIT_APPEAL_WINDOW_MINUTES': ('申诉窗口', '分钟', 'int', '申诉'),
        'LIMIT_APPEAL_MAX': ('申诉上限', '窗口内最多申诉次数', 'int', '申诉'),
        'LIMIT_KICKED_FILE': ('踢出记录文件', '相对插件目录', 'str', '申诉'),
        'LIMIT_APPEALS_FILE': ('申诉记录文件', '相对插件目录', 'str', '申诉'),
        'LIMIT_APPEAL_PENDING_FILE': ('待处理申诉文件', '相对插件目录', 'str', '申诉'),
    },
    'live': {
        'LIVE_ENABLED': ('总开关', '', 'bool', '基础'),
        'LIVE_WHITELIST_ENABLED': ('白名单限制', '开启后仅 streamers.json 中的群生效', 'bool', '基础'),
        'LIVE_CHECK_INTERVAL_SECONDS': ('轮询间隔', '开播状态轮询间隔秒数', 'int', '基础'),
        'LIVE_WEBHOOK_ENABLED': ('启用 Webhook', '是否接收录播姬 Webhook', 'bool', '基础'),
        'LIVE_WEBHOOK_PATH': ('Webhook 路径', '录播姬 POST 的接收路径', 'str', '基础'),
        'LIVE_USER_AGENT': ('User-Agent', '请求 B 站接口使用', 'str', '基础'),
        'LIVE_IMAGE_SUFFIX': ('图片后缀', '本地模板图片文件后缀，JSON 数组', 'str', '基础'),
    },
    'notice': {
        'NOTICE_ENABLED': ('总开关', '监听群内禁言/称号/管理员变动并上报', 'bool', '基础'),
        'NOTICE_MANAGERS': ('附加接收者', '额外接收上报的 QQ，JSON 数组', 'str', '基础'),
        'NOTICE_EXCEPTION_GROUPS': ('豁免群', '不上报的群号，JSON 数组', 'str', '基础'),
    },
    'morninginfo': {
        'MORNING_ENABLED': ('总开关', '', 'bool', '基础'),
        'MORNING_TIME': ('推送时间', '格式 HH:MM:SS 或 HH:MM', 'str', '基础'),
        'MORNING_TIMEZONE': ('时区', '例如 Asia/Shanghai', 'str', '基础'),
        'MORNING_WHITELIST_ENABLED': ('白名单限制', '开启后仅 groups.json 中启用群生效', 'bool', '基础'),
        'MORNING_TOP_USERS': ('活跃用户 Top', '昨日统计活跃用户展示条数', 'int', '基础'),
        'MORNING_DEFAULT_DISPLAY': ('默认展示', 'image 图片卡片 / text 纯文本', 'str', '基础'),
        'MORNING_BG_ENABLED': ('横幅背景', '图片模式下横幅背景开关', 'bool', '横幅'),
        'MORNING_BG_DIR': ('背景图目录', '留空使用插件内 backgrounds 目录', 'str', '横幅'),
        'MORNING_BG_ONLINE': ('在线图源回退', '本地无图时回退在线二次元图源', 'bool', '横幅'),
        'MORNING_IMAGE_URLS': ('图源列表', '国内横版二次元图源，JSON 数组', 'str', '横幅'),
        'MORNING_MODULES': ('默认模块', '新群默认启用模块，JSON 数组', 'str', '模块'),
        'MORNING_WORDCLOUD_SIZE': ('词云词数', '群聊词云最多参与排布词数', 'int', '模块'),
        'MORNING_CATCHUP_MINUTES': ('补发窗口', '离线错过推送后的补发分钟数，0 关闭', 'int', '模块'),
        'MORNING_SEND_RETRIES': ('推送重试', '推送失败重试次数', 'int', '模块'),
        'MORNING_HTTP_TIMEOUT': ('请求超时', '数据源请求超时秒数', 'int', '数据源'),
        'MORNING_HTTP_RETRIES': ('请求重试', '数据源请求重试次数', 'int', '数据源'),
        'MORNING_HOTLIST_URL': ('热榜地址', 'DailyHotApi 兼容格式', 'str', '数据源'),
        'MORNING_HOTLIST_BOARDS': ('热榜榜单', 'JSON 数组，如 ["bilibili","weibo"]', 'str', '数据源'),
        'MORNING_HOTLIST_SIZE': ('榜单条数', '每个榜单展示条数', 'int', '数据源'),
        'MORNING_HOTLIST_FALLBACK_URL': ('热榜兜底', '主源失败时按 ?type=<board> 请求', 'str', '数据源'),
    },
    'poster': {
        'POSTER_ENABLED': ('总开关', 'Telegram 投稿推送插件', 'bool', '基础'),
        'POSTER_CHANNEL_ID': ('发布频道', 'Telegram channel，@username 或 -100xxxx', 'str', '基础'),
        'POSTER_ADMIN_IDS': ('管理员', '可审核投稿的用户 ID，JSON 数组', 'str', '基础'),
        'POSTER_REVIEW_CHAT_ID': ('审核目标', '审核消息发送目标，留空私发管理员', 'str', '基础'),
        'POSTER_COMMAND': ('投稿指令', '不含 /', 'str', '基础'),
        'POSTER_CANCEL_COMMAND': ('取消指令', '不含 /', 'str', '基础'),
        'POSTER_STATS_COMMAND': ('统计指令', '查看投稿统计', 'str', '基础'),
        'POSTER_WL_COMMAND': ('白名单指令', '', 'str', '基础'),
        'POSTER_BL_COMMAND': ('黑名单指令', '', 'str', '基础'),
        'POSTER_TAGS': ('可选标签', 'JSON 数组', 'str', '基础'),
        'POSTER_ANON_FLAGS': ('匿名标志', 'JSON 数组', 'str', '基础'),
        'POSTER_WHITELIST_IDS': ('白名单', '直发用户 ID，JSON 数组', 'str', '基础'),
        'POSTER_BLACKLIST_IDS': ('黑名单', '直接拒绝用户 ID，JSON 数组', 'str', '基础'),
        'POSTER_TIMEOUT_SECONDS': ('投稿超时', '秒', 'int', '基础'),
    },
    'welcome': {
        'PLUGINS_WELCOME_ENABLED': ('总开关', '', 'bool', '基础'),
        'WHITELIST_ENABLED': ('白名单限制', '关闭后所有群都会触发入群欢迎', 'bool', '基础'),
        'DEFAULT_TEXT': ('默认欢迎文本', '不使用模板时的降级文本', 'str', '基础'),
        'IMAGE_SUFFIX': ('图片后缀', '模板图片文件后缀，JSON 数组', 'str', '基础'),
    },
    'exit': {
        'PLUGINS_EXIT_ENABLED': ('总开关', '', 'bool', '基础'),
        'WHITELIST_ENABLED': ('白名单限制', '关闭后所有群都会触发退群提示', 'bool', '基础'),
        'AVATAR_ENABLED': ('退群头像', '展示退群者头像，获取不到则不显示', 'bool', '基础'),
    },
    'errorlog': {
        'ERRORLOG_ENABLED': ('总开关', '报错收集插件', 'bool', '基础'),
        'ERRORLOG_MAX_ENTRIES': ('最大条数', '最多保留的报错记录数', 'int', '基础'),
        'ERRORLOG_VIEW_COUNT': ('默认列出', '/errlog 默认列出条数', 'int', '基础'),
        'ERRORLOG_FILE': ('数据文件', '相对插件目录', 'str', '基础'),
        'ERRORLOG_DEDUP_SECONDS': ('去重窗口', '相同错误在 N 秒内合并计数', 'int', '基础'),
        'ERRORLOG_TRACEBACK_LIMIT': ('堆栈上限', '堆栈保存的最大字符数', 'int', '基础'),
    },
    'datadetail': {
        'DATADETAIL_ENABLED': ('总开关', '', 'bool', '基础'),
        'DATADETAIL_RETENTION_DAYS': ('统计保留天数', '0 永久保留', 'int', '基础'),
        'DATADETAIL_WORDCLOUD_ENABLED': ('词频开关', '关闭后仅记录条数/字数', 'bool', '基础'),
        'DATADETAIL_WORDCLOUD_MAX_WORDS': ('词云词数', '最多展示/排布词汇数', 'int', '基础'),
        'DATADETAIL_STOPWORDS': ('停用词', '空格/逗号/顿号/分号分隔', 'str', '基础'),
        'DATADETAIL_RAW_ENABLED': ('原文保存', '是否按天保存聊天原文', 'bool', '基础'),
        'DATADETAIL_RAW_RETENTION_DAYS': ('原文保留天数', '0 永久保留', 'int', '基础'),
    },
    'pigimage': {
        'PIGIMAGE_ENABLED': ('总开关', '用户画像插件', 'bool', '基础'),
        'PIGIMAGE_LLM_API_BASE': ('LLM 地址', '留空复用 chat 全局 LLM 配置', 'str', '模型'),
        'PIGIMAGE_LLM_API_KEY': ('LLM 密钥', '留空复用 chat 全局 LLM 配置', 'secret', '模型'),
        'PIGIMAGE_LLM_MODEL': ('LLM 模型', '留空复用 chat 全局 LLM 配置', 'str', '模型'),
        'PIGIMAGE_LLM_TEMPERATURE': ('采样温度', '0~2', 'float', '模型'),
        'PIGIMAGE_LLM_MAX_TOKENS': ('最大输出', '单次生成最大 token 数', 'int', '模型'),
        'PIGIMAGE_MAX_MESSAGES': ('分析消息数', '最多分析最近多少条发言', 'int', '数据'),
        'PIGIMAGE_DAYS': ('分析天数', '只分析最近 N 天发言', 'int', '数据'),
        'PIGIMAGE_QUOTES_MIN': ('引用下限', '事实依据引用条数下限', 'int', '数据'),
        'PIGIMAGE_QUOTES_MAX': ('引用上限', '事实依据引用条数上限', 'int', '数据'),
        'PIGIMAGE_CACHE_TTL': ('缓存时长', '画像缓存有效期秒数', 'int', '数据'),
    },
    'weather': {
        'WEATHER_ENABLED': ('总开关', '', 'bool', '基础'),
        'WEATHER_PROVIDER': ('数据源', 'qweather 和风天气 / wttr', 'str', '基础'),
        'WEATHER_QWEATHER_API_KEY': ('和风 Key', '和风天气 API KEY', 'secret', '基础'),
        'WEATHER_QWEATHER_HOST': ('和风 Host', '控制台 API Host', 'str', '基础'),
        'WEATHER_WTTR_URL': ('wttr 模板', '{city} 会被替换', 'str', '基础'),
        'WEATHER_DEFAULT_CITY': ('默认城市', '未指定城市时使用', 'str', '基础'),
        'WEATHER_TIMEOUT': ('请求超时', '秒', 'int', '基础'),
    },
    'menu': {
        'MENU_ENABLED': ('总开关', '', 'bool', '基础'),
    },
}

# 未写入 .env 时由 pydantic Config 提供的默认值。面板展示这些默认值，
# 避免把「未配置」误显示成 false/0，也让管理员知道当前实际生效的是什么。
PLUGIN_CONFIG_DEFAULTS = {
    'CHAT_HUMANIZE_ENABLED': 'true',
    'CHAT_TYPING_SIMULATION': 'true',
    'CHAT_TYPING_CHARS_PER_SECOND': '9.0',
    'CHAT_TYPING_MAX_DELAY': '6.0',
    'CHAT_READ_DELAY_MIN': '0.45',
    'CHAT_READ_DELAY_MAX': '1.8',
    'CHAT_SPLIT_PROBABILITY': '0.25',
    'CHAT_ANTI_REPEAT_ENABLED': 'true',
    'CHAT_ANTI_REPEAT_THRESHOLD': '0.78',
    'CHAT_ANTI_REPEAT_WINDOW': '6',
    'CHAT_MOOD_ENABLED': 'true',
    'CHAT_ACTIVITY_ENABLED': 'true',
    'CHAT_LIFE_ENABLED': 'true',
    'CHAT_LIFE_SHARE_ENABLED': 'true',
    'CHAT_LIFE_SHARE_GROUPS': '',
    'CHAT_LIFE_SHARE_INTERVAL_MINUTES': '20',
    'CHAT_LIFE_SHARE_QUIET_MINUTES': '45',
    'CHAT_LIFE_SHARE_PROBABILITY': '0.35',
    'CHAT_ACTIVITY_INTERVAL_MINUTES': '75',
    'CHAT_MOOD_CUES_ENABLED': 'true',
    'CHAT_WEB_SEARCH_ENABLED': 'true',
    'CHAT_WEB_SEARCH_AUTO_DECIDE': 'true',
    'CHAT_WEB_SEARCH_PLANNER_TIMEOUT': '12',
    'CHAT_WEB_SEARCH_MAX_RESULTS': '4',
    'CHAT_WEB_SEARCH_TIMEOUT': '20',
    'CHAT_WEB_SEARCH_API_BASE': '',
    'CHAT_NIGHT_QUIET_ENABLED': 'true',
    'CHAT_NIGHT_START_HOUR': '0',
    'CHAT_NIGHT_END_HOUR': '7',
    'CHAT_MEMORY_RETRIEVAL_ENABLED': 'true',
    'CHAT_PROFILE_ENABLED': 'true',
    'CHAT_PROFILE_REFRESH_HOURS': '48',
    'CHAT_PROFILE_PROMPT_ENABLED': 'true',
    'CHAT_BIRTHDAY_ENABLED': 'true',
    'CHAT_BIRTHDAY_HOUR': '9',
    'CHAT_BIRTHDAY_MIN_FAVORABILITY': '10',
    'CHAT_FAVOR_LEDGER_ENABLED': 'true',
    'CHAT_FAVOR_PENALTY_WINDOW_SECONDS': '600',
    'CHAT_FAVOR_PENALTY_CAP': '6.0',
    'CHAT_PROFILE_LIKE_ENABLED': 'false',
    'CHAT_PROFILE_LIKE_PROBABILITY': '0.15',
    'CHAT_PROFILE_LIKE_TIMES': '10',
    'CHAT_PROFILE_LIKE_COOLDOWN_HOURS': '24',
    'CHAT_PROFILE_LIKE_DAILY_LIMIT': '10',
    'CHAT_QZONE_ENABLED': 'false',
    'CHAT_QZONE_START_HOUR': '10',
    'CHAT_QZONE_END_HOUR': '23',
    'CHAT_QZONE_PROBABILITY': '0.25',
    'CHAT_QZONE_DAILY_LIMIT': '1',
    'CHAT_QZONE_MIN_INTERVAL_HOURS': '2',
    'CHAT_QZONE_PROBABILITY_RAMP_HOURS': '96',
    'CHAT_QZONE_MAX_PROBABILITY': '0.8',
    'CHAT_QZONE_HOME_EVENT_PROBABILITY': '0.08',
    'CHAT_QZONE_OUTSIDE_EVENT_PROBABILITY': '0.22',
    'CHAT_QZONE_SLEEP_ONSET_PROBABILITY': '0.12',
    'CHAT_QZONE_SLEEP_ONSET_MINUTES': '30',
    'CHAT_QZONE_EVENT_MAX_AGE_MINUTES': '120',
    'CHAT_QZONE_IMAGE_PROBABILITY': '0.35',
    'CHAT_QZONE_EVENT_IMAGE_PROBABILITY': '0.75',
    'CHAT_IMAGE_ENABLED': 'true',
    'CHAT_IMAGE_PROVIDER': 'qwen',
    'CHAT_IMAGE_API_BASE': 'https://maas.qianwenaiapi.com/compatible-mode/v1',
    'CHAT_IMAGE_API_KEY': '',
    'CHAT_IMAGE_MODEL': 'qwen-image-3.0-pro',
    'CHAT_IMAGE_SIZE': '1024x1024',
    'CHAT_IMAGE_PROMPT_SUFFIX': 'anime style, 2D illustration, cel shading, clean line art, soft colors, wholesome, no photorealistic, no 3D render',
    'CHAT_IMAGE_NEGATIVE_PROMPT': 'photorealistic, realistic photo, 3D render, CGI, text, watermark, logo, low quality, bad anatomy, extra fingers, nsfw',
    'CHAT_QZONE_COMMENT_ENABLED': 'true',
    'CHAT_QZONE_COMMENT_INTERVAL_MINUTES': '2',
    'CHAT_QZONE_COMMENT_HOT_MINUTES': '10',
    'CHAT_QZONE_COMMENT_HOT_INTERVAL_SECONDS': '30',
    'CHAT_QZONE_COMMENT_MAX_PER_TICK': '0',
    'CHAT_QZONE_COMMENT_MAX_PER_DAY': '0',
}

# 各插件的环境变量前缀（用于兜底捕获 .env 中 meta 未登记的键）。
# 注意：morninginfo 的字段前缀是 MORNING_（而非插件名），welcome/exit 前缀不规范故未列入。
PLUGIN_CONFIG_PREFIX = {
    'chat': 'CHAT_',
    'limit': 'LIMIT_',
    'live': 'LIVE_',
    'notice': 'NOTICE_',
    'morninginfo': 'MORNING_',
    'poster': 'POSTER_',
    'errorlog': 'ERRORLOG_',
    'datadetail': 'DATADETAIL_',
    'pigimage': 'PIGIMAGE_',
    'weather': 'WEATHER_',
    'menu': 'MENU_',
}

# 这些键影响进程/网络/鉴权本身，不能在不重启的情况下安全替换。
RUNTIME_CONFIG_SKIP = {
    'HOST', 'PORT', 'DRIVER', 'ENVIRONMENT', 'LOG_LEVEL', 'LOCALSTORE_USE_CWD',
    'ADMIN_HOST', 'ADMIN_PORT', 'ADMIN_PASSWORD', 'ADMIN_USERNAME', 'ADMIN_ENABLED',
    'ONEBOT_ACCESS_TOKEN', 'SUPERUSERS', 'COMMAND_START', 'COMMAND_SEP', 'NICKNAME',
    'TELEGRAM_TOKEN', 'TELEGRAM_API_SERVER', 'TELEGRAM_IS_WEBHOOK', 'TELEGRAM_PROXY',
    'TELEGRAM_BOTS',
}


def parse_runtime_value(raw: str):
    """把 .env 文本还原成运行时值；无法 JSON 解析时按普通字符串处理。"""
    text = str(raw).strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        try:
            return json.loads(text)
        except Exception:
            pass
    if len(text) >= 2 and text[0] == text[-1] == "'":
        text = text[1:-1]
    try:
        return json.loads(text)
    except Exception:
        return text


class AdminServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        # The bot's main server owns process signals.
        yield


class AdminHTTPProtocol(H11Protocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Disable polling request logs locally, without clearing shared handlers.
        self.access_log = False


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(text, encoding='utf-8')
    os.replace(temporary, path)


def hot_reload(plugin):
    # NoneBot 暂无官方热重载接口：先清理该插件已注册的事件响应器与注册信息，
    # 再移除模块缓存并重新导入，使改动后的插件代码生效。
    import importlib
    import sys

    from nonebot.plugin import _revert_plugin

    module_name = plugin.module.__name__
    for matcher in list(plugin.matcher):
        with contextlib.suppress(Exception):
            matcher.destroy()
    plugin.matcher.clear()
    _revert_plugin(plugin)
    sys.modules.pop(module_name, None)
    importlib.import_module(module_name)


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=512)
    username: str = Field(default='', max_length=32)


class Toggle(BaseModel):
    enabled: bool


class ConfigChange(BaseModel):
    values: dict[str, str]


class SendMessage(BaseModel):
    bot_id: str
    target_type: str
    target_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=4000)


class SendForwardMessage(BaseModel):
    bot_id: str
    target_type: str
    target_id: str = Field(min_length=1, max_length=64)
    messages: list[dict]


class FavorUpdate(BaseModel):
    favorability: float = Field(ge=-100000, le=1000)


class GrowthUpdate(BaseModel):
    entries: list[str]


class OpsReload(BaseModel):
    plugin: str = Field(min_length=1, max_length=128)


class AdminCreate(BaseModel):
    username: str = Field(min_length=2, max_length=32, pattern=r'^[A-Za-z0-9_.-]+$')
    password: str = Field(min_length=12, max_length=512)
    role: str = 'admin'


class ImportPayload(BaseModel):
    config: dict[str, str]


class WhitelistToggle(BaseModel):
    kind: str
    id: str = Field(min_length=1, max_length=64)
    enabled: bool


class WhitelistCreate(BaseModel):
    kind: str
    id: str = Field(min_length=5, max_length=20)
    enabled: bool = True


class ProfileUpdate(BaseModel):
    nickname: str = Field(default='', max_length=64)
    self_alias: str = Field(default='', max_length=20)
    aliases: dict[str, str] = Field(default_factory=dict)
    birthday: str = Field(default='', max_length=16)
    notes: str = Field(default='', max_length=500)
    tags: list[str] = Field(default_factory=list)


class BotEntry(BaseModel):
    qq: str = Field(pattern=r'^\d{5,20}$')
    name: str = Field(default='', max_length=64)
    is_bot: bool = True
    source: str = Field(default='manual', max_length=16)


class QzonePost(BaseModel):
    content: str = Field(default='', max_length=500)


class MediaWarm(BaseModel):
    width: int = Field(default=360, ge=64, le=1600)


class BackupCreate(BaseModel):
    kind: str = 'manual'


class BackupRestore(BaseModel):
    confirm: bool = False


class EmoteUpload(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    category: str = Field(default='misc', max_length=64)
    data: str = Field(min_length=1, max_length=8 * 1024 * 1024)


class EmotePath(BaseModel):
    path: str = Field(min_length=1, max_length=500)


class EmoteHash(BaseModel):
    hash: str = Field(min_length=8, max_length=64)


class ImageTest(BaseModel):
    prompt: str = Field(default='', max_length=800)


class QzoneImageTest(BaseModel):
    content: str = Field(default='', max_length=300)
    prompt: str = Field(default='', max_length=800)


class SearchTest(BaseModel):
    query: str = Field(min_length=1, max_length=128)


class ReplyTest(BaseModel):
    text: str = Field(min_length=1, max_length=500)


class LifeActionBody(BaseModel):
    action: str = Field(min_length=2, max_length=20)
    location: str = Field(default='', max_length=20)
    reason: str = Field(default='', max_length=120)


class FavorAppealBody(BaseModel):
    note: str = Field(default='', max_length=300)


class FavorResolveBody(BaseModel):
    accepted: bool = True
    kind: str = 'normal'
    corrected_reason: str = ''


class AnomalyUpdateBody(BaseModel):
    kind: str = 'normal'
    corrected_reason: str = ''


class SlangUpdateBody(BaseModel):
    status: str = ''
    meaning: str = ''
    usage: str = ''
    risk: str = ''
    scope: str = ''


class EvalLabelBody(BaseModel):
    labels: dict[str, bool] = Field(default_factory=dict)
    note: str = Field(default='', max_length=300)


class RiskReviewLabelBody(BaseModel):
    label: str = Field(default='uncertain', max_length=30)
    categories: list[str] = Field(default_factory=list)
    note: str = Field(default='', max_length=500)


class RiskReviewReplayBody(BaseModel):
    sample_ids: list[int] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=50)


class RelationBody(BaseModel):
    a: str = Field(min_length=5, max_length=12)
    b: str = Field(min_length=5, max_length=12)
    kind: str = Field(default='other', max_length=12)
    label: str = Field(default='', max_length=120)


class PanelChatBody(BaseModel):
    session: str = Field(default='owner', max_length=32)
    text: str = Field(min_length=1, max_length=1000)


class RelayBody(BaseModel):
    target: str = Field(pattern=r'^(group|computer)$')
    text: str = Field(min_length=1, max_length=500)


class SelfFactBody(BaseModel):
    text: str = Field(min_length=1, max_length=200)


class EvalReplayBody(BaseModel):
    sample_ids: list[str] = Field(default_factory=list)
    limit: int = Field(default=10, ge=1, le=30)


class WorldExpandBody(BaseModel):
    parent: str = Field(default='plaza', max_length=30)
    name: str = Field(min_length=1, max_length=30)
    kind: str = Field(default='shop', max_length=20)


class WorldDetailBody(BaseModel):
    node: str = Field(min_length=1, max_length=40)
    key: str = Field(min_length=1, max_length=40)
    value: str = Field(default='', max_length=120)


class AgentPlanBody(BaseModel):
    goal: str = Field(min_length=1, max_length=160)
    description: str = Field(default='', max_length=500)
    steps: list[dict] = Field(min_length=1, max_length=8)
    idempotency_key: str = Field(min_length=8, max_length=160)


class AgentPolicyBody(BaseModel):
    auto_daily_minor: int = Field(default=10000, ge=0, le=100_000_000, strict=True)
    paused: bool = Field(strict=True)
    single_minor: int = Field(ge=0, le=100_000_000, strict=True)
    daily_minor: int = Field(ge=0, le=100_000_000, strict=True)
    monthly_minor: int = Field(ge=0, le=100_000_000, strict=True)


class AgentGrantInput(BaseModel):
    id: str = Field(min_length=1, max_length=160)
    capability: str = Field(min_length=1, max_length=160)
    mode: str = 'simulation'
    merchant: str = Field(min_length=1, max_length=160)
    recipient: str = Field(min_length=1, max_length=160)
    purpose: str = Field(min_length=1, max_length=160)
    enabled: bool = Field(default=False, strict=True)
    require_approval: bool = Field(default=False, strict=True)
    single_minor: int = Field(default=2000, ge=0, le=100_000_000, strict=True)
    daily_minor: int = Field(default=10000, ge=0, le=100_000_000, strict=True)
    monthly_minor: int = Field(default=150000, ge=0, le=100_000_000, strict=True)
    expires_at: float


class AgentTaskBody(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=160)
    grant_id: str = Field(min_length=1, max_length=160)
    capability: str = Field(min_length=1, max_length=160)
    mode: str = 'simulation'
    merchant: str = Field(min_length=1, max_length=160)
    recipient: str = Field(min_length=1, max_length=160)
    purpose: str = Field(min_length=1, max_length=160)
    amount_minor: int = Field(default=0, ge=0, le=100_000_000, strict=True)
    currency: str = 'CNY'
    description: str = Field(min_length=1, max_length=500)


class AgentNodeBody(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    domains: list[str] = Field(min_length=1, max_length=30)


class AgentNodeToggle(BaseModel):
    enabled: bool = Field(strict=True)


class AgentBrowserBody(BaseModel):
    node_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    url: str = Field(min_length=8, max_length=2000)


class AgentWorkerResult(BaseModel):
    job_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    lease: str = Field(min_length=32, max_length=128)
    result: dict


def create_app(driver, root: Path = ROOT):
    from src.qq_autonomy import QQAutonomy, qq_autonomy
    qq_store = qq_autonomy if root.resolve() == ROOT.resolve() else QQAutonomy(root / 'data' / 'qq')
    game_store = GameStore(root / 'data' / 'qq' / 'games.sqlite3')
    state = root / 'data' / 'admin'
    state.mkdir(parents=True, exist_ok=True)
    if os.name != 'nt':
        state.chmod(0o700)
    agent_control = AgentControl(root / 'data' / 'agent' / 'control.sqlite3')
    node_queue = NodeQueue(agent_control)
    payments = Payments(agent_control)
    phone_url = str(getattr(driver.config, 'agent_phone_url', '') or '').strip()
    if phone_url:
        phone_client = PhoneClient(
            phone_url,
            str(getattr(driver.config, 'agent_phone_token', '') or '') or None,
        )
        real_phone = True
    else:
        phone_client = ComputerClient(
            str(getattr(driver.config, 'agent_computer_url', '') or '') or None,
            str(getattr(driver.config, 'agent_computer_token', '') or '') or None,
        )
        real_phone = False
    def yuna_persona_loader():
        """给电脑/手机 Agent 注入 Yuna 的人设，操作设备的也是她本人。"""
        path = CHAT_DIR / 'character.txt'
        try:
            return path.read_text(encoding='utf-8').strip()[:600]
        except OSError:
            return ''

    phone_cls = RealPhoneAgent if real_phone else ComputerAgent
    phone_agent = phone_cls(phone_client, AgentLLM(
        str(getattr(driver.config, 'chat_llm_api_base', '') or ''),
        str(getattr(driver.config, 'chat_llm_api_key', '') or ''),
        str(getattr(driver.config, 'chat_llm_model', '') or ''),
    ), play=phone_play, history_path=root/'data/agent/phone_tasks.json',
        persona_loader=yuna_persona_loader)
    def agent_memory_loader(goal):
        plugin=next((p for p in nonebot.get_loaded_plugins() if p.name=='chat'),None)
        loader=getattr(plugin.module,'agent_chat_memory',None) if plugin else None
        return loader(goal) if callable(loader) else ''
    phone_agent.chat_memory_loader=agent_memory_loader
    async def _share_to_whitelist_group(group: str, text: str):
        from src.agent.phone_agent import PhoneTaskError
        from src.agent.wechat_phone import WeChatPhone
        row = next((g for g in wechat_store.list_groups(True)
                    if group in (g.get('name'), g.get('id6'), g.get('wxid'))), None)
        if not row:
            raise PhoneTaskError('目标群不在微信白名单')
        if not await WeChatPhone(phone_client).send(row.get('name') or '', text):
            raise PhoneTaskError('微信群分享未完成')
    phone_agent.share_group = _share_to_whitelist_group
    set_phone_agent(phone_agent)
    from src.agent.minecraft import MinecraftAgent, MinecraftClient, set_minecraft_agent
    game_llm=AgentLLM(phone_agent.llm.base,phone_agent.llm.key,phone_agent.llm.model,
                      system='你是清汐的Minecraft游戏决策器。只输出JSON对象。当前执行器只有结构化游戏状态，没有截图或UI工具；严格使用提示列出的真实技能，不继承电脑浏览器动作。只根据服务器状态描述已完成的行为。')
    minecraft = MinecraftAgent(game_llm, MinecraftClient(
        url=str(getattr(driver.config,'agent_minecraft_url','') or '') or None,
        token=str(getattr(driver.config,'agent_minecraft_token','') or '') or None), root/'data/agent/minecraft.json',
                               context_loader=lambda _: yuna_persona_loader()+'\n'+agent_memory_loader('Minecraft MC 游戏'),
                               availability_guard=lambda: not phone_agent._active and phone_play.snapshot().get('status')=='idle')
    set_minecraft_agent(minecraft)
    media = MediaCache(state / 'cache' / 'media')
    backups = BackupManager(root, state, CHAT_DIR)
    try:
        alert_cooldown = int(getattr(driver.config, 'admin_alert_cooldown_seconds', 6 * 3600))
    except (TypeError, ValueError):
        alert_cooldown = 6 * 3600
    alerts = AlertManager(state / 'alerts.json', alert_cooldown)
    password = str(getattr(driver.config, 'admin_password', '') or '')
    if not password:
        password_file = state / 'password.txt'
        if not password_file.exists():
            atomic_write(password_file, secrets.token_urlsafe(24))
            if os.name != 'nt':
                password_file.chmod(0o600)
        password = password_file.read_text(encoding='utf-8').strip()
    if len(password) < 12:
        raise ValueError('ADMIN_PASSWORD must contain at least 12 characters')
    digest = hashlib.sha256(password.encode()).digest()
    owner_name = str(getattr(driver.config, 'admin_username', 'admin') or 'admin')
    admins_file = state / 'admins.json'
    audit_file = state / 'audit.jsonl'
    audit_logs: deque = deque(maxlen=500)
    sessions: dict[str, float] = {}
    session_user: dict[str, tuple[str, str]] = {}
    # 会话持久化：部署/重启不再把所有人登出（过期条目启动时清理）
    sessions_file = state / 'sessions.json'

    def load_sessions() -> None:
        try:
            raw = json.loads(sessions_file.read_text(encoding='utf-8'))
            now = time.time()
            for token, expiry in (raw.get('sessions') or {}).items():
                if float(expiry) > now:
                    sessions[token] = float(expiry)
            for token, pair in (raw.get('users') or {}).items():
                if token in sessions and isinstance(pair, list) and len(pair) == 2:
                    session_user[token] = (str(pair[0]), str(pair[1]))
        except (OSError, ValueError, TypeError):
            pass

    def save_sessions() -> None:
        try:
            now = time.time()
            alive = {k: v for k, v in sessions.items() if v > now}
            atomic_write(sessions_file, json.dumps({
                'sessions': alive,
                'users': {k: list(v) for k, v in session_user.items() if k in alive},
            }))
        except OSError:
            pass

    load_sessions()
    attempts: dict[str, deque] = {}
    logs = deque(maxlen=600)
    event_history = deque(maxlen=100)
    event_queues = set()
    started = time.time()
    counters = {'events': 0}
    disabled_file = state / 'disabled.json'
    disabled = set(json.loads(disabled_file.read_text()) if disabled_file.exists() else [])
    app = FastAPI(title='Yuna 管理', docs_url=None, redoc_url=None, openapi_url=None)
    app.state.disabled = disabled
    app.state.counters = counters
    app.state.logs = logs
    app.state.event_history = event_history
    app.state.media = media
    app.state.backups = backups
    app.state.alerts = alerts
    app.state.version = VERSION
    health_state = {'warnings': [], 'updated': 0, 'last_log': 0}
    app.state.health = health_state

    def env_path():
        environment = str(getattr(driver.config, 'environment', 'prod'))
        if not re.fullmatch(r'[\w-]+', environment):
            raise HTTPException(400, '无效环境名称')
        return root / f'.env.{environment}'

    def read_config():
        result = {}
        for path in (root / '.env', env_path()):
            if path.exists():
                for line in path.read_text(encoding='utf-8-sig').splitlines():
                    match = re.match(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$', line)
                    if match:
                        result[match[1].upper()] = match[2]
        return result

    def chat_setting(key: str, default):
        value = getattr(driver.config, key.lower(), None)
        if value in (None, ''):
            value = PLUGIN_CONFIG_DEFAULTS.get(key, default)
        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return value
        return value

    def favor_of(qq: str, users: dict) -> float:
        info = users.get(str(qq)) if isinstance(users, dict) else None
        try:
            return float((info or {}).get('favorability', 5.0))
        except (TypeError, ValueError):
            return 5.0

    def like_cap_for(qq: str, users: dict) -> int:
        favor = favor_of(qq, users)
        try:
            if str(qq) in {str(x) for x in (getattr(driver.config, 'superusers', set()) or set())}:
                return 10
        except Exception:
            pass
        if favor < -30:
            return 0
        if favor < 10:
            return 1
        if favor < 20:
            return 3
        if favor < 30:
            return 4
        if favor < 45:
            return 5
        if favor < 60:
            return 6
        if favor < 75:
            return 8
        if favor < 85:
            return 9
        return 10

    def live_human_state() -> dict:
        module = chat_module()
        fn = getattr(module, 'human_snapshot', None) if module else None
        if callable(fn):
            try:
                return fn()
            except Exception as error:
                logger.warning(f'管理页面：读取实时拟人状态失败，回退文件：{error}')
        return read_human_state()

    def publish_event(kind: str, **data) -> None:
        event = {'type': kind, 'time': int(time.time()), 'data': data}
        event_history.append(event)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        for queue in list(event_queues):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def notify_emote_reload() -> None:
        module = chat_module()
        fn = getattr(module, 'reload_emote_index', None) if module else None
        if callable(fn):
            try:
                fn()
            except Exception as error:
                logger.warning(f'管理页面：刷新表情索引失败：{error}')

    def chat_module():
        plugin = next(
            (p for p in nonebot.get_loaded_plugins() if p.name == 'chat'),
            None,
        )
        return plugin.module if plugin else None

    def risk_module():
        plugin = next(
            (p for p in nonebot.get_loaded_plugins() if p.name == 'risk'),
            None,
        )
        return plugin.module if plugin else None

    def redact(text):
        for key, value in read_config().items():
            value = value.strip('"\' ')
            if SECRET.search(key) and len(value) >= 4:
                text = text.replace(value, '[已隐藏]')
        return text.replace(password, '[已隐藏]')

    def sink(message):
        record = message.record
        message = redact(record['message'])
        logs.append({'time': record['time'].isoformat(), 'level': record['level'].name,
                     'message': message})
        publish_event('log', level=record['level'].name, message=message)

    sink_id = logger.add(sink, level='INFO', catch=True)
    app.state.sink_id = sink_id

    def collect_health() -> dict:
        """轻量健康快照：供 /api/health、概览页与后台巡检复用。"""
        try:
            bots = list(nonebot.get_bots().keys())
        except Exception:
            bots = []
        try:
            plugins = len(nonebot.get_loaded_plugins())
        except Exception:
            plugins = 0
        cache = media.stats()
        try:
            backup_items = backups.list()['items']
        except Exception:
            backup_items = []
        try:
            usage = shutil.disk_usage(root)
            disk = {
                'total': usage.total,
                'free': usage.free,
                'used_pct': round(usage.used / usage.total * 100, 1) if usage.total else 0,
            }
        except OSError:
            disk = {'total': 0, 'free': 0, 'used_pct': 0}
        cutoff = time.time() - 900
        recent_errors = 0
        for entry in list(logs):
            if entry.get('level') not in ('ERROR', 'CRITICAL'):
                continue
            try:
                if datetime.fromisoformat(entry.get('time', '')).timestamp() >= cutoff:
                    recent_errors += 1
            except (TypeError, ValueError):
                continue
        warnings = []
        if not bots:
            warnings.append('没有在线机器人连接')
        if disk['free'] and disk['free'] < 1024 ** 3:
            warnings.append('磁盘剩余空间不足 1GB')
        if cache.get('max_bytes') and cache.get('bytes', 0) > cache['max_bytes'] * 0.9:
            warnings.append('图片缩略图缓存接近上限')
        if recent_errors:
            warnings.append(f'最近 15 分钟有 {recent_errors} 条 ERROR 日志')
        health = {
            'ok': not warnings,
            'status': 'ok' if not warnings else 'warn',
            'version': VERSION,
            'uptime': int(time.time() - started),
            'environment': str(getattr(driver.config, 'environment', 'prod')),
            'bots': bots,
            'plugins': plugins,
            'cache': {k: cache.get(k) for k in ('files', 'bytes', 'max_bytes', 'hits', 'misses')},
            'backups': len(backup_items),
            'disk': disk,
            'recent_errors': recent_errors,
            'warnings': warnings,
            'now': int(time.time()),
        }
        health_state['warnings'] = warnings
        health_state['updated'] = health['now']
        return health


    def alert_settings() -> dict:
        raw_enabled = getattr(driver.config, 'admin_alerts_enabled', True)
        if isinstance(raw_enabled, str):
            enabled = raw_enabled.strip().lower() not in ('false', '0', 'no', 'off', '')
        else:
            enabled = bool(raw_enabled)
        raw = getattr(driver.config, 'admin_alert_qqs', None)
        targets: list[str] = []
        if isinstance(raw, str):
            text = raw.strip()
            try:
                parsed = json.loads(text)
            except Exception:
                parsed = [part for part in re.split(r'[,，\s]+', text) if part]
            raw = parsed
        if isinstance(raw, (list, tuple, set)):
            for item in raw:
                value = str(item).strip()
                if value.isdigit() and value not in targets:
                    targets.append(value)
        if not targets:
            try:
                for item in getattr(driver.config, 'superusers', set()) or set():
                    value = str(item).strip()
                    if value.isdigit() and value not in targets:
                        targets.append(value)
            except Exception:
                pass
        webhook = str(getattr(driver.config, 'admin_alert_webhook', '') or '').strip()
        if webhook and not webhook.startswith(('http://', 'https://')):
            webhook = ''
        try:
            timeout = float(getattr(driver.config, 'admin_alert_webhook_timeout', 10))
        except (TypeError, ValueError):
            timeout = 10.0
        return {
            'enabled': enabled,
            'targets': targets,
            'webhook': webhook,
            'webhook_token': str(getattr(driver.config, 'admin_alert_webhook_token', '') or ''),
            'webhook_timeout': max(3.0, min(30.0, timeout)),
        }

    async def send_webhook_alert(
        settings: dict, health: dict, message: str, force: bool
    ) -> tuple[bool, str]:
        """向通用 HTTP Webhook 发送告警，不依赖 Telegram。"""
        url = settings.get('webhook') or ''
        if not url:
            return False, ''
        payload = {
            'source': 'yuna-admin',
            'event': 'health_alert_test' if force else 'health_alert',
            'message': message,
            'warnings': health.get('warnings') or [],
            'health': {
                'version': health.get('version'),
                'uptime': health.get('uptime'),
                'environment': health.get('environment'),
                'bots': health.get('bots') or [],
                'plugins': health.get('plugins'),
                'disk': health.get('disk'),
                'recent_errors': health.get('recent_errors'),
            },
            'timestamp': int(time.time()),
        }
        headers = {'Content-Type': 'application/json', 'User-Agent': 'Yuna-Admin/1.0'}
        token = settings.get('webhook_token') or ''
        if token:
            headers['Authorization'] = f'Bearer {token}'
        try:
            async with httpx.AsyncClient(timeout=settings['webhook_timeout']) as client:
                response = await client.post(url, json=payload, headers=headers)
            if 200 <= response.status_code < 300:
                return True, ''
            return False, f'webhook HTTP {response.status_code}'
        except Exception as error:
            return False, f'webhook: {error}'

    async def deliver_health_alert(health: dict, force: bool = False) -> dict:
        settings = alert_settings()
        warnings = health.get('warnings') or []
        if not settings['enabled']:
            return {'sent': 0, 'reason': 'disabled'}
        if not warnings:
            return {'sent': 0, 'reason': 'healthy'}
        if not force and not alerts.should_send(warnings):
            return {'sent': 0, 'reason': 'cooldown'}
        bots = [
            bot for bot in nonebot.get_bots().values()
            if bot.adapter.get_name() == 'OneBot V11'
        ]
        targets = settings['targets']
        webhook = settings['webhook']
        if (not bots or not targets) and not webhook:
            return {'sent': 0, 'reason': 'no_channel'}
        message = alerts.build_message(health)
        if force:
            message = message.replace('【Yuna 健康告警】', '【Yuna 告警测试】', 1)
        delivered = 0
        errors: list[str] = []
        record_targets: list[str] = []
        if bots and targets:
            for qq in targets:
                try:
                    await asyncio.wait_for(
                        bots[0].call_api(
                            'send_private_msg', user_id=int(qq), message=message
                        ),
                        timeout=15,
                    )
                    delivered += 1
                    record_targets.append(qq)
                except Exception as error:
                    errors.append(f'{qq}: {error}')
        if webhook:
            ok, error = await send_webhook_alert(settings, health, message, force)
            if ok:
                delivered += 1
                record_targets.append('webhook')
            elif error:
                errors.append(error)
        alerts.record(warnings, delivered, record_targets, '; '.join(errors))
        if delivered:
            logger.warning(f'管理页面：健康告警已发送 {delivered} 个通道')
        else:
            logger.error(f'管理页面：健康告警发送失败：{"; ".join(errors)}')
        return {
            'sent': delivered,
            'targets': record_targets,
            'warnings': warnings,
            'errors': errors,
        }

    app.state.collect_health = collect_health
    app.state.publish_event = publish_event
    app.state.deliver_health_alert = deliver_health_alert

    def read_admins() -> dict:
        if not admins_file.exists():
            return {}
        try:
            data = json.loads(admins_file.read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def write_admins(data: dict):
        atomic_write(admins_file, json.dumps(data, ensure_ascii=False, indent=2))
        if os.name != 'nt':
            admins_file.chmod(0o600)

    def verify_login(username: str, candidate: bytes):
        username = (username or '').strip()
        if not username or username == owner_name:
            if hmac.compare_digest(candidate, digest):
                return owner_name, 'owner'
        record = read_admins().get(username)
        if isinstance(record, dict):
            try:
                stored = bytes.fromhex(str(record.get('hash', '')))
            except ValueError:
                stored = b''
            if stored and hmac.compare_digest(candidate, stored):
                return username, str(record.get('role', 'admin'))
        return None

    def actor(request: Request) -> tuple[str, str]:
        return session_user.get(request.cookies.get('yuna_admin', ''), (owner_name, 'owner'))

    def audit(request: Request, action: str, detail: str = ''):
        name, _role = actor(request)
        entry = {'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'user': name,
                 'action': action, 'detail': redact(detail)[:500]}
        audit_logs.append(entry)
        publish_event('audit', user=name, action=action, detail=entry['detail'])
        try:
            with audit_file.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except OSError:
            pass

    @app.middleware('http')
    async def protection(request: Request, call_next):
        if request.method not in ('GET', 'HEAD'):
            origin = request.headers.get('origin')
            if origin and urlsplit(origin).netloc != request.headers.get('host'):
                return Response('不允许跨站请求', status_code=403)
            if request.headers.get('x-yuna-admin') != '1':
                return Response('缺少管理请求标识', status_code=403)
        response = await call_next(request)
        path = request.url.path
        # 媒体与静态资源由各自端点设置强缓存/协商缓存；其余接口一律不缓存。
        if not path.startswith(('/assets/', '/api/media/', '/api/chat/emote-file')):
            response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
            "font-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
        )
        return response

    def authenticated(request: Request):
        token = request.cookies.get('yuna_admin', '')
        if sessions.get(token, 0) < time.time():
            sessions.pop(token, None)
            save_sessions()
            session_user.pop(token, None)
            raise HTTPException(401, '请先登录，或会话已过期')

    @app.get('/')
    async def index():
        response = FileResponse(STATIC / 'index.html')
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/next')
    @app.get('/next/{path:path}')
    async def next_frontend(path: str = ''):
        base = (STATIC / 'next').resolve()
        target = (base / path).resolve() if path else (base / 'index.html')
        if base not in target.parents and target != base / 'index.html':
            raise HTTPException(404)
        if target.is_dir():
            target = target / 'index.html'
        if not target.is_file():
            target = base / 'index.html'
        if not target.is_file():
            raise HTTPException(404, '新版界面尚未构建')
        response = FileResponse(target)
        response.headers['Cache-Control'] = 'no-cache'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.get('/favicon.ico')
    async def favicon():
        target = STATIC / 'favicon.svg'
        if target.is_file():
            return FileResponse(target, media_type='image/svg+xml')
        raise HTTPException(404)

    @app.get('/assets/{path:path}')
    async def assets(path: str, request: Request):
        base = STATIC.resolve()
        target = (base / path).resolve()
        if base not in target.parents or not target.is_file():
            raise HTTPException(404)
        if target.suffix.lower() not in {
            '.js', '.css', '.svg', '.png', '.webp', '.ico', '.woff2', '.json',
        }:
            raise HTTPException(404)
        stat = target.stat()
        etag = '"' + hashlib.md5(
            f'{stat.st_mtime_ns}-{stat.st_size}'.encode(), usedforsecurity=False
        ).hexdigest() + '"'
        headers = {
            'Cache-Control': 'no-cache',
            'ETag': etag,
        }
        if request.headers.get('if-none-match') == etag:
            return Response(status_code=304, headers=headers)
        return FileResponse(target, headers=headers)

    @app.post('/api/login')
    async def login(body: Login, request: Request, response: Response):
        now = time.time()
        address = request.client.host if request.client else 'unknown'
        for key in list(attempts):
            if not attempts[key] or attempts[key][-1] < now - 300:
                attempts.pop(key)
        recent = attempts.setdefault(address, deque(maxlen=6))
        while recent and recent[0] < now - 300:
            recent.popleft()
        if len(recent) >= 5:
            raise HTTPException(429, '尝试过于频繁，请 5 分钟后重试')
        verified = verify_login(body.username, hashlib.sha256(body.password.encode()).digest())
        if verified is None:
            recent.append(now)
            raise HTTPException(401, '账号或口令不正确')
        attempts.pop(address, None)
        name, role = verified
        for key in list(sessions):
            if sessions[key] < now:
                sessions.pop(key)
                save_sessions()
                session_user.pop(key, None)
        token = secrets.token_urlsafe(32)
        sessions[token] = now + 8 * 3600
        session_user[token] = (name, role)
        save_sessions()
        response.set_cookie('yuna_admin', token, httponly=True, samesite='strict',
                            secure=request.url.scheme == 'https', max_age=8 * 3600)
        logger.info(f'管理页面：{name} 已登录')
        return {'ok': True, 'username': name, 'role': role,
                'version': VERSION, 'environment': str(driver.config.environment)}

    @app.post('/api/logout', dependencies=[Depends(authenticated)])
    async def logout(request: Request, response: Response):
        token = request.cookies.get('yuna_admin')
        sessions.pop(token, None)
        save_sessions()
        session_user.pop(token, None)
        response.delete_cookie('yuna_admin')
        return {'ok': True}

    @app.get('/api/me', dependencies=[Depends(authenticated)])
    async def me(request: Request):
        name, role = actor(request)
        return {'username': name, 'role': role, 'version': VERSION,
                'environment': str(driver.config.environment)}

    @app.get('/api/events', dependencies=[Depends(authenticated)])
    async def get_events():
        return {'items': list(event_history)[-50:]}

    @app.websocket('/api/events/ws')
    async def events_ws(websocket: WebSocket):
        token = websocket.cookies.get('yuna_admin', '')
        if sessions.get(token, 0) < time.time():
            await websocket.accept()
            await websocket.close(code=4401, reason='session expired')
            return
        await websocket.accept()
        queue: asyncio.Queue = asyncio.Queue(maxsize=80)
        event_queues.add(queue)
        try:
            await websocket.send_json({'type': 'snapshot', 'time': int(time.time()),
                                       'data': {'items': list(event_history)[-20:]}})
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=25)
                    await websocket.send_json(event)
                except asyncio.TimeoutError:
                    await websocket.send_json({'type': 'ping', 'time': int(time.time())})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            event_queues.discard(queue)

    @app.get('/api/health')
    async def health():
        """部署脚本与外部探活使用；不返回任何用户数据。"""
        snapshot = collect_health()
        return {
            'ok': snapshot['status'] == 'ok',
            'status': snapshot['status'],
            'version': snapshot['version'],
            'uptime': snapshot['uptime'],
            'bots': len(snapshot['bots']),
            'bot_ids': snapshot['bots'],
            'plugins': snapshot['plugins'],
            'environment': snapshot['environment'],
            'warnings': snapshot['warnings'],
            'now': snapshot['now'],
        }

    @app.get('/api/health/detail', dependencies=[Depends(authenticated)])
    async def health_detail():
        return collect_health()

    @app.get('/api/alerts', dependencies=[Depends(authenticated)])
    async def alerts_status():
        settings = alert_settings()
        status = alerts.status(settings['enabled'], settings['targets'])
        status['webhook_enabled'] = bool(settings['webhook'])
        if settings['webhook']:
            try:
                status['webhook_host'] = urlsplit(settings['webhook']).netloc
            except Exception:
                status['webhook_host'] = '—'
        return status

    @app.post('/api/alerts/test', dependencies=[Depends(authenticated)])
    async def alerts_test(request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以发送测试告警')
        health = collect_health()
        if not health.get('warnings'):
            health = {
                **health,
                'warnings': ['这是一条测试告警，收到后可以忽略'],
            }
        result = await deliver_health_alert(health, force=True)
        audit(request, '发送测试告警', f'{result.get("sent", 0)} 个通道')
        if not result.get('sent'):
            raise HTTPException(409, f'测试告警未发送：{result.get("reason") or result.get("errors")}')
        return result

    def agent_owner(request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '自主能力与支付授权仅所有者可管理')

    def agent_call(fn, *args):
        try:
            return fn(*args)
        except ControlError as error:
            raise HTTPException(409, str(error)) from error

    @app.get('/api/qq/login', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def qq_login_status(response: Response):
        from .qq_login import snapshot
        response.headers['Cache-Control'] = 'no-store, private'
        return await snapshot()

    def qq_bot():
        from nonebot.adapters.onebot.v11 import Bot as OneBot
        bot = next((b for b in nonebot.get_bots().values() if isinstance(b, OneBot)), None)
        if bot is None:
            raise HTTPException(409, '当前没有在线 QQ 机器人')
        return bot

    @app.get('/api/qq/autonomy', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def qq_autonomy_status(response: Response):
        response.headers['Cache-Control'] = 'no-store, private'
        return qq_store.snapshot()

    @app.put('/api/qq/autonomy', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def qq_autonomy_policy(body: dict, request: Request):
        try:
            result = qq_store.configure(body)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        audit(request, '调整QQ自主能力', '更新开关、会话范围和表情预算')
        return result

    @app.get('/api/qq/activity/{identifier}', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def qq_activity_detail(identifier: str, response: Response):
        response.headers['Cache-Control'] = 'no-store, private'
        row = qq_store.detail(identifier)
        if row is None:
            raise HTTPException(404, '行为记录不存在')
        return row

    @app.post('/api/qq/groups/{group_id}/file/read', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def qq_read_group_file(group_id: int, body: dict, response: Response):
        if group_id <= 0 or set(body)-{'file_id','folder_id'} or not isinstance(body.get('file_id'),str) or not 1<=len(body['file_id'])<=512 or not isinstance(body.get('folder_id',''),str) or len(body.get('folder_id',''))>512:
            raise HTTPException(400, '文件标识不合法')
        bot = qq_bot()
        response.headers['Cache-Control'] = 'no-store, private'
        try:
            groups = await asyncio.wait_for(bot.call_api('get_group_list'),8)
            if str(group_id) not in {str(g.get('group_id')) for g in groups}:
                raise HTTPException(403, '机器人不在该群')
            note = await qq_store.read_file(bot,group_id,body['file_id'],body.get('folder_id',''))
            return note
        except HTTPException:
            raise
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        except Exception as error:
            raise HTTPException(502, '文件读取失败：'+type(error).__name__) from error

    @app.get('/api/qq/bridge', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def qq_bridge_status(response: Response):
        from src.qq_bridge import implementation
        bot = qq_bot()
        response.headers['Cache-Control'] = 'no-store, private'
        version = await implementation(bot)
        try:
            status = await asyncio.wait_for(bot.call_api('get_status'), 8)
            groups = await asyncio.wait_for(bot.call_api('get_group_list'), 8)
        except Exception as error:
            raise HTTPException(502, 'QQ 接口暂不可用：' + type(error).__name__) from error
        return {'account': bot.self_id, 'implementation': version, 'status': status,
                'groups': [{'id': str(g.get('group_id')), 'name': str(g.get('group_name') or '')} for g in groups]}

    @app.get('/api/qq/audit', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def qq_plugin_audit():
        path = Path(__file__).parent / 'snowluma-audit.json'
        return json.loads(path.read_text()) if path.exists() else {'plugins': [], 'actions': []}

    from src.qq_brief import GroupBriefs
    group_briefs = GroupBriefs()

    @app.get('/api/qq/qzone-social', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def qq_qzone_social_status(response: Response):
        from src.qq_qzone_social import qzone_social
        bot=qq_bot();response.headers['Cache-Control']='no-store, private'
        result=qzone_social.snapshot(str(bot.self_id));result['friends']=[]
        try:
            friends=await asyncio.wait_for(bot.call_api('get_friend_list'),8)
            result['friends']=[{'id':str(r['user_id']),'name':str(r.get('remark') or r.get('nickname') or r['user_id'])[:80]} for r in friends]
        except Exception:result['warning']='好友列表暂不可用；已保存的白名单保留，暂不能添加新成员。'
        return result

    @app.put('/api/qq/qzone-social', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def qq_qzone_social_config(body: dict, request: Request):
        from src.qq_qzone_social import qzone_social
        bot=qq_bot();account=str(bot.self_id)
        if isinstance(body.get('comment_users'),list):
            added=set(str(q) for q in body['comment_users'])-set(qzone_social.policy(account)['comment_users'])
            if added:
                try:friends=await asyncio.wait_for(bot.call_api('get_friend_list'),8)
                except Exception as error:raise HTTPException(502,'好友名单未核实，未增加评论授权') from error
                if added-{str(r.get('user_id')) for r in friends}:raise HTTPException(400,'只能加入当前QQ好友；已保存的旧成员可移除')
        try:result=qzone_social.configure(account,body)
        except ValueError as error:raise HTTPException(400,str(error)) from error
        audit(request,'调整好友空间互动','保存时段、频率及独立评论白名单')
        return result

    @app.get('/api/qq/presence', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def qq_presence(response: Response):
        from src.qq_presence import presence
        bot = qq_bot()
        row = presence.states.get(str(bot.self_id), {})
        response.headers['Cache-Control'] = 'no-store, private'
        observed = row.get('observed') or {}
        return {'account': str(bot.self_id), 'verified': bool(row.get('verified')),
                'desired': (row.get('target') or [None, None, '待同步'])[2],
                'observed': {k: observed.get(k) for k in ('status', 'ext_status')},
                'checked': row.get('checked'), 'error': row.get('error')}

    @app.post('/api/qq/groups/{group_id}/brief', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def qq_group_brief(group_id: int, body: dict, response: Response, request: Request):
        if group_id <= 0 or set(body)-{'mode'} or body.get('mode', 'handoff') not in ('handoff', 'catchup'):
            raise HTTPException(400, '无效的交接简报请求')
        bot = qq_bot()
        response.headers['Cache-Control'] = 'no-store, private'
        groups = await asyncio.wait_for(bot.call_api('get_group_list'), 8)
        if str(group_id) not in {str(g.get('group_id')) for g in groups}:
            raise HTTPException(403, '机器人不在该群')
        module = chat_module()
        getter = getattr(module, '_utility_llm', None) if module else None
        llm = getter() if callable(getter) else None
        try:
            result = await group_briefs.generate(bot, group_id, llm, body.get('mode', 'handoff'))
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        audit(request, '生成群交接草稿', f"{group_id} · {result['status']} · {len(result['sources'])}条依据")
        return result

    @app.get('/api/qq/groups/{group_id}/documents', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def qq_group_documents(group_id: int, response: Response, folder_id: str = ''):
        from src.qq_documents import SUFFIXES, MAX_BYTES
        from src.qq_autonomy import SECRET
        if group_id <= 0 or len(folder_id) > 512:
            raise HTTPException(400, '无效的群资料目录')
        bot = qq_bot()
        response.headers['Cache-Control'] = 'no-store, private'
        groups = await asyncio.wait_for(bot.call_api('get_group_list'), 8)
        if str(group_id) not in {str(g.get('group_id')) for g in groups}:
            raise HTTPException(403, '机器人不在该群')
        try:
            raw = await qq_store.file_list(bot, group_id, folder_id)
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        except Exception as error:
            raise HTTPException(502, '群资料目录暂时不可用') from error
        if not isinstance(raw, dict) or any(not isinstance(raw.get(k, []), list) or any(not isinstance(r, dict) for r in raw.get(k, [])) for k in ('files', 'folders')):
            raise HTTPException(502, '群资料目录返回格式异常')
        items = []
        if not folder_id:
            items.extend({'id': str(r.get('folder_id') or ''), 'name': str(r.get('folder_name') or ''),
                          'folder': True} for r in raw.get('folders', []))
        for row in raw.get('files', []):
            name = str(row.get('file_name') or '')
            try:
                size = max(0, int(row.get('file_size') or 0))
            except (ValueError, TypeError, OverflowError) as error:
                raise HTTPException(502, '群资料大小信息异常') from error
            secret = bool(SECRET.search(name)) or name.startswith('.')
            reason = '敏感或隐藏文件' if secret else '超过2MB' if size > MAX_BYTES else '暂不支持此格式' if Path(name).suffix.lower() not in SUFFIXES else ''
            items.append({'id': str(row.get('file_id') or ''), 'name': '[文件名已隐藏]' if secret else name,
                          'size': size, 'folder': False, 'readable': not reason, 'reason': reason})
        return {'group_id': str(group_id), 'folder_id': folder_id, 'items': items[:150],
                'truncated': len(items) > 150, 'scope': 'verified_folder' if folder_id else 'root'}

    @app.put('/api/qq/groups/{group_id}/capabilities', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def qq_group_capabilities(group_id: int, body: dict, request: Request, response: Response):
        if group_id <= 0 or not body or set(body)-{'history', 'files', 'reactions'} or any(v is not None and type(v) is not bool for v in body.values()):
            raise HTTPException(400, '仅可调整本群的上下文、文件和表情能力；null表示跟随全局')
        bot = qq_bot()
        groups = await asyncio.wait_for(bot.call_api('get_group_list'), 8)
        if str(group_id) not in {str(g.get('group_id')) for g in groups}:
            raise HTTPException(403, '机器人不在该群')
        # Read/merge/configure are synchronous here: no intervening await may
        # replace another group's settings or an unchanged field in this group.
        scopes = qq_store.snapshot()['policy']['scopes']
        key = 'group:'+str(group_id)
        rule = dict(scopes.get(key, {}))
        for name, value in body.items():
            if value is None:
                rule.pop(name, None)
            else:
                rule[name] = value
        if rule:
            scopes[key] = rule
        else:
            scopes.pop(key, None)
        try:
            result = qq_store.configure({'scopes': scopes})
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        response.headers['Cache-Control'] = 'no-store, private'
        audit(request, '调整单群工具能力', str(group_id)+' · '+','.join(body))
        return result

    @app.get('/api/qq/groups/{group_id}/{kind}', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def qq_group_resource(group_id: int, kind: str, response: Response, keyword: str = ''):
        from src.qq_bridge import group_resource
        if kind not in ('history', 'files', 'todos', 'essence', 'notices', 'storage', 'honor') or group_id <= 0 or len(keyword) > 80:
            raise HTTPException(400, '无效的群资源查询')
        bot = qq_bot()
        response.headers['Cache-Control'] = 'no-store, private'
        try:
            groups = await asyncio.wait_for(bot.call_api('get_group_list'), 8)
            if str(group_id) not in {str(g.get('group_id')) for g in groups}:
                raise HTTPException(403, '机器人不在该群')
            return await group_resource(bot, group_id, kind, keyword=keyword)
        except HTTPException:
            raise
        except Exception as error:
            raise HTTPException(502, '群资源读取失败：' + type(error).__name__) from error

    @app.get('/api/qq/games', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def qq_games(response: Response, view: str = 'live', status: str | None = None):
        if status:
            view = 'live' if status == 'open' else 'all' if status == 'all' else view
        if view not in ('live', 'signup', 'active', 'reminded', 'finished', 'all'):
            raise HTTPException(400, '视图只能是 live、signup、active、finished 或 all')
        bot = qq_bot()
        all_rows = game_store.all_rows(str(bot.self_id))
        groups, warning = {}, ''
        try:
            raw = await asyncio.wait_for(bot.call_api('get_group_list'), 8)
            if isinstance(raw, list):
                groups = {str(g.get('group_id')): str(g.get('group_name') or '') for g in raw if isinstance(g, dict)}
        except Exception:
            warning = '群名暂不可用；约局列表仍会显示群号。'
        now = game_store.clock()
        for row in all_rows:
            members = row.get('members') or []
            row['group_name'] = groups.get(str(row.get('gid')), '')
            row['host_name'] = next((m.get('name') or m.get('qq') for m in members if str(m.get('qq')) == str(row.get('host'))), '')
            row['member_count'] = len(members)
            row['formal_count'] = sum(m.get('status', 'formal') == 'formal' for m in members)
            row['waitlist_count'] = sum(m.get('status') == 'waitlist' for m in members)
            row['total_capacity'] = (int(row.get('capacity') or 0) + int(row.get('waitlist_capacity') or 0)) if row.get('capacity') else 0
            row['phase'] = game_phase(row, now)
            row['phase_name'] = phase_name(row, now)
            live = row.get('state') == 'open' and row['phase'] in ('signup', 'active')
            row['can_remind'] = live and not bool(row.get('reminded'))
            row['can_cancel'] = live
        def keeps(row):
            if view == 'all':
                return True
            if view == 'live':
                return row['phase'] in ('signup', 'active')
            if view == 'finished':
                return row['phase'] in ('ended', 'cancelled')
            if view == 'reminded':
                return bool(row.get('reminded'))
            return row['phase'] == view
        rows = [row for row in all_rows if keeps(row)]
        live_rows = [row for row in all_rows if row['phase'] in ('signup', 'active')]
        response.headers['Cache-Control'] = 'no-store, private'
        return {'account': str(bot.self_id), 'now': now, 'view': view, 'items': rows, 'groups': groups,
                'warning': warning,
                'stats': {
                    'signup': sum(row['phase'] == 'signup' for row in all_rows),
                    'active': sum(row['phase'] == 'active' for row in all_rows),
                    'finished': sum(row['phase'] in ('ended', 'cancelled') for row in all_rows),
                    'open': len(live_rows),
                    'reminded': sum(bool(row.get('reminded')) for row in all_rows),
                    'members': sum(int(row.get('member_count') or 0) for row in live_rows),
                    'formal': sum(int(row.get('formal_count') or 0) for row in live_rows),
                    'waitlist': sum(int(row.get('waitlist_count') or 0) for row in live_rows),
                    'due_soon': sum(row['phase'] == 'signup' and 0 <= float(row.get('start') or 0) - now <= 600 for row in live_rows),
                }}

    @app.post('/api/qq/games', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def qq_game_create(body: dict, request: Request, response: Response):
        allowed = {'gid', 'game', 'start', 'capacity', 'waitlist_capacity', 'state', 'host', 'host_name', 'members', 'notes', 'trigger_rules'}
        if not isinstance(body, dict) or set(body) - allowed or not body.get('gid') or not body.get('game') or body.get('start') is None or not body.get('host'):
            raise HTTPException(400, '需要群号、游戏、开始时间和组织者')
        members = body.get('members') or []
        if not isinstance(members, list) or any(not isinstance(m, dict) for m in members):
            raise HTTPException(400, '成员数据不合法')
        if body.get('notes') is not None and not isinstance(body.get('notes'), (list, dict)):
            raise HTTPException(400, '备注需是键值对列表')
        if body.get('trigger_rules') is not None and not isinstance(body.get('trigger_rules'), (list, dict)):
            raise HTTPException(400, '触发条件需是规则列表')
        bot = qq_bot()
        try:
            raw = await asyncio.wait_for(bot.call_api('get_group_list'), 8)
        except Exception as error:
            raise HTTPException(502, '群列表暂不可用：' + type(error).__name__) from error
        if str(body['gid']) not in {str(g.get('group_id')) for g in raw}:
            raise HTTPException(403, '机器人不在该群')
        try:
            row = await run_in_threadpool(game_store.admin_create, str(bot.self_id), str(body['gid']),
                                          str(body['host']), str(body.get('host_name') or ''), str(body['game']),
                                          body['start'], body.get('capacity', 0), body.get('state', 'open'), members,
                                          body.get('waitlist_capacity', 0), body.get('notes'), body.get('trigger_rules'))
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        response.headers['Cache-Control'] = 'no-store, private'
        audit(request, '新建开黑局', f'{body["gid"]} · {body["game"]}')
        return {'ok': True, 'item': row}

    @app.put('/api/qq/games/{game_id}', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def qq_game_update(game_id: int, body: dict, request: Request, response: Response):
        if game_id <= 0 or not isinstance(body, dict) or not body or set(body) - {'gid', 'game', 'start', 'capacity', 'waitlist_capacity', 'state', 'host', 'host_name', 'notes', 'trigger_rules'}:
            raise HTTPException(400, '没有可编辑字段')
        bot = qq_bot()
        current = game_store.get(game_id, str(bot.self_id))
        if current is None:
            raise HTTPException(404, '约局不存在')
        if 'gid' in body:
            try:
                raw = await asyncio.wait_for(bot.call_api('get_group_list'), 8)
            except Exception as error:
                raise HTTPException(502, '群列表暂不可用：' + type(error).__name__) from error
            if str(body['gid']) not in {str(g.get('group_id')) for g in raw}:
                raise HTTPException(403, '机器人不在该群')
        try:
            row = await run_in_threadpool(game_store.admin_update, game_id, str(bot.self_id), body)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        response.headers['Cache-Control'] = 'no-store, private'
        audit(request, '编辑开黑局', f'#{game_id} · {row.get("game")}')
        return {'ok': True, 'item': row}

    @app.post('/api/qq/games/{game_id}/members', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def qq_game_member_add(game_id: int, body: dict, request: Request, response: Response):
        if game_id <= 0 or not isinstance(body, dict) or set(body) - {'qq', 'name', 'status'} or not body.get('qq') or body.get('status', 'auto') not in ('auto', 'formal', 'waitlist'):
            raise HTTPException(400, '需要成员 QQ')
        bot = qq_bot()
        if game_store.get(game_id, str(bot.self_id)) is None:
            raise HTTPException(404, '约局不存在')
        try:
            row = await run_in_threadpool(game_store.add_member, game_id, str(bot.self_id), body['qq'], body.get('name') or '', body.get('status', 'auto'))
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        response.headers['Cache-Control'] = 'no-store, private'
        audit(request, '添加开黑成员', f'#{game_id} · {body["qq"]}')
        return {'ok': True, 'item': row}

    @app.delete('/api/qq/games/{game_id}/members/{member_qq}', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def qq_game_member_remove(game_id: int, member_qq: str, request: Request, response: Response):
        if game_id <= 0 or not member_qq.strip():
            raise HTTPException(400, '无效的成员')
        bot = qq_bot()
        if game_store.get(game_id, str(bot.self_id)) is None:
            raise HTTPException(404, '约局不存在')
        try:
            removed = await run_in_threadpool(game_store.remove_member, game_id, str(bot.self_id), member_qq)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        if not removed:
            raise HTTPException(404, '成员不在这一局')
        response.headers['Cache-Control'] = 'no-store, private'
        audit(request, '移除开黑成员', f'#{game_id} · {member_qq}')
        return {'ok': True, 'item': game_store.get(game_id, str(bot.self_id))}

    @app.get('/api/qq/games/{game_id}/member-candidates', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def qq_game_member_candidates(game_id: int, response: Response, q: str = ''):
        if game_id <= 0 or len(q) > 60:
            raise HTTPException(400, '无效的成员查询')
        bot = qq_bot()
        row = game_store.get(game_id, str(bot.self_id))
        if row is None:
            raise HTTPException(404, '约局不存在')
        try:
            gid = int(row['gid'])
        except (TypeError, ValueError) as error:
            raise HTTPException(400, '该约局的群号不是数字，无法查询群成员') from error
        try:
            raw = await asyncio.wait_for(bot.call_api('get_group_member_list', group_id=gid), 10)
        except Exception as error:
            raise HTTPException(502, '群成员列表暂不可用：' + type(error).__name__) from error
        if not isinstance(raw, list):
            raise HTTPException(502, '群成员列表格式异常')
        need = q.strip().lower()
        existing = {str(m.get('qq')): str(m.get('status') or 'formal') for m in row.get('members') or []}
        items = []
        for member in raw:
            if not isinstance(member, dict):
                continue
            qq = str(member.get('user_id') or member.get('qq') or '').strip()
            if not qq:
                continue
            nickname = str(member.get('nickname') or '').strip()
            card = str(member.get('card') or '').strip()
            remark = str(member.get('remark') or '').strip()
            name = card or nickname or qq
            haystack = ' '.join((qq, nickname, card, remark)).lower()
            if need and need not in haystack:
                continue
            items.append({'qq': qq, 'name': name[:60], 'nickname': nickname[:60], 'card': card[:60],
                          'in_game': qq in existing, 'status': existing.get(qq, '')})
            if len(items) >= 30:
                break
        response.headers['Cache-Control'] = 'no-store, private'
        return {'items': items, 'q': q}

    @app.post('/api/qq/games/{game_id}/members/{member_qq}/at', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def qq_game_member_at(game_id: int, member_qq: str, request: Request, response: Response):
        if game_id <= 0 or not member_qq.strip():
            raise HTTPException(400, '无效的成员')
        bot = qq_bot()
        row = game_store.get(game_id, str(bot.self_id))
        if row is None:
            raise HTTPException(404, '约局不存在')
        member = next((m for m in row.get('members') or [] if str(m.get('qq')) == member_qq), None)
        if member is None:
            raise HTTPException(404, '成员不在这一局')
        try:
            groups = await asyncio.wait_for(bot.call_api('get_group_list'), 8)
            if str(row['gid']) not in {str(g.get('group_id')) for g in groups}:
                raise HTTPException(403, '机器人不在该群')
        except HTTPException:
            raise
        except Exception as error:
            raise HTTPException(502, '群列表暂不可用，未发送 @：' + type(error).__name__) from error
        from nonebot.adapters.onebot.v11 import Message, MessageSegment
        label = '（候补）' if member.get('status') == 'waitlist' else ''
        message = Message([MessageSegment.at(member_qq), MessageSegment.text(f' {row["game"]} 这局{label}看到回我一下～')])
        try:
            await asyncio.wait_for(bot.send_group_msg(group_id=int(row['gid']), message=message), 10)
        except Exception as error:
            raise HTTPException(502, '单独 @ 发送失败：' + type(error).__name__) from error
        response.headers['Cache-Control'] = 'no-store, private'
        audit(request, '单独@开黑成员', f'#{game_id} · {member_qq}')
        return {'ok': True, 'at': member_qq, 'item': game_store.get(game_id, str(bot.self_id))}

    @app.post('/api/qq/games/{game_id}/remind', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def qq_game_remind(game_id: int, request: Request, response: Response, body: dict | None = None):
        if game_id <= 0 or (body is not None and body):
            raise HTTPException(400, '无效的提醒请求')
        bot = qq_bot()
        row = game_store.get(game_id, str(bot.self_id))
        if row is None:
            raise HTTPException(404, '约局不存在')
        if row.get('state') != 'open':
            raise HTTPException(409, '这局已经结束或取消，不能提醒')
        try:
            groups = await asyncio.wait_for(bot.call_api('get_group_list'), 8)
            if str(row['gid']) not in {str(g.get('group_id')) for g in groups}:
                raise HTTPException(403, '机器人不在该群')
        except HTTPException:
            raise
        except Exception as error:
            raise HTTPException(502, '群列表暂不可用，未发送提醒：' + type(error).__name__) from error
        try:
            await send_game_reminder(bot, row)
        except Exception as error:
            raise HTTPException(502, '群提醒发送失败：' + type(error).__name__) from error
        game_store.mark_reminded(game_id, str(bot.self_id))
        response.headers['Cache-Control'] = 'no-store, private'
        audit(request, '提醒开黑局', f'{row["gid"]} · {row["game"]} · @{len(row.get("members") or [])}人')
        return {'ok': True, 'at': [str(m.get('qq')) for m in row.get('members') or []],
                'item': game_store.get(game_id, str(bot.self_id))}

    @app.post('/api/qq/games/{game_id}/cancel', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def qq_game_cancel(game_id: int, request: Request, response: Response, body: dict | None = None):
        if game_id <= 0 or (body is not None and body):
            raise HTTPException(400, '无效的取消请求')
        bot = qq_bot()
        row = game_store.get(game_id, str(bot.self_id))
        if row is None:
            raise HTTPException(404, '约局不存在')
        if not game_store.cancel(game_id, str(bot.self_id)):
            raise HTTPException(409, '只有进行中的约局可以取消')
        response.headers['Cache-Control'] = 'no-store, private'
        audit(request, '取消开黑局', f'{row["gid"]} · {row["game"]}')
        return {'ok': True, 'item': game_store.get(game_id, str(bot.self_id))}

    @app.get('/api/agent', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_status():
        result = await run_in_threadpool(agent_control.snapshot)
        result['providers'] = PROVIDERS
        return result

    def task_summary(task):
        return {k: v for k, v in task.items() if k not in ('result', 'description')} | {'summary': task['description'][:100]}

    @app.get('/api/agent/apps', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_apps():
        data = await run_in_threadpool(agent_control.snapshot)
        runtime = await run_in_threadpool(node_queue.snapshot)
        for job in runtime['browser_jobs']:
            result = job.pop('result')
            job['summary'] = result.get('title') or result.get('error') if result else '读取公开网页'
            job['has_screenshot'] = bool(result and result.get('has_screenshot'))
        return {**runtime, 'policy': data['policy'], 'providers': PROVIDERS,
                'phone_tasks': phone_agent.list_tasks()[:30],
                'tasks': [task_summary(t) for t in data['tasks'] if not is_payment(t)],
                'grants': [g for g in data['grants'] if not is_payment(g)],
                'capabilities': [c for c in data['capabilities'] if not is_payment({'capability': c['id']})],
                'plans': [{k: v for k, v in p.items() if k not in ('steps', 'description')} for p in data['plans']]}

    @app.get('/api/agent/autonomy', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_autonomy():
        from src.agent.autonomy_policy import policy
        from src.agent.chat_preferences import preferences
        return {**policy.snapshot(),'preferences':preferences.rows[-100:]}

    @app.put('/api/agent/autonomy', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_autonomy_update(body: dict,request: Request):
        from src.agent.autonomy_policy import policy
        if not body or set(body)-{'quiet','notify_payments'} or any(type(v)is not bool for v in body.values()):
            raise HTTPException(400,'仅接受静默和支付通知的布尔设置')
        with policy.lock:policy.data.update(body);policy.save()
        audit(request,'更新Agent自主偏好',json.dumps(body))
        return policy.snapshot()

    @app.post('/api/agent/preferences', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_preferences_ingest(body: dict,request: Request):
        from src.agent.chat_preferences import preferences
        text=body.get('text')
        if not isinstance(text,str) or not 2<=len(text)<=1600:raise HTTPException(400,'请输入2到1600字的行为要求')
        class Adapter:
            available=phone_agent.llm.available
            async def chat(self,messages,**kwargs):
                return json.dumps(await phone_agent.llm.decide(json.dumps(messages,ensure_ascii=False)),ensure_ascii=False)
        before=len(preferences.rows)
        await preferences.learn(Adapter(),text,actor(request)[0],True,'owner-panel')
        return {'stored':len(preferences.rows)-before}

    @app.delete('/api/agent/preferences/{identifier}', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_preferences_remove(identifier: str,request: Request):
        from src.agent.chat_preferences import preferences
        with preferences.lock:
            preferences.rows=[r for r in preferences.rows if r['id']!=identifier];preferences.save()
        audit(request,'删除Agent行为偏好',identifier)
        return {'ok':True}

    @app.get('/api/agent/minecraft', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def minecraft_status():
        return minecraft.snapshot()

    @app.get('/api/agent/minecraft/runs/{identifier}', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def minecraft_detail(identifier: str):
        row=next((r for r in minecraft.snapshot(detail=True)['runs'] if r['id']==identifier),None)
        if not row:raise HTTPException(404,'游玩记录不存在')
        return row

    @app.get('/api/agent/minecraft/projects/{identifier}', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def minecraft_project_detail(identifier: str):
        row=next((p for p in minecraft.snapshot(detail=True)['projects'] if p['id']==identifier),None)
        if not row:raise HTTPException(404,'游戏计划不存在')
        return row

    @app.put('/api/agent/minecraft/policy', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def minecraft_policy(body: dict,request: Request):
        from src.agent.minecraft import GameError
        try:result=minecraft.policy(body)
        except GameError as error:raise HTTPException(400,str(error)) from error
        audit(request,'调整MC游玩预算',json.dumps(body));return result

    @app.post('/api/agent/minecraft/start', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def minecraft_start(request: Request):
        from src.agent.minecraft import GameError
        # This owner instruction authorizes one session, not unlimited play or operator rights.
        if phone_agent._active or phone_play.snapshot().get('status')!='idle':raise HTTPException(409,'其他电脑任务正在进行')
        try:result=await minecraft.start(manual=True)
        except GameError as error:raise HTTPException(409,str(error)) from error
        audit(request,'启动自主MC游玩',result['id']);return result

    @app.post('/api/agent/minecraft/stop', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def minecraft_stop(request: Request):
        result=await minecraft.stop();audit(request,'停止MC游玩','');return result

    @app.get('/api/agent/learning', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_learning():
        from src.agent.learning import learning_store
        data=learning_store.snapshot()
        return {'policy':data['policy'],'runs':data['runs'][-30:],
                'notes':[{k:v for k,v in n.items() if k!='quote'} for n in reversed(data['notes'][-100:])]}

    @app.post('/api/agent/learning/read', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_learning_read(request: Request,body: dict | None = None):
        from src.agent.learning_runtime import read_once
        try:started=await read_once(phone_agent,phone_play,manual=True,search=getattr(chat_module(),'search_web_now',None),topic=(body or {}).get('topic'))
        except (ValueError,asyncio.TimeoutError) as error:raise HTTPException(409,'自主阅读暂未启动，请检查模型与电脑状态') from error
        audit(request,'尝试自主阅读','started' if started else 'skipped')
        return {'started':started}

    @app.get('/api/agent/learning/notes/{note_id}', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_learning_note(note_id: str):
        from src.agent.learning import learning_store
        row=next((n for n in learning_store.snapshot()['notes'] if n['id']==note_id),None)
        if not row:raise HTTPException(404,'阅读笔记不存在')
        return row

    @app.put('/api/agent/learning/policy', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_learning_policy(body: dict,request: Request):
        from src.agent.learning import learning_store
        try:result=learning_store.policy(body.get('enabled'),body.get('daily_minutes',20),body.get('cooldown_hours',6))
        except ValueError as error:raise HTTPException(400,str(error)) from error
        audit(request,'调整自主阅读策略',json.dumps(result,ensure_ascii=False))
        return result

    @app.get('/api/agent/social', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_social():
        return social_store.snapshot()

    @app.post('/api/agent/social/posts', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_social_post(body: dict, request: Request):
        try:
            result = social_store.register(str(body.get('platform') or ''), str(body.get('external_id') or ''),
                str(body.get('title') or ''), str(body.get('account') or ''))
            with social_store.db() as db:
                db.execute('UPDATE social_posts SET enabled=? WHERE id=?', (int(body.get('enabled') is True), result['id']))
        except SocialError as error:
            raise HTTPException(409,str(error)) from error
        audit(request,'登记评论监控帖子',result['id'])
        return result

    @app.post('/api/agent/social/setup', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_social_setup(request: Request):
        try: result=request_social_setup()
        except AidError as error: raise HTTPException(409,str(error)) from error
        audit(request,'请求QQ提供评论验收目标',result['id'])
        return result

    @app.get('/api/agent/social/replies/{rid}', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_social_reply(rid: str):
        try: return social_store.reply(rid)
        except SocialError as error: raise HTTPException(404,str(error)) from error

    @app.get('/api/agent/aid', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_aid_list():
        return {'items': aid_store.pending()}

    @app.post('/api/agent/aid', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_aid_create(body: dict, request: Request):
        try:
            result = aid_store.create(
                str(body.get('kind') or ''), str(body.get('title') or ''),
                str(body.get('detail') or ''), int(body.get('ttl') or 300),
            )
        except (AidError, ValueError, TypeError) as error:
            raise HTTPException(409, str(error)) from error
        audit(request, '创建一次性协助请求', result['id'])
        return result

    @app.post('/api/agent/aid/{aid}/respond', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_aid_respond(aid: str, body: dict, request: Request):
        try:
            result = aid_store.respond(aid, str(body.get('value') or ''), actor(request)[0])
        except (AidError, ValueError) as error:
            raise HTTPException(409, str(error)) from error
        audit(request, '填写一次性协助', aid)
        return result

    @app.post('/api/agent/aid/{aid}/cancel', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_aid_cancel(aid: str, request: Request):
        try:
            aid_store.cancel(aid)
        except AidError as error:
            raise HTTPException(409, str(error)) from error
        audit(request, '取消一次性协助', aid)
        return {'ok': True}

    @app.get('/api/agent/phone', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_phone():
        try:
            return await phone_client.health()
        except (PhoneError, httpx.HTTPError) as error:
            raise HTTPException(409, str(error)) from error

    @app.get('/api/agent/phone/screenshot', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_phone_screenshot():
        try:
            return Response(await phone_client.screenshot(), media_type='image/png')
        except (PhoneError, httpx.HTTPError) as error:
            raise HTTPException(409, str(error)) from error

    @app.post('/api/agent/phone/aid/{aid}', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_phone_aid(aid: str, request: Request):
        try:
            result = await phone_client.type_aid(aid)
        except (PhoneError, AidError) as error:
            raise HTTPException(409, str(error)) from error
        audit(request, '电脑输入一次性协助', aid)
        return result

    @app.get('/api/agent/phone/play', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_phone_play():
        return phone_play.snapshot()

    @app.post('/api/agent/phone/play/request', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_phone_play_request(body: dict, request: Request):
        try:
            result = phone_play.request(str(body.get('plan') or ''), int(body.get('duration_minutes') or 20),
                                        str(body.get('reason') or '想玩一会儿电脑'), force=bool(body.get('force')))
        except (PhonePlayError, ValueError, TypeError) as error:
            raise HTTPException(409, str(error)) from error
        audit(request, '申请电脑时间', result.get('request', {}).get('id', ''))
        return result

    @app.post('/api/agent/phone/play/{rid}/approve', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_phone_play_approve(rid: str, request: Request):
        try:
            result = phone_play.approve(rid, actor(request)[0])
        except PhonePlayError as error:
            raise HTTPException(409, str(error)) from error
        session = result.get('session') or {}
        goal = session.get('plan') or phone_play.snapshot().get('resume_goal') or '玩一会儿电脑'
        try:
            if real_phone:
                await phone_agent.start(goal, max_steps=24)
            elif not await start_social_setup(phone_agent):
                await phone_agent.start(goal, max_steps=24)
            result['task_started'] = True
        except PhoneTaskError as error:
            result['task_started'] = False
            result['task_error'] = str(error)
        audit(request, '批准电脑时间', rid)
        return result

    @app.post('/api/agent/phone/play/{rid}/deny', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_phone_play_deny(rid: str, request: Request):
        try:
            result = phone_play.deny(rid, actor(request)[0])
        except PhonePlayError as error:
            raise HTTPException(409, str(error)) from error
        audit(request, '拒绝电脑时间', rid)
        return result

    @app.post('/api/agent/phone/play/cooldown', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_phone_play_cooldown(body: dict, request: Request):
        try:
            result = phone_play.set_cooldown(int(body.get('minutes') or 90))
        except (PhonePlayError, ValueError, TypeError) as error:
            raise HTTPException(409, str(error)) from error
        audit(request, '设置电脑时间冷却', str(body.get('minutes') or 90))
        return result

    @app.post('/api/agent/phone/play/end', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_phone_play_end(request: Request):
        result = phone_play.end('admin')
        audit(request, '结束电脑时间', '')
        return result

    @app.post('/api/agent/phone/play/plan', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_phone_play_plan(body: dict, request: Request):
        try:
            result = phone_play.update_plan(str(body.get('plan') or ''), body.get('duration_minutes'))
        except (PhonePlayError, ValueError, TypeError) as error:
            raise HTTPException(409, str(error)) from error
        audit(request, '修改电脑计划', '')
        return result

    @app.get('/api/agent/phone/tasks', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_phone_tasks():
        return {'tasks': phone_agent.list_tasks(), 'llm_ready': phone_agent.llm.available}

    @app.post('/api/agent/phone/tasks', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_phone_task_start(body: dict, request: Request):
        created_session=None
        try:
            authorization=None
            if body.get('autonomous_intro') is True:
                authorization={'platform':'xiaoheihe','purpose':'self_introduction','account':str(getattr(driver.config,'xiaoheihe_account','') or os.getenv('XIAOHEIHE_ACCOUNT','') or ''),'max_posts':1,'expires':time.time()+3600}
                if phone_play.snapshot().get('status')=='idle':
                    req=phone_play.request('自主检索其他AI介绍风格，撰写并发布本次清汐自我介绍，不询问超管',45,reason='超管已明确授权本次自主发布',force=True)
                    created_session=phone_play.approve(req['request']['id'],actor='owner-autonomous-intro')['session']['id']
            from src.agent.autonomy_policy import policy as autonomy
            if autonomy.snapshot()['quiet'] and phone_play.snapshot().get('status')=='idle':
                req=phone_play.request(str(body.get('goal') or '')[:500],20,reason='超管已直接下达任务，不重复询问',force=True)
                created_session=phone_play.approve(req['request']['id'],actor='owner-direct-request')['session']['id']
            result = await phone_agent.start(str(body.get('goal') or ''),
                body.get('max_steps'), social_post_id=body.get('social_post_id'),
                no_owner_contact=body.get('no_owner_contact',False),publish_authorization=authorization)
        except (PhoneTaskError,PhonePlayError) as error:
            if created_session and (phone_play.snapshot().get('session') or {}).get('id')==created_session:phone_play.end('task_start_failed')
            raise HTTPException(409, str(error)) from error
        audit(request, '启动电脑自主任务', result['id'])
        return result

    @app.get('/api/agent/phone/tasks/{tid}', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_phone_task(tid: str):
        try:
            return phone_agent.get_task(tid)
        except PhoneTaskError as error:
            raise HTTPException(404, str(error)) from error

    @app.post('/api/agent/phone/tasks/{tid}/cancel', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_phone_task_cancel(tid: str, request: Request):
        try:
            result = phone_agent.cancel(tid)
        except PhoneTaskError as error:
            raise HTTPException(404, str(error)) from error
        audit(request, '取消电脑自主任务', tid)
        return result

    @app.get('/api/agent/phone/tasks/{tid}/screenshot', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_phone_task_shot(tid: str):
        try:
            return Response(await phone_agent.screenshot(tid), media_type='image/png')
        except PhoneTaskError as error:
            raise HTTPException(404, str(error)) from error

    @app.post('/api/agent/phone/action', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_phone_action(body: dict, request: Request):
        try:
            result = await phone_client.action(body)
        except (PhoneError, httpx.HTTPError) as error:
            raise HTTPException(409, str(error)) from error
        audit(request, '电脑动作', str(body.get('action') or '')[:80])
        return result

    @app.post('/api/agent/computer/manual/action', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_computer_manual_action(body: dict, request: Request):
        active=phone_agent.tasks.get(phone_agent._active) if phone_agent._active else None
        if active and active.get('status')!='waiting_aid':
            raise HTTPException(409, 'Bot正在操作，请先停止任务或等待人工协助状态')
        if body.get('action') not in ('tap','drag','swipe','text','key'):
            raise HTTPException(409, '人工接管动作未开放')
        try:result=await phone_client.action(body)
        except (PhoneError,httpx.HTTPError) as error:raise HTTPException(409,str(error)) from error
        audit(request,'人工接管电脑',str(body.get('action') or '')[:40])
        return result

    # 新电脑入口；旧phone路径只兼容既有客户端和历史，不连接Android。
    for legacy_route in list(app.routes):
        if getattr(legacy_route, 'path', '').startswith('/api/agent/phone'):
            app.add_api_route(legacy_route.path.replace('/api/agent/phone', '/api/agent/computer', 1),
                legacy_route.endpoint, methods=legacy_route.methods,
                dependencies=[Depends(authenticated), Depends(agent_owner)])

    @app.get('/api/wechat/contacts', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def wechat_contacts():
        return {'items': wechat_store.list_contacts(), 'source': 'wechat'}

    @app.post('/api/wechat/contacts', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def wechat_contact_add(body: dict, request: Request):
        try:
            row = wechat_store.contact(str(body.get('nickname') or ''), str(body.get('wxid') or ''),
                                       bool(body.get('whitelist')))
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        audit(request, '登记微信联系人', row['id6'])
        return row

    @app.post('/api/wechat/contacts/{id6}/whitelist', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def wechat_contact_whitelist(id6: str, body: dict, request: Request):
        if not wechat_store.set_whitelist(id6, bool(body.get('enabled'))):
            raise HTTPException(404, '联系人不存在')
        audit(request, '切换微信白名单', id6)
        return {'ok': True}

    @app.post('/api/wechat/groups/{id6}/send', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def wechat_group_send(id6: str, body: dict, request: Request):
        text = str(body.get('text') or '').strip()
        if not text or len(text) > 1000:
            raise HTTPException(409, '需要 1–1000 字的分享内容')
        row = next((r for r in wechat_store.list_groups(True) if r['id6'] == id6), None)
        if not row:
            raise HTTPException(409, '该微信群不在白名单')
        from src.agent.wechat_phone import WeChatPhone
        try:
            ok = await WeChatPhone(phone_client).send(row['name'], text)
        except Exception as error:
            raise HTTPException(409, f'微信群分享失败：{type(error).__name__}') from error
        if not ok:
            raise HTTPException(409, '微信群分享未完成')
        audit(request, '微信白名单群分享', id6)
        return {'ok': True, 'id6': id6}

    @app.post('/api/wechat/send', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def wechat_send(body: dict, request: Request):
        id6 = str(body.get('id6') or '').strip()
        text = str(body.get('text') or '').strip()
        if not id6 or not text or len(text) > 1000:
            raise HTTPException(409, '需要联系人编号和 1–1000 字内容')
        row = next((r for r in wechat_store.list_contacts(True) if r['id6'] == id6), None)
        if not row:
            raise HTTPException(409, '该联系人不在微信白名单')
        from src.agent.wechat_phone import WeChatPhone
        from src.agent.wechat_vision import WeChatVisionSkill
        try:
            vision = None
            try:
                import importlib
                chat_module = importlib.import_module('src.yuna_plugins.chat')
                vision, _ = chat_module._vision_client('', 'image')
            except Exception:
                vision = None
            # 先走确定性的已有会话+绿色按钮路径；视觉 Skill 只作为后续回退，避免一次请求卡住。
            ok = await asyncio.wait_for(
                WeChatPhone(phone_client).send(row['remark'] or row['nickname'], text), 90)
        except Exception as error:
            raise HTTPException(409, f'微信发送失败：{type(error).__name__}') from error
        if not ok:
            raise HTTPException(409, '微信发送未完成')
        audit(request, '微信白名单发送', id6)
        return {'ok': True, 'id6': id6}

    @app.get('/api/wechat/groups', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def wechat_groups():
        return {'items': wechat_store.list_groups(), 'source': 'wechat'}

    @app.post('/api/wechat/groups', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def wechat_group_add(body: dict, request: Request):
        try:
            row = wechat_store.group(str(body.get('name') or ''), str(body.get('wxid') or ''),
                                     bool(body.get('whitelist')))
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        audit(request, '登记微信群', row['id6'])
        return row

    @app.post('/api/wechat/groups/{id6}/whitelist', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def wechat_group_whitelist(id6: str, body: dict, request: Request):
        with wechat_store.db() as d:
            changed = d.execute('UPDATE groups SET whitelist=?, updated=? WHERE id6=?',
                                (1 if body.get('enabled') else 0, __import__('time').time(), id6)).rowcount == 1
        if not changed:
            raise HTTPException(404, '群不存在')
        audit(request, '切换微信群白名单', id6)
        return {'ok': True}

    @app.get('/api/agent/payments', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_payments():
        data = await run_in_threadpool(agent_control.snapshot)
        with agent_control._db() as db:
            real_usage = agent_control._usage(db, 'live')
        phone_ops = [t for t in phone_agent.list_tasks() if any(
            word in str(t.get('goal') or '') for word in ('支付', '购物', '购买', '充值', '下单', '付款', '支付宝'))][:30]
        return {'policy': data['policy'], 'usage': data['usage'], 'live_usage': real_usage,
                'live_available': False, 'records': await run_in_threadpool(payments.history),
                'phone_operations': phone_ops,
                'grants': [g for g in data['grants'] if is_payment(g)],
                'capabilities': [c for c in data['capabilities'] if is_payment({'capability': c['id']})],
                'tasks': [task_summary(t) for t in data['tasks'] if is_payment(t)]}

    @app.get('/api/agent/payments/records/{payment_id}', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_payment_detail(payment_id: str):
        return await run_in_threadpool(agent_call, payments.detail, payment_id)

    @app.get('/api/agent/task-detail/{task_id}', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_task_detail(task_id: str):
        with agent_control._tx() as db:
            agent_control._expire(db)
            row = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if not row:
                raise HTTPException(404, '任务不存在')
            return {**agent_control._task(row, db),
                    'audit': [dict(r) for r in db.execute('SELECT * FROM events WHERE task_id=? ORDER BY seq', (task_id,))],
                    'approvals': [dict(r) for r in db.execute('SELECT rule,period,actor,created FROM approvals WHERE task_id=?', (task_id,))]}

    @app.get('/api/agent/plans/{plan_id}', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_plan_detail(plan_id: str):
        with agent_control._db() as db:
            row = db.execute('SELECT * FROM plans WHERE id=?', (plan_id,)).fetchone()
            if not row:
                raise HTTPException(404, '计划不存在')
            return agent_control._plan(row)

    @app.post('/api/agent/nodes', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_node_create(body: AgentNodeBody, request: Request):
        return await run_in_threadpool(agent_call, node_queue.create_node, body.name, body.domains, actor(request)[0])

    @app.put('/api/agent/nodes/{node_id}', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_node_toggle(node_id: str, body: AgentNodeToggle, request: Request):
        return await run_in_threadpool(agent_call, node_queue.update_node, node_id, body.enabled, actor(request)[0])

    @app.post('/api/agent/browser-jobs', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_browser_submit(body: AgentBrowserBody, request: Request):
        return await run_in_threadpool(agent_call, node_queue.submit, body.node_id, body.url, actor(request)[0])

    @app.get('/api/agent/browser-jobs/{job_id}', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_browser_detail(job_id: str):
        with agent_control._db() as db:
            row = db.execute('SELECT id,node_id,url,state,created,result FROM browser_jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise HTTPException(404, '行为记录不存在')
            return {**dict(row), 'result': json.loads(row['result']) if row['result'] else None}

    @app.get('/api/agent/browser-jobs/{job_id}/screenshot', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_browser_screenshot(job_id: str):
        if not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise HTTPException(404, '截图不存在')
        path = node_queue.artifacts / (job_id + '.png')
        if not path.is_file():
            raise HTTPException(404, '截图不存在')
        return FileResponse(path, media_type='image/png')

    def worker_token(request: Request):
        if not request.client or request.client.host not in ('127.0.0.1', '::1'):
            raise HTTPException(403, '执行节点须通过SSH隧道连接')
        value = request.headers.get('authorization', '')
        if not value.startswith('Bearer '):
            raise HTTPException(401, '缺少节点认证')
        return value[7:]

    @app.post('/api/agent/worker/claim')
    async def agent_worker_claim(request: Request):
        token = worker_token(request)
        return await run_in_threadpool(agent_call, node_queue.claim, token)

    @app.post('/api/agent/worker/complete')
    async def agent_worker_complete(body: AgentWorkerResult, request: Request):
        token = worker_token(request)
        return await run_in_threadpool(agent_call, node_queue.complete, token, body.job_id, body.lease, body.result)

    @app.get('/api/agent/plan', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_plan():
        path = DOCS / 'AGENT_PLATFORM.md'
        if not path.is_file():
            raise HTTPException(404, '方案文档尚未部署')
        return FileResponse(path, filename='AGENT_PLATFORM.md', media_type='text/markdown')

    @app.post('/api/agent/deepseek/balance', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_deepseek_balance(request: Request):
        key = str(getattr(driver.config, 'agent_deepseek_api_key', '') or '')
        base = str(getattr(driver.config, 'chat_llm_api_base', '') or '')
        if not key and urlsplit(base).hostname == 'api.deepseek.com' and urlsplit(base).scheme == 'https':
            key = str(getattr(driver.config, 'chat_llm_api_key', '') or '')
        try:
            result = await deepseek_balance(key)
        except (ValueError, httpx.HTTPError) as error:
            detail = str(error) if isinstance(error, ValueError) else 'DeepSeek 余额查询网络错误'
            raise HTTPException(409, detail) from error
        audit(request, 'DeepSeek余额查询', '只读；未充值')
        return result

    @app.post('/api/agent/plans', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_plan_create(body: AgentPlanBody, request: Request):
        plan = {'goal': body.goal, 'description': body.description, 'steps': body.steps}
        try:
            result = await run_in_threadpool(
                agent_call, agent_control.propose_plan, plan, body.idempotency_key, actor(request)[0]
            )
        except ControlError as error:
            raise HTTPException(409, str(error))
        audit(request, '创建自主计划', body.goal[:80])
        return result

    @app.post('/api/agent/plans/{plan_id}/cancel', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_plan_cancel(plan_id: str, request: Request):
        try:
            result = await run_in_threadpool(agent_call, agent_control.cancel_plan, plan_id, actor(request)[0])
        except ControlError as error:
            raise HTTPException(409, str(error))
        audit(request, '取消自主计划', plan_id)
        return result

    @app.put('/api/agent/policy', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_policy(body: AgentPolicyBody, request: Request):
        result = await run_in_threadpool(agent_call, agent_control.set_policy, body.model_dump(), actor(request)[0])
        audit(request, '自主能力全局策略', '暂停' if body.paused else '恢复模拟授权')
        return result

    @app.put('/api/agent/grants', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_grant(body: AgentGrantInput, request: Request):
        result = await run_in_threadpool(agent_call, agent_control.save_grant, body.model_dump(), actor(request)[0])
        audit(request, '自主能力授权', body.id)
        return result

    @app.post('/api/agent/tasks', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_task(body: AgentTaskBody, request: Request):
        task = body.model_dump()
        idem = task.pop('idempotency_key')
        result = await run_in_threadpool(agent_call, agent_control.propose, task, idem, actor(request)[0])
        audit(request, '创建模拟任务', result['id'])
        return result

    @app.post('/api/agent/tasks/{task_id}/{action}', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def agent_action(task_id: str, action: str, request: Request):
        result = await run_in_threadpool(agent_call, agent_control.transition, task_id, action, actor(request)[0])
        audit(request, '自主任务操作', f'{task_id} {action}')
        return result

    @app.get('/api/roadmap', dependencies=[Depends(authenticated)])
    async def roadmap():
        if not ROADMAP_FILE.is_file():
            raise HTTPException(404, '开发路线文档尚未部署')
        return PlainTextResponse(
            ROADMAP_FILE.read_text(encoding='utf-8'),
            media_type='text/markdown; charset=utf-8',
        )

    @app.get('/api/overview', dependencies=[Depends(authenticated)])
    async def overview():
        bots = [{'id': bot.self_id, 'adapter': bot.adapter.get_name()} for bot in nonebot.get_bots().values()]
        metrics = {}
        try:
            import psutil
            process = psutil.Process()
            metrics = {'cpu': psutil.cpu_percent(), 'memory_mb': round(process.memory_info().rss / 1024**2, 1)}
        except ImportError:
            pass
        health = collect_health()
        return {'uptime': int(time.time() - started), 'bots': bots, 'events': counters['events'],
                'plugins': len(nonebot.get_loaded_plugins()), 'disabled': len(disabled),
                'environment': str(driver.config.environment), 'metrics': metrics,
                'version': VERSION, 'started': int(started),
                'health': {'status': health['status'], 'warnings': health['warnings'],
                           'disk': health['disk'], 'backups': health['backups']},
                'now': int(time.time())}

    @app.get('/api/plugins', dependencies=[Depends(authenticated)])
    async def plugins():
        return [{'id': p.name, 'name': p.metadata.name if p.metadata else p.name,
                 'description': p.metadata.description if p.metadata else '',
                 'usage': p.metadata.usage if p.metadata else '',
                 'matchers': len(p.matcher), 'enabled': p.name not in disabled}
                for p in sorted(nonebot.get_loaded_plugins(), key=lambda p: p.name)]

    @app.put('/api/plugins/{name}', dependencies=[Depends(authenticated)])
    async def toggle(name: str, body: Toggle, request: Request):
        if name not in {p.name for p in nonebot.get_loaded_plugins()}:
            raise HTTPException(404, '插件不存在')
        updated = disabled.copy()
        updated.discard(name) if body.enabled else updated.add(name)
        atomic_write(disabled_file, json.dumps(sorted(updated)))
        disabled.clear()
        disabled.update(updated)
        logger.info(f'管理页面：插件 {name} 事件响应已{"启用" if body.enabled else "停用"}')
        audit(request, '插件开关', f'{name} → {"启用" if body.enabled else "停用"}')
        return {'ok': True}

    @app.get('/api/config', dependencies=[Depends(authenticated)])
    async def config():
        return {'file': env_path().name, 'items': [{'key': key, 'value': '' if SECRET.search(key) else value,
                'secret': bool(SECRET.search(key)), 'configured': bool(value.strip('"\' '))}
                for key, value in sorted(read_config().items())]}

    def update_runtime_config(values: dict) -> dict:
        """把配置同步进 driver.config / os.environ，并尝试原地热加载插件。

        聊天插件实现了 ``reload_config()``，可以保留滚动上下文、后台任务与事件
        响应器；其它插件暂未提供回调，仍会在响应里标记为需要重启。
        """
        prefix_to_plugin = {
            prefix: name for name, prefix in PLUGIN_CONFIG_PREFIX.items()
        }
        applied: list[str] = []
        restart_keys: list[str] = []
        errors: list[str] = []
        chat_touched = False
        for key, raw in values.items():
            if key in RUNTIME_CONFIG_SKIP:
                restart_keys.append(key)
                continue
            plugin_name = next(
                (name for prefix, name in prefix_to_plugin.items()
                 if key.startswith(prefix)),
                '',
            )
            if not plugin_name and not hasattr(driver.config, key.lower()):
                # 未知的全局键不塞进 driver.config，避免污染运行时配置
                restart_keys.append(key)
                continue
            try:
                value = parse_runtime_value(raw)
                setattr(driver.config, key.lower(), value)
                os.environ[key] = str(raw)
                applied.append(key)
                if plugin_name == 'chat':
                    chat_touched = True
                elif plugin_name:
                    restart_keys.append(key)
            except Exception as error:
                errors.append(f'{key}: {error}')
                restart_keys.append(key)

        hot_keys: list[str] = []
        if chat_touched:
            chat_plugin = next(
                (p for p in nonebot.get_loaded_plugins() if p.name == 'chat'),
                None,
            )
            reload_fn = getattr(chat_plugin.module, 'reload_config', None) if chat_plugin else None
            if callable(reload_fn):
                try:
                    reload_fn()
                    hot_keys.extend(k for k in applied if k.startswith('CHAT_'))
                except Exception as error:
                    logger.warning(f'管理页面：chat 配置热加载失败：{error}')
                    errors.append(f'chat.reload_config: {error}')
            else:
                errors.append('chat 插件未实现 reload_config()')

        return {
            'applied': applied,
            'hot_keys': sorted(set(hot_keys)),
            'restart_keys': sorted(set(restart_keys) | (set(applied) - set(hot_keys))),
            'errors': errors,
        }

    def apply_config(values: dict) -> tuple[int, dict]:
        for key, value in values.items():
            if not re.fullmatch(r'[A-Z][A-Z0-9_]*', key):
                raise HTTPException(400, f'配置键无效：{key}')
            if '\n' in value or '\r' in value or len(value) > 8192:
                raise HTTPException(400, f'配置值无效：{key}')
        path = env_path()
        old = path.read_text(encoding='utf-8-sig') if path.exists() else ''
        lines = []
        for line in old.splitlines():
            match = re.match(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=', line)
            key = match[1].upper() if match else None
            lines.append(f'{key}={values[key]}' if key in values else line)
        present = {m[1].upper() for m in re.finditer(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=', old, re.M)}
        lines.extend(f'{key}={value}' for key, value in values.items() if key not in present)
        if path.exists():
            atomic_write(state / 'config.backup', old)
        atomic_write(path, '\n'.join(lines) + '\n')
        runtime = update_runtime_config(values)
        return len(values), runtime

    @app.put('/api/config', dependencies=[Depends(authenticated)])
    async def save_config(body: ConfigChange, request: Request):
        count, runtime = apply_config(body.values)
        restart_required = bool(runtime['restart_keys'] or runtime['errors'])
        logger.info(
            f'管理页面：保存 {count} 项配置；热加载 {len(runtime["hot_keys"])} 项，'
            f'需重启 {len(runtime["restart_keys"])} 项'
        )
        audit(request, '保存配置', '、'.join(sorted(body.values)))
        return {'ok': True, 'restart_required': restart_required,
                'hot_reloaded': bool(runtime['hot_keys']),
                'hot_keys': runtime['hot_keys'],
                'restart_keys': runtime['restart_keys'],
                'errors': runtime['errors']}

    @app.get('/api/logs', dependencies=[Depends(authenticated)])
    async def get_logs():
        return {'items': list(logs)}

    @app.post('/api/messages', dependencies=[Depends(authenticated)])
    async def send(body: SendMessage, request: Request):
        bot = nonebot.get_bots().get(body.bot_id)
        if not bot:
            raise HTTPException(409, '机器人已离线')
        if bot.adapter.get_name() != 'OneBot V11':
            raise HTTPException(400, '消息发送当前支持 OneBot V11')
        if body.target_type not in {'group', 'private'} or not body.target_id.isdigit() or int(body.target_id) <= 0:
            raise HTTPException(400, '请选择群聊或私聊，并填写有效数字 ID')
        try:
            result = await asyncio.wait_for(bot.call_api('send_msg', message_type=body.target_type,
                **{f'{body.target_type}_id' if body.target_type == 'group' else 'user_id': int(body.target_id)},
                message=[{'type': 'text', 'data': {'text': body.message}}]), timeout=20)
        except Exception:
            raise HTTPException(502, '发送失败，请检查连接、权限和目标 ID')
        logger.info(f'管理页面：机器人 {body.bot_id} 向 {body.target_type} {body.target_id} 发送消息')
        audit(request, '发送消息', f'{body.target_type} {body.target_id}')
        return {'ok': True, 'message_id': result.get('message_id') if isinstance(result, dict) else None}

    @app.post('/api/messages/forward', dependencies=[Depends(authenticated)])
    async def send_forward(body: SendForwardMessage, request: Request):
        bot = nonebot.get_bots().get(body.bot_id)
        if not bot:
            raise HTTPException(409, '机器人已离线')
        if bot.adapter.get_name() != 'OneBot V11':
            raise HTTPException(400, '消息发送当前支持 OneBot V11')
        if body.target_type != 'group' or not body.target_id.isdigit() or int(body.target_id) <= 0:
            raise HTTPException(400, '合并转发只能发送到有效群号')
        if not isinstance(body.messages, list) or not 1 <= len(body.messages) <= 30:
            raise HTTPException(400, '合并转发节点需为 1—30 条')
        try:
            result = await asyncio.wait_for(bot.call_api('send_group_forward_msg',
                group_id=int(body.target_id), messages=body.messages), timeout=20)
        except Exception as error:
            raise HTTPException(502, '合并转发发送失败：' + type(error).__name__) from error
        logger.info(f'管理页面：机器人 {body.bot_id} 向群 {body.target_id} 发送合并转发')
        audit(request, '发送合并转发', f'群 {body.target_id} · {len(body.messages)} 条')
        return {'ok': True, 'message_id': result.get('message_id') if isinstance(result, dict) else None}

    # ---------- 各插件独立配置 ----------

    def plugin_prefix(name: str) -> str:
        return PLUGIN_CONFIG_PREFIX.get(name, f'{name.upper()}_')

    def coerce_config_value(kind: str, value: str) -> str:
        if kind == 'bool':
            lowered = value.strip().lower()
            if lowered in ('true', '1', 'yes', 'on'):
                return 'true'
            if lowered in ('false', '0', 'no', 'off'):
                return 'false'
            raise HTTPException(400, f'布尔值无效：{value}')
        if kind == 'int':
            try:
                int(value)
            except (TypeError, ValueError):
                raise HTTPException(400, f'整数无效：{value}')
        elif kind == 'float':
            try:
                float(value)
            except (TypeError, ValueError):
                raise HTTPException(400, f'小数无效：{value}')
        return value

    def plugin_config_items(name: str):
        meta = PLUGIN_CONFIG_META.get(name, {})
        prefix = plugin_prefix(name)
        existing = read_config()
        keys = set(meta)
        keys.update(k for k in existing if k.startswith(prefix))
        items = []
        for key in sorted(keys):
            value = existing.get(key, PLUGIN_CONFIG_DEFAULTS.get(key, ''))
            label, help_text, kind, group = meta.get(key, (key, '', 'str', '其他'))
            secret = bool(SECRET.search(key)) or kind == 'secret'
            items.append({'key': key, 'value': '' if secret else value, 'secret': secret,
                          'configured': bool(value.strip('"\' ')), 'label': label,
                          'help': help_text, 'type': kind, 'group': group})
        return items

    @app.get('/api/plugins/{name}/config', dependencies=[Depends(authenticated)])
    async def plugin_config(name: str):
        if name not in {p.name for p in nonebot.get_loaded_plugins()}:
            raise HTTPException(404, '插件不存在')
        return {'plugin': name, 'prefix': plugin_prefix(name), 'items': plugin_config_items(name)}

    @app.put('/api/plugins/{name}/config', dependencies=[Depends(authenticated)])
    async def plugin_config_save(name: str, body: ConfigChange, request: Request):
        if name not in {p.name for p in nonebot.get_loaded_plugins()}:
            raise HTTPException(404, '插件不存在')
        meta = PLUGIN_CONFIG_META.get(name, {})
        prefix = plugin_prefix(name)
        existing = read_config()
        allowed = set(meta)
        allowed.update(k for k in existing if k.startswith(prefix))
        values = {}
        for key, raw in body.values.items():
            key = key.strip().upper()
            if key not in allowed:
                raise HTTPException(400, f'不允许修改的配置键：{key}')
            kind = meta.get(key, (key, '', 'str', '其他'))[2]
            if kind == 'secret' and not str(raw).strip():
                continue
            values[key] = coerce_config_value(kind, str(raw))
        if not values:
            raise HTTPException(400, '没有需要保存的配置项')
        count, runtime = apply_config(values)
        restart_required = bool(runtime['restart_keys'] or runtime['errors'])
        logger.info(
            f'管理页面：保存插件 {name} 的 {count} 项配置；'
            f'热加载 {len(runtime["hot_keys"])} 项，需重启 {len(runtime["restart_keys"])} 项'
        )
        audit(request, '保存插件配置', f'{name} · {count} 项')
        return {'ok': True, 'restart_required': restart_required,
                'hot_reloaded': bool(runtime['hot_keys']),
                'hot_keys': runtime['hot_keys'],
                'restart_keys': runtime['restart_keys'],
                'errors': runtime['errors']}

    # ---------- 聊天插件数据管理 ----------

    def chat_json(relative: str):
        path = (CHAT_DIR / relative).resolve()
        if CHAT_DIR.resolve() not in path.parents or path.suffix != '.json':
            raise HTTPException(400, '非法的数据路径')
        if not path.exists():
            return path, {}
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            raise HTTPException(422, '数据文件格式损坏，无法解析')
        return path, data

    def memory_path(kind: str, key: str):
        if kind not in {'group', 'user'} or not key.isdigit():
            raise HTTPException(400, '记忆标识无效')
        return CHAT_DIR / 'memory' / kind / f'{key}.json'

    def usage_totals() -> dict:
        """跨群 / 私聊 / legacy 汇总每个用户的 token 用量。"""
        _, usage = chat_json('usage.json')
        totals: dict[str, dict] = {}

        def add(qq, value):
            if not isinstance(value, dict):
                return
            row = totals.setdefault(qq, {'calls': 0, 'prompt_tokens': 0,
                                         'completion_tokens': 0, 'total_tokens': 0})
            for field in row:
                try:
                    row[field] += int(value.get(field, 0) or 0)
                except (TypeError, ValueError):
                    pass

        def walk(node):
            for key, value in node.items() if isinstance(node, dict) else []:
                if isinstance(value, dict) and any(k in value for k in ('calls', 'total_tokens', 'prompt_tokens')):
                    add(key, value)
                elif isinstance(value, dict):
                    walk(value)

        walk(usage)
        return totals

    def scan_memory_for(qq: str):
        """扫描记忆文件，返回 (该用户原始发言列表, 被提及的固化条目数)。"""
        pending_msgs = []
        mention_count = 0
        qq_pat = re.compile(rf'(?<!\d){qq}(?!\d)')
        for kind in ('group', 'user'):
            directory = CHAT_DIR / 'memory' / kind
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob('*.json')):
                try:
                    data = json.loads(path.read_text(encoding='utf-8'))
                except Exception:
                    continue
                for turn in data.get('pending', []) or []:
                    if isinstance(turn, dict) and str(turn.get('qq')) == qq and turn.get('role') == 'user':
                        content = str(turn.get('content') or '').strip()
                        if content:
                            pending_msgs.append({'time': turn.get('time', 0), 'content': content})
                for episode in data.get('episodes', []) or []:
                    if isinstance(episode, dict) and qq_pat.search(str(episode.get('text') or '')):
                        mention_count += 1
                for fact in data.get('facts', []) or []:
                    if isinstance(fact, str) and qq_pat.search(fact):
                        mention_count += 1
        pending_msgs.sort(key=lambda r: r['time'])
        return pending_msgs, mention_count

    def read_human_state() -> dict:
        path = CHAT_DIR / 'human_state.json'
        default_mood = {'valence': 0.15, 'energy': 0.65, 'irritation': 0.0,
                        'loneliness': 0.25, 'updated': int(time.time())}
        if not path.exists():
            return {'mood': default_mood, 'label': '平静', 'energy_label': '正常',
                    'stale': True, 'recent_replies': {}, 'night': False, 'activity': ''}
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return {'mood': default_mood, 'label': '平静', 'energy_label': '正常',
                    'stale': True, 'recent_replies': {}, 'night': False, 'activity': ''}
        mood = data.get('mood') if isinstance(data.get('mood'), dict) else default_mood
        try:
            fallback_updated = int(data.get('updated') or time.time())
        except (TypeError, ValueError):
            fallback_updated = int(time.time())
        return {
            'mood': {**default_mood, 'updated': fallback_updated, **mood},
            'label': data.get('label') or '平静',
            'energy_label': data.get('energy_label') or '正常',
            'stale': False,
            'night': bool(data.get('night')),
            'activity': str(data.get('activity') or ''),
            'activity_updated': int(data.get('activity_updated') or 0),
            'recent_replies': data.get('recent_replies') if isinstance(
                data.get('recent_replies'), dict) else {},
        }

    def relationship_of(qq: str, favorability, interactions) -> dict:
        try:
            favor = float(favorability or 0)
        except (TypeError, ValueError):
            favor = 0.0
        try:
            interactions = int(interactions or 0)
        except (TypeError, ValueError):
            interactions = 0
        brother_qq = ''
        character = CHAT_DIR / 'character.txt'
        if character.exists():
            match = re.search(r'QQ号(\d{5,})', character.read_text(encoding='utf-8'))
            if match:
                brother_qq = match.group(1)
        if brother_qq and str(qq) == brother_qq:
            return {'stage': 'brother', 'label': '哥哥', 'score': 100}
        if favor < -100:
            stage, label = 'hostile', '仇视'
        elif favor < -60:
            stage, label = 'disgust', '厌恶'
        elif favor < -30:
            stage, label = 'aversion', '反感'
        elif favor < -10:
            stage, label = 'cold', '冷淡'
        elif favor < 0:
            stage, label = 'wary', '生疏·戒备'
        elif favor < 10:
            stage, label = 'stranger', '生疏'
        elif favor < 20:
            stage, label = 'familiar', '眼熟·点头'
        elif favor < 30:
            stage, label = 'familiar_plus', '眼熟·常聊'
        elif favor < 45:
            stage, label = 'friend', '熟人·轻松'
        elif favor < 60:
            stage, label = 'friend_plus', '熟人·默契'
        elif favor < 75:
            stage, label = 'close', '亲近·在意'
        elif favor < 85:
            stage, label = 'close_plus', '亲近·偏爱'
        else:
            stage, label = 'attached', '依赖·羁绊'
        score = min(100, int(favor + min(20, interactions / 80)))
        return {'stage': stage, 'label': label, 'score': score}

    @app.get('/api/chat/overview', dependencies=[Depends(authenticated)])
    async def chat_overview():
        _, users = chat_json('users.json')
        _, growth = chat_json('growth_persona.json')
        _, learned = chat_json('emote_learned.json')
        totals = usage_totals()
        memories = 0
        for kind in ('group', 'user'):
            directory = CHAT_DIR / 'memory' / kind
            if directory.exists():
                memories += len(list(directory.glob('*.json')))
        emotes = 0
        for folder in ('emotes', 'misc_emoji'):
            directory = CHAT_DIR / folder
            if directory.exists():
                emotes += sum(
                    1 for p in directory.rglob('*')
                    if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
                )
        total_tokens = sum(int(v.get('total_tokens', 0)) for v in totals.values())
        calls = sum(int(v.get('calls', 0)) for v in totals.values())
        saved = sum(1 for v in learned.values()
                    if isinstance(v, dict) and v.get('status') == 'saved')
        rejected = sum(1 for v in learned.values()
                       if isinstance(v, dict) and v.get('status') == 'rejected')
        human = live_human_state()
        return {'users': len(users), 'memories': memories, 'growth_buckets': len(growth),
                'learned': len(learned), 'saved': saved, 'rejected': rejected,
                'emotes': emotes, 'tokens': total_tokens,
                'calls': calls, 'human': human, 'media': media.stats()}

    @app.get('/api/chat/human', dependencies=[Depends(authenticated)])
    async def chat_human():
        state = live_human_state()
        _, users = chat_json('users.json')
        brother_qq = ''
        character = CHAT_DIR / 'character.txt'
        if character.exists():
            match = re.search(r'QQ号(\d{5,})', character.read_text(encoding='utf-8'))
            if match:
                brother_qq = match.group(1)
        state['brother_qq'] = brother_qq
        stages: dict[str, int] = {}
        for qq, info in (users.items() if isinstance(users, dict) else []):
            if not isinstance(info, dict):
                continue
            rel = relationship_of(qq, info.get('favorability', 0), info.get('interactions', 0))
            stages[rel['label']] = stages.get(rel['label'], 0) + 1
        order = ['哥哥', '依赖·羁绊', '亲近·偏爱', '亲近·在意', '熟人·默契', '熟人·轻松', '眼熟·常聊', '眼熟·点头', '生疏', '生疏·戒备', '冷淡', '反感', '厌恶', '仇视']
        state['relationships'] = [
            {'label': label, 'count': stages.get(label, 0)}
            for label in order if stages.get(label, 0)
        ]
        state['config'] = {
            'humanize': bool(getattr(driver.config, 'chat_humanize_enabled', True)),
            'typing': bool(getattr(driver.config, 'chat_typing_simulation', True)),
            'mood': bool(getattr(driver.config, 'chat_mood_enabled', True)),
            'activity': bool(getattr(driver.config, 'chat_activity_enabled', True)),
            'life': bool(getattr(driver.config, 'chat_life_enabled', True)),
            'mood_cues': bool(getattr(driver.config, 'chat_mood_cues_enabled', True)),
            'night_quiet': bool(getattr(driver.config, 'chat_night_quiet_enabled', True)),
            'anti_repeat': bool(getattr(driver.config, 'chat_anti_repeat_enabled', True)),
        }
        return state

    @app.get('/api/chat/world', dependencies=[Depends(authenticated)])
    async def chat_world():
        module = chat_module()
        fn = getattr(module, 'world_snapshot', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供世界地图')
        return fn()

    @app.post('/api/chat/world/expand', dependencies=[Depends(authenticated)])
    async def chat_world_expand(body: WorldExpandBody, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以拓展地图')
        module = chat_module()
        fn = getattr(module, 'world_expand', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供世界地图')
        result = fn(body.parent, body.name, body.kind)
        if not result.get('ok'):
            raise HTTPException(409, result.get('error') or '拓展失败')
        audit(request, '拓展地图', f'{body.parent} → {body.name}')
        return result

    @app.post('/api/chat/world/detail', dependencies=[Depends(authenticated)])
    async def chat_world_detail(body: WorldDetailBody, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以修改场景细节')
        module = chat_module()
        fn = getattr(module, 'world_detail', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供世界地图')
        result = fn(body.node, body.key, body.value)
        if not result.get('ok'):
            raise HTTPException(409, result.get('error') or '修改失败')
        audit(request, '修改场景细节', f'{body.node}/{body.key}')
        return result

    @app.get('/api/chat/proactive', dependencies=[Depends(authenticated), Depends(agent_owner)])
    async def chat_proactive():
        module = chat_module()
        fn = getattr(module, 'proactive_snapshot', None) if module else None
        if not callable(fn):
            return {'rows': [], 'available': False}
        return {**fn(), 'available': True}

    @app.get('/api/chat/life', dependencies=[Depends(authenticated)])
    async def chat_life():
        module = chat_module()
        fn = getattr(module, 'life_snapshot', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供生活状态')
        return fn()

    @app.post('/api/chat/life/action', dependencies=[Depends(authenticated)])
    async def chat_life_action(body: LifeActionBody, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以调整生活状态')
        module = chat_module()
        fn = getattr(module, 'life_action', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供生活动作')
        result = fn(body.action, body.location, body.reason)
        if not result.get('ok'):
            raise HTTPException(409, result.get('error') or '动作未执行')
        audit(request, '调整生活状态', f'{body.action} {body.location}'.strip())
        return result

    @app.post('/api/chat/life/reset', dependencies=[Depends(authenticated)])
    async def chat_life_reset(request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以重置生活状态')
        module = chat_module()
        fn = getattr(module, 'life_reset', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供生活状态')
        result = fn()
        audit(request, '重置生活状态')
        return result

    @app.post('/api/chat/human/reset', dependencies=[Depends(authenticated)])
    async def chat_human_reset(request: Request):
        module = chat_module()
        fn = getattr(module, 'reset_human_state', None) if module else None
        if callable(fn):
            fn()
        else:
            path = CHAT_DIR / 'human_state.json'
            data = read_human_state()
            now = int(time.time())
            state = {
                'mood': {'valence': 0.15, 'energy': 0.65, 'irritation': 0.0,
                         'loneliness': 0.25, 'updated': now},
                'label': '平静', 'energy_label': '正常', 'night': False,
                'activity': '', 'activity_updated': now,
                'recent_replies': data.get('recent_replies', {}),
                'updated': now,
            }
            atomic_write(path, json.dumps(state, ensure_ascii=False, indent=2))
        audit(request, '重置拟人心情')
        return {'ok': True}

    @app.get('/api/agent/self-memory', dependencies=[Depends(authenticated)])
    async def agent_self_memory():
        """统一自我记忆：群聊与电脑/手机共享的自我经历与自我认知。"""
        from src.agent.self_memory import self_memory
        return self_memory.snapshot()

    @app.post('/api/agent/self-memory/fact', dependencies=[Depends(authenticated)])
    async def agent_self_memory_add_fact(body: SelfFactBody, request: Request):
        from src.agent.self_memory import self_memory
        row = self_memory.add_fact(body.text, 'manual')
        if row is None:
            raise HTTPException(409, '该事实已存在或内容无效（不超过200字）')
        audit(request, '新增自我记忆事实', body.text[:60])
        return {'ok': True, **row}

    @app.delete('/api/agent/self-memory/fact', dependencies=[Depends(authenticated)])
    async def agent_self_memory_remove_fact(text: str, request: Request):
        from src.agent.self_memory import self_memory
        if not self_memory.remove_fact(text):
            raise HTTPException(404, '该事实不存在')
        audit(request, '删除自我记忆事实', text[:60])
        return {'ok': True}

    @app.get('/api/chat/relations', dependencies=[Depends(authenticated)])
    async def chat_relations(query: str = '', qq: str = ''):
        module = chat_module()
        fn = getattr(module, 'relations_snapshot', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供关系图谱')
        return fn(query=query, qq=qq)

    @app.put('/api/chat/relations', dependencies=[Depends(authenticated)])
    async def chat_relations_update(body: RelationBody, request: Request):
        if not body.a.isdigit() or not body.b.isdigit():
            raise HTTPException(400, '两端 QQ 号无效')
        module = chat_module()
        fn = getattr(module, 'relations_set', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供关系图谱')
        result = fn(body.a, body.b, body.kind, body.label)
        if not result.get('ok'):
            raise HTTPException(409, result.get('error') or '保存失败')
        audit(request, '保存人物关系', f'{body.a}-{body.b} {body.kind}')
        return result

    @app.post('/api/chat/panel-chat', dependencies=[Depends(authenticated)])
    async def chat_panel_chat(body: PanelChatBody, request: Request):
        module = chat_module()
        fn = getattr(module, 'panel_chat_send', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供面板聊天')
        result = await fn(body.session, body.text)
        if not result.get('ok'):
            raise HTTPException(409, result.get('error') or '生成失败')
        return result

    @app.get('/api/chat/panel-chat', dependencies=[Depends(authenticated)])
    async def chat_panel_chat_history(session: str = 'owner'):
        module = chat_module()
        fn = getattr(module, 'panel_chat_history', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供面板聊天')
        return fn(session)

    @app.delete('/api/chat/panel-chat', dependencies=[Depends(authenticated)])
    async def chat_panel_chat_clear(session: str = 'owner', request: Request = None):
        module = chat_module()
        fn = getattr(module, 'panel_chat_clear', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供面板聊天')
        return fn(session)

    @app.post('/api/chat/relay', dependencies=[Depends(authenticated)])
    async def chat_relay(body: RelayBody, request: Request):
        """面板捎话：target=group 由群聊定时转达；target=computer 带进下次电脑任务。"""
        module = chat_module()
        sender = actor(request)[0]
        if body.target == 'group':
            from src.agent.mailbox import mailbox
            item = mailbox.send(source='panel', target='group', text=body.text, sender=sender)
            audit(request, '面板捎话到群聊', body.text[:50])
            return {'ok': True, 'item': item}
        fn = getattr(module, '_relay_maybe_enqueue', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供捎话')
        # 直接给电脑：构造命中格式
        item_text = f'帮我跟服务器捎句话：{body.text}'
        result = fn('panel', item_text, sender)
        if not result:
            raise HTTPException(409, '捎话内容无法解析')
        audit(request, '面板捎话到服务器', body.text[:50])
        return result

    @app.get('/api/chat/relay', dependencies=[Depends(authenticated)])
    async def chat_relay_list():
        from src.agent.mailbox import mailbox
        return {'items': mailbox.snapshot(30), 'pending': mailbox.pending_count()}

    @app.get('/api/chat/memory-graph', dependencies=[Depends(authenticated)])
    async def chat_memory_graph():
        module = chat_module()
        fn = getattr(module, 'memory_graph_snapshot', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供记忆图谱')
        return await run_in_threadpool(fn)

    @app.post('/api/chat/relations/infer', dependencies=[Depends(authenticated)])
    async def chat_relations_infer(request: Request):
        module = chat_module()
        fn = getattr(module, 'relations_infer', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供关系图谱')
        result = await run_in_threadpool(fn)
        audit(request, '重新推断关系图谱', f"+{result.get('added', 0)} 条")
        return result

    @app.delete('/api/chat/relations', dependencies=[Depends(authenticated)])
    async def chat_relations_delete(a: str, b: str, request: Request):
        module = chat_module()
        fn = getattr(module, 'relations_remove', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供关系图谱')
        result = fn(a, b)
        if not result.get('ok'):
            raise HTTPException(404, '关系不存在')
        audit(request, '删除人物关系', f'{a}-{b}')
        return result

    @app.get('/api/chat/metrics', dependencies=[Depends(authenticated)])
    async def chat_metrics(days: int = 14):
        module = chat_module()
        fn = getattr(module, 'chat_metrics_snapshot', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供运行指标')
        return fn(max(1, min(30, days)))

    @app.get('/api/chat/eval', dependencies=[Depends(authenticated)])
    async def chat_eval(query: str = '', labeled: str = 'all', limit: int = 200):
        module = chat_module()
        fn = getattr(module, 'eval_snapshot', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供评测集')
        return fn(query=query, labeled=labeled if labeled in ('all', 'yes', 'no', 'flagged') else 'all', limit=limit)

    @app.post('/api/chat/eval/{sample_id}/label', dependencies=[Depends(authenticated)])
    async def chat_eval_label(sample_id: str, body: EvalLabelBody, request: Request):
        module = chat_module()
        fn = getattr(module, 'eval_label', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供评测集')
        result = fn(sample_id, body.labels, body.note)
        if not result.get('ok'):
            raise HTTPException(404, '样本不存在')
        flagged = [name for name, value in (body.labels or {}).items() if value]
        audit(request, '标注评测样本', f'{sample_id} · {", ".join(flagged) if flagged else "正常"}')
        return result

    @app.delete('/api/chat/eval/{sample_id}', dependencies=[Depends(authenticated)])
    async def chat_eval_delete(sample_id: str, request: Request):
        module = chat_module()
        fn = getattr(module, 'eval_delete', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供评测集')
        result = fn(sample_id)
        if not result.get('ok'):
            raise HTTPException(404, '样本不存在')
        audit(request, '删除评测样本', sample_id)
        return result

    @app.post('/api/chat/eval/replay', dependencies=[Depends(authenticated)])
    async def chat_eval_replay(body: EvalReplayBody, request: Request):
        module = chat_module()
        fn = getattr(module, 'eval_replay', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供评测集')
        try:
            result = await asyncio.wait_for(
                fn(body.sample_ids, body.limit), timeout=280
            )
        except asyncio.TimeoutError:
            raise HTTPException(504, '回归超时：样本过多或模型响应慢，请减小数量后重试')
        audit(request, '评测回归重放', f'{result.get("ran", 0)} 条样本')
        return result

    @app.get('/api/risk/reviews', dependencies=[Depends(authenticated)])
    async def risk_reviews(status: str = 'pending', query: str = '', limit: int = 100):
        module = risk_module()
        fn = getattr(module, 'review_snapshot', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '风控插件未提供人工复核队列')
        return fn(status if status in ('pending', 'labeled', 'all') else 'pending', query, max(1, min(300, limit)))

    @app.post('/api/risk/reviews/{review_id}/label', dependencies=[Depends(authenticated)])
    async def risk_review_label(review_id: int, body: RiskReviewLabelBody, request: Request):
        module = risk_module()
        fn = getattr(module, 'review_label', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '风控插件未提供人工复核队列')
        result = fn(review_id, body.label, body.categories, body.note)
        if not result.get('ok'):
            raise HTTPException(404, '复核样本不存在或标签无效')
        audit(request, '风控人工复核', f'{review_id} · {body.label}')
        return result

    @app.delete('/api/risk/reviews/{review_id}', dependencies=[Depends(authenticated)])
    async def risk_review_delete(review_id: int, request: Request):
        module = risk_module()
        fn = getattr(module, 'review_delete', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '风控插件未提供人工复核队列')
        result = fn(review_id)
        if not result.get('ok'):
            raise HTTPException(404, '复核样本不存在')
        audit(request, '删除风控复核样本', str(review_id))
        return result

    @app.post('/api/risk/reviews/replay', dependencies=[Depends(authenticated)])
    async def risk_review_replay(body: RiskReviewReplayBody, request: Request):
        module = risk_module()
        fn = getattr(module, 'review_replay', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '风控插件未提供人工复核队列')
        try:
            result = await asyncio.wait_for(fn(body.sample_ids, body.limit), timeout=280)
        except asyncio.TimeoutError:
            raise HTTPException(504, '风控复核重放超时，请减少条数')
        audit(request, '风控复核重放', f'{result.get("ran", 0)} 条')
        return result

    @app.get('/api/chat/favor/ledger', dependencies=[Depends(authenticated)])
    async def chat_favor_ledger(qq: str = '', limit: int = 100):
        module = chat_module()
        fn = getattr(module, 'favor_ledger_snapshot', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供好感账本')
        items = fn(qq, max(1, min(300, limit)))
        _, users = chat_json('users.json')
        for entry in items:
            info = users.get(str(entry.get('qq')))
            entry['nickname'] = (info or {}).get('nickname', '') if isinstance(info, dict) else ''
            # 触发原文与上下文体积大，只在详情端点返回；has_text 供前端区分新旧记录
            entry['has_text'] = bool(str(entry.get('text') or '').strip())
            # 给前端做“上下文范围”合并用的脱敏键：只返回消息哈希，不暴露正文。
            context_keys = []
            for turn in (entry.get('context') or []):
                if not isinstance(turn, dict):
                    continue
                content = str(turn.get('content') or '').strip()
                if not content:
                    continue
                raw_key = f"{turn.get('role') or ''}|{turn.get('qq') or ''}|{content[:300]}"
                context_keys.append(hashlib.sha1(raw_key.encode('utf-8')).hexdigest()[:16])
            # 旧记录没有对话 context 时，用原文存档前后几行的哈希作为弱上下文键；
            # 只有共享邻接行才会被前端合并，不会跨大量对话硬并。
            archived = entry.get('archive_context') or {}
            if isinstance(archived, dict):
                for line in (archived.get('before') or []) + (archived.get('after') or []):
                    text_line = str(line or '').strip()
                    if not text_line:
                        continue
                    context_keys.append(hashlib.sha1(('archive|' + text_line[:300]).encode('utf-8')).hexdigest()[:16])
            entry['context_keys'] = list(dict.fromkeys(context_keys))[-20:]
            entry.pop('text', None)
            entry.pop('context', None)
            entry.pop('archive_context', None)
            entry.pop('triggers', None)
        stats_fn = getattr(module, 'favor_ledger_stats', None)
        stats = stats_fn() if callable(stats_fn) else None
        return {'items': items, 'stats': stats or {}}

    @app.get('/api/chat/slang', dependencies=[Depends(authenticated)])
    async def chat_slang(status: str = '', limit: int = 200):
        module = chat_module()
        fn = getattr(module, 'slang_snapshot', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供玩梗记忆')
        return fn(status, max(1, min(500, limit)))

    @app.post('/api/chat/slang/test', dependencies=[Depends(authenticated)])
    async def chat_slang_test(body: dict, request: Request):
        module = chat_module()
        fn = getattr(module, 'slang_test', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供玩梗语义测试')
        return await fn(str(body.get('text') or ''), str(body.get('group') or ''))

    @app.get('/api/chat/meme', dependencies=[Depends(authenticated)])
    async def chat_meme(status: str = '', limit: int = 100):
        module = chat_module()
        fn = getattr(module, 'meme_snapshot', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供玩梗记忆')
        return fn(status, max(1, min(500, limit)))

    @app.get('/api/vision/chain', dependencies=[Depends(authenticated)])
    async def vision_chain_snapshot():
        module = chat_module()
        fn = getattr(module, 'vision_chain_snapshot', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供视觉链路管理')
        return fn()

    @app.post('/api/vision/chain/nodes', dependencies=[Depends(authenticated)])
    async def vision_chain_create(body: dict, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以修改视觉链路')
        module = chat_module()
        fn = getattr(module, 'vision_chain_create', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供视觉链路管理')
        try:
            node = await run_in_threadpool(fn, body if isinstance(body, dict) else {})
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        audit(request, '新增视觉链路节点', str(node.get('name') or node.get('id') or ''))
        return {'ok': True, 'item': node}

    @app.put('/api/vision/chain/nodes/{node_id}', dependencies=[Depends(authenticated)])
    async def vision_chain_update(node_id: str, body: dict, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以修改视觉链路')
        module = chat_module()
        fn = getattr(module, 'vision_chain_update', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供视觉链路管理')
        try:
            node = await run_in_threadpool(fn, node_id, body if isinstance(body, dict) else {})
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        if node is None:
            raise HTTPException(404, '视觉链路节点不存在')
        audit(request, '编辑视觉链路节点', str(node.get('name') or node_id))
        return {'ok': True, 'item': node}

    @app.delete('/api/vision/chain/nodes/{node_id}', dependencies=[Depends(authenticated)])
    async def vision_chain_delete(node_id: str, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以修改视觉链路')
        module = chat_module()
        fn = getattr(module, 'vision_chain_delete', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供视觉链路管理')
        if not await run_in_threadpool(fn, node_id):
            raise HTTPException(404, '视觉链路节点不存在')
        audit(request, '删除视觉链路节点', node_id)
        return {'ok': True}

    @app.put('/api/vision/chain/settings', dependencies=[Depends(authenticated)])
    async def vision_chain_settings(body: dict, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以修改模型链路')
        if not isinstance(body, dict) or 'auto_select' not in body:
            raise HTTPException(400, '缺少 auto_select')
        module = chat_module()
        fn = getattr(module, 'vision_chain_set_auto', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供模型链路管理')
        result = await run_in_threadpool(fn, bool(body.get('auto_select')))
        audit(request, '切换模型自动择优', '开启' if result.get('auto_select') else '关闭')
        return result

    @app.put('/api/vision/chain/order', dependencies=[Depends(authenticated)])
    async def vision_chain_order(body: dict, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以修改视觉链路')
        ids = body.get('ids') if isinstance(body, dict) else None
        if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
            raise HTTPException(400, '节点顺序不合法')
        module = chat_module()
        fn = getattr(module, 'vision_chain_reorder', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供视觉链路管理')
        items = await run_in_threadpool(fn, ids)
        audit(request, '调整视觉链路顺序', ','.join(ids)[:200])
        return {'ok': True, 'items': items}

    @app.post('/api/vision/cache/clear', dependencies=[Depends(authenticated)])
    async def vision_cache_clear(request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以清理识图缓存')
        module = chat_module()
        fn = getattr(module, 'vision_cache_clear', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供识图缓存管理')
        result = await run_in_threadpool(fn)
        audit(request, '清理识图缓存', '')
        return result

    @app.post('/api/vision/chain/import', dependencies=[Depends(authenticated)])
    async def vision_chain_import(request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以修改视觉链路')
        module = chat_module()
        fn = getattr(module, 'vision_chain_import_legacy', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供视觉链路管理')
        try:
            result = await run_in_threadpool(fn)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        audit(request, '导入当前视觉配置', str(result.get('message') or ''))
        return result

    @app.post('/api/vision/chain/nodes/{node_id}/probe', dependencies=[Depends(authenticated)])
    async def vision_chain_probe(node_id: str, body: dict, request: Request):
        module = chat_module()
        fn = getattr(module, 'vision_chain_probe', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供视觉链路管理')
        result = await fn(node_id, str((body or {}).get('text') or ''), str((body or {}).get('image_url') or ''))
        audit(request, '测试视觉链路节点', node_id)
        return result

    @app.post('/api/chat/meme/test', dependencies=[Depends(authenticated)])
    async def chat_meme_test(body: dict, request: Request):
        module = chat_module()
        fn = getattr(module, 'meme_test', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供玩梗分析')
        result = await fn(str(body.get('text') or ''), str(body.get('group') or ''))
        audit(request, '测试玩梗语义分析', str(body.get('text') or '')[:80])
        return result

    @app.post('/api/chat/meme/review/{observation_id}', dependencies=[Depends(authenticated)])
    async def chat_meme_review(observation_id: str, body: dict, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以复核玩梗判断')
        module = chat_module()
        fn = getattr(module, 'meme_review', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供玩梗复核')
        allowed = ('mode', 'variant_type', 'speech_act', 'reply_strategy', 'recognized_term',
                   'meaning', 'reply_goal', 'confidence', 'corrected_term',
                   'corrected_meaning', 'corrected_usage')
        fields = {key: body[key] for key in allowed if key in body}
        result = await run_in_threadpool(fn, observation_id,
                                         str(body.get('outcome') or ''),
                                         str(body.get('note') or ''), **fields)
        if result is None:
            raise HTTPException(404, '观察记录不存在')
        audit(request, '复核玩梗判断', f"{observation_id} · {body.get('outcome') or '未改标签'}")
        return result

    @app.delete('/api/chat/meme/{observation_id}', dependencies=[Depends(authenticated)])
    async def chat_meme_delete(observation_id: str, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以删除玩梗判断')
        module = chat_module()
        fn = getattr(module, 'meme_delete', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供玩梗记录删除')
        if not await run_in_threadpool(fn, observation_id):
            raise HTTPException(404, '观察记录不存在')
        audit(request, '删除玩梗判断', observation_id)
        return {'ok': True}

    @app.post('/api/chat/meme/evaluate', dependencies=[Depends(authenticated)])
    async def chat_meme_evaluate(body: dict, request: Request):
        module = chat_module()
        fn = getattr(module, 'meme_evaluate', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供玩梗效果评估')
        result = await fn(str(body.get('group') or ''), str(body.get('qq') or ''), str(body.get('text') or ''))
        audit(request, '评估玩梗接梗效果', str(body.get('text') or '')[:80])
        return result

    @app.put('/api/chat/slang/settings', dependencies=[Depends(authenticated)])
    async def chat_slang_settings(body: dict, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以修改玩梗开关')
        module = chat_module()
        fn = getattr(module, 'slang_set_enabled', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供玩梗开关')
        enabled = bool(body.get('enabled', True))
        result = await run_in_threadpool(fn, enabled)
        audit(request, '玩梗接梗总开关', '开启' if enabled else '关闭')
        return result

    @app.put('/api/chat/slang/{term}', dependencies=[Depends(authenticated)])
    async def chat_slang_update(term: str, body: SlangUpdateBody, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以修改玩梗语义记忆')
        module = chat_module()
        fn = getattr(module, 'slang_update', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供玩梗记忆')
        changes = {k: v for k, v in body.model_dump().items() if v}
        result = await run_in_threadpool(fn, term, **changes)
        if result is None:
            raise HTTPException(404, '词条不存在')
        audit(request, '更新玩梗语义记忆', term)
        return result

    @app.post('/api/chat/slang/prune', dependencies=[Depends(authenticated)])
    async def chat_slang_prune(body: dict, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以清理用语记忆')
        module = chat_module()
        fn = getattr(module, 'slang_prune', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供玩梗记忆')
        result = await run_in_threadpool(
            fn, max(1, min(365, int(body.get('days') or 14))),
            bool(body.get('include_active')), bool(body.get('force'))
        )
        audit(request, '清理冷门用语', f"-{result.get('count', 0)} 条")
        return result

    @app.post('/api/chat/slang/clear-all', dependencies=[Depends(authenticated)])
    async def chat_slang_clear_all(request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以清空用语记忆')
        module = chat_module()
        fn = getattr(module, 'slang_clear_all', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供玩梗记忆')
        result = await run_in_threadpool(fn)
        audit(request, '清空长期语义记忆', f"-{result.get('cleared', 0)} 条")
        return result

    @app.delete('/api/chat/slang/{term}', dependencies=[Depends(authenticated)])
    async def chat_slang_delete(term: str, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以删除用语记忆')
        module = chat_module()
        fn = getattr(module, 'slang_remove', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供玩梗记忆')
        if not await run_in_threadpool(fn, term):
            raise HTTPException(404, '词条不存在')
        audit(request, '删除网络用语', term)
        return {'ok': True}

    @app.post('/api/chat/slang/learn', dependencies=[Depends(authenticated)])
    async def chat_slang_learn(body: dict, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以触发用语学习')
        module = chat_module()
        fn = getattr(module, 'slang_learn_now', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供用语学习')
        result = await fn(str(body.get('scope') or 'group'))
        audit(request, '学习群聊用语', str(body.get('scope') or 'group'))
        return result

    @app.post('/api/chat/favor/ledger/backfill', dependencies=[Depends(authenticated)])
    async def chat_favor_ledger_backfill(request: Request):
        module = chat_module()
        fn = getattr(module, 'favor_ledger_backfill', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供好感账本')
        result = await run_in_threadpool(fn)
        audit(request, '回填扣分原文', f"+{result.get('filled', 0)} 条")
        return result

    @app.get('/api/chat/favor/ledger/{entry_id}', dependencies=[Depends(authenticated)])
    async def chat_favor_ledger_detail(entry_id: str):
        module = chat_module()
        fn = getattr(module, 'favor_ledger_detail', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供好感账本')
        detail = fn(entry_id)
        if detail is None:
            raise HTTPException(404, '扣分记录不存在')
        entry = detail.get('entry') or {}
        _, users = chat_json('users.json')
        info = users.get(str(entry.get('qq')))
        detail['nickname'] = (info or {}).get('nickname', '') if isinstance(info, dict) else ''
        return detail

    @app.put('/api/chat/favor/ledger/{entry_id}/anomaly', dependencies=[Depends(authenticated)])
    async def chat_favor_ledger_anomaly(entry_id: str, body: AnomalyUpdateBody, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以修改异常类型')
        module = chat_module()
        fn = getattr(module, 'favor_ledger_set_anomaly', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供好感账本')
        result = fn(entry_id, body.kind, body.corrected_reason)
        if not result.get('ok'):
            raise HTTPException(409, '只能修改已认定异常的记录')
        audit(request, '修改异常类型', f'{entry_id} → {body.kind}/{body.corrected_reason}')
        return result

    @app.post('/api/chat/favor/ledger/{entry_id}/appeal', dependencies=[Depends(authenticated)])
    async def chat_favor_ledger_appeal(entry_id: str, body: FavorAppealBody, request: Request):
        module = chat_module()
        fn = getattr(module, 'favor_ledger_appeal', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供好感账本')
        result = fn(entry_id, body.note)
        if not result.get('ok'):
            raise HTTPException(404, '扣分记录不存在')
        audit(request, '好感扣分申诉', entry_id)
        return result

    @app.post('/api/chat/favor/ledger/{entry_id}/resolve', dependencies=[Depends(authenticated)])
    async def chat_favor_ledger_resolve(entry_id: str, body: FavorResolveBody, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以处理申诉')
        module = chat_module()
        fn = getattr(module, 'favor_ledger_resolve', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供好感账本')
        result = fn(entry_id, body.accepted, body.kind, body.corrected_reason)
        if not result.get('ok'):
            raise HTTPException(409, result.get('error') or '处理失败')
        audit(request, '好感扣分申诉处理', f'{entry_id} → {"恢复" if body.accepted else "维持"}')
        return result

    @app.get('/api/chat/favor', dependencies=[Depends(authenticated)])
    async def chat_favor():
        _, users = chat_json('users.json')
        _, profile_data = chat_json('profiles.json')
        profiles = profile_data.get('profiles') if isinstance(profile_data, dict) else {}
        _, bots = chat_json('bots.json')
        rows = []
        for qq, info in users.items():
            if not isinstance(info, dict):
                continue
            profile = profiles.get(qq) if isinstance(profiles, dict) else None
            profile = profile if isinstance(profile, dict) else {}
            manual = profile.get('manual') if isinstance(profile.get('manual'), dict) else {}
            rows.append({'qq': qq, 'nickname': info.get('nickname', ''),
                         'favorability': info.get('favorability', 0),
                         'interactions': info.get('interactions', 0),
                         'last_active': info.get('last_active', 0),
                         'tags': manual.get('tags') or [],
                         'notes': manual.get('notes') or '',
                         'birthday': manual.get('birthday') or profile.get('birthday') or '',
                         'is_bot': bool((bots.get(qq) or {}).get('is_bot')) if isinstance(bots.get(qq), dict) else False})
        rows.sort(key=lambda r: (-float(r['favorability'] or 0), -int(r['interactions'] or 0)))
        return {'items': rows}

    @app.put('/api/chat/favor/{qq}', dependencies=[Depends(authenticated)])
    async def chat_favor_update(qq: str, body: FavorUpdate, request: Request):
        if not qq.isdigit():
            raise HTTPException(400, 'QQ 号无效')
        path, users = chat_json('users.json')
        if qq not in users or not isinstance(users[qq], dict):
            raise HTTPException(404, '用户不存在')
        users[qq]['favorability'] = round(body.favorability, 1)
        atomic_write(path, json.dumps(users, ensure_ascii=False, indent=2))
        audit(request, '修改好感度', f'{qq} → {users[qq]["favorability"]}')
        return {'ok': True}

    @app.get('/api/chat/profile/{qq}', dependencies=[Depends(authenticated)])
    async def chat_profile(qq: str):
        if not qq.isdigit():
            raise HTTPException(400, 'QQ 号无效')
        _, users = chat_json('users.json')
        info = users.get(qq) if isinstance(users, dict) else None
        info = info if isinstance(info, dict) else {}
        _, bots = chat_json('bots.json')
        bot = bots.get(qq) if isinstance(bots, dict) else None
        bot = bot if isinstance(bot, dict) else {}
        pending_msgs, mention_count = scan_memory_for(qq)
        usage = usage_totals().get(qq, {})
        aliases = info.get('aliases')
        _, profile_data = chat_json('profiles.json')
        profiles = profile_data.get('profiles') if isinstance(profile_data, dict) else {}
        profile = profiles.get(qq) if isinstance(profiles, dict) else None
        profile = profile if isinstance(profile, dict) else {}
        manual = profile.get('manual') if isinstance(profile.get('manual'), dict) else {}
        return {'qq': qq,
                'profile': profile,
                'birthday': manual.get('birthday') or profile.get('birthday') or '',
                'profile_notes': manual.get('notes') or '',
                'profile_tags': manual.get('tags') or [],
                'like_cap': like_cap_for(qq, users),
                'like_today': int(profile.get('like_today') or 0) if str(profile.get('like_day') or '') == datetime.now().strftime('%Y-%m-%d') else 0,
                'nickname': info.get('nickname', ''),
                'aliases': aliases if isinstance(aliases, dict) else {},
                'self_alias': info.get('self_alias', ''),
                'favorability': info.get('favorability', 0),
                'interactions': info.get('interactions', 0),
                'first_seen': info.get('first_seen', 0),
                'last_active': info.get('last_active', 0),
                'is_bot': bool(bot.get('is_bot')),
                'bot_name': bot.get('name', ''),
                'usage': {'calls': usage.get('calls', 0),
                          'prompt_tokens': usage.get('prompt_tokens', 0),
                          'completion_tokens': usage.get('completion_tokens', 0),
                          'total_tokens': usage.get('total_tokens', 0)},
                'message_count': len(pending_msgs),
                'mention_count': mention_count,
                'recent_messages': pending_msgs[-30:]}

    @app.put('/api/chat/profile/{qq}', dependencies=[Depends(authenticated)])
    async def chat_profile_update(qq: str, body: ProfileUpdate, request: Request):
        if not qq.isdigit():
            raise HTTPException(400, 'QQ 号无效')
        birthday = body.birthday.strip()
        if birthday:
            match = re.fullmatch(r'(?:\d{4}-)?(\d{1,2})-(\d{1,2})', birthday)
            if not match:
                raise HTTPException(400, '生日格式应为 MM-DD 或 YYYY-MM-DD')
            month, day = int(match.group(1)), int(match.group(2))
            if not (1 <= month <= 12 and 1 <= day <= 31):
                raise HTTPException(400, '生日日期无效')
            birthday = f'{month:02d}-{day:02d}'
        path, data = chat_json('profiles.json')
        data = data if isinstance(data, dict) else {}
        profiles = data.setdefault('profiles', {})
        profile = profiles.setdefault(qq, {'qq': qq})
        manual = profile.setdefault('manual', {})
        if birthday:
            manual['birthday'] = birthday
        else:
            manual.pop('birthday', None)
        manual['notes'] = body.notes.strip()[:500]
        manual['tags'] = [tag.strip()[:24] for tag in body.tags if tag.strip()][:12]
        profile['updated'] = int(time.time())
        atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))
        if body.nickname or body.self_alias:
            upath, users = chat_json('users.json')
            user = users.setdefault(qq, {})
            if body.nickname:
                user['nickname'] = body.nickname.strip()[:64]
            if body.self_alias:
                user['self_alias'] = body.self_alias.strip()[:20]
            atomic_write(upath, json.dumps(users, ensure_ascii=False, indent=2))
        audit(request, '编辑用户资料', f'{qq} · 生日 {birthday or "未设置"}')
        return {'ok': True, 'profile': profile, 'birthday': birthday,
                'notes': manual.get('notes', ''), 'tags': manual.get('tags', [])}

    @app.post('/api/chat/profile/{qq}/refresh', dependencies=[Depends(authenticated)])
    async def chat_profile_refresh(qq: str, request: Request, gid: str = ''):
        module = chat_module()
        fn = getattr(module, 'refresh_profile_now', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供资料刷新能力')
        try:
            profile = await fn(qq, gid or None)
        except Exception as error:
            raise HTTPException(502, f'资料刷新失败：{error}')
        audit(request, '刷新资料卡', f'{qq} · {gid or "全局"}')
        return {'ok': True, 'profile': profile}

    @app.post('/api/chat/profile/{qq}/like', dependencies=[Depends(authenticated)])
    async def chat_profile_like(qq: str, request: Request, times: int = 10):
        module = chat_module()
        fn = getattr(module, 'like_profile_now', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供点赞能力')
        try:
            result = await fn(qq, max(1, min(20, times)))
        except Exception as error:
            raise HTTPException(502, f'点赞失败：{error}')
        audit(request, '给资料卡点赞', f'{qq} · {times} 次')
        return result

    @app.get('/api/chat/social', dependencies=[Depends(authenticated)])
    async def chat_social():
        _, data = chat_json('profiles.json')
        data = data if isinstance(data, dict) else {}
        profiles = data.get('profiles') if isinstance(data.get('profiles'), dict) else {}
        today_key = datetime.now().strftime('%m-%d')
        _, user_data = chat_json('users.json')
        users = user_data if isinstance(user_data, dict) else {}
        try:
            birthday_min = float(chat_setting('CHAT_BIRTHDAY_MIN_FAVORABILITY', 10))
        except (TypeError, ValueError):
            birthday_min = 10.0
        birthdays = []
        for qq, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            manual = profile.get('manual') if isinstance(profile.get('manual'), dict) else {}
            birthday = str(manual.get('birthday') or profile.get('birthday') or '')
            if not re.fullmatch(r'\d{2}-\d{2}', birthday):
                continue
            birthdays.append({
                'qq': qq,
                'nickname': profile.get('card') or profile.get('nickname') or qq,
                'birthday': birthday,
                'today': birthday == today_key,
                'groups': len(profile.get('groups') or {}),
                'is_friend': bool(profile.get('is_friend')),
                'eligible': favor_of(qq, users) >= birthday_min,
            })
        birthdays.sort(key=lambda item: (not item['today'], item['birthday']))
        _, qzone_state = chat_json('qzone_state.json')
        qzone_state = qzone_state if isinstance(qzone_state, dict) else {}
        today_text = datetime.now().strftime('%Y-%m-%d')
        comments = {
            'initialized': bool(qzone_state.get('comment_initialized')),
            'seen': len(qzone_state.get('comment_seen') or []),
            'last_check': int(qzone_state.get('last_comment_check') or 0),
            'last_reply': int(qzone_state.get('last_comment_reply') or 0),
            'last_error': qzone_state.get('last_comment_error') or '',
            'replied_today': int(qzone_state.get('comment_reply_count') or 0)
                if str(qzone_state.get('comment_reply_day') or '') == today_text else 0,
            'last_comment': qzone_state.get('last_comment_text') or '',
            'last_reply_text': qzone_state.get('last_comment_reply_text') or '',
            'hot_until': int(qzone_state.get('comment_hot_until') or 0),
            'hot': int(qzone_state.get('comment_hot_until') or 0) > int(time.time()),
            'max_per_tick': int(chat_setting('CHAT_QZONE_COMMENT_MAX_PER_TICK', 0)),
            'max_per_day': int(chat_setting('CHAT_QZONE_COMMENT_MAX_PER_DAY', 0)),
        }
        last_post = int(qzone_state.get('last_post') or 0)
        min_hours = float(chat_setting('CHAT_QZONE_MIN_INTERVAL_HOURS', 2))
        ramp_hours = float(chat_setting('CHAT_QZONE_PROBABILITY_RAMP_HOURS', 96))
        max_probability = float(chat_setting('CHAT_QZONE_MAX_PROBABILITY', 0.8))
        elapsed_hours = (time.time() - last_post) / 3600 if last_post else ramp_hours
        if elapsed_hours < min_hours:
            current_probability = 0.0
        elif elapsed_hours >= ramp_hours:
            current_probability = max_probability
        else:
            current_probability = max_probability * (elapsed_hours - min_hours) / max(1e-6, ramp_hours - min_hours)
        qzone = {
            'enabled': bool(chat_setting('CHAT_QZONE_ENABLED', False)),
            'start_hour': int(chat_setting('CHAT_QZONE_START_HOUR', 10)),
            'end_hour': int(chat_setting('CHAT_QZONE_END_HOUR', 23)),
            'daily_limit': int(chat_setting('CHAT_QZONE_DAILY_LIMIT', 1)),
            'probability': float(chat_setting('CHAT_QZONE_PROBABILITY', 0.25)),
            'min_interval': float(chat_setting('CHAT_QZONE_MIN_INTERVAL_HOURS', 2)),
            'ramp_hours': float(chat_setting('CHAT_QZONE_PROBABILITY_RAMP_HOURS', 96)),
            'max_probability': float(chat_setting('CHAT_QZONE_MAX_PROBABILITY', 0.8)),
            'home_event_probability': float(chat_setting('CHAT_QZONE_HOME_EVENT_PROBABILITY', 0.08)),
            'outside_event_probability': float(chat_setting('CHAT_QZONE_OUTSIDE_EVENT_PROBABILITY', 0.22)),
            'image_enabled': bool(chat_setting('CHAT_IMAGE_ENABLED', True) and chat_setting('CHAT_IMAGE_API_BASE', '') and (chat_setting('CHAT_IMAGE_API_KEY', '') or os.environ.get('DASHSCOPE_API_KEY', '')) and chat_setting('CHAT_IMAGE_MODEL', '')),
            'image_probability': float(chat_setting('CHAT_QZONE_IMAGE_PROBABILITY', 0.35)),
            'event_image_probability': float(chat_setting('CHAT_QZONE_EVENT_IMAGE_PROBABILITY', 0.75)),
            'comment_enabled': bool(chat_setting('CHAT_QZONE_COMMENT_ENABLED', True)),
            'comment_interval': int(chat_setting('CHAT_QZONE_COMMENT_INTERVAL_MINUTES', 2)),
            'comment_hot_minutes': int(chat_setting('CHAT_QZONE_COMMENT_HOT_MINUTES', 10)),
            'comment_hot_interval': int(chat_setting('CHAT_QZONE_COMMENT_HOT_INTERVAL_SECONDS', 30)),
            'last_post': int(qzone_state.get('last_post') or 0),
            'last_event_key': qzone_state.get('last_event_key') or '',
            'current_probability': round(current_probability, 4),
            'posts': list(reversed(qzone_state.get('posts') or []))[:10],
            'last_error': qzone_state.get('last_error') or '',
            'comments': comments,
        }
        _, bots = chat_json('bots.json')
        return {
            'profiles': len(profiles),
            'with_birthday': sum(1 for item in birthdays if item['birthday']),
            'with_groups': sum(1 for item in birthdays if item['groups']),
            'birthdays': birthdays[:30],
            'today': [item for item in birthdays if item['today']],
            'qzone': qzone,
            'bots': len(bots) if isinstance(bots, dict) else 0,
            'profile_like_enabled': bool(chat_setting('CHAT_PROFILE_LIKE_ENABLED', False)),
            'birthday_hour': int(chat_setting('CHAT_BIRTHDAY_HOUR', 9)),
            'birthday_min_favorability': birthday_min,
        }

    @app.get('/api/chat/qzone/comments', dependencies=[Depends(authenticated)])
    async def chat_qzone_comments():
        module = chat_module()
        fn = getattr(module, 'preview_qzone_comments_now', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供空间评论预览能力')
        try:
            result = await fn()
        except Exception as error:
            raise HTTPException(502, f'读取空间评论失败：{error}')
        return result

    @app.post('/api/chat/qzone/comments/check', dependencies=[Depends(authenticated)])
    async def chat_qzone_comments_check(request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以检查空间评论')
        module = chat_module()
        fn = getattr(module, 'check_qzone_comments_now', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供空间评论回复能力')
        try:
            result = await fn(True)
        except Exception as error:
            raise HTTPException(502, f'检查空间评论失败：{error}')
        audit(request, '检查空间评论', f"新 {result.get('new', 0)} / 回复 {result.get('replied', 0)}")
        return result

    @app.post('/api/chat/reply/test', dependencies=[Depends(authenticated)])
    async def chat_reply_test(body: ReplyTest, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以测试聊天生成')
        module = chat_module()
        fn = getattr(module, 'test_reply_generation', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供生成测试')
        result = await fn(body.text)
        if not result.get('ok'):
            raise HTTPException(502, result.get('reason') or '生成失败')
        audit(request, '测试聊天生成', body.text[:60])
        return result

    @app.post('/api/chat/search/test', dependencies=[Depends(authenticated)])
    async def chat_search_test(body: SearchTest, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以测试联网检索')
        module = chat_module()
        fn = getattr(module, 'search_web_now', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供联网检索能力')
        try:
            result = await fn(body.query)
        except Exception as error:
            raise HTTPException(502, f'检索失败：{error}')
        if not result.get('ok'):
            raise HTTPException(409, result.get('reason') or '检索失败')
        audit(request, '测试联网检索', body.query[:60])
        return result

    @app.post('/api/chat/image/test', dependencies=[Depends(authenticated)])
    async def chat_image_test(body: ImageTest, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以测试图片生成')
        module = chat_module()
        fn = getattr(module, 'test_image_generation', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供图片生成能力')
        try:
            result = await fn(body.prompt)
        except Exception as error:
            raise HTTPException(502, f'图片生成失败：{error}')
        if not result.get('ok'):
            raise HTTPException(409, result.get('reason') or '图片生成失败')
        try:
            rel = Path(result['path']).resolve().relative_to(CHAT_DIR.resolve()).as_posix()
            result['url'] = '/api/media/emote?path=' + quote(rel, safe='')
        except Exception:
            result['url'] = ''
        audit(request, '测试图片生成', f"{result.get('provider')} · {result.get('model')}")
        return result

    @app.post('/api/chat/qzone/image-test', dependencies=[Depends(authenticated)])
    async def chat_qzone_image_test(body: QzoneImageTest, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以测试空间配图')
        module = chat_module()
        fn = getattr(module, 'test_qzone_image', None) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供空间配图测试能力')
        try:
            result = await fn(body.content, body.prompt)
        except Exception as error:
            raise HTTPException(502, f'空间配图测试失败：{error}')
        if not result.get('ok'):
            raise HTTPException(409, result.get('reason') or '空间配图测试失败')
        try:
            rel = Path(result['image']).resolve().relative_to(CHAT_DIR.resolve()).as_posix()
            result['url'] = '/api/media/emote?path=' + quote(rel, safe='')
        except Exception:
            result['url'] = ''
        audit(request, '测试空间配图', '私有空间动态')
        return result

    @app.post('/api/chat/qzone/post', dependencies=[Depends(authenticated)])
    async def chat_qzone_post(request: Request, body: QzonePost | None = None):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以手动发布空间动态')
        module = chat_module()
        content = (body.content.strip() if body else '')
        fn = getattr(
            module,
            'publish_qzone_text_now' if content else 'publish_qzone_now',
            None,
        ) if module else None
        if not callable(fn):
            raise HTTPException(501, '聊天插件未提供空间发布能力')
        try:
            result = await (fn(content) if content else fn(True))
        except Exception as error:
            raise HTTPException(502, f'空间发布失败：{error}')
        if not result.get('ok'):
            raise HTTPException(409, f'空间发布未完成：{result.get("reason")}')
        audit(request, '发布空间动态', result.get('content', '')[:80])
        return result

    @app.get('/api/chat/bots', dependencies=[Depends(authenticated)])
    async def chat_bots():
        _, bots = chat_json('bots.json')
        bots = bots if isinstance(bots, dict) else {}
        _, users = chat_json('users.json')
        items = []
        for qq, entry in bots.items():
            if not isinstance(entry, dict):
                continue
            items.append({
                'qq': qq,
                'name': entry.get('name') or (users.get(qq) or {}).get('nickname', ''),
                'is_bot': bool(entry.get('is_bot')),
                'source': entry.get('source', 'manual'),
                'time': entry.get('time', 0),
            })
        items.sort(key=lambda row: (not row['is_bot'], row['qq']))
        candidates = []
        for qq, info in (users.items() if isinstance(users, dict) else []):
            if not isinstance(info, dict) or qq in bots:
                continue
            name = str(info.get('nickname') or '')
            if BOT_NAME_RE.search(name):
                candidates.append({'qq': qq, 'name': name})
        return {'items': items, 'candidates': candidates[:50]}

    @app.post('/api/chat/bots', dependencies=[Depends(authenticated)])
    async def chat_bots_add(body: BotEntry, request: Request):
        path, bots = chat_json('bots.json')
        bots = bots if isinstance(bots, dict) else {}
        bots[body.qq] = {
            'is_bot': bool(body.is_bot),
            'name': body.name.strip()[:64],
            'source': body.source or 'manual',
            'time': int(time.time()),
        }
        atomic_write(path, json.dumps(bots, ensure_ascii=False, indent=2))
        audit(request, '登记机器人', f'{body.qq} · {"是" if body.is_bot else "否"}')
        return {'ok': True, 'qq': body.qq}

    @app.delete('/api/chat/bots/{qq}', dependencies=[Depends(authenticated)])
    async def chat_bots_delete(qq: str, request: Request):
        path, bots = chat_json('bots.json')
        bots = bots if isinstance(bots, dict) else {}
        if qq not in bots:
            raise HTTPException(404, '机器人名单中没有该 QQ')
        bots.pop(qq, None)
        atomic_write(path, json.dumps(bots, ensure_ascii=False, indent=2))
        audit(request, '移除机器人名单', qq)
        return {'ok': True}

    @app.get('/api/chat/growth', dependencies=[Depends(authenticated)])
    async def chat_growth():
        _, growth = chat_json('growth_persona.json')
        return {'items': [{'key': k, 'entries': v if isinstance(v, list) else []}
                          for k, v in growth.items()]}

    @app.put('/api/chat/growth/{key}', dependencies=[Depends(authenticated)])
    async def chat_growth_update(key: str, body: GrowthUpdate, request: Request):
        if not (key == 'global' or key.isdigit()):
            raise HTTPException(400, '成长分桶键无效')
        path, growth = chat_json('growth_persona.json')
        entries = [e.strip() for e in body.entries if e.strip()]
        if entries:
            growth[key] = entries
        else:
            growth.pop(key, None)
        atomic_write(path, json.dumps(growth, ensure_ascii=False, indent=2))
        audit(request, '编辑成长人格', f'{key} · {len(entries)} 条')
        return {'ok': True}

    def emote_index():
        _, learned = chat_json('emote_learned.json')
        learned = learned if isinstance(learned, dict) else {}
        by_path, by_hash = {}, {}
        for key, value in learned.items():
            if not isinstance(value, dict):
                continue
            if value.get('path'):
                by_path[str(value['path'])] = value
            by_hash[key] = value
        return by_path, by_hash, learned

    def media_url(rel: str, width: int | None = None) -> str:
        query = 'path=' + quote(rel, safe='')
        if width:
            query += f'&w={int(width)}'
        return '/api/media/emote?' + query

    @app.get('/api/chat/emotes', dependencies=[Depends(authenticated)])
    async def chat_emotes():
        by_path, by_hash, learned = emote_index()
        files = []
        categories: dict[str, int] = {}
        total_size = 0
        for folder in ('emotes', 'misc_emoji'):
            directory = CHAT_DIR / folder
            if not directory.exists():
                continue
            for path in sorted(directory.rglob('*')):
                if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                    continue
                rel = path.relative_to(CHAT_DIR).as_posix()
                category = path.parent.name if folder == 'emotes' else '杂项'
                meta = by_path.get(rel) or by_hash.get(path.stem) or {}
                label = str(meta.get('label') or (path.stem if folder == 'misc_emoji' else ''))
                stat = path.stat()
                total_size += stat.st_size
                files.append({
                    'path': rel, 'category': category, 'label': label,
                    'status': meta.get('status', 'saved'), 'time': meta.get('time', 0),
                    'size': stat.st_size, 'mtime': int(stat.st_mtime),
                    'ext': path.suffix.lower().lstrip('.'),
                    'animated': path.suffix.lower() in ('.gif', '.webp'),
                    'thumb': media_url(rel, 360) + f'&v={stat.st_mtime_ns}',
                    'url': media_url(rel) + f'&v={stat.st_mtime_ns}',
                })
                categories[category] = categories.get(category, 0) + 1
        files_total = len(files)
        files = files[:240]
        rejected = sum(1 for v in learned.values() if isinstance(v, dict) and v.get('status') == 'rejected')
        saved = sum(1 for v in learned.values() if isinstance(v, dict) and v.get('status') == 'saved')
        learnable = sum(1 for v in learned.values() if isinstance(v, dict) and v.get('status') == 'learnable')
        managed = []
        seen_hashes = set()
        if REJECTED_EMOTE_DIR.exists():
            for path in sorted(REJECTED_EMOTE_DIR.rglob('*')):
                if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                    continue
                # 拒绝区可能有几十万字节/上千个文件；文件名通常就是 sha1。
                # 只有非哈希文件名才读内容，避免每次打开表情页把磁盘读满。
                stem = path.stem
                if re.fullmatch(r"[0-9a-fA-F]{40}", stem):
                    digest = stem.lower()
                else:
                    digest = hashlib.sha1(path.relative_to(CHAT_DIR).as_posix().encode()).hexdigest()
                meta = learned.get(digest) if isinstance(learned.get(digest), dict) else {}
                if not meta:
                    meta = by_hash.get(digest) if isinstance(by_hash.get(digest), dict) else {}
                rel = path.relative_to(CHAT_DIR).as_posix()
                status = str(meta.get('status') or 'rejected')
                if status not in ('rejected', 'learnable'):
                    status = 'rejected'
                managed.append({
                    'hash': digest, 'path': rel,
                    'label': str(meta.get('label') or path.stem),
                    'category': str(meta.get('category') or ('可学习' if status == 'learnable' else '已拒绝')),
                    'status': status,
                    'reason': str(meta.get('reason') or '已移出收藏'),
                    'flow': str(meta.get('reason') or '')[:60],
                    'scene': str(meta.get('scene') or '')[:60],
                    'manual_note': str(meta.get('manual_note') or '')[:300],
                    'manual_time': int(meta.get('manual_time') or 0),
                    'time': int(meta.get('time') or path.stat().st_mtime),
                    'size': path.stat().st_size,
                    'animated': path.suffix.lower() in ('.gif', '.webp'),
                    'thumb': media_url(rel, 240),
                    'url': media_url(rel),
                    'no_file': False,
                })
                seen_hashes.add(digest)
        for digest, meta in learned.items():
            if not isinstance(meta, dict) or meta.get('status') not in ('rejected', 'learnable'):
                continue
            if digest in seen_hashes:
                continue
            rel_meta = str(meta.get('path') or '')
            thumb = ''
            url = ''
            size = 0
            if rel_meta:
                try:
                    candidate = (CHAT_DIR / rel_meta).resolve()
                    if CHAT_DIR.resolve() in candidate.parents and candidate.is_file():
                        thumb = media_url(rel_meta, 240)
                        url = media_url(rel_meta)
                        size = candidate.stat().st_size
                except Exception:
                    pass
            # 原因优先展示模型给出的具体判断，其次流程标注，最后才是兜底文案
            reason = str(meta.get('detail') or meta.get('reason') or '')
            if not reason:
                reason = '等待再次出现' if meta.get('status') == 'learnable' else '已拒绝（旧记录未保存原因）'
            label = str(meta.get('label') or '')
            if not label:
                label = rel_meta.rsplit('/', 1)[-1] if rel_meta else ''
            managed.append({
                'hash': digest, 'path': rel_meta,
                'label': label,
                'category': str(meta.get('category') or ('可学习' if meta.get('status') == 'learnable' else '已拒绝')),
                'status': str(meta.get('status')),
                'reason': reason,
                'flow': str(meta.get('reason') or '')[:60],
                'scene': str(meta.get('scene') or '')[:60],
                'manual_note': str(meta.get('manual_note') or '')[:300],
                'manual_time': int(meta.get('manual_time') or 0),
                'no_file': not rel_meta,
                'time': int(meta.get('time') or 0),
                'animated': rel_meta.lower().endswith(('.gif', '.webp')),
                'size': size, 'thumb': thumb, 'url': url,
            })
        # 确保 learnable 记录一定出现在面板上；即使同哈希存在拒绝区文件，
        # 也把该行更新为可学习，而不是继续显示为已拒绝。
        for digest, meta in learned.items():
            if not isinstance(meta, dict) or meta.get('status') != 'learnable':
                continue
            row = next((item for item in managed if item.get('hash') == digest), None)
            payload = {
                'status': 'learnable',
                'category': '可学习',
                'reason': str(meta.get('reason') or '等待再次出现'),
                'manual_note': str(meta.get('manual_note') or '')[:300],
                'manual_time': int(meta.get('manual_time') or 0),
                'time': int(meta.get('time') or 0),
            }
            if row is not None:
                row.update(payload)
            else:
                managed.append({
                    'hash': digest, 'path': str(meta.get('path') or ''),
                    'label': str(meta.get('label') or '未命名'),
                    **payload, 'flow': '', 'scene': '',
                    'size': 0, 'animated': False, 'thumb': '', 'url': '', 'no_file': True,
                })
        managed.sort(key=lambda row: (row['status'] != 'rejected', -row['time']))
        managed_total = len(managed)
        managed = managed[:120]
        learnable_items = []
        for digest, meta in learned.items():
            if not isinstance(meta, dict) or meta.get('status') != 'learnable':
                continue
            rel_meta = str(meta.get('path') or '')
            thumb = media_url(rel_meta, 240) if rel_meta else ''
            url = media_url(rel_meta) if rel_meta else ''
            learnable_items.append({
                'hash': digest, 'path': rel_meta,
                'label': str(meta.get('label') or '未命名'),
                'category': '可学习', 'status': 'learnable',
                'reason': str(meta.get('reason') or '等待再次出现'),
                'manual_note': str(meta.get('manual_note') or '')[:300],
                'manual_time': int(meta.get('manual_time') or 0),
                'time': int(meta.get('time') or 0), 'size': 0,
                'animated': rel_meta.lower().endswith(('.gif', '.webp')),
                'thumb': thumb, 'url': url, 'no_file': not bool(rel_meta),
            })
        learnable_items = learnable_items[:120]
        return {'files': files, 'files_total': files_total, 'total_size': total_size,
                'managed_total': managed_total,
                'categories': [{'label': k, 'count': v}
                               for k, v in sorted(categories.items(), key=lambda r: -r[1])],
                'learned': len(learned), 'saved': saved, 'rejected': rejected,
                'learnable': learnable, 'managed': managed, 'learnable_items': learnable_items,
                'cache': media.stats()}

    def emote_file(path_value: str):
        if not path_value or '\\' in path_value or path_value.startswith('/'):
            raise HTTPException(400, '路径无效')
        base = CHAT_DIR.resolve()
        path = (base / path_value).resolve()
        if base not in path.parents or not path.is_file():
            raise HTTPException(404, '文件不存在')
        return path

    async def serve_media(path_value: str, request: Request, width: int = 0):
        source = emote_file(path_value)
        if source.suffix.lower() not in IMAGE_SUFFIXES:
            raise HTTPException(404, '不是可预览的图片')
        cache_headers = {
            'Cache-Control': 'private, max-age=604800, stale-while-revalidate=86400',
            'X-Content-Type-Options': 'nosniff',
            'Content-Disposition': 'inline',
            'Vary': 'Cookie',
        }
        if width:
            thumb = await run_in_threadpool(media.thumbnail, source, media.clamp_width(width))
            if thumb is not None:
                etag = thumb.etag
                target = thumb.path
                media_type = 'image/webp'
            else:
                etag = media.source_etag(source)
                target = source
                media_type = media.media_type(source)
        else:
            etag = media.source_etag(source)
            target = source
            media_type = media.media_type(source)
        cache_headers['ETag'] = etag
        if request.headers.get('if-none-match') == etag:
            return Response(status_code=304, headers=cache_headers)
        return FileResponse(target, media_type=media_type, headers=cache_headers)

    @app.get('/api/media/emote', dependencies=[Depends(authenticated)])
    async def media_emote(request: Request, path: str, w: int = 0):
        return await serve_media(path, request, w)

    @app.get('/api/media/cache', dependencies=[Depends(authenticated)])
    async def media_cache():
        return {'ok': True, **media.stats()}

    @app.delete('/api/media/cache', dependencies=[Depends(authenticated)])
    async def media_cache_clear(request: Request):
        removed = await run_in_threadpool(media.clear)
        audit(request, '清空图片缓存', f'{removed} 个缩略图')
        return {'ok': True, 'removed': removed, **media.stats()}

    @app.post('/api/media/warm', dependencies=[Depends(authenticated)])
    async def media_warm(body: MediaWarm, request: Request):
        paths = [
            path
            for folder in ('emotes', 'misc_emoji')
            for path in (CHAT_DIR / folder).rglob('*')
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        ]
        generated = 0
        failed = 0
        for path in paths[:40]:
            thumb = await run_in_threadpool(media.thumbnail, path, body.width)
            if thumb is None:
                failed += 1
            else:
                generated += 1
        audit(request, '预热图片缓存', f'{generated} 张 · 宽度 {body.width}')
        return {'ok': True, 'generated': generated, 'failed': failed,
                'total': len(paths[:40]), **media.stats()}

    # ---------- 数据备份与恢复 ----------

    @app.get('/api/backups', dependencies=[Depends(authenticated)])
    async def list_backups():
        return backups.list()

    @app.post('/api/backups', dependencies=[Depends(authenticated)])
    async def create_backup(body: BackupCreate, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以创建备份')
        kind = body.kind if body.kind in {'manual', 'auto'} else 'manual'
        try:
            result = await run_in_threadpool(
                backups.create, str(driver.config.environment), kind
            )
        except BackupError as error:
            raise HTTPException(400, str(error))
        audit(request, '创建数据备份', f'{result["name"]} · {result["files"]} 个文件')
        return result

    @app.get('/api/backups/{name}', dependencies=[Depends(authenticated)])
    async def download_backup(name: str, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以下载备份')
        try:
            path = backups.resolve(name)
        except BackupError as error:
            raise HTTPException(404, str(error))
        audit(request, '下载数据备份', name)
        return FileResponse(path, filename=name, media_type='application/gzip')

    @app.delete('/api/backups/{name}', dependencies=[Depends(authenticated)])
    async def delete_backup(name: str, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以删除备份')
        try:
            backups.delete(name)
        except BackupError as error:
            raise HTTPException(404, str(error))
        audit(request, '删除数据备份', name)
        return {'ok': True}

    @app.post('/api/backups/{name}/restore', dependencies=[Depends(authenticated)])
    async def restore_backup(name: str, body: BackupRestore, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可以恢复备份')
        if not body.confirm:
            raise HTTPException(400, '恢复备份需要显式确认')
        try:
            result = await run_in_threadpool(backups.restore, name)
        except BackupError as error:
            raise HTTPException(400, str(error))
        # 恢复后的 .env 同步进运行时；聊天插件再清空记忆缓存并重读状态文件
        runtime = update_runtime_config(read_config())
        chat_plugin = next(
            (p for p in nonebot.get_loaded_plugins() if p.name == 'chat'), None
        )
        reload_data = getattr(chat_plugin.module, 'reload_data', None) if chat_plugin else None
        if callable(reload_data):
            try:
                reload_data()
            except Exception as error:
                logger.warning(f'管理页面：恢复后重载聊天数据失败：{error}')
                runtime['errors'].append(f'chat.reload_data: {error}')
        hot = bool(runtime.get('hot_keys'))
        restart_required = bool(runtime.get('restart_keys') or runtime.get('errors'))
        logger.info(
            f'管理页面：从备份 {name} 恢复 {result["files"]} 个文件；'
            f'热加载={hot} 需重启={restart_required}'
        )
        audit(request, '恢复数据备份', f'{name} · {result["files"]} 个文件')
        return {**result, 'hot_reloaded': hot,
                'restart_required': restart_required,
                'hot_keys': runtime.get('hot_keys', []),
                'restart_keys': runtime.get('restart_keys', []),
                'errors': runtime.get('errors', [])}

    @app.get('/api/chat/emote-file', dependencies=[Depends(authenticated)])
    async def chat_emote_file(path: str, request: Request):
        return await serve_media(path, request, 0)

    @app.post('/api/chat/emotes', dependencies=[Depends(authenticated)])
    async def chat_emote_upload(body: EmoteUpload, request: Request):
        name = body.name.strip()
        category = body.category.strip().lower()
        if not re.fullmatch(r'[\w\u4e00-\u9fa5.-]+', name) or '/' in name or '\\' in name or name in ('.', '..'):
            raise HTTPException(400, '文件名无效')
        if category == 'misc':
            category = 'misc_emoji'
        elif not re.fullmatch(r'[a-z0-9_]+', category):
            raise HTTPException(400, '分类无效')
        try:
            raw = base64.b64decode(body.data, validate=True)
        except Exception:
            raise HTTPException(400, '图片数据不是有效的 Base64')
        if not raw or len(raw) > 6 * 1024 * 1024:
            raise HTTPException(400, '图片为空或超过 6MB')
        if not (raw.startswith(b'\x89PNG') or raw.startswith(b'\xff\xd8\xff')
                or raw.startswith(b'GIF8') or (raw.startswith(b'RIFF') and raw[8:12] == b'WEBP')):
            raise HTTPException(400, '仅支持 PNG / JPEG / GIF / WebP 图片')
        if Path(name).suffix.lower() not in {'.png', '.jpg', '.jpeg', '.gif', '.webp'}:
            raise HTTPException(400, '文件名需带图片扩展名')
        directory = CHAT_DIR / category
        directory.mkdir(parents=True, exist_ok=True)
        target = (directory / name).resolve()
        if CHAT_DIR.resolve() not in target.parents:
            raise HTTPException(400, '路径越界')
        if target.exists():
            raise HTTPException(409, '同名文件已存在')
        target.write_bytes(raw)
        digest = hashlib.sha1(raw).hexdigest()
        _, _, learned = emote_index()
        learned[digest] = {'status': 'saved', 'source': 'manual',
                           'category': 'emotion' if category != 'misc_emoji' else 'misc',
                           'label': name.rsplit('.', 1)[0],
                           'path': target.relative_to(CHAT_DIR).as_posix(),
                           'time': int(time.time())}
        atomic_write(CHAT_DIR / 'emote_learned.json', json.dumps(learned, ensure_ascii=False, indent=2))
        audit(request, '上传表情', target.relative_to(CHAT_DIR).as_posix())
        notify_emote_reload()
        return {'ok': True, 'path': target.relative_to(CHAT_DIR).as_posix()}

    @app.delete('/api/chat/emote-file', dependencies=[Depends(authenticated)])
    async def chat_emote_delete(path: str, request: Request):
        target = emote_file(path)
        if target.suffix == '.json':
            raise HTTPException(400, '不能删除数据文件')
        rel = target.relative_to(CHAT_DIR).as_posix()
        if REJECTED_EMOTE_DIR.resolve() in target.resolve().parents:
            raise HTTPException(400, '拒绝区文件请使用拒绝区管理接口')
        result = await move_emote_to_rejected(
            rel, reason='deleted:手动删除', source_kind='deleted'
        )
        audit(request, '删除表情', path)
        notify_emote_reload()
        return {**result, 'status': 'rejected'}


    async def move_emote_to_rejected(rel: str, *, reason: str = 'manual:拒绝', source_kind: str = 'manual') -> dict:
        source = emote_file(rel)
        if REJECTED_EMOTE_DIR.resolve() in source.resolve().parents:
            raise HTTPException(400, '该文件已在拒绝区')
        digest = hashlib.sha1(source.read_bytes()).hexdigest()
        dest = (REJECTED_EMOTE_DIR / source.relative_to(CHAT_DIR)).resolve()
        if CHAT_DIR.resolve() not in dest.parents:
            raise HTTPException(400, '路径越界')
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest = dest.with_name(f'{dest.stem}_{digest[:8]}{dest.suffix}')
        await run_in_threadpool(media.invalidate, source)
        source.replace(dest)
        _, _, learned = emote_index()
        learned[digest] = {
            'status': 'rejected', 'source': source_kind, 'path': dest.relative_to(CHAT_DIR).as_posix(),
            'reason': reason, 'time': int(time.time()),
        }
        atomic_write(CHAT_DIR / 'emote_learned.json', json.dumps(learned, ensure_ascii=False, indent=2))
        return {'ok': True, 'hash': digest, 'path': dest.relative_to(CHAT_DIR).as_posix()}

    @app.post('/api/chat/emotes/reject', dependencies=[Depends(authenticated)])
    async def chat_emote_reject(body: EmotePath, request: Request):
        result = await move_emote_to_rejected(body.path)
        audit(request, '拒绝表情', body.path)
        notify_emote_reload()
        return result

    @app.post('/api/chat/emotes/accept', dependencies=[Depends(authenticated)])
    async def chat_emote_accept(body: EmoteHash, request: Request):
        digest = body.hash.strip().lower()
        _, by_hash, learned = emote_index()
        entry = by_hash.get(digest) if isinstance(by_hash.get(digest), dict) else {}
        rel = str(entry.get('path') or '')
        source = None
        if rel:
            try:
                candidate = emote_file(rel)
                if REJECTED_EMOTE_DIR.resolve() in candidate.resolve().parents:
                    source = candidate
            except HTTPException:
                source = None
        if source is None and REJECTED_EMOTE_DIR.exists():
            for path in REJECTED_EMOTE_DIR.rglob('*'):
                if path.is_file() and hashlib.sha1(path.read_bytes()).hexdigest() == digest:
                    source = path
                    break
        if source is not None:
            rel_part = source.relative_to(REJECTED_EMOTE_DIR)
            dest = (CHAT_DIR / rel_part).resolve()
            if CHAT_DIR.resolve() not in dest.parents:
                raise HTTPException(400, '路径越界')
            if dest.exists():
                raise HTTPException(409, '目标位置已有同名文件')
            dest.parent.mkdir(parents=True, exist_ok=True)
            source.replace(dest)
            learned[digest] = {
                'status': 'saved', 'source': 'manual-accept',
                'path': dest.relative_to(CHAT_DIR).as_posix(),
                'time': int(time.time()),
            }
            status = 'saved'
        else:
            learned[digest] = {
                'status': 'learnable', 'source': 'manual-accept',
                'reason': 'accepted:等待再次出现', 'time': int(time.time()),
            }
            status = 'learnable'
        atomic_write(CHAT_DIR / 'emote_learned.json', json.dumps(learned, ensure_ascii=False, indent=2))
        audit(request, '接受表情', f'{digest[:8]} → {status}')
        notify_emote_reload()
        return {'ok': True, 'hash': digest, 'status': status}

    @app.post('/api/chat/emotes/learnable', dependencies=[Depends(authenticated)])
    async def chat_emote_learnable(body: dict, request: Request):
        digest = str(body.get('hash') or '').strip().lower()
        note = str(body.get('note') or '').strip()[:300]
        if not re.fullmatch(r'[a-f0-9]{8,64}', digest):
            raise HTTPException(400, '表情哈希无效')
        _, by_hash, learned = emote_index()
        entry = by_hash.get(digest) if isinstance(by_hash.get(digest), dict) else {}
        if not entry:
            entry = learned.get(digest) if isinstance(learned.get(digest), dict) else {}
        if not entry:
            raise HTTPException(404, '表情记录不存在')
        if str(entry.get('status') or '') == 'saved':
            raise HTTPException(409, '已拥有表情不能标记为可学习')
        learned[digest] = {
            **entry,
            'status': 'learnable',
            'source': 'manual-learnable',
            'manual_note': note,
            'manual_time': int(time.time()),
        }
        atomic_write(CHAT_DIR / 'emote_learned.json', json.dumps(learned, ensure_ascii=False, indent=2))
        audit(request, '加入可学习表情', f'{digest[:8]} {note[:40]}')
        notify_emote_reload()
        return {'ok': True, 'hash': digest, 'note': note}

    @app.delete('/api/chat/emotes/managed', dependencies=[Depends(authenticated)])
    async def chat_emote_managed_delete(body: EmotePath, request: Request):
        target = emote_file(body.path)
        if REJECTED_EMOTE_DIR.resolve() not in target.resolve().parents:
            raise HTTPException(400, '不是拒绝区文件')
        digest = hashlib.sha1(target.read_bytes()).hexdigest()
        await run_in_threadpool(media.invalidate, target)
        target.unlink()
        _, _, learned = emote_index()
        learned[digest] = {
            'status': 'rejected', 'source': 'deleted', 'path': body.path,
            'reason': 'deleted:拒绝区永久删除', 'time': int(time.time()),
        }
        atomic_write(CHAT_DIR / 'emote_learned.json', json.dumps(learned, ensure_ascii=False, indent=2))
        audit(request, '删除拒绝表情', body.path)
        notify_emote_reload()
        return {'ok': True, 'status': 'rejected'}

    # ---------- 聊天白名单与记忆 ----------

    def whitelist_path(kind: str):
        if kind == 'group':
            return CHAT_DIR / 'whitelist_group.json'
        if kind == 'user':
            return CHAT_DIR / 'whitelist_user.json'
        raise HTTPException(400, '白名单类型无效')

    @app.get('/api/chat/whitelist', dependencies=[Depends(authenticated)])
    async def chat_whitelist():
        _, groups = chat_json('whitelist_group.json')
        _, users = chat_json('whitelist_user.json')
        return {'groups': groups if isinstance(groups, list) else [],
                'users': users if isinstance(users, list) else []}

    @app.put('/api/chat/whitelist', dependencies=[Depends(authenticated)])
    async def chat_whitelist_update(body: WhitelistToggle, request: Request):
        path = whitelist_path(body.kind)
        _, data = chat_json(path.name)
        if not isinstance(data, list):
            data = []
        field = 'id_group' if body.kind == 'group' else 'id_user'
        for entry in data:
            if isinstance(entry, dict) and str(entry.get(field)) == body.id:
                entry['enabled'] = body.enabled
                break
        else:
            raise HTTPException(404, '白名单条目不存在')
        atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))
        audit(request, '切换白名单', f'{body.kind} {body.id} → {"启用" if body.enabled else "停用"}')
        return {'ok': True}

    @app.post('/api/chat/whitelist', dependencies=[Depends(authenticated)])
    async def chat_whitelist_create(body: WhitelistCreate, request: Request):
        if body.kind not in ('group', 'user'):
            raise HTTPException(400, '白名单类型无效')
        if not body.id.isdigit():
            raise HTTPException(400, '群号 / QQ 号必须是数字')
        path = whitelist_path(body.kind)
        _, data = chat_json(path.name)
        data = data if isinstance(data, list) else []
        field = 'id_group' if body.kind == 'group' else 'id_user'
        if any(isinstance(entry, dict) and str(entry.get(field)) == body.id for entry in data):
            raise HTTPException(409, '该条目已在白名单中')
        entry = {field: int(body.id), 'enabled': body.enabled}
        if body.kind == 'group':
            entry['config'] = {
                'llm': {'api_base': '', 'api_key': '', 'model': ''},
                'vision': {'enabled': True, 'api_base': '', 'api_key': '',
                           'model': '', 'detail': '', 'carry_history': True},
                'emote': {'enabled': True},
            }
        data.append(entry)
        atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))
        audit(request, '新增白名单', f'{body.kind} {body.id}')
        return {'ok': True}

    @app.delete('/api/chat/whitelist', dependencies=[Depends(authenticated)])
    async def chat_whitelist_delete(kind: str, id: str, request: Request):
        path = whitelist_path(kind)
        _, data = chat_json(path.name)
        data = data if isinstance(data, list) else []
        field = 'id_group' if kind == 'group' else 'id_user'
        kept = [entry for entry in data
                if not (isinstance(entry, dict) and str(entry.get(field)) == str(id))]
        if len(kept) == len(data):
            raise HTTPException(404, '白名单条目不存在')
        atomic_write(path, json.dumps(kept, ensure_ascii=False, indent=2))
        audit(request, '删除白名单', f'{kind} {id}')
        return {'ok': True}

    @app.get('/api/chat/sessions', dependencies=[Depends(authenticated)])
    async def chat_sessions():
        items = []
        for kind in ('group', 'user'):
            directory = CHAT_DIR / 'memory' / kind
            if not directory.exists():
                continue
            for path in directory.glob('*.json'):
                try:
                    data = json.loads(path.read_text(encoding='utf-8'))
                except Exception:
                    continue
                pending = data.get('pending') or []
                episodes = data.get('episodes') or []
                times = [e.get('time', 0) for e in pending + episodes if isinstance(e, dict)]
                items.append({'kind': kind, 'key': path.stem, 'pending': len(pending),
                              'episodes': len(episodes), 'facts': len(data.get('facts') or []),
                              'recalled': sum(1 for e in pending if isinstance(e, dict) and e.get('recalled')),
                              'summary': bool(data.get('summary')), 'last': int(max(times, default=0))})
        items.sort(key=lambda r: -r['last'])
        return {'items': items}

    @app.get('/api/chat/session/{kind}/{key}', dependencies=[Depends(authenticated)])
    async def chat_session(kind: str, key: str):
        path = memory_path(kind, key)
        if not path.exists():
            raise HTTPException(404, '会话记忆不存在')
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            raise HTTPException(422, '记忆文件损坏，无法解析')
        return {'kind': kind, 'key': key, 'summary': data.get('summary', ''),
                'facts': data.get('facts') or [],
                'episodes': (data.get('episodes') or [])[-50:],
                'pending': (data.get('pending') or [])[-100:]}

    @app.delete('/api/chat/session/{kind}/{key}', dependencies=[Depends(authenticated)])
    async def chat_session_delete(kind: str, key: str, request: Request):
        path = memory_path(kind, key)
        if not path.exists():
            raise HTTPException(404, '会话记忆不存在')
        path.unlink()
        audit(request, '删除记忆', f'{kind}/{key}')
        return {'ok': True}

    # ---------- 数据统计 ----------

    @app.get('/api/chat/stats', dependencies=[Depends(authenticated)])
    async def chat_stats():
        _, users = chat_json('users.json')
        buckets = {'负值': 0, '0-10': 0, '10-20': 0, '20-30': 0, '30 以上': 0}
        interaction_top = []
        now = time.time()
        active = {'24h': 0, '7d': 0, '30d': 0}
        for qq, info in (users.items() if isinstance(users, dict) else []):
            if not isinstance(info, dict):
                continue
            value = float(info.get('favorability', 0) or 0)
            if value < 0:
                buckets['负值'] += 1
            elif value < 10:
                buckets['0-10'] += 1
            elif value < 20:
                buckets['10-20'] += 1
            elif value < 30:
                buckets['20-30'] += 1
            else:
                buckets['30 以上'] += 1
            interaction_top.append({'qq': qq, 'nickname': info.get('nickname', ''),
                                    'interactions': int(info.get('interactions', 0) or 0),
                                    'favorability': value,
                                    'last_active': info.get('last_active', 0)})
            last = float(info.get('last_active', 0) or 0)
            if last and last >= now - 86400:
                active['24h'] += 1
            if last and last >= now - 7 * 86400:
                active['7d'] += 1
            if last and last >= now - 30 * 86400:
                active['30d'] += 1
        interaction_top.sort(key=lambda r: -r['interactions'])
        totals = usage_totals()
        tokens = sorted(({
            'qq': qq,
            'nickname': (users.get(qq) or {}).get('nickname', '') if isinstance(users.get(qq), dict) else '',
            'calls': v.get('calls', 0),
            'prompt_tokens': v.get('prompt_tokens', 0),
            'completion_tokens': v.get('completion_tokens', 0),
            'total_tokens': v.get('total_tokens', 0)} for qq, v in totals.items()),
                        key=lambda r: -r['total_tokens'])[:15]
        memories = []
        for kind in ('group', 'user'):
            directory = CHAT_DIR / 'memory' / kind
            if not directory.exists():
                continue
            for path in directory.glob('*.json'):
                try:
                    data = json.loads(path.read_text(encoding='utf-8'))
                except Exception:
                    continue
                memories.append({'kind': kind, 'key': path.stem,
                                 'nickname': (users.get(path.stem) or {}).get('nickname', '') if kind == 'user' else '',
                                 'pending': len(data.get('pending') or []),
                                 'episodes': len(data.get('episodes') or []),
                                 'facts': len(data.get('facts') or [])})
        memories.sort(key=lambda r: -(r['pending'] + r['episodes']))
        _, _, learned = emote_index()
        emote_categories: dict[str, int] = {}
        for value in learned.values():
            if isinstance(value, dict) and value.get('status') == 'saved':
                label = str(value.get('label') or '未命名')
                emote_categories[label] = emote_categories.get(label, 0) + 1
        return {'favor_buckets': [{'label': k, 'count': v} for k, v in buckets.items()],
                'tokens': tokens, 'memories': memories[:15],
                'interaction_top': interaction_top[:15], 'active': active,
                'emote_categories': [{'label': k, 'count': v}
                                     for k, v in sorted(emote_categories.items(), key=lambda r: -r[1])[:20]]}

    @app.get('/api/chat/quality', dependencies=[Depends(authenticated)])
    async def chat_quality():
        return await run_in_threadpool(analyze_chat, CHAT_DIR)

    # ---------- 定时任务 ----------

    @app.get('/api/jobs', dependencies=[Depends(authenticated)])
    async def jobs():
        try:
            from nonebot_plugin_apscheduler import scheduler
        except ImportError:
            return {'available': False, 'running': False, 'items': []}
        items = []
        for job in scheduler.get_jobs():
            next_run = getattr(job, 'next_run_time', None)
            items.append({'id': job.id, 'name': job.name or job.id, 'trigger': str(job.trigger),
                          'next_run': next_run.isoformat() if next_run else None,
                          'paused': next_run is None})
        items.sort(key=lambda r: r['next_run'] or '~')
        return {'available': True, 'running': bool(getattr(scheduler, 'running', False)), 'items': items}

    @app.put('/api/jobs/{job_id}', dependencies=[Depends(authenticated)])
    async def job_toggle(job_id: str, body: Toggle, request: Request):
        try:
            from nonebot_plugin_apscheduler import scheduler
        except ImportError:
            raise HTTPException(503, '未安装定时任务插件')
        if job_id not in {job.id for job in scheduler.get_jobs()}:
            raise HTTPException(404, '任务不存在')
        if body.enabled:
            scheduler.resume_job(job_id)
        else:
            scheduler.pause_job(job_id)
        audit(request, '定时任务', f'{job_id} → {"恢复" if body.enabled else "暂停"}')
        return {'ok': True}

    # ---------- 多管理员与审计 ----------

    @app.get('/api/admins', dependencies=[Depends(authenticated)])
    async def list_admins(request: Request):
        now = time.time()
        active: dict[str, int] = {}
        for token, (name, _role) in list(session_user.items()):
            if sessions.get(token, 0) >= now:
                active[name] = active.get(name, 0) + 1
        rows = [{'username': owner_name, 'role': 'owner', 'active': active.get(owner_name, 0)}]
        for name, record in sorted(read_admins().items()):
            rows.append({'username': name, 'role': str(record.get('role', 'admin')),
                         'active': active.get(name, 0)})
        name, role = actor(request)
        return {'items': rows, 'current': name, 'role': role}

    @app.post('/api/admins', dependencies=[Depends(authenticated)])
    async def create_admin(body: AdminCreate, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可管理管理员')
        username = body.username.strip()
        if username == owner_name:
            raise HTTPException(400, '用户名与所有者冲突')
        admins = read_admins()
        if username in admins:
            raise HTTPException(409, '该管理员已存在')
        admins[username] = {'hash': hashlib.sha256(body.password.encode()).hexdigest(), 'role': 'admin'}
        write_admins(admins)
        audit(request, '新增管理员', username)
        return {'ok': True}

    @app.delete('/api/admins/{username}', dependencies=[Depends(authenticated)])
    async def delete_admin(username: str, request: Request):
        if actor(request)[1] != 'owner':
            raise HTTPException(403, '仅所有者可管理管理员')
        admins = read_admins()
        if username not in admins:
            raise HTTPException(404, '管理员不存在')
        admins.pop(username)
        write_admins(admins)
        for token, (name, _role) in list(session_user.items()):
            if name == username:
                sessions.pop(token, None)
                save_sessions()
                session_user.pop(token, None)
        audit(request, '删除管理员', username)
        return {'ok': True}

    @app.get('/api/audit', dependencies=[Depends(authenticated)])
    async def audit_logs_page():
        items = list(audit_logs)
        if not items and audit_file.exists():
            for line in audit_file.read_text(encoding='utf-8').splitlines()[-500:]:
                try:
                    items.append(json.loads(line))
                except Exception:
                    continue
        return {'items': items[::-1]}

    # ---------- 运维与热更新 ----------

    @app.post('/api/ops/reload', dependencies=[Depends(authenticated)])
    async def ops_reload(body: OpsReload, request: Request):
        target = next((p for p in nonebot.get_loaded_plugins() if p.name == body.plugin), None)
        if target is None:
            raise HTTPException(404, '插件不存在')
        try:
            hot_reload(target)
        except Exception as error:
            logger.warning(f'管理页面：插件 {body.plugin} 热重载失败：{error}')
            raise HTTPException(500, f'重载失败：{error}')
        logger.info(f'管理页面：已热重载插件 {body.plugin}')
        audit(request, '热重载插件', body.plugin)
        return {'ok': True, 'plugin': body.plugin}

    @app.get('/api/ops/export', dependencies=[Depends(authenticated)])
    async def ops_export():
        values = {key: ('' if SECRET.search(key) else value.strip('"\' '))
                  for key, value in read_config().items()}
        return {'file': env_path().name, 'values': values}

    @app.post('/api/ops/import', dependencies=[Depends(authenticated)])
    async def ops_import(body: ImportPayload, request: Request):
        values = {key.strip().upper(): str(value) for key, value in body.config.items() if str(value).strip()}
        if not values:
            raise HTTPException(400, '没有可导入的配置项（空值会被忽略以保护敏感信息）')
        count, runtime = apply_config(values)
        restart_required = bool(runtime['restart_keys'] or runtime['errors'])
        logger.info(
            f'管理页面：导入 {count} 项配置；'
            f'热加载 {len(runtime["hot_keys"])} 项，需重启 {len(runtime["restart_keys"])} 项'
        )
        audit(request, '导入配置', f'{count} 项')
        return {'ok': True, 'imported': count, 'restart_required': restart_required,
                'hot_reloaded': bool(runtime['hot_keys']),
                'hot_keys': runtime['hot_keys'],
                'restart_keys': runtime['restart_keys'],
                'errors': runtime['errors']}

    return app


def install():
    driver = nonebot.get_driver()
    if not getattr(driver.config, 'admin_enabled', True):
        return
    app = create_app(driver)
    host = str(getattr(driver.config, 'admin_host', '0.0.0.0'))
    port = int(getattr(driver.config, 'admin_port', 14000))
    # NoneBot owns process-wide logging. A second Uvicorn dictConfig adds
    # a parent console handler and makes the main server's records print twice.
    server = AdminServer(uvicorn.Config(
        app, host=host, port=port, log_config=None, log_level=None,
        http=AdminHTTPProtocol,
    ))
    task = None
    backup_task = None
    health_task = None

    async def auto_backup_loop():
        """每天 04:00 之后创建一份 auto 数据备份，进程重启不会重复创建。"""
        await asyncio.sleep(20)
        while True:
            try:
                now = time.localtime()
                if now.tm_hour >= 4:
                    items = app.state.backups.list()['items']
                    today = time.strftime('%Y-%m-%d', now)
                    exists = any(
                        item['kind'] == 'auto' and str(item['created']).startswith(today)
                        for item in items
                    )
                    if not exists:
                        result = await asyncio.to_thread(
                            app.state.backups.create,
                            str(driver.config.environment),
                            'auto',
                        )
                        logger.info(
                            f'管理页面：已创建每日自动备份 {result["name"]}'
                            f'（{result["files"]} 个文件）'
                        )
            except Exception as error:
                logger.warning(f'管理页面：自动备份失败：{error}')
            await asyncio.sleep(1800)

    async def health_loop():
        """每 60 秒检查一次；异常持续时最多每 30 分钟写一条 WARNING，避免刷屏。"""
        await asyncio.sleep(15)
        while True:
            try:
                snapshot = app.state.collect_health()
                warnings = snapshot.get('warnings') or []
                publisher = getattr(app.state, 'publish_event', None)
                if callable(publisher):
                    publisher('health', status=snapshot.get('status'), bots=snapshot.get('bots'),
                              warnings=warnings, uptime=snapshot.get('uptime'))
                last = float(app.state.health.get('last_log') or 0)
                if warnings:
                    try:
                        alert_fn = getattr(app.state, 'deliver_health_alert', None)
                        if callable(alert_fn):
                            await alert_fn(snapshot)
                    except Exception as error:
                        logger.warning(f'管理页面：健康告警发送异常：{error}')
                if warnings and time.time() - last > 1800:
                    app.state.health['last_log'] = time.time()
                    logger.warning('管理页面健康检查：' + '；'.join(warnings))
            except Exception as error:
                logger.warning(f'管理页面：健康巡检失败：{error}')
            await asyncio.sleep(60)

    @event_preprocessor
    async def count_events():
        app.state.counters['events'] += 1

    @run_preprocessor
    async def gate(matcher: Matcher):
        plugin = matcher.plugin
        if plugin and plugin.name in app.state.disabled:
            raise IgnoredException('管理页面已停用该插件的事件响应')

    @driver.on_startup
    async def start():
        nonlocal task, backup_task, health_task
        # Bind before starting the task so port conflicts fail visibly without a hidden SystemExit.
        import socket
        sock = None
        last_error = None
        for attempt in range(8):
            candidate = socket.socket(socket.AF_INET6 if ':' in host else socket.AF_INET, socket.SOCK_STREAM)
            try:
                candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                candidate.bind((host, port))
                candidate.setblocking(False)
                sock = candidate
                break
            except OSError as error:
                last_error = error
                candidate.close()
                if attempt < 7:
                    time.sleep(1)
        if sock is None:
            logger.error(f'管理页面无法监听 {host}:{port}，请检查端口占用')
            raise last_error or OSError('bind failed')
        task = asyncio.create_task(server.serve(sockets=[sock]))
        backup_task = asyncio.create_task(auto_backup_loop())
        health_task = asyncio.create_task(health_loop())
        logger.info(f'Yuna 管理页面：http://{host}:{port}；口令由 ADMIN_PASSWORD 或 data/admin/password.txt 提供')

    @driver.on_shutdown
    async def stop():
        server.should_exit = True
        if backup_task:
            backup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await backup_task
        if health_task:
            health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await health_task
        if task:
            try:
                await asyncio.wait_for(task, timeout=5)
            except asyncio.TimeoutError:
                task.cancel()
        logger.remove(app.state.sink_id)
