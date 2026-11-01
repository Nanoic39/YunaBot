"""Risk appeal records kept separate from normal private-chat routing."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "qq" / "appeals.sqlite3"


class AppealStore:
    def __init__(self, path: Path, clock=time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        with self.db() as d:
            d.executescript(
                """CREATE TABLE IF NOT EXISTS appeals(
                    id INTEGER PRIMARY KEY, risk_id INTEGER, account TEXT, qq TEXT, gid TEXT,
                    action TEXT, text TEXT, status TEXT DEFAULT 'pending', created REAL,
                    notice_mid TEXT DEFAULT '', owner_note TEXT DEFAULT '')"""
            )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @contextmanager
    def db(self):
        d = sqlite3.connect(self.path)
        d.row_factory = sqlite3.Row
        try:
            with d:
                yield d
        finally:
            d.close()

    def create(self, risk_id: int, account: str, qq: str, gid: str, action: str) -> int:
        with self.db() as d:
            return int(d.execute(
                "INSERT INTO appeals(risk_id,account,qq,gid,action,created) VALUES(?,?,?,?,?,?)",
                (int(risk_id), str(account), str(qq), str(gid or ""), str(action), self.clock()),
            ).lastrowid)

    def notice(self, appeal_id: int, mid) -> None:
        if mid in (None, ""):
            return
        with self.db() as d:
            d.execute("UPDATE appeals SET notice_mid=? WHERE id=?", (str(mid), int(appeal_id)))

    def by_notice(self, qq: str, mid) -> dict | None:
        with self.db() as d:
            row = d.execute(
                "SELECT * FROM appeals WHERE qq=? AND notice_mid=? AND status='pending' ORDER BY id DESC LIMIT 1",
                (str(qq), str(mid)),
            ).fetchone()
            return dict(row) if row else None

    def get(self, appeal_id: int) -> dict | None:
        with self.db() as d:
            row = d.execute("SELECT * FROM appeals WHERE id=?", (int(appeal_id),)).fetchone()
            return dict(row) if row else None

    def submit(self, appeal_id: int, text: str) -> bool:
        with self.db() as d:
            return d.execute(
                "UPDATE appeals SET text=?, created=? WHERE id=? AND status='pending'",
                (str(text or "")[:1200], self.clock(), int(appeal_id)),
            ).rowcount == 1

    def pending(self, limit: int = 50) -> list[dict]:
        with self.db() as d:
            return [dict(r) for r in d.execute(
                "SELECT * FROM appeals WHERE status='pending' ORDER BY id DESC LIMIT ?", (int(limit),)
            )]

    def decide(self, appeal_id: int, status: str, note: str = "") -> bool:
        with self.db() as d:
            return d.execute(
                "UPDATE appeals SET status=?, owner_note=? WHERE id=? AND status='pending'",
                (str(status), str(note or "")[:500], int(appeal_id)),
            ).rowcount == 1


appeal_store = AppealStore(DB)
