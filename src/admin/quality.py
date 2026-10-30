"""聊天质量快照：用可解释的规则扫描已保存的助手回复。

不调用模型、不猜测语义，只统计最影响“像人”的几类问题：
* 模板化/AI 免责声明；
* 与本次会话近期回复高度相似（复读）；
* 过短或过长的回复；
* 问句占比。

目标是给提示词调整提供基线，而不是给模型打分。
"""

from __future__ import annotations

import json
import re
import time
from collections import deque
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

TEMPLATE_PATTERNS = [
    ("AI 身份", re.compile(r"(作为|身为|我只是|我是一个).{0,8}(AI|人工智能|语言模型|助手|机器人)", re.I)),
    ("总结腔", re.compile(r"(总的来说|综上所述|总而言之|总结一下|概括来说)")),
    ("列点腔", re.compile(r"(首先|其次|最后)[，,：:]")),
    ("客套腔", re.compile(r"(希望(这些|以上|我的)?(内容|回答|建议)?(对你|对您)?(有|能)(所)?帮助|如果你(还)?(有|需要)(任何)?(问题|帮助|疑问))", re.I)),
    ("书面连接", re.compile(r"(不过说真的|值得一提的是|值得注意的是|这让我想起|不得不说)")),
]

_MARKER = re.compile(r"\[\[(?:SPLIT|EMOTE|MISC)[^\]]*\]\]")
_NORMALIZE = re.compile(r"[\s，。！？!?、；;：:…~～\"'“”‘’「」『』（）()\[\]【】]+")


def _normalize(text: str) -> str:
    return _NORMALIZE.sub("", _MARKER.sub("", text or ""))


def _percent(value: int, total: int) -> float:
    return round(value / total * 100, 1) if total else 0.0


@dataclass
class Reply:
    kind: str
    key: str
    time: float
    text: str


def _iter_replies(chat_dir: Path) -> tuple[list[Reply], int, list[str]]:
    replies: list[Reply] = []
    sessions = 0
    errors: list[str] = []
    for kind in ("group", "user"):
        directory = chat_dir / "memory" / kind
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            sessions += 1
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as error:
                errors.append(f"{kind}/{path.stem}: {error}")
                continue
            for turn in data.get("pending") or []:
                if not isinstance(turn, dict) or turn.get("role") != "assistant":
                    continue
                text = str(turn.get("content") or "").strip()
                if not text:
                    continue
                try:
                    timestamp = float(turn.get("time") or 0)
                except (TypeError, ValueError):
                    timestamp = 0.0
                replies.append(Reply(kind, path.stem, timestamp, text))
    replies.sort(key=lambda item: item.time)
    return replies, sessions, errors


def analyze_chat(chat_dir: Path, example_limit: int = 30) -> dict:
    started = time.time()
    replies, sessions, errors = _iter_replies(Path(chat_dir))
    total = len(replies)
    lengths = [len(_MARKER.sub("", item.text)) for item in replies]
    lengths.sort()

    def percentile(values: list[int], ratio: float) -> int:
        if not values:
            return 0
        index = min(len(values) - 1, max(0, int(len(values) * ratio)))
        return values[index]

    template_count = 0
    repeated_count = 0
    short_count = 0
    long_count = 0
    question_count = 0
    flagged_count = 0
    examples: list[dict] = []
    per_key_previous: dict[str, deque] = {}
    by_key: dict[str, dict] = {}

    for reply, length in zip(replies, lengths):
        plain = _MARKER.sub("", reply.text).strip()
        reasons: list[str] = []
        for label, pattern in TEMPLATE_PATTERNS:
            if pattern.search(plain):
                template_count += 1
                reasons.append(label)
                break
        if length < 6:
            short_count += 1
            reasons.append("过短")
        if length > 120:
            long_count += 1
            reasons.append("过长")
        if re.search(r"[?？]", plain):
            question_count += 1
        normalized = _normalize(plain)
        previous = per_key_previous.setdefault(reply.key, deque(maxlen=20))
        if len(normalized) >= 4:
            for old in previous:
                if len(old) >= 4 and SequenceMatcher(None, normalized, old).ratio() >= 0.85:
                    repeated_count += 1
                    reasons.append("复读")
                    break
            previous.append(normalized)
        bucket = by_key.setdefault(reply.key, {"kind": reply.kind, "key": reply.key, "replies": 0, "flagged": 0})
        bucket["replies"] += 1
        if reasons:
            bucket["flagged"] += 1
            flagged_count += 1
            examples.append({
                "kind": reply.kind,
                "key": reply.key,
                "time": int(reply.time),
                "text": plain[:500],
                "length": length,
                "reasons": list(dict.fromkeys(reasons)),
            })
            if len(examples) > example_limit:
                examples.pop(0)

    examples.sort(key=lambda item: item["time"], reverse=True)
    buckets = [
        {"label": "<6 字", "value": sum(1 for item in lengths if item < 6)},
        {"label": "6–20 字", "value": sum(1 for item in lengths if 6 <= item < 20)},
        {"label": "20–40 字", "value": sum(1 for item in lengths if 20 <= item < 40)},
        {"label": "40–80 字", "value": sum(1 for item in lengths if 40 <= item < 80)},
        {"label": "80 字以上", "value": sum(1 for item in lengths if item >= 80)},
    ]
    return {
        "ok": True,
        "generated": int(time.time()),
        "elapsed_ms": int((time.time() - started) * 1000),
        "sessions": sessions,
        "total_replies": total,
        "total_chars": sum(lengths),
        "avg_length": round(sum(lengths) / total, 1) if total else 0,
        "median_length": percentile(lengths, 0.5),
        "p90_length": percentile(lengths, 0.9),
        "template_count": template_count,
        "template_rate": _percent(template_count, total),
        "repeat_count": repeated_count,
        "repeat_rate": _percent(repeated_count, total),
        "short_count": short_count,
        "short_rate": _percent(short_count, total),
        "long_count": long_count,
        "long_rate": _percent(long_count, total),
        "question_count": question_count,
        "question_rate": _percent(question_count, total),
        "length_buckets": buckets,
        "flagged_count": flagged_count,
        "examples": examples,
        "errors": errors[:20],
        "by_key": sorted(
            by_key.values(),
            key=lambda item: (-item["flagged"], -item["replies"]),
        )[:20],
    }
