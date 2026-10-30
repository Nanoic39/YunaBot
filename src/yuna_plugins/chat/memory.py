import ast
import json
import re
import time
from datetime import datetime
from pathlib import Path

from nonebot import logger
from src.agent.memory_retrieval import memory_terms, relevance


class MemoryManager:
    """按 QQ 号 / 群号分文件存储记忆，pending 缓冲到达阈值后动态固化"""

    def __init__(self, base_dir: Path, llm, config, assistant_name: str = "芸Yuna"):
        self.base_dir = base_dir
        self.llm = llm
        self.config = config
        self.assistant_name = assistant_name
        self._cache: dict[Path, dict] = {}

    # ---------- 读写 ----------
    def _path(self, kind: str, key: str) -> Path:
        return self.base_dir / kind / f"{key}.json"

    def _load(self, kind: str, key: str) -> dict:
        path = self._path(kind, key)
        if path in self._cache:
            return self._cache[path]
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        else:
            data = {}
        data.setdefault("facts", [])
        data.setdefault("episodes", [])
        data.setdefault("fact_entries", [])
        data.setdefault("corrections", [])
        data.setdefault("pending", [])
        data.setdefault("adaptive_turns", self.config.chat_memory_consolidate_min_turns)
        data.setdefault("summary", "")
        data.setdefault("last_consolidated", 0)
        # 修复历史脏数据：adaptive_turns 必须为正数，否则会退化成几乎每条都固化、
        # 上下文过少导致说话人归属错乱
        try:
            if int(data["adaptive_turns"]) <= 0:
                data["adaptive_turns"] = self.config.chat_memory_consolidate_min_turns
        except (TypeError, ValueError):
            data["adaptive_turns"] = self.config.chat_memory_consolidate_min_turns
        migrated = self._migrate_long_term(data)
        self._sync_fact_entries(data)
        cleaned = self._clean_empty_placeholders(data)
        self._cache[path] = data
        if migrated or cleaned:
            self._save(kind, key, data)
        return data

    @staticmethod
    def _clean_empty_placeholders(data: dict) -> bool:
        """清掉历史里由旁白清理残留的独立 [] / （） 占位，避免它们进入上下文。"""
        changed = False
        for entry in data.get('pending', []):
            if not isinstance(entry, dict) or str(entry.get('qq') or '').strip():
                continue
            content = str(entry.get('content') or '')
            cleaned = re.sub(r'(?m)^\s*[\[\]()（）{}【】]{1,8}\s*$', '', content)
            cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
            if cleaned != content:
                entry['content'] = cleaned
                changed = True
        for episode in data.get('episodes', []):
            if not isinstance(episode, dict):
                continue
            text = str(episode.get('text') or '')
            cleaned = re.sub(r'(?m)^\s*[\[\]()（）{}【】]{1,8}\s*$', '', text)
            cleaned = re.sub(r'\n{3,}', '\n\n', cleaned).strip()
            if cleaned != text:
                episode['text'] = cleaned
                changed = True
        return changed

    @staticmethod
    def _migrate_long_term(data: dict) -> bool:
        """把旧的单层 long_term 迁入 episodes（时间戳取当前），返回是否发生迁移。"""
        legacy = data.pop("long_term", None)
        if not legacy:
            return False
        now = time.time()
        for item in legacy:
            if isinstance(item, dict):
                text = (item.get("text") or "").strip()
                ts = item.get("time") or now
            elif isinstance(item, str):
                text = item.strip()
                ts = now
            else:
                continue
            if text:
                data["episodes"].append({"text": text, "time": ts})
        return True

    @staticmethod
    def _sync_fact_entries(data: dict) -> None:
        now = time.time()
        facts = [m for m in data.get("facts", []) if isinstance(m, str) and m.strip()]
        existing = {
            str(entry.get("text")): entry
            for entry in data.get("fact_entries", [])
            if isinstance(entry, dict) and entry.get("text")
        }
        merged = []
        for text in facts:
            entry = existing.get(text) or {
                "text": text, "source": "legacy", "time": now,
                "confidence": 0.5, "scope": "unknown",
            }
            entry.setdefault("source", "legacy")
            entry.setdefault("time", now)
            entry.setdefault("confidence", 0.5)
            entry.setdefault("scope", "unknown")
            merged.append(entry)
        data["fact_entries"] = merged[-500:]

    @staticmethod
    def _fact_meta(text: str, source: str, scope: str, confidence: float = 0.7) -> dict:
        return {"text": text, "source": source, "time": time.time(),
                "confidence": round(float(confidence), 2), "scope": scope}

    def correct_fact(self, kind: str, key: str, old: str, new: str,
                     *, source: str = "user", confidence: float = 0.95) -> bool:
        """用户纠正时替换旧 fact，不保留两个版本；冲突单独留档。"""
        data = self._load(kind, key)
        old = str(old or "").strip()
        new = str(new or "").strip()
        if not old or not new:
            return False
        facts = [m for m in data.get("facts", []) if isinstance(m, str)]
        if old not in facts:
            return False
        data["facts"] = [new if m == old else m for m in facts]
        data.setdefault("corrections", []).append({
            "old": old, "new": new, "source": source,
            "time": time.time(), "scope": kind,
        })
        data["corrections"] = data["corrections"][-50:]
        entries = data.setdefault("fact_entries", [])
        replaced = False
        for entry in entries:
            if isinstance(entry, dict) and entry.get("text") == old:
                entry.update(self._fact_meta(new, source, kind, confidence))
                replaced = True
        if not replaced:
            entries.append(self._fact_meta(new, source, kind, confidence))
        self._sync_fact_entries(data)
        self._save(kind, key, data)
        return True

    def _save(self, kind: str, key: str, data: dict):
        path = self._path(kind, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._cache[path] = data

    def clear_cache(self):
        """丢弃内存中的记忆文件缓存，让下一次读取从磁盘重新加载。

        管理面板从备份恢复记忆后调用；不清理会导致旧缓存覆盖恢复后的文件。
        """
        self._cache.clear()

    # ---------- 读取 ----------
    def _episode_expired(self, ep) -> bool:
        if not isinstance(ep, dict):
            return False
        ts = ep.get("time")
        if not ts:
            return False
        try:
            age = time.time() - float(ts)
        except (TypeError, ValueError):
            return False
        retention_days = max(1, int(self.config.chat_episode_retention_days))
        return age > retention_days * 86400

    def get_long_term(self, kind: str, key: str, limit: int = None) -> list:
        """返回长期 facts + 未过期 episodes；facts 只增不删，全部保留。"""
        data = self._load(kind, key)
        facts = [m for m in data.get("facts", []) if isinstance(m, str) and m.strip()]
        episodes = [
            ep["text"].strip()
            for ep in data.get("episodes", [])
            if isinstance(ep, dict)
            and (ep.get("text") or "").strip()
            and not self._episode_expired(ep)
        ]
        if limit is None:
            return facts + episodes
        limit = int(limit)
        # facts 是长期人格核心，全部保留；近期事件详情在剩余额度内取最近几条，
        # 并为近期事件保底一个最低配额，避免 facts 变多后把近期记忆整段挤掉
        remaining = max(0, limit - len(facts))
        reserve_events = min(len(episodes), max(10, limit // 4))
        take_events = max(remaining, reserve_events)
        if take_events <= 0:
            return facts
        return facts + episodes[-take_events:]

    _memory_terms = staticmethod(memory_terms)
    _relevance = staticmethod(relevance)

    def get_prompt_memory(
        self,
        kind: str,
        key: str,
        facts_limit: int = 12,
        events_limit: int = 6,
        max_chars: int = 1200,
        query: str = "",
    ) -> list:
        """普通对话用的记忆切片：限制条数与总字数。

        长期记忆越多，模型越容易把旧事当成聊天素材反复提起。这里只保留最近的
        少量 facts；有当前消息且开启检索时，只注入相关事件，不用无关近况补满。
        没有查询或关闭检索时保留最近事件模式，供原有调用与回退使用。
        在字数不足时按相关性保留完整条目，不截断记忆主体。
        """
        data = self._load(kind, key)
        limit_facts = max(0, int(facts_limit))
        facts = [
            m.strip()
            for m in data.get("facts", [])
            if isinstance(m, str) and m.strip()
        ][-limit_facts:] if limit_facts else []
        limit_events = max(0, int(events_limit))
        episode_items = [
            {"text": ep["text"].strip(), "time": ep.get("time") or 0}
            for ep in data.get("episodes", [])
            if isinstance(ep, dict)
            and (ep.get("text") or "").strip()
            and not self._episode_expired(ep)
        ]
        episodes: list[str]
        if (
            query
            and limit_events
            and bool(getattr(self.config, "chat_memory_retrieval_enabled", True))
        ):
            scored = sorted(
                episode_items,
                key=lambda ep: (
                    self._relevance(ep["text"], query),
                    float(ep.get("time") or 0),
                ),
                reverse=True,
            )
            # 时间只用于相关度相同的条目排序，不能让近期无关事件自动入选。
            selected = []
            seen = set(facts)
            for ep in scored:
                if self._relevance(ep["text"], query) < 0.15:
                    continue
                if ep["text"] in seen:
                    continue
                selected.append(ep["text"])
                seen.add(ep["text"])
            episodes = selected
        else:
            episodes = [
                ep["text"] for ep in episode_items[-limit_events:]
            ] if limit_events else []
        remaining = max(0, int(max_chars))
        chosen_facts = []
        # 长期事实维持最近条目优先；预算不足时跳过整条，避免切断主体或否定词。
        for fact in reversed(facts):
            if len(fact) <= remaining:
                chosen_facts.append(fact)
                remaining -= len(fact)
        chosen_facts.reverse()
        chosen_events = []
        candidates = episodes if query and bool(getattr(self.config, "chat_memory_retrieval_enabled", True)) else reversed(episodes)
        for episode in candidates:
            if episode not in chosen_facts and episode not in chosen_events and len(episode) <= remaining:
                chosen_events.append(episode)
                remaining -= len(episode)
                if len(chosen_events) >= limit_events:
                    break
        if not (query and bool(getattr(self.config, "chat_memory_retrieval_enabled", True))):
            chosen_events.reverse()
        return chosen_facts + chosen_events

    def get_recent_turns(self, kind: str, key: str, limit: int) -> list:
        return self._load(kind, key)["pending"][-limit:]

    def get_summary(self, kind: str, key: str) -> str:
        return self._load(kind, key).get("summary", "")

    def memory_span(self, kind: str, key: str) -> tuple | None:
        """返回记忆覆盖的时间范围 (最早时间戳, 最晚时间戳)；无带时间的记录则返回 None。"""
        data = self._load(kind, key)
        times: list[float] = []
        for ep in data.get("episodes", []):
            ts = ep.get("time") if isinstance(ep, dict) else None
            if ts:
                try:
                    times.append(float(ts))
                except (TypeError, ValueError):
                    continue
        for t in data.get("pending", []):
            ts = t.get("time") if isinstance(t, dict) else None
            if ts:
                try:
                    times.append(float(ts))
                except (TypeError, ValueError):
                    continue
        if not times:
            return None
        return min(times), max(times)

    def list_index(self) -> dict:
        """返回所有已存在记忆文件的摘要索引 {kind: {key: summary}}，用于自然语言跨群查询。"""
        index: dict = {"group": {}, "user": {}}
        for kind in ("group", "user"):
            d = self.base_dir / kind
            if not d.exists():
                continue
            for p in d.glob("*.json"):
                data = self._load(kind, p.stem)
                index[kind][p.stem] = data.get("summary", "")
        return index

    def search_memory(
        self, keywords: list[str], exclude_kind=None, exclude_key=None
    ) -> list[str]:
        """在所有记忆文件的 facts 与 episodes 里搜索包含关键词的条目，用于跨群查人。"""
        results: list[str] = []
        for kind in ("group", "user"):
            d = self.base_dir / kind
            if not d.exists():
                continue
            for p in d.glob("*.json"):
                if exclude_kind == kind and exclude_key == p.stem:
                    continue
                data = self._load(kind, p.stem)
                for m in data.get("facts", []):
                    if isinstance(m, str) and any(kw and kw in m for kw in keywords):
                        results.append(m)
                for ep in data.get("episodes", []):
                    text = ep.get("text") if isinstance(ep, dict) else ep
                    if isinstance(text, str) and any(
                        kw and kw in text for kw in keywords
                    ):
                        results.append(text)
        return results

    # ---------- 写入 ----------
    def add_turn(
        self,
        kind: str,
        key: str,
        role: str,
        name: str,
        content: str,
        qq: str = "",
        images: list | None = None,
        image_understanding: bool = False,
        timestamp: float | None = None,
        msg_id: int | None = None,
    ):
        data = self._load(kind, key)
        entry = {"role": role, "name": name, "content": content, "qq": qq}
        if images:
            entry["images"] = images
        if image_understanding:
            entry["image_understanding"] = True
        if msg_id is not None:
            entry["msg_id"] = msg_id
        entry["time"] = time.time() if timestamp is None else timestamp
        data["pending"].append(entry)
        self._save(kind, key, data)

    def amend_image_understanding(self, kind: str, key: str, msg_id: int | None, summary: str) -> bool:
        """多模态记忆：把图片内容摘要补写进已记录的用户消息（按 msg_id 定位）。

        补写后滚动上下文与固化缓冲都带上图片内容，支持“上次那张图”这类指代。
        """
        summary = str(summary or "").strip()
        if not summary:
            return False
        # 防御：历史图片段落以“◆ 来自 …”开头，只能作为本轮提示，
        # 不能写进当前消息，否则会复制成之后每条消息的上下文 padding。
        if summary.startswith("◆ 来自"):
            return False
        summary = summary.split("\n◆ 来自", 1)[0].strip()
        if not summary:
            return False
        data = self._load(kind, key)
        target = None
        for entry in reversed(data["pending"]):
            if entry.get("role") != "user":
                continue
            if msg_id is not None and entry.get("msg_id") == msg_id:
                target = entry
                break
            if msg_id is None:
                target = entry
                break
        if target is None:
            return False
        note = f"\n（图：{summary[:400]}）"
        if "（图：" in str(target.get("content") or "")[-500:]:
            return False  # 已补写过，避免重复
        target["content"] = str(target.get("content") or "") + note
        target["image_understanding"] = True
        self._save(kind, key, data)
        return True

    def mark_recalled(self, kind: str, key: str, msg_id: int) -> bool:
        """按消息 ID 标记 pending 中对应条目为已撤回，返回是否命中。"""
        data = self._load(kind, key)
        hit = False
        for t in data.get("pending", []):
            if isinstance(t, dict) and t.get("msg_id") == msg_id:
                t["recalled"] = True
                hit = True
        if hit:
            self._save(kind, key, data)
        return hit

    def unmark_recalled(self, kind: str, key: str, msg_id: int) -> bool:
        """撤销 pending 中对应条目的撤回标记（错别字重发场景），返回是否命中。"""
        data = self._load(kind, key)
        hit = False
        for t in data.get("pending", []):
            if isinstance(t, dict) and t.get("msg_id") == msg_id:
                if t.pop("recalled", False):
                    hit = True
        if hit:
            self._save(kind, key, data)
        return hit

    def should_consolidate(self, kind: str, key: str) -> bool:
        data = self._load(kind, key)
        pending = data["pending"]
        if not pending:
            return False
        interval = self.config.chat_memory_consolidate_min_interval_seconds
        if interval > 0:
            last = data.get("last_consolidated") or 0
            if time.time() - last < interval:
                return False
        if len(pending) >= data["adaptive_turns"]:
            return True
        total_chars = sum(len(t.get("content", "")) for t in pending)
        return total_chars >= self.config.chat_memory_consolidate_max_chars

    def has_expired_episodes(self, kind: str, key: str) -> bool:
        """是否存在超过保留天数的 episodes（用于决定是否后台压缩，避免每次消息都起任务）。"""
        data = self._load(kind, key)
        return any(self._episode_expired(ep) for ep in data.get("episodes", []))

    async def consolidate(
        self,
        kind: str,
        key: str,
        llm=None,
        existing_growth: dict = None,
        users: dict = None,
        brother_qq: str = "",
        existing_personas: dict = None,
    ):
        llm = llm or self.llm
        data = self._load(kind, key)
        # 快照一份，固化期间新进来的消息会继续追加到 data["pending"]，稍后只移除本批处理过的
        pending = list(data["pending"])
        if not pending:
            return None
        def _fmt(t: dict) -> str:
            ts = t.get("time")
            when = datetime.fromtimestamp(ts).strftime("%m-%d %H:%M") if ts else ""
            prefix = f"[{when}] " if when else ""
            recalled = "（该消息已被撤回）" if t.get("recalled") else ""
            if t.get("role") == "assistant":
                line = f"[助手|{self.assistant_name}] {t['content']}"
                if t.get("image_understanding"):
                    line += "（这是助手对用户图片内容的理解）"
                return prefix + line + recalled
            line = f"[{t.get('qq', '')}|{t.get('name', '')}] {t['content']}"
            if t.get("images"):
                line += "（该消息附带图片）"
            return prefix + line + recalled

        dialogue = "\n".join(_fmt(t) for t in pending)
        time_range = self._time_range_text(pending)
        facts = [m for m in data.get("facts", []) if isinstance(m, str) and m.strip()]
        episode_texts = [
            ep["text"].strip()
            for ep in data.get("episodes", [])
            if isinstance(ep, dict) and (ep.get("text") or "").strip()
        ]
        existing_memory = facts + episode_texts
        lt_text = (
            "\n".join(f"- {m}" for m in existing_memory)
            if existing_memory
            else "（暂无）"
        )
        existing_personas = existing_personas or {}
        personas_text = (
            json.dumps(existing_personas, ensure_ascii=False)
            if existing_personas
            else "（暂无）"
        )
        growth_parts: list[str] = []
        for gk, gv in (existing_growth or {}).items():
            if not isinstance(gv, list):
                continue
            label = "通用（global）" if str(gk) == "global" else f"针对QQ号{gk}"
            for gx in gv:
                growth_parts.append(f"[{label}] {gx}")
        growth_text = "\n".join(growth_parts) if growth_parts else "（暂无）"

        # 从 users.json 取本次对话与已有画像中出现的用户档案，让记忆固化能同时参照
        # 昵称/当前好感度/称呼偏好，而不是只靠画像里那份快照。
        users = users or {}
        roster_qqs: set[str] = set()
        for t in pending:
            q = str(t.get("qq", ""))
            if q.isdigit():
                roster_qqs.add(q)
        for q in existing_personas:
            if str(q).isdigit():
                roster_qqs.add(str(q))
        roster_lines: list[str] = []
        for q in sorted(roster_qqs):
            u = users.get(q) or {}
            fields = [f"QQ号{q}"]
            nick = (u.get("nickname") or "").strip()
            if nick:
                fields.append(f"昵称={nick}")
            fav = u.get("favorability")
            if fav is not None:
                fields.append(f"当前好感度={fav}")
            self_alias = (u.get("self_alias") or "").strip()
            if self_alias:
                fields.append(f"希望被称呼={self_alias}")
            aliases = u.get("aliases") or {}
            if isinstance(aliases, dict) and aliases:
                fields.append(
                    "要求称呼他人="
                    + "；".join(f"{k}→{v}" for k, v in aliases.items())
                )
            roster_lines.append(" ".join(fields))
        users_text = "\n".join(roster_lines) if roster_lines else "（暂无）"

        prompt = (
            "你是记忆整理助手。请阅读【新增对话记录】，并结合【已有长期记忆】【已有群友画像】【用户档案（users.json）】【你已有的成长人格】，"
            "在已有内容基础上增量更新：去重、合并、修正，而不是简单重复。只输出一个 JSON 对象（不要输出其他任何文字），格式如下：\n"
            '{"facts": ["值得长期记住的稳定事实（人物身份、稳定关系、长期偏好、重要里程碑，以及群里反复出现的梗或共同话题、彼此固定的称呼与关系等；严格按QQ号区分人，昵称变化不代表换人），每条一句话，最多6条；只写新增或需要修订的，不要重复已有记忆；纯一次性的闲聊、问好、单次吐槽、复读等不必写"], '
            '"events": ["这段时间发生的关键事件与近期动态，每条一句话并写出准确的日期或日期范围，最多6条；只写新增的，不要重复已有记忆；用于近期回顾，不必像 facts 那样极度精简，但仍要剔除纯凑数的水话"], '
            '"summary": "一句话概括这个群/用户的主要话题、人群或氛围", '
            '"personas": {"QQ号": {"nickname": "昵称", "style": {"语气": "简短描述", "性格": "简短描述", "常见话题": ["话题1"], "关系亲疏": 0到10的整数, "活跃度": 0到10的整数}}}, '
            '"favorability": {"QQ号": -10.0到2.0之间的小数，表示本次对话后你对该人好感度的变化量}, '
            '"relationships": [{"a": "QQ号", "b": "QQ号", "kind": "friend|couple|colleague|family|rival|online|other", "label": "从对话中看出的两人关系，一句话，如：两人是大学同学，常互损"}] 或 []（只在对话确实体现了两个用户之间的关系时才写，最多3条；不确定就不要写）, '
            '"self_growth": {"QQ号或global": ["基于已有成长人格与这段对话，新增或修订的自我认知/性格倾向/说话习惯，每条一句话；针对某个具体QQ号（如哥哥或某群友）的成长写在该QQ号键下，针对一般群友通用的成长写在"global"键下；全部键加起来最多1条；只有稳定、跨场景反复出现的说话习惯或性格倾向才值得写，一次性互动、单次吐槽、单次玩梗一律不写；只写具体的行为与性格倾向，不要在末尾加「不敷衍也不冷场」「不真生气也不冷场」这类自我约束式后缀；无明显成长则输出 {}"]}}\n'
            "JSON 字符串内部不要用反斜杠转义单引号（直接写 ' 即可，不要写成 \\'）。\n"
            "favorability 是「变化量」而不是绝对好感度：很厌恶约 -10.0，很开心约 +2.0，无明显变化为 0；不要一次涨太多。"
            "变化量要基于【用户档案】里的「当前好感度」判断，不是从 0 开始。"
            "若发现某个 QQ 反复说意思相近的话刻意刷好感、套近乎，应产生厌恶，favorability 给负变化量。\n"
            f"注意：助手「{self.assistant_name}」不是用户，不要把它收录进 personas 或 favorability；"
            "personas 与 favorability 的键必须是纯数字 QQ 号。"
            "style 必须是对象（含多个维度与数值），不要写成一段字符串；要基于长期观察综合判断，不要被一两句短期发言轻易改变。"
            "personas 只需输出本次【新增对话记录】里实际出现的用户；没有出现的用户沿用已有画像即可，不要重复输出、不要凑数。"
            "【用户档案】里的昵称、当前好感度、称呼偏好与画像是对同一批用户的两份记录，要互相印证：画像 nickname 以【用户档案】的最新昵称为准，"
            "好感度变化量叠加在【用户档案】的当前好感度之上；用户「希望被称呼」「要求称呼他人」也要在画像或 facts 里体现。"
            "self_growth 只描述助手自己的成长，不描述其他用户。\n"
            "反复出现且含义明确的网络表达，可将已形成的使用习惯和适用语境简短记入 self_growth；"
            "不要因一次接梗建立固定口癖，不记来源不明的梗义，不照抄群友原句。\n"
            "对话记录里每行开头的 [QQ号|昵称] 是该条消息的说话人（QQ号唯一标识人，昵称可能变化），"
            f"[助手|{self.assistant_name}] 是你自己的发言；整理 facts/events/personas/favorability 时必须严格按 QQ 号归属，绝不能张冠李戴。"
            "多人接同一个梗、复读同一句话时，每条也只看自己那行开头的 QQ 号，不要因内容相近就合并成同一个人。\n\n"
            f"【已有长期记忆】\n{lt_text}\n\n"
            f"【已有群友画像】\n{personas_text}\n\n"
            f"【用户档案（users.json，昵称/当前好感度/称呼偏好）】\n{users_text}\n\n"
            f"【你已有的成长人格】\n{growth_text}\n\n"
            f"【新增对话记录的时间范围】\n{time_range}\n\n"
            "【新增对话记录】\n"
            + dialogue
        )
        if brother_qq:
            prompt += (
                f"\n助手唯一称呼为「哥哥」的人的 QQ 号是 {brother_qq}；"
                f"self_growth 中凡是对着哥哥养成的成长（如「面对哥哥…」）必须写在键 \"{brother_qq}\" 下，"
                "不要写进 global。"
            )
        messages = [
            {"role": "system", "content": "你负责整理记忆，只输出 JSON。"},
            {"role": "user", "content": prompt},
        ]
        try:
            result = await llm.chat(
                messages,
                temperature=0.3,
                max_tokens=self.config.chat_memory_consolidate_max_tokens,
                disable_thinking=True,
                json_mode=True,
            )
        except Exception as e:
            # 少数 OpenAI 兼容端点不支持 response_format，回退普通模式重试一次
            logger.warning(f"[memory] JSON 模式固化失败，回退普通模式重试 {kind}/{key}: {e}")
            try:
                result = await llm.chat(
                    messages,
                    temperature=0.3,
                    max_tokens=self.config.chat_memory_consolidate_max_tokens,
                    disable_thinking=True,
                )
            except Exception as e2:
                logger.error(f"记忆固化失败 {kind}/{key}: {e2}")
                return None

        parsed = self._parse_consolidate_result(result)

        def _clean_list(value) -> list[str]:
            if not isinstance(value, list):
                return []
            return [e.strip() for e in value if isinstance(e, str) and e.strip()]

        # 分层写入：facts 是长期稳定的固化记忆（只增不删）；events 是近期事件（过渡记忆，
        # 带时间戳，超过保留天数后由 compact_expired_episodes 压缩进 facts）。
        new_facts = _clean_list(parsed.get("facts"))
        new_events = _clean_list(parsed.get("events"))
        summary = (parsed.get("summary") or "").strip()
        personas = parsed.get("personas") or {}
        if not new_facts and not new_events and not summary and not personas:
            logger.error(
                f"[memory] 固化结果解析失败或为空，保留 pending 待重试: {kind}/{key}"
            )
            return None

        times = [t.get("time") for t in pending if t.get("time")]
        episode_time = max(times) if times else time.time()
        existing_fact_texts = {
            m for m in data.get("facts", []) if isinstance(m, str)
        }
        existing_episode_texts = {
            (ep.get("text") or "").strip()
            for ep in data.get("episodes", [])
            if isinstance(ep, dict)
        }
        added = 0
        for entry in new_facts:
            if entry in existing_fact_texts or entry in existing_episode_texts:
                continue
            data["facts"].append(entry)
            data.setdefault("fact_entries", []).append(
                self._fact_meta(entry, "model_consolidation", kind, 0.65)
            )
            existing_fact_texts.add(entry)
            added += 1
        for entry in new_events:
            if entry in existing_fact_texts or entry in existing_episode_texts:
                continue
            data["episodes"].append({
                "text": entry, "time": episode_time,
                "source": "group_context" if kind == "group" else "user_context",
                "confidence": 0.72, "scope": kind,
            })
            existing_episode_texts.add(entry)
            added += 1
        if added:
            if added >= 3:
                data["adaptive_turns"] = max(15, data["adaptive_turns"] - 1)
            else:
                data["adaptive_turns"] = min(30, data["adaptive_turns"] + 2)

        if summary:
            data["summary"] = summary

        # 只移除本批处理过的条目，保留固化期间新追加的发言，避免并发丢消息
        processed = {id(t) for t in pending}
        data["pending"] = [t for t in data["pending"] if id(t) not in processed]
        data["last_consolidated"] = time.time()
        self._save(kind, key, data)
        sg = parsed.get("self_growth") or {}
        sg_count = (
            sum(len(v) for v in sg.values())
            if isinstance(sg, dict)
            else len(sg)
        )
        logger.info(
            f"[memory] 固化完成 kind={kind} key={key} pending={len(pending)} "
            f"episodes=+{added} personas={list(personas.keys())} "
            f"fav={parsed.get('favorability')} "
            f"growth=+{sg_count}"
        )
        return parsed

    async def compact_expired_episodes(self, kind: str, key: str, llm=None) -> bool:
        """把超过保留天数的 episodes 提炼成长期 facts 后删除详情，facts 只增不删。"""
        llm = llm or self.llm
        data = self._load(kind, key)
        expired: list[str] = []
        kept: list[dict] = []
        for ep in data.get("episodes", []):
            if not isinstance(ep, dict):
                continue
            text = (ep.get("text") or "").strip()
            if not text:
                continue
            if self._episode_expired(ep):
                expired.append(text)
            else:
                kept.append({"text": text, "time": ep.get("time") or time.time()})
        if not expired:
            return False

        facts = await self._extract_facts_from_expired(kind, key, llm, expired)
        if not facts:
            # LLM/JSON 失败，或没能提炼出任何可留存的长期事实：保留过期 episodes 待下次
            # 重试，避免"删掉事件详情却没沉淀成 facts"造成记忆静默丢失
            return False
        for f in facts:
            if f not in data["facts"]:
                data["facts"].append(f)
                data.setdefault("fact_entries", []).append(
                    self._fact_meta(f, "compacted_event", kind, 0.7)
                )
        data["episodes"] = kept
        self._save(kind, key, data)
        logger.info(
            f"[memory] 过期事件压缩 kind={kind} key={key} "
            f"episodes={len(expired)} -> facts=+{len(facts)}"
        )
        return True

    async def _extract_facts_from_expired(
        self, kind: str, key: str, llm, episodes: list[str]
    ) -> list[str]:
        prompt = (
            "你是长期记忆整理助手。下面是一些较早的群聊事件记录，请提炼出"
            "「值得长期记住」的长期事实（人物身份、稳定关系、性格偏好、重要里程碑，"
            "以及群里反复出现的梗或共同话题、彼此固定的称呼与关系），"
            "剔除纯闲聊、一次性玩梗、短期事件等噪声。每条一句话，最多 8 条。\n"
            "只输出一个 JSON 对象：{\"facts\": [\"...\"]}，没有可留的内容就输出 {\"facts\": []}。\n\n"
            "【较早的事件记录】\n" + "\n".join(f"- {e}" for e in episodes)
        )
        try:
            result = await llm.chat(
                [
                    {"role": "system", "content": "你负责整理长期记忆，只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=512,
                disable_thinking=True,
            )
        except Exception as e:
            logger.error(f"过期事件压缩失败 {kind}/{key}: {e}")
            return None
        parsed = self._parse_json_object(result)
        if parsed is None:
            logger.warning(f"过期事件压缩 JSON 解析失败 {kind}/{key}")
            return None
        raw = parsed.get("facts") or []
        if not isinstance(raw, list):
            raw = []
        return [f.strip() for f in raw if isinstance(f, str) and f.strip()]

    @staticmethod
    def _time_range_text(pending: list) -> str:
        """把 pending 里每条消息的时间戳拼成准确时间范围，供固化时把 facts 写成准确日期。"""
        times = [t.get("time") for t in pending if t.get("time")]
        if not times:
            return "（时间未知）"
        t_min, t_max = min(times), max(times)
        s_min = datetime.fromtimestamp(t_min).strftime("%Y-%m-%d %H:%M")
        s_max = datetime.fromtimestamp(t_max).strftime("%Y-%m-%d %H:%M")
        span_days = (t_max - t_min) / 86400
        span = (
            f"约{span_days:.1f}天"
            if span_days >= 1
            else f"约{int(span_days * 24)}小时"
        )
        return (
            f"这批对话发生在 {s_min} 至 {s_max}（时间跨度 {span}）。"
            "对话记录每行开头的 [月-日 时:分] 是该条消息的发送时间（省略了年份，"
            "年份见上面的起止时间）。写 facts 时，凡涉及时间的事件，时间必须写成"
            "「准确的起止范围」（如「2026-09-08 至 09-10」或「9月8日至9月10日」），"
            "结合对话记录里该事件从开始到结束的消息时间来判断起止；禁止只写单点日期，"
            "也禁止使用「最近」「前几天」「很久之前」这类会随现在时间变化而失效的模糊说法。"
        )

    @staticmethod
    def _balance_json(text: str) -> str:
        """补齐缺失的收尾括号，修复 LLM 漏写闭括号（尤其是嵌套对象）的 JSON。

        按「字符串 / 转义」状态扫描，统计未闭合的 { [ 数量，在末尾补上对应的 } ]；
        若结尾停在字符串中间则先补一个引号。这样「漏写闭括号」或「输出被截断」的
        JSON 也能被解析，至少救回 facts / summary，避免整批记忆被丢弃。
        """
        stack: list[str] = []
        in_str = False
        escaped = False
        for ch in text:
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch in "{[":
                stack.append(ch)
            elif ch == "}" and stack and stack[-1] == "{":
                stack.pop()
            elif ch == "]" and stack and stack[-1] == "[":
                stack.pop()
        if not stack and not in_str:
            return text
        repair = '"' if in_str else ""
        repair += "".join("}" if c == "{" else "]" for c in reversed(stack))
        return text + repair

    @staticmethod
    def _parse_json_object(text: str):
        """从 LLM 输出里提取 JSON 对象；失败返回 None。"""
        text = (text or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text).strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return None
        candidate = text[start : end + 1]
        # LLM 偶发把单引号写成 \'，而 JSON 里单引号无需转义，去掉多余反斜杠
        candidate = candidate.replace("\\'", "'")
        try:
            return json.loads(candidate)
        except Exception:
            pass
        # 兜底 1：去掉尾随逗号后重试（LLM 偶发在数组/对象末尾多写逗号）
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", candidate))
        except Exception:
            pass
        # 兜底 2：LLM 偶发输出 Python 字典风格（单引号 / True / None），用 literal_eval 解析
        try:
            return ast.literal_eval(candidate)
        except Exception:
            pass
        # 兜底 3：LLM 偶发漏写嵌套对象的 }（括号不闭合），补齐括号后再解析
        try:
            balanced = MemoryManager._balance_json(candidate)
            return json.loads(re.sub(r",\s*([}\]])", r"\1", balanced))
        except Exception:
            pass
        return None

    @staticmethod
    def _parse_consolidate_result(result: str) -> dict:
        """解析 LLM 输出的 JSON；失败返回空结果，避免把整段 JSON 当成事实污染长期记忆。"""
        parsed = MemoryManager._parse_json_object(result)
        if parsed is not None:
            return parsed
        text = (result or "").strip()
        logger.warning(
            f"[memory] 记忆整理结果 JSON 解析失败，丢弃该次结果: {text!r}"
        )
        return {"facts": []}
