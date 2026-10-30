"""持久化任务与额度控制。当前执行器仅提供模拟，不执行外部副作用。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))
CAPABILITIES = {
    'browser.read': '浏览与资料研究',
    'social.publish': '跨平台发布',
    'payment.recharge': '服务充值',
    'shopping.gift': '购买与赠礼',
    'finance.research': '金融研究',
    'finance.paper': '金融模拟交易',
    'finance.live': '真实金融交易',
    'tool.develop': '工具开发与测试',
    'tool.deploy': '工具发布',
    'app.use': '使用应用 / 平台操作（不含付款）',
    'payment.pay': '独立资金支付 / 扣款',
}
CAPABILITY_META = {
    'app.use': {'kind': 'app', 'label': '使用应用'},
    'payment.pay': {'kind': 'payment', 'label': '资金支付'},
    'shopping.gift': {'kind': 'legacy', 'label': '购物/赠礼（旧版合并能力）'},
    'payment.recharge': {'kind': 'legacy', 'label': '服务充值（旧版合并能力）'},
}
DEFAULT_POLICY = {'paused': True, 'single_minor': 2000, 'daily_minor': 10000, 'monthly_minor': 150000, 'auto_daily_minor': 10000}
RESERVED = ('authorized', 'running', 'unknown')


class ControlError(ValueError):
    pass


def integer(value, name):
    if type(value) is not int or not 0 <= value <= 100_000_000:
        raise ControlError(f'{name} 必须是 0 到 100000000 的整数（分）')
    return value


class AgentControl:
    def __init__(self, path: Path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS grants (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, idem TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
                    body TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL,
                    expires REAL NOT NULL, authorized REAL, result TEXT);
                CREATE TABLE IF NOT EXISTS approvals (
                    task_id TEXT NOT NULL, rule TEXT NOT NULL, period TEXT NOT NULL,
                    context TEXT NOT NULL, actor TEXT NOT NULL, created REAL NOT NULL,
                    PRIMARY KEY(task_id, rule, period));
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT, time REAL NOT NULL,
                    actor TEXT NOT NULL, action TEXT NOT NULL, task_id TEXT, detail TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS payment_records (
                    id TEXT PRIMARY KEY, task_id TEXT UNIQUE NOT NULL, simulation INTEGER NOT NULL,
                    provider TEXT NOT NULL, provider_order_id TEXT NOT NULL, transaction_id TEXT NOT NULL,
                    amount_minor INTEGER NOT NULL, currency TEXT NOT NULL, summary TEXT NOT NULL,
                    details TEXT NOT NULL, created REAL NOT NULL, status TEXT NOT NULL,
                    UNIQUE(provider, provider_order_id));
                CREATE TABLE IF NOT EXISTS payment_deliveries (
                    payment_id TEXT NOT NULL, recipient TEXT NOT NULL, attempts INTEGER NOT NULL,
                    next_retry REAL NOT NULL, sent_at REAL, PRIMARY KEY(payment_id,recipient));
                CREATE TABLE IF NOT EXISTS plans (
                    id TEXT PRIMARY KEY, idem TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
                    body TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL);
            ''')
            db.execute('INSERT OR IGNORE INTO settings VALUES (1, ?)', (json.dumps(DEFAULT_POLICY),))
        self.path.chmod(0o600)
        self.path.parent.chmod(0o700)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _tx(self):
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise

    def _event(self, db, actor, action, task_id='', detail=''):
        db.execute('INSERT INTO events(time,actor,action,task_id,detail) VALUES (?,?,?,?,?)',
                   (self.clock(), actor, action, task_id, detail[:500]))

    def _expire(self, db):
        # 未执行授权过期释放额度；运行中/结果未知的任务绝不自动释放。
        db.execute("UPDATE tasks SET state='expired' WHERE state IN ('pending','authorized') AND expires<=?",
                   (self.clock(),))

    def policy(self, db):
        return {**DEFAULT_POLICY, **json.loads(db.execute('SELECT body FROM settings WHERE id=1').fetchone()[0])}

    def set_policy(self, policy, actor):
        if set(policy) != set(DEFAULT_POLICY) or type(policy['paused']) is not bool:
            raise ControlError('全局设置格式错误')
        for key in ('single_minor', 'daily_minor', 'monthly_minor', 'auto_daily_minor'):
            integer(policy[key], key)
        with self._tx() as db:
            db.execute('UPDATE settings SET body=? WHERE id=1', (json.dumps(policy),))
            self._event(db, actor, 'policy.update', detail=json.dumps(policy))
        return policy

    def save_grant(self, grant, actor):
        allowed = {'id', 'capability', 'mode', 'merchant', 'recipient', 'purpose', 'enabled',
                   'require_approval', 'single_minor', 'daily_minor', 'monthly_minor', 'expires_at'}
        if set(grant) != allowed:
            raise ControlError('授权字段不完整或包含未知字段')
        if grant['capability'] not in CAPABILITIES or grant['mode'] not in ('simulation', 'live'):
            raise ControlError('未知能力或环境')
        for key in ('enabled', 'require_approval'):
            if type(grant[key]) is not bool:
                raise ControlError('授权开关必须为布尔值')
        for key in ('single_minor', 'daily_minor', 'monthly_minor'):
            integer(grant[key], key)
        for key in ('id', 'merchant', 'recipient', 'purpose'):
            if not isinstance(grant[key], str) or not grant[key].strip() or len(grant[key]) > 160:
                raise ControlError(f'{key} 必须为非空、最多160字符的精确值')
        if type(grant['expires_at']) not in (int, float) or not self.clock() < grant['expires_at'] < self.clock() + 366 * 86400:
            raise ControlError('授权有效期必须在未来一年内')
        with self._tx() as db:
            # 一个范围只有一份授权，防止拆成多个 grant 绕过用途额度。
            for row in db.execute('SELECT body FROM grants WHERE id<>?', (grant['id'],)):
                other = json.loads(row[0])
                if all(other[k] == grant[k] for k in ('capability', 'mode', 'merchant', 'recipient', 'purpose')):
                    raise ControlError('相同范围已有授权，请编辑原授权')
            db.execute('INSERT OR REPLACE INTO grants VALUES (?,?)', (grant['id'], json.dumps(grant)))
            self._event(db, actor, 'grant.update', detail=grant['id'])
        return grant

    def _check(self, db, task):
        policy = self.policy(db)
        if policy['paused']:
            raise ControlError('自主能力已暂停')
        row = db.execute('SELECT body FROM grants WHERE id=?', (task['grant_id'],)).fetchone()
        if not row:
            raise ControlError('授权不存在')
        grant = json.loads(row[0])
        if not grant['enabled'] or grant['expires_at'] <= self.clock():
            raise ControlError('授权已停用或到期')
        if any(grant[k] != task[k] for k in ('capability', 'mode', 'merchant', 'recipient', 'purpose')):
            raise ControlError('任务不在授权范围内')
        if task['mode'] == 'live':
            raise ControlError('真实执行适配器尚未接入；此阶段仅支持模拟')
        return policy, grant

    def _usage(self, db, mode, grant=None, exclude=''):
        now = datetime.fromtimestamp(self.clock(), TZ)
        day = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
        daily = monthly = reserved = 0
        for row in db.execute("SELECT * FROM tasks WHERE state IN ('authorized','running','unknown','simulated','succeeded') AND id<>?", (exclude,)):
            body = json.loads(row['body'])
            if body['mode'] != mode or (grant and body['grant_id'] != grant):
                continue
            amount = body['amount_minor']
            if row['state'] in RESERVED:
                # 跨日/月仍未完成的预占持续计入当前限额，防止跨边界绕过。
                reserved += amount
                daily += amount
                monthly += amount
            else:
                daily += amount if row['authorized'] >= day else 0
                monthly += amount if row['authorized'] >= month else 0
        return {'daily_minor': daily, 'monthly_minor': monthly, 'reserved_minor': reserved}

    def _requirements(self, db, row):
        task = json.loads(row['body'])
        policy = self.policy(db)
        record = db.execute('SELECT body FROM grants WHERE id=?', (task['grant_id'],)).fetchone()
        if not record:
            return []
        grant = json.loads(record[0])
        now = datetime.fromtimestamp(self.clock(), TZ)
        periods = {'single': row['id'], 'daily': now.strftime('%Y-%m-%d'), 'monthly': now.strftime('%Y-%m')}
        requirements = []

        def add(scope, dimension, limit, used, period):
            rule = scope + ':' + dimension
            context = hashlib.sha256(json.dumps([row['fingerprint'], rule, limit]).encode()).hexdigest()
            approval = db.execute('SELECT context FROM approvals WHERE task_id=? AND rule=? AND period=?',
                                  (row['id'], rule, period)).fetchone()
            requirements.append({'rule': rule, 'scope': scope, 'dimension': dimension,
                                 'period': period, 'limit_minor': limit, 'used_minor': used,
                                 'amount_minor': task['amount_minor'],
                                 'projected_minor': used + task['amount_minor'],
                                 'approved': bool(approval and approval[0] == context), 'context': context})

        for scope, limits, grant_id in (('global', policy, None), ('grant', grant, task['grant_id'])):
            usage = self._usage(db, task['mode'], grant_id, row['id'])
            for dimension in ('single', 'daily', 'monthly'):
                used = 0 if dimension == 'single' else usage[dimension + '_minor']
                limit = limits[dimension + '_minor']
                if used + task['amount_minor'] > limit:
                    add(scope, dimension, limit, used, periods[dimension])
        if grant['require_approval']:
            add('grant', 'review', 0, 0, row['id'])
        else:
            usage = self._usage(db, task['mode'], exclude=row['id'])
            # 日额度和免审核阈值相同时，日审批已经表达人工确认，不再重复要求。
            daily_needed = any(r['rule'] == 'global:daily' for r in requirements)
            if (usage['daily_minor'] + task['amount_minor'] > policy['auto_daily_minor']
                    and not (daily_needed and policy['auto_daily_minor'] >= policy['daily_minor'])):
                add('global', 'review', policy['auto_daily_minor'], usage['daily_minor'], periods['daily'])
        return requirements

    def _reserve(self, db, row, actor):
        self._check(db, json.loads(row['body']))
        missing = [r for r in self._requirements(db, row) if not r['approved']]
        if missing:
            names = {'single': '单笔', 'daily': '每日', 'monthly': '每月', 'review': '常规'}
            raise ControlError('仍需独立审批：' + '、'.join(dict.fromkeys(names[r['dimension']] for r in missing)))
        db.execute("UPDATE tasks SET state='authorized', authorized=? WHERE id=?", (self.clock(), row['id']))
        self._event(db, actor, 'task.authorize', row['id'])

    def _task(self, row, db=None):
        result = {**json.loads(row['body']), 'id': row['id'], 'state': row['state'],
                  'created_at': row['created'], 'expires_at': row['expires'],
                  'result': json.loads(row['result']) if row['result'] else None}
        if row['state'] in ('pending', 'authorized'):
            if db is None:
                with self._db() as connection:
                    requirements = self._requirements(connection, row)
            else:
                requirements = self._requirements(db, row)
            result['requirements'] = [{k: v for k, v in r.items() if k != 'context'} for r in requirements]
            result['required_approvals'] = list(dict.fromkeys(r['dimension'] for r in requirements if not r['approved']))
        else:
            result.update(requirements=[], required_approvals=[])
        return result

    def propose_plan(self, plan, idempotency_key, actor='bot'):
        """规划层只保存步骤，不预占额度、不执行；应用操作和支付是独立能力。"""
        fields = {'goal', 'description', 'steps'}
        if set(plan) != fields:
            raise ControlError('计划字段不完整或包含未知字段')
        if not isinstance(plan['goal'], str) or not plan['goal'].strip() or len(plan['goal']) > 160:
            raise ControlError('目标无效')
        if not isinstance(plan['description'], str) or len(plan['description']) > 500:
            raise ControlError('计划说明无效')
        if not isinstance(plan['steps'], list) or not 1 <= len(plan['steps']) <= 8:
            raise ControlError('计划步骤数量应为 1 到 8')
        clean_steps = []
        for index, step in enumerate(plan['steps']):
            allowed = {'capability', 'mode', 'merchant', 'recipient', 'purpose', 'amount_minor', 'description', 'depends_on'}
            if not isinstance(step, dict) or set(step) != allowed:
                raise ControlError(f'第 {index + 1} 步字段无效')
            if step['capability'] not in CAPABILITIES or step['mode'] not in ('simulation', 'live'):
                raise ControlError(f'第 {index + 1} 步能力无效')
            for key in ('merchant', 'recipient', 'purpose'):
                if not isinstance(step[key], str) or not step[key].strip() or len(step[key]) > 160:
                    raise ControlError(f'第 {index + 1} 步 {key} 无效')
            integer(step['amount_minor'], 'amount_minor')
            if step['capability'] == 'payment.pay' and step['amount_minor'] <= 0:
                raise ControlError('支付步骤必须填写金额')
            if step['capability'] == 'app.use' and step['amount_minor'] != 0:
                raise ControlError('应用操作步骤不直接扣款，金额必须为 0')
            depends = step.get('depends_on')
            if depends is not None and (not isinstance(depends, int) or depends < 0 or depends >= index):
                raise ControlError('依赖步骤必须指向更早的步骤')
            description = str(step.get('description') or '')
            if len(description) > 500:
                raise ControlError('步骤说明过长')
            clean_steps.append({**step, 'description': description})
        body = {'goal': plan['goal'].strip(), 'description': plan['description'], 'steps': clean_steps}
        serialized = json.dumps(body, sort_keys=True, ensure_ascii=False)
        fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
        if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 160:
            raise ControlError('幂等键长度应为8到160字符')
        with self._tx() as db:
            old = db.execute('SELECT * FROM plans WHERE idem=?', (idempotency_key,)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:
                    raise ControlError('幂等键已用于不同计划')
                return self._plan(old)
            pid = uuid.uuid4().hex
            db.execute('INSERT INTO plans(id,idem,fingerprint,body,state,created,expires) VALUES (?,?,?,?,?,?,?)',
                       (pid, idempotency_key, fingerprint, serialized, 'draft', self.clock(), self.clock() + 86400))
            self._event(db, actor, 'plan.propose', pid)
            return self._plan(db.execute('SELECT * FROM plans WHERE id=?', (pid,)).fetchone())

    def _plan(self, row):
        return {**json.loads(row['body']), 'id': row['id'], 'state': row['state'],
                'created_at': row['created'], 'expires_at': row['expires']}

    def cancel_plan(self, plan_id, actor):
        with self._tx() as db:
            row = db.execute('SELECT * FROM plans WHERE id=?', (plan_id,)).fetchone()
            if not row:
                raise ControlError('计划不存在')
            if row['state'] != 'draft':
                raise ControlError('当前计划不可取消')
            db.execute("UPDATE plans SET state='cancelled' WHERE id=?", (plan_id,))
            self._event(db, actor, 'plan.cancel', plan_id)
            return self._plan(db.execute('SELECT * FROM plans WHERE id=?', (plan_id,)).fetchone())

    def propose(self, task, idempotency_key, actor='bot'):
        fields = {'grant_id', 'capability', 'mode', 'merchant', 'recipient', 'purpose', 'amount_minor', 'currency', 'description'}
        if set(task) != fields:
            raise ControlError('任务字段不完整或包含未知字段')
        if task['currency'] != 'CNY':
            raise ControlError('第一阶段仅支持 CNY；金额使用整数分')
        integer(task['amount_minor'], 'amount_minor')
        if task['capability'] == 'payment.pay' and task['amount_minor'] <= 0:
            raise ControlError('支付任务必须填写金额')
        if task['capability'] == 'app.use' and task['amount_minor'] != 0:
            raise ControlError('应用操作任务不直接扣款，金额必须为 0')
        for key in fields - {'amount_minor'}:
            if not isinstance(task[key], str) or not task[key].strip() or len(task[key]) > (500 if key == 'description' else 160):
                raise ControlError(f'任务字段 {key} 无效')
        if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 160:
            raise ControlError('幂等键长度应为8到160字符')
        serialized = json.dumps(task, sort_keys=True, ensure_ascii=False)
        fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
        with self._tx() as db:
            self._expire(db)
            old = db.execute('SELECT * FROM tasks WHERE idem=?', (idempotency_key,)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:
                    raise ControlError('幂等键已用于不同任务')
                return self._task(old, db)
            _, grant = self._check(db, task)
            tid = uuid.uuid4().hex
            db.execute('INSERT INTO tasks(id,idem,fingerprint,body,state,created,expires) VALUES (?,?,?,?,?,?,?)',
                       (tid, idempotency_key, fingerprint, serialized, 'pending', self.clock(), self.clock() + 3600))
            row = db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
            self._event(db, actor, 'task.propose', tid)
            if not self._task(row, db)['required_approvals']:
                self._reserve(db, row, actor)
            else:
                self._event(db, actor, 'task.manual_required', tid, '需要按维度独立审批')
            return self._task(db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone(), db)

    def transition(self, tid, action, actor, expected_requirements=None):
        if action not in ('approve', 'approve-single', 'approve-daily', 'approve-monthly', 'cancel', 'simulate'):
            raise ControlError('未知操作')
        with self._tx() as db:
            self._expire(db)
            row = db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
            if not row:
                raise ControlError('任务不存在')
            if action.startswith('approve'):
                if row['state'] not in ('pending', 'authorized'):
                    raise ControlError('只有待审批或尚未执行的授权任务可审批')
                self._check(db, json.loads(row['body']))
                dimension = action.removeprefix('approve-') if action != 'approve' else 'review'
                requirements = self._requirements(db, row)
                selected = [r for r in requirements if r['dimension'] in (dimension, 'review')]
                if expected_requirements is not None:
                    actual=sorted([[r[k] for k in ('rule','period','context','projected_minor')] for r in selected])
                    if row['state']!='pending' or actual!=expected_requirements:
                        raise ControlError('审批条件已经变化，需要重新确认')
                if action != 'approve' and not any(r['dimension'] == dimension for r in requirements):
                    raise ControlError('该任务当前不需要此维度的超限审批')
                for requirement in selected:
                    db.execute('INSERT OR REPLACE INTO approvals VALUES (?,?,?,?,?,?)',
                               (tid, requirement['rule'], requirement['period'], requirement['context'], actor, self.clock()))
                    self._event(db, actor, 'quota.approve', tid,
                                requirement['rule'] + ' / ' + requirement['period'] + ' / 仅此任务')
                if not any(not r['approved'] for r in self._requirements(db, row)):
                    self._reserve(db, row, actor)
                else:
                    db.execute("UPDATE tasks SET state='pending' WHERE id=?", (tid,))
            elif action == 'cancel':
                if row['state'] not in ('pending', 'authorized'):
                    raise ControlError('当前状态不可取消')
                db.execute("UPDATE tasks SET state='cancelled' WHERE id=?", (tid,))
            else:
                if row['state'] == 'simulated':
                    return self._task(row, db)
                if row['state'] != 'authorized':
                    raise ControlError('任务未获授权或已经结束')
                # 执行前再次检查撤权、暂停和降低后的额度。
                self._reserve(db, row, actor)
                result = {'simulation': True, 'external_effect': False,
                          'message': '仅完成权限和额度演练；未访问平台、未下单、未付款、未交易、未运行代码。'}
                db.execute("UPDATE tasks SET state='simulated', result=? WHERE id=?", (json.dumps(result, ensure_ascii=False), tid))
                from .payments import Payments
                Payments(self).simulation_db(db, row)
            self._event(db, actor, 'task.' + action, tid)
            return self._task(db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone(), db)

    def pending_notifications(self, recipient):
        with self._tx() as db:
            self._expire(db)
            return [self._task(r, db) for r in db.execute(
                "SELECT * FROM tasks WHERE state='pending' AND id NOT IN "
                "(SELECT task_id FROM events WHERE action=?) ORDER BY created LIMIT 5",
                ('notify:' + recipient,))]

    def mark_notified(self, tid, recipient):
        with self._tx() as db:
            self._event(db, 'system', 'notify:' + recipient, tid)

    def snapshot(self):
        with self._tx() as db:
            self._expire(db)
            return {'policy': self.policy(db), 'currency': 'CNY', 'timezone': 'Asia/Shanghai',
                    'live_available': False, 'executor': 'authorization_simulation',
                    'capabilities': [{'id': key, 'label': val, **CAPABILITY_META.get(key, {})} for key, val in CAPABILITIES.items()],
                    'plans': [self._plan(r) for r in db.execute('SELECT * FROM plans ORDER BY created DESC, rowid DESC LIMIT 30')],
                    'grants': [json.loads(r[0]) for r in db.execute('SELECT body FROM grants ORDER BY id')],
                    'usage': self._usage(db, 'simulation'),
                    'tasks': [self._task(r, db) for r in db.execute('SELECT * FROM tasks ORDER BY created DESC, rowid DESC LIMIT 100')],
                    'events': [dict(r) for r in db.execute('SELECT * FROM events ORDER BY seq DESC LIMIT 100')]}
