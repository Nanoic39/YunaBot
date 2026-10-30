"""管理面板媒体缓存。

表情仓库里的原图动辄几 MB（最大接近 5 MB），直接把它们塞进 100px 的卡片既
浪费带宽，也会让浏览器同时解码几十张大图。这里提供磁盘级 WebP 缩略图缓存：

* 以「源文件绝对路径 + mtime + 文件大小 + 目标宽度」生成内容哈希键；
* 缩略图写入 data/admin/cache/media/thumbs，原子替换，避免读到半截文件；
* 同一张图同一宽度只生成一次，多个请求并发时按 key 加锁；
* 缓存超过预算时按最近访问时间淘汰旧文件；
* 提供 ETag 所需的稳定版本串，让浏览器对原图/缩略图使用强缓存。

缩略图生成失败时调用方可以回退到原图；管理面板不会因为一张坏图而打不开。
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from nonebot.log import logger

try:  # Pillow 是项目依赖，但管理面板允许在缺失时降级为原图
    from PIL import Image, ImageOps
except Exception:  # pragma: no cover - 仅在异常环境触发
    Image = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


@dataclass(frozen=True)
class Thumb:
    path: Path
    etag: str
    width: int
    height: int
    bytes: int


def _safe_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


class MediaCache:
    def __init__(
        self,
        cache_dir: Path,
        *,
        max_bytes: int = 256 * 1024 * 1024,
        default_width: int = 360,
        max_width: int = 1600,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.thumb_dir = self.cache_dir / "thumbs"
        self.thumb_dir.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            try:
                self.cache_dir.chmod(0o700)
            except OSError:
                pass
        self.max_bytes = max(8 * 1024 * 1024, int(max_bytes))
        self.default_width = _safe_int(default_width, 360, 64, max_width)
        self.max_width = _safe_int(max_width, 1600, 128, 4096)
        self._meta_lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._writes = 0
        self._hits = 0
        self._misses = 0
        self._errors = 0
        self._last_cleanup = 0.0

    # ---------- 基础信息 ----------
    @property
    def available(self) -> bool:
        return Image is not None

    @staticmethod
    def media_type(path: Path) -> str:
        return MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")

    @staticmethod
    def _stat_key(path: Path) -> tuple[int, int]:
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size

    def source_etag(self, path: Path) -> str:
        mtime_ns, size = self._stat_key(path)
        raw = f"{path.resolve()}|{mtime_ns}|{size}".encode("utf-8", "surrogatepass")
        return '"' + hashlib.sha256(raw).hexdigest()[:32] + '"'

    def _thumb_key(self, path: Path, width: int) -> tuple[str, int, int]:
        mtime_ns, size = self._stat_key(path)
        raw = f"{path.resolve()}|{mtime_ns}|{size}|{width}|webp-v1".encode(
            "utf-8", "surrogatepass"
        )
        digest = hashlib.sha256(raw).hexdigest()
        return digest, mtime_ns, size

    def _thumb_path(self, key: str) -> Path:
        return self.thumb_dir / key[:2] / f"{key}.webp"

    def _lock_for(self, key: str) -> threading.Lock:
        with self._meta_lock:
            lock = self._locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._locks[key] = lock
                # 锁对象只用于生成期，避免无限增长
                if len(self._locks) > 512:
                    for stale in list(self._locks)[:128]:
                        self._locks.pop(stale, None)
            return lock

    def clamp_width(self, value) -> int:
        return _safe_int(value, self.default_width, 64, self.max_width)

    # ---------- 缩略图 ----------
    def thumbnail(self, source: Path, width: int | None = None) -> Thumb | None:
        """生成/读取缩略图；失败返回 None，由调用方回退到原图。"""
        width = self.clamp_width(width)
        source = Path(source)
        if not self.available or not source.is_file():
            return None
        if source.suffix.lower() not in IMAGE_SUFFIXES:
            return None
        key, _mtime_ns, _size = self._thumb_key(source, width)
        target = self._thumb_path(key)
        if target.is_file() and target.stat().st_size > 0:
            with self._meta_lock:
                self._hits += 1
            self._maybe_cleanup()
            return self._thumb_result(target, key, width)

        lock = self._lock_for(key)
        with lock:
            if target.is_file() and target.stat().st_size > 0:
                with self._meta_lock:
                    self._hits += 1
                return self._thumb_result(target, key, width)
            try:
                result = self._render(source, target, width)
                with self._meta_lock:
                    self._misses += 1
                    self._writes += 1
                self._maybe_cleanup()
                return result
            except Exception as error:  # 坏图/不支持格式不应该拖垮面板
                with self._meta_lock:
                    self._errors += 1
                logger.warning(f"[admin] 缩略图生成失败 {source.name}: {error}")
                return None

    def _render(self, source: Path, target: Path, width: int) -> Thumb:
        assert Image is not None and ImageOps is not None
        image = Image.open(source)
        try:
            # 动图只取第一帧做封面；点开预览时仍然看原图
            if getattr(image, "is_animated", False):
                image.seek(0)
            image.load()
            image = ImageOps.exif_transpose(image)
            image.thumbnail((width, width), Image.Resampling.LANCZOS)
            has_alpha = image.mode in ("RGBA", "LA") or (
                image.mode == "P" and "transparency" in image.info
            )
            if has_alpha:
                image = image.convert("RGBA")
            else:
                image = image.convert("RGB")
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            image.save(
                temporary,
                format="WEBP",
                quality=82,
                method=4,
                optimize=True,
            )
            os.replace(temporary, target)
            return self._thumb_result(target, target.stem, width)
        finally:
            try:
                image.close()
            except Exception:
                pass

    def _thumb_result(self, target: Path, key: str, width: int) -> Thumb:
        stat = target.stat()
        return Thumb(
            path=target,
            etag='"' + key + '"',
            width=width,
            height=width,
            bytes=stat.st_size,
        )

    # ---------- 维护 ----------
    def _maybe_cleanup(self) -> None:
        now = time.time()
        if now - self._last_cleanup < 30:
            return
        self._last_cleanup = now
        self.cleanup()

    def cleanup(self) -> int:
        """按 LRU 淘汰缩略图，返回删除的文件数。"""
        files: list[tuple[float, int, Path]] = []
        total = 0
        try:
            for path in self.thumb_dir.rglob("*.webp"):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                total += stat.st_size
                files.append((stat.st_atime, stat.st_size, path))
        except OSError:
            return 0
        if total <= self.max_bytes:
            return 0
        files.sort(key=lambda item: item[0])
        removed = 0
        for _atime, size, path in files:
            if total <= self.max_bytes:
                break
            try:
                path.unlink()
                total -= size
                removed += 1
            except OSError:
                continue
        # 清理空目录
        try:
            for directory in sorted(
                (p for p in self.thumb_dir.iterdir() if p.is_dir()),
                key=lambda p: p.name,
            ):
                try:
                    directory.rmdir()
                except OSError:
                    pass
        except OSError:
            pass
        if removed:
            logger.info(f"[admin] 媒体缓存清理 {removed} 个文件，当前 {total / 1024**2:.1f} MB")
        return removed

    def invalidate(self, source: Path) -> int:
        """删除某个源文件在各尺寸下的缩略图（源文件仍存在时调用）。"""
        source = Path(source)
        if not source.is_file():
            return 0
        removed = 0
        widths = {160, 360, 720, self.default_width, self.max_width}
        for width in widths:
            try:
                key, _mtime, _size = self._thumb_key(source, self.clamp_width(width))
                target = self._thumb_path(key)
                if target.is_file():
                    target.unlink()
                    removed += 1
            except OSError:
                continue
        return removed

    def stats(self) -> dict:
        total = 0
        files = 0
        try:
            for path in self.thumb_dir.rglob("*.webp"):
                try:
                    total += path.stat().st_size
                    files += 1
                except OSError:
                    continue
        except OSError:
            pass
        with self._meta_lock:
            hits, misses, errors, writes = (
                self._hits,
                self._misses,
                self._errors,
                self._writes,
            )
        return {
            "available": self.available,
            "files": files,
            "bytes": total,
            "max_bytes": self.max_bytes,
            "hits": hits,
            "misses": misses,
            "errors": errors,
            "writes": writes,
            "default_width": self.default_width,
            "max_width": self.max_width,
        }

    def clear(self) -> int:
        removed = 0
        with self._meta_lock:
            for path in list(self.thumb_dir.rglob("*.webp")):
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    continue
            self._hits = self._misses = self._errors = self._writes = 0
            self._last_cleanup = 0.0
        try:
            for directory in sorted(
                (p for p in self.thumb_dir.iterdir() if p.is_dir()),
                key=lambda p: p.name,
            ):
                try:
                    directory.rmdir()
                except OSError:
                    pass
        except OSError:
            pass
        return removed
