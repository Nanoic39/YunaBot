"""报错收集（errorlog）插件。

通过 loguru 钩子自动记录所有 bot 相关的报错：
- 报错时间、级别、日志内容、异常类型、完整堆栈
- 报错所属插件 / 模块 / 函数 / 行号（自动归因）
- 相同报错在短时间内自动合并计数
数据持久化到 errors.json，超管可通过 /errlog 命令查看。
"""

import json
import re
import time
import traceback
from datetime import datetime
from pathlib import Path
from threading import Lock

from nonebot import get_loaded_plugins, get_plugin_config, logger, on_command
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.matcher import Matcher
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="报错收集",
    description="自动记录所有 bot 相关报错的时间、日志、原因、所属插件等信息",
    usage=(
        "超管发送：\n"
        "/errlog        查看最近报错列表\n"
        "/errlog <序号>  查看某条报错详情（含堆栈）\n"
        "/errlog stat   按插件/级别统计\n"
        "/errlog clear  清空全部记录"
    ),
)

_cfg = get_plugin_config(Config)

PLUGIN_DIR = Path(__file__).resolve().parent
ERROR_FILE = PLUGIN_DIR / _cfg.errorlog_file

_lock = Lock()
_in_sink = False  # 防止 sink 内部再次记录造成死循环


# ---------------------------------------------------------------------------
# 报错归因
# ---------------------------------------------------------------------------


def _plugin_candidates() -> list[tuple[str, str]]:
    """[(模块前缀, 插件 id)]，按前缀长度降序（越具体越优先）。"""
    cands = []
    for p in get_loaded_plugins():
        mod = getattr(p, "module", None)
        mn = (getattr(mod, "__name__", "") or "").strip()
        if mn:
            cands.append((mn, p.id_))
    cands.sort(key=lambda x: len(x[0]), reverse=True)
    return cands


def _attributed_plugin(record: dict, tb_text: str, frames: list[str]) -> str:
    """根据日志模块、堆栈帧、堆栈文本推断报错所属插件。"""
    cands = _plugin_candidates()
    if not cands:
        return "未知/系统"

    # 1) 日志调用点所在模块（loguru 的 module 字段才是调用 logger 的真实模块）
    rec_module = str(record.get("module") or "")
    for pre, pid in cands:
        if rec_module.startswith(pre):
            return pid

    # 2) 异常堆栈帧（越靠前的帧越接近插件自己的代码）
    for m in frames:
        for pre, pid in cands:
            if m.startswith(pre):
                return pid

    # 3) 堆栈文本模糊匹配
    best = ""
    for pre, pid in cands:
        if pre in tb_text and len(pre) > len(best):
            best = pid
    if best:
        return best

    return "未知/系统"


def _exc_type(exc) -> str:
    try:
        t = exc.type
        return f"{t.__module__}.{t.__name__}" if getattr(t, "__module__", None) else str(t)
    except Exception:
        return ""


def _fmt_exception(exc) -> str:
    """尽量格式化完整堆栈文本。"""
    try:
        if hasattr(exc, "format_exception"):
            lines = exc.format_exception()
            if lines:
                return "".join(lines)
    except Exception:
        pass
    try:
        return "".join(traceback.format_exception(exc.type, exc.value, exc.traceback))
    except Exception:
        pass
    frames = []
    try:
        for f in exc.traceback.frames:
            frames.append(
                f'  File "{getattr(f, "file", "?")}", line {getattr(f, "line", "?")}, in {getattr(f, "name", "?")}'
            )
    except Exception:
        pass
    return "\n".join(frames) or str(getattr(exc, "value", ""))


def _frame_modules(exc) -> list[str]:
    out: list[str] = []
    try:
        for f in exc.traceback.frames:
            m = getattr(f, "module", "") or ""
            if not m:
                try:
                    m = f.globals.get("__name__", "")
                except Exception:
                    m = ""
            if m:
                out.append(m)
    except Exception:
        pass
    return out


# ---------------------------------------------------------------------------
# loguru 钩子
# ---------------------------------------------------------------------------


def _error_sink(message) -> None:
    global _in_sink
    if _in_sink:
        return
    _in_sink = True
    try:
        record = getattr(message, "record", None)
        if record is None:
            return
        level = str(record.get("level").name if record.get("level") else "ERROR")
        msg = str(record.get("message") or "")
        exc = record.get("exception")
        exc_type, tb_text, frames = "", "", []
        if exc is not None:
            exc_type = _exc_type(exc)
            tb_text = _fmt_exception(exc)
            frames = _frame_modules(exc)
        plugin = _attributed_plugin(record, tb_text, frames)

        now = time.time()
        entry = {
            "time": datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp": now,
            "level": level,
            "plugin": plugin,
            "module": str(record.get("module") or ""),
            "function": str(record.get("function") or ""),
            "line": record.get("line"),
            "message": msg,
            "exception_type": exc_type,
            "traceback": tb_text[: _cfg.errorlog_traceback_limit],
            "count": 1,
        }
        _store(entry)
    except Exception:
        pass
    finally:
        _in_sink = False


def _load() -> list:
    if not ERROR_FILE.exists():
        return []
    try:
        data = json.loads(ERROR_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(data: list) -> None:
    try:
        ERROR_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _store(entry: dict) -> None:
    with _lock:
        try:
            data = _load()
        except Exception:
            data = []
        key = (
            entry.get("plugin"),
            entry.get("module"),
            entry.get("function"),
            entry.get("line"),
            entry.get("message", "")[:120],
        )
        if data:
            last = data[-1]
            last_key = (
                last.get("plugin"),
                last.get("module"),
                last.get("function"),
                last.get("line"),
                last.get("message", "")[:120],
            )
            if last_key == key and entry["timestamp"] - last.get("timestamp", 0) < _cfg.errorlog_dedup_seconds:
                last["count"] = last.get("count", 1) + 1
                last["last_time"] = entry["time"]
                last["timestamp"] = entry["timestamp"]
                _save(data)
                return
        data.append(entry)
        if len(data) > _cfg.errorlog_max_entries:
            data = data[-_cfg.errorlog_max_entries :]
        _save(data)


if _cfg.errorlog_enabled:
    # 捕获 ERROR 及以上级别日志（不重复追加：模块仅导入一次）
    logger.add(_error_sink, level="ERROR", enqueue=False, backtrace=False, diagnose=False)


# ---------------------------------------------------------------------------
# 查看命令
# ---------------------------------------------------------------------------

_err_cmd = on_command("errlog", aliases={"报错", "错误记录"}, permission=SUPERUSER, priority=5, block=True)


def _chunk(text: str, size: int) -> list[str]:
    return [text[i : i + size] for i in range(0, len(text), size)] or [""]


@_err_cmd.handle()
async def _handle(event: MessageEvent, matcher: Matcher):
    args = str(event.message).strip()

    if args == "clear":
        with _lock:
            try:
                ERROR_FILE.unlink()
            except FileNotFoundError:
                pass
        await matcher.finish("已清空报错记录。")

    if args == "stat":
        data = _load()
        by_plugin: dict[str, int] = {}
        by_level: dict[str, int] = {}
        for e in data:
            by_plugin[str(e.get("plugin") or "未知/系统")] = by_plugin.get(str(e.get("plugin") or "未知/系统"), 0) + int(
                e.get("count", 1)
            )
            by_level[str(e.get("level") or "?")] = by_level.get(str(e.get("level") or "?"), 0) + int(e.get("count", 1))
        text = [f"报错统计（共 {len(data)} 条记录）", "", "【按插件】"]
        for k, v in sorted(by_plugin.items(), key=lambda x: -x[1]):
            text.append(f"  {k}: {v}")
        text.append("")
        text.append("【按级别】")
        for k, v in by_level.items():
            text.append(f"  {k}: {v}")
        await matcher.finish("\n".join(text))

    m = re.fullmatch(r"\d+", args)
    if m:
        idx = int(m.group(0))
        data = _load()
        if not data or not (1 <= idx <= len(data)):
            await matcher.finish(f"序号超出范围（共 {len(data)} 条）。")
        e = data[idx - 1]
        tb = str(e.get("traceback") or "").strip()
        lines = [
            f"#{idx} [{e.get('level')}] {e.get('time')}（出现 {e.get('count', 1)} 次）",
            f"插件：{e.get('plugin')}",
            f"位置：{e.get('module')}:{e.get('function')}:{e.get('line')}",
            f"类型：{e.get('exception_type') or '-'}",
            f"日志：{e.get('message')}",
        ]
        if tb:
            lines.append("")
            lines.append("堆栈：")
            lines.append(tb)
        chunks = _chunk("\n".join(lines), 1500)
        for c in chunks[:-1]:
            await matcher.send(c)
        await matcher.finish(chunks[-1])

    data = _load()
    if not data:
        await matcher.finish("暂无报错记录。")
    n = _cfg.errorlog_view_count
    count = min(n, len(data))
    start_idx = len(data) - count + 1
    lines = [f"最近 {count} 条报错（/errlog <序号> 看详情）："]
    for i, e in enumerate(data[-count:]):
        msg = (str(e.get("message") or "").replace("\n", " "))[:60]
        time_str = str(e.get("time") or "")
        lines.append(
            f"#{start_idx + i} [{e.get('level')}] {time_str[5:16]} {e.get('plugin')} ×{e.get('count', 1)} {msg}"
        )
    lines.append("")
    lines.append("可用：/errlog <序号> 详情 · /errlog stat 统计 · /errlog clear 清空")
    await matcher.finish("\n".join(lines))
