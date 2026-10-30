"""运行指标采集：LLM 延迟/成功率/Token、记忆固化耗时、表情命中率。

按天分桶持久化到 metrics.json，只保留最近 30 天；
延迟样本保留最近 400 条（内存环形缓冲，重启即清），用于计算 p50/p95。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

KEEP_DAYS = 30
LATENCY_SAMPLES = 400


def _percentile(sorted_values: list[float], ratio: float) -> int:
    if not sorted_values:
        return 0
    index = min(len(sorted_values) - 1, max(0, round(ratio * (len(sorted_values) - 1))))
    return int(round(sorted_values[index]))


class Metrics:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self.data = self._load()
        self._latencies: list[int] = list(self.data.get("latencies") or [])[-LATENCY_SAMPLES:]

    # ---------- 持久化 ----------

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "days": {}, "latencies": []}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("days"), dict):
                return raw
        except Exception:
            pass
        return {"version": 1, "days": {}, "latencies": []}

    def _save_locked(self) -> None:
        self.data["latencies"] = self._latencies[-LATENCY_SAMPLES:]
        self._trim_locked()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(self.data, ensure_ascii=False), encoding="utf-8"
            )
            os.replace(temporary, self.path)
        except Exception:
            pass  # 指标写盘失败不影响主流程

    def _trim_locked(self) -> None:
        days = self.data.get("days") or {}
        if len(days) <= KEEP_DAYS:
            return
        cutoff = time.strftime("%Y-%m-%d", time.localtime(time.time() - KEEP_DAYS * 86400))
        self.data["days"] = {k: v for k, v in days.items() if k >= cutoff}

    def _day_locked(self) -> dict:
        key = time.strftime("%Y-%m-%d")
        return self.data.setdefault("days", {}).setdefault(key, {})

    # ---------- 采集接口 ----------

    def record_llm(self, ok: bool, latency_ms: int, tokens: int = 0, *, error: str = "") -> None:
        """记录一次 LLM 调用：成败、耗时、Token；error 可为 content_filter / error。"""
        try:
            ms = max(0, int(latency_ms))
        except (TypeError, ValueError):
            ms = 0
        with self._lock:
            bucket = self._day_locked().setdefault("llm", {})
            bucket["calls"] = int(bucket.get("calls", 0)) + 1
            if ok:
                bucket["ok"] = int(bucket.get("ok", 0)) + 1
            else:
                bucket["fail"] = int(bucket.get("fail", 0)) + 1
                if str(error) == "content_filter":
                    bucket["filter"] = int(bucket.get("filter", 0)) + 1
            bucket["tokens"] = int(bucket.get("tokens", 0)) + max(0, int(tokens or 0))
            bucket["ms_sum"] = int(bucket.get("ms_sum", 0)) + ms
            bucket["ms_max"] = max(int(bucket.get("ms_max", 0)), ms)
            self._latencies.append(ms)
            self._latencies = self._latencies[-LATENCY_SAMPLES:]
            self._save_locked()

    def record_consolidate(self, ok: bool, latency_ms: int) -> None:
        """记录一次记忆固化：成败与总耗时。"""
        try:
            ms = max(0, int(latency_ms))
        except (TypeError, ValueError):
            ms = 0
        with self._lock:
            bucket = self._day_locked().setdefault("consolidate", {})
            bucket["count"] = int(bucket.get("count", 0)) + 1
            if not ok:
                bucket["fail"] = int(bucket.get("fail", 0)) + 1
            bucket["ms_sum"] = int(bucket.get("ms_sum", 0)) + ms
            bucket["ms_max"] = max(int(bucket.get("ms_max", 0)), ms)
            self._save_locked()

    def record_emote(self, hit: bool) -> None:
        """记录一次表情请求是否在库里命中图片。"""
        with self._lock:
            bucket = self._day_locked().setdefault("emote", {})
            bucket["requested"] = int(bucket.get("requested", 0)) + 1
            if hit:
                bucket["hit"] = int(bucket.get("hit", 0)) + 1
            self._save_locked()

    # ---------- 汇总 ----------

    def snapshot(self, days: int = 14) -> dict:
        with self._lock:
            raw_days = {k: json.loads(json.dumps(v)) for k, v in (self.data.get("days") or {}).items()}
            latencies = sorted(self._latencies)
        keys = sorted(raw_days.keys())[-max(1, min(30, days)):]
        series = []
        calls_total = ok_total = fail_total = filter_total = tokens_total = 0
        for key in keys:
            day = raw_days.get(key) or {}
            llm = day.get("llm") or {}
            con = day.get("consolidate") or {}
            emote = day.get("emote") or {}
            calls = int(llm.get("calls", 0))
            ok = int(llm.get("ok", 0))
            calls_total += calls
            ok_total += ok
            fail_total += int(llm.get("fail", 0))
            filter_total += int(llm.get("filter", 0))
            tokens_total += int(llm.get("tokens", 0))
            series.append({
                "date": key,
                "calls": calls,
                "ok": ok,
                "fail": int(llm.get("fail", 0)),
                "tokens": int(llm.get("tokens", 0)),
                "ms_avg": int(llm.get("ms_sum", 0) / calls) if calls else 0,
                "ms_max": int(llm.get("ms_max", 0)),
                "consolidations": int(con.get("count", 0)),
                "consolidate_fail": int(con.get("fail", 0)),
                "consolidate_ms_avg": int(con.get("ms_sum", 0) / con.get("count", 1)) if con.get("count") else 0,
                "emote_requested": int(emote.get("requested", 0)),
                "emote_hit": int(emote.get("hit", 0)),
            })
        today = series[-1] if series else {}
        return {
            "days": series,
            "llm": {
                "calls": calls_total,
                "ok": ok_total,
                "fail": fail_total,
                "content_filter": filter_total,
                "success_rate": round(ok_total / calls_total, 4) if calls_total else None,
                "tokens": tokens_total,
                "p50_ms": _percentile(latencies, 0.50),
                "p95_ms": _percentile(latencies, 0.95),
                "avg_ms": int(sum(latencies) / len(latencies)) if latencies else 0,
                "today_calls": int(today.get("calls", 0)),
                "today_fail": int(today.get("fail", 0)),
                "today_tokens": int(today.get("tokens", 0)),
                "token_cap": 0,
            },
            "consolidate": {
                "count": sum(int(row.get("consolidations", 0)) for row in series),
                "fail": sum(int(row.get("consolidate_fail", 0)) for row in series),
                "ms_avg": round(
                    sum(int(row.get("consolidate_ms_avg", 0)) * int(row.get("consolidations", 0)) for row in series)
                    / max(1, sum(int(row.get("consolidations", 0)) for row in series))
                ),
            },
            "emote": {
                "requested": sum(int(row.get("emote_requested", 0)) for row in series),
                "hit": sum(int(row.get("emote_hit", 0)) for row in series),
            },
        }
