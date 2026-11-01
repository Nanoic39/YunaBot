import base64
import io
import json
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from nonebot import get_driver, get_loaded_plugins, get_plugin_config, logger, on_message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment, PrivateMessageEvent
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from nonebot.rule import CommandRule, Rule, ShellCommandRule

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="功能菜单",
    description="列出 bot 已加载的功能插件并渲染为图片",
    usage="发送 !menu / ！menu / /menu 查看；超管发送 ！menu refresh 或 !menurefresh 刷新",
    config=Config,
    supported_adapters=None,
)

menu_config = get_plugin_config(Config)
driver = get_driver()

PLUGIN_DIR = Path(__file__).parent
MENU_JSON = PLUGIN_DIR / "menu.json"
_SELF_MODULE = __name__
_CACHE_TTL = 24 * 3600

# 渲染参数
_W = 640
_MARGIN = 28
_TITLE = "功能菜单"

_TITLE_SIZE = 40
_SUB_SIZE = 22
_NAME_SIZE = 26
_DESC_SIZE = 21
_CMD_SIZE = 21
_USAGE_SIZE = 20
_BADGE_SIZE = 20

_NAME_LH = _NAME_SIZE + 6
_DESC_LH = _DESC_SIZE + 6
_USAGE_LH = _USAGE_SIZE + 6

_CARD_PAD_X = 18
_CARD_PAD_Y = 16
_CARD_RADIUS = 14
_CARD_GAP = 14
_ACCENT_W = 6
_CONTENT_GAP = 8
_CHIP_PAD_X = 10
_CHIP_PAD_Y = 4

_BG = (246, 247, 250, 255)
_TITLE_COLOR = (28, 28, 32, 255)
_SUB_COLOR = (120, 120, 128, 255)
_CARD_BG = (255, 255, 255, 255)
_CARD_BORDER = (226, 227, 233, 255)
_NAME_COLOR = (33, 33, 38, 255)
_DESC_COLOR = (108, 108, 118, 255)
_USAGE_COLOR = (140, 140, 150, 255)

# 触发方式标注样式：command=可指令调用，event=事件触发，library=库/依赖
_TRIGGER_STYLE = {
    "command": {"label": "指令", "accent": (35, 154, 100), "bg": (232, 245, 238), "fg": (24, 133, 82)},
    "event": {"label": "事件", "accent": (52, 111, 238), "bg": (233, 240, 253), "fg": (40, 99, 212)},
    "library": {"label": "库", "accent": (150, 150, 158), "bg": (236, 236, 240), "fg": (118, 118, 126)},
}

_FONT_BOLD = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]
_FONT_REGULAR = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\Deng.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
]


def _load_font(size: int, bold: bool = False):
    for path in (_FONT_BOLD if bold else _FONT_REGULAR):
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size, index=0)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap(text: str, font, max_width: int) -> list[str]:
    lines = []
    line = ""
    for ch in text:
        if font.getlength(line + ch) <= max_width:
            line += ch
        else:
            if line:
                lines.append(line)
            line = ch
    if line:
        lines.append(line)
    return lines


def _text_height(font, text: str = "Ag") -> int:
    bbox = font.getbbox(text)
    return bbox[3] - bbox[1]


def _command_start() -> str:
    try:
        starts = get_driver().config.command_start
        for prefer in ("/", "!", "！"):
            if prefer in starts:
                return prefer
        for s in starts:
            if s:
                return s
    except Exception:
        pass
    return "/"


def _command_sep() -> str:
    try:
        seps = get_driver().config.command_sep
        if seps:
            return sorted(seps)[0]
    except Exception:
        pass
    return "."


def _extract_commands(plugin) -> list[str]:
    """从 on_command / on_shell_command 注册的 matcher 中提取指令及别名。"""
    sep = _command_sep()
    cmds: set[str] = set()
    for matcher in plugin.matcher:
        checkers = getattr(matcher, "rule", None)
        if checkers is None:
            continue
        for checker in getattr(checkers, "checkers", ()):
            call = getattr(checker, "call", None)
            if isinstance(call, (CommandRule, ShellCommandRule)):
                for cmd in call.cmds:
                    cmds.add(sep.join(cmd))
    return sorted(cmds)


def _derive_type_and_commands(plugin, meta) -> tuple[str, list[str]]:
    """根据插件元信息与 matcher 推导触发类型与指令列表。"""
    if meta and meta.type == "library":
        return "library", []
    auto = _extract_commands(plugin)
    if auto:
        start = _command_start()
        return "command", [f"{start}{c}" for c in auto]
    return "event", []


def _load_menu_config() -> dict:
    if not MENU_JSON.exists():
        return {}
    try:
        data = json.loads(MENU_JSON.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.error(f"【菜单】读取 menu.json 失败：{e}")
        return {}


def _auto_write_menu_config(force: bool = False) -> bool:
    """启动时补齐 menu.json 缺失字段；force=True 时刷新自动条目以适配新功能。"""
    cfg = _load_menu_config()
    plugins = cfg.get("plugins", {})
    if not isinstance(plugins, dict):
        plugins = {}

    changed = False
    for plugin in get_loaded_plugins():
        if plugin.module_name == _SELF_MODULE:
            continue
        meta = plugin.metadata
        if meta is None:
            continue
        pid = plugin.id_

        entry = plugins.get(pid)
        if not isinstance(entry, dict):
            entry = {}
            plugins[pid] = entry

        d_trigger, d_cmds = _derive_type_and_commands(plugin, meta)
        has_manual = (
            isinstance(entry.get("commands"), list)
            and any(str(c).strip() for c in entry["commands"])
        )
        if meta and meta.type == "library":
            trigger = "library"
        elif has_manual or d_trigger == "command":
            trigger = "command"
        else:
            trigger = "event"

        manual = bool(entry.get("manual"))
        if "name" not in entry:
            entry["name"] = meta.name or pid
            changed = True
        if "description" not in entry or (force and not manual):
            value = meta.description or ""
            if entry.get("description") != value:
                entry["description"] = value
                changed = True
        if "usage" not in entry or (force and not manual):
            value = meta.usage or ""
            if entry.get("usage") != value:
                entry["usage"] = value
                changed = True
        if "show" not in entry:
            entry["show"] = True
            changed = True
        if "type" not in entry or (force and not manual):
            if entry.get("type") != trigger:
                entry["type"] = trigger
                changed = True
        if "commands" not in entry or (force and not manual and not entry.get("commands_manual")):
            if entry.get("commands") != d_cmds:
                entry["commands"] = d_cmds
                changed = True

    if not changed:
        return False

    cfg["exclude"] = cfg.get("exclude", [])
    cfg["plugins"] = plugins
    MENU_JSON.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return True


def _collect_items() -> list[dict]:
    """扫描已加载插件，合并 menu.json 的排除与覆盖配置，并标注触发方式与指令。"""
    cfg = _load_menu_config()
    raw_exclude = cfg.get("exclude", [])
    exclude = {str(x) for x in raw_exclude} if isinstance(raw_exclude, list) else set()
    overrides = cfg.get("plugins", {})
    if not isinstance(overrides, dict):
        overrides = {}

    items = []
    for plugin in get_loaded_plugins():
        if plugin.module_name == _SELF_MODULE:
            continue
        pid = plugin.id_
        if pid in exclude:
            continue

        ov = overrides.get(pid, {})
        if not isinstance(ov, dict):
            ov = {}

        meta = plugin.metadata
        if meta is None and not ov:
            continue

        name = meta.name if meta and meta.name else pid
        desc = meta.description if meta and meta.description else ""
        usage = meta.usage if meta and meta.usage else ""

        if ov.get("name"):
            name = str(ov["name"])
        if ov.get("description"):
            desc = str(ov["description"])
        if ov.get("usage"):
            usage = str(ov["usage"])
        if ov.get("show") is False:
            continue

        d_trigger, d_cmds = _derive_type_and_commands(plugin, meta)

        if "commands" in ov and isinstance(ov["commands"], list):
            commands = [str(c) for c in ov["commands"] if str(c).strip()]
        else:
            commands = d_cmds

        ov_type = ov.get("type")
        if ov_type in ("command", "event", "library"):
            trigger = ov_type
        elif meta and meta.type == "library":
            trigger = "library"
        elif commands:
            trigger = "command"
        else:
            trigger = "event"

        if trigger != "command":
            commands = []

        items.append(
            {
                "name": name,
                "description": desc,
                "usage": usage,
                "trigger": trigger,
                "commands": commands,
            }
        )

    order = {"command": 0, "event": 1, "library": 2}
    items.sort(key=lambda x: (order.get(x["trigger"], 3), x["name"]))
    return items


def _lighten(color: tuple[int, int, int, int], ratio: float = 0.85) -> tuple[int, int, int, int]:
    return tuple(int(c + (255 - c) * ratio) for c in color)


def _render(items: list[dict]) -> bytes:
    font_title = _load_font(_TITLE_SIZE, bold=True)
    font_sub = _load_font(_SUB_SIZE)
    font_name = _load_font(_NAME_SIZE, bold=True)
    font_desc = _load_font(_DESC_SIZE)
    font_cmd = _load_font(_CMD_SIZE)
    font_usage = _load_font(_USAGE_SIZE)
    font_badge = _load_font(_BADGE_SIZE, bold=True)

    content_left = _MARGIN + _ACCENT_W + 12
    content_right = _W - _MARGIN - _CARD_PAD_X
    content_w = content_right - content_left

    def chip_width(text: str) -> int:
        return int(font_cmd.getlength(text)) + _CHIP_PAD_X * 2

    chip_h = _text_height(font_cmd) + _CHIP_PAD_Y * 2

    entries = []
    for it in items:
        name_lines = _wrap(it["name"], font_name, content_w)
        desc_lines = _wrap(it["description"], font_desc, content_w) if it["description"] else []

        if it["trigger"] == "command" and it["commands"]:
            chips = it["commands"]
        else:
            chips = []

        if it["trigger"] == "command" and it["usage"]:
            usage_lines = _wrap(f"说明：{it['usage'].replace(chr(10), ' ')}", font_usage, content_w)
        elif it["trigger"] == "event" and it["usage"]:
            usage_lines = _wrap(f"触发：{it['usage'].replace(chr(10), ' ')}", font_usage, content_w)
        else:
            usage_lines = []

        # 计算 chips 换行布局
        chip_rows = []
        row = []
        cur_w = 0
        for chip in chips:
            cw = chip_width(chip)
            if row and cur_w + cw > content_w:
                chip_rows.append(row)
                row = []
                cur_w = 0
            row.append(chip)
            cur_w += cw + _CONTENT_GAP
        if row:
            chip_rows.append(row)

        # 头部高度 = max(徽章高度, 名称高度)
        badge_h = _text_height(font_badge) + _CHIP_PAD_Y * 2
        header_h = max(badge_h, len(name_lines) * _NAME_LH)

        content_h = header_h
        if desc_lines:
            content_h += _CONTENT_GAP + len(desc_lines) * _DESC_LH
        if chip_rows:
            content_h += _CONTENT_GAP + len(chip_rows) * chip_h + max(0, len(chip_rows) - 1) * _CONTENT_GAP
        if usage_lines:
            content_h += _CONTENT_GAP + len(usage_lines) * _USAGE_LH

        entries.append(
            {
                "it": it,
                "name_lines": name_lines,
                "desc_lines": desc_lines,
                "chip_rows": chip_rows,
                "usage_lines": usage_lines,
                "header_h": header_h,
                "content_h": content_h,
            }
        )

    title_h = _TITLE_SIZE + 10
    sub_h = _SUB_SIZE + 18
    body_h = sum(e["content_h"] + _CARD_PAD_Y * 2 + _CARD_GAP for e in entries)
    total_h = _MARGIN + title_h + sub_h + body_h + _MARGIN

    img = Image.new("RGBA", (_W, total_h), _BG)
    draw = ImageDraw.Draw(img)

    y = _MARGIN
    draw.text((_MARGIN, y), _TITLE, font=font_title, fill=_TITLE_COLOR)
    y += title_h
    draw.text((_MARGIN, y), f"共 {len(items)} 项功能", font=font_sub, fill=_SUB_COLOR)
    y += sub_h

    for e in entries:
        it = e["it"]
        style = _TRIGGER_STYLE[it["trigger"]]
        accent = style["accent"]

        card_top = y
        card_bottom = y + e["content_h"] + _CARD_PAD_Y * 2

        # 卡片背景与边框
        draw.rounded_rectangle(
            [_MARGIN, card_top, _W - _MARGIN, card_bottom],
            radius=_CARD_RADIUS,
            fill=_CARD_BG,
            outline=_CARD_BORDER,
            width=1,
        )
        # 左侧触发色条
        draw.rounded_rectangle(
            [_MARGIN, card_top, _MARGIN + _ACCENT_W, card_bottom],
            radius=_CARD_RADIUS,
            fill=accent,
        )

        cy = card_top + _CARD_PAD_Y
        cx = content_left

        # 头部：触发徽章 + 名称
        badge_text = style["label"]
        badge_w = int(font_badge.getlength(badge_text)) + _CHIP_PAD_X * 2
        badge_h = _text_height(font_badge) + _CHIP_PAD_Y * 2
        draw.rounded_rectangle(
            [cx, cy, cx + badge_w, cy + badge_h],
            radius=badge_h // 2,
            fill=style["bg"],
        )
        bbox = font_badge.getbbox(badge_text)
        draw.text(
            (cx + _CHIP_PAD_X, cy + (badge_h - (bbox[3] - bbox[1])) // 2 - bbox[1]),
            badge_text,
            font=font_badge,
            fill=style["fg"],
        )

        name_x = cx + badge_w + 10
        name_y = cy + (e["header_h"] - len(e["name_lines"]) * _NAME_LH) // 2
        for line in e["name_lines"]:
            draw.text((name_x, name_y), line, font=font_name, fill=_NAME_COLOR)
            name_y += _NAME_LH

        cy += e["header_h"]

        if e["desc_lines"]:
            cy += _CONTENT_GAP
            for line in e["desc_lines"]:
                draw.text((cx, cy), line, font=font_desc, fill=_DESC_COLOR)
                cy += _DESC_LH

        if e["chip_rows"]:
            cy += _CONTENT_GAP
            chip_bg = _lighten(accent)
            for idx, row in enumerate(e["chip_rows"]):
                rx = cx
                for chip in row:
                    cw = chip_width(chip)
                    draw.rounded_rectangle(
                        [rx, cy, rx + cw, cy + chip_h],
                        radius=chip_h // 2,
                        fill=chip_bg,
                    )
                    bbox = font_cmd.getbbox(chip)
                    draw.text(
                        (rx + _CHIP_PAD_X, cy + (chip_h - (bbox[3] - bbox[1])) // 2 - bbox[1]),
                        chip,
                        font=font_cmd,
                        fill=accent,
                    )
                    rx += cw + _CONTENT_GAP
                cy += chip_h
                if idx < len(e["chip_rows"]) - 1:
                    cy += _CONTENT_GAP

        if e["usage_lines"]:
            cy += _CONTENT_GAP
            for line in e["usage_lines"]:
                draw.text((cx, cy), line, font=font_usage, fill=_USAGE_COLOR)
                cy += _USAGE_LH

        y = card_bottom + _CARD_GAP

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# 内存缓存：图片复用，menu.json 未变化且 24 小时内有效
_menu_cache: dict = {}


def _menu_mtime() -> float:
    return MENU_JSON.stat().st_mtime if MENU_JSON.exists() else 0.0


def _build_menu() -> str | None:
    items = _collect_items()
    if not items:
        _menu_cache.clear()
        return None
    b64 = base64.b64encode(_render(items)).decode()
    _menu_cache.update(
        mtime=_menu_mtime(), b64=b64, count=len(items), built_at=time.time()
    )
    return b64


def _ensure_menu() -> str | None:
    mtime = _menu_mtime()
    if _menu_cache.get("b64") and _menu_cache.get("mtime") == mtime:
        if time.time() - _menu_cache.get("built_at", 0) < _CACHE_TTL:
            return _menu_cache["b64"]
    return _build_menu()


@driver.on_startup
async def _warm_menu():
    try:
        if _auto_write_menu_config(force=True):
            logger.info("【菜单】已按当前插件功能刷新 menu.json")
        if _build_menu():
            logger.info(f"【菜单】已生成 {_menu_cache.get('count', 0)} 项功能菜单")
    except Exception as e:
        logger.error(f"【菜单】启动时生成失败：{e}")


def _is_menu_command(event) -> bool:
    if not isinstance(event, (GroupMessageEvent, PrivateMessageEvent)):
        return False
    text = event.get_message().extract_plain_text().strip().lower().replace("！", "!")
    return text in {"!menu", "/menu"}


menu_matcher = on_message(rule=Rule(_is_menu_command), priority=10, block=True)


@menu_matcher.handle()
async def _handle_menu():
    if not menu_config.menu_enabled:
        await menu_matcher.finish()
    try:
        b64 = _ensure_menu()
    except Exception as e:
        logger.error(f"【菜单】渲染失败：{e}")
        await menu_matcher.finish("菜单生成失败，请稍后重试。")
    if not b64:
        await menu_matcher.finish("暂无可显示的插件。")
    await menu_matcher.finish(MessageSegment.image(f"base64://{b64}"))


def _is_refresh_command(event) -> bool:
    if not isinstance(event, (GroupMessageEvent, PrivateMessageEvent)):
        return False
    text = event.get_message().extract_plain_text().strip().lower().replace("！", "!")
    text = " ".join(text.split())
    return text in {"!menurefresh", "/menurefresh", "!menu refresh", "/menu refresh", "!menu 刷新", "/menu 刷新"}


menu_refresh_matcher = on_message(
    rule=Rule(_is_refresh_command), permission=SUPERUSER, priority=10, block=True
)


@menu_refresh_matcher.handle()
async def _handle_menu_refresh():
    try:
        synced = _auto_write_menu_config(force=True)
        _menu_cache.clear()
        b64 = _build_menu()
    except Exception as e:
        logger.error(f"【菜单】强制刷新失败：{e}")
        await menu_refresh_matcher.finish("菜单刷新失败，请稍后重试。")
    if not b64:
        await menu_refresh_matcher.finish("暂无可显示的插件。")
    note = "（已按当前功能刷新菜单配置）" if synced else ""
    logger.info(f"【菜单】超管强制刷新，共 {_menu_cache.get('count', 0)} 项")
    await menu_refresh_matcher.finish(
        MessageSegment.image(f"base64://{b64}") + f"\n菜单已强制刷新{note}"
    )
