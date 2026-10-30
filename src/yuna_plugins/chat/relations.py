"""关系图谱：用户之间的称呼与关系（谁认识谁、谁叫谁什么）。

数据来源：
* users.json 的 aliases 导入（A 给 B 设置的称呼）；
* 管理面板手动增删改；
* （后续）记忆固化时从对话中抽取。

用途：群聊提示词注入「本群人物关系」，避免张冠李戴；面板可视化。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

KINDS = {"friend": "朋友", "address": "称呼", "couple": "亲密", "family": "家人", "rival": "不对付",
          "colleague": "同学/同事", "online": "网友", "other": "其他"}


def _pair(a: str, b: str) -> tuple[str, str]:
    a, b = str(a or ""), str(b or "")
    return (a, b) if a <= b else (b, a)


class RelationStore:
    def __init__(self, path: Path, max_edges: int = 2000):
        self.path = Path(path)
        self.max_edges = max_edges
        self.lock = threading.RLock()
        self.data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "edges": []}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("edges"), list):
                raw["edges"] = [e for e in raw["edges"] if isinstance(e, dict) and e.get("a") and e.get("b")]
                return raw
        except (OSError, ValueError):
            pass
        return {"version": 1, "edges": []}

    def _save_locked(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def set_edge(self, a: str, b: str, *, kind: str = "other", label: str = "", source: str = "manual") -> dict:
        if str(a) == str(b) or not str(a).strip() or not str(b).strip():
            raise ValueError("关系两端不能是同一人或为空")
        kind = kind if kind in KINDS else "other"
        a, b = _pair(a, b)
        edge = {
            "a": a, "b": b, "kind": kind,
            "label": str(label or "").strip()[:120],
            "source": str(source or "manual")[:16],
            "time": int(time.time()),
        }
        with self.lock:
            edges = [e for e in self.data["edges"] if _pair(e["a"], e["b"]) != (a, b)]
            edges.append(edge)
            self.data["edges"] = edges[-self.max_edges:]
            self._save_locked()
        return edge

    def remove_edge(self, a: str, b: str) -> bool:
        key = _pair(a, b)
        with self.lock:
            before = len(self.data["edges"])
            self.data["edges"] = [e for e in self.data["edges"] if _pair(e["a"], e["b"]) != key]
            removed = before != len(self.data["edges"])
            if removed:
                self._save_locked()
        return removed

    def edges_of(self, qq: str = "") -> list[dict]:
        with self.lock:
            rows = [dict(e) for e in self.data["edges"]]
        qq = str(qq or "")
        if qq:
            rows = [e for e in rows if qq in (e["a"], e["b"])]
        return list(reversed(rows))

    def import_aliases(self, users: dict) -> int:
        """从 users.json 的 aliases 导入称呼关系（A 给 B 设置的称呼）。幂等：已存在同端边不覆盖。"""
        existing = {_pair(e["a"], e["b"]) for e in self.data["edges"]}
        added = 0
        for qq, info in (users or {}).items():
            aliases = (info or {}).get("aliases") if isinstance(info, dict) else None
            for target, alias in (aliases or {}).items():
                if not alias:
                    continue
                # 跳过自指/无效目标（历史数据里存在自己给自己设称呼的情况）
                if str(qq) == str(target) or not str(target).strip():
                    continue
                if _pair(qq, target) in existing:
                    continue
                try:
                    self.set_edge(qq, target, kind="address",
                                  label=f"{qq} 称呼 {target} 为「{alias}」", source="aliases")
                    existing.add(_pair(qq, target))
                    added += 1
                except ValueError:
                    continue
        return added

    def prompt_hint(self, member_qqs: set, nickname_of, limit: int = 8) -> str:
        """生成群聊注入的人物关系提示（只取两端都是群成员的边）。"""
        members = {str(q) for q in (member_qqs or set())}
        if len(members) < 2:
            return ""
        rows = []
        for e in self.data["edges"]:
            if e["a"] in members and e["b"] in members:
                na, nb = nickname_of(e["a"]), nickname_of(e["b"])
                label = e.get("label") or ""
                if e.get("kind") == "address" and " 称呼 " not in label:
                    label = f"{na} 称呼 {nb}：{label}" if label else f"{na} 和 {nb} 有称呼关系"
                elif not label:
                    label = f"{na} 和 {nb}：{KINDS.get(e.get('kind'), '相关')}"
                rows.append(label)
                if len(rows) >= max(1, limit):
                    break
        return "；".join(rows)

    def infer_from_memory(self, memory_dir, users: dict) -> dict:
        """从群聊记忆自动推断用户关系（幂等，source='infer' 的边每次重建）。

        两个数据源：
        * 滚动 pending（固化前）：共现/提及统计（见 _scan_pending）。
        * 持久 episodes / facts（固化后长期保留）：一条记忆同时提到两个已知昵称
          → 直接建边（label 取记忆摘要）。这解决了"固化清空 pending 后推断边消失"的问题。
        """
        nick_index = self._nick_index(users)
        pairs, mem_edges = self._scan_memory_files(memory_dir, nick_index)
        return self._rebuild_infer_edges(pairs, mem_edges, memory_dir)

    def _nick_index(self, users: dict) -> dict:
        index = {}
        for qq, info in (users or {}).items():
            if isinstance(info, dict):
                nick = str(info.get("nickname") or "").strip()
                if nick and len(nick) >= 2:
                    index[nick] = str(qq)
        return index

    def _scan_memory_files(self, memory_dir, nick_index: dict):
        import re as _re
        pairs: dict[tuple, dict] = {}
        mem_edges: dict[tuple, dict] = {}

        def bump(key, field):
            slot = pairs.setdefault(key, {"co": 0, "mention": 0})
            slot[field] += 1

        def remember_from_text(text, owner_qq):
            hits = [qq for nick, qq in nick_index.items() if nick in text]
            hits = [q for q in dict.fromkeys(hits) if q != owner_qq][:3]
            for i in range(len(hits)):
                for j in range(i + 1, len(hits)):
                    key = _pair(hits[i], hits[j])
                    slot = mem_edges.setdefault(key, {"count": 0, "label": ""})
                    slot["count"] += 1
                    if not slot["label"]:
                        clean = _re.sub(r"\s+", " ", str(text)).strip()
                        slot["label"] = clean[:80]

        try:
            base = Path(memory_dir)
            files = []
            for kind in ("group", "user"):
                d = base / kind
                if d.is_dir():
                    files.extend(d.glob("*.json"))
        except OSError:
            files = []
        for path in files:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            # 1) 持久记忆：episodes + facts 双昵称共现
            for ep in (data.get("episodes") or []):
                if isinstance(ep, dict):
                    remember_from_text(str(ep.get("text") or ""), "")
                elif isinstance(ep, str):
                    remember_from_text(ep, "")
            for fact in (data.get("facts") or []):
                remember_from_text(fact if isinstance(fact, str) else str(getattr(fact, "get", lambda *_: "")("text") or ""), "")
            # 2) 滚动 pending：共现/提及
            turns = [t for t in (data.get("pending") or []) if isinstance(t, dict) and t.get("role") == "user"]
            turns.sort(key=lambda t: float(t.get("time") or 0))
            window: list[dict] = []
            for turn in turns:
                ts = float(turn.get("time") or 0)
                qq = str(turn.get("qq") or "")
                if not qq:
                    continue
                while window and ts - float(window[0]["time"] or 0) > 600:
                    window.pop(0)
                for other in window:
                    oqq = str(other.get("qq") or "")
                    if oqq and oqq != qq:
                        bump(_pair(qq, oqq), "co")
                window.append({"qq": qq, "time": ts})
                text = str(turn.get("content") or "")
                for nick, target in nick_index.items():
                    if target != qq and nick in text:
                        bump(_pair(qq, target), "mention")
        return pairs, mem_edges

    def _rebuild_infer_edges(self, pairs, mem_edges, memory_dir):
        files = 0
        try:
            base = Path(memory_dir)
            files = sum(1 for kind in ("group", "user") if (base / kind).is_dir()
                        for _ in (base / kind).glob("*.json"))
        except OSError:
            pass
        with self.lock:
            kept = [e for e in self.data["edges"] if e.get("source") != "infer"]
        self.data["edges"] = kept
        added = 0
        seen = set()
        # 记忆共现边优先（持久、带语义摘要）
        for key, slot in sorted(mem_edges.items(), key=lambda kv: -kv[1]["count"]):
            if slot["count"] >= 1 and key not in seen:
                seen.add(key)
                try:
                    self.set_edge(key[0], key[1], kind=self._kind_from_label(slot["label"]),
                                  label=f"共同出现在记忆中：{slot['label']}", source="infer")
                    added += 1
                except ValueError:
                    continue
        for key, slot in sorted(pairs.items(), key=lambda kv: -(kv[1]["co"] * 2 + kv[1]["mention"])):
            if key in seen:
                continue
            co, mention = slot["co"], slot["mention"]
            if co >= 3:
                seen.add(key)
                try:
                    self.set_edge(key[0], key[1], kind="friend",
                                  label=f"常一起聊天（同群同时段共现 {co} 次）", source="infer")
                    added += 1
                except ValueError:
                    continue
            elif mention >= 2:
                seen.add(key)
                try:
                    self.set_edge(key[0], key[1], kind="online",
                                  label=f"一个常提到另一个（发言提及 {mention} 次）", source="infer")
                    added += 1
                except ValueError:
                    continue
            if added >= 60:
                break
        return {"added": added, "scanned_files": files, "total": self.stats()["edges"]}

    @staticmethod
    def _kind_from_label(text: str) -> str:
        text = str(text or "")
        if any(w in text for w in ("同学", "同事", "上班", "学校", "大学", "高中", "初中")):
            return "colleague"
        if any(w in text for w in ("情侣", "cp", "CP", "女朋友", "男朋友", "在一起")):
            return "couple"
        if any(w in text for w in ("吵架", "互怼", "对线", "翻脸", "互骂")):
            return "rival"
        if any(w in text for w in ("家人", "哥哥", "姐姐", "弟弟", "妹妹", "爸妈")):
            return "family"
        return "online"
        pairs: dict[tuple, dict] = {}
        mentions: dict[tuple, int] = {}
        nick_index = {}
        for qq, info in (users or {}).items():
            if isinstance(info, dict):
                nick = str(info.get("nickname") or "").strip()
                if nick and len(nick) >= 2:
                    nick_index[nick] = str(qq)

        def bump(key, field):
            slot = pairs.setdefault(key, {"co": 0, "mention": 0})
            slot[field] += 1

        try:
            group_dir = Path(memory_dir) / "group"
            files = list(group_dir.glob("*.json")) if group_dir.is_dir() else []
        except OSError:
            files = []
        for path in files:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            turns = [t for t in (data.get("pending") or []) if isinstance(t, dict) and t.get("role") == "user"]
            turns.sort(key=lambda t: float(t.get("time") or 0))
            window: list[dict] = []
            for turn in turns:
                ts = float(turn.get("time") or 0)
                qq = str(turn.get("qq") or "")
                if not qq:
                    continue
                while window and ts - float(window[0]["time"] or 0) > 600:
                    window.pop(0)
                for other in window:
                    oqq = str(other.get("qq") or "")
                    if oqq and oqq != qq:
                        bump(_pair(qq, oqq), "co")
                window.append({"qq": qq, "time": ts})
                text = str(turn.get("content") or "")
                for nick, target in nick_index.items():
                    if target != qq and nick in text:
                        bump(_pair(qq, target), "mention")

        # 重建推断边（保留 manual/aliases/consolidation 来源）
        with self.lock:
            kept = [e for e in self.data["edges"] if e.get("source") != "infer"]
        self.data["edges"] = kept
        added = 0
        for key, slot in sorted(pairs.items(), key=lambda kv: -(kv[1]["co"] * 2 + kv[1]["mention"])):
            co, mention = slot["co"], slot["mention"]
            if co >= 3:
                try:
                    self.set_edge(key[0], key[1], kind="friend",
                                  label=f"常一起聊天（同群同时段共现 {co} 次）", source="infer")
                    added += 1
                except ValueError:
                    continue
            elif mention >= 2:
                try:
                    self.set_edge(key[0], key[1], kind="online",
                                  label=f"一个常提到另一个（发言提及 {mention} 次）", source="infer")
                    added += 1
                except ValueError:
                    continue
            if added >= 60:
                break
        return {"added": added, "scanned_files": len(files), "total": self.stats()["edges"]}

    def apply_consolidation(self, rows: list) -> int:
        """记忆固化时写入模型推断的语义关系（同学/情侣/同事等）。"""
        added = 0
        for row in (rows or [])[:12]:
            if not isinstance(row, dict):
                continue
            a, b = str(row.get("a") or ""), str(row.get("b") or "")
            kind = str(row.get("kind") or "other")
            label = str(row.get("label") or "").strip()[:120]
            if not a.isdigit() or not b.isdigit() or a == b or not label:
                continue
            try:
                self.set_edge(a, b, kind=kind if kind in KINDS else "other",
                              label=label, source="consolidation")
                added += 1
            except ValueError:
                continue
        return added

    def stats(self) -> dict:
        with self.lock:
            edges = list(self.data["edges"])
        nodes = set()
        for e in edges:
            nodes.update((e["a"], e["b"]))
        by_kind: dict[str, int] = {}
        for e in edges:
            by_kind[e.get("kind") or "other"] = by_kind.get(e.get("kind") or "other", 0) + 1
        return {"edges": len(edges), "nodes": len(nodes), "by_kind": by_kind}
