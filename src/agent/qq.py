"""私聊审批命令；不调用语言模型，任务正文不能授予权限。"""
import re

from .control import AgentControl, ControlError

COMMAND = re.compile(r'^(自主任务|查看任务|批准任务|批准单笔|批准每日|批准每月|拒绝任务)(?:\s+([a-f0-9]{32}))?$')


def approval_command(store: AgentControl, text: str, qq: str, superusers: set[str], private: bool) -> str | None:
    match = COMMAND.fullmatch(text.strip())
    if not match:
        return None
    if not private:
        return '请在私聊中处理自主任务。'
    if qq not in superusers:
        return '只有配置中的超管可以审批自主任务。'
    action, tid = match.groups()
    if action == '自主任务':
        tasks = [t for t in store.snapshot()['tasks'] if t['state'] == 'pending'][:10]
        return '\n\n'.join(task_notice(t) for t in tasks) or '没有待审批任务。当前执行器仅支持模拟。'
    if not tid:
        return '请提供完整任务 ID，例如：查看任务 <任务ID>。'
    try:
        if action == '查看任务':
            with store._db() as db:
                row = db.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
                if not row:
                    raise ControlError('任务不存在')
                task = store._task(row, db)
            return task_notice(task) + f"\n状态：{task['state']}"
        actions = {'批准任务': 'approve', '批准单笔': 'approve-single', '批准每日': 'approve-daily', '批准每月': 'approve-monthly', '拒绝任务': 'cancel'}
        task = store.transition(tid, actions[action], f'qq:{qq}')
        return f"任务 {tid}：{task['state']}。仅变更模拟任务状态，未付款或下单。" + ('\n' + approval_summary(task) if task['required_approvals'] else '')
    except ControlError as error:
        return str(error)


def approval_summary(task):
    names = {'single': '单笔', 'daily': '每日', 'monthly': '每月', 'review': '常规'}
    required = task.get('required_approvals', [])
    summary = '仍需独立审批：' + '、'.join(names[d] for d in required) if required else '当前没有未完成的额度审批。'
    for r in task.get('requirements', []):
        if not r['approved'] and r['dimension'] != 'review':
            period='这笔' if r['dimension']=='single' else r['period']
            summary += f"\n{names[r['dimension']]}（{period}）：预计 {r['projected_minor']/100:.2f} / 限额 {r['limit_minor']/100:.2f} 元"
    return summary


def task_notice(task: dict) -> str:
    return (f"我想在{task['merchant']}为{task['recipient']}处理{task['purpose']}，"
            f"金额{task['amount_minor']/100:.2f}元。\n"
            + approval_summary(task) + "\n这次只是授权演练，不会实际付款。你可以直接回复我是否同意。")
