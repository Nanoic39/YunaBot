"""SnowLuma native Qzone mapping to the chat plugin's existing comment contract."""
from __future__ import annotations

import hashlib
import html
import re


def clean(value, limit=240):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', '', str(value or '')))).strip()[:limit]


def comment_item(tid, item, *, index=0, parent_id='', parent_uin=''):
    cid = str(item.get('comment_id') if item.get('comment_id') is not None else '')
    prefix = f'{tid}:{parent_id}' if parent_id != '' else tid
    text = clean(item.get('content'), 180)
    if not text:
        return None
    identity = cid if cid != '' else 'sha1:' + hashlib.sha1(
        f"{prefix}|{item.get('uin')}|{text}".encode()).hexdigest()[:20]
    return {'key': f'{prefix}:{identity}', 'index': index, 'comment_id': cid,
            'parent_comment_id': str(parent_id), 'parent_uin': str(parent_uin),
            'uin': str(item.get('uin') or ''), 'name': clean(item.get('name'), 40),
            'content': text, 'created': int(item.get('time') or 0), 'source': 'snowluma'}


async def fetch_posts(bot, num=3):
    owner = str(bot.self_id)
    data = await bot.call_api('get_qzone_msg_list', num=max(1, min(10, int(num))), pos=0)
    if not isinstance(data, dict) or not isinstance(data.get('msglist'), list):
        raise ValueError('空间列表返回格式异常')
    posts = []
    for post in data['msglist']:
        tid = str(post.get('tid') or '')
        if not tid:
            continue
        top_comments = []
        seen_ids = set()
        reported_total = 0
        position = 0
        for _ in range(5):
            detail = await bot.call_api('get_qzone_msg_detail', tid=tid, target_uin=int(owner),
                                       pos=position, num=100-len(top_comments))
            if not isinstance(detail, dict) or not isinstance(detail.get('comments'), list):
                raise ValueError('空间评论返回格式异常')
            page = detail['comments']
            reported_total = max(reported_total, int(detail.get('comment_num') or 0))
            fresh = []
            for row in page:
                identity = str(row.get('comment_id')) if row.get('comment_id') is not None else repr((row.get('uin'), row.get('time'), row.get('content')))
                if identity not in seen_ids:
                    seen_ids.add(identity)
                    fresh.append(row)
            top_comments.extend(fresh[:100-len(top_comments)])
            position += len(page)
            if not fresh or len(top_comments) >= 100 or position >= reported_total:
                break
        comments = []
        for index, raw in enumerate(top_comments, 1):
            item = comment_item(tid, raw, index=index)
            if item:
                comments.append(item)
            # Inline replies are not guaranteed complete by SnowLuma. Never claim completeness.
            for reply in raw.get('replies') or []:
                parent_id = reply.get('parent_comment_id', raw.get('comment_id'))
                parent_uin = reply.get('parent_comment_uin', raw.get('uin'))
                item = comment_item(tid, reply, index=index,
                    parent_id=str(parent_id) if parent_id is not None else '',
                    parent_uin=str(parent_uin) if parent_uin is not None else '')
                if item:
                    comments.append(item)
        posts.append({'tid': tid, 'uin': owner, 'name': '', 'content': clean(post.get('content')),
                      'created': int(post.get('time') or 0), 'comments': comments,
                      'comments_partial': reported_total > len(top_comments),
                      'inline_replies_complete': False})
    return {'ok': True, 'uin': owner, 'posts': posts, 'source': 'snowluma'}


async def reply_comment(bot, post_tid, post_uin, comment, content):
    parent = comment.get('parent_comment_id')
    nested = parent is not None and str(parent) != ''
    cid = str(parent if nested else comment.get('comment_id') if comment.get('comment_id') is not None else '')
    author = str(comment.get('parent_uin') if nested else comment.get('uin') or '')
    if cid == '' or not author.isdigit():
        return {'ok': False, 'error': '缺少有效的父评论 ID 或作者，未发送回复'}
    params = {'tid': str(post_tid), 'target_uin': int(post_uin or bot.self_id),
              'content': content, 'comment_id': cid, 'comment_uin': int(author)}
    if nested:
        target = str(comment.get('uin') or '')
        name = str(comment.get('name') or '')
        if not target.isdigit() or not name:
            return {'ok': False, 'error': '缺少楼中楼回复对象或昵称，未发送回复'}
        params.update(reply_uin=int(target), reply_name=name)
    # No retry or HTTP fallback: a timeout may have happened after the write succeeded.
    result = await bot.call_api('comment_qzone', **params)
    return {'ok': True, 'content': content, 'comment_id': str((result or {}).get('comment_id') or ''),
            'parent_comment_id': cid, 'source': 'snowluma'}
