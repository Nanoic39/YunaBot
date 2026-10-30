"""早报模块系统基座：模块基类、注册表、上下文与带 TTL 的缓存。

约定每个内容模块实现 `collect()`（异步取数，返回可渲染数据或 None 表示跳过）、
`text()`（纯文本渲染）与 `draw()`（图片渲染，把内容画到画布上并返回新的 y 坐标）。
主流程只依赖本文件定义的接口，新增板块只需注册一个模块，无需改动主流程。
"""

from __future__ import annotations

import time
from typing import Any


class ModuleContext:
    """单次早报组装时的上下文。"""

    def __init__(self, gid: str, gc: dict, now, date_str: str, weekday: str):
        self.gid = gid
        self.gc = gc or {}
        self.now = now
        self.date_str = date_str
        self.weekday = weekday
        # 群级模块标题覆盖：{module_name: custom_title}
        titles = self.gc.get("module_titles")
        self.titles: dict = titles if isinstance(titles, dict) else {}
        # 当前渲染列的几何信息（由渲染层在绘制每个模块前设置）：
        # col_x0 为列内容区左边界，col_w 为列内容区宽度，二者均由 render 层注入。
        self.col_x0: int = 0
        self.col_w: int = 0
        # 渲染层注入的最小高度提示（0 表示不限制）；首行等高对齐时用于让本卡重绘到目标高度。
        self.min_height: int = 0


class MorningModule:
    """早报内容模块基类。"""

    # 唯一标识（用于群配置里启用/排序）
    name: str = ""
    # 展示标题
    title: str = ""
    # 是否为新群默认启用
    default_enabled: bool = False
    # 缓存时长（秒）：>0 按秒过期；0 不使用缓存；<0 不按时间过期（改由 cache_key 决定刷新，
    # 例如每日一图按日期 key，跨天过 0 点自然刷新）
    cache_ttl: int = 0
    # 是否横跨整行（True 时在双列瀑布流中单独占满一行，适合宽幅卡片/大图）
    full_width: bool = False
    # 首行等高对齐：为 True 时，若同排另一栏的卡片更高，渲染层会通过 ctx.min_height
    # 注入目标高度并重绘本卡，使首行两栏顶部齐平（模块自身无需感知邻居）。
    align_top: bool = False

    async def collect(self, ctx: ModuleContext) -> Any | None:
        """拉取并归一化数据；返回 None 表示本模块今天无内容（自动跳过）。"""
        raise NotImplementedError

    def text(self, data: Any, ctx: ModuleContext) -> list[str]:
        """纯文本渲染，返回若干行；每行不含前导符号。"""
        return []

    def draw(self, img, y: int, data: Any, ctx: ModuleContext) -> int:
        """图片渲染：把内容绘制到 img 上，返回绘制后的新 y 坐标。"""
        return y

    # 可选：自定义缓存键（默认按群隔离）
    def cache_key(self, ctx: ModuleContext) -> str:
        return ctx.gid


# 注册表：name -> 模块实例
MODULES: dict[str, MorningModule] = {}


def register(mod: MorningModule) -> MorningModule:
    if not mod.name:
        raise ValueError("模块必须有 name")
    MODULES[mod.name] = mod
    return mod


def default_modules() -> list[str]:
    """按注册顺序返回默认启用的模块名。"""
    return [m.name for m in MODULES.values() if m.default_enabled]


# ---------- 带 TTL 的内存缓存 ----------
_cache: dict[str, tuple[float, Any]] = {}


def cache_get(key: str, ttl: int) -> Any | None:
    """读取缓存：ttl==0 不使用缓存；ttl>0 按秒过期；ttl<0 不按时间过期（仅靠 key 区分）。"""
    if ttl == 0:
        return None
    item = _cache.get(key)
    if not item:
        return None
    ts, data = item
    if ttl > 0 and time.time() - ts > ttl:
        _cache.pop(key, None)
        return None
    return data


def cache_set(key: str, data: Any):
    _cache[key] = (time.time(), data)


def cache_clear(prefix: str | None = None):
    if prefix is None:
        _cache.clear()
        return
    for k in [k for k in _cache if k.startswith(prefix)]:
        _cache.pop(k, None)
