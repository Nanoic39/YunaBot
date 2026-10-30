"""支付结果与强制通知。记录结果和创建通知原子提交；节点不能写支付结果。"""
import json
import uuid
from datetime import datetime

from .control import ControlError, TZ, integer

PAYMENT_CAPABILITIES = {'payment.pay', 'payment.recharge', 'shopping.gift'}


def is_payment(task):
    return task.get('capability') in PAYMENT_CAPABILITIES


class Payments:
    def __init__(self, control):
        self.control = control

    def _record(self, db, row, *, simulation, provider, order_id, transaction_id, details):
        task = json.loads(row['body'])
        old = db.execute('SELECT * FROM payment_records WHERE task_id=?', (row['id'],)).fetchone()
        if old:
            if old['provider_order_id'] != order_id or old['transaction_id'] != transaction_id:
                raise ControlError('该任务已有不同的支付结果，禁止覆盖')
            return old['id']
        if db.execute('SELECT 1 FROM payment_records WHERE provider=? AND provider_order_id=?', (provider, order_id)).fetchone():
            raise ControlError('支付订单已关联其他任务')
        pid = uuid.uuid4().hex
        db.execute('INSERT INTO payment_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                   (pid, row['id'], int(simulation), provider, order_id, transaction_id,
                    task['amount_minor'], task['currency'], task['description'],
                    json.dumps(details, ensure_ascii=False), self.control.clock(), 'simulated' if simulation else 'paid'))
        self.control._event(db, 'payment-adapter' if not simulation else 'simulator',
                            'payment.record', row['id'], pid)
        return pid

    def simulation_db(self, db, row):
        task = json.loads(row['body'])
        if is_payment(task):
            return self._record(db, row, simulation=True, provider='simulation',
                                order_id='sim-' + row['id'], transaction_id='',
                                details={'note': '授权演练，无实际付款'})

    def settle_verified(self, tid, provider, order_id, transaction_id, paid_minor, currency):
        """仅供后续可信支付适配器在查询/验签通过后调用；不对HTTP/LLM/worker开放。"""
        integer(paid_minor, 'paid_minor')
        if provider != 'alipay' or not all(isinstance(x, str) and 1 <= len(x) <= 160 for x in (order_id, transaction_id)):
            raise ControlError('支付渠道或订单标识无效')
        with self.control._tx() as db:
            row = db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
            if not row:
                raise ControlError('任务不存在')
            task = json.loads(row['body'])
            if not is_payment(task) or task['mode'] != 'live' or row['state'] not in ('running', 'unknown', 'succeeded'):
                raise ControlError('任务不是可结算的真实支付')
            if paid_minor != task['amount_minor'] or currency != task['currency']:
                raise ControlError('支付结果与授权金额/币种不一致')
            pid = self._record(db, row, simulation=False, provider=provider, order_id=order_id,
                               transaction_id=transaction_id, details={'verified': True})
            if row['state'] == 'succeeded':
                return {'payment_id': pid, 'duplicate': True}
            db.execute("UPDATE tasks SET state='succeeded', authorized=?, result=? WHERE id=?",
                       (self.control.clock(), json.dumps({'payment_id': pid, 'verified': True}), tid))
            return {'payment_id': pid}

    def history(self):
        with self.control._db() as db:
            return [dict(r) for r in db.execute('SELECT id,task_id,simulation,provider,amount_minor,currency,summary,created,status FROM payment_records ORDER BY created DESC,rowid DESC LIMIT 100')]

    def detail(self, pid):
        with self.control._db() as db:
            row = db.execute('SELECT * FROM payment_records WHERE id=?', (pid,)).fetchone()
            if not row:
                raise ControlError('支付记录不存在')
            task = db.execute('SELECT * FROM tasks WHERE id=?', (row['task_id'],)).fetchone()
            deliveries = [dict(r) for r in db.execute('SELECT recipient,attempts,next_retry,sent_at FROM payment_deliveries WHERE payment_id=?', (pid,))]
            return {**dict(row), 'details': json.loads(row['details']), 'task': self.control._task(task, db),
                    'deliveries': deliveries}

    def pending(self, recipient):
        with self.control._db() as db:
            rows = db.execute('''SELECT p.* FROM payment_records p
                LEFT JOIN payment_deliveries d ON d.payment_id=p.id AND d.recipient=?
                WHERE d.sent_at IS NULL AND (d.next_retry IS NULL OR d.next_retry<=?)
                ORDER BY p.created,p.rowid LIMIT 5''', (recipient, self.control.clock())).fetchall()
            result = []
            for row in rows:
                task = json.loads(db.execute('SELECT body FROM tasks WHERE id=?', (row['task_id'],)).fetchone()[0])
                result.append({**dict(row), 'task': task})
            return result

    def delivered(self, pid, recipient, success):
        with self.control._tx() as db:
            old = db.execute('SELECT attempts,sent_at FROM payment_deliveries WHERE payment_id=? AND recipient=?', (pid, recipient)).fetchone()
            if old and old['sent_at'] is not None:
                return
            attempt = (old['attempts'] if old else 0) + 1
            now = self.control.clock()
            db.execute('INSERT OR REPLACE INTO payment_deliveries VALUES (?,?,?,?,?)',
                       (pid, recipient, attempt, now + min(3600, 30 * 2 ** min(attempt, 7)), now if success else None))
            self.control._event(db, 'system', 'payment.notified' if success else 'payment.notify_retry', detail=pid)


def payment_notice(payment):
    task=payment['task']
    when=datetime.fromtimestamp(payment['created'],TZ).strftime('%m月%d日 %H:%M:%S')
    result='刚才的支付演练已经完成，未实际付款' if payment['simulation'] else '刚才已经支付成功'
    message=(f"{result}，金额{payment['currency']} {payment['amount_minor']/100:.2f}。\n"
             f"是在{task['merchant']}为{task['recipient']}处理{task['purpose']}，具体内容：{payment['summary']}。\n"
             f"完成时间是{when}，渠道是{payment['provider']}。")
    if not payment['simulation']:
        message+=f"\n平台订单号：{payment['provider_order_id']}；交易号：{payment['transaction_id']}。"
    return message+'\n详细记录也留在后台支付管理里了。'
