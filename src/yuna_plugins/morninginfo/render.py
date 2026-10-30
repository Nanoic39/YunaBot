"""早报图片渲染基础件与整卡组装。

提供字体探测、配色常量、圆角软阴影卡片、渐变/渐隐、横幅绘制等基础能力，
以及 `render_report()` 把各模块绘制结果按顺序拼成一张早报卡片。
本模块不依赖具体业务模块（模块通过参数以鸭子类型传入），避免循环导入。
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

PLUGIN_DIR = Path(__file__).parent
# 本地缓存图片目录（早报成品图、每日一图等）
CACHE_DIR = PLUGIN_DIR / "cache"

# ---------- 字体 ----------
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


# ---------- 尺寸 / 配色 ----------
_W = 1040
_PAD = 22
_GAP = 20
# 模块之间的垂直间距
_VGAP = 16
_RADIUS = 20
# 单模块独立图层的左右预留边距：为卡片柔和阴影留出空间，避免被裁切
_LAYER_MARGIN = 26
# 单模块图层最大高度（内容超长时才触及，正常不会）
_LAYER_MAX_H = 4200
_BANNER_H = 176

_BG = (247, 248, 251, 255)
# 卡片半透明：二次元背景整卡铺底后可透出，同时保持文字可读
_CARD_BG = (255, 255, 255, 205)
_LINE = (236, 238, 244, 255)
_TITLE_COLOR = (43, 45, 66, 255)
_TEXT_COLOR = (86, 89, 108, 255)
_SUB_COLOR = (152, 156, 172, 255)
_ACCENT = (96, 128, 240, 255)
_ACCENT_SOFT = (240, 245, 255, 208)

# 清新横幅：淡蓝 → 淡紫
_BANNER_TOP = (230, 239, 255)
_BANNER_BOTTOM = (244, 236, 255)
_BANNER_TITLE = (43, 45, 66, 255)
_BANNER_SUB = (110, 118, 150, 235)
_RANK_COLORS = [(255, 176, 92, 255), (152, 165, 198, 255), (214, 160, 130, 255)]

# 日系 ACG 运势卡配色（奶白 + 樱花粉 + 浅紫 + 天空蓝）
_F_BG = (255, 249, 252, 214)      # #FFF9FC 奶白（半透明，可透出背景）
_F_PINK = (244, 203, 225, 255)    # #F4CBE1 樱花粉
_F_PURPLE = (220, 207, 244, 255)  # #DCCFF4 浅紫
_F_BLUE = (217, 234, 247, 255)    # #D9EAF7 天空蓝
_F_TEXT = (99, 87, 127, 255)      # #63577F 正文
_F_SUBTEXT = (162, 152, 190, 255)
_F_LINE = (244, 228, 240, 255)
_F_PANEL = (255, 255, 255, 165)
# 五维指数进度条配色（柔和但有对比度）
_F_BARS = [
    (236, 156, 194, 255),  # 樱粉
    (180, 158, 228, 255),  # 浅紫
    (128, 184, 226, 255),  # 天空蓝
    (128, 200, 168, 255),  # 薄荷绿
    (236, 190, 120, 255),  # 暖金
]
_F_CHARACTER = PLUGIN_DIR / "character" / "Yuna立绘AI.png"
_yuna_src: "Image.Image | None" = None
_yuna_loaded = False


# ---------- 通用工具 ----------
def _center(draw, cx, y, text, font, fill):
    draw.text((cx - font.getlength(text) / 2, y), text, font=font, fill=fill)


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


def _ellipsize(text: str, font, max_width: int) -> str:
    if font.getlength(text) <= max_width:
        return text
    out = ""
    for ch in text:
        if font.getlength(out + ch + "…") > max_width:
            break
        out += ch
    return out + "…"


def _fmt_chars(n: int) -> str:
    if n >= 10000:
        return f"{n / 10000:.1f} 万字"
    if n >= 1000:
        return f"{n / 1000:.1f} 千字"
    return f"{n} 字"


def _gradient(size: tuple[int, int], top: tuple, bottom: tuple) -> Image.Image:
    w, h = size
    img = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        rgb = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3))
        d.line([(0, y), (w, y)], fill=rgb + (255,))
    return img


def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """等比缩放并居中裁剪（从顶部取，突出人物上半身）到 w×h。"""
    iw, ih = img.size
    if iw <= 0 or ih <= 0:
        return Image.new("RGBA", (w, h), (255, 255, 255, 255))
    scale = max(w / iw, h / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    resized = img.resize((nw, nh), Image.LANCZOS)
    left = (nw - w) // 2
    return resized.crop((left, 0, left + w, h))


def _fit_contain(src: Image.Image, w: int, h: int) -> Image.Image:
    """把图片完整缩放居中放入 w×h 的框内，四周用同图模糊铺底，
    避免竖排图片被裁剪、显示不全。"""
    src = src.convert("RGBA")
    box = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    box.alpha_composite(_cover(src, w, h).filter(ImageFilter.GaussianBlur(20)), (0, 0))
    iw, ih = src.size
    if iw <= 0 or ih <= 0:
        return box
    scale = min(w / iw, h / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    fitted = src.resize((nw, nh), Image.LANCZOS)
    box.alpha_composite(fitted, ((w - nw) // 2, (h - nh) // 2))
    return box


def _soft_card(img, box, radius, fill=_CARD_BG, shadow_alpha=28, blur=10, offset=6):
    """在 img 上绘制带柔和阴影的圆角卡片。"""
    x0, y0, x1, y1 = box
    pad = blur * 2
    lx0, ly0 = max(0, x0 - pad), max(0, y0 - pad)
    lx1, ly1 = min(img.width, x1 + pad), min(img.height, y1 + offset + pad)
    if lx1 <= lx0 or ly1 <= ly0:
        return
    sh = Image.new("RGBA", (lx1 - lx0, ly1 - ly0), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle(
        [x0 - lx0, y0 - ly0 + offset, x1 - lx0, y1 - ly0 + offset],
        radius=radius,
        fill=(120, 130, 165, shadow_alpha),
    )
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(blur)), (lx0, ly0))
    ImageDraw.Draw(img).rounded_rectangle(box, radius=radius, fill=fill)


def _hbar(draw, x, y, w, h, ratio, color):
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=(238, 240, 246, 255))
    fw = max(h, w * ratio)
    draw.rounded_rectangle([x, y, x + fw, y + h], radius=h // 2, fill=color)


# ---------- 今日运势卡（日系 ACG 三栏状态面板） ----------
def _load_yuna() -> "Image.Image | None":
    """加载 Yuna 立绘（透明背景 PNG），带一次性缓存；失败返回 None。"""
    global _yuna_src, _yuna_loaded
    if not _yuna_loaded:
        _yuna_loaded = True
        try:
            _yuna_src = Image.open(_F_CHARACTER).convert("RGBA")
        except Exception:
            _yuna_src = None
    return _yuna_src


def _panel(img: Image.Image, box, fill=_F_PANEL, outline=_F_LINE, radius=14):
    """细描边圆角面板：以 alpha 合成方式绘制，避免半透明填充覆盖底图。"""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return
    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(lay).rounded_rectangle(
        [0, 0, w - 1, h - 1], radius=radius, fill=fill, outline=outline, width=1
    )
    img.alpha_composite(lay, (x0, y0))


def _star_text(stars: int) -> str:
    return "★" * stars + "☆" * (5 - stars)


def _decorate(img: Image.Image, box):
    """四角点缀淡淡的樱花花瓣与星光，轻盈不压文字。"""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    for fx, fy, r in (
        (0.905, 0.09, 9), (0.83, 0.22, 6), (0.955, 0.38, 5),
        (0.07, 0.92, 7), (0.16, 0.80, 5), (0.70, 0.955, 6),
    ):
        cx, cy = int(w * fx), int(h * fy)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(244, 203, 225, 95))
    for fx, fy, s in ((0.66, 0.055, 5), (0.30, 0.20, 4), (0.5, 0.97, 4)):
        cx, cy = int(w * fx), int(h * fy)
        d.line([cx - s, cy, cx + s, cy], fill=(220, 207, 244, 160), width=1)
        d.line([cx, cy - s, cx, cy + s], fill=(220, 207, 244, 160), width=1)
    img.alpha_composite(lay, (x0, y0))


def _fortune_list(draw, x, y, w, title, items, color):
    """宜 / 忌 单列列表：标题 + 圆点条目。"""
    draw.text((x, y), title, font=_load_font(14, bold=True), fill=color)
    f = _load_font(12)
    for i, it in enumerate(items[:4]):
        ly = y + 24 + i * 22
        draw.ellipse([x + 4, ly + 6, x + 10, ly + 12], fill=color)
        draw.text((x + 16, ly), _ellipsize(it, f, w - 16), font=f, fill=_F_TEXT)


def _draw_fortune_left(img, draw, x, y, w, h, data, accent):
    # 左栏：大尺寸立绘 + 底部叠加寄语浮层
    lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lay)
    ld.ellipse([w - 118, 8, w + 38, 164], fill=accent[:3] + (32,))
    ld.ellipse([-26, h - 176, 136, h - 14], fill=(220, 207, 244, 54))

    # 立绘：透明背景 PNG 直接叠加，尽量放大，底部对齐
    yuna = _load_yuna()
    if yuna is not None:
        iw, ih = yuna.size
        if iw > 0 and ih > 0:
            scale = min((w * 0.98) / iw, (h * 0.97) / ih)
            nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
            yuna_img = yuna.resize((nw, nh), Image.LANCZOS)
            lay.alpha_composite(yuna_img, ((w - nw) // 2, h - nh))

    # 寄语叠在立绘底部，而不是额外占一块高度
    mf = _load_font(14)
    lines = _wrap(data.get("message", ""), mf, w - 34)[:3]
    overlay_h = 40 + len(lines) * 22
    oy = max(10, h - overlay_h - 12)
    ld.rounded_rectangle(
        [10, oy, w - 10, h - 10],
        radius=18,
        fill=(255, 255, 255, 220),
        outline=(248, 224, 240, 255),
        width=1,
    )
    ld.text((24, oy + 11), "今日寄语", font=_load_font(13, bold=True), fill=accent)
    for i, ln in enumerate(lines):
        ld.text((24, oy + 34 + i * 22), ln, font=mf, fill=_F_TEXT)
    img.alpha_composite(lay, (x, y))


def _draw_fortune_mid(img, draw, x, y, w, h, data, accent):
    panel_h = 116
    _panel(img, [x, y, x + w, y + panel_h], fill=_F_PANEL,
           outline=(248, 224, 240, 255), radius=16)
    draw.text((x + 16, y + 12), "综合指数", font=_load_font(13, bold=True), fill=_F_SUBTEXT)
    # 综合评分
    score_txt = str(data["score"])
    f_big = _load_font(44, bold=True)
    draw.text((x + 16, y + 24), score_txt, font=f_big, fill=accent)
    draw.text((x + 18 + f_big.getlength(score_txt), y + 54), "/100",
              font=_load_font(14), fill=_F_SUBTEXT)
    # 等级标签（右上）
    lv = data["level"]
    lf = _load_font(15, bold=True)
    pill_w = int(lf.getlength(lv)) + 22
    px0 = x + w - 14 - pill_w
    _panel(img, [px0, y + 14, x + w - 14, y + 40], fill=accent, outline=accent, radius=13)
    draw.text((px0 + 11, y + 19), lv, font=lf, fill=(255, 255, 255, 255))
    # 星级 + 总结
    draw.text((x + 16, y + 72), _star_text(data["stars"]), font=_load_font(17), fill=accent)
    draw.text((x + 16, y + 94), _ellipsize(data["summary"], _load_font(12), w - 32),
              font=_load_font(12), fill=_F_TEXT)

    # 五维指数
    indices = data["indices"][:5]
    top = y + panel_h + 10
    row_h = max(1, (h - panel_h - 10) // max(1, len(indices)))
    for i, it in enumerate(indices):
        ry = top + i * row_h
        bar = _F_BARS[i % len(_F_BARS)]
        draw.text((x + 2, ry), it["name"], font=_load_font(15, bold=True), fill=_F_TEXT)
        sf = _load_font(15, bold=True)
        sc = str(it["score"])
        draw.text((x + w - 2 - sf.getlength(sc), ry), sc, font=sf, fill=bar)
        _hbar(draw, x + 2, ry + 21, w - 4, 6, it["score"] / 100, bar)
        draw.text((x + 2, ry + 31), _ellipsize(it["tip"], _load_font(11), w - 4),
                  font=_load_font(11), fill=_F_SUBTEXT)


def _draw_fortune_right(img, draw, x, y, w, h, data, accent):
    # 今日宜忌
    gy_h = 192
    _panel(img, [x, y, x + w, y + gy_h], fill=_F_PANEL,
           outline=(248, 224, 240, 255), radius=16)
    draw.text((x + 12, y + 10), "今日宜忌", font=_load_font(15, bold=True), fill=_F_TEXT)
    pad, col_gap = 12, 8
    col_w = (w - pad * 2 - col_gap) // 2
    _fortune_list(draw, x + pad, y + 36, col_w, "宜", data["good"], (232, 146, 188, 255))
    _fortune_list(draw, x + pad + col_w + col_gap, y + 36, col_w, "忌", data["bad"], (168, 158, 210, 255))

    # 幸运信息
    ly = y + gy_h + 8
    lh = h - gy_h - 8
    _panel(img, [x, ly, x + w, ly + lh], fill=_F_PANEL,
           outline=(248, 224, 240, 255), radius=16)
    draw.text((x + 12, ly + 10), "幸运信息", font=_load_font(15, bold=True), fill=_F_TEXT)
    ff = _load_font(14)
    step = max(1, (lh - 44) // 3)
    ry = ly + 34
    # 幸运色（色点）
    draw.text((x + 12, ry), "幸运色", font=ff, fill=_F_SUBTEXT)
    draw.ellipse([x + 76, ry + 2, x + 92, ry + 18],
                 fill=tuple(data.get("lucky_color_rgb", accent)))
    draw.text((x + 98, ry), data["lucky_color"], font=ff, fill=_F_TEXT)
    # 幸运数字（徽章）
    ry += step
    draw.text((x + 12, ry), "幸运数字", font=ff, fill=_F_SUBTEXT)
    num = str(data["lucky_number"])
    nf = _load_font(13, bold=True)
    bw = int(nf.getlength(num)) + 16
    _panel(img, [x + 76, ry - 3, x + 76 + bw, ry + 21], fill=accent, outline=accent, radius=12)
    draw.text((x + 84, ry + 1), num, font=nf, fill=(255, 255, 255, 255))
    # 幸运物
    ry += step
    draw.text((x + 12, ry), "幸运物", font=ff, fill=_F_SUBTEXT)
    draw.text((x + 76, ry), data["lucky_item"], font=ff, fill=_F_TEXT)


def draw_fortune_card(img: Image.Image, box, data: dict) -> None:
    """日系简约 ACG 风格的横向三栏运势卡（左 35% 角色 / 中 38% 指数 / 右 27% 宜忌与幸运）。"""
    x0, y0, x1, y1 = box
    cw, ch = x1 - x0, y1 - y0
    p = 18
    accent = tuple(data.get("accent", _F_PINK))

    _soft_card(img, [x0, y0, x1, y1], 24, fill=_F_BG, shadow_alpha=30, blur=12, offset=7)
    _panel(img, [x0, y0, x1, y1], fill=(0, 0, 0, 0), outline=(247, 228, 240, 255), radius=24)
    _decorate(img, [x0, y0, x1, y1])
    draw = ImageDraw.Draw(img)

    # 顶部标题区
    draw.text((x0 + p, y0 + 14), data.get("title", "今日运势"),
              font=_load_font(23, bold=True), fill=_F_TEXT)
    draw.text((x0 + p, y0 + 45), "Today's Fortune", font=_load_font(12),
              fill=(196, 182, 214, 255))
    df = _load_font(15)
    draw.text((x1 - p - df.getlength(data["date"]), y0 + 24), data["date"],
              font=df, fill=_F_SUBTEXT)
    draw.line([(x0 + p, y0 + 62), (x1 - p, y0 + 62)], fill=_F_LINE, width=1)

    # 三栏
    cy0, cy1 = y0 + 76, y1 - 34
    col_h = cy1 - cy0
    gap = 14
    avail = (cw - 2 * p) - gap * 2
    w_left = int(avail * 0.40)
    w_mid = int(avail * 0.33)
    w_right = avail - w_left - w_mid
    lx = x0 + p
    mx = lx + w_left + gap
    rx = mx + w_mid + gap

    _draw_fortune_left(img, draw, lx, cy0, w_left, col_h, data, accent)
    _draw_fortune_mid(img, draw, mx, cy0, w_mid, col_h, data, accent)
    _draw_fortune_right(img, draw, rx, cy0, w_right, col_h, data, accent)

    # 页脚
    draw.line([(x0 + p, y1 - 30), (x1 - p, y1 - 30)], fill=_F_LINE, width=1)
    ff = _load_font(12)
    draw.text((x0 + p, y1 - 24), "Yuna · 每日运势", font=ff, fill=_F_SUBTEXT)
    sig = "愿你今天也被温柔以待"
    draw.text((x1 - p - ff.getlength(sig), y1 - 24), sig, font=ff, fill=(214, 178, 205, 255))


# ---------- 横幅 ----------
def _paste_banner_gradient(img: Image.Image, w: int):
    img.paste(_gradient((w, _BANNER_H), _BANNER_TOP, _BANNER_BOTTOM), (0, 0))


def _paste_full_background(img: Image.Image, bg: Image.Image, w: int, h: int):
    """把二次元图等比铺满整张卡片作为底层背景（从顶部取，突出人物上半身）。"""
    img.alpha_composite(_cover(bg.convert("RGBA"), w, h), (0, 0))


def draw_banner(img: Image.Image, w: int, title: str, subtitle: str, on_image: bool = False):
    if not on_image:
        _paste_banner_gradient(img, w)
        overlay = Image.new("RGBA", (w, _BANNER_H + 30), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        for dx, dy, dr, color in (
            (w - 62, 40, 68, (150, 178, 255, 60)),
            (w - 148, 120, 42, (198, 176, 255, 48)),
            (w - 26, 128, 26, (150, 178, 255, 70)),
        ):
            od.ellipse([dx - dr, dy - dr, dx + dr, dy + dr], fill=color)
        img.alpha_composite(overlay, (0, 0))

    draw = ImageDraw.Draw(img)
    # 直接叠在二次元背景图上时加白色描边，保证标题/副标题清晰可读
    if on_image:
        draw.text((_PAD, 30), title, font=_load_font(44, bold=True), fill=_BANNER_TITLE,
                  stroke_width=3, stroke_fill=(255, 255, 255, 205))
        draw.rounded_rectangle([_PAD + 2, 94, _PAD + 40, 100], radius=3, fill=_ACCENT)
        draw.text((_PAD, 110), subtitle, font=_load_font(22), fill=_BANNER_SUB,
                  stroke_width=2, stroke_fill=(255, 255, 255, 205))
    else:
        draw.text((_PAD, 30), title, font=_load_font(44, bold=True), fill=_BANNER_TITLE)
        draw.rounded_rectangle([_PAD + 2, 94, _PAD + 40, 100], radius=3, fill=_ACCENT)
        draw.text((_PAD, 110), subtitle, font=_load_font(22), fill=_BANNER_SUB)


# ---------- 整卡组装 ----------
def _paste_layer(img: Image.Image, layer: Image.Image, x: int, y: int) -> None:
    """把透明图层合成到画布指定位置，自动裁去超出画布的部分。"""
    lx, ly = max(0, x), max(0, y)
    ox, oy = lx - x, ly - y
    w = min(layer.width - ox, img.width - lx)
    h = min(layer.height - oy, img.height - ly)
    if w <= 0 or h <= 0:
        return
    img.alpha_composite(layer.crop((ox, oy, ox + w, oy + h)), (lx, ly))


def _render_layer(mod, data, ctx, col_w: int) -> Image.Image | None:
    """把单个模块渲染到独立透明图层上，返回裁切到实际内容高度的图层。

    模块的 draw() 通过 ctx.col_x0 / ctx.col_w 感知所在列的位置与宽度，
    这里把列左边界设为图层内边距 _LAYER_MARGIN，以容纳卡片阴影不被裁切。
    """
    m = _LAYER_MARGIN
    canvas = Image.new("RGBA", (col_w + m * 2, _LAYER_MAX_H), (0, 0, 0, 0))
    old = (ctx.col_x0, ctx.col_w)
    ctx.col_x0, ctx.col_w = m, col_w
    try:
        bottom = mod.draw(canvas, 0, data, ctx)
    finally:
        ctx.col_x0, ctx.col_w = old
    try:
        bottom = int(bottom)
    except Exception:  # noqa: BLE001
        return None
    if bottom <= 0:
        return None
    return canvas.crop((0, 0, col_w + m * 2, min(_LAYER_MAX_H, bottom)))


def render_report(
    ctx,
    collected: list,
    footer: str = "",
    banner_bg=None,
    title: str = "早安",
) -> bytes:
    """把 [(module, data), ...] 渲染成一张双列瀑布流早报卡片。

    各模块高度不一，先逐个渲染为独立图层并量出真实高度，再按「放入当前较矮的一列」
    的瀑布流规则摆放；full_width 模块横跨整行。画布高度按内容动态计算，
    从根本上避免内容被截断，也不会出现过长的留白。
    """
    w = _W
    content_w = w - _PAD * 2
    col_w = (content_w - _GAP) // 2
    top = _BANNER_H + 22

    # 1) 逐模块渲染为独立图层（同时得到真实高度）；单模块失败不影响整卡
    entries: list[list] = []  # [mod, data, full, layer]
    for mod, data in collected:
        full = bool(getattr(mod, "full_width", False))
        cw = content_w if full else col_w
        try:
            layer = _render_layer(mod, data, ctx, cw)
        except Exception:  # noqa: BLE001
            layer = None
        if layer is not None:
            entries.append([mod, data, full, layer])

    # 2) 首行等高对齐：首行两栏中标记 align_top 的较矮卡片，重绘到与另一栏等高。
    #    上限 _ALIGN_MAX 防止对方过高时把卡片拉得离谱（统计卡 3 人时约 230px）。
    _ALIGN_MAX = 240
    row1 = [e for e in entries if not e[2]][:2]
    if len(row1) == 2:
        for i in (0, 1):
            mod_i, data_i, _, layer_i = row1[i]
            other = row1[1 - i][3]
            if not getattr(mod_i, "align_top", False):
                continue
            if layer_i.height < other.height <= _ALIGN_MAX:
                ctx.min_height = other.height
                try:
                    new_layer = _render_layer(mod_i, data_i, ctx, col_w)
                except Exception:  # noqa: BLE001
                    new_layer = None
                finally:
                    ctx.min_height = 0
                if new_layer is not None:
                    row1[i][3] = new_layer

    # 2.5) 首行无法等高对齐时（对方超过 _ALIGN_MAX，如统计卡 >3 人），
    #      天气所在列会留下一段空档；把「每日寄语(一言)」提前到首行下方补足高度。
    if len(row1) == 2 and any(
        getattr(row1[i][0], "align_top", False) and row1[i][3].height < row1[1 - i][3].height
        for i in (0, 1)
    ):
        qi = next((i for i, e in enumerate(entries) if getattr(e[0], "name", "") == "quote"), None)
        if qi is not None:
            pos = entries.index(row1[1])
            if qi > pos:
                entries.insert(pos + 1, entries.pop(qi))

    layers: list[tuple[bool, Image.Image]] = [(e[2], e[3]) for e in entries]

    # 3) 瀑布流排版：full 模块横跨整行，其余放入当前较矮的一列
    y_a = y_b = top
    placements: list[tuple[int, int, Image.Image]] = []
    for full, layer in layers:
        h = layer.height
        if full:
            y = max(y_a, y_b)
            placements.append((_PAD - _LAYER_MARGIN, y, layer))
            y_a = y_b = y + h + _VGAP
        elif y_a <= y_b:
            placements.append((_PAD - _LAYER_MARGIN, y_a, layer))
            y_a += h + _VGAP
        else:
            placements.append((_PAD + col_w + _GAP - _LAYER_MARGIN, y_b, layer))
            y_b += h + _VGAP
    body_bottom = max(y_a, y_b)

    # 3) 页脚（自定义文本）
    foot_lines: list[str] = []
    if footer:
        foot_lines = _wrap(footer, _load_font(18), content_w)
    foot_block = len(foot_lines) * 26 + 6 if foot_lines else 0

    total_h = body_bottom + foot_block + _PAD
    # 画布额外放宽 _LAYER_MARGIN 以容纳末行卡片阴影，最后统一裁回 total_h
    img = Image.new("RGBA", (w, total_h + _LAYER_MARGIN), _BG)
    # 二次元图铺满整张卡片作为底层背景（不再局限于顶部区域），卡片半透明叠在其上
    if banner_bg is not None:
        _paste_full_background(img, banner_bg, w, total_h + _LAYER_MARGIN)
    draw_banner(img, w, title, f"{ctx.date_str} · {ctx.weekday}", on_image=banner_bg is not None)

    for x, y, layer in placements:
        _paste_layer(img, layer, x, y)

    if foot_lines:
        draw = ImageDraw.Draw(img)
        f_foot = _load_font(18)
        fy = body_bottom + 6
        for ln in foot_lines:
            draw.text((_PAD + 2, fy), ln, font=f_foot, fill=_SUB_COLOR)
            fy += 26

    img = img.crop((0, 0, w, total_h))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
