"""游戏场景空间理解：网格叠加截图 + 方向键动作 + 卡住检测。

2D 游戏里模型点不准"门把手"的根因是截图没有坐标参考。
给截图叠加 3x3 网格与行列标签（A1..C3），提示词里教模型用
网格坐标描述目标，tap 支持 grid 字段（如 "B2"）自动换算像素中心。
"""

from __future__ import annotations

import io
import re

GRID_COLS = 3
GRID_ROWS = 3
CELL_NAMES = [[f"{chr(65 + c)}{r + 1}" for c in range(GRID_COLS)] for r in range(GRID_ROWS)]


def overlay_grid(png_bytes: bytes) -> bytes:
    """在截图上画 3x3 网格与格名标签；失败时返回原图。"""
    try:
        from PIL import Image, ImageDraw
        picture = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        width, height = picture.size
        draw = ImageDraw.Draw(picture)
        line_color = (255, 96, 160)
        for c in range(1, GRID_COLS):
            x = width * c // GRID_COLS
            draw.line([(x, 0), (x, height)], fill=line_color, width=2)
        for r in range(1, GRID_ROWS):
            y = height * r // GRID_ROWS
            draw.line([(0, y), (width, y)], fill=line_color, width=2)
        draw.line([(width // 2 - 12, height // 2), (width // 2 + 12, height // 2)], fill=line_color, width=2)
        draw.line([(width // 2, height // 2 - 12), (width // 2, height // 2 + 12)], fill=line_color, width=2)
        for r in range(GRID_ROWS):
            for c in range(GRID_COLS):
                label = CELL_NAMES[r][c]
                x = width * c // GRID_COLS + 8
                y = height * r // GRID_ROWS + 6
                draw.rectangle([x - 2, y - 2, x + 34, y + 18], fill=(0, 0, 0))
                draw.text((x, y), label, fill=(255, 255, 255))
        out = io.BytesIO()
        picture.save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return png_bytes


def grid_to_point(grid: str, width: int, height: int) -> tuple[int, int] | None:
    """把网格名（A1..C3）换算为该格中心像素；无效返回 None。"""
    match = re.fullmatch(r"(?i)\s*([A-Ca-c])\s*([1-3])\s*", str(grid or ""))
    if not match:
        return None
    col = ord(match.group(1).upper()) - ord("A")
    row = int(match.group(2)) - 1
    return (width * (2 * col + 1) // (2 * GRID_COLS),
            height * (2 * row + 1) // (2 * GRID_ROWS))


def is_stuck(history: list, window: int = 4) -> bool:
    """连续 window 步 view_hash 相同 → 视觉上没动，卡住了。"""
    views = [h.get("view_hash") for h in (history or [])[-window:]]
    return len(views) == window and all(views) and len(set(views)) == 1


DIRECTION_KEYS = {"up": 19, "down": 20, "left": 21, "right": 22}
