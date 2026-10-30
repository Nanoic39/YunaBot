"""用户画像（pigimage）插件。

结合 chat 插件的记忆数据与 LLM 配置：
1. 从 chat 的记忆文件中收集目标用户最近的发言记录（时间、昵称、内容）；
2. 通过大模型对发言进行 6 维度画像分析（元数据 / 个性摘要 / 核心特质 / 兴趣领域 / 沟通风格 / 词云）；
3. 将结果渲染成一张精美卡片图片发送。
"""

import json
import math
import re
import time
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

import httpx
from nonebot import get_loaded_plugins, get_plugin_config, on_command
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.matcher import Matcher
from nonebot.plugin import PluginMetadata

from PIL import Image, ImageDraw, ImageFont

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="用户画像",
    description="结合群聊记录，用 AI 生成目标用户的 6 维度用户画像卡片",
    usage="发送 /画像 生成自己的画像，或 /画像 <QQ号> 生成指定用户画像",
)

_cfg = get_plugin_config(Config)

PLUGIN_DIR = Path(__file__).resolve().parent
CHAT_MEMORY_DIR = PLUGIN_DIR.parent / "chat" / "memory"
USERS_JSON = PLUGIN_DIR.parent / "chat" / "users.json"

_cmd = on_command("画像", aliases={"用户画像", "profile"}, priority=5, block=True)

# 缓存：qq -> (数据指纹, 图片 bytes, 过期时间戳)
_cache: dict[str, tuple[str, bytes, float]] = {}

# ---------------------------------------------------------------------------
# 数据收集
# ---------------------------------------------------------------------------


def _nickname_of(qq: str) -> str:
    """从 chat 插件的 users.json 中查找昵称。

    users.json 有两个可能存在的历史结构：
    - {qq: {"nickname": ...}}（当前）
    - {"users": [{qq, nickname}]}
    """
    try:
        data = json.loads(USERS_JSON.read_text(encoding="utf-8"))
    except Exception:
        return qq
    try:
        if isinstance(data, dict):
            users = data.get("users", data)
            if isinstance(users, dict):
                u = users.get(str(qq))
                if isinstance(u, dict):
                    return str(u.get("nickname") or u.get("name") or qq)
            elif isinstance(users, list):
                for u in users:
                    if isinstance(u, dict) and str(u.get("qq")) == str(qq):
                        return str(u.get("nickname") or u.get("name") or qq)
        elif isinstance(data, list):
            for u in data:
                if isinstance(u, dict) and str(u.get("qq")) == str(qq):
                    return str(u.get("nickname") or u.get("name") or qq)
    except Exception:
        pass
    return qq


def _to_ts(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0


def _collect_records(qq: str) -> tuple[list[dict], list[dict]]:
    """收集目标用户的画像数据。

    返回 (records, background)：
    - records：pending 里的原始发言（用于 evidence 事实依据）
    - background：episodes/facts 里该用户的固化历史（用于综合画像）
    """
    records: list[dict] = []
    background: list[dict] = []
    cutoff = time.time() - _cfg.pigimage_days * 86400
    qq_pat = re.compile(rf"(?<!\d){qq}(?!\d)")
    if CHAT_MEMORY_DIR.is_dir():
        for kind in ("group", "user"):
            d = CHAT_MEMORY_DIR / kind
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.json")):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    continue
                # 原始发言（pending）
                for turn in data.get("pending", []) or []:
                    if not isinstance(turn, dict):
                        continue
                    if str(turn.get("qq")) != qq:
                        continue
                    if turn.get("role") != "user":
                        continue
                    if turn.get("recalled"):
                        continue
                    content = str(turn.get("content") or "").strip()
                    if not content or content.startswith(("/", "!", "！")):
                        continue
                    ts = _to_ts(turn.get("time"))
                    if ts and ts < cutoff:
                        continue
                    records.append(
                        {
                            "time": ts,
                            "name": str(turn.get("name") or qq),
                            "content": content,
                        }
                    )
                # 固化历史（episodes + facts），仅收集含该 QQ 的条目
                for ep in data.get("episodes", []) or []:
                    if not isinstance(ep, dict):
                        continue
                    text = str(ep.get("text") or "").strip()
                    if not text or not qq_pat.search(text):
                        continue
                    ts = _to_ts(ep.get("time"))
                    if ts and ts < cutoff:
                        continue
                    background.append({"time": ts, "text": text})
                for fact in data.get("facts", []) or []:
                    if isinstance(fact, str):
                        text = fact.strip()
                        if text and qq_pat.search(text):
                            background.append({"time": 0, "text": text})
    records.sort(key=lambda r: r["time"])
    background.sort(key=lambda b: b["time"])
    if len(records) > _cfg.pigimage_max_messages:
        records = records[-_cfg.pigimage_max_messages :]
    return records, background


# ---------------------------------------------------------------------------
# LLM 分析
# ---------------------------------------------------------------------------


def _get_llm() -> Optional[Any]:
    """优先用独立配置新建 LLM，否则复用 chat 插件的全局 LLM。"""
    if (
        _cfg.pigimage_llm_api_base
        and _cfg.pigimage_llm_api_key
        and _cfg.pigimage_llm_model
    ):
        cls = _import_llm_client()
        if cls is not None:
            try:
                return cls(
                    _cfg.pigimage_llm_api_base,
                    _cfg.pigimage_llm_api_key,
                    _cfg.pigimage_llm_model,
                )
            except Exception:
                pass
    for p in get_loaded_plugins():
        if p.id_ == "chat" and p.module is not None:
            llm = getattr(p.module, "_global_llm", None)
            if llm is not None:
                return llm
    return None


def _import_llm_client():
    for p in get_loaded_plugins():
        if p.id_ == "chat" and p.module is not None:
            return getattr(p.module, "LLMClient", None)
    return None


def _build_prompt(
    qq: str, nickname: str, records: list[dict], background: list[dict]
) -> str:
    """构建参数化、通用的画像分析提示词。"""
    lines = []
    for r in records:
        t = (
            datetime.fromtimestamp(r["time"]).strftime("%m-%d %H:%M")
            if r["time"]
            else "--"
        )
        lines.append(f"[{t}] {r['content']}")
    records_text = "\n".join(lines) or "（暂无原始发言）"

    bg_lines = []
    for b in background:
        t = (
            datetime.fromtimestamp(b["time"]).strftime("%m-%d %H:%M")
            if b["time"]
            else "--"
        )
        bg_lines.append(f"[{t}] {b['text']}")
    bg_text = "\n".join(bg_lines) or "（暂无）"

    return f"""你是一名专业的「用户画像分析引擎」。请综合目标用户的【原始发言】与【历史事件/长期记录】，深度分析并输出一份结构化的用户画像。所有结论必须能在输入数据中找到依据，禁止编造记录中不存在的信息。

【目标用户】
昵称：{nickname}
QQ号：{qq}

【原始发言】
共 {len(records)} 条，每条格式为 [时间] 发言内容：
{records_text}

【历史事件/长期记录】
共 {len(background)} 条（含该用户的跨群事件、长期事实，格式为 [时间] 事件/事实描述）：
{bg_text}

【分析维度（必须全部覆盖）】
1. 元数据 metadata：用户昵称、画像生成时间。
2. 个性摘要 personal_summary：活跃时段、发言频率特征、核心话题领域（3~5 个）、性格特征（用 3~6 个形容词概括）。
3. 核心特质 core_traits：个人标签（3~5 个）、游戏/网络/科技偏好（具体到游戏名/平台/方向）、自我认知与表达态度、内容喜好倾向。
4. 兴趣领域 interests：按类别分组列出具体兴趣爱好（如游戏类、数码类、二次元类、运动类等）。
5. 沟通风格 communication_style：句式特征、高频口头禅/口癖（从原始发言中原词提取 3~5 个）、群内互动模式、情绪特征、活跃节奏。
6. 词云 wordcloud：从【原始发言】与【历史事件/长期记录】中提炼 15~25 个能代表该用户的高频关键词（如兴趣、话题、常用词、口头禅等），每个词附带权重 weight（1~10，越高越能体现该用户特征）。

【分析要求】
- 沟通风格（口癖、句式、情绪、互动模式）主要依据【原始发言】；【历史事件/长期记录】仅作背景补充，不参与口癖/原话提取。
- 个性摘要、核心特质、兴趣领域应综合【原始发言】与【历史事件/长期记录】共同判断，避免只依据少量近期发言。

【输出要求】
只输出一个 JSON 对象，禁止输出任何多余文字或 markdown 代码块。JSON 结构严格如下：
{{
  "metadata": {{"nickname": "{nickname}", "generated_at": "YYYY-MM-DD HH:MM"}},
  "personal_summary": {{
    "active_hours": "例如：14:00-23:00",
    "speech_frequency": "例如：发言较多，集中在晚上",
    "core_topics": ["话题1", "话题2"],
    "personality": ["形容词1", "形容词2"]
  }},
  "core_traits": {{
    "tags": ["标签1", "标签2"],
    "preferences": ["具体偏好"],
    "self_awareness": "自我认知与表达态度描述",
    "content_preference": "内容喜好倾向描述"
  }},
  "interests": [
    {{"category": "游戏", "items": ["具体游戏"]}}
  ],
  "communication_style": {{
    "sentence_patterns": ["句式特征描述"],
    "catchphrases": ["口癖原词"],
    "interaction_pattern": "互动模式描述",
    "emotional_characteristics": "情绪特征描述",
    "activity_rhythm": "活跃节奏描述"
  }},
  "wordcloud": [
    {{"word": "关键词", "weight": 8}}
  ]
}}"""


def _parse_json(text: str) -> Optional[dict]:
    """宽容解析 LLM 返回的 JSON。"""
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except Exception:
            pass
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e > s:
        try:
            return json.loads(text[s : e + 1])
        except Exception:
            pass
    return None


# ---------------------------------------------------------------------------
# 图片渲染
# ---------------------------------------------------------------------------

_W = 960  # 画布总宽
_PAD = 24  # 页面外边距
_GAP = 16  # 模块间距
_COLS = 2  # 模块列数
_COL_W = (_W - 2 * _PAD - _GAP) // 2  # 单列宽
_HDR_H = 128  # 顶部用户信息栏高
_FOOTER_H = 56  # 底部文案栏高
_BG = (250, 246, 248)
_TX = (58, 48, 54)
_SUB = (146, 134, 141)
_CARD_BG = (255, 255, 255)
_BORDER = (238, 226, 231)
_ACCENTS = {
    "summary": (255, 143, 177),  # 粉
    "traits": (155, 123, 212),  # 紫
    "interests": (93, 173, 226),  # 蓝
    "style": (240, 162, 90),  # 橙
}
_WC_PALETTE = [
    (255, 143, 177),
    (155, 123, 212),
    (93, 173, 226),
    (240, 162, 90),
    (126, 190, 152),
    (235, 110, 110),
    (112, 148, 214),
    (199, 125, 187),
]

_FONT_BOLD = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]
_FONT_REGULAR = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\Deng.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
]
_EMOJI_FONTS = [
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/opentype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji-Regular.ttf",
    r"C:\Windows\Fonts\seguiemj.ttf",
]
_EMOJI_SIZE = 109  # Noto Color Emoji 位图 strike，固定字号渲染彩色表情

# 按完整 emoji 序列匹配（国旗对 / 键帽 / 肤色修饰符 / ZWJ 连接），避免拆散
_EMOJI_RE = re.compile(
    r"[\U0001F1E6-\U0001F1FF]{2}"
    r"|[0-9#*]\uFE0F?\u20E3"
    r"|["
    r"\U0001F000-\U0001FAFF"
    r"\u2600-\u27BF"
    r"\u2B00-\u2BFF"
    r"\u2300-\u23FF"
    r"\u25A0-\u25FF"
    r"\u2190-\u21FF"
    r"\u2934-\u2935"
    r"\u3030\u303D"
    r"\u3297\u3299"
    r"\u00A9\u00AE\u203C\u2049\u2122\u2139"
    r"]\uFE0F?"
    r"(?:[\U0001F3FB-\U0001F3FF])?"
    r"(?:\u200D["
    r"\U0001F000-\U0001FAFF"
    r"\u2600-\u27BF"
    r"\u2B00-\u2BFF"
    r"\u2300-\u23FF"
    r"\u25A0-\u25FF"
    r"\u2190-\u21FF"
    r"\u2934-\u2935"
    r"\u3030\u303D"
    r"\u3297\u3299"
    r"\u00A9\u00AE\u203C\u2049\u2122\u2139"
    r"\uFE0F])*"
)

_font_cache: dict[tuple[int, bool], Any] = {}
_emoji_font_obj: Optional[Any] = None
_emoji_font_loaded = False


def _font(size: int, bold: bool = False):
    key = (size, bold)
    if key not in _font_cache:
        for path in _FONT_BOLD if bold else _FONT_REGULAR:
            if Path(path).exists():
                try:
                    _font_cache[key] = ImageFont.truetype(path, size=size, index=0)
                    break
                except Exception:
                    continue
        else:
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


def _emoji_font() -> Optional[Any]:
    global _emoji_font_obj, _emoji_font_loaded
    if not _emoji_font_loaded:
        _emoji_font_loaded = True
        for path in _EMOJI_FONTS:
            if Path(path).exists():
                try:
                    _emoji_font_obj = ImageFont.truetype(path, size=_EMOJI_SIZE)
                    break
                except Exception:
                    continue
    return _emoji_font_obj


def _split_runs(text: str) -> list[tuple[bool, str]]:
    """把文本切成 emoji 序列 / 普通文本两段，emoji 序列保持完整。"""
    runs: list[tuple[bool, str]] = []
    pos = 0
    for m in _EMOJI_RE.finditer(text):
        if m.start() > pos:
            runs.append((False, text[pos : m.start()]))
        runs.append((True, m.group(0)))
        pos = m.end()
    if pos < len(text):
        runs.append((False, text[pos:]))
    return runs


def _emoji_piece(seg: str, target_h: int) -> Optional[Image.Image]:
    """用固定字号渲染彩色 emoji 并缩放到目标高度。"""
    ef = _emoji_font()
    if ef is None:
        return None
    pad = 4
    canvas = Image.new(
        "RGBA", (_EMOJI_SIZE + pad * 2, _EMOJI_SIZE + pad * 2), (0, 0, 0, 0)
    )
    td = ImageDraw.Draw(canvas)
    try:
        td.text((pad, pad), seg, font=ef, embedded_color=True)
    except Exception:
        td.text((pad, pad), seg, font=ef, fill=(0, 0, 0))
    bbox = canvas.getbbox()
    if not bbox:
        return None
    crop = canvas.crop(bbox)
    bw = bbox[2] - bbox[0]
    bh = bbox[3] - bbox[1]
    if bw <= 0 or bh <= 0:
        return None
    nw = max(1, round(bw * target_h / bh))
    return crop.resize((nw, target_h), Image.LANCZOS)


def _text_width(text: str, font) -> float:
    ef = _emoji_font()
    if ef is None:
        return font.getlength(text)
    target_h = round(font.size * 1.15)
    w = 0.0
    for is_emoji, seg in _split_runs(text):
        if is_emoji:
            piece = _emoji_piece(seg, target_h)
            w += piece.width if piece is not None else font.getlength(seg)
        else:
            w += font.getlength(seg)
    return w


def _draw_mixed_text(d, xy, text: str, font, fill):
    x, y = xy
    ef = _emoji_font()
    target_h = round(font.size * 1.15)
    asc, _ = font.getmetrics()
    rep = font.getbbox("国")
    text_cy = ((rep[1] + rep[3]) / 2) if rep else asc * 0.5
    for is_emoji, seg in _split_runs(text):
        if is_emoji and ef is not None:
            piece = _emoji_piece(seg, target_h)
            if piece is not None:
                paste_y = round(y + text_cy - target_h / 2)
                d._image.paste(piece, (round(x), paste_y), piece)
                x += piece.width
                continue
        d.text((x, y), seg, font=font, fill=fill)
        x += font.getlength(seg)


def _draw_centered(d, cx, y, text: str, font, fill):
    _draw_mixed_text(d, (cx - _text_width(text, font) / 2, y), text, font, fill)


def _wrap(text: str, font, max_w: int) -> list[str]:
    """按字符宽度折行（含 emoji 度量）。"""
    out: list[str] = []
    for seg in text.split("\n"):
        lines, cur = [], ""
        for ch in seg:
            if _text_width(cur + ch, font) <= max_w:
                cur += ch
            else:
                if cur:
                    lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
        if not lines:
            lines = [""]
        out.extend(lines)
    return out or [""]


def _chips_layout(chips: list[str], font, max_w: int) -> list[list[str]]:
    """标签流式折行，返回若干行。"""
    lines, cur, cur_w = [], [], 0
    for c in chips:
        w = _text_width(c, font) + 30
        if cur and cur_w + w > max_w:
            lines.append(cur)
            cur, cur_w = [c], w
        else:
            cur.append(c)
            cur_w += w
    if cur:
        lines.append(cur)
    return lines


def _build_sections(data: dict) -> list[dict]:
    """把 LLM 结果整理为渲染用的 section 列表。"""
    ps = data.get("personal_summary") or {}
    ct = data.get("core_traits") or {}
    it = data.get("interests") or []
    cs = data.get("communication_style") or {}
    return _all_sections(ps, ct, it, cs)


def _all_sections(ps, ct, it, cs) -> list[dict]:
    """实际的 section 组装。"""
    sections = []

    s1 = {"title": "个性摘要", "key": "summary", "rows": []}
    if isinstance(ps, dict):
        if ps.get("active_hours"):
            s1["rows"].append(("kv", "活跃时段", str(ps["active_hours"])))
        if ps.get("speech_frequency"):
            s1["rows"].append(("kv", "发言频率", str(ps["speech_frequency"])))
        if ps.get("core_topics"):
            s1["rows"].append(
                ("text", "核心话题：" + "、".join(map(str, ps["core_topics"])))
            )
        if ps.get("personality"):
            s1["rows"].append(("chips", list(map(str, ps["personality"]))))

    s2 = {"title": "核心特质", "key": "traits", "rows": []}
    if isinstance(ct, dict):
        if ct.get("tags"):
            s2["rows"].append(("chips", list(map(str, ct["tags"]))))
        if ct.get("preferences"):
            s2["rows"].append(
                (
                    "text",
                    "游戏/网络/科技偏好：" + "；".join(map(str, ct["preferences"])),
                )
            )
        if ct.get("self_awareness"):
            s2["rows"].append(("kv", "自我认知", str(ct["self_awareness"])))
        if ct.get("content_preference"):
            s2["rows"].append(("kv", "内容喜好", str(ct["content_preference"])))

    s3 = {"title": "兴趣领域", "key": "interests", "rows": []}
    if isinstance(it, list):
        for g in it:
            if isinstance(g, dict) and g.get("items"):
                s3["rows"].append(
                    (
                        "text",
                        f"✦ {g.get('category', '兴趣')}："
                        + "、".join(map(str, g["items"])),
                    )
                )

    s4 = {"title": "沟通风格", "key": "style", "rows": []}
    if isinstance(cs, dict):
        if cs.get("sentence_patterns"):
            s4["rows"].append(
                ("text", "句式特征：" + "；".join(map(str, cs["sentence_patterns"])))
            )
        if cs.get("catchphrases"):
            s4["rows"].append(("chips", list(map(str, cs["catchphrases"]))))
        if cs.get("interaction_pattern"):
            s4["rows"].append(("kv", "互动模式", str(cs["interaction_pattern"])))
        if cs.get("emotional_characteristics"):
            s4["rows"].append(("kv", "情绪特征", str(cs["emotional_characteristics"])))
        if cs.get("activity_rhythm"):
            s4["rows"].append(("kv", "活跃节奏", str(cs["activity_rhythm"])))

    for s in (s1, s2, s3, s4):
        if s["rows"]:
            sections.append(s)
    return sections


def _draw_section(sec: dict, width: int) -> Image.Image:
    accent = _ACCENTS.get(sec["key"], _ACCENTS["summary"])
    rows = sec["rows"]
    cw = width - 2 * 24
    f14, f12 = _font(14), _font(12)

    body_h = 0
    layouts = []
    for row in rows:
        kind = row[0]
        if kind in ("kv", "text"):
            text = f"{row[1]}：{row[2]}" if kind == "kv" else row[1]
            lines = _wrap(text, f14, cw - 28)
            layouts.append(("text", lines, f14, _TX))
            body_h += len(lines) * 26
        elif kind == "chips":
            chip_lines = _chips_layout(row[1], f12, cw - 28)
            layouts.append(("chips", chip_lines))
            body_h += len(chip_lines) * 34
        body_h += 8

    h = 62 + body_h + 18
    img = Image.new("RGBA", (width, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, width - 1, h - 1], radius=16, fill=_CARD_BG)
    d.rounded_rectangle([0, 0, 5, h - 1], radius=3, fill=accent)
    d.text((24, 20), sec["title"], font=_font(17, True), fill=accent)
    d.line([24, 50, width - 24, 50], fill=_BORDER, width=1)

    y = 62
    for layout in layouts:
        if layout[0] == "chips":
            for chip_line in layout[1]:
                x = 38
                for label in chip_line:
                    w = _text_width(label, f12) + 22
                    d.rounded_rectangle(
                        [x, y + 3, x + w, y + 27], radius=12, fill=(*accent[:3], 30)
                    )
                    _draw_mixed_text(d, (x + 11, y + 8), label, f12, accent)
                    x += w + 10
                y += 34
        else:
            lines, font, color = layout[1], layout[2], layout[3]
            for line in lines:
                _draw_mixed_text(d, (38, y), line, font, color)
                y += 26
            y += 4
    return img


def _draw_chart_card(title: str, chart: Image.Image, width: int, accent) -> Image.Image:
    h = 60 + chart.height + 18
    img = Image.new("RGBA", (width, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, width - 1, h - 1], radius=16, fill=_CARD_BG)
    d.rounded_rectangle([0, 0, 5, h - 1], radius=3, fill=accent)
    d.text((24, 20), title, font=_font(17, True), fill=accent)
    d.line([24, 50, width - 24, 50], fill=_BORDER, width=1)
    img.paste(chart, (24, 58), chart)
    return img


def _draw_wordcloud(words: list[dict], width: int, height: int) -> Image.Image:
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    items: list[tuple[str, int]] = []
    for w in words:
        if isinstance(w, dict) and w.get("word"):
            word = str(w["word"]).strip()
            if word:
                try:
                    weight = max(1, min(10, int(w.get("weight", 5))))
                except Exception:
                    weight = 5
                items.append((word, weight))
    if not items:
        return img

    items.sort(key=lambda t: -t[1])
    max_wt = max(t[1] for t in items)
    min_wt = min(t[1] for t in items)
    span = max(1, max_wt - min_wt)

    placed: list[tuple[int, int, int, int]] = []
    cx, cy = width / 2, height / 2
    for word, wt in items:
        size = 12 + int(round((wt - min_wt) / span * 22))
        font = _font(size, True) if wt >= max_wt * 0.7 else _font(size)
        ok = False
        for _try in range(4):
            bbox = font.getbbox(word)
            x0b, y0b, x1b, y1b = bbox
            tw = x1b - x0b
            th = y1b - y0b
            if tw <= 0 or th <= 0:
                break
            pad = 2
            a = 0.0
            r = 0.0
            for _ in range(4000):
                x = cx + r * math.cos(a)
                y = cy + r * math.sin(a)
                bx0 = int(x - tw / 2) - pad
                by0 = int(y - th / 2) - pad
                bx1 = bx0 + tw + pad * 2
                by1 = by0 + th + pad * 2
                if bx0 >= 1 and by0 >= 1 and bx1 <= width - 1 and by1 <= height - 1:
                    if not any(
                        not (bx1 < p[0] or bx0 > p[2] or by1 < p[1] or by0 > p[3])
                        for p in placed
                    ):
                        placed.append((bx0, by0, bx1, by1))
                        ok = True
                        break
                a += 0.4 / (r + 1.0)
                r = a * 2.0
            if ok:
                break
            size = max(11, size - 4)
            font = _font(size)
        if not ok:
            continue
        color = _WC_PALETTE[sum(ord(c) for c in word) % len(_WC_PALETTE)]
        _draw_mixed_text(d, (bx0 + pad - x0b, by0 + pad - y0b), word, font, color)
    return img


def _draw_freq_chart(records: list[dict], width: int, height: int) -> Image.Image:
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f10 = _font(10)
    accent = _ACCENTS["style"]

    hours = [0] * 24
    for r in records:
        ts = r.get("time")
        if ts:
            hours[datetime.fromtimestamp(ts).hour] += 1
    total = sum(hours)

    ml, mr, mt, mb = 38, 12, 14, 26
    pw = width - ml - mr
    ph = height - mt - mb
    max_v = max(hours) if total else 1

    d.line([(ml, mt), (ml, mt + ph)], fill=_BORDER, width=1)
    d.line([(ml, mt + ph), (ml + pw, mt + ph)], fill=_BORDER, width=1)
    for i in range(1, 4):
        v = max_v * i / 4
        y = mt + ph - (v / max_v) * ph
        d.line([(ml, y), (ml + pw, y)], fill=(236, 228, 233), width=1)
        d.text((2, y - 6), str(int(v)), font=f10, fill=_SUB)

    pts = []
    for h in range(24):
        x = ml + (h / 23) * pw
        y = mt + ph - (hours[h] / max_v) * ph
        pts.append((x, y))

    if total:
        d.line(pts, fill=accent, width=2, joint="curve")
    for h, (x, y) in enumerate(pts):
        if hours[h] > 0:
            d.ellipse([x - 2.5, y - 2.5, x + 2.5, y + 2.5], fill=accent)

    for h in (0, 6, 12, 18, 23):
        x = ml + (h / 23) * pw
        d.text((x - 8, mt + ph + 6), f"{h}时", font=f10, fill=_SUB)
    return img


def _make_avatar(data: bytes, size: int) -> Optional[Image.Image]:
    try:
        img = Image.open(BytesIO(data)).convert("RGBA")
    except Exception:
        return None
    img = img.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


async def _fetch_avatar(qq: str) -> Optional[bytes]:
    url = f"https://q1.qlogo.cn/g?b=qq&nk={qq}&s=640"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.content
            return data if len(data) > 200 else None
    except Exception:
        return None


def _draw_topbar() -> Image.Image:
    h = 64
    img = Image.new("RGBA", (_W, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.text((_PAD, 12), "用户画像", font=_font(28, True), fill=_TX)
    d.text((_PAD, 46), "USER PROFILE", font=_font(11), fill=_SUB)
    return img


def _draw_header(
    nickname: str,
    qq: str,
    generated: str,
    stats: dict,
    avatar: Optional[bytes],
) -> Image.Image:
    img = Image.new("RGBA", (_W, _HDR_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, _W - 1, _HDR_H - 1], radius=18, fill=_CARD_BG)

    size = 88
    ax = _PAD + 8
    ay = (_HDR_H - size) // 2
    av = _make_avatar(avatar, size) if avatar else None
    if av is not None:
        img.paste(av, (ax, ay), av)
        d.ellipse(
            [ax - 3, ay - 3, ax + size + 3, ay + size + 3],
            outline=_ACCENTS["summary"],
            width=3,
        )
    else:
        d.ellipse([ax, ay, ax + size, ay + size], fill=(245, 236, 240))
        _draw_centered(
            d, ax + size / 2, ay + size / 2 - 18, (nickname or "?")[:1],
            _font(36, True), _SUB,
        )

    tx = ax + size + 22
    d.text((tx, ay + 4), nickname or qq, font=_font(26, True), fill=_TX)
    f14 = _font(14)
    d.text((tx, ay + 48), f"QQ {qq}", font=f14, fill=_SUB)
    gx = tx + _text_width(f"QQ {qq}", f14) + 16
    d.text((gx, ay + 48), f"生成于 {generated}", font=f14, fill=_SUB)

    rx = _W - _PAD - 8
    d.text(
        (rx, ay + 4),
        f"分析 {stats.get('count', 0)} 条记录",
        font=_font(16, True),
        fill=_ACCENTS["summary"],
        anchor="ra",
    )
    span = stats.get("span", "")
    if span:
        d.text((rx, ay + 38), f"时间跨度 {span}", font=_font(12), fill=_SUB, anchor="ra")
    return img


def _render_profile(
    data: dict,
    nickname: str,
    qq: str,
    stats: dict,
    records: list[dict],
    avatar: Optional[bytes],
) -> bytes:
    meta = data.get("metadata") or {}
    generated = str(
        meta.get("generated_at") or datetime.now().strftime("%Y-%m-%d %H:%M")
    )
    sections = _build_sections(data)
    wordcloud = data.get("wordcloud") or []

    cards: list[Image.Image] = []
    for s in sections:
        cards.append(_draw_section(s, _COL_W))
    wc_img = _draw_wordcloud(wordcloud, _COL_W - 48, 220)
    cards.append(_draw_chart_card("关键词云", wc_img, _COL_W, _ACCENTS["summary"]))
    fc_img = _draw_freq_chart(records, _COL_W - 48, 200)
    cards.append(_draw_chart_card("发言频率 · 时间", fc_img, _COL_W, _ACCENTS["style"]))

    rows: list[tuple[list[Image.Image], int]] = []
    for i in range(0, len(cards), _COLS):
        row = cards[i : i + _COLS]
        rows.append((row, max(c.height for c in row)))
    grid_h = sum(rh for _, rh in rows) + _GAP * (len(rows) - 1)

    topbar = _draw_topbar()
    header = _draw_header(nickname, qq, generated, stats, avatar)

    total_h = (
        topbar.height
        + _GAP
        + header.height
        + _GAP
        + grid_h
        + _FOOTER_H
    )
    canvas = Image.new("RGBA", (_W, total_h), _BG)
    y = 0
    canvas.paste(topbar, (0, y), topbar)
    y += topbar.height + _GAP
    canvas.paste(header, (0, y), header)
    y += header.height + _GAP

    x_left = _PAD
    x_right = _PAD + _COL_W + _GAP
    for row, row_h in rows:
        if row:
            canvas.paste(row[0], (x_left, y), row[0])
        if len(row) > 1:
            canvas.paste(row[1], (x_right, y), row[1])
        y += row_h + _GAP

    fd = ImageDraw.Draw(canvas)
    fd.line(
        [_PAD, total_h - _FOOTER_H, _W - _PAD, total_h - _FOOTER_H],
        fill=_BORDER,
        width=1,
    )
    txt = "由 [YunaNexus] 提供数据及分析支持"
    fd.text(
        ((_W - _font(12).getlength(txt)) / 2, total_h - _FOOTER_H + 20),
        txt,
        font=_font(12),
        fill=_SUB,
    )

    buf = BytesIO()
    canvas.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 命令入口
# ---------------------------------------------------------------------------


@_cmd.handle()
async def _handle(event: MessageEvent, matcher: Matcher):
    if not _cfg.pigimage_enabled:
        await matcher.finish("用户画像功能未启用。")

    args = str(event.message).strip()
    m = re.search(r"\d{5,12}", args)
    qq = m.group(0) if m else str(event.user_id)

    nickname = _nickname_of(qq)
    records, background = _collect_records(qq)
    if not records and not background:
        await matcher.finish(
            f"没有找到 {nickname}({qq}) 的聊天记录，暂时无法生成画像。"
        )

    key = (
        f"{len(records)}|{len(background)}|"
        f"{records[-1]['time'] if records else 0}|"
        f"{background[-1]['time'] if background else 0}"
    )
    hit = _cache.get(qq)
    if hit and hit[0] == key and hit[2] > time.time():
        await matcher.finish(MessageSegment.image(hit[1]))

    await matcher.send(
        f"正在分析 {nickname} 的画像（原始发言 {len(records)} 条 + 历史记录 {len(background)} 条），请稍候…"
    )

    llm = _get_llm()
    if llm is None:
        await matcher.finish(
            "未找到可用的 LLM 配置（chat 插件未加载或未配置），无法生成画像。"
        )

    prompt = _build_prompt(qq, nickname, records, background)
    try:
        raw = await llm.chat(
            [
                {
                    "role": "system",
                    "content": "你是严格的 JSON 输出助手，只输出 JSON，绝不输出多余内容。",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=_cfg.pigimage_llm_temperature,
            max_tokens=_cfg.pigimage_llm_max_tokens,
            disable_thinking=True,
        )
    except Exception as e:
        await matcher.finish(f"AI 分析失败：{e}")

    data = _parse_json(raw)
    if not data:
        await matcher.finish("AI 返回结果无法解析，请稍后重试。")

    all_ts = [r["time"] for r in records if r["time"]] + [
        b["time"] for b in background if b["time"]
    ]
    if all_ts:
        start = datetime.fromtimestamp(min(all_ts)).strftime("%m-%d")
        end = datetime.fromtimestamp(max(all_ts)).strftime("%m-%d")
        span = f"{start}~{end}"
    else:
        span = ""
    stats = {"count": len(records) + len(background), "span": span}

    avatar = await _fetch_avatar(qq)

    try:
        img_bytes = _render_profile(data, nickname, qq, stats, records, avatar)
    except Exception as e:
        await matcher.finish(f"画像渲染失败：{e}")

    _cache[qq] = (key, img_bytes, time.time() + _cfg.pigimage_cache_ttl)
    await matcher.finish(MessageSegment.image(img_bytes))
