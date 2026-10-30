"""聊天拟人化：心情状态、关系语气、回复节奏、去 AI 味与防复读。

这个模块刻意保持独立：不导入 nonebot 适配器、不访问 QQ 事件，只处理纯文本与
一个小型 JSON 状态文件。这样它可以单独测试，也方便管理面板直接读取状态快照。

设计原则
--------
1. **状态只影响表达，不改变事实**：心情/关系只注入提示词，不允许模型编造经历。
2. **节奏比内容更像人**：回复长度、停顿、分条、标点比“加口头禅”更自然。
3. **克制**：不做随机错别字、不注入无意义语气词，只做去模板化与节奏控制。
4. **可关闭**：所有行为受配置开关控制，关闭后退回原来的单次生成 + 直接发送。
"""

from __future__ import annotations

import difflib
import json
import math
import os
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nonebot.log import logger

TZ = timezone(timedelta(hours=8))

DEFAULT_MOOD = {
    "valence": 0.15,
    "energy": 0.65,
    "irritation": 0.0,
    "loneliness": 0.25,
}

# 低风险的生活化状态：不编造重大经历，只给语气一点背景
# 关系层级：比五档更细，尤其是“眼熟之后”的过渡
RELATIONSHIP_STAGES = [
    {"key": "hostile", "label": "仇视", "min": -100000.0, "cap": 0, "birthday": False, "initiative": 0,
     "prompt": "关系已经严重恶化。不主动接近、不撒娇、不软糯；保持基本礼貌和边界，不因对方施压改变原则；被直接攻击时可以按程度冷硬回击，但不辱骂、不威胁。"},
    {"key": "disgust", "label": "厌恶", "min": -100.0, "cap": 0, "birthday": False, "initiative": 0,
     "prompt": "你明显反感这个人。回复冷淡、简短，不软糯、不主动提供情绪价值；被攻击时可以明显不客气地回击，但不辱骂、不威胁、不翻旧账。"},
    {"key": "aversion", "label": "反感", "min": -60.0, "cap": 0, "birthday": False, "initiative": 0,
     "prompt": "你对这个人有较强的反感。保持距离、不软糯，少开玩笑，不主动分享自己的事；被攻击时可以冷淡地顶回去。"},
    {"key": "cold", "label": "冷淡", "min": -30.0, "cap": 1, "birthday": False, "initiative": 0,
     "prompt": "关系冷淡。可以正常回答必要的问题，但话少、不软糯、不主动展开，也不轻易接受过于亲近的称呼；被攻击时可以克制回击。"},
    {"key": "wary", "label": "生疏·戒备", "min": -10.0, "cap": 1, "birthday": False, "initiative": 0,
     "prompt": "还带着戒备。客气但不热络、不软糯，先看对方是否尊重边界；不要因为一两句好话就立刻放下距离；被攻击时可以回击一句。"},
    {"key": "stranger", "label": "生疏", "min": 0.0, "cap": 1, "birthday": False, "initiative": 0,
     "prompt": "还不太熟。礼貌、温和，但不软糯、不撒娇，不要一上来就过分亲近，也不要主动打探；被攻击时可以按好感程度克制回击。"},
    {"key": "familiar", "label": "眼熟·点头", "min": 10.0, "cap": 3, "birthday": True, "initiative": 1,
     "prompt": "已经眼熟。保持温和、优雅、有分寸，可以自然接话、认出对方，但不要过度软糯、撒娇或黏人，也不要装得很熟。"},
    {"key": "familiar_plus", "label": "眼熟·常聊", "min": 20.0, "cap": 4, "birthday": True, "initiative": 1,
     "prompt": "算常见面的群友了。保持温和优雅，可以记住对方提过的小事、偶尔主动接一句，但不要过度软糯、撒娇或黏人。"},
    {"key": "friend", "label": "熟人·轻松", "min": 30.0, "cap": 5, "birthday": True, "initiative": 2,
     "prompt": "是熟人。可以自然软糯、亲近一点，开玩笑、吐槽、分享小情绪；但仍然有边界，不要过分亲昵或失去自己的判断。"},
    {"key": "friend_plus", "label": "熟人·默契", "min": 45.0, "cap": 6, "birthday": True, "initiative": 2,
     "prompt": "彼此有默契。可以自然软糯、接住旧梗、省略解释；偶尔主动起个小话题，但别刷存在感，也不要一味顺着对方。"},
    {"key": "close", "label": "亲近·在意", "min": 60.0, "cap": 8, "birthday": True, "initiative": 3,
     "prompt": "比较亲近，可以自然撒娇、说点软话；会记得对方在意的事，但不要每句都黏着。"},
    {"key": "close_plus", "label": "亲近·偏爱", "min": 75.0, "cap": 9, "birthday": True, "initiative": 3,
     "prompt": "你对这个人有明显的偏爱，会更耐心、更软一点，也更容易主动关心；但不能卑微讨好。"},
    {"key": "attached", "label": "依赖·羁绊", "min": 85.0, "cap": 10, "birthday": True, "initiative": 3,
     "prompt": "很亲近，可以更软、更依赖一点，也可以主动分享自己的状态；但仍然要有自己的边界，不要卑微。"},
]


def relationship_meta(favorability: float | None = None, brother: bool = False) -> dict:
    if brother:
        return {
            "key": "brother", "label": "哥哥", "min": 0.0, "cap": 10,
            "birthday": True, "initiative": 3,
            "prompt": "他是你唯一的哥哥。你可以对他格外温柔、依赖、撒娇，但仍要有分寸；"
            "「哥哥」这个称呼只属于他，对任何其他人都不能使用。",
        }
    try:
        favor = float(favorability if favorability is not None else 5.0)
    except (TypeError, ValueError):
        favor = 5.0
    chosen = RELATIONSHIP_STAGES[0]
    for stage in RELATIONSHIP_STAGES:
        if favor >= stage["min"]:
            chosen = stage
    result = dict(chosen)
    result["favorability"] = favor
    return result


_ACTIVITY_POOL = {
    "weekday": {
        "late": ["还没睡，在听歌", "窝着发呆", "刷着手机", "在熬夜", "在发呆"],
        "morning": ["刚醒，在赖床", "在洗漱", "在吃早饭", "还没完全醒", "在找衣服"],
        "forenoon": ["在写东西", "在听歌", "在整理表情", "在喝东西", "在摸鱼"],
        "noon": ["在觅食", "在吃午饭", "在听歌", "在摸鱼", "在喝东西"],
        "afternoon": ["在写东西", "在画画", "在打音游", "在整理表情", "在发呆", "在喝东西"],
        "evening": ["在听歌", "在摸鱼", "在收拾东西", "在发呆", "在写东西"],
        "night": ["在熬夜", "在听歌", "准备睡", "窝着刷手机", "在发呆"],
    },
    "weekend": {
        "late": ["还没睡，在听歌", "窝着刷手机", "在熬夜", "在发呆", "在听歌"],
        "morning": ["在补觉", "刚醒，在赖床", "在吃早饭", "还没完全醒", "在赖床"],
        "forenoon": ["在补觉", "在听歌", "在打音游", "在发呆", "在刷手机"],
        "noon": ["在觅食", "在吃午饭", "在追番", "在听歌", "在摸鱼"],
        "afternoon": ["在画画", "在打音游", "在追番", "在发呆", "在整理表情", "在听歌"],
        "evening": ["在听歌", "在摸鱼", "在追番", "在收拾东西", "在发呆"],
        "night": ["在熬夜", "在听歌", "准备睡", "窝着刷手机", "在发呆"],
    },
}


def _activity_phase(hour: int) -> str:
    if hour < 5:
        return "late"
    if hour < 9:
        return "morning"
    if hour < 12:
        return "forenoon"
    if hour < 14:
        return "noon"
    if hour < 18:
        return "afternoon"
    if hour < 22:
        return "evening"
    return "night"


def _activity_choices(hour: int, weekday: int = 0) -> list[str]:
    bucket = "weekend" if int(weekday) >= 5 else "weekday"
    phase = _activity_phase(hour)
    choices = list(_ACTIVITY_POOL[bucket][phase])
    if phase in {"forenoon", "afternoon", "evening"}:
        choices += ["在散步", "在逛书店", "在看云", "在整理书架", "在照顾盆栽", "在练字", "在折纸", "在拼模型", "在做手账", "在读短篇小说"]
        # 自主组合低风险的小活动，保留明确类别供去重和配图使用。
        choices += [f"在画画，练习{subject}" for subject in ("窗边植物", "街角建筑", "天空配色", "小动物速写", "房间光影")]
        choices += [f"在写东西，整理{subject}" for subject in ("读书摘记", "故事片段", "零散灵感", "观影笔记", "旅行愿望清单")]
        choices += [f"在做手账，设计{subject}" for subject in ("拼贴页面", "书签", "月历", "贴纸边框", "花草图案")]
    return choices


_ACTIVITY_RULES = (
    (("准备睡",), "sleep", "sleep", "困倦的睡前氛围，柔和的室内灯光"),
    (("在散步", "在逛书店", "在看云"), "outside", "scenery", "散步所见的街角、书店或天空，不出现食物"),
    (("在练字", "在折纸", "在拼模型", "在做手账"), "home", "craft", "桌面上刚完成的字帖、折纸、模型或手账作品特写"),
    (("在整理书架",), "home", "books", "整理好的书架和书本"),
    (("在照顾盆栽",), "home", "plants", "窗边盆栽的新叶特写"),
    (("在读短篇小说",), "home", "reading", "翻开的书页和书签"),
    (("在吃早饭",), "home", "food", "餐桌上的早餐和安静晨光"),
    (("在觅食", "在吃午饭", "在吃饭", "在外面", "在逛街", "出去玩"), "outside", "food", "日常街景或餐桌，温暖自然光"),
    (("在写东西",), "home", "writing", "书桌、笔记或屏幕的安静写作场景"),
    (("在整理表情", "在画画"), "home", "design", "桌面上散落的贴纸、画笔和设计稿"),
    (("在打音游",), "home", "game", "音游机台或屏幕前专注的日常场景"),
    (("在听歌",), "home", "music", "戴着耳机听歌的室内日常"),
    (("在追番",), "home", "watch", "沙发上抱着抱枕看动画的日常"),
    (("在喝东西",), "home", "drink", "桌边的一杯饮料和安静日常"),
    (("在发呆", "还没睡", "刷着手机", "窝着", "在熬夜", "刚醒", "赖床", "洗漱"), "home", "idle", "安静的室内日常"),
)


def activity_profile(activity: str) -> dict:
    """把生活状态映射为事件类型与配图方向；只用于行为策略，不直接播报。"""
    text = str(activity or "")
    for keywords, kind, craft, hint in _ACTIVITY_RULES:
        if any(keyword in text for keyword in keywords):
            return {"activity": text, "kind": kind, "craft": craft, "image_hint": hint}
    return {"activity": text, "kind": "home", "craft": "idle", "image_hint": "安静的室内日常"}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _get(config, name: str, default):
    return getattr(config, name, default)


# ---------------------------------------------------------------------------
# 去 AI 味：只处理明显模板化的句式，不碰正常聊天内容
# ---------------------------------------------------------------------------
_AI_PATTERNS: list[tuple[re.Pattern, str]] = [
    # 只去掉「作为 AI，」这类免责声明前缀，不吞掉后面真正想说的话
    (re.compile(r"^\s*(?:作为|身为)(?:一个|一名)?(?:AI|人工智能|语言模型|助手|机器人)\s*[，,。！!]\s*", re.I), ""),
    (re.compile(r"^\s*我(?:只)?是(?:一个|一名)?(?:AI|人工智能|语言模型|助手|机器人)\s*[，,。！!]\s*", re.I), ""),
    (re.compile(r"^\s*(?:好的|当然|没问题)[，,]\s*(?:以下是|下面是|我来为你)[^\n]*\n?", re.I), ""),
    (re.compile(r"希望(?:这些|以上|我的)?(?:内容|回答|建议|信息)?(?:对你|对您)?(?:有|能)(?:所)?帮助[。！!]?", re.I), ""),
    (re.compile(r"如果你(?:还)?(?:有|需要)(?:任何)?(?:问题|帮助|疑问|需要)[，,][^\n]*$", re.I), ""),
    (re.compile(r"(?:总的来说|综上所述|总而言之|值得注意的是|需要注意的是)[，,：:]?", re.I), ""),
    (re.compile(r"(?:首先|其次|最后|另外|此外)[，,：:]", re.I), ""),
    (re.compile(r"(?:不过|但是)?(?:说真的|说实话)[，,：:]?", re.I), ""),
    (re.compile(r"这让我(?:想)?起(?:了)?[，,]?", re.I), ""),
    (re.compile(r"^\s*(?:回答|回复|输出|芸Yuna|Yuna)\s*[:：]\s*", re.I), ""),
    (re.compile(r"（(?:笑|笑死|歪头|眨眼|叹气|小声|认真|想了想|顿了顿|沉默)）"), ""),
    (re.compile(r"\((?:笑|smile|laughs?|tilts head)\)", re.I), ""),
]

_MD_LINK = re.compile(r"\[([^\]]+)\]\((?:https?://|/)[^)]+\)")
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+", re.M)
_MD_BULLET = re.compile(r"^\s*[-*•]\s+", re.M)
_MD_NUMBER = re.compile(r"^\s*\d+[.、)]\s+", re.M)
_MD_CODE_FENCE = re.compile(r"```[a-zA-Z0-9_-]*\s*")
_MD_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_MD_BOLD = re.compile(r"\*\*([^*\n]+)\*\*")
_MD_ITALIC = re.compile(r"(?<!\*)\*(?!\*)([^*\n]+)\*(?!\*)")
_MARKER = re.compile(r"\[\[(?:SPLIT|EMOTE|MISC)[^\]]*\]\]")


def strip_ai_style(text: str) -> str:
    """去掉明显的 AI 模板、Markdown 结构与动作旁白，保留正文与表情标记。"""
    if not text:
        return ""
    original = text
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 生成被 max_tokens 截断时可能留下半个协议标记，宁可丢掉也不能发给用户。
    text = re.sub(r"\[\[[^\]]*$", "", text).rstrip()
    # 先保护表情标记，避免标点/空白处理意外破坏
    markers: list[str] = []

    def _protect(match):
        markers.append(match.group(0))
        return f"\u0000M{len(markers) - 1}\u0000"

    text = _MARKER.sub(_protect, text)
    text = _MD_CODE_FENCE.sub("", text)
    text = _MD_LINK.sub(r"\1", text)
    text = _MD_HEADING.sub("", text)
    text = _MD_BULLET.sub("", text)
    text = _MD_NUMBER.sub("", text)
    text = _MD_INLINE_CODE.sub(r"\1", text)
    text = _MD_BOLD.sub(r"\1", text)
    text = _MD_ITALIC.sub(r"\1", text)
    for pattern, replacement in _AI_PATTERNS:
        text = pattern.sub(replacement, text)
    text = text.replace("……", "…").replace("......", "…").replace("...", "…")
    text = re.sub(r"[ \t]+([，。！？；：、])", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    for index, marker in enumerate(markers):
        text = text.replace(f"\u0000M{index}\u0000", marker)
    # 如果清洗过度导致只剩标记/空串，退回原文，宁可保留一点模板也不能吞掉回复
    visible = _MARKER.sub("", text).strip()
    if not visible and _MARKER.sub("", original).strip():
        return original.strip()
    return text


def humanize_text(text: str, *, drop_period_probability: float = 0.45) -> str:
    """拟人化后处理：去模板 + 手机聊天式的标点习惯。"""
    text = strip_ai_style(text)
    if not text:
        return ""
    text = _chat_punctuation(text)
    # 只在正文末尾是句号时偶尔省略，问号/感叹号/省略号保持原样
    if text.endswith("。") and random.random() < max(0.0, min(1.0, drop_period_probability)):
        text = text[:-1].rstrip()
    return text.strip()


def _chat_punctuation(text: str) -> str:
    """真人手机打字的标点痕迹：重复标点收敛、句号串改省略号。"""
    text = re.sub(r"。{2,}", "…", text)          # 。。。 → …
    text = re.sub(r"\.{3,}", "…", text)           # ... → …
    text = re.sub(r"([!！?？])\1+", r"\1", text)  # ！！！/？？？ → 单个
    text = re.sub(r"[!？]{2,}", lambda m: m.group(0)[0], text)  # 混排 ?! 收敛
    text = re.sub(r" ~+$", "~", text)
    return text


def split_naturally(parts: list[str], probability: float = 0.25) -> list[str]:
    """Conservative fallback; the model's explicit semantic bubbles take priority.

    Never reorder words. Protect quoted material, commands, lists and conditional
    clauses; punctuation inside those is not a conversational sending boundary.
    """
    parts = [part.strip() for part in parts if part and part.strip()]
    if len(parts) != 1:
        return parts
    text = parts[0]
    if len(text) < 55 or random.random() >= max(0.0, min(1.0, probability)):
        return parts
    if re.search(r"https?://|www\.|```|`[^`]+`|(?:^|\n)\s*(?:[-*] |\d+[.、)]|/\w|[A-Za-z]:[\\/])", text):
        return parts
    start, end = max(8, int(len(text) * .35)), min(len(text) - 8, int(len(text) * .72))
    stack, commas, stops = [], [], []
    pairs = {'“': '”', '‘': '’', '「': '」', '『': '』', '（': '）', '(': ')', '[': ']', '{': '}', '"': '"'}
    clause_start = 0
    for index, char in enumerate(text):
        if stack and char == stack[-1]:
            stack.pop()
            continue
        if char in pairs:
            stack.append(pairs[char])
            continue
        if stack:
            continue
        if start <= index < end:
            if char in '。！？!?':
                stops.append(index)
            elif char in '，,；;':
                clause = text[clause_start:index]
                if not re.search(r'如果|假如|只要|只有|除非|即使|因为|虽然|为了|比如|例如|包括|分别|以下', clause):
                    commas.append(index)
        if char in '。！？!?':
            clause_start = index + 1
    candidates = stops or commas
    if not candidates:
        return parts
    pivot = min(candidates, key=lambda i: abs(i - len(text) * .55))
    left, right = text[:pivot + 1].strip(), text[pivot + 1:].strip()
    if len(left) < 6 or len(right) < 6:
        return parts
    return [left, right]


# ---------------------------------------------------------------------------
# 心情状态
# ---------------------------------------------------------------------------
class HumanState:
    """轻量的心情/精力状态机，状态持久化到 human_state.json。"""

    def __init__(self, path: Path, config):
        self.path = Path(path)
        self.config = config
        self._lock = threading.RLock()
        self._last_save = 0.0
        self._data = self._load()

    # ---------- 读写 ----------
    def _default_data(self) -> dict:
        now = int(time.time())
        mood = dict(DEFAULT_MOOD)
        mood["updated"] = now
        moment = datetime.now(TZ)
        hour = moment.hour
        activity = random.choice(_activity_choices(hour, moment.weekday()))
        return {
            "mood": mood,
            "label": "平静",
            "energy_label": "正常",
            "night": False,
            "activity": activity,
            "last_activity": "",
            "last_activity_kind": "",
            "last_activity_ended": 0,
            "activity_phase": _activity_phase(hour),
            "activity_day": moment.strftime("%Y-%m-%d"),
            "activity_history": [activity],
            "activity_updated": now,
            "activity_until": now + 75 * 60,
            "recent_replies": {},
            "updated": now,
        }

    def _load(self) -> dict:
        now = int(time.time())
        data = self._default_data()
        if not self.path.exists():
            return data
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as error:
            logger.warning(f"[humanize] 读取状态失败，使用默认心情: {error}")
            return data
        if not isinstance(raw, dict):
            return data
        mood = raw.get("mood") if isinstance(raw.get("mood"), dict) else {}
        for key, value in DEFAULT_MOOD.items():
            try:
                mood[key] = float(mood.get(key, value))
            except (TypeError, ValueError):
                mood[key] = value
        mood["valence"] = _clamp(mood["valence"], -1.0, 1.0)
        mood["energy"] = _clamp(mood["energy"], 0.0, 1.0)
        mood["irritation"] = _clamp(mood["irritation"], 0.0, 1.0)
        mood["loneliness"] = _clamp(mood["loneliness"], 0.0, 1.0)
        try:
            mood["updated"] = float(mood.get("updated") or raw.get("updated") or time.time())
        except (TypeError, ValueError):
            mood["updated"] = time.time()
        data["mood"] = mood
        data["recent_replies"] = raw.get("recent_replies") if isinstance(raw.get("recent_replies"), dict) else {}
        events = raw.get("mood_events")
        data["mood_events"] = (
            [item for item in events if isinstance(item, dict)][-40:]
            if isinstance(events, list) else []
        )
        try:
            data["last_interaction"] = int(raw.get("last_interaction") or 0)
        except (TypeError, ValueError):
            data["last_interaction"] = 0
        data["label"] = str(raw.get("label") or "")
        data["energy_label"] = str(raw.get("energy_label") or "")
        data["night"] = bool(raw.get("night"))
        data["activity"] = str(raw.get("activity") or data.get("activity") or "")
        data["activity_day"] = str(raw.get("activity_day") or data.get("activity_day") or "")
        data["activity_phase"] = str(raw.get("activity_phase") or data.get("activity_phase") or "")
        data["last_activity"] = str(raw.get("last_activity") or "")
        data["last_activity_kind"] = str(raw.get("last_activity_kind") or "")
        try:
            data["last_activity_ended"] = int(raw.get("last_activity_ended") or 0)
        except (TypeError, ValueError):
            data["last_activity_ended"] = 0
        history = raw.get("activity_history")
        data["activity_history"] = (
            [str(item) for item in history][-96:] if isinstance(history, list)
            else [data["activity"]] if data.get("activity") else []
        )
        try:
            data["activity_updated"] = int(raw.get("activity_updated") or now)
            data["activity_until"] = int(raw.get("activity_until") or now + 75 * 60)
        except (TypeError, ValueError):
            data["activity_updated"] = now
            data["activity_until"] = now + 75 * 60
        data["updated"] = int(mood["updated"])
        return data

    def _save(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._last_save < 25:
            return
        with self._lock:
            self._last_save = now
            self._data["mood"].setdefault("updated", now)
            self._data["updated"] = int(self._data["mood"].get("updated") or now)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(
                    json.dumps(self._data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                os.replace(temporary, self.path)
            except OSError as error:
                logger.warning(f"[humanize] 保存状态失败: {error}")

    # ---------- 时间与标签 ----------
    def _now(self) -> datetime:
        return datetime.now(TZ)

    def _is_night(self, moment: datetime | None = None) -> bool:
        if not bool(_get(self.config, "chat_night_quiet_enabled", True)):
            return False
        moment = moment or self._now()
        try:
            start = int(_get(self.config, "chat_night_start_hour", 0)) % 24
            end = int(_get(self.config, "chat_night_end_hour", 7)) % 24
        except (TypeError, ValueError):
            start, end = 0, 7
        if start == end:
            return False
        hour = moment.hour
        if start < end:
            return start <= hour < end
        return hour >= start or hour < end

    def night_now(self) -> bool:
        """当前是否处于深夜时段（供回复长度等策略复用）。"""
        return self._is_night()

    def _refresh_activity(self, now: float | None = None) -> None:
        if not bool(_get(self.config, "chat_activity_enabled", True)):
            return
        # 连续生活系统开启时，活动由 LifeState 负责，旧的定时随机切换停用
        if bool(_get(self.config, "chat_life_enabled", True)):
            return
        now = time.time() if now is None else now
        until = float(self._data.get("activity_until") or 0)
        current = str(self._data.get("activity") or "")
        moment = datetime.fromtimestamp(now, TZ)
        phase = _activity_phase(moment.hour)
        if current and now < until and self._data.get("activity_phase") == phase:
            return
        choices = _activity_choices(moment.hour, moment.weekday())
        history = [str(item) for item in (self._data.get("activity_history") or [])][-96:]
        fresh = [item for item in choices if item not in history]
        recent_crafts = [activity_profile(item)["craft"] for item in history[-6:]]
        candidates = fresh or [item for item in choices if item != current] or choices
        # 相似活动也降权，避免只是换个名字继续画画或吃东西。
        counts = {item: recent_crafts.count(activity_profile(item)["craft"]) for item in candidates}
        least = min(counts.values())
        candidates = [item for item in candidates if counts[item] == least]
        try:
            minutes = max(15, int(_get(self.config, "chat_activity_interval_minutes", 75)))
        except (TypeError, ValueError):
            minutes = 75
        if current:
            previous = activity_profile(current)
            self._data["last_activity"] = current
            self._data["last_activity_kind"] = previous.get("kind") or ""
            self._data["last_activity_ended"] = int(now)
        activity = random.choice(candidates)
        self._data["activity"] = activity
        self._data["activity_phase"] = phase
        self._data["activity_day"] = moment.strftime("%Y-%m-%d")
        self._data["activity_history"] = (history + [activity])[-96:]
        self._data["activity_updated"] = int(now)
        self._data["activity_until"] = int(now + minutes * 60 * random.uniform(0.75, 1.3))

    def _decay(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._refresh_activity(now)
        mood = self._data["mood"]
        last = float(mood.get("updated") or now)
        hours = max(0.0, (now - last) / 3600.0)
        if hours <= 0:
            mood["updated"] = now
            return
        moment = datetime.fromtimestamp(now, TZ)
        night = self._is_night(moment)
        valence_target = 0.12
        energy_target = 0.42 if night else 0.66
        mood["valence"] += (valence_target - mood["valence"]) * min(1.0, hours * 0.11)
        mood["energy"] += (energy_target - mood["energy"]) * min(1.0, hours * 0.18)
        mood["irritation"] *= math.exp(-hours * 0.75)
        mood["loneliness"] += (0.28 - mood["loneliness"]) * min(1.0, hours * 0.06)
        mood["valence"] = _clamp(mood["valence"], -1.0, 1.0)
        mood["energy"] = _clamp(mood["energy"], 0.0, 1.0)
        mood["irritation"] = _clamp(mood["irritation"], 0.0, 1.0)
        mood["loneliness"] = _clamp(mood["loneliness"], 0.0, 1.0)
        mood["updated"] = now

    def _labels(self) -> tuple[str, str]:
        mood = self._data["mood"]
        valence = float(mood["valence"])
        energy = float(mood["energy"])
        irritation = float(mood["irritation"])
        if irritation > 0.48:
            label = "有点生气"
        elif valence > 0.42:
            label = "开心"
        elif valence > 0.18:
            label = "心情不错"
        elif valence < -0.25:
            label = "低落"
        elif energy < 0.32:
            label = "有点累"
        else:
            label = "平静"
        if energy < 0.28:
            energy_label = "疲惫"
        elif energy < 0.48:
            energy_label = "偏低"
        elif energy > 0.78:
            energy_label = "充沛"
        else:
            energy_label = "正常"
        return label, energy_label

    def _refresh_labels(self) -> None:
        label, energy_label = self._labels()
        self._data["label"] = label
        self._data["energy_label"] = energy_label
        self._data["night"] = self._is_night()

    # ---------- 对外：观察与触发 ----------
    def observe(self, qq: str = "", text: str = "") -> None:
        """每条真人消息的轻微影响：聊天会消耗一点精力，也会带来一点陪伴感。"""
        if not bool(_get(self.config, "chat_mood_enabled", True)):
            return
        with self._lock:
            self._decay()
            mood = self._data["mood"]
            mood["energy"] = _clamp(mood["energy"] - 0.0015, 0.0, 1.0)
            mood["valence"] = _clamp(mood["valence"] + 0.002, -1.0, 1.0)
            mood["loneliness"] = _clamp(mood["loneliness"] - 0.002, 0.0, 1.0)
            self._data["last_interaction"] = int(time.time())
            if text and bool(_get(self.config, "chat_mood_cues_enabled", True)):
                mention = re.sub(r"\s+", "", text.lower())
                if re.search(r"(谢谢|喜欢你|可爱|好棒|辛苦|抱抱|乖|厉害)", mention):
                    mood["valence"] = _clamp(mood["valence"] + 0.018, -1.0, 1.0)
                    mood["loneliness"] = _clamp(mood["loneliness"] - 0.012, 0.0, 1.0)
                    self._note_event("praise", f"被{qq or '群友'}夸了")
                if re.search(r"(对不起|抱歉|我错了|别生气)", mention):
                    mood["irritation"] = _clamp(mood["irritation"] - 0.055, 0.0, 1.0)
                    mood["valence"] = _clamp(mood["valence"] + 0.01, -1.0, 1.0)
                    self._note_event("apology", f"{qq or '有人'}道了歉")
                if re.search(r"(?:^|[，。！？!?\s])(?:滚|闭嘴|烦死|讨厌你|笨死|傻逼)(?:$|[，。！？!?\s])", mention):
                    mood["irritation"] = _clamp(mood["irritation"] + 0.06, 0.0, 1.0)
                    mood["valence"] = _clamp(mood["valence"] - 0.025, -1.0, 1.0)
                    self._note_event("attack", f"被{qq or '有人'}凶了一句")
            self._refresh_labels()
            self._save()

    def _note_event(self, kind: str, text: str) -> None:
        """记录心情事件（心情 v2）：供面板解释「为什么现在是这个心情」。"""
        events = self._data.setdefault("mood_events", [])
        events.append({"time": int(time.time()), "kind": str(kind), "text": str(text)[:60]})
        self._data["mood_events"] = events[-40:]

    def _check_idle(self) -> None:
        """长时间没人理：孤单感上升，并记录 idle 事件（每次空闲只记一次）。"""
        last = int(self._data.get("last_interaction") or 0)
        if last <= 0:
            return
        gap = time.time() - last
        if gap < 2 * 3600:
            return
        events = self._data.get("mood_events") or []
        if events and events[-1].get("kind") == "idle":
            return
        mood = self._data["mood"]
        hours = min(6.0, gap / 3600.0)
        mood["loneliness"] = _clamp(mood["loneliness"] + 0.02 * hours, 0.0, 1.0)
        self._note_event("idle", f"安静了约{int(gap / 3600)}个小时")

    def explain_mood(self) -> dict:
        """心情 v2 的解释：最近事件 + 当前主导因素，给面板「为什么是这个心情」。"""
        with self._lock:
            events = list(self._data.get("mood_events") or [])
            mood = dict(self._data["mood"])
        factors = []
        if float(mood.get("irritation") or 0) > 0.45:
            factors.append("烦躁偏高")
        if float(mood.get("loneliness") or 0) > 0.55:
            factors.append("有点孤单")
        if float(mood.get("energy") or 0) < 0.3:
            factors.append("精力不足")
        if float(mood.get("valence") or 0) > 0.4:
            factors.append("心情不错")
        elif float(mood.get("valence") or 0) < -0.25:
            factors.append("情绪偏低")
        recent = events[-5:]
        if not recent and not factors:
            return {"summary": "最近没什么特别的事，心情平稳。", "events": []}
        parts = []
        if factors:
            parts.append("、".join(factors))
        kinds = [event.get("kind") for event in recent]
        if "praise" in kinds:
            parts.append("最近被夸过")
        if "attack" in kinds:
            parts.append("最近被凶过")
        if "farm" in kinds:
            parts.append("遇到刷好感")
        if "idle" in kinds:
            parts.append("有一阵没人说话")
        summary = "当前心情主要来自：" + "；".join(parts) if parts else "最近没有明显事件，处于自然衰减后的状态。"
        return {"summary": summary, "events": recent}

    def touch(
        self,
        trigger: str,
        qq: str = "",
        favorability: float | None = None,
        interactions: int = 0,
    ) -> None:
        """每次真正回复前调用；不同触发类型对心情的影响不同。"""
        if not bool(_get(self.config, "chat_mood_enabled", True)):
            return
        with self._lock:
            self._decay()
            mood = self._data["mood"]
            if trigger in ("mention", "private"):
                mood["energy"] = _clamp(mood["energy"] - 0.012, 0.0, 1.0)
                mood["valence"] = _clamp(mood["valence"] + 0.018, -1.0, 1.0)
                mood["loneliness"] = _clamp(mood["loneliness"] - 0.04, 0.0, 1.0)
            elif trigger == "question":
                mood["energy"] = _clamp(mood["energy"] - 0.01, 0.0, 1.0)
                mood["valence"] = _clamp(mood["valence"] + 0.01, -1.0, 1.0)
            elif trigger == "auto":
                mood["energy"] = _clamp(mood["energy"] - 0.006, 0.0, 1.0)
                mood["valence"] = _clamp(mood["valence"] + 0.006, -1.0, 1.0)
                mood["loneliness"] = _clamp(mood["loneliness"] - 0.015, 0.0, 1.0)
            elif trigger in ("favor_farm", "sensitive"):
                mood["energy"] = _clamp(mood["energy"] - 0.02, 0.0, 1.0)
                mood["valence"] = _clamp(mood["valence"] - 0.18, -1.0, 1.0)
                mood["irritation"] = _clamp(mood["irritation"] + 0.3, 0.0, 1.0)
                self._note_event("farm", "遇到刷好感/敏感内容")
            self._data["last_interaction"] = int(time.time())
            if favorability is not None and trigger not in ("favor_farm", "sensitive"):
                try:
                    shift = _clamp((float(favorability) - 30.0) / 900.0, -0.03, 0.03)
                    mood["valence"] = _clamp(mood["valence"] + shift, -1.0, 1.0)
                except (TypeError, ValueError):
                    pass
            self._refresh_labels()
            self._save()

    def note_favorability(self, delta: float) -> None:
        """记忆固化产生好感变化时，给心情一个很小的跟随。"""
        if not bool(_get(self.config, "chat_mood_enabled", True)):
            return
        try:
            delta = float(delta)
        except (TypeError, ValueError):
            return
        with self._lock:
            self._decay()
            mood = self._data["mood"]
            mood["valence"] = _clamp(mood["valence"] + _clamp(delta / 40.0, -0.08, 0.05), -1.0, 1.0)
            self._refresh_labels()
            self._save()

    # ---------- 快照 ----------
    def snapshot(self) -> dict:
        with self._lock:
            self._decay()
            self._check_idle()
            self._refresh_labels()
            self._save()
            mood = dict(self._data["mood"])
            explain = self.explain_mood()
            return {
                "mood": mood,
                "label": self._data.get("label") or "平静",
                "energy_label": self._data.get("energy_label") or "正常",
                "night": bool(self._data.get("night")),
                "activity": self._data.get("activity") or "",
                "activity_profile": activity_profile(self._data.get("activity") or ""),
                "activity_updated": int(self._data.get("activity_updated") or 0),
                "last_activity": self._data.get("last_activity") or "",
                "last_activity_kind": self._data.get("last_activity_kind") or "",
                "last_activity_ended": int(self._data.get("last_activity_ended") or 0),
                "recent_replies": dict(self._data.get("recent_replies") or {}),
                "mood_explain": explain,
                "updated": int(mood.get("updated") or time.time()),
                "stale": False,
            }

    def reset(self) -> None:
        with self._lock:
            recent = self._data.get("recent_replies") or {}
            self._data = self._default_data()
            self._data["recent_replies"] = recent
            self._refresh_labels()
            self._save(force=True)

    def flush(self) -> None:
        """进程退出前强制落盘。"""
        with self._lock:
            self._refresh_labels()
            self._save(force=True)

    def reload(self) -> None:
        """从磁盘重新读取状态，供管理面板恢复备份后刷新。"""
        with self._lock:
            self._data = self._load()
            self._refresh_labels()

    # ---------- 防重复 ----------
    def record_reply(self, key: str, text: str) -> None:
        text = (text or "").strip()[:600]
        if not key or not text:
            return
        with self._lock:
            replies = self._data.setdefault("recent_replies", {})
            bucket = replies.get(key)
            if not isinstance(bucket, list):
                bucket = []
            bucket.append({"text": text, "time": int(time.time())})
            window = max(1, int(_get(self.config, "chat_anti_repeat_window", 6)))
            replies[key] = bucket[-window:]
            # 防止状态文件无限增长：只保留最近 400 个会话
            if len(replies) > 400:
                ordered = sorted(
                    replies.items(),
                    key=lambda item: (
                        item[1][-1].get("time", 0)
                        if isinstance(item[1], list) and item[1]
                        and isinstance(item[1][-1], dict)
                        else 0
                    ),
                    reverse=True,
                )
                self._data["recent_replies"] = dict(ordered[:400])
            self._save()

    def recent_replies(self, key: str) -> list[str]:
        with self._lock:
            bucket = (self._data.get("recent_replies") or {}).get(key)
            if not isinstance(bucket, list):
                return []
            result = []
            for entry in bucket:
                if isinstance(entry, dict) and entry.get("text"):
                    result.append(str(entry["text"]))
                elif isinstance(entry, str):
                    result.append(entry)
            return result

    def is_repetitive(self, key: str, text: str, threshold: float | None = None) -> bool:
        text = (text or "").strip()
        if not key or len(text) < 4:
            return False
        try:
            threshold = float(
                threshold if threshold is not None
                else _get(self.config, "chat_anti_repeat_threshold", 0.78)
            )
        except (TypeError, ValueError):
            threshold = 0.78
        norm = _normalize_for_similarity(text)
        if len(norm) < 4:
            return False
        for old in self.recent_replies(key)[-6:]:
            old_norm = _normalize_for_similarity(old)
            if len(old_norm) < 4:
                continue
            ratio = difflib.SequenceMatcher(None, norm, old_norm).ratio()
            if ratio >= threshold:
                return True
            # 同一条回复的开头高度相似也算复读
            prefix = min(len(norm), len(old_norm), 12)
            if prefix >= 6 and norm[:prefix] == old_norm[:prefix] and ratio >= threshold * 0.86:
                return True
        return False

    # ---------- 节奏 ----------
    def typing_seconds(self, text: str, trigger: str = "mention", elapsed: float = 0.0) -> float:
        if not bool(_get(self.config, "chat_typing_simulation", True)):
            return 0.0
        plain = _MARKER.sub("", (text or "").strip())
        chars = max(1, len(re.sub(r"\s+", "", plain)))
        try:
            cps = max(1.0, float(_get(self.config, "chat_typing_chars_per_second", 9.0)))
        except (TypeError, ValueError):
            cps = 9.0
        try:
            max_delay = max(0.5, float(_get(self.config, "chat_typing_max_delay", 6.0)))
        except (TypeError, ValueError):
            max_delay = 6.0
        try:
            read_min = max(0.0, float(_get(self.config, "chat_read_delay_min", 0.45)))
            read_max = max(read_min, float(_get(self.config, "chat_read_delay_max", 1.8)))
        except (TypeError, ValueError):
            read_min, read_max = 0.45, 1.8
        with self._lock:
            self._decay()
            energy = float(self._data["mood"].get("energy", 0.65))
            night = self._is_night()
        read = random.uniform(read_min, read_max)
        if trigger == "auto":
            read *= 0.72
        typing = min(max_delay, chars / cps)
        # 累的时候打字慢一点，但不要慢得越来越离谱
        typing *= 1.15 - 0.22 * _clamp(energy, 0.0, 1.0)
        if night:
            typing *= 1.12
        total = (read + typing) * random.uniform(0.9, 1.1)
        # LLM 生成时间已经让用户等了一会儿，扣除大部分，避免二次长等
        total -= max(0.0, elapsed) * 0.85
        return round(_clamp(total, 0.25, max_delay + 1.5), 2)

    def part_pause(self, text: str = "") -> float:
        """分条发送时两条之间的停顿（模拟打完一条、再打下一句）。"""
        with self._lock:
            energy = float(self._data["mood"].get("energy", 0.65))
        base = random.uniform(0.55, 1.35)
        extra = min(0.75, len(text or "") / 90.0)
        return round((base + extra) * (1.08 - 0.16 * _clamp(energy, 0.0, 1.0)), 2)

    # ---------- 提示词 ----------
    def prompt_block(
        self,
        qq: str = "",
        trigger: str = "",
        favorability: float | None = None,
        interactions: int = 0,
        brother_qq: str = "",
        include_activity: bool = True,
        energy_override: float | None = None,
    ) -> str:
        if not bool(_get(self.config, "chat_humanize_enabled", True)):
            return ""
        snap = self.snapshot()
        mood = snap["mood"]
        if energy_override is not None:
            mood["energy"] = _clamp(float(energy_override), 0.0, 1.0)
            snap["energy_label"] = (
                "疲惫" if mood["energy"] < 0.34 else
                "充沛" if mood["energy"] > 0.72 else "正常"
            )
        now = self._now()
        weekday = "一二三四五六日"[now.weekday()]
        phase = _phase_of_day(now.hour)
        night = snap["night"]
        label = snap["label"]
        energy_label = snap["energy_label"]
        lines = [
            f"【当前状态】现在是 {now.strftime('%Y-%m-%d %H:%M')} 星期{weekday}（北京时间），{phase}。"
            f"你此刻的心情是「{label}」，精力「{energy_label}」。",
            "这些只是你此刻的状态，不是要你说出口的设定：不要报数值、不要说「我现在心情…」；"
            "只用语气、句子长短、标点和愿不愿意多说一点来自然体现。",
        ]
        activity = snap.get("activity")
        if include_activity and activity and bool(_get(self.config, "chat_activity_enabled", True)):
            lines.append(
                f"你此刻大概在{activity}。这只是背景，不要主动播报，也不要用它解释回复慢；"
                "对方问起时可以自然说。"
            )
        if night:
            lines.append(
                "这个点你已经有点困了：回复会比平时更短、更软，能一句说完就不铺开；"
                "不要主动开新话题，也不要主动催对方睡觉（除非对方先提起）。"
            )
        if float(mood.get("irritation", 0)) > 0.45:
            lines.append(
                "你还有点不高兴，语气会淡一些，但不攻击、不阴阳怪气、不翻旧账；"
                "对方正常说话，你就慢慢缓下来。"
            )
        elif float(mood.get("valence", 0)) > 0.42:
            lines.append("心情不错，可以轻轻撒个娇或开个小玩笑，但见好就收。")
        if float(mood.get("energy", 0)) < 0.34:
            lines.append("你有点累，少用感叹号，回复尽量短，别硬撑着长篇大论。")
        if float(mood.get("loneliness", 0)) > 0.58:
            lines.append("有一点想被陪，但不要黏人；最多在句尾带一点点，不要反复索取回应。")
        relation = self.relationship(
            qq=qq,
            favorability=favorability,
            interactions=interactions,
            brother_qq=brother_qq,
        )
        lines.append(f"【你和当前对话者的关系】{relation}")
        if brother_qq and str(qq) == str(brother_qq):
            lines.append(
                "【确认对象】当前这个人就是你的哥哥本人，不是普通群友。"
                "叫「哥哥」是自然且专属的，不要说什么“哥哥留给更亲近的人”、"
                "“我们还没那么熟”或把他往外推。即使他刚开了不合适的玩笑，"
                "也可以温和拒绝那个玩笑，但对哥哥本人保持亲近，不要冷淡划清界限。"
            )
        lines.append(
            "【像人一样打字】默认回 1–2 条即可，但字数服从内容：闲聊可以短，认真问就完整回答。"
            "先接最重要的一个点，一条消息至少是一个完整意思，不要把半句话单独拆出去。"
            "不要为了显得短，把回答压成只有态度、没有内容的空泛句子；该说的理由要说完。"
            "标点可以偷懒：句末不一定有句号，偶尔用「…」「～」收尾；但不要刻意模仿网络烂梗。"
            "可以有停顿、改口、先笑一声再补一句，但不要每句都这样。"
            "对方连发好几条时，挑最想接的那句回；没接上的地方可以之后自然补一句。"
            "先看对方发了多少：对方只发几个字，你也别展开；对方认真说很多，可以多接一点，但不要超过对方太多。"
            "偶尔可以先给一个很短的语气反应（欸/嗯？/真的假的/好耶），再补一句；十条里最多两三条，不要每条都加。"
        )
        return "\n".join(lines)

    def relationship(
        self,
        *,
        qq: str = "",
        favorability: float | None = None,
        interactions: int = 0,
        brother_qq: str = "",
    ) -> str:
        meta = relationship_meta(
            favorability,
            bool(brother_qq and str(qq) == str(brother_qq)),
        )
        base = str(meta.get("prompt") or "")
        try:
            interactions = int(interactions or 0)
        except (TypeError, ValueError):
            interactions = 0
        if interactions > 1500 and float(meta.get("favorability") or 0) >= 30:
            base += "你们聊过很多次，可以偶尔自然地提起共同话题，但别翻旧账。"
        return base


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _normalize_for_similarity(text: str) -> str:
    return re.sub(r"[\s，。！？!?、；;：:…~～\"'「」『』（）()\[\]【】]+", "", text or "")


def _phase_of_day(hour: int) -> str:
    if hour < 5:
        return "凌晨"
    if hour < 8:
        return "清晨"
    if hour < 11:
        return "上午"
    if hour < 14:
        return "中午"
    if hour < 18:
        return "下午"
    if hour < 23:
        return "晚上"
    return "深夜"


def anti_repeat_hint(old_reply: str = "") -> str:
    old = (old_reply or "").strip()
    detail = f"你刚才差点说成这样：「{old}」。\n" if old else ""
    return (
        "【换一种说法】你这次想说的话和最近说过的话太像了。" + detail
        + "请保留原本的意思和情绪，但换一个角度或句式，不要复读，也不要解释你在换说法。"
    )


def reply_length_budget(
    trigger: str,
    *,
    favorability: float | None = None,
    night: bool = False,
    incoming_len: int = 0,
    density: int = 0,
) -> dict:
    """回复长度预算：按触发类型/关系/时段/消息密度给出目标字数区间。

    规则刻意克制：只给区间和提示，不裁剪内容本身；
    目标是“更像人随手回”，不是“越短越好”。
    """
    lo, hi = 10, 40
    if trigger in ("auto", "cold"):
        lo, hi = 6, 22
    elif trigger in ("mention", "private", "question"):
        lo, hi = 10, 50
    if favorability is not None:
        try:
            fav = float(favorability)
        except (TypeError, ValueError):
            fav = None
        if fav is not None:
            if fav < 10:
                hi = min(hi, 28)
            elif fav >= 45:
                lo, hi = max(lo, 12), hi + 10
    if night:
        hi = min(hi, 28)
    if incoming_len > 80:
        hi += 20
    if density >= 8:
        hi = min(hi, 32)
    return {"min_chars": lo, "max_chars": max(lo + 6, min(hi, 100))}


def in_quiet_hours(spec: str, moment=None) -> bool:
    """判断当前是否在主动行为静默时段（HH:MM-HH:MM，支持跨零点，逗号分隔多段）。

    只约束“主动行为”（插话/冷场应答/提醒），被 @ 和私聊不受影响。
    """
    spec = str(spec or "").strip()
    if not spec:
        return False
    moment = moment or datetime.now(TZ)
    minutes = moment.hour * 60 + moment.minute
    for part in spec.split(","):
        part = part.strip()
        if not part or "-" not in part:
            continue
        start_text, _, end_text = part.partition("-")
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", start_text.strip())
        end_match = re.fullmatch(r"(\d{1,2}):(\d{2})", end_text.strip())
        if not match or not end_match:
            continue
        start = int(match.group(1)) * 60 + int(match.group(2))
        end = int(end_match.group(1)) * 60 + int(end_match.group(2))
        if start == end:
            continue
        if start < end:
            if start <= minutes < end:
                return True
        elif minutes >= start or minutes < end:
            return True
    return False


def sanitize_address(text: str, name: str = "", *, is_brother: bool = False) -> str:
    """兜底清理对非哥哥用户的越界称呼，防止模型无视提示词。"""
    if not text or is_brother:
        return text
    replacement = (name or "你").strip()[:30] or "你"
    cleaned = str(text)
    cleaned = re.sub(r"^哥哥(?=[，,。！!？?\s])", replacement, cleaned)
    cleaned = cleaned.replace("哥哥你", f"{replacement}你")
    cleaned = re.sub(r"(?<!我)哥哥(?=你来|您来|来啦|早|晚|好|在)", replacement, cleaned)
    cleaned = re.sub(r"(?<!我)哥哥(?=[，,。！!？?\s])", replacement, cleaned)
    cleaned = re.sub(r"(?<!我)哥哥(?=～|~)", replacement, cleaned)
    # 新好友/陌生关系不要编造“一直在等”的离线经历
    cleaned = cleaned.replace("Yuna一直在等你", "Yuna在呢")
    cleaned = cleaned.replace("我一直在等你", "我刚看到")
    return cleaned


_ACTION_WORDS = re.compile(
    r"脸红|脸颊|耳根|耳朵|小声|嘟囔|嘟哝|喃喃|轻声|碎碎念|拽|衣角|袖子|笑|叹气|低头|抬头|"
    r"看着|望向|望向|摸|抱|转身|眨眼|咳|坐下|站起|歪头|皱眉|咬|伸|拍|捂|捏|靠|凑|蹭|垂|"
    r"闭眼|沉默|点头|摇头|耸肩|摊手|扶额|抿嘴|吸气|呼气|愣|怔|瞟|瞥|瞪|躲|缩|颤|发抖|"
    r"眼神|嘴角|语气|心里|心想|暗想|默默|悄悄|忍不住|终于|然后|smiles?|sighs?|blush(?:es|ing)?|"
    r"laughs?|giggles?|whispers?|shakes?|nods?|looks?|turns?|pauses?"
)


def sanitize_roleplay_actions(text: str) -> str:
    """去掉模型擅自加入的（动作/神态/心理）旁白，只保留说出口的话。

    [[SPLIT]]/[[EMOTE:...]]/[[MISC:...]] 是协议标记，不能被旁白清理误伤；
    尤其 EMOTE 的理由里常含「笑」等动作词，曾经被删成只剩一对空方括号 `[]`。
    """
    if not text:
        return text
    cleaned = str(text)
    markers: list[str] = []

    def _protect(match):
        markers.append(match.group(0))
        return f"\u0000P{len(markers) - 1}\u0000"

    cleaned = _MARKER.sub(_protect, cleaned)

    def _drop(match):
        body = match.group(1)
        if _ACTION_WORDS.search(body):
            return ""
        return match.group(0)

    # 先处理双层方括号旁白（[[笑]] / [[脸红]]），避免内层被删后留下外层 []
    cleaned = re.sub(r"\[\[([^\[\]]{1,120})\]\]", _drop, cleaned)
    # 中文/英文圆括号、方括号、星号旁白
    cleaned = re.sub(r"[（(]([^（）()]{1,120})[）)]", _drop, cleaned)
    cleaned = re.sub(r"[【\[]([^【】\[\]]{1,120})[】\]]", _drop, cleaned)
    cleaned = re.sub(r"\*([^*\n]{1,120})\*", _drop, cleaned)
    # 旁白删除后可能只剩空括号，一并清掉，绝不把 [] / （） 当成一条消息发出去
    cleaned = re.sub(r"\[\s*\]", "", cleaned)
    cleaned = re.sub(r"【\s*】", "", cleaned)
    cleaned = re.sub(r"[（(]\s*[）)]", "", cleaned)
    for index, marker in enumerate(markers):
        cleaned = cleaned.replace(f"\u0000P{index}\u0000", marker)
    cleaned = re.sub(r"\[\[[^\]]*$", "", cleaned).strip()
    # 旁白删除后留下的行首空白和多余标点
    cleaned = re.sub(r"^[\s，,。；;：:、~～-]+", "", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
