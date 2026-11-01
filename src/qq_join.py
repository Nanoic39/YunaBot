"""Group join blacklist helpers shared by risk and join-request handling."""
from __future__ import annotations

import json
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "data" / "qq" / "group_blacklist.json"
_LOCK = threading.Lock()


def _load() -> dict:
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(FILE)


def blacklist(account: str, gid: str, qq: str) -> None:
    with _LOCK:
        data = _load()
        bucket = data.setdefault(str(account), {}).setdefault(str(gid), [])
        if str(qq) not in bucket:
            bucket.append(str(qq))
        _save(data)


def unblacklist(account: str, gid: str, qq: str) -> None:
    with _LOCK:
        data = _load()
        bucket = data.get(str(account), {}).get(str(gid), [])
        if str(qq) in bucket:
            bucket.remove(str(qq))
        _save(data)


def is_blacklisted(account: str, gid: str, qq: str) -> bool:
    bucket = _load().get(str(account), {}).get(str(gid), [])
    return str(qq) in bucket


def all_for(account: str, gid: str) -> list[str]:
    return list(_load().get(str(account), {}).get(str(gid), []))
