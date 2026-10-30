"""管理面板数据备份：只打包配置与记忆，不打包表情原图和缓存。

设计要点：
* 备份包含 `.env` / `.env.<env>` 与聊天数据（记忆、画像、用户、成长、白名单等），
  但显式排除表情图片、缩略图缓存、日志和密码文件；
* 归档内附带 `manifest.json`，恢复前校验格式与路径；
* 恢复使用安全解包（拒绝绝对路径、`..`、符号链接），先创建 pre-restore 备份，
  再把文件覆盖回项目，避免恢复失败后无法回退；
* 所有操作加进程内锁，避免两个备份同时写同一个文件。
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from nonebot.log import logger

BACKUP_PREFIX = "data-"
BACKUP_RE = re.compile(r"^data-\d{8}-\d{6}-(manual|auto|pre-restore)\.tar\.gz$")
MANIFEST = "manifest.json"
CHAT_NAME = "src/yuna_plugins/chat"
CHAT_FILES = {
    "bots.json",
    "character.txt",
    "emote_learned.json",
    "eval_samples.json",
    "favor_ledger.json",
    "growth_persona.json",
    "human_state.json",
    "profiles.json",
    "qzone_state.json",
    "relations.json",
    "usage.json",
    "users.json",
    "whitelist_group.json",
    "whitelist_user.json",
}
CHAT_DIRS = {"memory", "personas"}
SKIP_NAMES = {"__pycache__", ".pytest_cache", ".git"}


@dataclass(frozen=True)
class BackupInfo:
    name: str
    size: int
    mtime: int
    kind: str

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "size": self.size,
            "mtime": self.mtime,
            "kind": self.kind,
            "created": datetime.fromtimestamp(self.mtime).strftime("%Y-%m-%d %H:%M:%S"),
        }


class BackupError(Exception):
    pass


class BackupManager:
    def __init__(self, root: Path, state_dir: Path, chat_dir: Path, max_files: int = 20):
        self.root = Path(root)
        self.state_dir = Path(state_dir)
        self.chat_dir = Path(chat_dir)
        self.backups_dir = self.state_dir / "backups"
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            try:
                self.backups_dir.chmod(0o700)
            except OSError:
                pass
        self.max_files = max(3, int(max_files))
        self._lock = threading.RLock()

    # ---------- 路径与列表 ----------
    def _path(self, name: str) -> Path:
        if not BACKUP_RE.fullmatch(name or ""):
            raise BackupError("备份文件名无效")
        path = (self.backups_dir / name).resolve()
        if self.backups_dir.resolve() not in path.parents:
            raise BackupError("备份路径越界")
        if not path.is_file():
            raise BackupError("备份不存在")
        return path

    def resolve(self, name: str) -> Path:
        return self._path(name)

    def list(self) -> dict:
        items: list[BackupInfo] = []
        for path in self.backups_dir.glob("*.tar.gz"):
            if not BACKUP_RE.fullmatch(path.name):
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            match = BACKUP_RE.fullmatch(path.name)
            kind = match.group(1) if match else "manual"
            items.append(BackupInfo(path.name, stat.st_size, int(stat.st_mtime), kind))
        items.sort(key=lambda item: item.mtime, reverse=True)
        return {
            "dir": str(self.backups_dir),
            "items": [item.as_dict() for item in items],
            "total_bytes": sum(item.size for item in items),
            "max_files": self.max_files,
        }

    def _prune(self) -> None:
        items = self.list()["items"]
        # pre-restore 永远保留最新一份；其余手动/自动备份按时间保留
        keep = {item["name"] for item in items[: self.max_files]}
        pres = [item["name"] for item in items if item["kind"] == "pre-restore"]
        if pres:
            keep.add(pres[0])
        for item in items:
            if item["name"] in keep:
                continue
            try:
                (self.backups_dir / item["name"]).unlink()
            except OSError:
                continue

    # ---------- 创建 ----------
    def _iter_files(self, directory: Path, exclude_dirs: set[str]) -> Iterable[Path]:
        if not directory.is_dir():
            return
        for current, dirs, files in os.walk(directory):
            dirs[:] = [name for name in dirs if name not in exclude_dirs and name not in SKIP_NAMES]
            for filename in files:
                if filename in SKIP_NAMES:
                    continue
                path = Path(current) / filename
                if path.is_file() and not path.is_symlink():
                    yield path

    def _add_environment(self, archive: tarfile.TarFile, environment: str) -> list[str]:
        names = []
        candidates = [self.root / ".env", self.root / f".env.{environment}"]
        for path in candidates:
            if path.is_file():
                archive.add(path, arcname=path.name, recursive=False)
                names.append(path.name)
        return names

    def _add_data(self, archive: tarfile.TarFile) -> list[str]:
        data = self.root / "data"
        if not data.is_dir():
            return []
        skipped = {"cache", "backups"}
        names = []
        for path in self._iter_files(data, skipped):
            rel = path.relative_to(self.root).as_posix()
            if rel == "data/admin/password.txt":
                continue
            archive.add(path, arcname=rel, recursive=False)
            names.append(rel)
        return names

    def _add_chat(self, archive: tarfile.TarFile) -> list[str]:
        names = []
        for filename in sorted(CHAT_FILES):
            path = self.chat_dir / filename
            if path.is_file():
                rel = path.relative_to(self.root).as_posix()
                archive.add(path, arcname=rel, recursive=False)
                names.append(rel)
        for dirname in sorted(CHAT_DIRS):
            directory = self.chat_dir / dirname
            if not directory.is_dir():
                continue
            for path in self._iter_files(directory, set()):
                rel = path.relative_to(self.root).as_posix()
                archive.add(path, arcname=rel, recursive=False)
                names.append(rel)
        return names

    def create(self, environment: str, kind: str = "manual") -> dict:
        kind = kind if kind in {"manual", "auto", "pre-restore"} else "manual"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        name = f"{BACKUP_PREFIX}{stamp}-{kind}.tar.gz"
        target = self.backups_dir / name
        if target.exists():
            raise BackupError("同一秒内已存在同名备份，请稍后再试")
        temporary = target.with_name(target.name + ".tmp")
        with self._lock:
            try:
                with tarfile.open(temporary, "w:gz", compresslevel=6) as archive:
                    files = []
                    files.extend(self._add_environment(archive, environment))
                    files.extend(self._add_data(archive))
                    files.extend(self._add_chat(archive))
                    manifest = {
                        "format": "yuna-admin-backup",
                        "version": 1,
                        "created": int(time.time()),
                        "environment": environment,
                        "files": files,
                    }
                    payload = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
                    info = tarfile.TarInfo(MANIFEST)
                    info.size = len(payload)
                    info.mtime = int(time.time())
                    archive.addfile(info, io.BytesIO(payload))
                os.replace(temporary, target)
            except Exception as error:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
                raise BackupError(f"创建备份失败：{error}") from error
            self._prune()
            stat = target.stat()
            logger.info(f"[admin] 已创建数据备份 {name}（{stat.st_size / 1024:.1f} KB，{len(files)} 个文件）")
            return {
                "ok": True,
                "name": name,
                "size": stat.st_size,
                "files": len(files),
                "kind": kind,
            }

    # ---------- 恢复 ----------
    @staticmethod
    def _safe_member_path(member: tarfile.TarInfo, destination: Path) -> Path:
        name = member.name.replace("\\", "/")
        if not name or name.startswith("/") or name.startswith("../") or "/../" in name:
            raise BackupError(f"归档包含不安全路径：{member.name}")
        target = (destination / name).resolve()
        base = destination.resolve()
        if base not in target.parents and target != base:
            raise BackupError(f"归档路径越界：{member.name}")
        if member.issym() or member.islnk() or member.isdev():
            raise BackupError(f"归档包含不支持的链接/设备：{member.name}")
        if not (member.isfile() or member.isdir()):
            raise BackupError(f"归档包含不支持的文件类型：{member.name}")
        return target

    def restore(self, name: str) -> dict:
        path = self._path(name)
        with self._lock:
            with tarfile.open(path, "r:gz") as archive:
                try:
                    manifest = json.load(archive.extractfile(MANIFEST))  # type: ignore[arg-type]
                except Exception as error:
                    raise BackupError("备份缺少有效的 manifest.json") from error
                if not isinstance(manifest, dict) or manifest.get("format") != "yuna-admin-backup":
                    raise BackupError("不是 Yuna 管理面板的数据备份")
                if int(manifest.get("version", 0)) != 1:
                    raise BackupError("备份版本不受支持")

                temp_root = Path(tempfile.mkdtemp(prefix="yuna-restore-", dir=self.state_dir))
                copied = 0
                try:
                    for member in archive.getmembers():
                        if member.name == MANIFEST:
                            continue
                        target = self._safe_member_path(member, temp_root)
                        if member.isdir():
                            target.mkdir(parents=True, exist_ok=True)
                            continue
                        target.parent.mkdir(parents=True, exist_ok=True)
                        source = archive.extractfile(member)
                        if source is None:
                            continue
                        with target.open("wb") as handle:
                            shutil.copyfileobj(source, handle)
                        try:
                            os.utime(target, (member.mtime, member.mtime))
                        except OSError:
                            pass
                except Exception:
                    shutil.rmtree(temp_root, ignore_errors=True)
                    raise

                # 恢复前先留一份当前状态，避免误恢复无法回头
                try:
                    self.create(self._environment_from_manifest(manifest), "pre-restore")
                except Exception as error:
                    logger.warning(f"[admin] 恢复前备份失败（继续执行恢复）：{error}")

                for file_path in temp_root.rglob("*"):
                    if file_path.is_dir():
                        continue
                    rel = file_path.relative_to(temp_root).as_posix()
                    target = (self.root / rel).resolve()
                    if self.root.resolve() not in target.parents:
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file_path, target)
                    if rel.startswith('.env') or rel.startswith('data/admin/'):
                        try:
                            target.chmod(0o600)
                        except OSError:
                            pass
                    copied += 1
                shutil.rmtree(temp_root, ignore_errors=True)
            logger.info(f"[admin] 已从备份恢复 {name}：{copied} 个文件")
            return {
                "ok": True,
                "name": name,
                "files": copied,
                "environment": manifest.get("environment"),
                "restart_required": True,
            }

    @staticmethod
    def _environment_from_manifest(manifest: dict) -> str:
        value = str(manifest.get("environment") or "dev")
        return value if re.fullmatch(r"[\w-]+", value) else "dev"

    def delete(self, name: str) -> None:
        path = self._path(name)
        with self._lock:
            path.unlink()

    def prune(self) -> dict:
        with self._lock:
            self._prune()
            return self.list()
