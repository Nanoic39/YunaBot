"""图片识别结果持久缓存，避免同一张图重复调用视觉模型。"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path


class VisionCache:
    def __init__(self, path: Path, ttl_hours: int = 168, max_entries: int = 800):
        self.path = Path(path)
        self.ttl = max(1, int(ttl_hours)) * 3600
        self.max_entries = max(50, int(max_entries))
        self.data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "entries": {}}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("entries"), dict):
                return raw
        except Exception:
            pass
        return {"version": 1, "entries": {}}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    @staticmethod
    def key(url: str, detail: str = "auto") -> str:
        raw = str(url or "")
        # 去掉 URL 上的临时签名参数，同一张图尽量命中
        if raw.startswith(("http://", "https://")):
            raw = raw.split("?", 1)[0]
        return hashlib.sha1((raw + "|" + str(detail or "auto")).encode("utf-8", "ignore")).hexdigest()

    def get(self, url: str, detail: str = "auto") -> str:
        entry = (self.data.get("entries") or {}).get(self.key(url, detail))
        if not isinstance(entry, dict):
            return ""
        if time.time() - float(entry.get("time") or 0) > self.ttl:
            return ""
        return str(entry.get("text") or "")

    def put(self, url: str, text: str, detail: str = "auto") -> None:
        text = str(text or "").strip()
        if not text:
            return
        entries = self.data.setdefault("entries", {})
        entries[self.key(url, detail)] = {"text": text[:4000], "time": int(time.time())}
        if len(entries) > self.max_entries:
            ordered = sorted(entries.items(), key=lambda item: float(item[1].get("time") or 0))
            for key, _value in ordered[: max(1, len(entries) - self.max_entries)]:
                entries.pop(key, None)
        self._save()

    def stats(self) -> dict:
        return {"entries": len(self.data.get("entries") or {}), "ttl_hours": self.ttl // 3600,
                "max_entries": self.max_entries}
