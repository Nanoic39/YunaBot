"""WeChat identity/whitelist registry. Network actions stay in the phone agent."""
from __future__ import annotations

import hashlib
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / 'data' / 'agent' / 'wechat.sqlite3'


class WeChatStore:
    def __init__(self, path: Path = DB, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as d:
            d.executescript('''
                CREATE TABLE IF NOT EXISTS contacts(
                    id6 TEXT PRIMARY KEY, wxid TEXT DEFAULT '', nickname TEXT,
                    remark TEXT DEFAULT '', whitelist INTEGER DEFAULT 0,
                    source TEXT DEFAULT 'wechat', created REAL, updated REAL);
                CREATE TABLE IF NOT EXISTS groups(
                    id6 TEXT PRIMARY KEY, wxid TEXT DEFAULT '', name TEXT,
                    whitelist INTEGER DEFAULT 0, source TEXT DEFAULT 'wechat',
                    created REAL, updated REAL);
                CREATE TABLE IF NOT EXISTS messages(
                    id INTEGER PRIMARY KEY, contact_id6 TEXT, direction TEXT,
                    text TEXT, created REAL, meta TEXT DEFAULT '{}');
            ''')
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

    def _new_id(self, seed: str, used: set[str]) -> str:
        base = hashlib.sha256(('wechat:' + seed).encode('utf-8')).hexdigest().lower()
        for i in range(0, 64, 6):
            candidate = base[i:i + 6]
            if len(candidate) == 6 and candidate not in used:
                return candidate
        for n in range(10000):
            candidate = hashlib.sha256(f'wechat:{seed}:{n}'.encode()).hexdigest()[:6]
            if candidate not in used:
                return candidate
        raise RuntimeError('无法分配微信ID')

    def contact(self, nickname: str, wxid: str = '', whitelist: bool = False) -> dict:
        nickname = str(nickname or '').strip()[:80]
        wxid = str(wxid or '').strip()[:120]
        if not nickname and not wxid:
            raise ValueError('微信昵称不能为空')
        with self.db() as d:
            row = None
            if wxid:
                row = d.execute('SELECT * FROM contacts WHERE wxid=?', (wxid,)).fetchone()
            if row is None and nickname:
                row = d.execute('SELECT * FROM contacts WHERE nickname=?', (nickname,)).fetchone()
            if row:
                if whitelist and not row['whitelist']:
                    d.execute('UPDATE contacts SET whitelist=1, updated=? WHERE id6=?', (self.clock(), row['id6']))
                return dict(d.execute('SELECT * FROM contacts WHERE id6=?', (row['id6'],)).fetchone())
            used = {r[0] for r in d.execute('SELECT id6 FROM contacts')}
            id6 = self._new_id(wxid or nickname, used)
            now = self.clock()
            d.execute('INSERT INTO contacts(id6,wxid,nickname,remark,whitelist,source,created,updated) VALUES(?,?,?,?,?,?,?,?)',
                      (id6, wxid, nickname, '', 1 if whitelist else 0, 'wechat', now, now))
            return dict(d.execute('SELECT * FROM contacts WHERE id6=?', (id6,)).fetchone())

    @staticmethod
    def display_name(row: dict) -> str:
        return f"{row.get('id6')}-{row.get('remark') or row.get('nickname') or ''}"

    def list_contacts(self, whitelist_only: bool = False) -> list[dict]:
        with self.db() as d:
            sql = 'SELECT * FROM contacts' + (' WHERE whitelist=1' if whitelist_only else '') + ' ORDER BY updated DESC, id6'
            return [dict(r) for r in d.execute(sql)]

    def set_whitelist(self, id6: str, enabled: bool) -> bool:
        with self.db() as d:
            return d.execute('UPDATE contacts SET whitelist=?, updated=? WHERE id6=?',
                             (1 if enabled else 0, self.clock(), str(id6))).rowcount == 1

    def set_remark(self, id6: str, remark: str) -> bool:
        with self.db() as d:
            return d.execute('UPDATE contacts SET remark=?, updated=? WHERE id6=?',
                             (str(remark or '')[:80], self.clock(), str(id6))).rowcount == 1

    def group(self, name: str, wxid: str = '', whitelist: bool = False) -> dict:
        name = str(name or '').strip()[:120]
        if not name:
            raise ValueError('微信群名不能为空')
        with self.db() as d:
            row = d.execute('SELECT * FROM groups WHERE name=?', (name,)).fetchone()
            if row:
                if whitelist and not row['whitelist']:
                    d.execute('UPDATE groups SET whitelist=1, updated=? WHERE id6=?', (self.clock(), row['id6']))
                return dict(d.execute('SELECT * FROM groups WHERE id6=?', (row['id6'],)).fetchone())
            used = {r[0] for r in d.execute('SELECT id6 FROM groups')}
            id6 = self._new_id(wxid or ('group:' + name), used)
            now = self.clock()
            d.execute('INSERT INTO groups(id6,wxid,name,whitelist,source,created,updated) VALUES(?,?,?,?,?,?,?)',
                      (id6, str(wxid or ''), name, 1 if whitelist else 0, 'wechat', now, now))
            return dict(d.execute('SELECT * FROM groups WHERE id6=?', (id6,)).fetchone())

    def list_groups(self, whitelist_only: bool = False) -> list[dict]:
        with self.db() as d:
            sql = 'SELECT * FROM groups' + (' WHERE whitelist=1' if whitelist_only else '') + ' ORDER BY updated DESC, id6'
            return [dict(r) for r in d.execute(sql)]


wechat_store = WeChatStore()
