"""管理面板健康告警状态：决定何时发送、避免重复刷屏。"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

from nonebot.log import logger


class AlertManager:
    def __init__(self, path: Path, cooldown_seconds: int = 6 * 3600):
        self.path = Path(path)
        self.cooldown_seconds = max(300, int(cooldown_seconds))
        self._lock = threading.RLock()
        self._data = self._load()

    def _default(self) -> dict:
        return {
            "last_sent": 0,
            "last_signature": "",
            "last_warnings": [],
            "history": [],
        }

    def _load(self) -> dict:
        data = self._default()
        if not self.path.exists():
            return data
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as error:
            logger.warning(f"[admin] 读取告警状态失败：{error}")
            return data
        if not isinstance(raw, dict):
            return data
        data["last_sent"] = int(raw.get("last_sent") or 0)
        data["last_signature"] = str(raw.get("last_signature") or "")
        data["last_warnings"] = [
            str(item) for item in (raw.get("last_warnings") or []) if str(item).strip()
        ]
        data["history"] = raw.get("history") if isinstance(raw.get("history"), list) else []
        return data

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + ".tmp")
            temporary.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except OSError as error:
            logger.warning(f"[admin] 保存告警状态失败：{error}")

    @staticmethod
    def signature(warnings: list[str]) -> str:
        text = "|".join(sorted(str(item) for item in warnings))
        return hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]

    def should_send(self, warnings: list[str], now: float | None = None) -> bool:
        warnings = [str(item) for item in warnings if str(item).strip()]
        if not warnings:
            return False
        now = time.time() if now is None else now
        signature = self.signature(warnings)
        if signature != self._data.get("last_signature"):
            return True
        return now - float(self._data.get("last_sent") or 0) >= self.cooldown_seconds

    def record(
        self,
        warnings: list[str],
        delivered: int,
        targets: list[str],
        error: str = "",
        now: float | None = None,
    ) -> None:
        warnings = [str(item) for item in warnings if str(item).strip()]
        with self._lock:
            now = int(time.time() if now is None else now)
            self._data["last_sent"] = now
            self._data["last_signature"] = self.signature(warnings)
            self._data["last_warnings"] = warnings
            entry = {
                "time": now,
                "created": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                "warnings": warnings,
                "targets": targets,
                "delivered": delivered,
                "error": error,
            }
            history = self._data.get("history") or []
            history.append(entry)
            self._data["history"] = history[-50:]
            self._save()

    def status(self, enabled: bool, targets: list[str]) -> dict:
        with self._lock:
            data = json.loads(json.dumps(self._data, ensure_ascii=False))
        return {
            "enabled": bool(enabled),
            "targets": targets,
            "cooldown_seconds": self.cooldown_seconds,
            "last_sent": data.get("last_sent", 0),
            "last_warnings": data.get("last_warnings", []),
            "history": list(reversed(data.get("history", [])))[:20],
        }

    @staticmethod
    def build_message(health: dict) -> str:
        warnings = health.get("warnings") or []
        lines = [
            "【Yuna 健康告警】",
            f"时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"环境：{health.get('environment', '—')} · 版本：{health.get('version', '—')}",
            f"机器人：{len(health.get('bots') or [])} 个 · 插件：{health.get('plugins', 0)} 个",
            f"磁盘剩余：{(health.get('disk') or {}).get('free', 0) / 1024 ** 3:.1f} GB",
        ]
        lines.append("警告：")
        lines.extend(f"- {item}" for item in warnings)
        lines.append("请打开管理面板查看详情。")
        return "\n".join(lines)
