"""独立节点与公开网页只读队列；节点凭据无支付审批或任意代码执行能力。"""
from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import re
import secrets
import uuid
from urllib.parse import urlsplit

from .control import AgentControl, ControlError


def hostname(value):
    if not isinstance(value, str) or not value or len(value) > 253:
        raise ControlError('域名无效')
    try:
        host = value.rstrip('.').encode('idna').decode('ascii').lower()
    except UnicodeError as error:
        raise ControlError('域名无效') from error
    if not re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', host) or '..' in host:
        raise ControlError('仅支持精确域名，不支持通配符、端口或路径')
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
        raise ControlError('禁止内网域名')
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ControlError('禁止非公网地址')
    return host


def validate_url(value, domains):
    if not isinstance(value, str) or len(value) > 2000 or any(ord(c) < 33 for c in value):
        raise ControlError('URL无效')
    try:
        parsed = urlsplit(value)
        port = parsed.port
        host = hostname(parsed.hostname or '')
    except ValueError as error:
        raise ControlError('URL无效') from error
    if parsed.scheme != 'https' or port not in (None, 443) or parsed.username or parsed.password:
        raise ControlError('只允许无凭据的HTTPS标准端口URL')
    if host not in domains:
        raise ControlError('域名不在节点白名单中')
    return host


class NodeQueue:
    def __init__(self, control: AgentControl):
        self.control = control
        self.artifacts = control.path.parent / 'artifacts'
        with control._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS worker_nodes (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,
                    enabled INTEGER NOT NULL, domains TEXT NOT NULL, last_seen REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS browser_jobs (
                    id TEXT PRIMARY KEY, node_id TEXT NOT NULL, url TEXT NOT NULL,
                    state TEXT NOT NULL, created REAL NOT NULL, lease_hash TEXT,
                    lease_until REAL, result TEXT, result_hash TEXT);
            ''')

    def create_node(self, name, domains, actor):
        if not isinstance(name, str) or not name.strip() or len(name) > 80:
            raise ControlError('节点名无效')
        if not isinstance(domains, list) or not 1 <= len(domains) <= 30:
            raise ControlError('请指定1到30个精确域名')
        domains = sorted({hostname(d) for d in domains})
        token, nid = secrets.token_urlsafe(32), uuid.uuid4().hex
        with self.control._tx() as db:
            db.execute('INSERT INTO worker_nodes(id,name,token_hash,enabled,domains) VALUES (?,?,?,?,?)',
                       (nid, name, hashlib.sha256(token.encode()).hexdigest(), 0, json.dumps(domains)))
            self.control._event(db, actor, 'node.create', detail=nid)
        return {'id': nid, 'token': token, 'enabled': False}

    def update_node(self, nid, enabled, actor):
        if type(enabled) is not bool:
            raise ControlError('节点开关无效')
        with self.control._tx() as db:
            if not db.execute('SELECT 1 FROM worker_nodes WHERE id=?', (nid,)).fetchone():
                raise ControlError('节点不存在')
            db.execute('UPDATE worker_nodes SET enabled=? WHERE id=?', (int(enabled), nid))
            self.control._event(db, actor, 'node.enable' if enabled else 'node.disable', detail=nid)
        return {'ok': True}

    def _node(self, db, token):
        if not isinstance(token, str) or not 32 <= len(token) <= 128:
            raise ControlError('节点认证失败')
        row = db.execute('SELECT * FROM worker_nodes WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        if not row or not row['enabled']:
            raise ControlError('节点未授权或已停用')
        return row

    def submit(self, nid, url, actor):
        with self.control._tx() as db:
            if self.control.policy(db)['paused']:
                raise ControlError('自主能力已暂停')
            node = db.execute('SELECT * FROM worker_nodes WHERE id=?', (nid,)).fetchone()
            if not node or not node['enabled']:
                raise ControlError('节点不存在或未启用')
            validate_url(url, json.loads(node['domains']))
            jid = uuid.uuid4().hex
            db.execute('INSERT INTO browser_jobs(id,node_id,url,state,created) VALUES (?,?,?,?,?)',
                       (jid, nid, url, 'queued', self.control.clock()))
            self.control._event(db, actor, 'browser.submit', jid)
        return {'id': jid, 'state': 'queued'}

    def claim(self, token):
        with self.control._tx() as db:
            node = self._node(db, token)
            now = self.control.clock()
            db.execute('UPDATE worker_nodes SET last_seen=? WHERE id=?', (now, node['id']))
            # 只读任务超时也不自动重派，避免无限重试；每次重试由所有者重新提交。
            db.execute("UPDATE browser_jobs SET state='expired' WHERE (state='running' AND lease_until<?) OR (state='queued' AND created<?)", (now, now - 600))
            if self.control.policy(db)['paused']:
                return {'job': None, 'paused': True}
            row = db.execute("SELECT * FROM browser_jobs WHERE node_id=? AND state='queued' ORDER BY created,rowid LIMIT 1", (node['id'],)).fetchone()
            if not row:
                return {'job': None, 'paused': False}
            lease = secrets.token_urlsafe(32)
            db.execute("UPDATE browser_jobs SET state='running',lease_hash=?,lease_until=? WHERE id=?",
                       (hashlib.sha256(lease.encode()).hexdigest(), now + 90, row['id']))
            self.control._event(db, 'node:' + node['id'], 'browser.claim', row['id'])
            return {'job': {'id': row['id'], 'url': row['url'], 'allowed_domains': json.loads(node['domains']),
                            'lease': lease, 'timeout_seconds': 30}, 'paused': False}

    def complete(self, token, jid, lease, result):
        if not isinstance(result, dict) or set(result) != {'ok', 'url', 'title', 'text', 'screenshot', 'error'}:
            raise ControlError('执行结果格式无效')
        if type(result['ok']) is not bool:
            raise ControlError('执行结果状态无效')
        for key, limit in (('url', 2000), ('title', 300), ('text', 20000), ('error', 300), ('screenshot', 2_800_000)):
            if not isinstance(result[key], str) or len(result[key]) > limit:
                raise ControlError('执行结果字段超限')
        digest = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
        with self.control._tx() as db:
            node = self._node(db, token)
            row = db.execute('SELECT * FROM browser_jobs WHERE id=? AND node_id=?', (jid, node['id'])).fetchone()
            if not row or not isinstance(lease, str) or hashlib.sha256(lease.encode()).hexdigest() != row['lease_hash']:
                raise ControlError('任务租约无效')
            if row['state'] in ('succeeded', 'failed'):
                if row['result_hash'] != digest:
                    raise ControlError('已完成任务不能替换结果')
                return {'ok': True, 'duplicate': True}
            if row['state'] != 'running' or row['lease_until'] < self.control.clock():
                raise ControlError('任务租约已过期')
            validate_url(result['url'], json.loads(node['domains']))
            saved = dict(result)
            screenshot = saved.pop('screenshot')
            if screenshot:
                try:
                    image = base64.b64decode(screenshot, validate=True)
                except ValueError as error:
                    raise ControlError('截图编码无效') from error
                if len(image) > 2 * 1024 * 1024 or not image.startswith(b'\x89PNG\r\n\x1a\n'):
                    raise ControlError('截图格式或大小无效')
                self.artifacts.mkdir(exist_ok=True)
                self.artifacts.chmod(0o700)
                path = self.artifacts / (jid + '.png')
                path.write_bytes(image)
                path.chmod(0o600)
            saved['has_screenshot'] = bool(screenshot)
            saved['external_effect'] = 'public_read_only'
            db.execute('UPDATE browser_jobs SET state=?,result=?,result_hash=? WHERE id=?',
                       ('succeeded' if result['ok'] else 'failed', json.dumps(saved, ensure_ascii=False), digest, jid))
            self.control._event(db, 'node:' + node['id'], 'browser.complete', jid, 'ok' if result['ok'] else 'failed')
        return {'ok': True}

    def snapshot(self):
        with self.control._db() as db:
            nodes = [{**dict(r), 'enabled': bool(r['enabled']), 'domains': json.loads(r['domains'])}
                     for r in db.execute('SELECT id,name,enabled,domains,last_seen FROM worker_nodes')]
            jobs = [{**dict(r), 'result': json.loads(r['result']) if r['result'] else None}
                    for r in db.execute('SELECT id,node_id,url,state,created,result FROM browser_jobs ORDER BY created DESC,rowid DESC LIMIT 50')]
        return {'nodes': nodes, 'browser_jobs': jobs}
