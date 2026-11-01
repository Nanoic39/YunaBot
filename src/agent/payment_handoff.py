"""Structured record for purchases that hand the Alipay password step to the owner."""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / 'data' / 'agent' / 'payment_handoffs.sqlite3'


class PaymentHandoffStore:
    def __init__(self, path=DB, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as d:
            d.executescript('''CREATE TABLE IF NOT EXISTS handoffs(
                id INTEGER PRIMARY KEY, task_id TEXT, merchant TEXT, order_id TEXT,
                amount_minor INTEGER, purpose TEXT, status TEXT DEFAULT 'awaiting_password',
                created REAL, updated REAL, note TEXT DEFAULT '')''')
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

    def create(self, task_id, merchant='', order_id='', amount_minor=0, purpose=''):
        with self.db() as d:
            rid = d.execute(
                'INSERT INTO handoffs(task_id,merchant,order_id,amount_minor,purpose,created,updated) VALUES(?,?,?,?,?,?,?)',
                (str(task_id), str(merchant or '')[:120], str(order_id or '')[:160],
                 max(0, int(amount_minor or 0)), str(purpose or '')[:500], self.clock(), self.clock()),
            ).lastrowid
            return dict(d.execute('SELECT * FROM handoffs WHERE id=?', (rid,)).fetchone())

    def list(self, limit=100):
        with self.db() as d:
            return [dict(r) for r in d.execute('SELECT * FROM handoffs ORDER BY id DESC LIMIT ?', (int(limit),))]

    def expire_stale(self, minutes: int = 35) -> int:
        cutoff = self.clock() - max(1, int(minutes)) * 60
        with self.db() as d:
            cur = d.execute("UPDATE handoffs SET status='expired',note='等待支付密码超时',updated=? "
                            "WHERE status IN ('awaiting_password','awaiting_user_password') AND created<?",
                            (self.clock(), cutoff))
            return int(cur.rowcount or 0)

    def status(self, rid, status, note=''):
        with self.db() as d:
            d.execute('UPDATE handoffs SET status=?,note=?,updated=? WHERE id=?',
                      (str(status), str(note or '')[:500], self.clock(), int(rid)))
            return bool(d.total_changes)


handoff_store = PaymentHandoffStore()
