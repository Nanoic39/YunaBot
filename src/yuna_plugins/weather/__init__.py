import base64
import io
import json
import math
import re
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont
from nonebot import get_driver, get_plugin_config, logger, on_command, on_message
from nonebot.adapters.onebot.v11 import (
    GroupMessageEvent,
    MessageEvent,
    MessageSegment,
    PrivateMessageEvent,
)
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata
from nonebot.rule import Rule

from .config import Config

__plugin_meta__ = PluginMetadata(
    name="天气查询",
    description="查询城市实时天气，支持和风天气 / wttr.in 数据源",
    usage="发送「南京天气」直接查询；发送「今日天气」后回复城市；或 /天气 南京",
    type="application",
    config=Config,
    supported_adapters={"onebot.v11"},
)

cfg = get_plugin_config(Config)

# 直接命令：南京天气 / 北京天气 等（城市名 + 天气）
_CITY_RE = re.compile(r"^([\u4e00-\u9fa5]{2,10})天气$")
# 对话式触发词
_CONV_WORDS = {"今日天气", "今天天气", "现在天气"}

# 合法城市/国家白名单（来自 city.json，由行政区域与国家标准地名数据生成），
# 只有命中名单的「XX天气」才会触发查询，避免普通语句被误判
_CITY_FILE = Path(__file__).with_name("city.json")


def _load_places() -> frozenset[str]:
    try:
        data = json.loads(_CITY_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning(f"【天气】未找到 {_CITY_FILE.name}，城市匹配将不做白名单校验")
        return frozenset()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"【天气】{_CITY_FILE.name} 解析失败：{e}")
        return frozenset()
    names: set[str] = set()
    for key in ("provinces", "cities", "countries"):
        for n in data.get(key) or []:
            n = str(n).strip()
            if n:
                names.add(n)
    return frozenset(names)


_PLACES = _load_places()

# 等待用户回复城市的会话状态
_pending: set[str] = set()


def _scope_key(event: MessageEvent) -> str | None:
    if isinstance(event, GroupMessageEvent):
        return f"g:{event.group_id}:{event.user_id}"
    if isinstance(event, PrivateMessageEvent):
        return f"p:{event.user_id}"
    return None


_starts_cache: list[str] | None = None


def _starts() -> list[str]:
    """获取已配置的命令前缀（按长度降序，便于优先剥离最长前缀）。"""
    global _starts_cache
    if _starts_cache is None:
        try:
            starts = get_driver().config.command_start
            _starts_cache = sorted((s for s in starts if s), key=len, reverse=True)
        except Exception:
            _starts_cache = ["/", "!", "！"]
    return _starts_cache


def _plain(event: MessageEvent) -> str:
    """取消息纯文本，并剥离前导命令前缀（如 !、/、！）。"""
    text = event.get_message().extract_plain_text().strip()
    for s in _starts():
        if text.startswith(s):
            return text[len(s):].strip()
    return text


# ---------- 数据源 ----------
async def _qweather(city: str) -> dict | None:
    host = cfg.weather_qweather_host
    key = cfg.weather_qweather_api_key
    headers = {"X-QW-Api-Key": key}
    try:
        async with httpx.AsyncClient(timeout=cfg.weather_timeout) as client:
            geo = await client.get(
                f"https://{host}/geo/v2/city/lookup",
                params={"location": city},
                headers=headers,
            )
            if geo.status_code != 200:
                logger.error(
                    f"【天气】GeoAPI 状态异常：{geo.status_code} {geo.text[:200]}"
                )
                return None
            gdata = geo.json()
            if gdata.get("code") != "200":
                logger.error(f"【天气】GeoAPI 返回错误：{gdata.get('code')}")
                return None
            locations = gdata.get("location") or []
            if not locations:
                return None
            loc = locations[0]
            loc_id = loc.get("id")
            name = loc.get("name") or city
            if not loc_id:
                return None
            now = await client.get(
                f"https://{host}/v7/weather/now",
                params={"location": loc_id},
                headers=headers,
            )
            if now.status_code != 200:
                logger.error(
                    f"【天气】Now API 状态异常：{now.status_code} {now.text[:200]}"
                )
                return None
            ndata = now.json()
    except Exception as e:
        logger.error(f"【天气】和风天气获取失败 {city}：{e}")
        return None

    if ndata.get("code") != "200":
        return None
    cur = ndata.get("now") or {}
    if not cur:
        return None
    wdir = cur.get("windDir") or ""
    wscale = cur.get("windScale") or ""
    wind = f"{wdir} {wscale} 级".strip() if (wdir or wscale) else ""
    return {
        "city": name,
        "temp": cur.get("temp"),
        "feels_like": cur.get("feelsLike"),
        "desc": cur.get("text"),
        "humidity": cur.get("humidity"),
        "wind": wind,
        "icon": cur.get("icon"),
    }


async def _wttr(city: str) -> dict | None:
    url = cfg.weather_wttr_url.format(city=city)
    try:
        async with httpx.AsyncClient(timeout=cfg.weather_timeout) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None
            payload = resp.json()
    except Exception as e:
        logger.error(f"【天气】wttr 获取失败 {city}：{e}")
        return None
    try:
        cur = (payload.get("current_condition") or [{}])[0]
        desc = ""
        for item in cur.get("lang_zh") or []:
            if item.get("value"):
                desc = item["value"]
                break
        if not desc:
            for item in cur.get("weatherDesc") or []:
                if item.get("value"):
                    desc = item["value"]
                    break
        wd = cur.get("winddir16Point") or ""
        ws = cur.get("windspeedKmph") or ""
        wind = f"{wd} {ws} km/h".strip() if (wd or ws) else ""
        return {
            "city": city,
            "temp": cur.get("temp_C"),
            "feels_like": cur.get("FeelsLikeC"),
            "desc": desc,
            "humidity": cur.get("humidity"),
            "wind": wind,
            "icon": None,
        }
    except Exception:
        return None


async def get_weather(city: str) -> dict | None:
    """查询城市实时天气，返回归一化字段，供本插件及 morninginfo 等调用。"""
    city = (city or "").strip() or cfg.weather_default_city
    provider = (cfg.weather_provider or "qweather").strip().lower()
    if provider == "wttr":
        return await _wttr(city)
    return await _qweather(city)


def _format(w: dict) -> str:
    lines = [f"{w.get('city') or cfg.weather_default_city} 天气"]
    lines.append(f"{w.get('desc') or '--'} · {w.get('temp') or '--'}°C")
    extra = []
    if w.get("feels_like"):
        extra.append(f"体感 {w['feels_like']}°C")
    if w.get("humidity"):
        extra.append(f"湿度 {w['humidity']}%")
    if w.get("wind"):
        extra.append(str(w["wind"]))
    if extra:
        lines.append(" · ".join(extra))
    return "\n".join(lines)


# ---------- 图标绘制 ----------
_FONT_BOLD = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]
_FONT_REGULAR = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\Deng.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]


def _load_font(size: int, bold: bool = False):
    for path in _FONT_BOLD if bold else _FONT_REGULAR:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size, index=0)
            except Exception:
                continue
    return ImageFont.load_default()


def _icon_category(code) -> str:
    """把和风天气图标代码归类到绘制类别：sun/suncloud/cloud/rain/snow/fog/wind。"""
    try:
        n = int(str(code).strip())
    except (TypeError, ValueError):
        return "cloud"
    if 300 <= n < 320 or 350 <= n < 360:
        return "rain"
    if 400 <= n < 410 or n in (456, 457):
        return "snow"
    if 500 <= n < 520:
        return "fog"
    if 200 <= n < 215:
        return "wind"
    day = n - 50 if 150 <= n < 160 else n
    if day == 100 or n in (900, 901):
        return "sun"
    if day in (101, 102, 103):
        return "suncloud"
    return "cloud"


_THEMES = {
    "sun": ((88, 170, 255), (56, 118, 218)),
    "suncloud": ((122, 176, 240), (82, 132, 206)),
    "cloud": ((150, 172, 198), (104, 126, 156)),
    "rain": ((106, 144, 188), (62, 94, 136)),
    "snow": ((170, 198, 226), (120, 154, 192)),
    "fog": ((158, 168, 184), (112, 124, 140)),
    "wind": ((148, 180, 212), (100, 134, 170)),
}


def _theme(category: str) -> tuple:
    return _THEMES.get(category, _THEMES["cloud"])


def weather_theme(code) -> tuple:
    """返回按天气图标代码选取的渐变主题色 (top, bottom)，供早报等复用。"""
    return _theme(_icon_category(code))


def _gradient(size: tuple[int, int], top: tuple, bottom: tuple) -> Image.Image:
    w, h = size
    img = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        rgb = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        d.line([(0, y), (w, y)], fill=rgb + (255,))
    return img


def _draw_sun(draw, cx, cy, r, color):
    for i in range(8):
        ang = math.radians(i * 45)
        draw.line(
            [
                (cx + math.cos(ang) * r * 1.4, cy + math.sin(ang) * r * 1.4),
                (cx + math.cos(ang) * r * 1.95, cy + math.sin(ang) * r * 1.95),
            ],
            fill=color,
            width=max(2, int(r * 0.2)),
        )
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)


def _draw_cloud(draw, cx, cy, w, color):
    h = w * 0.66
    x0, y0 = cx - w / 2, cy - h / 2
    draw.ellipse([x0, y0 + h * 0.44, x0 + w, y0 + h], fill=color)
    draw.ellipse([x0 + w * 0.05, y0 + h * 0.16, x0 + w * 0.58, y0 + h * 0.9], fill=color)
    draw.ellipse([x0 + w * 0.36, y0, x0 + w * 0.94, y0 + h * 0.74], fill=color)


def _draw_rain(draw, cx, cy, w, color):
    span = w * 0.78
    step = span / 3
    for i in range(4):
        lx = cx - span / 2 + i * step
        draw.line([(lx, cy), (lx - span * 0.07, cy + span * 0.28)], fill=color, width=3)


def _draw_snow(draw, cx, cy, w, color):
    span = w * 0.78
    step = span / 3
    r = max(2, w * 0.045)
    for i in range(4):
        lx = cx - span / 2 + i * step
        draw.ellipse([lx - r, cy - r, lx + r, cy + r], fill=color)


def _draw_fog(draw, cx, cy, w, color):
    for i in range(3):
        ly = cy + i * w * 0.16
        off = w * 0.08 if i % 2 else 0
        draw.line([(cx - w / 2 + off, ly), (cx + w / 2 - off, ly)], fill=color, width=3)


def _draw_wind(draw, cx, cy, w, color):
    for i, dw in enumerate((0.62, 1.0, 0.78)):
        ly = cy - w * 0.24 + i * w * 0.24
        draw.line([(cx - w / 2, ly), (cx - w / 2 + dw * w, ly)], fill=color, width=3)


def draw_weather_icon(
    draw,
    cx,
    cy,
    size,
    code,
    *,
    cloud=(255, 255, 255, 255),
    sun=(255, 206, 84, 255),
    rain=(150, 205, 255, 255),
    snow=(255, 255, 255, 255),
    fog=(255, 255, 255, 255),
):
    """在 (cx, cy) 处以 size 尺寸绘制天气图标，供天气卡片与早报复用。"""
    cat = _icon_category(code)
    if cat == "sun":
        _draw_sun(draw, cx, cy, size * 0.28, sun)
    elif cat == "suncloud":
        _draw_sun(draw, cx + size * 0.2, cy - size * 0.16, size * 0.19, sun)
        _draw_cloud(draw, cx - size * 0.06, cy + size * 0.1, size * 0.8, cloud)
    elif cat == "rain":
        _draw_cloud(draw, cx, cy - size * 0.12, size * 0.86, cloud)
        _draw_rain(draw, cx, cy + size * 0.28, size * 0.62, rain)
    elif cat == "snow":
        _draw_cloud(draw, cx, cy - size * 0.12, size * 0.86, cloud)
        _draw_snow(draw, cx, cy + size * 0.3, size * 0.62, snow)
    elif cat == "fog":
        _draw_cloud(draw, cx, cy - size * 0.16, size * 0.82, cloud)
        _draw_fog(draw, cx, cy + size * 0.22, size * 0.74, fog)
    elif cat == "wind":
        _draw_wind(draw, cx, cy, size * 0.86, cloud)
    else:
        _draw_cloud(draw, cx, cy, size * 0.88, cloud)


# ---------- 天气卡片 ----------
_WC_W = 640
_WC_H = 470
_WC_PAD = 30


def _center_text(draw, cx, y, text, font, fill):
    draw.text((cx - font.getlength(text) / 2, y), text, font=font, fill=fill)


def render_weather_card(w: dict) -> bytes:
    """把归一化天气数据渲染为带图标的渐变卡片图片。"""
    top, bottom = _theme(_icon_category(w.get("icon")))
    W, H = _WC_W, _WC_H
    white = (255, 255, 255, 255)
    soft = (255, 255, 255, 210)
    cx = W // 2

    # 圆角渐变底
    base = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, W - 1, H - 1], radius=30, fill=255)
    base.paste(_gradient((W, H), top, bottom), (0, 0), mask)

    # 半透明装饰层：先单独绘制再 alpha 合成，避免直接覆盖 alpha 导致文字不可见
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)

    # 右上：天气描述胶囊
    f_pill = _load_font(22, bold=True)
    pill_cx = None
    if w.get("desc"):
        dtxt = str(w["desc"])
        pw = int(f_pill.getlength(dtxt)) + 34
        od.rounded_rectangle(
            [W - _WC_PAD - pw, 34, W - _WC_PAD, 74],
            radius=20,
            fill=(255, 255, 255, 78),
        )
        pill_cx = W - _WC_PAD - pw / 2

    # 底部信息小卡
    details = []
    if w.get("feels_like"):
        details.append(("体感", f"{w['feels_like']}°"))
    if w.get("humidity"):
        details.append(("湿度", f"{w['humidity']}%"))
    if w.get("wind"):
        details.append(("风力", str(w["wind"])))
    geom = []
    if details:
        n = len(details)
        gap = 14
        cw = (W - _WC_PAD * 2 - gap * (n - 1)) / n
        ch_y, ch_h = 378, 64
        for i in range(n):
            x = _WC_PAD + i * (cw + gap)
            od.rounded_rectangle(
                [x, ch_y, x + cw, ch_y + ch_h], radius=16, fill=(255, 255, 255, 60)
            )
            geom.append((x, ch_y))

    base.alpha_composite(overlay)
    draw = ImageDraw.Draw(base)

    # 左上：城市名
    draw.text(
        (_WC_PAD, 26),
        str(w.get("city") or cfg.weather_default_city),
        font=_load_font(38, bold=True),
        fill=white,
    )

    # 右上：天气描述文字
    if pill_cx is not None:
        _center_text(draw, pill_cx, 43, str(w["desc"]), f_pill, white)

    # 中部：大图标
    draw_weather_icon(draw, cx, 186, 150, w.get("icon"))

    # 温度
    temp = f"{w.get('temp')}°" if w.get("temp") else "--°"
    _center_text(draw, cx, 262, temp, _load_font(86, bold=True), white)

    # 底部信息文字
    if details:
        f_lab = _load_font(19)
        f_val = _load_font(25, bold=True)
        for (label, value), (x, ch_y) in zip(details, geom):
            _center_text(draw, x + cw / 2, ch_y + 9, label, f_lab, soft)
            _center_text(draw, x + cw / 2, ch_y + 31, value, f_val, white)

    buf = io.BytesIO()
    base.save(buf, format="PNG")
    return buf.getvalue()


async def _reply(matcher: Matcher, city: str):
    w = await get_weather(city)
    if not w:
        await matcher.finish(f"查询 {(city or cfg.weather_default_city).strip()} 天气失败，请稍后重试。")
    try:
        img = render_weather_card(w)
    except Exception as e:
        logger.error(f"【天气】卡片渲染失败：{e}")
        await matcher.finish(_format(w))
    b64 = base64.b64encode(img).decode()
    await matcher.finish(MessageSegment.image(f"base64://{b64}"))


# ---------- 指令 ----------
# /天气 [城市]
weather_cmd = on_command("天气", aliases={"weather"}, priority=5, block=True)


@weather_cmd.handle()
async def _handle_cmd(matcher: Matcher, args=CommandArg()):
    if not cfg.weather_enabled:
        await matcher.finish("天气功能未开启")
    await _reply(matcher, args.extract_plain_text().strip())


def _is_direct(event: MessageEvent) -> bool:
    if not isinstance(event, (GroupMessageEvent, PrivateMessageEvent)):
        return False
    text = _plain(event)
    if text in _CONV_WORDS:
        return False
    m = _CITY_RE.match(text)
    # 只匹配 city.json 白名单内的城市/国家，避免普通语句被误判
    return bool(m) and m.group(1) in _PLACES


def _is_conv(event: MessageEvent) -> bool:
    if not isinstance(event, (GroupMessageEvent, PrivateMessageEvent)):
        return False
    return _plain(event) in _CONV_WORDS


def _is_pending(event: MessageEvent) -> bool:
    key = _scope_key(event)
    return key is not None and key in _pending


direct_matcher = on_message(rule=Rule(_is_direct), priority=5, block=True)


@direct_matcher.handle()
async def _handle_direct(event: MessageEvent, matcher: Matcher):
    if not cfg.weather_enabled:
        await matcher.finish()
    text = _plain(event)
    await _reply(matcher, _CITY_RE.match(text).group(1))


conv_matcher = on_message(rule=Rule(_is_conv), priority=5, block=True)


@conv_matcher.handle()
async def _handle_conv(event: MessageEvent, matcher: Matcher):
    if not cfg.weather_enabled:
        await matcher.finish()
    key = _scope_key(event)
    if key:
        _pending.add(key)
    await matcher.finish("你想查询哪个城市的天气呢？直接回复城市名即可。")


pending_matcher = on_message(rule=Rule(_is_pending), priority=10, block=True)


@pending_matcher.handle()
async def _handle_pending(event: MessageEvent, matcher: Matcher):
    key = _scope_key(event)
    _pending.discard(key)
    city = _plain(event)
    if not city:
        await matcher.finish()
    if _PLACES and city not in _PLACES:
        await matcher.finish(f"没找到「{city}」，请发送有效的城市或国家名。")
    await _reply(matcher, city)
