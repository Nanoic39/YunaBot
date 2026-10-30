'use strict';

import {
  api, badge, bytes, clear, compact, downloadCSV, el, emptyState, icon, num, panel,
  statCard, table, toast,
} from '../core.js';
import { barChart, donutChart } from '../charts.js';

export async function render(host, ctx) {
  let data;
  try {
    data = await api('chat/stats');
  } catch (error) {
    host.append(panel('统计总览', {}, emptyState('统计数据加载失败', error.message, 'warning')));
    return;
  }
  const favorTotal = (data.favor_buckets || []).reduce((sum, row) => sum + Number(row.count || 0), 0);
  const tokenTotal = (data.tokens || []).reduce((sum, row) => sum + Number(row.total_tokens || 0), 0);
  const interactionTotal = (data.interaction_top || []).reduce((sum, row) => sum + Number(row.interactions || 0), 0);
  const memoryItems = data.memories || [];

  host.append(el('div', { class: 'grid grid-4' },
    statCard({ label: '好感度覆盖', value: num(favorTotal), hint: '有档案的用户', icon: 'users', accent: true }),
    statCard({ label: 'Token 总量', value: compact(tokenTotal), hint: 'Top 15 用户合计', icon: 'chart' }),
    statCard({ label: '互动次数', value: compact(interactionTotal), hint: 'Top 15 用户合计', icon: 'activity' }),
    statCard({ label: '记忆规模', value: num(memoryItems.length), hint: '群聊 / 私聊会话', icon: 'memory' })));

  const favorRows = (data.favor_buckets || []).map((row) => ({ label: row.label, value: row.count }));
  const tokenRows = (data.tokens || []).map((row) => ({ label: row.nickname || row.qq, value: row.total_tokens, extra: `${row.calls || 0} 次调用` }));
  const interactionRows = (data.interaction_top || []).map((row) => ({ label: row.nickname || row.qq, value: row.interactions, extra: `好感 ${row.favorability}` }));

  const favorPanel = panel('好感度分布', { subtitle: '按区间统计用户数量' },
    favorRows.length ? barChart(favorRows, { height: 280, tone: 'primary' }) : emptyState('暂无好感度数据', '', 'heart'));
  const tokenPanel = panel('Token 用量 Top 15', {
    subtitle: '输入与输出合计',
    actions: el('button', { class: 'button ghost small', type: 'button', onclick: () => downloadCSV('yuna-tokens.csv', [
      { label: '昵称', key: 'nickname' }, { label: 'QQ', key: 'qq' }, { label: '调用', key: 'calls' }, { label: '输入', key: 'prompt_tokens' },
      { label: '输出', key: 'completion_tokens' }, { label: '总量', key: 'total_tokens' },
    ], data.tokens || []) }, icon('download', 14), '导出'),
  }, tokenRows.length ? barChart(tokenRows, { height: 280, tone: 'teal' }) : emptyState('暂无 Token 数据', '', 'chart'));
  const activityPanel = panel('活跃度', { subtitle: '有最近活跃记录的用户数' },
    el('div', { class: 'stat-grid', style: { gridTemplateColumns: 'repeat(3,1fr)' } },
      ...[['24h', '24 小时'], ['7d', '7 天'], ['30d', '30 天']].map(([key, label]) => el('div', { class: 'profile-stat' },
        el('span', { text: label }), el('strong', { text: num(data.active?.[key] || 0) })))));

  const tokenTable = panel('Token 明细', { subtitle: '按总量排序' },
    table([
      { label: '用户', key: 'nickname', render: (row) => el('span', null, el('strong', { text: row.nickname || '—' }), el('small', { class: 'mono muted', text: ` ${row.qq}`, style: { display: 'block', fontSize: '10px' } })) },
      { label: '调用', key: 'calls', render: (row) => num(row.calls) },
      { label: '输入', key: 'prompt_tokens', render: (row) => num(row.prompt_tokens) },
      { label: '输出', key: 'completion_tokens', render: (row) => num(row.completion_tokens) },
      { label: '总量', key: 'total_tokens', render: (row) => el('strong', { text: num(row.total_tokens) }) },
    ], data.tokens || [], { empty: '暂无 Token 数据' }));

  const interactionTable = panel('互动排行', { subtitle: 'Top 15' },
    table([
      { label: '昵称', key: 'nickname', render: (row) => row.nickname || '—' },
      { label: 'QQ', key: 'qq', className: 'mono' },
      { label: '互动', key: 'interactions', render: (row) => num(row.interactions) },
      { label: '好感度', key: 'favorability', render: (row) => Number(row.favorability || 0).toFixed(1) },
    ], data.interaction_top || [], { empty: '暂无互动数据' }));

  const memoryTable = panel('记忆规模', { subtitle: '待处理 / 情景 / 事实' },
    table([
      { label: '类型', key: 'kind', render: (row) => row.kind === 'group' ? badge('群聊', 'info') : badge('私聊', 'outline') },
      { label: '标识', key: 'key', render: (row) => row.kind === 'user' ? (row.nickname ? `${row.nickname}（${row.key}）` : row.key) : row.key },
      { label: '待处理', key: 'pending', render: (row) => num(row.pending) },
      { label: '情景', key: 'episodes', render: (row) => num(row.episodes) },
      { label: '事实', key: 'facts', render: (row) => num(row.facts) },
    ], memoryItems, { empty: '暂无记忆数据' }));

  const emoteRows = (data.emote_categories || []).map((row) => ({ label: row.label, value: row.count }));
  const emotePanel = panel('表情分类', { subtitle: '已学习并保存的表情标签' },
    emoteRows.length ? barChart(emoteRows, { height: 260, tone: 'amber' }) : emptyState('暂无表情分类', '', 'image'));

  host.append(el('div', { class: 'grid grid-2' }, favorPanel, tokenPanel),
    el('div', { class: 'grid grid-2' }, activityPanel, emotePanel),
    el('div', { class: 'grid grid-2' }, tokenTable, interactionTable),
    memoryTable);
}
