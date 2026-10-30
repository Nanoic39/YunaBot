"""任务结果的自然语言回报；不把执行器汇报包装成平台核验。"""
def task_notice(task):
    goal=str(task.get('goal') or '刚才的任务')[:100]
    summary=str(task.get('summary') or task.get('error') or '没有更多结果')[:300]
    if task.get('verification')=='unknown':
        return f'刚才「{goal}」的提交结果还没核实。我先停在这里，没有直接重发。\n{summary}'
    if task.get('status')=='done':
        label='已经完成，也核验到了实际结果' if task.get('verification')=='platform_verified' else '这轮操作已结束，下面是本轮观察和汇报'
        return f'「{goal}」{label}：\n{summary}'
    if task.get('status')=='cancelled' and task.get('verification')=='platform_verified':
        return f'「{goal}」已停止后续步骤。此前提交已经核验完成，不会重复提交。'
    if task.get('status')=='cancelled':return f'「{goal}」已经停下了。'
    return f'「{goal}」还没做完，停在这里了：\n{summary}'
