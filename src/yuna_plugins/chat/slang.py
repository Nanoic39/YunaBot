"""长期梗/群黑话的语义记忆库。

只保存词条、释义、用法和低敏来源摘要；不保存完整聊天记录或用户隐私。
这里不负责“用户这句话是不是梗”，也不做关键词命中；是否玩梗由 meme.py 的上下文语义分析决定。
候选词默认不进入提示词，经反馈或人工确认后才转为 active。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path

_KINDS = {"internet", "jargon", "speech"}
_RISKS = {"low", "edgy", "offensive"}
_TERM_RE = re.compile(r"^[\w\u4e00-\u9fff·\-+～~]{1,24}$")
# 广泛流通、跨年份仍有人用的基础梗，不按单次学习热度清理
_KNOWN_POPULAR = {
    "yyds", "摆烂", "破防", "栓q", "内卷", "显眼包", "city不city", "dbq", "nsdd",
    "emo", "硬控", "叠甲", "背刺", "回手掏", "0帧起手", "夯到拉", "丝瓜汤", "典",
    "绷不住", "乐", "草", "哈基米", "遥遥领先", "家人们", "尊嘟假嘟", "泰裤辣",
    "老六", "拿捏", "松弛感", "班味", "citywalk", "搭子", "纯爱战神", "牛马",
    "打工人", "i人", "e人", "社恐", "社牛", "躺平", "内耗", "抽象", "赢麻了",
    "麻了", "绝绝子", "无语子", "退退退", "电子榨菜", "科技与狠活", "海克斯科技",
    "鼠鼠", "小丑", "乐子人", "整活", "好活", "烂活", "开摆", "润", "摸鱼",
    "画饼", "pua", "cpu", "情绪价值", "边界感", "钝感力", "吗喽", "卡皮巴拉",
    "水豚", "猫meme", "曼波", "叮咚鸡", "鸡你太美", "坤坤", "小黑子", "ikun",
    "你干嘛", "练习生", "两年半", "电子木鱼", "赛博", "功德", "电子宠物",
}


def _now() -> int:
    return int(time.time())


def _clean(value, limit=120) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


# 基础常用梗：不依赖某一次联网结果，避免清理/断网后连最常见说法都不认识。
_SEED_COMMON = {
    "yyds": ("永远的神，夸人或事物非常厉害", "夸赞、玩梗，不用于严肃评价"),
    "内卷": ("过度竞争、互相消耗", "聊学习、工作、行业竞争"),
    "emo": ("情绪低落、突然伤感", "自嘲或关心别人情绪"),
    "绝绝子": ("太绝了，夸张地夸赞", "轻松夸赞，语气偏网络化"),
    "躺平": ("不再拼命竞争，放低欲望", "自嘲、聊工作生活压力"),
    "摸鱼": ("上班/上课偷偷偷懒", "日常自嘲"),
    "打工人": ("上班族的自称", "自嘲工作"),
    "牛马": ("被使唤、辛苦干活的人", "自嘲工作劳累"),
    "老六": ("不按常理出牌、爱阴人的人", "游戏或朋友间调侃"),
    "哈基米": ("可爱小猫/小动物的萌称", "发猫图、卖萌"),
    "尊嘟假嘟": ("真的假的，卖萌式疑问", "轻松反问"),
    "泰裤辣": ("太酷啦的谐音", "夸张夸赞"),
    "纯爱战神": ("坚定相信纯爱的人", "感情话题调侃"),
    "社恐": ("害怕社交", "自嘲社交状态"),
    "社牛": ("社交能力很强、不怕生", "调侃或夸人"),
    "抽象": ("离谱、难以理解", "吐槽离谱内容"),
    "乐子人": ("看热闹、找乐子的人", "看热闹时自嘲"),
    "整活": ("搞节目效果、做离谱有趣的事", "群聊玩梗"),
    "电子榨菜": ("吃饭时看的视频/直播", "聊下饭内容"),
    "科技与狠活": ("调侃食品添加剂或过度加工", "食品安全话题"),
    "鼠鼠": ("自嘲卑微、普通的小人物", "自嘲"),
    "卡皮巴拉": ("水豚，情绪稳定的代名词", "表情包、松弛感"),
    "吗喽": ("猴子的谐音，自嘲打工/疲惫", "自嘲"),
    "发疯文学": ("用夸张密集的文字表达情绪", "玩梗式发疯"),
    "情绪价值": ("让人感到被理解、被支持的价值", "关系、聊天话题"),
    "松弛感": ("不紧绷、从容的状态", "夸人或自嘲"),
    "班味": ("上班后疲惫、社畜感", "自嘲工作状态"),
    "搭子": ("一起做某件事的轻量伙伴", "饭搭子、旅游搭子"),
    "citywalk": ("城市里随意散步", "聊周末、休闲"),
    "赢麻了": ("赢太多、赢到麻木", "夸张庆祝或反讽"),
    "麻了": ("无语、无奈到麻木", "吐槽"),
    "退退退": ("用夸张动作赶走不好的东西", "玩梗式拒绝"),
    "雪糕刺客": ("看起来普通但价格很贵的雪糕", "吐槽高价商品"),
    "爷青回": ("爷的青春回来了", "怀旧"),
    "精神状态": ("调侃自己当前的心理状态", "自嘲"),
}


class SlangStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data = self._load()
        self._seed_common()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("entries"), dict):
                # 公网热梗只作为候选，避免模型把不确定词条直接说进群。
                for entry in data.get("entries", {}).values():
                    sources = entry.get("sources") or []
                    if (entry.get("status") == "active" and sources
                            and all(str(src).startswith("http") for src in sources)
                            and int(entry.get("count") or 0) <= 1
                            and float(entry.get("popularity") or 0) < 0.75):
                        entry["status"] = "candidate"
                return data
        except (OSError, ValueError):
            pass
        return {"version": 1, "entries": {}, "updated": 0}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["updated"] = _now()
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(self.path)

    @staticmethod
    def _key(term: str) -> str:
        return str(term or "").strip().lower()

    def _seed_common(self) -> None:
        # 只播种一次；清空或六个月自动清理后，不在重启时把旧基础词悄悄加回来。
        if self.data.get("seeded"):
            return
        now = _now()
        changed = False
        for term, (meaning, usage) in _SEED_COMMON.items():
            key = self._key(term)
            if key in self.data["entries"]:
                continue
            self.data["entries"][key] = {
                "term": term, "meaning": meaning, "usage": usage, "kind": "internet",
                "scope": "global", "risk": "low", "confidence": 0.95,
                "popularity": 0.9, "status": "active", "sources": ["builtin"],
                "examples": [], "count": 1, "first_seen": now, "last_seen": now,
            }
            changed = True
        self.data["seeded"] = True
        self._save()

    def learn_extraction(self, rows, *, group: str = "", source: str = "group") -> int:
        """把模型提取的词条写入记忆；返回新增/提升数量。"""
        if not isinstance(rows, list):
            return 0
        changed = 0
        now = _now()
        with self.lock:
            for row in rows:
                if not isinstance(row, dict):
                    continue
                term = _clean(row.get("term"), 24)
                meaning = _clean(row.get("meaning"), 160)
                usage = _clean(row.get("usage"), 160)
                if not term or not meaning or not _TERM_RE.fullmatch(term):
                    continue
                kind = str(row.get("kind") or "internet")
                if kind not in _KINDS:
                    kind = "internet"
                risk = str(row.get("risk") or "low")
                if risk not in _RISKS:
                    risk = "edgy"
                if risk == "offensive":
                    continue
                try:
                    confidence = max(0.0, min(1.0, float(row.get("confidence") or 0)))
                except (TypeError, ValueError):
                    confidence = 0.0
                raw_popularity = row.get("popularity")
                if raw_popularity in (None, ""):
                    popularity = confidence
                else:
                    try:
                        popularity = max(0.0, min(1.0, float(raw_popularity)))
                    except (TypeError, ValueError):
                        popularity = confidence
                scopes = row.get("scope") or ""
                if scopes == "global":
                    scope = "global"
                elif group and scopes in ("group", "群内", "", None):
                    scope = str(group)
                else:
                    scope = str(scopes or (group or "global"))
                # 对公网不确定词条更保守；群内重复使用过的词才更容易提升。
                threshold = 0.88 if source == "web" else 0.82
                key = self._key(term)
                entry = self.data["entries"].get(key)
                if entry is None:
                    entry = {
                        "term": term, "meaning": meaning, "usage": usage, "kind": kind,
                        "scope": scope, "risk": risk, "confidence": confidence,
                        "popularity": popularity,
                        # 从群聊/人工复核新学的长期记忆默认启用；只有公网来源保留候选门槛。
                        "status": "active" if source != "web" or (confidence >= threshold and popularity >= 0.55) else "candidate",
                        "sources": [], "examples": [], "count": 1,
                        "first_seen": now, "last_seen": now,
                    }
                    self.data["entries"][key] = entry
                    changed += 1
                else:
                    entry["count"] = int(entry.get("count") or 0) + 1
                    entry["last_seen"] = now
                    entry["confidence"] = max(float(entry.get("confidence") or 0), confidence)
                    entry["popularity"] = max(float(entry.get("popularity") or 0), popularity)
                    if meaning and len(meaning) > len(entry.get("meaning") or ""):
                        entry["meaning"] = meaning
                    if usage:
                        entry["usage"] = usage
                    if entry.get("status") != "blocked" and entry["confidence"] >= threshold:
                        entry["status"] = "active"
                    changed += 1
                if group:
                    entry["scope"] = scope
                source_ref = _clean(row.get("source") or "", 300)
                if source_ref and source_ref not in entry["sources"]:
                    entry["sources"] = (entry["sources"] + [source_ref])[-5:]
                example = _clean(row.get("example") or "", 240)
                if example:
                    entry["examples"] = (entry["examples"] + [example])[-4:]
            self._save()
        return changed

    def has(self, term: str) -> bool:
        return self._key(term) in self.data["entries"]

    def touch(self, term: str) -> bool:
        """真实对话里再次用到某个梗时刷新使用时间，供六个月清理判断。"""
        key = self._key(term)
        with self.lock:
            entry = self.data["entries"].get(key)
            if not entry:
                return False
            entry["last_seen"] = _now()
            self._save()
            return True

    def feedback(self, term: str, success: bool, note: str = "") -> dict | None:
        """根据后续对话效果调整语义置信度；不参与任何关键词判定。"""
        key = self._key(term)
        with self.lock:
            entry = self.data["entries"].get(key)
            if not entry:
                return None
            delta = 0.025 if success else -0.04
            entry["confidence"] = max(0.0, min(1.0, float(entry.get("confidence") or 0) + delta))
            entry["popularity"] = max(0.0, min(1.0, float(entry.get("popularity") or 0) + (0.01 if success else -0.015)))
            if success:
                entry["feedback_success"] = int(entry.get("feedback_success") or 0) + 1
            else:
                entry["feedback_wrong"] = int(entry.get("feedback_wrong") or 0) + 1
                if entry.get("status") == "active" and entry["confidence"] < 0.6:
                    entry["status"] = "candidate"
            entry["last_feedback"] = _now()
            if note:
                entry["last_feedback_note"] = _clean(note, 80)
            self._save()
            return dict(entry)

    def add_example(self, term: str, example: str) -> dict | None:
        key = self._key(term)
        value = _clean(example, 240)
        if not value:
            return None
        with self.lock:
            entry = self.data["entries"].get(key)
            if not entry:
                return None
            entry["examples"] = (list(entry.get("examples") or []) + [value])[-6:]
            self._save()
            return dict(entry)

    def update(self, term: str, *, status=None, meaning=None, usage=None, risk=None, scope=None) -> dict | None:
        key = self._key(term)
        with self.lock:
            entry = self.data["entries"].get(key)
            if not entry:
                return None
            if status in ("active", "candidate", "blocked"):
                entry["status"] = status
            if meaning is not None:
                entry["meaning"] = _clean(meaning, 160)
            if usage is not None:
                entry["usage"] = _clean(usage, 160)
            if risk in _RISKS:
                entry["risk"] = risk
            if scope is not None:
                entry["scope"] = _clean(scope, 80)
            self._save()
            return dict(entry)

    def clear_all(self) -> int:
        """清空长期语义记忆（含内置词），用于从干净状态重新学习。"""
        with self.lock:
            count = len(self.data.get("entries") or {})
            self.data["entries"] = {}
            self.data["seeded"] = True
            self._save()
            return count

    def prune_stale(self, *, days: int = 180, include_blocked: bool = False) -> dict:
        """删除超过指定天数没有在对话中使用/反馈过的梗。"""
        cutoff = _now() - max(30, int(days)) * 86400
        removed = []
        with self.lock:
            for key, entry in list(self.data.get("entries", {}).items()):
                if entry.get("status") == "blocked" and not include_blocked:
                    continue
                last = max(float(entry.get("last_seen") or 0), float(entry.get("last_feedback") or 0))
                if last <= cutoff:
                    removed.append(entry.get("term") or key)
                    del self.data["entries"][key]
            if removed:
                self._save()
        return {"removed": removed, "count": len(removed)}

    def prune(self, *, days: int = 14, min_confidence: float = 0.75,
              min_popularity: float = 0.55, include_active: bool = False,
              force: bool = False) -> dict:
        """清理长期只出现一次、低热度/低置信的候选词；默认不动 active。"""
        cutoff = _now() - max(1, int(days)) * 86400
        removed = []
        with self.lock:
            for key, entry in list(self.data["entries"].items()):
                if entry.get("status") == "blocked":
                    continue
                if str(entry.get("term") or key).lower() in _KNOWN_POPULAR:
                    continue
                # 旧数据没有热度字段时，不能仅凭单次出现就判定冷门。
                if entry.get("popularity") is None and not force:
                    continue
                if entry.get("status") == "active" and not include_active:
                    continue
                if int(entry.get("count") or 0) > 1:
                    continue
                if not force and int(entry.get("last_seen") or 0) > cutoff:
                    continue
                if not (force and include_active):
                    if (float(entry.get("confidence") or 0) >= min_confidence
                            and float(entry.get("popularity") or 0) >= min_popularity):
                        continue
                removed.append(entry.get("term") or key)
                del self.data["entries"][key]
            if removed:
                self._save()
        return {"removed": removed, "count": len(removed)}

    def remove(self, term: str) -> bool:
        with self.lock:
            if self.data["entries"].pop(self._key(term), None) is None:
                return False
            self._save()
            return True

    def list(self, status: str = "", limit: int = 200):
        rows = list(self.data.get("entries", {}).values())
        if status:
            rows = [e for e in rows if e.get("status") == status]
        rows.sort(key=lambda e: (e.get("last_seen", 0), e.get("confidence", 0)), reverse=True)
        return rows[:max(1, min(500, limit))]

    def stats(self):
        rows = list(self.data.get("entries", {}).values())
        return {
            "total": len(rows),
            "active": sum(1 for e in rows if e.get("status") == "active"),
            "candidate": sum(1 for e in rows if e.get("status") == "candidate"),
            "blocked": sum(1 for e in rows if e.get("status") == "blocked"),
            "updated": self.data.get("updated", 0),
        }
