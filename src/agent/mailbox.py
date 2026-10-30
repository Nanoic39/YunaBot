"""跨场景捎话信箱：群聊 ↔ 面板聊天框 ↔ 电脑/手机任务 之间的消息级双向通道。

* 群聊里说「帮我跟服务器里的XX捎句话：……」→ 写入信箱，电脑任务启动时携带；
* 面板聊天框里说「在群里替我说……」→ 写入信箱，由群聊侧定时取出发送；
* 送达/回复结果回写 self_memory，Yuna 在两边都能自然提起。

单文件 JSON 存储，进程内锁保护；条目状态：pending → delivered → replied/failed。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

MAX_ITEMS = 100


class Mailbox:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "items": []}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("items"), list):
                return raw
        except (OSError, ValueError):
            pass
        return {"version": 1, "items": []}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def send(self, *, source: str, target: str, text: str, sender: str = "") -> dict:
        """投一条捎话。source: group/panel/computer；target: group:<群号> / computer / panel"""
        text = str(text or "").strip()[:500]
        if not text:
            raise ValueError("捎话内容不能为空")
        item = {
            "id": f"{int(time.time())}-{os.urandom(3).hex()}",
            "time": int(time.time()),
            "source": str(source or "")[:12],
            "target": str(target or "")[:16],
            "sender": str(sender or "")[:24],
            "text": text,
            "status": "pending",
            "result": "",
        }
        with self.lock:
            self.data["items"].append(item)
            self.data["items"] = self.data["items"][-MAX_ITEMS:]
            self._save()
        return item

    def due(self, target: str, limit: int = 3) -> list[dict]:
        """取走发往指定目标的 pending 捎话（标记 delivered）。"""
        with self.lock:
            picked = []
            for item in self.data["items"]:
                if item.get("status") == "pending" and item.get("target") == str(target):
                    item["status"] = "delivered"
                    item["delivered_time"] = int(time.time())
                    picked.append(dict(item))
                    if len(picked) >= max(1, limit):
                        break
            if picked:
                self._save()
            return picked

    def reply(self, item_id: str, result: str, ok: bool = True) -> dict | None:
        with self.lock:
            for item in self.data["items"]:
                if str(item.get("id")) == str(item_id):
                    item["status"] = "replied" if ok else "failed"
                    item["result"] = str(result or "")[:300]
                    self._save()
                    return dict(item)
        return None

    def snapshot(self, limit: int = 30) -> list[dict]:
        with self.lock:
            return [dict(i) for i in self.data["items"]][-max(1, min(100, limit)):][::-1]

    def pending_count(self) -> int:
        with self.lock:
            return sum(1 for i in self.data["items"] if i.get("status") == "pending")


mailbox = Mailbox(Path(os.getenv("MAILBOX_FILE") or "data/agent/mailbox.json"))
