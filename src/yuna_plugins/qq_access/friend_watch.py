"""好友列表快照：补偿 SnowLuma/OneBot 不稳定的好友新增/删除通知。"""
from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path


class FriendWatchStore:
    def __init__(self, path: Path, clock=time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.lock = threading.RLock()
        with self.db() as d:
            d.executescript('''
            CREATE TABLE IF NOT EXISTS friends(
              account TEXT, qq TEXT, nickname TEXT, remark TEXT,
              first_seen REAL, last_seen REAL, removed_at REAL DEFAULT 0,
              blacklisted INTEGER DEFAULT 0, blacklisted_at REAL DEFAULT 0,
              readded_count INTEGER DEFAULT 0,
              PRIMARY KEY(account, qq)
            );
            CREATE TABLE IF NOT EXISTS snapshots(
              account TEXT PRIMARY KEY, initialized INTEGER DEFAULT 0, updated REAL
            );
            CREATE TABLE IF NOT EXISTS unidirectional(
              account TEXT, qq TEXT, nickname TEXT, first_seen REAL, last_seen REAL,
              PRIMARY KEY(account, qq)
            );
            ''')

    @contextmanager
    def db(self):
        d = sqlite3.connect(self.path)
        d.row_factory = sqlite3.Row
        try:
            with d:
                yield d
        finally:
            d.close()

    @staticmethod
    def normalize(rows) -> dict[str, dict]:
        if isinstance(rows, dict):
            rows = rows.get('friends') or rows.get('list') or []
        result = {}
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            qq = str(row.get('user_id') or row.get('qq') or row.get('uin') or '').strip()
            if not qq.isdigit():
                continue
            result[qq] = {
                'qq': qq,
                'nickname': str(row.get('nickname') or row.get('name') or '')[:60],
                'remark': str(row.get('remark') or '')[:80],
            }
        return result

    def sync(self, account: str, rows) -> list[dict]:
        """返回本次新增/删除事件；第一次只建立基线，不产生推送。"""
        actual = self.normalize(rows)
        events = []
        with self.lock, self.db() as d:
            snap = d.execute('SELECT initialized FROM snapshots WHERE account=?', (account,)).fetchone()
            initialized = bool(snap and snap['initialized'])
            old_rows = {r['qq']: dict(r) for r in d.execute('SELECT * FROM friends WHERE account=?', (account,))}
            now = self.clock()
            for qq, info in actual.items():
                old = old_rows.get(qq)
                if initialized and (old is None or float(old.get('removed_at') or 0) > 0):
                    re_added = bool(old and float(old.get('removed_at') or 0) > 0)
                    if old:
                        d.execute('UPDATE friends SET readded_count=readded_count+1 WHERE account=? AND qq=?', (account, qq))
                    events.append({
                        'type': 'added', 'qq': qq, 'nickname': info['nickname'],
                        'remark': info['remark'], 're_added': re_added,
                        'blacklisted': bool(old and old.get('blacklisted')),
                    })
                d.execute('''
                INSERT INTO friends(account,qq,nickname,remark,first_seen,last_seen,removed_at)
                VALUES(?,?,?,?,?,?,0)
                ON CONFLICT(account,qq) DO UPDATE SET nickname=excluded.nickname,
                  remark=excluded.remark,last_seen=excluded.last_seen,removed_at=0
                ''', (account, qq, info['nickname'], info['remark'], now if not old else float(old.get('first_seen') or now), now))
            for qq, old in old_rows.items():
                if qq not in actual and not float(old.get('removed_at') or 0):
                    d.execute('UPDATE friends SET removed_at=? WHERE account=? AND qq=?', (now, account, qq))
                    if initialized:
                        events.append({
                            'type': 'removed', 'qq': qq,
                            'nickname': str(old.get('nickname') or ''), 'remark': str(old.get('remark') or ''),
                            'blacklisted': bool(old.get('blacklisted')),
                        })
            d.execute('INSERT INTO snapshots(account,initialized,updated) VALUES(?,1,?) ON CONFLICT(account) DO UPDATE SET initialized=1,updated=excluded.updated', (account, now))
        return events

    def sync_unidirectional(self, account: str, rows) -> list[dict]:
        """单向好友：只通知新出现的，重复轮询不刷屏。"""
        if isinstance(rows, dict):
            rows = rows.get('friends') or rows.get('list') or rows.get('data') or []
        new = []
        now = self.clock()
        with self.lock, self.db() as d:
            for row in rows or []:
                if not isinstance(row, dict):
                    continue
                qq = str(row.get('user_id') or row.get('uin') or row.get('qq') or row.get('uid') or '').strip()
                if not qq.isdigit():
                    continue
                nick = str(row.get('nick') or row.get('nickname') or row.get('name') or '')[:60]
                old = d.execute('SELECT 1 FROM unidirectional WHERE account=? AND qq=?', (account, qq)).fetchone()
                if old:
                    d.execute('UPDATE unidirectional SET last_seen=?,nickname=? WHERE account=? AND qq=?', (now, nick, account, qq))
                else:
                    d.execute('INSERT INTO unidirectional VALUES(?,?,?,?,?)', (account, qq, nick, now, now))
                    new.append({'qq': qq, 'nickname': nick})
        return new

    def blacklist(self, account: str, qq: str, enabled: bool = True) -> bool:
        with self.lock, self.db() as d:
            row = d.execute('SELECT 1 FROM friends WHERE account=? AND qq=?', (account, qq)).fetchone()
            if not row:
                d.execute('INSERT INTO friends(account,qq,nickname,remark,first_seen,last_seen,blacklisted,blacklisted_at) VALUES(?,?,?,?,?,?,?,?)',
                          (account, qq, '', '', self.clock(), self.clock(), 1 if enabled else 0, self.clock() if enabled else 0))
            else:
                d.execute('UPDATE friends SET blacklisted=?, blacklisted_at=? WHERE account=? AND qq=?',
                          (1 if enabled else 0, self.clock() if enabled else 0, account, qq))
            return True

    def is_blacklisted(self, account: str, qq: str) -> bool:
        with self.db() as d:
            row = d.execute('SELECT blacklisted FROM friends WHERE account=? AND qq=?', (account, qq)).fetchone()
            return bool(row and row['blacklisted'])

    def state(self, account: str, qq: str) -> dict:
        with self.db() as d:
            row = d.execute('SELECT * FROM friends WHERE account=? AND qq=?', (account, qq)).fetchone()
            return dict(row) if row else {}
