"""电脑时间：向 owner 申请、批准后才能在电脑 Agent 上操作。"""
from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path

TZ_OFFSET = 8
MIN_MINUTES = 1
MAX_MINUTES = 1440


class PhonePlayError(ValueError):
    pass


class PhonePlayStore:
    def __init__(self, path: Path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.lock = threading.RLock()
        self.data = self._load()

    def _load(self):
        default = {'status': 'idle', 'request': {}, 'session': {}, 'resume_goal': '', 'last': {}, 'retry_at': 0, 'retry_plan': '', 'retry_duration': 20, 'retry_resume_task_id':None, 'retry_resume_guidance':'', 'cooldown_until': 0}
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if isinstance(data, dict):
                default.update(data)
        except (OSError, ValueError):
            pass
        return default

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(self.path)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def _expire(self):
        now = self.clock()
        if self.data.get('status') == 'pending':
            req = self.data.get('request') or {}
            if float(req.get('expires') or 0) <= now:
                self.data['status'] = 'idle'
                self.data['request'] = {}
                self.data['last'] = {'status': 'expired', 'time': now}
                self.data.update({'retry_at':0,'retry_plan':'','cooldown_until':now+24*3600})
                self._save()
        if self.data.get('status') == 'active':
            ses = self.data.get('session') or {}
            if float(ses.get('planned_end') or 0) <= now:
                self.data['status'] = 'idle'
                self.data['last'] = {'status': 'ended', 'time': now, 'reason': 'time_up'}
                self.data['session'] = {}
                self.data['cooldown_until'] = 0
                self._save()

    def snapshot(self):
        with self.lock:
            self._expire()
            data = json.loads(json.dumps(self.data, ensure_ascii=False))
            if data.get('status') == 'active':
                ses = data.get('session') or {}
                data['remaining_minutes'] = max(0, round((float(ses.get('planned_end') or 0) - self.clock()) / 60, 1))
            return data

    def can_operate(self):
        with self.lock:
            self._expire()
            return self.data.get('status') == 'active'

    def request(self, plan, duration_minutes, reason='想玩一会儿电脑', force=False, resume_task_id=None, resume_guidance=""):
        if not isinstance(plan, str) or not plan.strip() or len(plan) > 500:
            raise PhonePlayError('电脑计划无效')
        if type(duration_minutes) is not int or not MIN_MINUTES <= duration_minutes <= MAX_MINUTES:
            raise PhonePlayError(f'使用时长应为 {MIN_MINUTES} 到 {MAX_MINUTES} 分钟')
        if not isinstance(resume_guidance,str) or len(resume_guidance)>1000:
            raise PhonePlayError('接续要求无效')
        if resume_task_id is not None and (not isinstance(resume_task_id,str) or not 1<=len(resume_task_id)<=64):
            raise PhonePlayError('接续来源无效')
        with self.lock:
            self._expire()
            if self.data.get('status') != 'idle':
                raise PhonePlayError('已有电脑请求或正在电脑时间')
            now = self.clock()
            if not force and float(self.data.get('cooldown_until') or 0) > now:
                raise PhonePlayError('刚玩过电脑，先去做点别的事')
            rid = secrets.token_hex(6)
            self.data.update({
                'status': 'pending',
                'request': {'id': rid, 'plan': plan.strip(), 'duration_minutes': duration_minutes,
                            'reason': str(reason or '')[:200], 'requested': now,
                            'expires': now + 600, 'notified': False, 'resume_task_id':resume_task_id, 'resume_guidance':resume_guidance},
                'retry_at': 0, 'retry_plan': '', 'retry_duration': 20, 'retry_resume_task_id':None, 'retry_resume_guidance':'',
            })
            self._save()
            return self.snapshot()

    def approve(self, request_id, actor='owner', duration_minutes=None):
        with self.lock:
            self._expire()
            req = self.data.get('request') or {}
            if self.data.get('status') != 'pending' or req.get('id') != request_id:
                raise PhonePlayError('电脑请求不存在或已过期')
            now = self.clock()
            minutes = int(duration_minutes) if duration_minutes is not None else int(req['duration_minutes'])
            if not MIN_MINUTES <= minutes <= MAX_MINUTES:
                raise PhonePlayError('使用时长无效')
            session = {
                'id': secrets.token_hex(6), 'plan': req['plan'],
                'duration_minutes': minutes,
                'requested': req['requested'], 'started': now,
                'planned_end': now + minutes * 60,
                'approved_by': str(actor), 'changes': [], 'resume_task_id':req.get('resume_task_id'), 'resume_guidance':req.get('resume_guidance',''),
            }
            self.data.update({'status': 'active', 'request': {}, 'session': session})
            self._save()
            return self.snapshot()

    def deny(self, request_id, actor='owner', retry_minutes=None):
        with self.lock:
            self._expire()
            req = self.data.get('request') or {}
            if self.data.get('status') != 'pending' or req.get('id') != request_id:
                raise PhonePlayError('电脑请求不存在或已过期')
            data = {'status': 'idle', 'request': {},
                    'last': {'status': 'denied', 'time': self.clock(), 'by': str(actor)}}
            if type(retry_minutes) is int and 1 <= retry_minutes <= 240:
                data.update({'retry_at': self.clock() + retry_minutes * 60,
                             'retry_plan': req.get('plan') or '想玩一会儿电脑',
                             'retry_duration': int(req.get('duration_minutes') or 20), 'retry_resume_task_id':req.get('resume_task_id'), 'retry_resume_guidance':req.get('resume_guidance','')})
            else:
                data.update({'retry_at': 0, 'retry_plan': '', 'retry_duration': 20, 'retry_resume_task_id':None, 'retry_resume_guidance':''})
            self.data.update(data)
            self._save()
            return self.snapshot()

    def defer(self, minutes, plan=None, duration_minutes=None):
        if type(minutes) is not int or not 1 <= minutes <= 240:
            raise PhonePlayError('推迟时间无效')
        with self.lock:
            self._expire()
            if self.data.get('status') not in ('pending', 'idle'):
                raise PhonePlayError('当前不能推迟电脑请求')
            req = self.data.get('request') or {}
            self.data.update({
                'status': 'idle', 'request': {},
                'retry_at': self.clock() + minutes * 60,
                'retry_plan': str(plan or req.get('plan') or '')[:500],
                'retry_duration': int(duration_minutes or req.get('duration_minutes') or 20),
                'retry_resume_task_id':req.get('resume_task_id') if plan is None else None,
                'retry_resume_guidance':req.get('resume_guidance','') if plan is None else '',
                'last': {'status': 'deferred', 'time': self.clock(), 'minutes': minutes},
            })
            self._save()
            return self.snapshot()

    def retry_due(self):
        with self.lock:
            self._expire()
            if self.data.get('status') != 'idle':
                return None
            retry_at = float(self.data.get('retry_at') or 0)
            if retry_at <= 0 or retry_at > self.clock():
                return None
            return {'plan': self.data.get('retry_plan') or '想玩一会儿电脑',
                    'duration_minutes': int(self.data.get('retry_duration') or 20),
                    'resume_task_id':self.data.get('retry_resume_task_id'), 'resume_guidance':self.data.get('retry_resume_guidance','')}

    def end(self, reason='manual'):
        with self.lock:
            self._expire()
            if self.data.get('status') == 'idle':
                return self.snapshot()
            self.data.update({'status': 'idle', 'request': {}, 'session': {},
                              'last': {'status': 'ended', 'time': self.clock(), 'reason': str(reason)[:100]},
                              'cooldown_until': 0,
                              'retry_at': 0, 'retry_plan': '', 'retry_duration': 20, 'retry_resume_task_id':None, 'retry_resume_guidance':''})
            self._save()
            return self.snapshot()

    def update_plan(self, plan, duration_minutes=None):
        if not isinstance(plan, str) or not plan.strip() or len(plan) > 500:
            raise PhonePlayError('电脑计划无效')
        with self.lock:
            self._expire()
            if self.data.get('status') == 'pending':
                self.data['request']['plan'] = plan.strip()
                self.data['request']['resume_task_id'] = None
                self.data['request']['resume_guidance'] = ''
                if duration_minutes is not None:
                    if type(duration_minutes) is not int or not MIN_MINUTES <= duration_minutes <= MAX_MINUTES:
                        raise PhonePlayError('使用时长无效')
                    self.data['request']['duration_minutes'] = duration_minutes
            elif self.data.get('status') == 'active':
                ses = self.data['session']
                ses['plan'] = plan.strip()
                ses['resume_task_id'] = None
                ses['resume_guidance'] = ''
                changes = ses.setdefault('changes', [])
                changes.append({'time': self.clock(), 'plan': plan.strip()})
                ses['changes'] = changes[-20:]
                if duration_minutes is not None:
                    if type(duration_minutes) is not int or not MIN_MINUTES <= duration_minutes <= MAX_MINUTES:
                        raise PhonePlayError('使用时长无效')
                    ses['planned_end'] = max(ses['planned_end'], self.clock() + 60)
            else:
                raise PhonePlayError('当前没有电脑请求或电脑时间')
            self._save()
            return self.snapshot()

    def set_cooldown(self, minutes=90):
        with self.lock:
            self.data['cooldown_until'] = self.clock() + max(1, int(minutes)) * 60
            self._save()
            return self.snapshot()

    def set_resume(self, goal):
        with self.lock:
            self.data['resume_goal'] = str(goal or '')[:600]
            self._save()

    def note_continue(self):
        """一次电脑时间内 bot 自主决定「继续玩」的次数（防无限自我续命）。"""
        with self.lock:
            ses = self.data.get('session') or {}
            ses['auto_continues'] = int(ses.get('auto_continues') or 0) + 1
            self.data['session'] = ses
            self._save()
            return int(ses['auto_continues'])

    def continues_used(self):
        with self.lock:
            self._expire()
            ses = self.data.get('session') or {}
            return int(ses.get('auto_continues') or 0)

    def notifications_due(self):
        with self.lock:
            self._expire()
            req = self.data.get('request') or {}
            if self.data.get('status') == 'pending' and not req.get('notified'):
                return [dict(req)]
            return []

    def mark_notified(self, request_id):
        with self.lock:
            req = self.data.get('request') or {}
            if req.get('id') == request_id:
                req['notified'] = True
                self._save()


phone_play = PhonePlayStore(Path(os.getenv('PHONE_PLAY_FILE') or 'data/agent/phone_play.json'))
