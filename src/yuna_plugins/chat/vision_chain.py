"""可视模型链路：按用途串联多个 OpenAI 兼容视觉接口，失败自动降级。

设计目标：
- 节点顺序即真实调用顺序，前一个失败才尝试下一个；
- 每个节点可服务 image / sticker / ocr 中的一种或多种用途；
- API Key 只保存在本机 JSON（0600），管理接口只返回掩码；
- 最近调用记录保存在内存环形队列，供管理面板实时查看。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from nonebot import logger

from .llm import LLMClient

PURPOSES = {
    "chat": "主对话 LLM",
    "agent": "Agent / 工具规划",
    "image": "识图 / 多模态",
    "sticker": "表情/贴纸判断",
    "ocr": "文字提取",
    "video": "视频 / 动图封面",
}
DETAILS = {"auto", "low", "high", "original"}


def _text(value, limit=500) -> str:
    return str(value or "").strip()[:limit]


def _mask_key(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if len(raw) <= 8:
        return "••••"
    return raw[:4] + "••••" + raw[-4:]


def _public_node(node: dict) -> dict:
    item = {key: value for key, value in node.items() if key != "api_key"}
    item["has_key"] = bool(str(node.get("api_key") or "").strip())
    item["key_hint"] = _mask_key(str(node.get("api_key") or ""))
    return item


class VisionChain:
    def __init__(self, path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.lock = threading.RLock()
        self.recent = deque(maxlen=120)
        self.data = self._load()

    def _load(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        nodes = raw.get("nodes") if isinstance(raw.get("nodes"), list) else []
        clean = []
        for index, node in enumerate(nodes):
            if isinstance(node, dict):
                try:
                    clean.append(self._clean_node(node, order=index + 1))
                except (TypeError, ValueError):
                    continue
        return {"version": 1, "nodes": clean, "auto_select": bool(raw.get("auto_select", True)),
                "updated": float(raw.get("updated") or 0)}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["updated"] = self.clock()
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(self.path)

    def _clean_node(self, fields: dict, *, order: int | None = None) -> dict:
        raw = dict(fields or {})
        name = _text(raw.get("name"), 60)
        base_url = _text(raw.get("base_url"), 400).rstrip("/")
        model = _text(raw.get("model"), 120)
        if not name:
            name = model or "视觉节点"
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("接口地址必须以 http:// 或 https:// 开头")
        if not model:
            raise ValueError("模型名不能为空")
        purposes = raw.get("purposes")
        if isinstance(purposes, str):
            purposes = [x.strip() for x in purposes.split(",") if x.strip()]
        if not isinstance(purposes, list):
            purposes = ["image"]
        purposes = [str(x) for x in purposes if str(x) in PURPOSES]
        if not purposes:
            purposes = ["image"]
        try:
            timeout = max(5, min(120, int(raw.get("timeout") or 45)))
        except (TypeError, ValueError):
            timeout = 45
        try:
            max_tokens = max(64, min(4096, int(raw.get("max_tokens") or 900)))
        except (TypeError, ValueError):
            max_tokens = 900
        detail = str(raw.get("detail") or "auto")
        if detail not in DETAILS:
            detail = "auto"
        return {
            "id": str(raw.get("id") or uuid.uuid4().hex),
            "name": name,
            "base_url": base_url,
            "api_key": str(raw.get("api_key") or ""),
            "model": model,
            "purposes": list(dict.fromkeys(purposes)),
            "enabled": bool(raw.get("enabled", True)),
            "order": int(order if order is not None else (raw.get("order") or 0)),
            "detail": detail,
            "timeout": timeout,
            "max_tokens": max_tokens,
            "prompt": _text(raw.get("prompt"), 1200),
        }

    def _sorted(self) -> list[dict]:
        return sorted(
            self.data.get("nodes") or [],
            key=lambda node: (int(node.get("order") or 0), str(node.get("name") or "")),
        )

    def snapshot(self) -> dict:
        with self.lock:
            nodes = [_public_node(node) for node in self._sorted()]
            stats = self.stats()
            return {
                "version": 1,
                "nodes": nodes,
                "stats": stats,
                "purposes": PURPOSES,
                "auto_select": bool(self.data.get("auto_select", True)),
                "recent": list(reversed(self.recent)),
                "updated": self.data.get("updated") or 0,
            }

    def get(self, node_id: str) -> dict | None:
        with self.lock:
            return next((dict(node) for node in self.data.get("nodes") or []
                         if str(node.get("id")) == str(node_id)), None)

    def create(self, fields: dict) -> dict:
        with self.lock:
            node = self._clean_node(fields, order=len(self.data.get("nodes") or []) + 1)
            self.data.setdefault("nodes", []).append(node)
            self._save()
            return _public_node(node)

    def update(self, node_id: str, fields: dict) -> dict | None:
        with self.lock:
            nodes = self.data.get("nodes") or []
            node = next((item for item in nodes if str(item.get("id")) == str(node_id)), None)
            if node is None:
                return None
            incoming = dict(fields or {})
            # 管理面板不会回传真实 Key；留空或掩码表示保留原 Key。
            raw_key = str(incoming.get("api_key") or "")
            if raw_key and "••••" not in raw_key:
                incoming["api_key"] = raw_key
            else:
                incoming.pop("api_key", None)
            updated = {**node, **incoming, "id": node["id"]}
            clean = self._clean_node(updated, order=node.get("order"))
            nodes[nodes.index(node)] = clean
            self._save()
            return _public_node(clean)

    def set_auto_select(self, enabled: bool) -> bool:
        with self.lock:
            self.data["auto_select"] = bool(enabled)
            self._save()
            return bool(self.data["auto_select"])

    def delete(self, node_id: str) -> bool:
        with self.lock:
            nodes = self.data.get("nodes") or []
            keep = [node for node in nodes if str(node.get("id")) != str(node_id)]
            if len(keep) == len(nodes):
                return False
            self.data["nodes"] = keep
            self._save()
            return True

    def reorder(self, ids: list[str]) -> list[dict]:
        with self.lock:
            nodes = self.data.get("nodes") or []
            by_id = {str(node.get("id")): node for node in nodes}
            ordered = [by_id[str(node_id)] for node_id in ids if str(node_id) in by_id]
            ordered.extend(node for node in self._sorted() if str(node.get("id")) not in {str(x) for x in ids})
            for index, node in enumerate(ordered, 1):
                node["order"] = index
            self.data["nodes"] = ordered
            self._save()
            return [_public_node(node) for node in ordered]

    def nodes_for(self, purpose: str = "image") -> list[dict]:
        wanted = str(purpose or "image")
        with self.lock:
            nodes = [dict(node) for node in self._sorted()
                     if node.get("enabled") and wanted in (node.get("purposes") or [])]
            if not self.data.get("auto_select", True) or len(nodes) < 2:
                return nodes
            # 自动择优：优先近期成功率，其次平均延迟，最后才看人工顺序。
            # 这不会改动用户设置的链路顺序，只影响本次运行时的尝试顺序。
            scores = {}
            for node in nodes:
                rows = [row for row in self.recent
                        if str(row.get("node_id")) == str(node.get("id"))
                        and str(row.get("purpose")) == wanted]
                if not rows:
                    scores[str(node.get("id"))] = (0, 0.0, 0)
                    continue
                ok = sum(bool(row.get("ok")) for row in rows)
                latency = sum(int(row.get("latency_ms") or 0) for row in rows) / len(rows)
                scores[str(node.get("id"))] = (1, ok / len(rows), latency)
            return sorted(nodes, key=lambda node: (
                scores.get(str(node.get("id")), (0, 0.0, 0))[1] * -1,
                scores.get(str(node.get("id")), (0, 0.0, 0))[2],
                int(node.get("order") or 0),
            ))

    def signature(self, purpose: str = "image") -> str:
        with self.lock:
            nodes = [node for node in self._sorted()
                     if node.get("enabled") and str(purpose or "image") in (node.get("purposes") or [])]
        raw = "|".join(str(node.get("id")) + ":" + str(node.get("model")) for node in nodes)
        return hashlib.sha1((purpose + "|" + raw).encode("utf-8")).hexdigest()[:16]

    def record(self, node: dict, purpose: str, ok: bool, latency_ms: int, error: str = "") -> None:
        entry = {
            "time": self.clock(),
            "node_id": str(node.get("id") or ""),
            "name": str(node.get("name") or ""),
            "model": str(node.get("model") or ""),
            "purpose": str(purpose or "image"),
            "ok": bool(ok),
            "latency_ms": int(latency_ms),
            "error": _text(error, 240),
        }
        with self.lock:
            self.recent.append(entry)

    def stats(self) -> dict:
        nodes = self.data.get("nodes") or []
        return {
            "nodes": len(nodes),
            "enabled": sum(bool(node.get("enabled")) for node in nodes),
            "calls": len(self.recent),
            "success": sum(bool(row.get("ok")) for row in self.recent),
            "failed": sum(not bool(row.get("ok")) for row in self.recent),
        }


class VisionChainClient:
    """对外保持 LLMClient.chat 接口；内部按节点顺序失败降级。"""

    def __init__(self, chain: VisionChain, purpose: str = "image", fallback=None):
        self.chain = chain
        self.purpose = purpose
        self.fallback = fallback

    @property
    def available(self) -> bool:
        return bool(self.chain.nodes_for(self.purpose)) or bool(self.fallback and self.fallback.available)

    @property
    def api_base(self):
        return getattr(self.fallback, "api_base", "")

    @api_base.setter
    def api_base(self, value):
        if self.fallback is not None:
            self.fallback.api_base = value

    @property
    def api_key(self):
        return getattr(self.fallback, "api_key", "")

    @api_key.setter
    def api_key(self, value):
        if self.fallback is not None:
            self.fallback.api_key = value

    @property
    def model(self):
        nodes = self.chain.nodes_for(self.purpose)
        return str(nodes[0].get("model") or "") if nodes else getattr(self.fallback, "model", "")

    @model.setter
    def model(self, value):
        if self.fallback is not None:
            self.fallback.model = value

    async def chat_with_usage(self, messages: list, temperature: float = 0.8,
                              max_tokens: int = 1024, disable_thinking: bool = False,
                              json_mode: bool = False, *, context_guard: bool = False):
        """与 LLMClient.chat_with_usage 对齐；主对话/Agent 不会因为包装层缺少方法而失败。"""
        nodes = self.chain.nodes_for(self.purpose)
        if not nodes:
            if self.fallback and self.fallback.available:
                return await self.fallback.chat_with_usage(
                    messages, temperature, max_tokens, disable_thinking=disable_thinking,
                    json_mode=json_mode, context_guard=context_guard,
                )
            raise RuntimeError("模型链路没有可用节点")
        errors = []
        for node in nodes:
            llm = LLMClient(node["base_url"], node.get("api_key") or "local", node["model"])
            if not llm.available:
                errors.append(f'{node.get("name")}: 未配置 Key/模型')
                continue
            content = messages
            prompt = str(node.get("prompt") or "").strip()
            if prompt:
                content = [{"role": "system", "content": prompt}] + list(messages)
            started = time.monotonic()
            try:
                text, usage = await asyncio.wait_for(
                    llm.chat_with_usage(content, temperature, max_tokens,
                                        disable_thinking=disable_thinking, json_mode=json_mode,
                                        context_guard=context_guard),
                    timeout=max(5, int(node.get("timeout") or 45)),
                )
                latency = int((time.monotonic() - started) * 1000)
                if str(text or "").strip():
                    self.chain.record(node, self.purpose, True, latency)
                    return str(text).strip(), usage
                raise RuntimeError("返回为空")
            except Exception as error:
                latency = int((time.monotonic() - started) * 1000)
                message = f"{type(error).__name__}: {error}"[:240]
                self.chain.record(node, self.purpose, False, latency, message)
                errors.append(f'{node.get("name")}: {message}')
                logger.warning(f"[vision-chain] 节点失败 model={node.get('model')} purpose={self.purpose} {message}")
        if self.fallback and self.fallback.available:
            started = time.monotonic()
            try:
                text, usage = await self.fallback.chat_with_usage(
                    messages, temperature, max_tokens, disable_thinking=disable_thinking,
                    json_mode=json_mode, context_guard=context_guard,
                )
                self.chain.record({"id": "legacy", "name": "旧版单模型",
                                   "model": getattr(self.fallback, "model", "")},
                                  self.purpose, True, int((time.monotonic() - started) * 1000))
                return str(text or "").strip(), usage
            except Exception as error:
                message = f"{type(error).__name__}: {error}"[:240]
                self.chain.record({"id": "legacy", "name": "旧版单模型",
                                   "model": getattr(self.fallback, "model", "")},
                                  self.purpose, False, int((time.monotonic() - started) * 1000), message)
                errors.append("旧版单模型: " + message)
        raise RuntimeError("模型链路全部失败：" + "；".join(errors[-3:]))

    async def chat(self, messages: list, temperature: float = 0.8, max_tokens: int = 1024,
                   disable_thinking: bool = False, json_mode: bool = False) -> str:
        nodes = self.chain.nodes_for(self.purpose)
        if not nodes:
            if self.fallback and self.fallback.available:
                return await self.fallback.chat(messages, temperature, max_tokens,
                                                disable_thinking=disable_thinking, json_mode=json_mode)
            raise RuntimeError("视觉链路没有可用节点")
        errors = []
        for node in nodes:
            llm = LLMClient(node["base_url"], node.get("api_key") or "local", node["model"])
            if not llm.available:
                errors.append(f'{node.get("name")}: 未配置 Key/模型')
                continue
            content = messages
            prompt = str(node.get("prompt") or "").strip()
            if prompt:
                content = [{"role": "system", "content": prompt}] + list(messages)
            started = time.monotonic()
            try:
                text = await asyncio.wait_for(
                    llm.chat(content, temperature, max_tokens,
                             disable_thinking=disable_thinking, json_mode=json_mode),
                    timeout=max(5, int(node.get("timeout") or 45)),
                )
                latency = int((time.monotonic() - started) * 1000)
                if str(text or "").strip():
                    self.chain.record(node, self.purpose, True, latency)
                    return str(text).strip()
                raise RuntimeError("返回为空")
            except Exception as error:
                latency = int((time.monotonic() - started) * 1000)
                message = f"{type(error).__name__}: {error}"[:240]
                self.chain.record(node, self.purpose, False, latency, message)
                errors.append(f'{node.get("name")}: {message}')
                logger.warning(f"[vision-chain] 节点失败 model={node.get('model')} purpose={self.purpose} {message}")
        if self.fallback and self.fallback.available:
            started = time.monotonic()
            try:
                text = await self.fallback.chat(messages, temperature, max_tokens,
                                                disable_thinking=disable_thinking, json_mode=json_mode)
                self.chain.record({"id": "legacy", "name": "旧版单模型",
                                   "model": getattr(self.fallback, "model", "")},
                                  self.purpose, True, int((time.monotonic() - started) * 1000))
                return str(text or "").strip()
            except Exception as error:
                message = f"{type(error).__name__}: {error}"[:240]
                self.chain.record({"id": "legacy", "name": "旧版单模型",
                                   "model": getattr(self.fallback, "model", "")},
                                  self.purpose, False, int((time.monotonic() - started) * 1000), message)
                errors.append("旧版单模型: " + message)
        raise RuntimeError("视觉链路全部失败：" + "；".join(errors[-3:]))
