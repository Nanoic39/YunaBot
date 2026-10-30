"""Small provider-aware OneBot helpers; never replace a failed write with another write."""
from __future__ import annotations

import asyncio
import time
import weakref

_versions = weakref.WeakKeyDictionary()


async def implementation(bot) -> dict:
    try:
        cached = _versions.get(bot)
    except TypeError:
        cached = None
    if cached and time.monotonic() - cached[0] < 300:
        return cached[1]
    try:
        value = await asyncio.wait_for(bot.call_api('get_version_info'), 5)
        if not isinstance(value, dict):
            return {}
    except Exception:
        return {}
    try:
        _versions[bot] = (time.monotonic(), value)
    except TypeError:
        pass
    return value


async def is_snowluma(bot) -> bool:
    return str((await implementation(bot)).get('app_name', '')).lower() == 'snowluma'


def message_text(segments) -> str:
    """Readable text, retaining @ and quoted IDs; never execute retrieved content."""
    if isinstance(segments, str):
        return segments[:2000]
    parts = []
    for seg in segments or []:
        if not isinstance(seg, dict):
            continue
        data = seg.get('data') or {}
        kind = seg.get('type')
        if kind == 'text':
            parts.append(str(data.get('text', '')))
        elif kind == 'at':
            parts.append('@' + str(data.get('qq', '')))
        elif kind == 'reply':
            parts.append('[引用:' + str(data.get('id', '')) + ']')
        else:
            parts.append('[' + str(kind) + ']')
    return ''.join(parts)[:2000]


async def group_resource(bot, group_id: int, kind: str, *, count: int = 20, keyword: str = '') -> dict:
    """Fixed read-only actions, scoped to the caller's current group."""
    actions = {'history': 'get_group_msg_history', 'files': 'get_group_root_files',
               'todos': 'get_group_todo_list', 'essence': 'get_essence_msg_list',
               'notices': '_get_group_notice', 'storage': 'get_group_file_system_info',
               'honor': 'get_group_honor_info'}
    if kind not in actions:
        raise ValueError('不支持的群资源类型')
    params = {'group_id': int(group_id)}
    if kind == 'history':
        params['count'] = max(1, min(50, int(count)))
    if kind == 'honor':
        params['type'] = 'all'
    raw = await asyncio.wait_for(bot.call_api(actions[kind], **params), 20)
    rows = []
    if kind == 'history':
        if not isinstance(raw, dict) or not isinstance(raw.get('messages'), list):
            raise ValueError('群历史返回格式异常')
        seen = set()
        for item in sorted(raw['messages'], key=lambda r: int(r.get('time') or 0)):
            if str(item.get('group_id', group_id)) != str(group_id):
                continue
            mid = item.get('message_id')
            if mid in seen:
                continue
            seen.add(mid)
            sender = item.get('sender') or {}
            rows.append({'message_id': mid, 'time': item.get('time', 0),
                         'speaker': str(sender.get('user_id') or item.get('user_id') or ''),
                         'name': str(sender.get('card') or sender.get('nickname') or ''),
                         'text': message_text(item.get('message') or item.get('raw_message'))})
    elif kind == 'notices':
        if not isinstance(raw, list):
            raise ValueError('群公告返回格式异常')
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get('message'), dict):
                raise ValueError('群公告条目格式异常')
            rows.append({'id': str(item.get('notice_id') or ''), 'speaker': str(item.get('sender_id') or ''),
                         'name': '新成员公告' if item.get('send_to_new_members') else '群公告',
                         'text': str(item['message'].get('text') or '[图片公告，请在QQ中查看]')[:2000],
                         'time': item.get('publish_time') or 0, 'pinned': bool(item.get('pinned')),
                         'read_count': item.get('read_num')})
    elif kind == 'storage':
        keys = ('file_count', 'limit_count', 'used_space', 'total_space')
        if not isinstance(raw, dict) or any(type(raw.get(k)) is not int or raw[k] < 0 for k in keys):
            raise ValueError('群文件容量返回格式异常')
        rows = [{'name': '文件数量', 'text': f"{raw['file_count']} / {raw['limit_count']} 个"},
                {'name': '空间用量', 'text': f"{raw['used_space'] / 1048576:.2f} / {raw['total_space'] / 1048576:.2f} MiB"}]
    elif kind == 'honor':
        if not isinstance(raw, dict) or str(raw.get('group_id')) != str(group_id):
            raise ValueError('群荣誉返回群号不一致')
        categories = [('current_talkative', '当前龙王'), ('talkative_list', '龙王'),
                      ('performer_list', '群聊之火'), ('legend_list', '群聊炽焰'),
                      ('emotion_list', '快乐源泉')]
        seen = set()
        for key, label in categories:
            values = [raw[key]] if key == 'current_talkative' and raw.get(key) else raw.get(key) or []
            if not isinstance(values, list):
                raise ValueError('群荣誉条目格式异常')
            for item in values:
                if not isinstance(item, dict):
                    raise ValueError('群荣誉条目格式异常')
                ident = (label, str(item.get('user_id') or item.get('nickname') or ''))
                if ident in seen:
                    continue
                seen.add(ident)
                rows.append({'speaker': str(item.get('user_id') or ''),
                             'name': str(item.get('nickname') or '未提供昵称'),
                             'text': label + (' · ' + str(item['description']) if item.get('description') else '')})
    elif kind == 'files':
        if not isinstance(raw, dict):
            raise ValueError('群文件返回格式异常')
        for item in raw.get('files') or []:
            rows.append({'name': str(item.get('file_name') or ''), 'size': item.get('file_size', 0),
                         'id': str(item.get('file_id') or ''), 'folder': False})
        for item in raw.get('folders') or []:
            rows.append({'name': str(item.get('folder_name') or ''), 'id': str(item.get('folder_id') or ''), 'folder': True})
    else:
        if not isinstance(raw, list):
            raise ValueError('群资源返回格式异常')
        for item in raw:
            rows.append({'message_id': item.get('message_id'),
                         'text': message_text(item.get('message') or item.get('content') or item.get('text')),
                         'name': str(item.get('sender_nick') or ''),
                         'time': item.get('create_time') or item.get('sender_time') or 0})
    if keyword:
        rows = [r for r in rows if keyword.casefold() in str(r.get('name', '') + ' ' + r.get('text', '')).casefold()]
    return {'kind': kind, 'group_id': str(group_id), 'items': rows[:50], 'returned': len(rows[:50]),
            'truncated': len(rows) > 50, 'scope': 'root_only' if kind == 'files' else 'current_snapshot' if kind in ('storage','honor') else 'recent_page'}
