"""好感扣分账本：去重、窗口上限、原因/关系阈值与申诉恢复。

人工审核结果会反哺规则：同一原因近期多次被判定误判恢复时，
should_suppress() 返回 True，调用方可跳过后续同类扣分。
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

DEFAULT_WINDOW = 600
DEFAULT_CAP = 6.0
FEEDBACK_WINDOW_DAYS = 30


def _slim_context(context, limit: int = 16) -> list[dict]:
    """把滚动上下文瘦身为可存储的摘要：只留归属与文本。"""
    rows = []
    for turn in (context or [])[-limit:]:
        if not isinstance(turn, dict):
            continue
        content = str(turn.get("content") or "").strip()
        if not content:
            continue
        rows.append({
            "role": turn.get("role") or "user",
            "name": str(turn.get("name") or ""),
            "qq": str(turn.get("qq") or ""),
            "content": content[:300],
            "recalled": bool(turn.get("recalled")),
        })
    return rows


class FavorLedger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "entries": [], "learned_exceptions": [], "learned_anomalies": []}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("entries"), list):
                raw.setdefault("learned_exceptions", [])
                raw.setdefault("learned_anomalies", [])
                return raw
        except Exception:
            pass
        return {"version": 1, "entries": []}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha1(str(text or "").strip().encode("utf-8")).hexdigest()[:16]

    def apply(
        self,
        qq: str,
        penalty: float,
        *,
        reason: str,
        text: str,
        relation: dict,
        threshold: dict,
        source: str = "message",
        context: list | None = None,
        group: str = "",
        window: float = DEFAULT_WINDOW,
        cap: float = DEFAULT_CAP,
    ) -> tuple[float, dict | None]:
        """返回实际扣分和账本条目；重复/达到窗口上限时 actual=0。"""
        try:
            penalty = max(0.0, float(penalty))
        except (TypeError, ValueError):
            return 0.0, None
        now = int(time.time())
        text_hash = self._hash(text)
        if self.should_suppress(reason, text):
            return 0.0, None
        entries = self.data.setdefault("entries", [])
        cutoff = now - max(30, int(window))
        recent = [e for e in entries if int(e.get("time") or 0) >= cutoff and str(e.get("qq")) == str(qq)]
        # 同一窗口最多扣分 6 次，不限制每次分数；相同文本+原因允许再次触发
        if len(recent) >= max(1, int(float(cap))):
            return 0.0, None
        context_key_parts = []
        for turn in (context or [])[-8:]:
            if not isinstance(turn, dict):
                continue
            content = str(turn.get("content") or "").strip()
            if not content or content == str(text or "").strip():
                continue
            context_key_parts.append(f"{turn.get('role') or ''}:{turn.get('qq') or ''}:{content[:160]}")
        context_hash = hashlib.sha1(
            (str(group or "") + "|" + "|".join(context_key_parts[-4:])).encode("utf-8")
        ).hexdigest()[:16]
        # 同一用户、同一群、同一段上下文内的连续触发合并为一条账本记录。
        # 上下文允许夹杂其他人的发言，因此不能只比较完整哈希；用共享上下文
        # 或短时间连续触发兜底，避免同一轮对话被拆成多条。
        current_lines = {
            str(turn.get("content") or "").strip()
            for turn in (context or [])[-20:]
            if isinstance(turn, dict) and str(turn.get("content") or "").strip()
            and str(turn.get("content") or "").strip() != str(text or "").strip()
        }
        for existing in reversed(entries):
            if (str(existing.get("qq")) != str(qq) or existing.get("restored")
                    or str(existing.get("group") or "") != str(group or "")):
                continue
            old_lines = {
                str(turn.get("content") or "").strip()
                for turn in (existing.get("context") or [])
                if isinstance(turn, dict) and str(turn.get("content") or "").strip()
            }
            shared = bool(current_lines & old_lines)
            # 只按“是否同一段上下文”合并，不再要求十分钟/三分钟内。
            # 中间夹其他人的发言不影响：只要上下文仍有重合，或上下文哈希一致，
            # 即使隔了很久也归到同一条记录。
            # Empty contexts all hash to the same value. They provide no evidence
            # that two triggers belong to one conversation and must stay separate.
            same_context = bool(context_key_parts) and existing.get("context_hash") == context_hash
            if not same_context and not shared:
                continue
            triggers = existing.get("triggers")
            if not isinstance(triggers, list):
                triggers = [{
                    "time": existing.get("time"), "penalty": existing.get("penalty"),
                    "requested": existing.get("requested"), "reason": existing.get("reason"),
                    "text_hash": existing.get("text_hash"), "source": existing.get("source"),
                    "text": existing.get("text") or "",
                }]
            triggers.append({
                "time": now, "penalty": round(penalty, 2), "requested": round(penalty, 2),
                "reason": reason, "text_hash": text_hash, "source": source,
                "text": str(text or "")[:500],
            })
            existing["triggers"] = triggers[-50:]
            existing["penalty"] = round(sum(float(t.get("penalty") or 0) for t in existing["triggers"]), 2)
            existing["requested"] = round(sum(float(t.get("requested") or 0) for t in existing["triggers"]), 2)
            existing["reason"] = "、".join(dict.fromkeys(str(t.get("reason") or "") for t in existing["triggers"]))[:200]
            existing["count"] = len(existing["triggers"])
            existing["last_time"] = now
            existing["relation"] = dict(relation or existing.get("relation") or {})
            existing["threshold"] = dict(threshold or existing.get("threshold") or {})
            self._save()
            return round(penalty, 2), existing
        actual = penalty
        entry = {
            "id": f"{now}-{os.urandom(3).hex()}",
            "time": now,
            "last_time": now,
            "qq": str(qq),
            "penalty": round(actual, 2),
            "requested": round(penalty, 2),
            "reason": reason,
            "source": source,
            "group": str(group or ""),
            "context_hash": context_hash,
            "text_hash": text_hash,
            "text": str(text or "")[:500],
            "context": _slim_context(context),
            "triggers": [{
                "time": now, "penalty": round(penalty, 2), "requested": round(penalty, 2),
                "reason": reason, "text_hash": text_hash, "source": source,
                "text": str(text or "")[:500],
            }],
            "count": 1,
            "relation": dict(relation or {}),
            "threshold": dict(threshold or {}),
            "appeal": "none",
            "restored": False,
        }
        entries.append(entry)
        self.data["entries"] = entries[-1500:]
        self._save()
        return actual, entry

    def get(self, entry_id: str) -> dict | None:
        for entry in self.data.get("entries") or []:
            if str(entry.get("id")) == str(entry_id):
                return dict(entry)
        return None

    def list(self, qq: str = "", limit: int = 100) -> list[dict]:
        rows = list(self.data.get("entries") or [])
        if qq:
            rows = [e for e in rows if str(e.get("qq")) == str(qq)]
        # 返回副本：列表接口会移除 text/context 以避免把大字段带给前端；
        # 如果直接返回内部 dict，会把内存里的原文也一起删掉，详情就再也读不到。
        return [dict(entry) for entry in reversed(rows[-max(1, min(500, limit)):])]

    def stats(self) -> dict:
        """全量账本汇总：总扣分、待处理申诉与已恢复金额，供面板展示。"""
        entries = list(self.data.get("entries") or [])
        total = sum(float(e.get("penalty") or 0) for e in entries)
        restored = sum(float(e.get("penalty") or 0) for e in entries if e.get("restored"))
        pending = sum(1 for e in entries if e.get("appeal") == "pending")
        by_reason: dict[str, float] = {}
        for e in entries:
            reason = str(e.get("reason") or "未注明").strip() or "未注明"
            by_reason[reason] = round(by_reason.get(reason, 0.0) + float(e.get("penalty") or 0), 2)
        top_reasons = sorted(by_reason.items(), key=lambda kv: -kv[1])[:5]
        day_key = time.strftime("%Y-%m-%d")
        today = [e for e in entries if time.strftime("%Y-%m-%d", time.localtime(int(e.get("time") or 0))) == day_key]
        return {
            "count": len(entries),
            "total_penalty": round(total, 2),
            "restored_count": sum(1 for e in entries if e.get("restored")),
            "restored_penalty": round(restored, 2),
            "pending": pending,
            "today_count": len(today),
            "today_penalty": round(sum(float(e.get("penalty") or 0) for e in today), 2),
            "top_reasons": [{"reason": reason, "penalty": value} for reason, value in top_reasons],
        }

    def reason_feedback(self, reason: str) -> dict:
        """统计某原因近期的人工审核结果：accepted=误判恢复，rejected=维持扣分。"""
        cutoff = time.time() - FEEDBACK_WINDOW_DAYS * 86400
        accepted = rejected = 0
        last_note = ""
        for e in self.data.get("entries") or []:
            if int(e.get("time") or 0) < cutoff:
                continue
            if str(e.get("reason") or "") != str(reason):
                continue
            if e.get("appeal") == "accepted":
                accepted += 1
                last_note = str(e.get("appeal_note") or "")
            elif e.get("appeal") == "rejected":
                rejected += 1
        return {
            "reason": str(reason),
            "accepted": accepted,
            "rejected": rejected,
            "window_days": FEEDBACK_WINDOW_DAYS,
            "last_note": last_note[:200],
        }

    def backfill_from_memory(self, memory_dir: Path, memory_lookup) -> int:
        """用持久记忆回填旧条目缺失的触发原文。

        旧条目只有 text_hash（对原文的 SHA-1 前 16 位）；扫描记忆文件里
        每条用户发言，按同样哈希命中即回填 text/context。
        返回回填条数。
        """
        import hashlib
        memory_dir = Path(memory_dir)
        pending_hash = {}  # hash -> (text, context_summary)
        for kind in ("group", "user"):
            directory = memory_dir / kind
            if not directory.is_dir():
                continue
            for path in directory.glob("*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                for turn in (data.get("pending") or []):
                    if not isinstance(turn, dict) or turn.get("role") != "user":
                        continue
                    content = str(turn.get("content") or "").strip()
                    if not content:
                        continue
                    h = hashlib.sha1(content.encode("utf-8")).hexdigest()[:16]
                    if h not in pending_hash:
                        pending_hash[h] = {'text': content[:500], 'archive': None}
                for fact in (data.get("facts") or []):
                    if isinstance(fact, str) and len(fact) >= 4:
                        h = hashlib.sha1(fact.encode("utf-8")).hexdigest()[:16]
                        if h not in pending_hash:
                            pending_hash[h] = {'text': fact[:500], 'archive': None}
        # 旧版 datadetail 原文按天保存为 JSON 字符串数组；它们可能比记忆更完整。
        raw_root = memory_dir.parents[1] / "datadetail" / "data" / "raw"
        if raw_root.is_dir():
            for group_dir in raw_root.iterdir():
                if not group_dir.is_dir():
                    continue
                for path in group_dir.glob("*.json"):
                    try:
                        rows = json.loads(path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    if not isinstance(rows, list):
                        continue
                    for index, content in enumerate(rows):
                        if not isinstance(content, str) or not content.strip():
                            continue
                        content = content.strip()
                        h = hashlib.sha1(content.encode("utf-8")).hexdigest()[:16]
                        archive = {
                            'file': str(path.relative_to(raw_root)),
                            'before': [str(x)[:300] for x in rows[max(0, index - 4):index] if isinstance(x, str)],
                            'after': [str(x)[:300] for x in rows[index + 1:index + 5] if isinstance(x, str)],
                        }
                        if h not in pending_hash:
                            pending_hash[h] = {'text': content[:500], 'archive': archive}
                        elif pending_hash[h].get('archive') is None:
                            pending_hash[h]['archive'] = archive
        filled = 0
        with self.lock:
            for entry in self.data.get("entries") or []:
                target = str(entry.get("text_hash") or "")
                hit = pending_hash.get(target)
                if not hit:
                    continue
                changed = False
                if not (entry.get("text") or "").strip():
                    entry["text"] = hit['text']
                    entry["backfilled"] = True
                    changed = True
                if not entry.get("context") and not entry.get("archive_context") and hit.get('archive'):
                    entry["archive_context"] = hit['archive']
                    changed = True
                if changed:
                    filled += 1
            if filled:
                self._save()
        return filled

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"[\W_]+", "", str(text or "").lower())

    def _learn_exception(self, entry: dict) -> None:
        text = str(entry.get("text") or "").strip()
        if not text:
            return
        exceptions = self.data.setdefault("learned_exceptions", [])
        identity = (str(entry.get("reason") or ""), self._hash(text))
        if any((str(x.get("reason") or ""), str(x.get("text_hash") or "")) == identity for x in exceptions):
            return
        exceptions.append({
            "reason": str(entry.get("reason") or ""),
            "text": text[:500],
            "text_hash": self._hash(text),
            "qq": str(entry.get("qq") or ""),
            "time": int(time.time()),
            "source_entry": str(entry.get("id") or ""),
        })
        self.data["learned_exceptions"] = exceptions[-200:]

    def _learn_anomaly(self, entry: dict, kind: str = "normal", corrected_reason: str = "") -> None:
        text = str(entry.get("text") or "").strip()
        if not text:
            return
        anomalies = self.data.setdefault("learned_anomalies", [])
        normalized_reason = str(corrected_reason or entry.get("reason") or "").strip()[:80]
        identity = (str(entry.get("reason") or ""), self._hash(text), normalized_reason)
        if any((str(x.get("reason") or ""), str(x.get("text_hash") or ""), str(x.get("corrected_reason") or "")) == identity for x in anomalies):
            return
        anomalies.append({
            "reason": str(entry.get("reason") or ""),
            "corrected_reason": normalized_reason,
            "anomaly_kind": "type" if kind == "type" else "normal",
            "text": text[:500],
            "text_hash": self._hash(text),
            "qq": str(entry.get("qq") or ""),
            "time": int(time.time()),
            "source_entry": str(entry.get("id") or ""),
        })
        self.data["learned_anomalies"] = anomalies[-200:]

    def corrected_reason(self, reason: str, text: str = "") -> str:
        """类型异常学习：相似表达返回人工确认过的正确类型。"""
        raw = str(text or "").strip()
        if not raw:
            return ""
        target = self._normalize_text(raw)
        raw_hash = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        for panel in self.data.get("learned_anomalies") or []:
            if str(panel.get("anomaly_kind") or "") != "type":
                continue
            if str(panel.get("reason") or "") != str(reason or ""):
                continue
            corrected = str(panel.get("corrected_reason") or "").strip()
            if not corrected:
                continue
            if raw_hash == str(panel.get("text_hash") or ""):
                return corrected
            if len(target) < 4:
                continue
            learned = self._normalize_text(panel.get("text") or "")
            if len(learned) >= 4 and difflib.SequenceMatcher(None, target, learned).ratio() >= 0.86:
                return corrected
        return ""

    def should_suppress(self, reason: str, text: str = "") -> bool:
        """人工审核反哺：原因级抑制，以及单次申诉成功后的相似表达抑制。"""
        feedback = self.reason_feedback(reason)
        if feedback["accepted"] >= 2 and feedback["accepted"] > feedback["rejected"]:
            return True
        raw = str(text or "").strip()
        if not raw:
            return False
        target = self._normalize_text(raw)
        raw_hash = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        # 已被人工确认为异常的表达不能被“误判样本”误伤而跳过。
        for panel in self.data.get("learned_anomalies") or []:
            if str(panel.get("reason") or "") != str(reason or ""):
                continue
            if raw_hash == str(panel.get("text_hash") or ""):
                return False
            if len(target) < 4:
                continue
            learned_anomaly = self._normalize_text(panel.get("text") or "")
            if len(learned_anomaly) >= 4 and difflib.SequenceMatcher(None, target, learned_anomaly).ratio() >= 0.86:
                return False
        for panel in self.data.get("learned_exceptions") or []:
            if str(panel.get("reason") or "") != str(reason or ""):
                continue
            if raw_hash == str(panel.get("text_hash") or ""):
                return True
            if len(target) < 4:
                continue
            learned = self._normalize_text(panel.get("text") or "")
            if len(learned) >= 4 and difflib.SequenceMatcher(None, target, learned).ratio() >= 0.86:
                return True
        return False

    def set_anomaly(self, entry_id: str, kind: str = "normal", corrected_reason: str = "") -> dict | None:
        """修正已认定异常的类型；手误填错类型后可重新学习。"""
        for entry in self.data.get("entries") or []:
            if str(entry.get("id")) != str(entry_id):
                continue
            if entry.get("appeal") != "rejected":
                return None
            self.data["learned_anomalies"] = [
                row for row in (self.data.get("learned_anomalies") or [])
                if str(row.get("source_entry") or "") != str(entry_id)
            ]
            entry["anomaly_kind"] = "type" if kind == "type" else "normal"
            entry["corrected_reason"] = str(corrected_reason or "").strip()[:80]
            entry["resolution"] = "anomaly"
            self._learn_anomaly(entry, entry["anomaly_kind"], entry["corrected_reason"])
            self._save()
            return dict(entry)
        return None

    def appeal(self, entry_id: str, note: str = "") -> dict | None:
        for entry in self.data.get("entries") or []:
            if str(entry.get("id")) == str(entry_id):
                if entry.get("appeal") == "accepted":
                    return None
                entry["appeal"] = "pending"
                entry["appeal_note"] = str(note or "")[:300]
                entry["appeal_time"] = int(time.time())
                self._save()
                return dict(entry)
        return None

    def resolve(self, entry_id: str, accepted: bool, kind: str = "normal",
                corrected_reason: str = "") -> dict | None:
        for entry in self.data.get("entries") or []:
            if str(entry.get("id")) == str(entry_id):
                if entry.get("appeal") in ("accepted", "rejected"):
                    return None
                entry["appeal"] = "accepted" if accepted else "rejected"
                entry["resolved_time"] = int(time.time())
                entry["restored"] = bool(accepted)
                if accepted:
                    entry["resolution"] = "appeal_accepted"
                    self._learn_exception(entry)
                else:
                    entry["resolution"] = "anomaly"
                    entry["anomaly_kind"] = "type" if kind == "type" else "normal"
                    entry["corrected_reason"] = str(corrected_reason or "").strip()[:80]
                    self._learn_anomaly(entry, entry["anomaly_kind"], entry["corrected_reason"])
                self._save()
                return dict(entry)
        return None
