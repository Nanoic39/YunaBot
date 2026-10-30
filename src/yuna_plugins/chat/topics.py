"""短期话题状态：从最近消息提取当前话题、未解决问题与纠正。"""

from __future__ import annotations

import json
import re
import time

_CORRECTION = re.compile(r"(不是|其实|说错|记错|纠正|准确地说|应该是|改成)")
_QUESTION = re.compile(r"[?？]|吗|呢|怎么|为什么|什么|谁|哪|多少|如何")


def build_topic_hint(recent: list | None, current_text: str = "") -> str:
    recent = [m for m in (recent or []) if isinstance(m, dict)]
    if not recent and not current_text:
        return ""
    users = [m for m in recent if str(m.get("role")) == "user" and str(m.get("content") or "").strip()]
    if current_text.strip():
        users.append({"role": "user", "content": str(current_text).strip()})
    if not users:
        return ""
    topic = ""
    for item in reversed(users):
        text = str(item.get("content") or "").strip()
        if len(text) >= 4 and not text.startswith(("/", "!", "！")):
            topic = text[:72]
            break
    questions = []
    corrections = []
    for item in users[-8:]:
        text = str(item.get("content") or "").strip()
        if _QUESTION.search(text) and len(text) <= 120:
            questions.append(text[:72])
        if _CORRECTION.search(text):
            corrections.append(text[:80])
    if not topic and not questions and not corrections:
        return ""
    lines = ["【短期话题状态（只参考最近几条，不是长期事实）】"]
    if topic:
        lines.append(f"当前在聊：{topic}")
    if questions:
        lines.append("最近的未解决问题：" + "；".join(dict.fromkeys(questions[-2:])))
    if corrections:
        lines.append("最近出现过纠正：" + "；".join(dict.fromkeys(corrections[-2:])))
    lines.append("回复时优先接当前话题；不要把旧问题当成新问题，也不要把玩笑当成已确认事实。")
    return "\n".join(lines)


class TopicStore:
    """按会话保存少量话题线程；只保留摘要、参与者和未解决问题，不复制完整聊天。"""

    def __init__(self, path):
        self.path = path
        self.data = self._load()

    def _load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
            if isinstance(raw, dict):
                raw.setdefault("sessions", {})
                return raw
        except Exception:
            pass
        return {"sessions": {}}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    @staticmethod
    def _title(text: str) -> str:
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        return text[:42] or "新话题"

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(re.findall(r"[\u4e00-\u9fff]{2,}", str(text or ""))).union(
            set(re.findall(r"[A-Za-z0-9_]{2,}", str(text or "")))
        )

    def _session(self, key: str) -> dict:
        sessions = self.data.setdefault("sessions", {})
        session = sessions.get(str(key))
        if not isinstance(session, dict):
            session = {"threads": [], "active": "", "updated": 0}
            sessions[str(key)] = session
        session.setdefault("threads", [])
        return session

    def observe(self, key: str, qq: str, text: str, *, is_bot: bool = False) -> dict | None:
        text = str(text or "").strip()
        if not text:
            return None
        session = self._session(key)
        now = int(time.time())
        threads = [t for t in session["threads"] if isinstance(t, dict)]
        tokens = self._tokens(text)
        active_id = str(session.get("active") or "")
        active = next((t for t in threads if str(t.get("id")) == active_id), None)
        best = None
        best_score = 0
        for thread in threads:
            if now - int(thread.get("updated") or 0) > 1800:
                continue
            score = len(tokens & self._tokens(thread.get("title") + " " + " ".join(thread.get("keywords") or [])))
            if score > best_score:
                best, best_score = thread, score
        if active is None or (best is not None and best is not active and best_score >= 2):
            active = best or active
        if active is None or now - int(active.get("updated") or 0) > 1800:
            active = {
                "id": f"{now}-{len(threads)+1}",
                "title": self._title(text),
                "keywords": list(tokens)[:8],
                "participants": [],
                "open_questions": [],
                "corrections": [],
                "updated": now,
                "created": now,
            }
            threads.append(active)
            session["threads"] = threads[-8:]
        if not is_bot and str(qq) not in active.setdefault("participants", []):
            active["participants"].append(str(qq))
        if _QUESTION.search(text) and not is_bot:
            active.setdefault("open_questions", []).append({"qq": str(qq), "text": text[:80], "time": now})
            active["open_questions"] = active["open_questions"][-3:]
        if _CORRECTION.search(text):
            active.setdefault("corrections", []).append({"qq": str(qq), "text": text[:100], "time": now})
            active["corrections"] = active["corrections"][-3:]
        if not is_bot and len(tokens) >= 2:
            active["keywords"] = list(dict.fromkeys(list(tokens) + list(active.get("keywords") or [])))[:10]
        active["updated"] = now
        session["active"] = str(active.get("id"))
        session["updated"] = now
        self._save()
        return active

    def prompt_block(self, key: str) -> str:
        session = self._session(key)
        active_id = str(session.get("active") or "")
        active = next((t for t in session.get("threads", []) if str(t.get("id")) == active_id), None)
        if not active:
            return ""
        lines = ["【当前话题线程（短时状态，不是长期事实）】", f"话题：{active.get('title')}"]
        participants = active.get("participants") or []
        if participants:
            lines.append("参与者：" + "、".join(f"QQ{q}" for q in participants[:4]))
        questions = active.get("open_questions") or []
        if questions:
            lines.append("未解决：" + "；".join(str(q.get("text") or "")[:60] for q in questions[-2:]))
        corrections = active.get("corrections") or []
        if corrections:
            lines.append("最近纠正：" + "；".join(str(c.get("text") or "")[:70] for c in corrections[-2:]))
        lines.append("只接当前线程；不要把其他线程或旧话题硬塞进这条回复。")
        return "\n".join(lines)
