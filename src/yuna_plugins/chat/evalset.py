"""对话评测集：采样保存真实触发场景、人工标注、回归检查。

样本 = 触发时的滚动上下文 + 本条触发消息 + Yuna 当时的回复。
目的：提示词或模型变更后对同一批场景重放，对比模板化/复读/长度，
配合人工标注（接错人 / 越界称呼 / 编造经历）形成可回归的评测基线。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from difflib import SequenceMatcher
from pathlib import Path

# 人工标注的问题类型（与 ROADMAP 评测指标对应）
LABEL_KEYS = ("wrong_target", "repetitive", "template", "wrong_alias", "fabrication")
LABEL_NAMES = {
    "wrong_target": "接错人",
    "repetitive": "复读",
    "template": "过度模板",
    "wrong_alias": "越界称呼",
    "fabrication": "编造经历",
}

TEMPLATE_PATTERN = re.compile(
    r"(作为|身为|我只是|我是一个).{0,8}(AI|人工智能|语言模型|助手|机器人)"
    r"|(总的来说|综上所述|总而言之|总结一下)"
    r"|(首先|其次|最后)[，,：:]"
    r"|希望(这些|以上|我的)?(内容|回答|建议)?(对你|对您)?(有|能)(所)?帮助"
    r"|有什么可以帮",
    re.I,
)
_MARKER = re.compile(r"\[\[(?:SPLIT|EMOTE|MISC)[^\]]*\]\]")
_NORMALIZE = re.compile(r"[\s，。！？!?、；;：:…~～\"'“”‘’「」『』（）()\[\]【】]+")


def check_reply(reply: str, prev_assistant_texts: list[str]) -> dict:
    """对单条回复跑可解释规则检查：模板化 / 复读 / 长度 / 问句。"""
    plain = _MARKER.sub("", str(reply or "")).strip()
    normalized = _NORMALIZE.sub("", plain)
    repetitive = False
    if len(normalized) >= 4:
        for old in prev_assistant_texts[-8:]:
            old_norm = _NORMALIZE.sub("", _MARKER.sub("", str(old or "")))
            if len(old_norm) >= 4 and SequenceMatcher(None, normalized, old_norm).ratio() >= 0.78:
                repetitive = True
                break
    return {
        "template": bool(TEMPLATE_PATTERN.search(plain)),
        "repetitive": repetitive,
        "length": len(plain),
        "question": bool(re.search(r"[?？]", plain)),
    }


def _slim_context(context: list[dict], limit: int = 24) -> list[dict]:
    """裁剪滚动上下文用于存储：去掉图片数据，只保留文本与基本归属。"""
    rows = []
    for turn in (context or [])[-limit:]:
        if not isinstance(turn, dict):
            continue
        content = str(turn.get("content") or "")
        if not content.strip() and turn.get("role") != "assistant":
            continue
        row = {
            "role": turn.get("role") or "user",
            "name": turn.get("name") or "",
            "qq": str(turn.get("qq") or ""),
            "content": content[:2000],
        }
        if turn.get("images"):
            row["images"] = len(turn["images"])
        if turn.get("recalled"):
            row["recalled"] = True
        rows.append(row)
    return rows


class EvalStore:
    def __init__(self, path: Path, max_samples: int = 400):
        self.path = Path(path)
        self.max_samples = max(50, int(max_samples))
        self._lock = threading.Lock()
        self.data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "samples": []}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("samples"), list):
                return raw
        except Exception:
            pass
        return {"version": 1, "samples": []}

    def _save_locked(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            os.replace(temporary, self.path)
        except Exception:
            pass  # 评测集写盘失败不影响聊天主流程

    def add(
        self,
        *,
        kind: str,
        key: str,
        qq: str,
        trigger: str,
        text: str,
        context: list,
        reply: str,
    ) -> dict | None:
        """保存一条评测样本；回复为空（静默/失败兜底）时不保存。"""
        reply = str(reply or "").strip()
        if not reply or reply == "[SILENT]":
            return None
        sample = {
            "id": uuid.uuid4().hex[:12],
            "time": int(time.time()),
            "kind": str(kind),
            "key": str(key),
            "qq": str(qq),
            "trigger": str(trigger or ""),
            "text": str(text or "")[:1000],
            "context": _slim_context(context),
            "reply": reply[:1000],
            "labels": {name: False for name in LABEL_KEYS},
            "note": "",
            "labeled": False,
        }
        with self._lock:
            samples = self.data.setdefault("samples", [])
            samples.append(sample)
            if len(samples) > self.max_samples:
                self.data["samples"] = samples[-self.max_samples:]
            self._save_locked()
        return sample

    def list(self, query: str = "", labeled: str = "all", limit: int = 200) -> list[dict]:
        with self._lock:
            rows = [dict(s) for s in self.data.get("samples") or []]
        q = str(query or "").lower()
        out = []
        for row in reversed(rows):
            if labeled == "yes" and not row.get("labeled"):
                continue
            if labeled == "no" and row.get("labeled"):
                continue
            if labeled == "flagged" and not any((row.get("labels") or {}).values()):
                continue
            if q and q not in f"{row.get('qq')} {row.get('key')} {row.get('text')} {row.get('reply')} {row.get('note')}".lower():
                continue
            out.append(row)
            if len(out) >= max(1, min(500, limit)):
                break
        return out

    def get(self, sample_id: str) -> dict | None:
        with self._lock:
            for row in self.data.get("samples") or []:
                if str(row.get("id")) == str(sample_id):
                    return dict(row)
        return None

    def _find_locked(self, sample_id: str) -> dict | None:
        for row in self.data.get("samples") or []:
            if str(row.get("id")) == str(sample_id):
                return row
        return None

    def label(self, sample_id: str, labels: dict, note: str = "") -> dict | None:
        with self._lock:
            row = self._find_locked(sample_id)
            if row is None:
                return None
            clean = {name: bool(labels.get(name)) for name in LABEL_KEYS}
            row["labels"] = clean
            row["note"] = str(note or "")[:300]
            row["labeled"] = any(clean.values()) or bool(row["note"])
            row["labeled_time"] = int(time.time())
            self._save_locked()
            return dict(row)

    def delete(self, sample_id: str) -> bool:
        with self._lock:
            samples = self.data.get("samples") or []
            before = len(samples)
            self.data["samples"] = [s for s in samples if str(s.get("id")) != str(sample_id)]
            removed = before != len(self.data["samples"])
            if removed:
                self._save_locked()
            return removed

    def set_regression(self, sample_id: str, reply: str, checks: dict) -> dict | None:
        with self._lock:
            row = self._find_locked(sample_id)
            if row is None:
                return None
            old = str(row.get("reply") or "")
            row["regression"] = {
                "time": int(time.time()),
                "reply": str(reply)[:1000],
                "checks": checks,
                "changed": str(reply).strip() != old.strip(),
            }
            self._save_locked()
            return dict(row)

    def stats(self) -> dict:
        with self._lock:
            rows = [dict(s) for s in self.data.get("samples") or []]
        total = len(rows)
        labeled = sum(1 for r in rows if r.get("labeled"))
        label_counts = {name: 0 for name in LABEL_KEYS}
        for row in rows:
            for name, value in (row.get("labels") or {}).items():
                if value and name in label_counts:
                    label_counts[name] += 1
        auto_template = auto_repeat = 0
        lengths: list[int] = []
        for row in rows:  # 按时间正序统计
            context_texts = [
                t.get("content") or ""
                for t in (row.get("context") or [])
                if t.get("role") == "assistant"
            ]
            checks = check_reply(row.get("reply") or "", context_texts)
            auto_template += 1 if checks["template"] else 0
            auto_repeat += 1 if checks["repetitive"] else 0
            lengths.append(checks["length"])
        return {
            "total": total,
            "labeled": labeled,
            "label_counts": label_counts,
            "label_names": LABEL_NAMES,
            "auto_template": auto_template,
            "auto_repeat": auto_repeat,
            "avg_length": round(sum(lengths) / len(lengths), 1) if lengths else 0,
        }
