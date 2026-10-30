"""词云渲染（基于 wordcloud 库 + 中文字体）。

wordcloud 为可选依赖：未安装或找不到中文字体时 `render_wordcloud` 返回 None，
调用方应做好降级（如改为输出纯文本词频）。

注意：本模块文件名为 wordcloud.py，但内部使用绝对导入 `from wordcloud import
WordCloud`，Python 3 默认绝对导入，不会自引用。
"""

from __future__ import annotations

from pathlib import Path

# 中文字体候选（与 morninginfo/render.py 保持一致）
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\Deng.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]

# 亮底可读的配色（白卡背景上使用）
_PALETTE = [
    (96, 128, 240),
    (233, 90, 132),
    (240, 130, 60),
    (34, 160, 100),
    (150, 90, 200),
    (40, 150, 210),
    (210, 70, 80),
    (30, 140, 160),
]


def find_font() -> str | None:
    """返回第一个存在的中文字体路径，找不到返回 None。"""
    for p in _FONT_CANDIDATES:
        if Path(p).exists():
            return p
    return None


def _make_color_func():
    palette = _PALETTE

    def color_func(word, font_size, position, orientation, random_state=None, **kwargs):
        # 依据词本身固定取色，保证同一词多次渲染颜色稳定
        idx = sum(ord(c) for c in str(word)) % len(palette)
        return palette[idx]

    return color_func


def render_wordcloud(
    freq: dict,
    *,
    width: int = 900,
    height: int = 480,
    max_words: int = 200,
):
    """根据词频字典渲染透明背景词云，返回 PIL Image（RGBA）。

    词频为空、缺少 wordcloud 依赖或找不到中文字体时返回 None。
    """
    data = {str(k): int(v) for k, v in (freq or {}).items() if int(v) > 0}
    if not data:
        return None
    try:
        from wordcloud import WordCloud
    except Exception:  # noqa: BLE001
        return None
    font = find_font()
    if not font:
        return None
    try:
        wc = WordCloud(
            font_path=font,
            width=width,
            height=height,
            background_color=None,
            mode="RGBA",
            max_words=max_words,
            color_func=_make_color_func(),
            prefer_horizontal=0.95,
            relative_scaling=0.5,
            margin=4,
        )
        wc.generate_from_frequencies(data)
        return wc.to_image().convert("RGBA")
    except Exception:  # noqa: BLE001
        return None
