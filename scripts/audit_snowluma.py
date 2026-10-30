"""Rebuild a plugin compatibility inventory from the installed SnowLuma action docs."""
import argparse
import ast
import json
from pathlib import Path

NOTES = {
    'nonebot_plugin_status': '标准消息和 poke 通知；保留原有权限。',
    'agent_control': '标准私聊、撤回和原始 reply ID；审批权限与支付额度保持不变。',
    'chat': '原始@/引用已修复；原生空间评论、按需会话补查、表情回应、带来源的文件读取。生日字段缺失不编造。',
    'datadetail': '标准群消息、文本/图片发送；不将历史查询结果重新注入事件统计。',
    'errorlog': '日志存储与标准文本回复，无实现专属 API。',
    'exit': '标准退群通知与群资料查询；不主动触发退群。',
    'limit': '禁言/踢人/申诉接口齐全；原始 reply ID 已修复，支持负消息 ID；写入只做模拟验证。',
    'live': 'B站数据源保留；使用标准群消息和群成员角色，不修改直播订阅。',
    'menu': '本地图片渲染和标准图片消息；新增群资源命令自动进入菜单。',
    'morninginfo': '外部资讯源与合成逻辑保留；标准群消息和管理员权限。',
    'notice': '标准禁言/群荣誉/管理员通知；不改变通知名单。实际通知需对应事件验证。',
    'osu': '当前 __init__.py 为空，仅插件占位；没有可迁移业务或可宣称已实现的 osu 功能。',
    'pigimage': '用户画像与本地图片渲染保留；通过标准图片消息返回。',
    'poster': 'Telegram 投稿插件，QQ 实现变更不适用；继续保持停用状态，不改业务。',
    'nonebot_plugin_apscheduler': '定时任务依赖，不直接调用 QQ API；保持已有时区、任务和开关。',
    'weather': '天气外部数据源保留；标准文本/图片消息。',
    'welcome': '标准入群通知、成员信息与图片消息；未制造入群事件。',
    'qq_tools': '新增只读群文件/容量/公告/荣誉/待办/精华/最近聊天查询，限定当前群；面板所有者可查询已加入群。',
}


def audit(root, schema):
    docs = json.loads(Path(schema).read_text())['actions']
    actions = {row['name']: row for row in docs}
    for row in docs:
        for alias in row.get('aliases', []):
            actions[alias] = row
    plugins = []
    for folder in ('src/sys_plugins', 'src/other_plugins', 'src/yuna_plugins'):
        for plugin in sorted((root / folder).iterdir()):
            if not plugin.is_dir() or not (plugin / '__init__.py').exists():
                continue
            uses = set()
            if plugin.name != 'poster':
                for path in plugin.rglob('*.py'):
                    tree = ast.parse(path.read_text())
                    for node in ast.walk(tree):
                        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                            continue
                        if isinstance(node.func.value, ast.Name) and node.func.value.id == 'bot':
                            name = node.func.attr
                            if name == 'call_api' and node.args and isinstance(node.args[0], ast.Constant):
                                name = str(node.args[0].value)
                            if name in actions:
                                uses.add(name)
                            elif name in ('send', 'send_group_msg', 'send_private_msg'):
                                uses.add('send_msg')
                            elif name not in ('call_api', 'isdigit'):
                                raise ValueError(f'{path}:{node.lineno}: unknown QQ action {name}')
            if plugin.name == 'chat':
                uses.update({'get_qzone_msg_list', 'get_qzone_msg_detail', 'comment_qzone', 'get_version_info', 'get_group_msg_history', 'get_friend_msg_history', 'get_group_files_by_folder', 'get_group_file_url', 'get_group_root_files', 'set_msg_emoji_like'})
            elif plugin.name == 'qq_tools':
                uses.update({'get_group_msg_history', 'get_group_root_files', 'get_group_todo_list', 'get_essence_msg_list', 'get_version_info', '_get_group_notice', 'get_group_file_system_info', 'get_group_honor_info'})
            if plugin.name != 'poster' and (plugin / '__init__.py').stat().st_size:
                uses.add('send_msg')
            plugins.append({'plugin': plugin.name, 'actions': sorted(uses),
                'result': 'QQ不适用' if plugin.name == 'poster' else ('占位，无业务代码' if not (plugin / '__init__.py').stat().st_size else '接口定义已核对；业务实测见说明'),
                'notes': NOTES.get(plugin.name, '待人工核对')})
    plugins.append({'plugin': 'nonebot_plugin_apscheduler', 'actions': [], 'result': '无 QQ 实现依赖', 'notes': NOTES['nonebot_plugin_apscheduler']})
    placeholders = [r['name'] for r in docs if any(k in json.dumps(r.get('returnsSchema', {}), ensure_ascii=False) for k in ('占位', '恒空', '恒为'))]
    return {'schema_source': 'SnowLuma 2.2.0-node /api/debug/actions (2026-10-26)',
            'action_count': len(docs), 'plugins': plugins, 'placeholders': sorted(placeholders),
            'verification': '只读接口实测单独记录；接口定义检查不等于全部业务端到端验收。'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--schema', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    data = audit(Path(__file__).resolve().parents[1], args.schema)
    Path(args.out).write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    print(f"{len(data['plugins'])} plugins, {data['action_count']} action definitions")
