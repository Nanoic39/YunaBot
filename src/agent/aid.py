"""一次性 owner 协助请求：验证码、手机号、APK、超额等。

秘密只保存在进程内存，消费即删除；不写数据库、日志、提示词或审计正文。
"""
from __future__ import annotations

import re
import secrets
import threading
import time
import json
from pathlib import Path

KINDS = {
    'sms': '短信验证码', 'phone': '手机号', 'apk': 'APK / 安装包',
    'payment': '支付/超额确认', 'tool': '工具能力确认', 'manual': '人工协助',
    'password': '密码（一次性输入）', 'registration': '注册与条款确认',
    'authorization': '操作授权', 'publish': '发布内容审核',
}
MAX_TTL = 1800


class AidError(ValueError):
    pass


class AidStore:
    def __init__(self, clock=time.time, path=None):
        self.clock = clock
        self.lock = threading.RLock()
        self.items: dict[str, dict] = {}
        self.path=Path(path) if path else None
        if self.path:
            try:
                for item in json.loads(self.path.read_text()):
                    if item['expires']<=self.clock() or item.get('context','').startswith('phone:'):continue
                    item['notified']=set(item.get('notified',[]))
                    self.items[item['id']]=item
            except (OSError,ValueError,KeyError,TypeError):pass

    def _save(self):
        if not self.path:return
        rows=[]
        for item in self.items.values():
            if item['kind'] in ('sms','phone','password') or item.get('context','').startswith('phone:'):continue
            row={k:v for k,v in item.items() if k!='image'}
            row['notified']=list(row['notified']);rows.append(row)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.path.with_suffix('.tmp');temporary.write_text(json.dumps(rows,ensure_ascii=False))
        temporary.chmod(0o600);temporary.replace(self.path)

    def metadata(self):
        with self.lock:
            self._expire()
            return [self._meta(i) for i in self.items.values()]

    def _meta(self, item):
        return {k: item[k] for k in ('id', 'kind', 'title', 'detail', 'created', 'expires', 'status', 'answered_by', 'context')}

    def create(self, kind, title, detail='', ttl=300, context=''):
        if kind not in KINDS:
            raise AidError('未知协助类型')
        if not isinstance(title, str) or not title.strip() or len(title) > 120:
            raise AidError('协助标题无效')
        if not isinstance(detail, str) or len(detail) > 500:
            raise AidError('协助说明过长')
        if not isinstance(context,str) or len(context)>160:raise AidError('请求上下文无效')
        limit=86400 if kind=='manual' and context=='social-setup' else MAX_TTL
        if type(ttl) is not int or not 30 <= ttl <= limit:
            raise AidError(f'协助有效期应为30到{limit}秒')
        with self.lock:
            self._expire()
            if len(self.items) >= 3:
                raise AidError('当前已有未完成的协助请求')
            aid = secrets.token_hex(6)
            self.items[aid] = {'id': aid, 'kind': kind, 'title': title.strip(), 'detail': detail,
                               'created': self.clock(), 'expires': self.clock() + ttl,
                               'status': 'pending', 'value': '', 'notified': set(), 'answered_by': '', 'context':context}
            self._save()
            return self._meta(self.items[aid])

    def _expire(self):
        now = self.clock()
        for aid in list(self.items):
            if self.items[aid]['expires'] <= now:
                self.items.pop(aid, None)
                self._save()

    def pending(self):
        with self.lock:
            self._expire()
            return [self._meta(item) for item in self.items.values() if item['status'] == 'pending']

    def notifications_due(self, recipient="legacy"):
        with self.lock:
            self._expire()
            return [self._meta(item) for item in self.items.values()
                    if item['status'] == 'pending' and recipient not in item['notified']]

    def attach(self, aid, image):
        if not isinstance(image, bytes) or len(image)>2*1024*1024 or (image and not image.startswith(b'\x89PNG')):
            raise AidError('协助截图无效')
        with self.lock:
            if aid in self.items:self.items[aid]['image']=image

    def image(self, aid):
        with self.lock:
            self._expire()
            return self.items.get(aid,{}).get('image',b'')

    def mark_notified(self, aid, recipient="legacy"):
        with self.lock:
            item = self.items.get(aid)
            if item:
                item['notified'].add(recipient)
                self._save()

    def get(self, aid):
        with self.lock:
            self._expire()
            item = self.items.get(aid)
            return self._meta(item) if item else None

    def respond(self, aid, value, actor='owner'):
        if not isinstance(value, str):
            raise AidError('协助内容无效')
        value = value.strip()
        with self.lock:
            self._expire()
            item = self.items.get(aid)
            if not item:
                raise AidError('协助请求不存在或已过期')
            if item['status'] != 'pending':
                raise AidError('协助请求已经处理')
            kind = item['kind']
            if kind == 'sms':
                if not re.fullmatch(r'\d{4,8}', value):
                    raise AidError('验证码应为4到8位数字')
            elif kind == 'phone':
                if not re.fullmatch(r'\+?\d{6,20}', value):
                    raise AidError('手机号格式无效')
            elif kind == 'apk':
                if not re.fullmatch(r'/YunaGames/Android/apks/[^\x00]{1,200}\.apk', value):
                    raise AidError('APK 必须位于 /YunaGames/Android/apks 下')
            elif kind in ('payment', 'registration', 'authorization', 'publish', 'tool'):
                value = confirmation(value)
                if value not in ('approve', 'deny'):
                    raise AidError('请明确回复同意或拒绝（approve / deny）')
            elif kind == 'password':
                if not value or len(value) > 200:
                    raise AidError('密码长度无效')
            elif kind == 'manual':
                if not value or len(value) > 500:
                    raise AidError('人工协助内容无效')
            item['value'] = value
            item['status'] = 'answered'
            item['answered_by'] = str(actor)[:160]
            self._save()
            return self._meta(item)

    def take(self, aid):
        """内部执行器消费一次；消费后立即从内存删除。"""
        with self.lock:
            self._expire()
            item = self.items.get(aid)
            if not item:
                raise AidError('协助请求不存在或已过期')
            if item['status'] != 'answered':
                raise AidError('协助请求尚未填写')
            value = item['value']
            self.items.pop(aid, None)
            self._save()
            return value

    def cancel(self, aid):
        with self.lock:
            item = self.items.get(aid)
            if not item:
                raise AidError('协助请求不存在或已过期')
            self.items.pop(aid, None)
            self._save()


def confirmation(value):
    # 不从一句任意聊天中搜索“好/行”，避免把询问、否定或条件句当授权。
    value = value.strip().lower().rstrip('。！!')
    if value in ('approve', '同意', '批准', '可以', '确认', '同意注册', '同意授权', '同意发布', '确认发布'):
        return 'approve'
    if value in ('deny', '拒绝', '不同意', '不批准', '不可以', '取消', '停止'):
        return 'deny'
    return value


def natural_aid_reply(store, text):
    """只有一个协助请求且答复类型明确时才自动关联；其他消息仍走聊天。"""
    pending = store.pending()
    if len(pending) != 1:
        return None
    item = pending[0]
    value = text.strip()
    kind = item['kind']
    if kind == 'sms' and re.fullmatch(r'\d{4,8}', value):
        return item['id'], value
    if kind == 'phone' and re.fullmatch(r'\+?\d{6,20}', value):
        return item['id'], value
    if kind in ('registration', 'authorization', 'publish', 'payment', 'tool') and confirmation(value) in ('approve', 'deny'):
        return item['id'], confirmation(value)
    return None


aid_store = AidStore(path=Path(__file__).resolve().parents[2]/'data/agent/aid_requests.json')
