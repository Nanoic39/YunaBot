'use strict';

import {
  api, badge, busy, bytes, clamp, clear, closeModal, compact, confirmDialog, downloadCSV, el,
  emptyState, errorText, field, icon, iconButton, loading, markdownLite, meter,
  modal, notice, num, openLightbox, pageHead, pageSlice, pagerControls, panel, searchField, segments, stamp,
  stampShort, statCard, statusBadge, table, timeAgo, toast,
} from '../core.js';
import { barChart, donutChart, radarChart, svg } from '../charts.js';

const STAGES = [
  { min: 85, max: 100, label: '依赖·羁绊', tone: 'accent', cap: 10, birthday: true },
  { min: 75, max: 84, label: '亲近·偏爱', tone: 'accent', cap: 9, birthday: true },
  { min: 60, max: 74, label: '亲近·在意', tone: 'ok', cap: 8, birthday: true },
  { min: 45, max: 59, label: '熟人·默契', tone: 'info', cap: 6, birthday: true },
  { min: 30, max: 44, label: '熟人·轻松', tone: 'info', cap: 5, birthday: true },
  { min: 20, max: 29, label: '眼熟·常聊', tone: '', cap: 4, birthday: true },
  { min: 10, max: 19, label: '眼熟·点头', tone: '', cap: 3, birthday: true },
  { min: 0, max: 9, label: '生疏', tone: 'outline', cap: 1, birthday: false },
  { min: -10, max: -1, label: '生疏·戒备', tone: 'outline', cap: 1, birthday: false },
  { min: -30, max: -11, label: '冷淡', tone: 'warn', cap: 1, birthday: false },
  { min: -60, max: -31, label: '反感', tone: 'warn', cap: 0, birthday: false },
  { min: -100, max: -61, label: '厌恶', tone: 'danger', cap: 0, birthday: false },
  { min: -Infinity, max: -101, label: '仇视', tone: 'danger', cap: 0, birthday: false },
];

function stageFor(fav, interactions = 0, brotherQq = '', qq = '') {
  if (brotherQq && String(qq) === String(brotherQq)) return { label: '哥哥', tone: 'accent' };
  const value = Number(fav) || 0;
  return STAGES.find((stage) => value >= stage.min) || STAGES[STAGES.length - 1];
}

function stageBadge(fav, interactions, brotherQq, qq) {
  const stage = stageFor(fav, interactions, brotherQq, qq);
  return badge(stage.label, stage.tone);
}

function moodMeter(label, value, percent, words, tone) {
  return el('div', { class: 'mood-meter' },
    el('div', { class: 'mood-meter-label' },
      el('span', { text: `${label} · ${words}` }),
      el('strong', { text: `${Math.round(percent)}%` })),
    el('div', { class: 'meter' }, el('div', { class: `meter-fill ${tone}`, style: { width: `${Math.max(1, Math.min(100, percent))}%` } })));
}

function humanMoodPanel(human, ctx, compactMode = false) {
  const mood = human?.mood || {};
  const valence = Number(mood.valence) || 0;
  const energy = Number(mood.energy) || 0;
  const irritation = Number(mood.irritation) || 0;
  const loneliness = Number(mood.loneliness) || 0;
  const label = human?.label || '平静';
  const explain = human?.mood_explain || {};
  const orb = el('div', { class: 'mood-orb', text: label.slice(0, 1) });
  const headBits = el('div', { class: 'profile-badges' },
    badge(human?.night ? '深夜状态' : '日常状态', human?.night ? 'warn' : 'outline'),
    human?.activity ? badge(human.activity, 'info') : null,
    human?.energy_label ? badge(`精力 · ${human.energy_label}`, 'outline') : null);
  return el('div', { class: 'mood-compact' },
    el('div', { class: 'mood-title', style: { gap: '10px' } },
      orb,
      el('div', null,
        el('h2', { text: label, style: { margin: 0, fontSize: compactMode ? '18px' : '20px' } }),
        el('p', { style: { margin: '2px 0 0', fontSize: '11px', color: 'var(--muted)' }, text: human?.stale ? '还没有互动记录' : `更新于 ${timeAgo(mood.updated)}` }),
        headBits)),
    el('div', { class: 'mood-compact-meters' },
      moodMeter('心情', valence, (valence + 1) * 50, valence < -0.2 ? '低落' : valence > 0.3 ? '开心' : '平稳', 'teal'),
      moodMeter('精力', energy, energy * 100, energy < 0.35 ? '疲惫' : energy > 0.7 ? '充沛' : '正常', 'amber'),
      moodMeter('烦躁', irritation, irritation * 100, irritation > 0.45 ? '有点生气' : '平静', 'rose'),
      moodMeter('孤单', loneliness, loneliness * 100, loneliness > 0.5 ? '想被陪' : '还好', '')),
    explain.summary ? el('div', { class: 'mood-explain' },
      icon('info', 13),
      el('span', { text: explain.summary })) : null);
}

/* ==========================================================================
 * Chat overview
 * ========================================================================== */
async function chatOverview(host, ctx) {
  const [overview, human, cache, metrics] = await Promise.all([
    api('chat/overview'),
    api('chat/human').catch(() => null),
    api('media/cache').catch(() => null),
    api('chat/metrics?days=14').catch(() => null),
  ]);
  const stats = el('div', { class: 'stat-grid' },
    statCard({ label: '用户档案', value: num(overview.users), hint: 'users.json', icon: 'users', accent: true }),
    statCard({ label: '记忆会话', value: num(overview.memories), hint: '群聊 / 私聊', icon: 'memory' }),
    statCard({ label: '成长分桶', value: num(overview.growth_buckets), hint: '成长人格', icon: 'sprout' }),
    statCard({ label: '已学习表情', value: num(overview.learned), hint: `${num(overview.saved)} 条已收藏`, icon: 'smile' }),
    statCard({ label: '表情文件', value: num(overview.emotes), hint: 'emotes / misc_emoji', icon: 'image' }),
    statCard({ label: '累计 Token', value: compact(overview.tokens), hint: `${num(overview.calls)} 次调用`, icon: 'chart' }),
  );

  const moodPanel = el('section', { class: 'panel' },
    el('div', { class: 'panel-head' },
      el('div', { class: 'panel-title' }, icon('sparkles', 17), el('div', null, el('h2', { text: '此刻的她' }), el('p', { text: '心情与精力会自然影响语气和回复长度' }))),
      el('button', { class: 'button secondary small', type: 'button', onclick: () => ctx.navigate('human') }, icon('arrow-right', 14), '拟人状态')),
    human ? humanMoodPanel(human, ctx, true) : emptyState('还没有拟人状态', '等她说第一句话之后，这里会有心情记录。', 'sparkles'));

  const relationships = human?.relationships || [];
  const relationshipPanel = el('section', { class: 'panel' },
    el('div', { class: 'panel-head' },
      el('div', { class: 'panel-title' }, icon('heart', 17), el('div', null, el('h2', { text: '关系分布' }), el('p', { text: '按好感度与互动次数划分' }))),
      el('a', { class: 'chip', href: '#/favor' }, '好感度', icon('arrow-right', 12))),
    relationships.length
      ? el('div', { class: 'relationship-grid' }, ...relationships.map((row) => el('div', { class: 'relationship-item' },
        el('strong', { text: num(row.count) }), el('span', { text: row.label }))))
      : emptyState('还没有关系数据', '用户开始互动后会自动累计。', 'users'));

  const cachePanel = el('section', { class: 'panel' },
    el('div', { class: 'panel-head' },
      el('div', { class: 'panel-title' }, icon('cache', 17), el('div', null, el('h2', { text: '图片缓存' }), el('p', { text: '缩略图按需生成并复用' }))),
      el('a', { class: 'chip', href: '#/emotes' }, '表情仓库', icon('arrow-right', 12))),
    el('div', { class: 'metric-row' }, el('span', { text: '缩略图' }), el('strong', { text: cache ? `${num(cache.files)} 张` : '—' }), el('span')),
    el('div', { class: 'metric-row' }, el('span', { text: '占用空间' }), el('strong', { text: cache ? bytes(cache.bytes) : '—' }), el('span')),
    el('div', { class: 'metric-row' }, el('span', { text: '缓存命中' }), el('strong', { text: cache ? `${num(cache.hits)} / ${num(cache.hits + cache.misses)}` : '—' }), el('span')),
    el('div', { class: 'metric-row' }, el('span', { text: '原图总量' }), el('strong', { text: '按需读取' }), el('span')));

  const metricsPanel = (() => {
    const head = el('div', { class: 'panel-head' },
      el('div', { class: 'panel-title' }, icon('activity', 17), el('div', null, el('h2', { text: '运行指标' }), el('p', { text: '近 14 天 LLM 成功率、延迟、固化与表情命中' }))));
    if (!metrics || !metrics.llm) {
      return el('section', { class: 'panel' }, head, emptyState('暂无指标数据', '开始对话后会在这里累计延迟与成功率。', 'activity'));
    }
    const llm = metrics.llm || {};
    const consolidate = metrics.consolidate || {};
    const emote = metrics.emote || {};
    const rate = llm.success_rate;
    const emoteRequested = Number(emote.requested || 0);
    const consolidateCount = Number(consolidate.count || 0);
    const statBits = el('div', { class: 'ledger-stats' },
      el('div', { class: 'ledger-stat' }, el('small', { text: 'LLM 成功率' }),
        el('strong', { text: rate === null || rate === undefined ? '—' : `${(rate * 100).toFixed(1)}%` }),
        el('small', { class: 'muted', text: `${num(llm.calls || 0)} 次 · 失败 ${num(llm.fail || 0)}` })),
      el('div', { class: 'ledger-stat' }, el('small', { text: '延迟 p50 / p95' }),
        el('strong', { text: `${num(llm.p50_ms || 0)} / ${num(llm.p95_ms || 0)}ms` }),
        el('small', { class: 'muted', text: `平均 ${num(llm.avg_ms || 0)}ms` })),
      el('div', { class: 'ledger-stat' }, el('small', { text: '今日调用' }),
        el('strong', { text: num(llm.today_calls || 0) }),
        el('small', { class: 'muted', text: `失败 ${num(llm.today_fail || 0)} · 审查拒绝 ${num(llm.content_filter || 0)}` })),
      el('div', { class: 'ledger-stat' }, el('small', { text: 'Token 消耗' }),
        el('strong', { text: compact(llm.tokens || 0) }),
        el('small', { class: 'muted', text: '近 14 天合计' })),
      el('div', { class: 'ledger-stat' }, el('small', { text: '记忆固化' }),
        el('strong', { text: consolidateCount ? `${num(consolidateCount)} 次` : '—' }),
        el('small', { class: 'muted', text: consolidateCount ? `平均 ${num(consolidate.ms_avg || 0)}ms · 失败 ${num(consolidate.fail || 0)}` : '还没有固化任务' })));
    const emoteLine = emoteRequested
      ? el('p', { class: 'muted small', style: { margin: '0 0 10px' }, text: `表情命中：${num(emote.hit || 0)} / ${num(emoteRequested)}（${Math.round((Number(emote.hit || 0) / emoteRequested) * 100)}%），未命中表示模型想要的情绪目录里没有图。` })
      : null;
    const days = metrics.days || [];
    const chart = days.length
      ? barChart(days.map((row) => ({ label: row.date.slice(5), value: row.calls, fail: row.fail })), { height: 180, tone: 'primary', maxLabels: 14 })
      : emptyState('还没有每日数据', '第一天运行后会出现调用趋势。', 'chart');
    return el('section', { class: 'panel' }, head, statBits, emoteLine, chart);
  })();

  const links = [
    ['users', 'users', '用户画像', '搜索并查看某个人'],
    ['favor', 'heart', '好感度', '调整数值与关系'],
    ['growth', 'sprout', '成长人格', '她学会的说话方式'],
    ['whitelist', 'shield', '白名单', '控制触发范围'],
    ['sessions', 'sessions', '会话记忆', '浏览事实与近期对话'],
    ['emotes', 'image', '表情仓库', '分类、预览与上传'],
  ];
  const quick = el('section', { class: 'panel' },
    el('div', { class: 'panel-head' }, el('div', { class: 'panel-title' }, icon('bolt', 17), el('div', null, el('h2', { text: '聊天工作台' }), el('p', { text: '常用入口' })))),
    el('div', { class: 'quick-grid' }, ...links.map(([path, iconName, title, sub]) => el('button', {
      type: 'button', class: 'quick-action', onclick: () => ctx.navigate(path),
    }, icon(iconName, 17), el('div', null, el('strong', { text: title }), el('small', { text: sub }))))));

  host.append(stats, el('div', { class: 'grid grid-2' }, moodPanel, relationshipPanel),
    metricsPanel,
    el('div', { class: 'grid grid-2' }, cachePanel, quick));
}

/* ==========================================================================
 * Users
 * ========================================================================== */
async function usersPage(host, ctx) {
  const [rows, human] = await Promise.all([api('chat/favor'), api('chat/human').catch(() => null)]);
  const brother = human?.brother_qq || '';
  const state = ctx.state(() => ({ query: '', sort: 'favor', page: 1 }));
  const listHost = el('div');
  const count = el('span', { class: 'pill', text: `${rows.items.length} 位用户` });
  const searchSlot = el('div', { class: 'emote-search-slot' });
  const head = el('div', { class: 'page-head' },
    el('div', null, el('h2', { text: '用户画像' }), el('p', { text: '按 QQ 号区分人；画像与资料在这里，数值调整请去好感度页。' })),
    el('div', { class: 'page-actions' }, count,
      el('button', { class: 'button secondary small', type: 'button', onclick: () => downloadCSV('yuna-users.csv', [
        { label: '昵称', key: 'nickname' }, { label: 'QQ', key: 'qq' },
        { label: '标签', key: 'tags', csv: (row) => (row.tags || []).join('、') },
        { label: '生日', key: 'birthday' }, { label: '备注', key: 'notes' },
        { label: '互动', key: 'interactions' }, { label: '最近活跃', key: 'last_active', csv: (row) => stamp(row.last_active) },
      ], rows.items) }, icon('download', 14), '导出')));
  const toolbar = el('div', { class: 'toolbar between' }, searchSlot,
    el('select', { class: 'select', style: { maxWidth: '150px' }, onchange: (event) => { state.sort = event.target.value; renderList(); } },
      el('option', { value: 'favor' }, '按关系'),
      el('option', { value: 'interactions' }, '按互动次数'),
      el('option', { value: 'recent' }, '按最近活跃'),
      el('option', { value: 'name' }, '按昵称')));
  host.append(head, toolbar, listHost);

  searchSlot.append(searchField('搜索昵称、QQ 或标签…', (value) => { state.query = value; state.page = 1; renderList(); }, state.query));

  function renderList() {
    const q = state.query.toLowerCase();
    let items = rows.items.filter((row) => !q
      || `${row.nickname} ${row.qq} ${(row.tags || []).join(' ')} ${row.notes || ''}`.toLowerCase().includes(q));
    if (state.sort === 'favor') items.sort((a, b) => (Number(b.favorability) || 0) - (Number(a.favorability) || 0));
    if (state.sort === 'interactions') items.sort((a, b) => (Number(b.interactions) || 0) - (Number(a.interactions) || 0));
    if (state.sort === 'recent') items.sort((a, b) => (Number(b.last_active) || 0) - (Number(a.last_active) || 0));
    if (state.sort === 'name') items.sort((a, b) => String(a.nickname || '').localeCompare(String(b.nickname || ''), 'zh-CN'));
    clear(listHost);
    if (!items.length) {
      listHost.append(panel('用户列表', {}, emptyState('没有匹配的用户', '换一个昵称、QQ 或标签试试。', 'users')));
      return;
    }
    const page = pageSlice(items, state.page, 20);
    state.page = page.page;
    const rowsNodes = page.rows.map((row, index) => {
      const rank = (page.page - 1) * 20 + index + 1;
      const tags = row.tags || [];
      const profileBits = el('div', { class: 'profile-badges' },
        row.is_bot ? badge('机器人', 'info') : null,
        row.birthday ? badge(`生日 ${row.birthday}`, 'outline') : null,
        tags.slice(0, 3).map((tag) => badge(tag, '')));
      return el('tr', { style: { cursor: 'pointer' }, onclick: () => ctx.navigate(`users/${encodeURIComponent(row.qq)}`) },
        el('td', { class: 'num' }, el('span', { class: `rank${rank <= 3 && state.sort === 'favor' ? ' top' : ''}`, text: String(rank) })),
        el('td', null, el('div', { class: 'user-cell' },
          el('span', { class: 'avatar', text: String(row.nickname || row.qq).slice(0, 1) }),
          el('div', { class: 'user-cell-text' }, el('strong', { text: row.nickname || '—' }), el('small', { text: row.qq })))),
        el('td', null, stageBadge(row.favorability, row.interactions, brother, row.qq)),
        el('td', null, (row.is_bot || row.birthday || tags.length)
          ? profileBits
          : el('span', { class: 'muted small', text: '暂无画像标签' })),
        el('td', { text: num(row.interactions) }),
        el('td', { class: 'muted-cell', text: timeAgo(row.last_active) }),
        el('td', { class: 'actions' }, icon('arrow-right', 15)));
    });
    const tableNode = el('table', { class: 'data-table users-table' },
      el('thead', null, el('tr', null,
        el('th', { text: '#' }), el('th', { text: '用户' }), el('th', { text: '关系' }), el('th', { text: '画像' }),
        el('th', { text: '互动' }), el('th', { text: '最近活跃' }), el('th'))),
      el('tbody', null, ...rowsNodes));
    listHost.append(panel('用户列表', { subtitle: `共 ${items.length} 位` }, el('div', { class: 'table-wrap' }, tableNode),
      pagerControls({ page: page.page, pageCount: page.pageCount, total: page.total, onPage: (value) => { state.page = value; renderList(); }, unit: '位' })));
  }
  renderList();
}

/* ==========================================================================
 * User detail
 * ========================================================================== */
function hourBuckets(messages) {
  const counts = Array(24).fill(0);
  for (const message of messages || []) {
    const date = new Date((Number(message.time) || 0) * 1000);
    if (!Number.isNaN(date.getTime())) counts[date.getHours()] += 1;
  }
  return counts.map((value, hour) => ({ label: `${hour}时`, value })).filter((row) => row.value > 0);
}

async function userDetail(host, ctx) {
  const qq = ctx.params.qq;
  const [profile, human] = await Promise.all([
    api(`chat/profile/${encodeURIComponent(qq)}`),
    api('chat/human').catch(() => null),
  ]);
  const brother = human?.brother_qq || '';
  const isBrother = String(qq) === String(brother);
  const stage = stageFor(profile.favorability, profile.interactions, brother, qq);

  const back = el('button', { class: 'button ghost small', type: 'button', onclick: () => ctx.navigate('users') }, icon('arrow-left', 15), '返回用户列表');
  const refreshProfile = el('button', { class: 'button secondary small', type: 'button' }, icon('refresh', 14), '刷新资料卡');
  refreshProfile.addEventListener('click', async () => {
    busy(refreshProfile, true, '读取中…');
    try {
      await api(`chat/profile/${encodeURIComponent(qq)}/refresh`, { method: 'POST', timeout: 30000 });
      toast('资料卡已刷新', 'ok');
      ctx.rerender();
    } catch (error) { toast(errorText(error), 'error'); } finally { busy(refreshProfile, false); }
  });
  const likeProfile = el('button', { class: 'button secondary small', type: 'button' }, icon('heart', 14), '给资料卡点赞');
  likeProfile.addEventListener('click', async () => {
    busy(likeProfile, true, '点赞中…');
    try {
      const result = await api(`chat/profile/${encodeURIComponent(qq)}/like?times=10`, { method: 'POST', timeout: 30000 });
      if (result.ok) toast(`已点赞 ${result.times} 次（今日 ${result.used}/${result.cap}）`, 'ok');
      else toast(`今天已经点满啦（${result.used}/${result.cap}）`, 'warn');
    } catch (error) { toast(errorText(error), 'error'); } finally { busy(likeProfile, false); }
  });
  const actions = el('div', { class: 'page-actions' },
    refreshProfile, likeProfile,
    el('button', { class: 'button secondary small', type: 'button', onclick: () => openProfileEditor(profile, ctx) }, icon('edit', 14), '编辑资料'),
    el('button', { class: 'button secondary small', type: 'button', onclick: () => openFavorEditor(profile, host, ctx) }, icon('heart', 14), '调整好感度'),
    el('button', { class: 'button secondary small', type: 'button', onclick: () => ctx.navigate(`sessions/user/${encodeURIComponent(qq)}`) }, icon('sessions', 14), '查看记忆'));
  host.append(back, el('div', { class: 'page-head' },
    el('div', { class: 'profile-identity' },
      el('span', { class: 'avatar profile-avatar', text: String(profile.nickname || qq).slice(0, 1) }),
      el('div', null,
        el('h2', { text: profile.nickname || profile.bot_name || qq }),
        el('p', { text: `QQ ${qq} · 首次出现 ${stamp(profile.first_seen)} · 最近活跃 ${stamp(profile.last_active)}` }),
        el('div', { class: 'profile-badges' },
          stageBadge(profile.favorability, profile.interactions, brother, qq),
          profile.is_bot ? badge('机器人 / 其它 AI', 'info') : badge('真人', 'outline'),
          profile.self_alias ? badge(`希望被称呼为「${profile.self_alias}」`, 'accent') : null))),
    actions));

  const stats = el('div', { class: 'profile-stats' },
    ...[
      ['好感度', Number(profile.favorability || 0).toFixed(1)],
      ['互动次数', num(profile.interactions)],
      ['发言条数', num(profile.message_count)],
      ['被提及', num(profile.mention_count)],
      ['Token 总量', compact(profile.usage?.total_tokens || 0)],
      ['调用次数', num(profile.usage?.calls || 0)],
    ].map(([label, value]) => el('div', { class: 'profile-stat' }, el('span', { text: label }), el('strong', { text: value }))));

  const p = profile.profile || {};
  const profileRows = [
    ['性别', p.sex], ['年龄', p.age], ['签名', p.sign], ['QQ 等级', p.level],
    ['群名片', p.card], ['群角色', p.role], ['地区', p.region],
    ['生日', profile.birthday], ['今日点赞', profile.like_cap ? `${profile.like_today || 0} / ${profile.like_cap}` : ''],
    ['备注', profile.profile_notes], ['标签', (profile.profile_tags || []).join('、')],
  ].filter(([, value]) => value !== undefined && value !== null && String(value).trim() !== '');
  const profilePanel = panel('资料卡', {
    subtitle: p.updated ? `更新于 ${timeAgo(p.updated)} · 来源 ${p.source || '—'}` : '尚未获取公开资料',
  },
    profileRows.length
      ? el('div', { class: 'metric-row', style: { gridTemplateColumns: '90px minmax(0,1fr)' } }, ...profileRows.flatMap(([label, value]) => [
        el('span', { text: label }), el('strong', { class: 'break', text: String(value) }),
      ]).slice(0, 20))
      : emptyState('还没有资料卡', '点击「刷新资料卡」通过 OneBot 获取公开资料；好友生日字段只在好友列表同步时可见。', 'users'),
    profile.birthday ? notice(`生日：${profile.birthday}；到当天会在最近出现过的群发送祝福。`, 'info') : null);

  const tokenDonut = el('section', { class: 'panel chart-card' },
    el('div', { class: 'panel-head' }, el('div', { class: 'panel-title' }, icon('chart', 17), el('div', null, el('h2', { text: 'Token 构成' }), el('p', { text: '输入 / 输出占比' })))),
    profile.usage?.total_tokens
      ? donutChart([{ label: '输入', value: profile.usage.prompt_tokens || 0 }, { label: '输出', value: profile.usage.completion_tokens || 0 }], { centerLabel: '总 Token' })
      : emptyState('暂无 Token 数据', '她还没有为这个人调用模型。', 'chart'));

  const hourRows = hourBuckets(profile.recent_messages);
  const hourChart = el('section', { class: 'panel chart-card' },
    el('div', { class: 'panel-head' }, el('div', { class: 'panel-title' }, icon('clock', 17), el('div', null, el('h2', { text: '最近发言时段' }), el('p', { text: '按记忆中的消息时间统计' })))),
    hourRows.length ? barChart(hourRows, { height: 250, tone: 'primary', maxLabels: 24 }) : emptyState('暂无发言数据', '还没有足够的近期消息。', 'clock'));

  const analysis = el('section', { class: 'panel' },
    el('div', { class: 'panel-head' }, el('div', { class: 'panel-title' }, icon('activity', 17), el('div', null, el('h2', { text: '关系解读' }), el('p', { text: '基于现有数据生成，不额外调用模型' })))),
    el('ul', { class: 'analysis-list' },
      el('li', { text: `当前关系阶段：${stage.label}，好感度 ${Number(profile.favorability || 0).toFixed(1)}。` }),
      el('li', { text: `累计互动 ${num(profile.interactions)} 次，近期记忆里出现 ${num(profile.message_count)} 条发言。` }),
      el('li', { text: profile.usage?.calls ? `平均每次调用约 ${num(Math.round((profile.usage.total_tokens || 0) / profile.usage.calls))} Token。` : '还没有模型调用记录。' }),
      el('li', { text: profile.is_bot ? '该账号被标记为机器人或其它 AI，不会学习其说话风格。' : '该账号按真人处理，会进入长期记忆与画像。' }),
      profile.aliases && Object.keys(profile.aliases).length ? el('li', { text: `自定义称呼：${Object.entries(profile.aliases).map(([target, alias]) => `${target} → ${alias}`).join('、')}` }) : null,
      profile.mention_count ? el('li', { text: `在长期事实或事件中被提及 ${num(profile.mention_count)} 次。` }) : null));

  const msgPanel = el('section', { class: 'panel' },
    el('div', { class: 'panel-head' }, el('div', { class: 'panel-title' }, icon('message', 17), el('div', null, el('h2', { text: '最近发言' }), el('p', { text: `最近 ${Math.min(30, profile.recent_messages?.length || 0)} 条` })))),
    profile.recent_messages?.length
      ? el('div', { class: 'timeline' }, ...profile.recent_messages.slice().reverse().map((message) => el('div', { class: 'timeline-item' },
        el('span', { class: 'timeline-time', text: stampShort(message.time) }),
        el('span', { class: 'timeline-body', text: message.content || '（空消息）' }))))
      : emptyState('还没有近期发言', '这个人的消息还没有进入滚动记忆。', 'message'));

  const radarPanel = el('section', { class: 'panel chart-card' },
    el('div', { class: 'panel-head' }, el('div', { class: 'panel-title' }, icon('activity', 17), el('div', null, el('h2', { text: '画像雷达' }), el('p', { text: '好感 / 互动 / 发言 / 被提及 / 消耗（对数归一）' })))),
    radarChart([
      { label: '好感', value: clamp((Number(profile.favorability) || 0) + 30, 0, 130) / 1.3 },
      { label: '互动', value: Math.min(100, Math.log10(1 + (Number(profile.interactions) || 0)) * 40) },
      { label: '发言', value: Math.min(100, Math.log10(1 + (Number(profile.message_count) || 0)) * 33) },
      { label: '被提及', value: Math.min(100, Math.log10(1 + (Number(profile.mention_count) || 0)) * 33) },
      { label: '消耗', value: Math.min(100, Math.log10(1 + (Number(profile.usage?.total_tokens) || 0)) * 17) },
    ]));

  host.append(stats, profilePanel, el('div', { class: 'grid grid-2' }, tokenDonut, hourChart), el('div', { class: 'grid grid-2' }, radarPanel, analysis), msgPanel);
}

function openFavorEditor(profile, host, ctx) {
  const input = el('input', { class: 'input', type: 'number', step: '0.5', min: '-100000', max: '1000', value: String(profile.favorability ?? 0) });
  const body = el('div', null,
    field('好感度', input, { hint: '范围 0–100 为正常区间；负数表示厌恶。保存只改数值，不改互动与记忆。' }));
  modal({
    title: `调整 ${profile.nickname || profile.qq} 的好感度`,
    subtitle: `当前 ${Number(profile.favorability || 0).toFixed(1)}`,
    body,
    actions: [
      { label: '取消', className: 'secondary', onClick: (close) => close() },
      { label: '保存', className: 'primary', onClick: async (close) => {
        const value = Number(input.value);
        if (!Number.isFinite(value)) { toast('请输入有效数字', 'error'); return; }
        try {
          await api(`chat/favor/${encodeURIComponent(profile.qq)}`, { method: 'PUT', body: { favorability: value } });
          close();
          toast('好感度已更新', 'ok');
          ctx.rerender();
        } catch (error) { toast(errorText(error), 'error'); }
      } },
    ],
  });
}

function openProfileEditor(profile, ctx) {
  const p = profile.profile || {};
  const nickname = el('input', { class: 'input', value: profile.nickname || '', maxlength: '64' });
  const birthday = el('input', { class: 'input', value: profile.birthday || '', placeholder: 'MM-DD 或 YYYY-MM-DD' });
  const notes = el('textarea', { class: 'textarea', rows: '4', placeholder: '关于这个人的备注，会注入提示词（可选）' });
  notes.value = profile.profile_notes || '';
  const tags = el('input', { class: 'input', value: (profile.profile_tags || []).join('、'), placeholder: '用逗号分隔，例如：猫、前端、夜猫子' });
  const body = el('div', null,
    el('div', { class: 'form-grid' }, field('昵称', nickname), field('生日', birthday, { hint: '只填月日时按每年重复；可手动纠正资料卡读不到的生日' })),
    field('备注', notes),
    field('标签', tags));
  modal({
    title: `编辑 ${profile.nickname || profile.qq} 的资料`,
    subtitle: '手动资料只用于画像与提示词，不会修改 QQ 资料卡',
    body,
    actions: [
      { label: '取消', className: 'secondary', onClick: (close) => close() },
      { label: '保存', className: 'primary', onClick: async (close) => {
        try {
          await api(`chat/profile/${encodeURIComponent(profile.qq)}`, {
            method: 'PUT',
            body: {
              nickname: nickname.value.trim(),
              birthday: birthday.value.trim(),
              notes: notes.value.trim(),
              tags: tags.value.split(/[,，、\s]+/).map((tag) => tag.trim()).filter(Boolean),
            },
          });
          close();
          toast('资料已保存', 'ok');
          ctx.rerender();
        } catch (error) { toast(errorText(error), 'error'); }
      } },
    ],
  });
}

/* ==========================================================================
 * Favor
 * ========================================================================== */
async function favorPage(host, ctx) {
  const ledgerOnly = ctx.path === 'favor-ledger';
  let [rows, human, ledger] = await Promise.all([ledgerOnly ? null : api('chat/favor'), ledgerOnly ? null : api('chat/human'), ledgerOnly ? api('chat/favor/ledger?limit=200') : null]);
  rows ||= { items: [] };
  ledger ||= { items: [], stats: {} };
  let brother = human?.brother_qq || '';
  const state = ctx.state(() => ({ query: '', sort: 'favor', page: 1, ledgerStatus: 'all', ledgerQuery: '', ledgerPage: 1 }));
  const listHost = el('div');
  const count = el('span', { class: 'pill', text: `${rows.items.length} 位用户` });
  const searchSlot = el('div');
  const head = el('div', { class: 'page-head' },
    el('div', null, el('h2', { text: '好感度' }), el('p', { text: '数值在输入框外失焦或回车时立即保存。' })),
    el('div', { class: 'page-actions' }, count,
      el('button', { class: 'button secondary small', type: 'button', onclick: () => downloadCSV('yuna-favor.csv', [
        { label: '昵称', key: 'nickname' }, { label: 'QQ', key: 'qq' }, { label: '好感度', key: 'favorability' },
        { label: '互动', key: 'interactions' }, { label: '最近活跃', key: 'last_active', csv: (row) => stamp(row.last_active) },
      ], rows.items) }, icon('download', 14), '导出')));
  const toolbar = el('div', { class: 'toolbar between' }, searchSlot,
    el('select', { class: 'select', style: { maxWidth: '150px' }, onchange: (event) => { state.sort = event.target.value; renderList(); } },
      el('option', { value: 'favor' }, '按好感度'),
      el('option', { value: 'interactions' }, '按互动次数'),
      el('option', { value: 'recent' }, '按最近活跃')));
  const ledgerHost = el('div');
  let refreshLedgerTable = null;
  if (!ledgerOnly) host.append(head, toolbar, listHost);

  searchSlot.append(searchField('搜索昵称或 QQ…', (value) => { state.query = value; state.page = 1; renderList(); }, state.query));

  function renderList() {
    const q = state.query.toLowerCase();
    const items = rows.items.filter((row) => !q || `${row.nickname} ${row.qq}`.toLowerCase().includes(q));
    if (state.sort === 'favor') items.sort((a, b) => (Number(b.favorability) || 0) - (Number(a.favorability) || 0));
    if (state.sort === 'interactions') items.sort((a, b) => (Number(b.interactions) || 0) - (Number(a.interactions) || 0));
    if (state.sort === 'recent') items.sort((a, b) => (Number(b.last_active) || 0) - (Number(a.last_active) || 0));
    clear(listHost);
    if (!items.length) {
      listHost.append(panel('好感度', {}, emptyState('没有匹配的用户', '换一个关键词试试。', 'heart')));
      return;
    }
    const body = el('tbody');
    const page = pageSlice(items, state.page, 20);
    state.page = page.page;
    page.rows.forEach((row, index) => {
      const rank = (page.page - 1) * 20 + index + 1;
      const input = el('input', { class: 'input num', type: 'number', step: '0.5', min: '-100000', max: '1000', value: String(row.favorability ?? 0), 'aria-label': `${row.nickname || row.qq} 的好感度` });
      input.style.width = '86px';
      let last = Number(row.favorability) || 0;
      let saving = false;
      const commit = async () => {
        const value = Number(input.value);
        if (!Number.isFinite(value) || value === last || saving) return;
        saving = true;
        input.disabled = true;
        try {
          await api(`chat/favor/${encodeURIComponent(row.qq)}`, { method: 'PUT', body: { favorability: value } });
          last = value;
          row.favorability = value;
          toast(`${row.nickname || row.qq} 好感度已更新`, 'ok');
          rowNode.children[2].replaceChildren(stageBadge(value, row.interactions, brother, row.qq));
          rowNode.querySelector('.meter-fill').style.width = `${clamp(value, 0, 100)}%`;
          rowNode.querySelector('.favor-bar strong').textContent = value.toFixed(1);
        } catch (error) {
          input.value = String(last);
          toast(errorText(error), 'error');
        } finally { saving = false; input.disabled = false; }
      };
      input.addEventListener('change', commit);
      input.addEventListener('blur', commit);
      input.addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); void commit(); } });
      const rowNode = el('tr', null,
        el('td', { class: 'num' }, el('span', { class: `rank${rank <= 3 ? ' top' : ''}`, text: String(rank) })),
        el('td', null, el('div', { class: 'user-cell' },
          el('span', { class: 'avatar', text: String(row.nickname || row.qq).slice(0, 1) }),
          el('div', { class: 'user-cell-text' }, el('strong', { text: row.nickname || '—' }), el('small', { text: row.qq })))),
        el('td', null, stageBadge(row.favorability, row.interactions, brother, row.qq)),
        el('td', null, input),
        el('td', null, el('div', { class: 'favor-bar' }, el('div', { class: 'meter' }, el('div', { class: 'meter-fill', style: { width: `${clamp(last, 0, 100)}%` } })), el('strong', { text: Number(last).toFixed(1) }))),
        el('td', { text: num(row.interactions) }),
        el('td', { class: 'muted-cell', text: timeAgo(row.last_active) }));
      body.append(rowNode);
    });
    const tableNode = el('table', { class: 'data-table' },
      el('thead', null, el('tr', null, el('th', { text: '#' }), el('th', { text: '用户' }), el('th', { text: '关系' }), el('th', { text: '好感度' }), el('th', { text: '刻度' }), el('th', { text: '互动' }), el('th', { text: '最近活跃' }))),
      body);
    listHost.append(panel('好感度', { subtitle: `${items.length} 位用户` }, el('div', { class: 'table-wrap' }, tableNode),
      pagerControls({ page: page.page, pageCount: page.pageCount, total: page.total, onPage: (value) => { state.page = value; renderList(); }, unit: '位' })));
  }
  const LEDGER_STATUS = {
    none: { label: '未申诉', tone: 'outline' },
    pending: { label: '申诉中', tone: 'warn' },
    accepted: { label: '已恢复', tone: 'ok' },
    rejected: { label: '已驳回', tone: '' },
  };
  const LEDGER_SOURCE = { message: '群聊消息', private: '私聊消息', manual: '手动', system: '系统' };

  function ledgerUserCell(entry) {
    const name = entry.nickname || entry.qq;
    return el('div', { class: 'user-cell' },
      el('span', { class: 'avatar', text: String(name).slice(0, 1) }),
      el('div', { class: 'user-cell-text' }, el('strong', { text: name }), el('small', { class: 'mono', text: entry.qq })));
  }

  function openLedgerDetail(entry, rerender) {
    // 外层用普通容器：loading 只是占位，详情内容 replace 整个 body，
    // 绝不能往 loading-line（不换行的 flex）里塞内容
    const body = el('div', null, loading('正在读取扣分详情…'));
    modal({
      title: `扣分详情 · ${entry.nickname || entry.qq}`,
      subtitle: `${stamp(entry.time)} · ${entry.reason || '未注明'}`,
      body, wide: true,
      onClose: () => { if (rerender) rerender(); },
      actions: [{ label: '关闭', className: 'secondary', onClick: (close) => close() }],
    });
    api(`chat/favor/ledger/${entry.id}`).then((detail) => {
      const e = detail.entry || {};
      const feedback = detail.feedback || {};
      const hasText = Boolean((e.text || '').trim());
      const context = e.context || [];
      const appeal = e.appeal || 'none';
      const appealMeta = { none: ['未申诉', 'outline'], pending: ['申诉中', 'warn'], accepted: ['已恢复', 'ok'], rejected: ['已驳回', ''] }[appeal] || [appeal, 'outline'];
      const threshold = e.threshold || {};
      clear(body);
      // 概览
      body.append(el('div', { class: 'detail-stats' },
        el('div', { class: 'ledger-stat' }, el('small', { text: '扣分' }),
          el('strong', { text: `-${Number(e.penalty || 0).toFixed(1)}` }),
          el('small', { class: 'muted', text: Number(e.requested) !== Number(e.penalty) ? `请求 -${Number(e.requested || 0).toFixed(1)}` : '全额执行' })),
        el('div', { class: 'ledger-stat' }, el('small', { text: '当时好感' }),
          el('strong', { text: e.relation?.favorability !== undefined ? Number(e.relation.favorability).toFixed(1) : '—' }),
          el('small', { class: 'muted', text: `互动 ${num(e.relation?.interactions || 0)} 次${e.relation?.brother ? ' · 哥哥' : ''}` })),
        el('div', { class: 'ledger-stat' }, el('small', { text: '该原因误判率' }),
          el('strong', { text: `${feedback.accepted || 0} / ${(feedback.accepted || 0) + (feedback.rejected || 0)}` }),
          el('small', { class: 'muted', text: `近 ${feedback.window_days || 30} 天恢复/维持` })),
        el('div', { class: 'ledger-stat' }, el('small', { text: '申诉状态' }),
          el('strong', null, badge(appealMeta[0], appealMeta[1])),
          el('small', { class: 'muted', text: e.appeal_note ? `备注：${e.appeal_note}` : '' }))));
      if (detail.suppressed) {
        body.append(el('p', { class: 'notice warn', style: { margin: '10px 0 0' } },
          '该原因近期多次被人工判定误判，后续同类扣分已自动抑制（暂不执行），详情见下方统计。'));
      }
      // 触发原文
      body.append(el('h3', { style: { margin: '14px 0 6px', fontSize: '14px' }, text: '触发内容' }));
      if (hasText) {
        body.append(el('p', { class: 'notice warn', style: { margin: 0, overflowWrap: 'anywhere' }, text: e.text }));
        if (e.source) body.append(el('p', { class: 'muted small', style: { margin: '4px 0 0' }, text: `来源：${LEDGER_SOURCE[e.source] || e.source}` }));
      } else {
        body.append(el('div', { class: 'stack-sm' },
          el('p', { class: 'muted small', style: { margin: 0 }, text: e.backfilled
            ? '本条原文是从记忆中回填的近似内容（当时未直接保存），可能与触发时刻略有出入。'
            : '此条产生于「原文保存」功能上线之前，记忆里也没有匹配到相同内容的发言，原文无法找回。' }),
          el('div', { class: 'row', style: { gap: '8px', alignItems: 'center', margin: 0 } },
            el('p', { class: 'muted small mono', style: { margin: 0 }, text: `内容哈希：${e.text_hash || '—'}` }),
            e.text_hash ? el('button', { class: 'button ghost xs', type: 'button', onclick: async () => {
              try { await navigator.clipboard.writeText(e.text_hash); toast('哈希已复制', 'ok'); }
              catch (_) { toast('复制失败，请手动选择', 'error'); }
            } }, '复制') : null),
          el('div', { class: 'row', style: { gap: '8px', marginTop: '6px' } },
            el('button', { class: 'button ghost xs', type: 'button', onclick: async () => {
              try {
                const result = await api('chat/favor/ledger/backfill', { method: 'POST', timeout: 60000 });
                toast(`回填完成：${result.filled || 0} 条`, result.filled ? 'ok' : 'info');
                closeModal();
                openLedgerDetail(entry, rerender);
              } catch (error) { toast(errorText(error), 'error'); }
            } }, icon('refresh', 12), '尝试从记忆回填旧记录'),
            el('button', { class: 'button ghost xs', type: 'button', onclick: () => { closeModal(); ctx.navigate(`users/${encodeURIComponent(e.qq)}`); } }, icon('external', 12), '去该用户记忆里找'))));
      }
      // 上下文
      if (context.length) {
        body.append(el('h3', { style: { margin: '14px 0 6px', fontSize: '14px' }, text: `当时上下文（${context.length} 条）` }));
        body.append(el('div', { class: 'timeline', style: { maxHeight: '240px', overflowY: 'auto' } },
          ...context.map((turn) => el('div', { class: 'timeline-item' },
            el('span', { class: 'timeline-time', text: turn.role === 'assistant' ? 'Yuna' : (turn.name || turn.qq || '群友') }),
            el('span', { class: 'timeline-body', text: (turn.content || '') + (turn.recalled ? '（已撤回）' : '') })))));
      }
      // 判定阈值
      const thresholdRows = Object.entries(threshold).filter(([, v]) => v !== '' && v !== null && v !== undefined);
      if (thresholdRows.length) {
        body.append(el('h3', { style: { margin: '14px 0 6px', fontSize: '14px' }, text: '判定阈值（当时的规则参数）' }));
        const names = { close_favor: '熟人好感阈值', close_favor_with_turns: '熟人好感(高互动)', close_interactions: '熟人互动阈值', actual_favor: '当时好感', actual_interactions: '当时互动' };
        body.append(el('div', { class: 'metric-row', style: { gridTemplateColumns: '140px minmax(0,1fr)' } },
          ...thresholdRows.flatMap(([k, v]) => [el('span', { text: names[k] || k }), el('strong', { text: String(v) })])));
      }
      // 申诉操作（详情内直接处理）
      body.append(el('h3', { style: { margin: '14px 0 6px', fontSize: '14px' }, text: '申诉处理' }));
      const actionBar = el('div', { class: 'row', style: { gap: '8px', flexWrap: 'wrap' } });
      const act = async (fn, doneText) => {
        try { await fn(); toast(doneText, 'ok'); closeModal(); rerender?.(); }
        catch (error) { toast(errorText(error), 'error'); }
      };
      if (appeal === 'none') {
        const noteInput = el('input', { class: 'input', placeholder: '申诉备注（可选）：为什么认为是误判', maxlength: '300', style: { flex: '1 1 240px' } });
        actionBar.append(noteInput,
          el('button', { class: 'button secondary small', type: 'button', onclick: () => act(
            () => api(`chat/favor/ledger/${e.id}/appeal`, { method: 'POST', body: { note: noteInput.value.trim() } }),
            '已提交申诉，等待处理') }, icon('edit', 13), '提交申诉'));
      } else if (appeal === 'pending') {
        actionBar.append(
          el('button', { class: 'button small', type: 'button', onclick: () => act(
            () => api(`chat/favor/ledger/${e.id}/resolve`, { method: 'POST', body: { accepted: true } }),
            '已恢复好感') }, icon('check', 13), '误判恢复（返还扣分）'),
          el('button', { class: 'button danger small', type: 'button', onclick: () => act(
            () => api(`chat/favor/ledger/${e.id}/resolve`, { method: 'POST', body: { accepted: false } }),
            '已驳回申诉') }, '驳回'));
      } else {
        actionBar.append(el('span', { class: 'muted small', text: '该条已处理完毕。' }));
      }
      body.append(actionBar);
    }).catch((error) => {
      clear(body);
      body.append(emptyState('读取详情失败', errorText(error), 'warning'));
    });
  }

  function renderLedger() {
    const stats = ledger.stats || {};
    clear(ledgerHost);
    const statBits = el('div', { class: 'ledger-stats' },
      el('div', { class: 'ledger-stat' }, el('small', { text: '累计扣分' }), el('strong', { text: `-${Number(stats.total_penalty || 0).toFixed(1)}` })),
      el('div', { class: 'ledger-stat' }, el('small', { text: '扣分记录' }), el('strong', { text: num(stats.count || 0) })),
      el('div', { class: 'ledger-stat' }, el('small', { text: '已恢复' }), el('strong', { text: `+${Number(stats.restored_penalty || 0).toFixed(1)}` })),
      el('div', { class: 'ledger-stat' }, el('small', { text: '待处理申诉' }), el('strong', { text: num(stats.pending || 0) })),
      el('div', { class: 'ledger-stat' }, el('small', { text: '今日扣分' }), el('strong', { text: `-${Number(stats.today_penalty || 0).toFixed(1)}` })));
    const statusBar = el('div', { class: 'pill-row' });
    [['all', '全部'], ['none', '未申诉'], ['pending', '申诉中'], ['accepted', '已恢复'], ['rejected', '已驳回']].forEach(([value, label]) => {
      statusBar.append(el('button', {
        type: 'button', class: `chip${state.ledgerStatus === value ? ' active' : ''}`,
        onclick: (event) => {
          state.ledgerStatus = value;
          state.ledgerPage = 1;
          statusBar.querySelectorAll('.chip').forEach((node) => node.classList.toggle('active', node === event.currentTarget));
          renderLedgerTable();
        },
      }, label));
    });
    const filterSlot = el('div', { class: 'emote-search-slot' });
    filterSlot.append(searchField('搜索 QQ、昵称或原因…', (value) => { state.ledgerQuery = value; state.ledgerPage = 1; renderLedgerTable(); }, state.ledgerQuery));
    const tableHost = el('div');

    function renderLedgerTable() {
      const q = state.ledgerQuery.toLowerCase();
      const rowsShown = (ledger.items || []).filter((entry) => {
        if (state.ledgerStatus !== 'all' && (entry.appeal || 'none') !== state.ledgerStatus) return false;
        return !q || `${entry.qq} ${entry.nickname || ''} ${entry.reason || ''}`.toLowerCase().includes(q);
      });
      clear(tableHost);
      if (!rowsShown.length) {
        tableHost.append(emptyState(items.length ? '没有匹配的扣分记录' : '还没有扣分记录', items.length ? '换一个筛选条件或关键词试试。' : '触发边界规则后会在这里留下可申诉记录。', 'shield'));
        return;
      }
      const body = el('tbody');
      const page = pageSlice(rowsShown, state.ledgerPage, 20);
      state.ledgerPage = page.page;
      page.rows.forEach((entry) => {
        const appeal = entry.appeal || 'none';
        const statusMeta = LEDGER_STATUS[appeal] || { label: appeal, tone: 'outline' };
        const actions = el('div', { class: 'row end' });
        const act = (handler) => async (event) => { event.stopPropagation(); await handler(); };
        if (appeal === 'none') {
          actions.append(el('button', { class: 'button ghost xs', type: 'button', onclick: act(async () => {
            try { await api(`chat/favor/ledger/${entry.id}/appeal`, { method: 'POST', body: { note: '' } }); toast('已提交申诉', 'ok'); ctx.rerender(); }
            catch (error) { toast(errorText(error), 'error'); }
          }) }, '申诉'));
        }
        if (appeal === 'pending') {
          actions.append(el('button', { class: 'button secondary xs', type: 'button', onclick: act(async () => {
            try { await api(`chat/favor/ledger/${entry.id}/resolve`, { method: 'POST', body: { accepted: true } }); toast('已恢复好感', 'ok'); ctx.rerender(); }
            catch (error) { toast(errorText(error), 'error'); }
          }) }, '误判恢复'));
          actions.append(el('button', { class: 'button danger xs', type: 'button', onclick: act(async () => {
            try { await api(`chat/favor/ledger/${entry.id}/resolve`, { method: 'POST', body: { accepted: false } }); toast('已驳回', 'ok'); ctx.rerender(); }
            catch (error) { toast(errorText(error), 'error'); }
          }) }, '驳回'));
        }
        const detailBits = [
          `时间：${stamp(entry.time)}`,
          entry.source ? `来源：${LEDGER_SOURCE[entry.source] || entry.source}` : '',
          entry.appeal_note ? `申诉备注：${entry.appeal_note}` : '',
          entry.resolved_time ? `处理时间：${stamp(entry.resolved_time)}` : '',
          entry.requested !== undefined && Number(entry.requested) !== Number(entry.penalty) ? `原始请求：-${Number(entry.requested || 0).toFixed(1)}` : '',
        ].filter(Boolean).join('\n');
        body.append(el('tr', { title: detailBits, style: { cursor: 'pointer' }, onclick: () => openLedgerDetail(entry, ctx.rerender) },
          el('td', { class: 'muted-cell' }, el('span', { text: stampShort(entry.time) }), el('small', { class: 'muted', text: ` ${timeAgo(entry.time)}`, style: { display: 'block' } })),
          el('td', null, ledgerUserCell(entry)),
          el('td', { class: 'num' }, el('strong', { text: `-${Number(entry.penalty || 0).toFixed(1)}` })),
          el('td', null, el('span', { text: entry.reason || '—' }),
            el('small', { class: 'muted', style: { display: 'block' }, text: [
              entry.source ? (LEDGER_SOURCE[entry.source] || entry.source) : '',
              entry.has_text === false ? '无原文' : '有原文',
            ].filter(Boolean).join(' · ') })),
          el('td', { class: 'muted-cell', text: entry.relation?.label || (entry.relation?.close ? '熟人' : '普通') }),
          el('td', null, badge(statusMeta.label, statusMeta.tone)),
          el('td', { class: 'actions' }, actions)));
      });
      tableHost.append(el('div', { class: 'table-wrap' }, el('table', { class: 'data-table ledger-table' },
        el('thead', null, el('tr', null, el('th', { text: '时间' }), el('th', { text: '用户' }), el('th', { text: '扣分' }),
          el('th', { text: '原因' }), el('th', { text: '关系' }), el('th', { text: '申诉' }), el('th', { text: '操作' }))),
        body)));
      tableHost.append(pagerControls({ page: page.page, pageCount: page.pageCount, total: page.total, onPage: (value) => { state.ledgerPage = value; renderLedgerTable(); } }));
    }
    refreshLedgerTable = renderLedgerTable;
    renderLedgerTable();

    const topReasons = stats.top_reasons || [];
    ledgerHost.append(panel('好感扣分账本', {
      subtitle: '每条扣分记录时间、原因、关系阈值与申诉状态；误判恢复会返还本次扣分',
    },
      statBits,
      el('div', { class: 'emote-filter-row' }, filterSlot, statusBar),
      tableHost,
      topReasons.length ? el('div', { class: 'metric-row', style: { gridTemplateColumns: '90px minmax(0,1fr)', marginTop: '12px' } },
        el('span', { class: 'muted', text: '扣分主因' }),
        el('div', { class: 'profile-badges' }, topReasons.map((row) => badge(`${row.reason} · -${Number(row.penalty || 0).toFixed(1)}`, 'outline')))) : null));
  }
  if (ledgerOnly) {
    host.append(pageHead('好感扣分账本', '查看扣分依据和原文，处理申诉与恢复。'), ledgerHost);
    renderLedger();
  } else {
    head.querySelector('.page-actions').append(el('button', { class: 'button secondary small', type: 'button', onclick: () => ctx.navigate('favor-ledger') }, '查看扣分账本'));
    renderList();
  }
  ctx.onRefresh(async () => {
    if (ledgerOnly) {
      ledger = await api('chat/favor/ledger?limit=200');
      refreshLedgerTable();
      const stats = ledger.stats || {};
      const values = [`-${Number(stats.total_penalty || 0).toFixed(1)}`, num(stats.count || 0),
        `+${Number(stats.restored_penalty || 0).toFixed(1)}`, num(stats.pending || 0),
        `-${Number(stats.today_penalty || 0).toFixed(1)}`];
      ledgerHost.querySelectorAll('.ledger-stat strong').forEach((node, i) => { node.textContent = values[i]; });
    } else {
      const next = await Promise.all([api('chat/favor'), api('chat/human')]);
      [rows, human] = next;
      brother = human?.brother_qq || '';
      count.textContent = `${rows.items.length} 位用户`;
      renderList();
    }
  });
}

function buildLifeHistory(history) {
  const rows = history || [];
  if (!rows.length) return null;
  const items = rows.slice().reverse().map((row) => el('div', { class: 'timeline-item' },
    el('span', { class: 'timeline-time', text: stampShort(row.time) }),
    el('span', { class: 'timeline-body', text: `${row.name || '?'}${row.location ? ` · ${row.location}` : ''}${row.tool_result ? ' · 有作品' : ''}` })));
  return el('details', { class: 'life-history', style: { marginTop: '8px' } },
    el('summary', { class: 'muted small', style: { cursor: 'pointer' }, text: `最近做过的事（${rows.length} 条）` }),
    el('div', { class: 'timeline', style: { marginTop: '8px', maxHeight: '220px', overflowY: 'auto' } }, ...items));
}

function buildSelfMemoryPanel(selfMem, ctx) {
  const host = el('section', { class: 'panel' },
    el('div', { class: 'panel-head' },
      el('div', { class: 'panel-title' }, icon('book', 17), el('div', null, el('h2', { text: '自我记忆' }), el('p', { text: '群聊与电脑/手机共享的唯一记忆：她知道关于自己的事与真实经历' })))));
  if (!selfMem) {
    host.append(emptyState('自我记忆不可用', '需要聊天插件与 agent 模块同时加载。', 'book'));
    return host;
  }
  const factsHost = el('div', { class: 'stack-sm' });
  const factInput = el('input', { class: 'input', placeholder: '新增一条自我事实，如：我喜欢在电脑上用 Krita 画画', maxlength: '200' });
  const addFact = el('button', { class: 'button secondary small', type: 'button' }, icon('plus', 13), '添加');
  addFact.addEventListener('click', async () => {
    const text = factInput.value.trim();
    if (!text) { toast('先写内容再添加', 'warn'); return; }
    busy(addFact, true, '添加中…');
    try {
      await api('agent/self-memory/fact', { method: 'POST', body: { text } });
      toast('已加入自我认知', 'ok');
      ctx.rerender();
    } catch (error) { toast(errorText(error), 'error'); } finally { busy(addFact, false); }
  });
  (selfMem.facts || []).forEach((fact) => {
    const del = iconButton('trash', '删除', async () => {
      try { await api(`agent/self-memory/fact?text=${encodeURIComponent(fact)}`, { method: 'DELETE' }); toast('已删除', 'ok'); ctx.rerender(); }
      catch (error) { toast(errorText(error), 'error'); }
    });
    factsHost.append(el('div', { class: 'switch-row' }, el('span', { class: 'break', text: fact }), del));
  });
  const events = selfMem.events || [];
  host.append(
    el('h3', { style: { margin: '4px 0 6px', fontSize: '13px' }, text: `自我认知（${selfMem.facts?.length || 0} 条）` }),
    factsHost,
    el('div', { class: 'inline-form two', style: { marginTop: '10px' } }, field('新增事实', factInput), addFact),
    el('h3', { style: { margin: '14px 0 6px', fontSize: '13px' }, text: `最近经历（共 ${selfMem.total_events || 0} 条）` }),
    events.length
      ? el('div', { class: 'timeline', style: { maxHeight: '260px', overflowY: 'auto' } },
          ...events.slice().reverse().map((event) => el('div', { class: 'timeline-item' },
            el('span', { class: 'timeline-time', text: stampShort(event.time) }),
            el('span', { class: 'timeline-body', text: `[${event.scene || '?'}] ${event.text || ''}` }))))
      : emptyState('还没有经历记录', '用电脑/手机做事、群聊固化后会写入。', 'book'));
  return host;
}

/* ==========================================================================
 * Human state
 * ========================================================================== */
async function humanPage(host, ctx) {
  const [human, selfMem, proactive] = await Promise.all([
    api('chat/human'),
    api('agent/self-memory').catch(() => null),
    api('chat/proactive').catch(() => null),
  ]);
  const config = human.config || {};
  const reset = el('button', { class: 'button secondary small', type: 'button' }, icon('refresh', 14), '恢复默认心情');
  reset.addEventListener('click', async () => {
    const ok = await confirmDialog('把心情恢复到默认的平静状态？最近回复记录会保留，用于防重复。', { title: '恢复默认心情？', confirmText: '恢复默认' });
    if (!ok) return;
    busy(reset, true, '恢复中…');
    try {
      await api('chat/human/reset', { method: 'POST' });
      toast('心情已恢复默认', 'ok');
      ctx.rerender();
    } catch (error) { toast(errorText(error), 'error'); } finally { busy(reset, false); }
  });
  host.append(el('div', { class: 'page-head' },
    el('div', null, el('h2', { text: '拟人状态' }), el('p', { text: '心情、精力与关系阶段会注入提示词，但不会被直接说出口。' })),
    el('div', { class: 'page-actions' }, el('button', { class: 'button secondary small', type: 'button', onclick: () => ctx.navigate('plugins/chat') }, icon('sliders', 14), '调整参数'), reset)),);

  const mood = human.mood || {};
  const radarPanel = el('section', { class: 'panel chart-card' },
    el('div', { class: 'panel-head' }, el('div', { class: 'panel-title' }, icon('activity', 17), el('div', null, el('h2', { text: '状态雷达' }), el('p', { text: '心情 / 精力 / 平静 / 陪伴 一眼可比' })))),
    radarChart([
      { label: '心情', value: ((Number(mood.valence) || 0) + 1) * 50 },
      { label: '精力', value: (Number(mood.energy) || 0) * 100 },
      { label: '平静', value: (1 - (Number(mood.irritation) || 0)) * 100 },
      { label: '陪伴', value: (1 - (Number(mood.loneliness) || 0)) * 100 },
    ]));
  const moodEvents = human.mood_explain?.events || [];
  const moodEventPanel = el('section', { class: 'panel' },
    el('div', { class: 'panel-head' }, el('div', { class: 'panel-title' }, icon('sparkles', 17), el('div', null, el('h2', { text: '心情事件' }), el('p', { text: '为什么现在是这个心情（心情 v2）' })))),
    moodEvents.length
      ? el('div', { class: 'timeline' }, ...moodEvents.slice().reverse().map((event) => el('div', { class: 'timeline-item' },
          el('span', { class: 'timeline-time', text: stampShort(event.time) }),
          el('span', { class: 'timeline-body', text: event.text || event.kind }))))
      : emptyState('还没有心情事件', '被夸、被凶、刷好感、长时间没人说话都会记录在这里。', 'sparkles'));

  const moodPanel = el('section', { class: 'panel' },
    el('div', { class: 'panel-head' }, el('div', { class: 'panel-title' }, icon('sparkles', 17), el('div', null, el('h2', { text: '此刻' }), el('p', { text: human.stale ? '默认状态，等待第一次互动' : `最近更新 ${timeAgo(human.mood?.updated)}` })))),
    humanMoodPanel(human, ctx));

  const life = human.life || {};
  const lifeActivity = life.activity || {};
  const lifePhase = { prepare: '准备', active: '进行中', paused: '暂停', complete: '完成', abandoned: '放弃' }[lifeActivity.phase] || '空闲';
  const lifeAction = async (action, location = '') => {
    try {
      await api('chat/life/action', { method: 'POST', body: { action, location } });
      toast('生活状态已更新', 'ok');
      ctx.rerender();
    } catch (error) { toast(errorText(error), 'error'); }
  };
  const lifeReset = el('button', { class: 'button ghost xs', type: 'button' }, icon('refresh', 12), '重置生活');
  lifeReset.addEventListener('click', async () => {
    const ok = await confirmDialog('重置当前地点、活动和物品进度？历史与分享池也会清空。', { title: '重置生活状态？', confirmText: '重置', danger: true });
    if (!ok) return;
    try { await api('chat/life/reset', { method: 'POST' }); toast('生活状态已重置', 'ok'); ctx.rerender(); }
    catch (error) { toast(errorText(error), 'error'); }
  });
  const lifePanel = el('section', { class: 'panel' },
    el('div', { class: 'panel-head' }, el('div', { class: 'panel-title' }, icon('map', 17),
      el('div', null, el('h2', { text: '连续生活' }), el('p', { text: '地点、活动阶段、物品和后续计划；聊天不会替换当前活动' }))),
      el('div', { class: 'row' }, badge(life.location || '卧室', 'outline'), lifeReset)),
    el('div', { class: 'life-grid' },
      el('div', { class: 'life-stat' }, el('small', { text: '当前活动' }), el('strong', { text: lifeActivity.name || '空闲' })),
      el('div', { class: 'life-stat' }, el('small', { text: '阶段' }), el('strong', { text: lifePhase })),
      el('div', { class: 'life-stat' }, el('small', { text: '进度' }), el('strong', { text: `${Math.round(Number(lifeActivity.progress || 0) * 100)}%` })),
      el('div', { class: 'life-stat' }, el('small', { text: '精力' }), el('strong', { text: `${Math.round(Number(life.energy || 0) * 100)}%` }))),
    lifeActivity.step ? el('p', { class: 'muted small', text: `正在做：${lifeActivity.step}` }) : null,
    (lifeActivity.items || []).length ? el('p', { class: 'muted small', text: `物品：${(lifeActivity.items || []).join('、')}` }) : null,
    life.travel?.to ? el('p', { class: 'notice info', text: `正在前往${life.travel.to}，预计还需约 ${Math.max(0, Math.round((Number(life.travel.arrive || 0) - Date.now() / 1000) / 60))} 分钟` }) : null,
    el('div', { class: 'row', style: { marginTop: '10px' } },
      lifeActivity.phase === 'paused'
        ? el('button', { class: 'button secondary xs', type: 'button', onclick: () => lifeAction('resume') }, '继续')
        : el('button', { class: 'button secondary xs', type: 'button', onclick: () => lifeAction('pause') }, '暂停'),
      el('button', { class: 'button ghost xs', type: 'button', onclick: () => lifeAction('travel', '公园') }, '去公园'),
      el('button', { class: 'button ghost xs', type: 'button', onclick: () => lifeAction('travel', '书房') }, '去书房'),
      (life.share_pool || []).length ? badge(`${life.share_pool.length} 条待分享`, 'info') : null,
      Object.keys(life.share_stats || {}).length
        ? el('span', { class: 'muted small', text: '分享反馈：' + Object.entries(life.share_stats).map(([kind, row]) => `${kind} 接${row.replied || 0}/忽略${row.ignored || 0}/反感${row.negative || 0}`).join(' · ') })
        : null),
    (life.plans || []).length ? el('p', { class: 'muted small', style: { margin: '8px 0 0' }, text: '接下来的打算：' + life.plans.slice(-4).map((row) => row.name || row).join('、') }) : null,
    buildLifeHistory(life.history));

  const world = life.world || {};
  const worldCurrent = world.current || {};
  const worldWeather = world.weather || {};
  const worldRoutes = world.routes || [];
  const worldPanel = el('section', { class: 'panel' },
    el('div', { class: 'panel-head' }, el('div', { class: 'panel-title' }, icon('map', 17),
      el('div', null, el('h2', { text: '世界地图' }), el('p', { text: '固定布局、多路线、可拓展区域与场景细节' }))),
      badge(`${worldWeather.temperature ?? '—'}°C`, 'outline')),
    el('div', { class: 'life-grid' },
      el('div', { class: 'life-stat' }, el('small', { text: '当前位置' }), el('strong', { text: worldCurrent.name || life.location || '—' })),
      el('div', { class: 'life-stat' }, el('small', { text: '天气' }), el('strong', { text: worldWeather.text || '—' })),
      el('div', { class: 'life-stat' }, el('small', { text: '可达地点' }), el('strong', { text: `${worldRoutes.length} 个` })),
      el('div', { class: 'life-stat' }, el('small', { text: '待分享' }), el('strong', { text: `${(life.share_pool || []).length} 条` }))),
    (() => {
      const nameOf = (id) => (world.nodes || []).find((node) => node.id === id)?.name || id;
      return worldRoutes.length ? el('p', { class: 'muted small', text: '附近：' + worldRoutes.slice(0, 8).map((route) => `${nameOf(route.to === worldCurrent.id ? route.from : route.to)} · ${route.minutes}分钟`).join(' / ') }) : null;
    })(),
    Object.keys(world.details || {}).length ? el('p', { class: 'muted small', text: '场景细节：' + Object.entries(world.details).slice(0, 8).map(([key, value]) => `${key}=${value}`).join(' · ') }) : null,
    (world.lessons || []).length ? el('p', { class: 'notice info', text: '生活教训：' + world.lessons.slice(-3).map((row) => row.note || row.key).join('；') }) : null);

  if (world.nodes?.length) {
    const parentSelect = el('select', null, ...world.nodes.filter((node) => node.kind === 'area' || ['plaza','street_home','mall'].includes(node.id)).map((node) => el('option', { value: node.id, text: node.name })));
    const newName = el('input', { placeholder: '新区域 / 店铺名称' });
    const newKind = el('select', null, el('option', { value: 'shop' }, '店铺'), el('option', { value: 'street' }, '街道'), el('option', { value: 'park' }, '公共区域'));
    const expandForm = el('form', { class: 'inline-form', style: { marginTop: '10px' } },
      field('挂在', parentSelect), field('新增地点', newName), field('类型', newKind),
      el('button', { class: 'button secondary small', type: 'submit' }, icon('plus', 13), '拓展地图'));
    expandForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      try {
        await api('chat/world/expand', { method: 'POST', body: { parent: parentSelect.value, name: newName.value.trim(), kind: newKind.value } });
        toast('地图已拓展', 'ok'); ctx.rerender();
      } catch (error) { toast(errorText(error), 'error'); }
    });
    const nodeSelect = el('select', null, ...world.nodes.map((node) => el('option', { value: node.id, text: node.name })));
    const detailKey = el('input', { placeholder: '细节键，如 curtains' });
    const detailValue = el('input', { placeholder: '值，如 关闭' });
    const detailForm = el('form', { class: 'inline-form', style: { marginTop: '6px' } },
      field('场景', nodeSelect), field('细节', detailKey), field('值', detailValue),
      el('button', { class: 'button ghost small', type: 'submit' }, icon('check', 13), '保存细节'));
    detailForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      try {
        await api('chat/world/detail', { method: 'POST', body: { node: nodeSelect.value, key: detailKey.value.trim(), value: detailValue.value.trim() } });
        toast('场景细节已保存', 'ok'); ctx.rerender();
      } catch (error) { toast(errorText(error), 'error'); }
    });
    worldPanel.append(expandForm, detailForm);
  }

  const switches = [
    ['拟人化', config.humanize, '注入心情、关系与拟人化后处理'],
    ['打字延迟', config.typing, '回复前模拟阅读与打字耗时'],
    ['心情系统', config.mood, '根据互动与时间衰减心情'],
    ['生活感', config.activity, '缓慢变化“此刻在做什么”'],
    ['连续生活', config.life, '地点、活动阶段、移动与计划'],
    ['情绪线索', config.mood_cues, '按夸奖/道歉/攻击性话语微调心情'],
    ['深夜模式', config.night_quiet, '深夜回复更短、更软'],
    ['防重复', config.anti_repeat, '与最近回复太像时重写一次'],
  ];
  const statusPanel = el('section', { class: 'panel' },
    el('div', { class: 'panel-head' }, el('div', { class: 'panel-title' }, icon('sliders', 17), el('div', null, el('h2', { text: '策略开关' }), el('p', { text: '这些开关来自环境配置，修改后需重启' })))),
    el('div', { class: 'stack-sm' }, ...switches.map(([label, enabled, hint]) => el('div', { class: 'switch-row' },
      el('span', { text: label }), el('span', { class: 'muted small', text: hint }), statusBadge(enabled, '开启', '关闭')))),
    el('div', { class: 'notice info', style: { marginTop: '12px' } }, icon('info', 16), el('div', { text: '拟人化的目标不是“装人”，而是避免模板化：回复更短、有停顿、有情绪起伏，同时保留明确的人设边界。' })));

  const ladderPanel = el('section', { class: 'panel' },
    el('div', { class: 'panel-head' }, el('div', { class: 'panel-title' }, icon('heart', 17),
      el('div', null, el('h2', { text: '关系层级' }), el('p', { text: '眼熟之后还有常聊、轻松、默契、在意、偏爱、羁绊' })))),
    el('div', { class: 'relation-ladder' }, ...STAGES.map((stage) => el('div', { class: 'relation-ladder-row' },
      el('span', { class: 'relation-range', text: stage.min === -Infinity ? '< 10' : `${stage.min}–${stage.max}` }),
      el('strong', { text: stage.label }),
      el('span', { class: 'muted small', text: `点赞 ${stage.cap}/天` }),
      stage.birthday ? badge('生日可祝福', 'ok') : badge('无生日祝福', 'outline')))));

  const relationships = human.relationships || [];
  const relationPanel = el('section', { class: 'panel' },
    el('div', { class: 'panel-head' }, el('div', { class: 'panel-title' }, icon('heart', 17), el('div', null, el('h2', { text: '关系分布' }), el('p', { text: '按好感度与互动次数估算' })))),
    relationships.length
      ? el('div', { class: 'relationship-grid' }, ...relationships.map((row) => el('div', { class: 'relationship-item' },
        el('strong', { text: num(row.count) }), el('span', { text: row.label }))))
      : emptyState('还没有关系数据', '互动之后会在这里形成分布。', 'heart'));

  const recent = Object.entries(human.recent_replies || {}).slice(0, 8);
  const recentPanel = el('section', { class: 'panel' },
    el('div', { class: 'panel-head' }, el('div', { class: 'panel-title' }, icon('message', 17), el('div', null, el('h2', { text: '最近说过的话' }), el('p', { text: '用于防重复比较，保留最近几条' })))),
    recent.length
      ? el('div', { class: 'timeline' }, ...recent.map(([key, replies]) => {
        const latest = Array.isArray(replies) ? replies[replies.length - 1] : replies;
        const text = typeof latest === 'string' ? latest : latest?.text;
        return el('div', { class: 'timeline-item' },
          el('span', { class: 'timeline-time', text: key.replace('group:', '群 ').replace('user:', '私聊 ') }),
          el('span', { class: 'timeline-body', text: text || '—' }));
      }))
      : emptyState('还没有回复记录', '等她开口之后，这里会出现最近说过的话。', 'message'));

  const selfMemoryPanel = buildSelfMemoryPanel(selfMem, ctx);

  const shareLabels = { sent: '发送接口已确认', unknown: '投递结果不明', rejected: '候选未通过审查' };
  const feedbackLabels = { positive: '积极回应', neutral: '中性回应', negative: '负面回应' };
  const proactiveRows = proactive?.rows || [];
  const proactivePanel = panel('主动分享记录', { subtitle: '最近100条候选与发送记录；概要可展开查看详细信息。' },
    !proactive ? notice('仅所有者可查看，或暂时无法读取记录。', 'info') : !proactiveRows.length
      ? emptyState('暂无主动分享记录', '有候选或发送结果后会在这里显示。', 'chat')
      : el('div', null, ...proactiveRows.map(row => el('details', { class: 'life-history' },
          el('summary', { text: `${stampShort(row.time)} · 群 ${row.gid} · ${shareLabels[row.status] || row.status} · ${row.feedback ? feedbackLabels[row.feedback.sentiment] || '已归因反馈' : '暂无确认反馈'}` }),
          el('p', { text: row.text || '无文本' }),
          row.feedback ? el('p', { class: 'muted small', text: `判断依据：${row.feedback.reason || '无'}；置信度 ${Math.round(Number(row.feedback.confidence || 0) * 100)}%` }) : null,
          el('pre', { class: 'agent-result-text', text: JSON.stringify(row, null, 2) })))));
  host.append(el('div', { class: 'grid grid-2' }, moodPanel, radarPanel), statusPanel, lifePanel, worldPanel, ladderPanel, el('div', { class: 'grid grid-2' }, relationPanel, recentPanel), moodEventPanel, selfMemoryPanel, proactivePanel);
}

/* ==========================================================================
 * Growth persona
 * ========================================================================== */
async function growthPage(host, ctx) {
  const data = await api('chat/growth');
  const state = ctx.state(() => ({ query: '' }));
  const listHost = el('div');
  const searchSlot = el('div');
  let dirty = false;
  const setDirty = (value) => { dirty = value; ctx.setDirty(value); };
  host.append(el('div', { class: 'page-head' },
    el('div', null, el('h2', { text: '成长人格' }), el('p', { text: '每个分桶是一组“她学会的说话方式”，每行一条。' })),
    el('div', { class: 'page-actions' },
      el('span', { class: 'pill', text: `${data.items.length} 个分桶` }),
      el('button', { class: 'button secondary small', type: 'button', onclick: () => openAddBucket(ctx) }, icon('plus', 14), '新增分桶'))),
    el('div', { class: 'toolbar between' }, searchSlot),
    listHost);

  searchSlot.append(searchField('搜索分桶或成长点…', (value) => { state.query = value; renderList(); }, state.query));

  function renderList() {
    const q = state.query.toLowerCase();
    const items = data.items.filter((item) => !q || `${item.key} ${(item.entries || []).join(' ')}`.toLowerCase().includes(q));
    clear(listHost);
    if (!items.length) {
      listHost.append(panel('成长人格', {}, emptyState('没有匹配的成长分桶', '换一个关键词，或新增一个分桶。', 'sprout')));
      return;
    }
    const grid = el('div', { class: 'grid grid-2' });
    items.forEach((item) => {
      const head = el('div', { class: 'growth-head' },
        el('strong', { text: item.key === 'global' ? 'global · 通用群友' : `QQ ${item.key}` }),
        el('div', { class: 'row' },
          badge(`${item.entries?.length || 0} 条`, 'outline'),
          el('button', { class: 'button danger xs', type: 'button' }, icon('trash', 12), '删除分桶')));
      const area = el('textarea', { class: 'textarea', rows: '7', placeholder: '每行一条；留空并保存会删除该分桶' });
      area.value = (item.entries || []).join('\n');
      area.addEventListener('input', () => setDirty(true));
      const save = el('button', { class: 'button primary small', type: 'button' }, icon('check', 14), '保存');
      save.addEventListener('click', async () => {
        busy(save, true, '保存中…');
        try {
          await api(`chat/growth/${encodeURIComponent(item.key)}`, {
            method: 'PUT',
            body: { entries: area.value.split('\n').map((line) => line.trim()).filter(Boolean) },
          });
          item.entries = area.value.split('\n').map((line) => line.trim()).filter(Boolean);
          setDirty(false);
          toast('成长人格已保存', 'ok');
          renderList();
        } catch (error) { toast(errorText(error), 'error'); } finally { busy(save, false); }
      });
      head.lastElementChild.lastElementChild.addEventListener('click', async () => {
        const ok = await confirmDialog(`删除分桶 ${item.key} 的全部成长点？`, { title: '删除成长分桶', confirmText: '删除', danger: true });
        if (!ok) return;
        try {
          await api(`chat/growth/${encodeURIComponent(item.key)}`, { method: 'PUT', body: { entries: [] } });
          data.items = data.items.filter((row) => row.key !== item.key);
          setDirty(false);
          toast('分桶已删除', 'ok');
          renderList();
        } catch (error) { toast(errorText(error), 'error'); }
      });
      area.addEventListener('keydown', (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') save.click();
      });
      listHost.append(el('section', { class: 'panel' },
        head,
        area,
        el('div', { class: 'row between', style: { marginTop: '8px' } },
          el('span', { class: 'muted small', text: 'Ctrl/⌘ + Enter 保存' }),
          save)));
    });
  }
  renderList();
}

function openAddBucket(ctx) {
  const keyInput = el('input', { class: 'input', placeholder: '填写 global 或 QQ 号' });
  const area = el('textarea', { class: 'textarea', rows: '6', placeholder: '每行一条成长点' });
  const body = el('div', null,
    field('分桶键', keyInput, { hint: 'global 表示对一般群友的通用成长；纯数字表示针对某个 QQ。' }),
    field('成长点', area, { hint: '每行一条；只写稳定、跨场景重复出现的行为倾向。' }));
  modal({
    title: '新增成长分桶', subtitle: '把长期观察写成具体的行为倾向',
    body,
    actions: [
      { label: '取消', className: 'secondary', onClick: (close) => close() },
      { label: '创建', className: 'primary', onClick: async (close) => {
        const key = keyInput.value.trim();
        if (!(key === 'global' || /^\d{5,}$/.test(key))) { toast('分桶键必须是 global 或 QQ 号', 'error'); return; }
        const entries = area.value.split('\n').map((line) => line.trim()).filter(Boolean);
        if (!entries.length) { toast('至少写一条成长点', 'error'); return; }
        try {
          await api(`chat/growth/${encodeURIComponent(key)}`, { method: 'PUT', body: { entries } });
          close();
          toast('成长分桶已创建', 'ok');
          ctx.rerender();
        } catch (error) { toast(errorText(error), 'error'); }
      } },
    ],
  });
}

/* ==========================================================================
 * Whitelist
 * ========================================================================== */
async function whitelistPage(host, ctx) {
  const data = await api('chat/whitelist');
  const state = ctx.state(() => ({ tab: 'group', query: '' }));
  const listHost = el('div');
  const searchSlot = el('div');
  const count = el('span', { class: 'pill', text: '' });
  const groupChip = el('button', { type: 'button', class: 'chip active', onclick: () => setTab('group') }, '群聊');
  const userChip = el('button', { type: 'button', class: 'chip', onclick: () => setTab('user') }, '私聊');
  host.append(el('div', { class: 'page-head' },
    el('div', null, el('h2', { text: '触发白名单' }), el('p', { text: '被停用的条目仍保留配置，重新启用即可恢复。' })),
    el('div', { class: 'page-actions' }, count)),
    el('div', { class: 'toolbar between' }, searchSlot, el('div', { class: 'pill-row' }, groupChip, userChip)),
    panel('新增白名单', { subtitle: '群号或 QQ 号，添加后立即生效' }, buildAddForm()),
    listHost);

  searchSlot.append(searchField('搜索群号或 QQ…', (value) => { state.query = value; renderList(); }, state.query));

  function setTab(tab) {
    state.tab = tab;
    groupChip.classList.toggle('active', tab === 'group');
    userChip.classList.toggle('active', tab === 'user');
    renderList();
  }

  function buildAddForm() {
    const kind = el('select', { class: 'select' }, el('option', { value: 'group' }, '群聊'), el('option', { value: 'user' }, '私聊'));
    const idInput = el('input', { class: 'input', inputmode: 'numeric', placeholder: '群号 / QQ 号' });
    const add = el('button', { class: 'button primary', type: 'button' }, icon('plus', 14), '添加');
    add.addEventListener('click', async () => {
      const value = idInput.value.trim();
      if (!/^\d{5,20}$/.test(value)) { toast('请输入 5–20 位数字 ID', 'error'); return; }
      busy(add, true, '添加中…');
      try {
        await api('chat/whitelist', { method: 'POST', body: { kind: kind.value, id: value, enabled: true } });
        toast('已加入白名单', 'ok');
        idInput.value = '';
        const fresh = await api('chat/whitelist');
        data.groups = fresh.groups;
        data.users = fresh.users;
        setTab(kind.value);
      } catch (error) { toast(errorText(error), 'error'); } finally { busy(add, false); }
    });
    return el('div', { class: 'inline-form two' },
      field('类型', kind), field('ID', idInput), add);
  }

  function renderList() {
    const q = state.query.toLowerCase();
    const source = state.tab === 'group' ? (data.groups || []) : (data.users || []);
    const items = source.filter((entry) => {
      const id = state.tab === 'group' ? entry.id_group : entry.id_user;
      return !q || String(id).includes(q);
    });
    count.textContent = `群聊 ${data.groups?.length || 0} · 私聊 ${data.users?.length || 0}`;
    clear(listHost);
    if (!items.length) {
      listHost.append(panel(state.tab === 'group' ? '群聊白名单' : '私聊白名单', {}, emptyState('白名单为空', '添加一个群号或 QQ 号开始。', 'shield')));
      return;
    }
    const body = el('tbody');
    items.forEach((entry) => {
      const id = state.tab === 'group' ? entry.id_group : entry.id_user;
      const enabled = entry.enabled !== false;
      const config = entry.config || {};
      const override = config.llm?.model || config.llm?.api_base || config.vision?.model || config.vision?.api_base;
      const toggle = el('button', { class: `toggle small ${enabled ? '' : 'off'}`.trim(), type: 'button' }, enabled ? '已启用' : '已停用');
      toggle.classList.add('button');
      toggle.classList.toggle('secondary', !enabled);
      toggle.addEventListener('click', async () => {
        toggle.disabled = true;
        try {
          await api('chat/whitelist', { method: 'PUT', body: { kind: state.tab, id: String(id), enabled: !enabled } });
          entry.enabled = !enabled;
          renderList();
          toast('白名单状态已更新', 'ok');
        } catch (error) { toast(errorText(error), 'error'); } finally { toggle.disabled = false; }
      });
      const deleteButton = iconButton('trash', '删除白名单', async () => {
        const ok = await confirmDialog(`从白名单中移除 ${id}？`, { title: '移除白名单', confirmText: '移除', danger: true });
        if (!ok) return;
        try {
          await api(`chat/whitelist?kind=${state.tab}&id=${encodeURIComponent(id)}`, { method: 'DELETE' });
          const fresh = await api('chat/whitelist');
          data.groups = fresh.groups;
          data.users = fresh.users;
          renderList();
          toast('白名单条目已移除', 'ok');
        } catch (error) { toast(errorText(error), 'error'); }
      });
      body.append(el('tr', null,
        el('td', { class: 'mono', text: String(id) }),
        el('td', null, statusBadge(enabled, '已启用', '已停用')),
        el('td', null, state.tab === 'group'
          ? el('div', { class: 'row' },
            badge(config.emote?.enabled === false ? '表情关闭' : '表情开启', config.emote?.enabled === false ? 'warn' : 'ok'),
            badge(config.vision?.enabled === false ? '视觉关闭' : '视觉开启', config.vision?.enabled === false ? 'warn' : 'outline'),
            override ? badge(`模型覆盖 · ${config.llm?.model || config.vision?.model}`, 'info') : null)
          : badge('私聊触发', 'outline')),
        el('td', { class: 'actions' }, el('div', { class: 'row end' }, toggle, deleteButton))));
    });
    listHost.append(panel(state.tab === 'group' ? '群聊白名单' : '私聊白名单', { subtitle: `${items.length} 个条目` },
      el('div', { class: 'table-wrap' }, el('table', { class: 'data-table' },
        el('thead', null, el('tr', null, el('th', { text: 'ID' }), el('th', { text: '状态' }), el('th', { text: '覆盖配置' }), el('th'))), body))));
  }
  renderList();
}

/* ==========================================================================
 * Emotes
 * ========================================================================== */
async function emotesPage(host, ctx) {
  const data = await api('chat/emotes');
  const cache = data.cache || await api('media/cache').catch(() => null);
  const state = ctx.state(() => ({ query: '', category: '', sort: 'time', type: 'all', visible: [], limit: 24 }));
  let items = [...data.files];
  const grid = el('div', { class: 'emote-grid' });
  const pager = el('div', { class: 'emote-pager' });
  const categoryRow = el('div', { class: 'pill-row' });
  const count = el('span', { class: 'pill', text: `${items.length} 个文件` });
  const searchSlot = el('div', { class: 'emote-search-slot' });
  const sortSelect = el('select', { class: 'select', style: { maxWidth: '140px' } },
    el('option', { value: 'time' }, '最近收藏'),
    el('option', { value: 'name' }, '按文件名'),
    el('option', { value: 'size' }, '按文件大小'));
  const typeBar = el('div', { class: 'pill-row' },
    ...[['all', '全部'], ['gif', '动图'], ['static', '静态']].map(([value, label]) => el('button', {
      type: 'button', class: `chip${value === 'all' ? ' active' : ''}`, onclick: (event) => {
        state.type = value;
        state.limit = 24;
        typeBar.querySelectorAll('.chip').forEach((node) => node.classList.toggle('active', node === event.currentTarget));
        renderGrid();
      },
    }, label)));
  const uploadButton = el('button', { class: 'button primary small', type: 'button' }, icon('upload', 14), '上传表情');
  uploadButton.addEventListener('click', () => openUpload(ctx));
  const clearCache = el('button', { class: 'button secondary small', type: 'button' }, icon('trash', 14), '清空缓存');
  clearCache.addEventListener('click', async () => {
    const ok = await confirmDialog('只删除缩略图缓存，原图不会受影响。下次浏览时会重新生成。', { title: '清空缩略图缓存？', confirmText: '清空', danger: true });
    if (!ok) return;
    busy(clearCache, true, '清理中…');
    try {
      const result = await api('media/cache', { method: 'DELETE' });
      toast(`已清空 ${result.removed} 个缩略图`, 'ok');
      ctx.rerender();
    } catch (error) { toast(errorText(error), 'error'); } finally { busy(clearCache, false); }
  });
  const warm = el('button', { class: 'button secondary small', type: 'button' }, icon('bolt', 14), '预热全部');
  warm.addEventListener('click', async () => {
    busy(warm, true, '预热中…');
    try {
      const result = await api('media/warm', { method: 'POST', body: { width: 360 }, timeout: 180000 });
      toast(`已生成 ${result.generated} 张缩略图`, 'ok');
      ctx.rerender();
    } catch (error) { toast(errorText(error), 'error'); } finally { busy(warm, false); }
  });

  const managedState = { query: '', status: 'all', page: 1 };
  const managedHost = el('div');
  const managedCount = el('span', { class: 'muted small' });
  const managedStatusBar = el('div', { class: 'pill-row' });
  const managedSearchSlot = el('div', { class: 'emote-search-slot' });
  managedSearchSlot.append(searchField('搜索拒绝记录或 hash…', (value) => {
    managedState.query = value.toLowerCase();
    managedState.page = 1;
    renderManaged();
  }));

  function renderManaged() {
    const all = data.managed || [];
    let rows = all.filter((item) => {
      if (managedState.status !== 'all' && item.status !== managedState.status) return false;
      return !managedState.query || `${item.label} ${item.hash} ${item.path} ${item.reason}`.toLowerCase().includes(managedState.query);
    });
    const page = pageSlice(rows, managedState.page, 20);
    managedState.page = page.page;
    const shown = page.rows;
    clear(managedHost);
    clear(managedCount);
    managedCount.textContent = `显示 ${shown.length} / ${rows.length}${rows.length !== (data.managed_total || all.length) ? `（接口共 ${data.managed_total || all.length}）` : ''}`;
    if (!rows.length) {
      managedHost.append(emptyState('没有匹配的拒绝记录', '删除或拒绝表情后会出现在这里。', 'ban'));
      return;
    }
    const body = shown.map((item) => {
      const accept = el('button', { class: 'button secondary xs', type: 'button' }, icon('check', 12), item.status === 'learnable' ? '保持可学习' : '接受');
      accept.addEventListener('click', async () => {
        busy(accept, true, '处理中…');
        try {
          const result = await api('chat/emotes/accept', { method: 'POST', body: { hash: item.hash } });
          toast(result.status === 'saved' ? '已恢复到表情库' : '已标记为可学习，下次出现时会重新判断', 'ok');
          ctx.rerender();
        } catch (error) { toast(errorText(error), 'error'); } finally { busy(accept, false); }
      });
      const actions = el('div', { class: 'row end' }, accept);
      if (item.path) {
        const purge = iconButton('trash', '永久删除文件', async () => {
          const ok = await confirmDialog(`永久删除 ${item.path}？随后仍会保留为拒绝记录，避免再次学习。`, { title: '永久删除拒绝表情？', confirmText: '删除', danger: true });
          if (!ok) return;
          try {
            await api('chat/emotes/managed', { method: 'DELETE', body: { path: item.path } });
            toast('已删除文件，保留拒绝记录', 'ok');
            ctx.rerender();
          } catch (error) { toast(errorText(error), 'error'); }
        });
        actions.append(purge);
      }
      const label = item.label && item.label !== item.hash ? item.label : `未命名 · ${item.hash.slice(0, 8)}`;
      const previewSrc = item.thumb
        ? (item.animated ? (item.url || item.thumb) : item.thumb)
        : '';
      const previewCell = el('td', { class: 'managed-preview' },
        previewSrc
          ? el('img', { src: previewSrc, alt: label, loading: 'lazy', title: item.animated ? '动图 · 点击看原图' : '点击看原图', onclick: () => openLightbox([{ label, url: item.url || item.thumb }], 0) })
          : el('span', { class: 'muted small', text: item.no_file ? '旧记录未保存原图' : '文件已不存在' }));
      const metaBits = [
        el('strong', { text: label || '未命名' }),
        el('small', { class: 'mono', text: item.hash.slice(0, 20) }),
        el('small', { class: 'muted', text: `${item.time ? stampShort(item.time) : '时间未知'} · ${item.size ? bytes(item.size) : '无文件'}${item.animated ? ' · 动图' : ''}` }),
      ];
      if (item.scene) metaBits.push(el('small', { class: 'muted', text: `来自 ${item.scene}` }));
      const reasonText = item.reason
        || (item.status === 'learnable' ? '等待再次出现' : '手动/自动拒绝');
      return el('tr', null,
        previewCell,
        el('td', null, el('div', { class: 'user-cell-text' }, ...metaBits)),
        el('td', null, item.status === 'learnable' ? badge('可学习', 'info') : badge('已拒绝', 'outline')),
        el('td', { class: 'muted-cell managed-reason', text: reasonText }),
        el('td', { class: 'actions' }, actions));
    });
    managedHost.append(el('div', { class: 'table-wrap' }, el('table', { class: 'data-table managed-table' },
      el('thead', null, el('tr', null,
        el('th', { text: '预览' }), el('th', { text: '表情' }), el('th', { text: '状态' }), el('th', { text: '原因' }), el('th', { text: '操作' }))),
      el('tbody', null, ...body))));
    managedHost.append(pagerControls({ page: page.page, pageCount: page.pageCount, total: page.total, onPage: (value) => { managedState.page = value; renderManaged(); }, unit: '条' }));
  }

  clear(managedStatusBar);
  for (const [value, label] of [['all', '全部'], ['rejected', '已拒绝'], ['learnable', '可学习']]) {
    const chip = el('button', {
      type: 'button', class: `chip${managedState.status === value ? ' active' : ''}`,
      onclick: (event) => {
        managedState.status = value;
        managedState.page = 1;
        managedStatusBar.querySelectorAll('.chip').forEach((node) => node.classList.toggle('active', node === event.currentTarget));
        renderManaged();
      },
    }, label);
    managedStatusBar.append(chip);
  }
  renderManaged();
  const managedPanel = panel('被拒绝 / 可学习', {
    subtitle: `删除或拒绝的表情默认进入拒绝区；接受后恢复为可学习，下次出现会重新判断${data.managed_total > 120 ? ' · 接口显示最近 120 条' : ''}`,
  }, el('div', { class: 'emote-filter-row' }, managedSearchSlot),
    el('div', { class: 'emote-filter-row' }, managedStatusBar, managedCount),
    managedHost);

  host.append(el('div', { class: 'page-head' },
    el('div', null, el('h2', { text: '表情仓库' }), el('p', { text: '原图按需读取；列表使用 360px WebP 缩略图，减少带宽与解码压力。' })),
    el('div', { class: 'page-actions' }, count, uploadButton)),
    el('div', { class: 'stat-grid' },
      statCard({ label: '表情文件', value: num(items.length), hint: 'emotes / misc_emoji', icon: 'image' }),
      statCard({ label: '原图占用', value: bytes(data.total_size), hint: '仓库总体积', icon: 'database' }),
      statCard({ label: '缩略图缓存', value: cache ? `${num(cache.files)} 张` : '—', hint: cache ? bytes(cache.bytes) : '尚未生成', icon: 'cache', accent: true }),
      statCard({ label: '学习记录', value: num(data.learned), hint: `${num(data.saved)} 已收藏 · ${num(data.rejected)} 已拒绝`, icon: 'smile' })),
    panel('筛选与排序', { subtitle: '分类、类型与关键词' },
      el('div', { class: 'emote-filter-row' }, searchSlot, sortSelect, clearCache, warm),
      el('div', { class: 'emote-filter-row' }, typeBar),
      categoryRow),
    grid, pager, managedPanel);

  searchSlot.append(searchField('搜索文件名或标签…', (value) => { state.query = value; state.limit = 24; renderGrid(); }, state.query));
  sortSelect.addEventListener('change', () => { state.sort = sortSelect.value; renderGrid(); });

  function renderCategories() {
    clear(categoryRow);
    const all = el('button', { type: 'button', class: `chip${state.category === '' ? ' active' : ''}`, onclick: () => { state.category = ''; state.limit = 24; renderCategories(); renderGrid(); } }, '全部', el('span', { class: 'count', text: String(items.length) }));
    categoryRow.append(all);
    for (const category of data.categories || []) {
      categoryRow.append(el('button', {
        type: 'button', class: `chip${state.category === category.label ? ' active' : ''}`, onclick: () => {
          state.category = category.label;
          renderCategories();
          renderGrid();
        },
      }, category.label, el('span', { class: 'count', text: String(category.count) })));
    }
  }

  function renderGrid() {
    const q = state.query.toLowerCase();
    let rows = items.filter((file) => {
      if (state.category && file.category !== state.category) return false;
      if (state.type === 'gif' && !file.animated) return false;
      if (state.type === 'static' && file.animated) return false;
      return !q || `${file.path} ${file.label}`.toLowerCase().includes(q);
    });
    if (state.sort === 'time') rows.sort((a, b) => (Number(b.time) || Number(b.mtime) || 0) - (Number(a.time) || Number(a.mtime) || 0));
    if (state.sort === 'name') rows.sort((a, b) => String(a.path).localeCompare(String(b.path), 'zh-CN'));
    if (state.sort === 'size') rows.sort((a, b) => Number(b.size) - Number(a.size));
    const visible = rows.slice(0, state.limit);
    state.visible = visible;
    clear(grid);
    clear(pager);
    if (!rows.length) {
      grid.append(emptyState('没有匹配的表情', '换一个分类或关键词，也可以上传新的表情。', 'image'));
      return;
    }
    visible.forEach((file, index) => {
      const img = el('img', { alt: file.label || file.path, loading: 'lazy', decoding: 'async' });
      img.src = file.thumb || `/api/media/emote?path=${encodeURIComponent(file.path)}&w=360`;
      img.addEventListener('load', () => img.classList.add('loaded'));
      img.addEventListener('error', () => {
        const frame = img.parentElement;
        img.remove();
        frame?.append(el('span', { class: 'emote-fallback', text: '预览生成失败\n点击查看原图' }));
      });
      const frame = el('div', { class: 'emote-frame', title: '点击预览原图', onclick: () => openLightbox(rows.map((row) => ({
        label: row.label || row.path.split('/').pop(), category: row.category, size: row.size, url: row.url,
      })), index) }, img, el('span', { class: 'emote-type', text: file.animated ? 'GIF' : file.ext?.toUpperCase() || 'IMG' }));
      const copy = iconButton('copy', '复制路径', (event) => {
        event.stopPropagation();
        navigator.clipboard?.writeText(file.path).then(() => toast('路径已复制', 'ok')).catch(() => toast(file.path, 'info', 8000));
      });
      const del = iconButton('trash', '删除', async (event) => {
        event.stopPropagation();
        const ok = await confirmDialog(`确认把 ${file.path} 移入拒绝区？文件会保留并提供预览，之后不会再次学习。`, { title: '移入拒绝区', confirmText: '移入拒绝区', danger: true });
        if (!ok) return;
        try {
          await api(`chat/emote-file?path=${encodeURIComponent(file.path)}`, { method: 'DELETE' });
          items = items.filter((row) => row.path !== file.path);
          data.files = items;
          const counts = new Map();
          items.forEach((row) => counts.set(row.category, (counts.get(row.category) || 0) + 1));
          data.categories = [...counts.entries()].map(([label, count]) => ({ label, count }))
            .sort((a, b) => b.count - a.count);
          if (state.category && !counts.has(state.category)) state.category = '';
          count.textContent = `${items.length} 个文件`;
          renderCategories();
          renderGrid();
          toast('已移入拒绝区，可在下方管理', 'ok');
          ctx.rerender();
        } catch (error) { toast(errorText(error), 'error'); }
      });
      const reject = iconButton('ban', '移入拒绝区', async (event) => {
        event.stopPropagation();
        const ok = await confirmDialog(`把 ${file.path} 移入拒绝区？之后不会再用它。`, { title: '拒绝这个表情？', confirmText: '移入拒绝区', danger: true });
        if (!ok) return;
        try {
          await api('chat/emotes/reject', { method: 'POST', body: { path: file.path } });
          toast('已移入拒绝区', 'ok');
          ctx.rerender();
        } catch (error) { toast(errorText(error), 'error'); }
      });
      const copyAction = iconButton('copy', '复制路径', () => copy.click(), 'emote-action');
      const rejectAction = iconButton('ban', '移入拒绝区', () => reject.click(), 'emote-action');
      const deleteAction = iconButton('trash', '删除表情', () => del.click(), 'emote-action danger-ghost');
      const card = el('figure', { class: 'emote-card' },
        frame,
        el('figcaption', null,
          el('strong', { text: file.label || file.path.split('/').pop() }),
          el('small', { text: `${file.category} · ${bytes(file.size)}` })),
        el('div', { class: 'emote-actions' }, copyAction, rejectAction, deleteAction));
      grid.append(card);
    });
    if (rows.length > visible.length) {
      pager.append(el('button', {
        class: 'button secondary small', type: 'button', onclick: () => {
          state.limit += 24;
          renderGrid();
        },
      }, `加载更多（还有 ${rows.length - visible.length} 条）`));
    }
  }

  function openUpload() {
    const fileInput = el('input', { type: 'file', accept: 'image/png,image/jpeg,image/gif,image/webp', hidden: true });
    const drop = el('div', { class: 'upload-zone' },
      icon('upload', 26),
      el('strong', { text: '点击选择，或把图片拖到这里' }),
      el('small', { text: 'PNG / JPEG / GIF / WebP，单张不超过 6MB' }));
    const name = el('input', { class: 'input', placeholder: '例如 摸鱼.png' });
    const category = el('select', { class: 'select' },
      ...(data.categories || []).filter((row) => row.label !== '杂项').map((row) => el('option', { value: row.label }, row.label)),
      el('option', { value: 'misc' }, '杂项（misc_emoji）'));
    let selected = null;
    const choose = () => fileInput.click();
    drop.addEventListener('click', choose);
    fileInput.addEventListener('change', () => setFile(fileInput.files?.[0]));
    drop.addEventListener('dragover', (event) => { event.preventDefault(); drop.classList.add('drag-over'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('drag-over'));
    drop.addEventListener('drop', (event) => {
      event.preventDefault();
      drop.classList.remove('drag-over');
      setFile(event.dataTransfer?.files?.[0]);
    });
    function setFile(file) {
      if (!file) return;
      selected = file;
      if (!name.value.trim()) {
        const dot = file.name.lastIndexOf('.');
        name.value = dot > 0 ? file.name : `${file.name}.png`;
      }
      const text = drop.querySelector('strong');
      if (text) text.textContent = `已选择：${file.name}`;
    }
    const body = el('div', { class: 'stack' },
      drop, fileInput,
      el('div', { class: 'form-grid' }, field('文件名', name, { hint: '需要带扩展名；同名文件不会被覆盖' }), field('目标分类', category)),
      el('p', { class: 'notice info', text: '上传成功后会写入 emote_learned.json，并出现在对应分类里。' }));
    modal({
      title: '上传表情', subtitle: '补充她的表情仓库',
      wide: false,
      body,
      actions: [
        { label: '取消', className: 'secondary', onClick: (close) => close() },
        { label: '上传', className: 'primary', onClick: async (close) => {
          if (!selected) { toast('请先选择图片', 'error'); return; }
          if (selected.size > 6 * 1024 * 1024) { toast('图片不能超过 6MB', 'error'); return; }
          if (!name.value.trim()) { toast('请填写文件名', 'error'); return; }
          try {
            const base64 = await new Promise((resolve, reject) => {
              const reader = new FileReader();
              reader.onload = () => resolve(String(reader.result).split(',')[1]);
              reader.onerror = () => reject(new Error('读取文件失败'));
              reader.readAsDataURL(selected);
            });
            await api('chat/emotes', { method: 'POST', body: {
              name: name.value.trim(), category: category.value || 'misc', data: base64,
            } });
            close();
            toast('表情已上传', 'ok');
            ctx.rerender();
          } catch (error) { toast(errorText(error), 'error'); }
        } },
      ],
    });
  }

  renderCategories();
  renderGrid();
}

async function chatQuality(host, ctx) {
  const data = await api('chat/quality');
  if (!data.total_replies) {
    host.append(panel('聊天质量', {}, emptyState('还没有可分析的回复', '等聊天插件产生一些回复后，这里会统计模板化、复读与长度分布。只扫描记忆文件中仍在 pending 的助手回复。', 'activity')));
    return;
  }
  const rateTone = (value) => (value >= 8 ? 'danger' : value >= 3 ? 'warn' : 'ok');
  const stats = el('div', { class: 'stat-grid' },
    statCard({ label: '已分析回复', value: num(data.total_replies), hint: `${data.sessions} 个记忆会话`, icon: 'message', accent: true }),
    statCard({ label: '平均长度', value: data.avg_length, unit: '字', hint: `中位数 ${data.median_length} · P90 ${data.p90_length}`, icon: 'activity' }),
    statCard({ label: '模板化', value: `${data.template_rate}%`, hint: `${data.template_count} 条命中 AI/总结/列点腔`, icon: 'warning' }),
    statCard({ label: '复读', value: `${data.repeat_rate}%`, hint: `${data.repeat_count} 条与近期回复高度相似`, icon: 'copy' }),
    statCard({ label: '过短 / 过长', value: `${data.short_rate}% / ${data.long_rate}%`, hint: `${data.short_count} 条 <6 字 · ${data.long_count} 条 >120 字`, icon: 'sliders' }),
    statCard({ label: '被标记', value: num(data.flagged_count), hint: '至少命中一项规则', icon: 'flag' }));

  const lengthPanel = panel('回复长度分布', { subtitle: '字符数（不含表情标记）' },
    barChart(data.length_buckets.map((row) => ({ label: row.label, value: row.value })), { height: 250, tone: 'primary' }));
  const questionPanel = panel('表达倾向', { subtitle: '问句与长度概览' },
    el('div', { class: 'metric-row' }, el('span', { text: '问句占比' }), el('strong', { text: `${data.question_rate}%` }), el('span', { class: 'muted small', text: `${data.question_count} 条` })),
    el('div', { class: 'metric-row' }, el('span', { text: '模板化率' }), el('strong', { text: `${data.template_rate}%` }), statusBadge(data.template_rate < 3, '良好', '需关注')),
    el('div', { class: 'metric-row' }, el('span', { text: '复读率' }), el('strong', { text: `${data.repeat_rate}%` }), statusBadge(data.repeat_rate < 5, '良好', '需关注')),
    el('div', { class: 'metric-row' }, el('span', { text: '平均长度' }), el('strong', { text: `${data.avg_length} 字` }), el('span', { class: 'muted small', text: '目标约 12–30 字' })),
    notice('这是规则统计，不是模型评分；只扫描 pending 中的助手回复，记忆固化后原文不再逐条统计。模板/复读上升时，优先检查提示词与最近记忆注入量，而不是继续加语气词。', 'info'));

  const reasonTone = (reason) => {
    if (reason === '复读') return 'danger';
    if (reason === '过短' || reason === '过长') return 'outline';
    return 'warn';
  };
  const exampleRows = data.examples.map((item) => el('tr', { style: { cursor: 'pointer' }, onclick: () => ctx.navigate(`sessions/${item.kind}/${item.key}`) },
    el('td', null, badge(item.kind === 'group' ? '群聊' : '私聊', item.kind === 'group' ? 'info' : 'outline'), el('div', { class: 'mono muted', text: item.key })),
    el('td', { class: 'muted-cell', text: stamp(item.time) }),
    el('td', null, el('div', { class: 'row' }, ...item.reasons.map((reason) => badge(reason, reasonTone(reason))))),
    el('td', { class: 'break', text: item.text.length > 140 ? `${item.text.slice(0, 140)}…` : item.text }),
    el('td', { class: 'num', text: `${item.length} 字` })));
  const examplesPanel = panel('近期被标记的回复', { subtitle: `最多展示 ${data.examples.length} 条；点击行可查看会话` },
    exampleRows.length
      ? el('div', { class: 'table-wrap' }, el('table', { class: 'data-table' },
        el('thead', null, el('tr', null, el('th', { text: '会话' }), el('th', { text: '时间' }), el('th', { text: '原因' }), el('th', { text: '回复' }), el('th', { text: '长度' }))),
        el('tbody', null, ...exampleRows)))
      : emptyState('没有被标记的回复', '当前规则下没有发现明显的模板化、复读或长度异常。', 'check'));

  const byKeyRows = data.by_key.map((item) => el('tr', null,
    el('td', null, badge(item.kind === 'group' ? '群聊' : '私聊', item.kind === 'group' ? 'info' : 'outline')),
    el('td', { class: 'mono', text: item.key }),
    el('td', { text: num(item.replies) }),
    el('td', null, item.flagged ? badge(`${item.flagged} 条`, item.flagged / Math.max(1, item.replies) > 0.1 ? 'warn' : 'outline') : '—')));
  const byKeyPanel = panel('按会话分布', { subtitle: '被标记条数最多的会话' },
    byKeyRows.length
      ? el('div', { class: 'table-wrap' }, el('table', { class: 'data-table' },
        el('thead', null, el('tr', null, el('th', { text: '类型' }), el('th', { text: '会话' }), el('th', { text: '回复数' }), el('th', { text: '被标记' }))),
        el('tbody', null, ...byKeyRows)))
      : emptyState('暂无数据', '', 'sessions'));

  const errors = data.errors || [];
  host.append(stats, el('div', { class: 'grid grid-2' }, lengthPanel, questionPanel),
    examplesPanel, byKeyPanel);
  if (errors.length) {
    host.append(notice(`有 ${errors.length} 个记忆文件未能解析：${errors.slice(0, 3).join('；')}`, 'warn'));
  }
}

/* ==========================================================================
 * Social capabilities
 * ========================================================================== */
async function socialPage(host, ctx) {
  const data = await api('chat/social');
  const qzone = data.qzone || {};
  const comments = qzone.comments || {};
  const isOwner = ctx.user?.role === 'owner';
  const checkComments = el('button', { class: 'button secondary small', type: 'button' }, icon('refresh', 14), '检查评论');
  checkComments.disabled = !isOwner || !qzone.enabled;
  checkComments.addEventListener('click', async () => {
    busy(checkComments, true, '检查中…');
    try {
      const result = await api('chat/qzone/comments/check', { method: 'POST', timeout: 60000 });
      toast(`新评论 ${result.new || 0} 条，回复 ${result.replied || 0} 条`, result.errors?.length ? 'warn' : 'ok');
      ctx.rerender();
    } catch (error) { toast(errorText(error), 'error'); } finally { busy(checkComments, false); }
  });
  const postButton = el('button', { class: 'button primary small', type: 'button' }, icon('send', 14), '立即发一条');
  postButton.disabled = !isOwner || !qzone.enabled;
  postButton.title = qzone.enabled ? '手动生成并发布一条空间说说' : '请先在 chat 插件配置中开启 CHAT_QZONE_ENABLED';
  postButton.addEventListener('click', async () => {
    const ok = await ctx.confirm('将让 Yuna 生成一条简短的 QQ 空间说说并立即发布，请确认内容边界。', {
      title: '发布空间动态', confirmText: '发布', danger: false,
    });
    if (!ok) return;
    busy(postButton, true, '生成中…');
    try {
      const result = await api('chat/qzone/post', { method: 'POST', timeout: 120000 });
      toast(`已发布：${result.content?.slice(0, 40) || '空间说说'}`, 'ok');
      ctx.rerender();
    } catch (error) { toast(errorText(error), 'error'); } finally { busy(postButton, false); }
  });
  const stats = el('div', { class: 'stat-grid' },
    statCard({ label: '资料卡', value: num(data.profiles), hint: `${data.with_groups} 位有最近群记录`, icon: 'users', accent: true }),
    statCard({ label: '生日资料', value: num(data.with_birthday), hint: '来自好友资料或手动填写', icon: 'heart' }),
    statCard({ label: '今天生日', value: num(data.today?.length || 0), hint: `${data.birthday_hour ?? 9}:00 后自动发送祝福`, icon: 'sparkles' }),
    statCard({ label: 'QQ 空间', value: qzone.enabled ? '已开启' : '未开启', hint: qzone.enabled ? `每日上限 ${qzone.daily_limit} 条` : '默认关闭，按需开启', icon: 'image' }),
    statCard({ label: '自动点赞', value: data.profile_like_enabled ? '已开启' : '手动可用', hint: '点赞资料卡', icon: 'smile' }),
    statCard({ label: '机器人名单', value: num(data.bots), hint: '用于区分真人与其它 AI', icon: 'users' }));

  const birthdayRows = (data.birthdays || []).map((item) => el('tr', { style: { cursor: 'pointer' }, onclick: () => ctx.navigate(`users/${encodeURIComponent(item.qq)}`) },
    el('td', null, el('div', { class: 'user-cell' }, el('span', { class: 'avatar', text: String(item.nickname || item.qq).slice(0, 1) }),
      el('div', { class: 'user-cell-text' }, el('strong', { text: item.nickname || item.qq }), el('small', { text: item.qq })))),
    el('td', null, item.today ? badge('今天', 'accent') : badge(item.birthday, 'outline')),
    el('td', { text: num(item.groups) }),
    el('td', null, item.eligible ? badge('可祝福', 'ok') : badge('关系不足', 'outline'))));
  const birthdayPanel = panel('生日日历', { subtitle: `只有最近出现过的群且关系达到「眼熟」以上才会收到祝福；每天一次`, actions: el('a', { class: 'chip', href: '#/users' }, '用户列表', icon('arrow-right', 12)) },
    birthdayRows.length
      ? el('div', { class: 'table-wrap' }, el('table', { class: 'data-table birthday-table' },
        el('thead', null, el('tr', null, el('th', { text: '用户' }), el('th', { text: '生日' }), el('th', { text: '群记录' }), el('th', { text: '生日资格' }))),
        el('tbody', null, ...birthdayRows)))
      : emptyState('还没有生日资料', '好友资料同步或手动填写生日后，会在这里显示。', 'heart'));

  const postRows = (qzone.posts || []).map((post) => el('div', { class: 'timeline-item' },
    el('span', { class: 'timeline-time', text: post.date || stamp(post.time) }),
    el('span', { class: 'timeline-body', text: post.content || '—' }),
    post.kind && post.kind !== 'normal' ? badge(post.kind === 'home_event' ? '作品' : post.kind === 'outside' ? '外出' : '睡前', 'outline') : null));
  const qzonePanel = panel('QQ 空间', { subtitle: '主动说说是概率行为；评论回复轮询自己的说说', actions: el('div', { class: 'row' }, checkComments, postButton) },
    el('div', { class: 'metric-row' }, el('span', { text: '状态' }), el('strong', { text: qzone.enabled ? '已开启' : '未开启' }), statusBadge(qzone.enabled, '运行中', '关闭')),
    el('div', { class: 'metric-row' }, el('span', { text: '活跃时间' }), el('strong', { text: `${qzone.start_hour}:00 – ${qzone.end_hour}:00` }), el('span')),
    el('div', { class: 'metric-row' }, el('span', { text: '当前概率' }), el('strong', { text: `${Math.round((qzone.current_probability || 0) * 100)}%` }), el('span', { class: 'muted small', text: `2h 0% → 96h ${Math.round((qzone.max_probability || 0.8) * 100)}%` })),
    el('div', { class: 'metric-row' }, el('span', { text: 'AI 配图' }), el('strong', { text: qzone.image_enabled ? '已配置' : '未配置' }), el('span', { class: 'muted small', text: qzone.image_enabled ? `普通 ${Math.round((qzone.image_probability || 0.35) * 100)}%` : '需要配置图片 API' })),
    el('div', { class: 'metric-row' }, el('span', { text: '上次发布' }), el('strong', { text: qzone.last_post ? timeAgo(qzone.last_post) : '从未' }), el('span')),
    el('div', { class: 'metric-row' }, el('span', { text: '评论回复' }), el('strong', { text: qzone.comment_enabled ? '已开启' : '未开启' }), statusBadge(qzone.comment_enabled, `${comments.replied_today || 0} 条今日${comments.max_per_day ? ` / ${comments.max_per_day}` : ' · 不限'}`, '关闭')),
    el('div', { class: 'metric-row' }, el('span', { text: '检查节奏' }), el('strong', { text: comments.hot ? `快速 · ${qzone.comment_hot_interval || 30}s` : `${qzone.comment_interval || 2} 分钟` }), el('span')),
    el('div', { class: 'metric-row' }, el('span', { text: '单轮上限' }), el('strong', { text: comments.max_per_tick ? `${comments.max_per_tick} 条` : '不限' }), el('span')),
    el('div', { class: 'metric-row' }, el('span', { text: '上次检查' }), el('strong', { text: comments.last_check ? timeAgo(comments.last_check) : '尚未检查' }), el('span')),
    comments.last_comment ? notice(`最近评论：${comments.last_comment}`, 'info') : null,
    comments.last_reply_text ? notice(`最近回复：${comments.last_reply_text}`, 'ok') : null,
    qzone.last_error ? notice(`上次空间发布失败：${qzone.last_error}`, 'warn') : null,
    comments.last_error ? notice(`空间评论检查失败：${comments.last_error}`, 'warn') : null,
    postRows.length
      ? el('div', { class: 'timeline', style: { marginTop: '10px' } }, ...postRows)
      : emptyState('还没有发过说说', qzone.enabled ? '等待下一次概率触发，或手动发一条。' : '在插件配置中开启 CHAT_QZONE_ENABLED 后才会主动发布。', 'image'));

  const infoPanel = panel('资料与隐私', { subtitle: '只使用公开资料，不读取私密内容' },
    notice('资料卡来自 OneBot 的公开接口（昵称、性别、年龄、签名、等级、群名片、好友生日字段）。不会读取聊天记录以外的隐私，也不会把资料逐条念出来。', 'info'),
    el('p', { class: 'muted small', text: '资料刷新：每 48 小时最多一次；好友生日每天早上同步；生日祝福只发到最近 60 天出现过的白名单群。' }));

  host.append(stats, el('div', { class: 'grid grid-2' }, birthdayPanel, qzonePanel), infoPanel);
}

/* ==========================================================================
 * Bot identification
 * ========================================================================== */
async function botsPage(host, ctx) {
  const data = await api('chat/bots');
  const listHost = el('div');
  const qqInput = el('input', { class: 'input', inputmode: 'numeric', placeholder: 'QQ 号' });
  const nameInput = el('input', { class: 'input', placeholder: '昵称 / 备注' });
  const kindSelect = el('select', { class: 'select' }, el('option', { value: 'true' }, '机器人 / 其它 AI'), el('option', { value: 'false' }, '普通用户'));
  const addButton = el('button', { class: 'button primary', type: 'button' }, icon('plus', 14), '加入名单');
  addButton.addEventListener('click', async () => {
    const qq = qqInput.value.trim();
    if (!/^\d{5,20}$/.test(qq)) { toast('请输入 5–20 位 QQ 号', 'error'); return; }
    busy(addButton, true, '保存中…');
    try {
      await api('chat/bots', { method: 'POST', body: { qq, name: nameInput.value.trim(), is_bot: kindSelect.value === 'true', source: 'manual' } });
      toast('名单已更新', 'ok');
      ctx.rerender();
    } catch (error) { toast(errorText(error), 'error'); } finally { busy(addButton, false); }
  });

  function renderList() {
    clear(listHost);
    const rows = data.items.map((item) => {
      const toggle = el('button', { class: `button ${item.is_bot ? 'danger' : 'secondary'} xs`, type: 'button' }, item.is_bot ? '标记为真人' : '标记为机器人');
      toggle.addEventListener('click', async () => {
        busy(toggle, true, '保存中…');
        try {
          await api('chat/bots', { method: 'POST', body: { qq: item.qq, name: item.name, is_bot: !item.is_bot, source: 'manual' } });
          toast('名单已更新', 'ok');
          ctx.rerender();
        } catch (error) { toast(errorText(error), 'error'); } finally { busy(toggle, false); }
      });
      const remove = iconButton('trash', '移除名单', async () => {
        const ok = await ctx.confirm(`从机器人名单中移除 ${item.name || item.qq}？`, { title: '移除名单', confirmText: '移除', danger: true });
        if (!ok) return;
        try {
          await api(`chat/bots/${encodeURIComponent(item.qq)}`, { method: 'DELETE' });
          toast('已移除', 'ok');
          ctx.rerender();
        } catch (error) { toast(errorText(error), 'error'); }
      });
      return el('tr', null,
        el('td', null, el('div', { class: 'user-cell' }, el('span', { class: 'avatar', text: String(item.name || item.qq).slice(0, 1) }),
          el('div', { class: 'user-cell-text' }, el('strong', { text: item.name || '—' }), el('small', { text: item.qq })))),
        el('td', null, item.is_bot ? badge('机器人 / AI', 'warn') : badge('真人', 'outline')),
        el('td', null, badge(item.source === 'auto' ? '自动识别' : '人工登记', item.source === 'auto' ? 'info' : 'outline')),
        el('td', { class: 'muted-cell', text: item.time ? stamp(item.time) : '—' }),
        el('td', { class: 'actions' }, el('div', { class: 'row end' }, toggle, remove)));
    });
    listHost.append(panel('名单', { subtitle: `${data.items.length} 个账号` },
      rows.length ? el('div', { class: 'table-wrap' }, el('table', { class: 'data-table' },
        el('thead', null, el('tr', null, el('th', { text: '账号' }), el('th', { text: '判定' }), el('th', { text: '来源' }), el('th', { text: '登记时间' }), el('th'))),
        el('tbody', null, ...rows))) : emptyState('名单为空', '自动识别或手动登记机器人账号。', 'users')));
  }
  renderList();

  const candidateCards = (data.candidates || []).map((item) => {
    const button = el('button', { class: 'button secondary xs', type: 'button' }, icon('plus', 12), '标记为机器人');
    button.addEventListener('click', async () => {
      busy(button, true, '保存中…');
      try {
        await api('chat/bots', { method: 'POST', body: { qq: item.qq, name: item.name, is_bot: true, source: 'auto' } });
        toast(`已标记 ${item.name || item.qq}`, 'ok');
        ctx.rerender();
      } catch (error) { toast(errorText(error), 'error'); } finally { busy(button, false); }
    });
    return el('div', { class: 'plugin-card' },
      el('div', { class: 'plugin-identity' }, el('span', { class: 'avatar', text: String(item.name || item.qq).slice(0, 1) }),
        el('div', null, el('h3', { text: item.name || item.qq }), el('small', { text: item.qq }))),
      el('p', { class: 'plugin-desc', text: '昵称特征疑似机器人 / AI，尚未登记。' }), button);
  });
  const candidatePanel = panel('疑似机器人', { subtitle: '按昵称关键词自动发现，确认后会进入名单' },
    candidateCards.length ? el('div', { class: 'plugin-grid' }, ...candidateCards) : emptyState('没有待确认的候选', '昵称命中关键词的用户会出现在这里。', 'users'));

  host.append(el('div', { class: 'page-head' },
    el('div', null, el('h2', { text: '机器人识别' }), el('p', { text: '名单中的账号只进入滚动上下文，不写入长期记忆、不学习说话风格。' })),
    el('div', { class: 'page-actions' }, badge(`${data.items.length} 个已登记`, 'outline'))),
    panel('快速登记', { subtitle: '手动加入或纠正判定' }, el('div', { class: 'inline-form two' },
      field('QQ 号', qqInput), field('昵称 / 备注', nameInput), el('div', null, field('判定', kindSelect), addButton))),
    listHost, candidatePanel);
}

/* ==========================================================================
 * Evaluation set（对话评测集）
 * ========================================================================== */
const EVAL_LABELS = [
  ['wrong_target', '接错人'],
  ['repetitive', '复读'],
  ['template', '过度模板'],
  ['wrong_alias', '越界称呼'],
  ['fabrication', '编造经历'],
];
const EVAL_TRIGGERS = { mention: '@', reply: '引用', private: '私聊', random: '插话', cold: '冷场', keyword: '关键词' };

async function evalPage(host, ctx) {
  const data = await api('chat/eval');
  const stats = data.stats || {};
  const state = ctx.state(() => ({ query: '', labeled: 'all', page: 1 }));
  const listHost = el('div');
  const searchSlot = el('div', { class: 'emote-search-slot' });

  const labelCounts = stats.label_counts || {};
  const labelNames = stats.label_names || {};
  const statBits = el('div', { class: 'ledger-stats' },
    el('div', { class: 'ledger-stat' }, el('small', { text: '样本总数' }),
      el('strong', { text: num(stats.total || 0) }),
      el('small', { class: 'muted', text: `已标注 ${num(stats.labeled || 0)}` })),
    el('div', { class: 'ledger-stat' }, el('small', { text: '自动模板化' }),
      el('strong', { text: num(stats.auto_template || 0) }),
      el('small', { class: 'muted', text: '规则命中 AI 腔' })),
    el('div', { class: 'ledger-stat' }, el('small', { text: '自动复读' }),
      el('strong', { text: num(stats.auto_repeat || 0) }),
      el('small', { class: 'muted', text: '与上下文相似度 ≥ 0.78' })),
    el('div', { class: 'ledger-stat' }, el('small', { text: '平均长度' }),
      el('strong', { text: num(stats.avg_length || 0) }),
      el('small', { class: 'muted', text: '目标 12–30 字' })),
    el('div', { class: 'ledger-stat' }, el('small', { text: '人工标注问题' }),
      el('strong', { text: num(Object.values(labelCounts).reduce((a, b) => a + Number(b || 0), 0)) }),
      el('small', { class: 'muted', text: Object.entries(labelCounts).filter(([, v]) => Number(v) > 0).map(([k, v]) => `${labelNames[k] || k} ${v}`).join(' · ') || '还没有问题标注' })));

  const replayAll = el('button', { class: 'button secondary small', type: 'button' }, icon('refresh', 14), '跑回归（最近样本）');
  replayAll.addEventListener('click', async () => {
    const countInput = el('input', { class: 'input', type: 'number', value: '10', min: '1', max: '30' });
    modal({
      title: '重放评测样本',
      subtitle: '用当前提示词/模型重放样本场景，对比新旧回复；重放使用现在的记忆与心情，结论用于对比改动前后',
      body: field('重放条数', countInput, { hint: '每次重放都会真实调用模型（1–30 条）' }),
      actions: [
        { label: '取消', className: 'secondary', onClick: (close) => close() },
        { label: '开始重放', className: 'primary', onClick: async (close) => {
          const limit = Math.max(1, Math.min(30, Number(countInput.value) || 10));
          close();
          busy(replayAll, true, '重放中…');
          try {
            const result = await api('chat/eval/replay', { method: 'POST', body: { limit }, timeout: 300000 });
            const failed = (result.results || []).filter((row) => !row.ok).length;
            toast(`已重放 ${result.ran} 条${failed ? `，失败 ${failed} 条` : ''}`, failed ? 'warn' : 'ok');
            ctx.rerender();
          } catch (error) { toast(errorText(error), 'error'); } finally { busy(replayAll, false); }
        } },
      ],
    });
  });

  const exportCsv = el('button', { class: 'button secondary small', type: 'button', onclick: () => downloadCSV('yuna-eval.csv', [
    { label: '时间', key: 'time', csv: (row) => stamp(row.time) },
    { label: '场景', key: 'kind' }, { label: '群/QQ', key: 'key' }, { label: '触发者', key: 'qq' },
    { label: '触发类型', key: 'trigger' }, { label: '触发消息', key: 'text' }, { label: '当时回复', key: 'reply' },
    { label: '标注', key: 'labels', csv: (row) => EVAL_LABELS.filter(([key]) => row.labels?.[key]).map(([, name]) => name).join('、') },
    { label: '备注', key: 'note' },
    { label: '回归回复', key: 'regression', csv: (row) => row.regression?.reply || '' },
  ], data.items) }, icon('download', 14), '导出');

  host.append(el('div', { class: 'page-head' },
    el('div', null, el('h2', { text: '对话评测集' }), el('p', { text: '按采样率保存真实触发场景；标注问题类型后，提示词/模型变更可回归对比。' })),
    el('div', { class: 'page-actions' }, replayAll, exportCsv)),
    panel('基线统计', { subtitle: '自动规则检查 + 人工标注汇总' }, statBits));

  const statusBar = el('div', { class: 'pill-row' });
  [['all', '全部'], ['no', '待标注'], ['flagged', '有问题'], ['yes', '已标注']].forEach(([value, label]) => {
    statusBar.append(el('button', {
      type: 'button', class: `chip${state.labeled === value ? ' active' : ''}`,
      onclick: (event) => {
        state.labeled = value;
        state.page = 1;
        statusBar.querySelectorAll('.chip').forEach((node) => node.classList.toggle('active', node === event.currentTarget));
        renderList();
      },
    }, label));
  });
  host.append(el('div', { class: 'toolbar between' }, searchSlot, statusBar), listHost);
  searchSlot.append(searchField('搜索 QQ、群号、消息或备注…', (value) => { state.query = value; state.page = 1; renderList(); }, state.query));

  function labelBadges(sample) {
    const flagged = EVAL_LABELS.filter(([key]) => sample.labels?.[key]);
    if (!flagged.length && !sample.labeled) return badge('待标注', 'outline');
    if (!flagged.length) return badge('正常', 'ok');
    return flagged.map(([, name]) => badge(name, 'danger'));
  }

  function regressionBlock(sample) {
    const reg = sample.regression;
    if (!reg) return null;
    const checks = reg.checks || {};
    return el('div', { class: 'eval-regression' },
      el('div', { class: 'row', style: { gap: '6px', flexWrap: 'wrap' } },
        badge(reg.changed ? '回复已变化' : '回复一致', reg.changed ? 'warn' : 'ok'),
        badge(`重放于 ${timeAgo(reg.time)}`, 'outline'),
        checks.template ? badge('模板化', 'danger') : null,
        checks.repetitive ? badge('复读', 'danger') : null,
        checks.length ? badge(`${checks.length} 字`, 'outline') : null),
      el('p', { class: 'muted small', style: { margin: '6px 0 0' }, text: `重放回复：${reg.reply || '—'}` }));
  }

  function renderList() {
    const q = state.query.toLowerCase();
    const items = (data.items || []).filter((row) => {
      if (state.labeled === 'yes' && !row.labeled) return false;
      if (state.labeled === 'no' && row.labeled) return false;
      if (state.labeled === 'flagged' && !EVAL_LABELS.some(([key]) => row.labels?.[key])) return false;
      return !q || `${row.qq} ${row.key} ${row.text} ${row.reply} ${row.note || ''}`.toLowerCase().includes(q);
    });
    clear(listHost);
    if (!items.length) {
      listHost.append(panel('样本列表', {}, emptyState('还没有匹配的评测样本', 'Yuna 每次成功回复都有概率被采样保存，聊几天就会积累起来。', 'check')));
      return;
    }
    const body = el('tbody');
    const page = pageSlice(items, state.page, 20);
    state.page = page.page;
    page.rows.forEach((sample) => {
      const contextToggle = el('button', { class: 'button ghost xs', type: 'button' }, icon('eye', 12), '上下文');
      const contextRow = el('tr', { class: 'eval-context-row', style: { display: 'none' } },
        el('td', { colspan: '6' },
          el('div', { class: 'timeline', style: { maxHeight: '280px', overflowY: 'auto' } },
            ...(sample.context || []).map((turn) => el('div', { class: 'timeline-item' },
              el('span', { class: 'timeline-time', text: turn.role === 'assistant' ? 'Yuna' : (turn.name || turn.qq || '群友') }),
              el('span', { class: 'timeline-body', text: (turn.content || '') + (turn.images ? ` [图片×${turn.images}]` : '') + (turn.recalled ? '（已撤回）' : '') }))),
            el('div', { class: 'timeline-item' },
              el('span', { class: 'timeline-time', text: '触发' }),
              el('span', { class: 'timeline-body', text: `${sample.text}（${EVAL_TRIGGERS[sample.trigger] || sample.trigger}，来自 ${sample.qq}）` })))));
      contextToggle.addEventListener('click', () => {
        const open = contextRow.style.display !== 'none';
        contextRow.style.display = open ? 'none' : '';
        clear(contextToggle);
        contextToggle.append(icon(open ? 'eye' : 'close', 12), open ? '上下文' : '收起');
      });
      const replayOne = el('button', { class: 'button ghost xs', type: 'button' }, icon('refresh', 12), '重放');
      replayOne.addEventListener('click', async () => {
        busy(replayOne, true, '重放中…');
        try {
          const result = await api('chat/eval/replay', { method: 'POST', body: { sample_ids: [sample.id] }, timeout: 120000 });
          const row0 = (result.results || [])[0];
          if (row0?.ok) toast(row0.changed ? '重放完成，回复有变化' : '重放完成，回复一致', row0.changed ? 'warn' : 'ok');
          else toast(row0?.error || '重放失败', 'error');
          ctx.rerender();
        } catch (error) { toast(errorText(error), 'error'); } finally { busy(replayOne, false); }
      });
      const labelButton = el('button', { class: 'button secondary xs', type: 'button' }, icon('edit', 12), '标注');
      labelButton.addEventListener('click', () => openEvalLabelDialog(sample, ctx));
      const del = iconButton('trash', '删除样本', async () => {
        const ok = await confirmDialog('删除这条评测样本？', { title: '删除样本', confirmText: '删除', danger: true });
        if (!ok) return;
        try { await api(`chat/eval/${sample.id}`, { method: 'DELETE' }); toast('样本已删除', 'ok'); ctx.rerender(); }
        catch (error) { toast(errorText(error), 'error'); }
      });
      body.append(
        el('tr', null,
          el('td', { class: 'muted-cell' }, el('span', { text: stampShort(sample.time) }), el('small', { class: 'muted', text: ` ${timeAgo(sample.time)}`, style: { display: 'block' } })),
          el('td', null,
            el('div', { class: 'user-cell-text' },
              el('strong', { text: `${sample.kind === 'group' ? '群' : '私聊'} ${sample.key}` }),
              el('small', { text: `${EVAL_TRIGGERS[sample.trigger] || sample.trigger} · ${sample.qq}` }))),
          el('td', { class: 'break', text: sample.text || '（空消息/图片）' }),
          el('td', { class: 'break', text: sample.reply || '—' }),
          el('td', null, labelBadges(sample)),
          el('td', { class: 'actions' }, el('div', { class: 'row end' }, contextToggle, replayOne, labelButton, del))),
        contextRow);
      if (sample.regression) {
        body.append(el('tr', { class: 'eval-context-row' },
          el('td', { colspan: '6' }, regressionBlock(sample))));
      }
    });
    listHost.append(panel('样本列表', { subtitle: `${items.length} 条匹配` },
      el('div', { class: 'table-wrap' }, el('table', { class: 'data-table' },
        el('thead', null, el('tr', null, el('th', { text: '时间' }), el('th', { text: '场景' }), el('th', { text: '触发消息' }),
          el('th', { text: '当时回复' }), el('th', { text: '标注' }), el('th', { text: '操作' }))),
        body))));
    listHost.append(pagerControls({ page: page.page, pageCount: page.pageCount, total: page.total, onPage: (value) => { state.page = value; renderList(); }, unit: '条' }));
  }
  renderList();
}

function openEvalLabelDialog(sample, ctx) {
  const boxes = {};
  const boxRow = el('div', { class: 'stack-sm' }, ...EVAL_LABELS.map(([key, name]) => {
    const box = el('input', { type: 'checkbox' });
    box.checked = Boolean(sample.labels?.[key]);
    boxes[key] = box;
    const wrap = el('label', { class: 'switch-row', style: { cursor: 'pointer' } },
      box, el('span', { text: name }));
    return wrap;
  }));
  const note = el('textarea', { class: 'textarea', rows: '3', placeholder: '补充说明（可选），例如接错人的原因' });
  note.value = sample.note || '';
  modal({
    title: '标注评测样本',
    subtitle: `${stampShort(sample.time)} · ${sample.text?.slice(0, 40) || ''}`,
    body: el('div', null,
      el('p', { class: 'muted small', text: `Yuna 当时回复：${sample.reply || '—'}` }),
      boxRow, field('备注', note)),
    actions: [
      { label: '取消', className: 'secondary', onClick: (close) => close() },
      { label: '保存标注', className: 'primary', onClick: async (close) => {
        const labels = {};
        EVAL_LABELS.forEach(([key]) => { labels[key] = boxes[key].checked; });
        try {
          await api(`chat/eval/${sample.id}/label`, { method: 'POST', body: { labels, note: note.value.trim() } });
          close();
          toast('标注已保存', 'ok');
          ctx.rerender();
        } catch (error) { toast(errorText(error), 'error'); }
      } },
    ],
  });
}

const RELATION_KINDS = { friend: '朋友', address: '称呼', couple: '亲密', family: '家人', rival: '不对付', other: '其他' };

function relationGraphSvg(items) {
  /* 力导向有向图：节点=用户（大小=关系数），边=a→b 带箭头，颜色=关系类型 */
  if (!items.length) return emptyState('还没有关系数据', '点击「从群聊记录重新推断」从群聊发言与记忆自动生成，或在编辑里手动添加。', 'users');
  const KIND_COLOR = {
    friend: 'var(--accent)', address: '#5a9fe0', couple: '#de6b8c',
    family: '#e0a23d', rival: '#e05252', colleague: '#36b79b', online: '#9a8fb8', other: 'var(--muted)',
  };
  const counts = {};
  items.forEach((row) => { counts[row.a] = (counts[row.a] || 0) + 1; counts[row.b] = (counts[row.b] || 0) + 1; });
  const nodes = Object.keys(counts).sort((x, y) => y[1] - x[1]).slice(0, 24);
  const nodeSet = new Set(nodes);
  const edges = items.filter((row) => nodeSet.has(row.a) && nodeSet.has(row.b));
  const nick = {};
  items.forEach((row) => {
    if (nodeSet.has(row.a)) nick[row.a] = row.nickname_a || nick[row.a] || row.a;
    if (nodeSet.has(row.b)) nick[row.b] = row.nickname_b || nick[row.b] || row.b;
  });

  // 简易力导向：初始圆周，斥力 + 边弹簧，迭代收敛
  const W = 680, H = 520, cx = W / 2, cy = H / 2;
  const pos = {}, vel = {};
  nodes.forEach((qq, i) => {
    const ang = (Math.PI * 2 * i) / nodes.length;
    const r = 170 + (i % 3) * 26;
    pos[qq] = [cx + Math.cos(ang) * r, cy + Math.sin(ang) * r];
    vel[qq] = [0, 0];
  });
  const linkLen = Math.min(150, 320 / Math.sqrt(Math.max(1, nodes.length)));
  for (let iter = 0; iter < 160; iter += 1) {
    const force = {};
    nodes.forEach((q) => { force[q] = [0, 0]; });
    for (let i = 0; i < nodes.length; i += 1) {
      for (let j = i + 1; j < nodes.length; j += 1) {
        const [ax, ay] = pos[nodes[i]];
        const [bx, by] = pos[nodes[j]];
        let dx = ax - bx, dy = ay - by;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { d2 = 1; dx = 1; }
        const d = Math.sqrt(d2);
        const rep = 26000 / d2;
        const fx = (dx / d) * rep, fy = (dy / d) * rep;
        force[nodes[i]][0] += fx; force[nodes[i]][1] += fy;
        force[nodes[j]][0] -= fx; force[nodes[j]][1] -= fy;
      }
    }
    edges.forEach((row) => {
      const [ax, ay] = pos[row.a];
      const [bx, by] = pos[row.b];
      const dx = bx - ax, dy = by - ay;
      const d = Math.max(1, Math.hypot(dx, dy));
      const pull = (d - linkLen) * 0.06;
      const fx = (dx / d) * pull, fy = (dy / d) * pull;
      force[row.a][0] += fx; force[row.a][1] += fy;
      force[row.b][0] -= fx; force[row.b][1] -= fy;
    });
    nodes.forEach((qq) => {
      vel[qq][0] = (vel[qq][0] + force[qq][0]) * 0.82;
      vel[qq][1] = (vel[qq][1] + force[qq][1]) * 0.82;
      pos[qq][0] = Math.min(W - 46, Math.max(46, pos[qq][0] + vel[qq][0]));
      pos[qq][1] = Math.min(H - 40, Math.max(40, pos[qq][1] + vel[qq][1]));
    });
  }

  const wrap = document.createElement('div');
  wrap.className = 'relation-graph-wrap';
  const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'chart-svg relation-svg', role: 'img' });
  const defs = svg('defs');
  Object.entries(KIND_COLOR).forEach(([kind, color]) => {
    const marker = svg('marker', {
      id: `arrow-${kind}`, viewBox: '0 0 10 10', refX: 9, refY: 5,
      markerWidth: 7, markerHeight: 7, orient: 'auto-start-reverse',
    });
    const path = svg('path', { d: 'M 0 1 L 10 5 L 0 9 z', fill: color });
    marker.append(path);
    defs.append(marker);
  });
  root.append(defs);
  edges.forEach((row) => {
    const [x1, y1] = pos[row.a];
    const [x2, y2] = pos[row.b];
    const kind = row.kind || 'other';
    const dx = x2 - x1, dy = y2 - y1;
    const d = Math.max(1, Math.hypot(dx, dy));
    const rA = Math.min(13, 6 + counts[row.a]);
    const trim = rA + 6;
    const line = svg('line', {
      x1: x1 + (dx / d) * trim, y1: y1 + (dy / d) * trim,
      x2: x2 - (dx / d) * trim, y2: y2 - (dy / d) * trim,
      class: 'relation-edge', stroke: KIND_COLOR[kind] || KIND_COLOR.other,
      'marker-end': `url(#arrow-${kind})`,
      'stroke-dasharray': kind === 'address' ? '5 3' : '',
    });
    line.addEventListener('mouseenter', () => {
      toast(`${nick[row.a]} → ${nick[row.b]}：${row.label || RELATION_KINDS[kind] || kind}`, 'info', 5000);
    });
    root.append(line);
  });
  nodes.forEach((qq) => {
    const [x, y] = pos[qq];
    const r = Math.min(13, 6 + counts[qq]);
    const g = svg('g');
    const dot = svg('circle', { cx: x, cy: y, r, class: 'relation-node' });
    const label = svg('text', {
      x, y: y + r + 13, class: 'relation-label', 'text-anchor': 'middle',
    });
    label.textContent = nick[qq] || qq;
    g.append(dot, label);
    root.append(g);
  });
  wrap.append(root);
  return wrap;
}

async function relationsPage(host, ctx) {
  const data = await api('chat/relations');

  const inferButton = el('button', { class: 'button secondary small', type: 'button' }, icon('bolt', 13), '从群聊记录重新推断');
  inferButton.addEventListener('click', async () => {
    busy(inferButton, true, '扫描群聊记忆…');
    try {
      const result = await api('chat/relations/infer', { method: 'POST', timeout: 120000 });
      toast(`推断完成：新增 ${result.added || 0} 条（扫描 ${result.scanned_files || 0} 个群）`, 'ok');
      ctx.rerender();
    } catch (error) { toast(errorText(error), 'error'); } finally { busy(inferButton, false); }
  });

  const manageButton = el('button', { class: 'button primary small', type: 'button', onclick: () => openRelationEditor(data, ctx) }, icon('plus', 13), '新增 / 管理关系');

  const stats = data.stats || {};
  const byKind = stats.by_kind || {};
  const kindBits = el('div', { class: 'profile-badges' },
    Object.entries(RELATION_KINDS).map(([value, name]) => badge(`${name} ${byKind[value] || 0}`, byKind[value] ? '' : 'outline')));

  host.append(el('div', { class: 'page-head' },
    el('div', null, el('h2', { text: '关系图谱' }),
      el('p', { text: `共 ${stats.edges || 0} 条关系 / ${stats.nodes || 0} 人。固化时自动推断语义关系；点击按钮从全局群聊发言重建共现/提及关系。` })),
    el('div', { class: 'page-actions' }, inferButton, manageButton)),
    panel('关系图', { subtitle: '节点=用户，连线=关系；数据来源：称呼导入 / 群聊推断 / 固化语义 / 手动' },
      relationGraphSvg(data.items || []),
      el('div', { class: 'row', style: { marginTop: '10px', justifyContent: 'center' } }, kindBits)));
}

function openRelationEditor(data, ctx) {
  const aInput = el('input', { class: 'input', placeholder: 'QQ A', inputmode: 'numeric', maxlength: '12' });
  const bInput = el('input', { class: 'input', placeholder: 'QQ B', inputmode: 'numeric', maxlength: '12' });
  const kindSelect = el('select', { class: 'select' }, ...Object.entries(RELATION_KINDS).map(([value, name]) => el('option', { value, text: name })));
  const labelInput = el('input', { class: 'input', placeholder: '关系描述，如：A 叫 B「小黑」，两人是大学同学' });

  const manageHost = el('div', { class: 'stack-sm', style: { maxHeight: '240px', overflowY: 'auto' } });
  const renderManage = () => {
    clear(manageHost);
    const rows = data.items || [];
    if (!rows.length) {
      manageHost.append(el('p', { class: 'muted small', text: '还没有任何关系。' }));
      return;
    }
    rows.forEach((row) => {
      const del = iconButton('trash', '删除', async () => {
        try {
          await api(`chat/relations?a=${encodeURIComponent(row.a)}&b=${encodeURIComponent(row.b)}`, { method: 'DELETE' });
          toast('已删除', 'ok');
          closeModal();
          ctx.rerender();
        } catch (error) { toast(errorText(error), 'error'); }
      });
      manageHost.append(el('div', { class: 'switch-row' },
        el('span', { class: 'break', text: `${row.nickname_a || row.a} ⇄ ${row.nickname_b || row.b}：${row.label || RELATION_KINDS[row.kind] || ''}` }),
        el('span', { class: 'muted small', text: row.source || '' }),
        del));
    });
  };
  renderManage();

  const body = el('div', null,
    el('h3', { style: { margin: '0 0 8px', fontSize: '13px' }, text: '新增关系' }),
    el('div', { class: 'form-grid' }, field('QQ A', aInput), field('QQ B', bInput), field('类型', kindSelect)),
    field('关系描述', labelInput),
    el('div', { class: 'row end', style: { margin: '10px 0 16px' } },
      el('button', { class: 'button primary small', type: 'button', onclick: async () => {
        if (!/^\d{5,12}$/.test(aInput.value.trim()) || !/^\d{5,12}$/.test(bInput.value.trim())) { toast('两端都要填 5-12 位 QQ 号', 'error'); return; }
        if (aInput.value.trim() === bInput.value.trim()) { toast('两端不能是同一个人', 'error'); return; }
        try {
          await api('chat/relations', { method: 'PUT', body: { a: aInput.value.trim(), b: bInput.value.trim(), kind: kindSelect.value, label: labelInput.value.trim() } });
          toast('关系已保存', 'ok');
          closeModal();
          ctx.rerender();
        } catch (error) { toast(errorText(error), 'error'); }
      } }, icon('check', 13), '保存关系')),
    el('h3', { style: { margin: '0 0 8px', fontSize: '13px' }, text: `管理现有关系（${(data.items || []).length} 条）` }),
    manageHost);
  modal({ title: '关系图谱编辑', subtitle: '手动登记与维护；自动推断的关系也可在此删除', body, wide: true,
    actions: [{ label: '关闭', className: 'secondary', onClick: (close) => close() }] });
}

const MEM_KIND_META = {
  self: { label: '她自己', color: 'var(--accent)', shape: 'circle', r: 16 },
  user: { label: '用户', color: '#5a9fe0', shape: 'circle', r: 8 },
  bot: { label: '机器人', color: '#9a8fb8', shape: 'circle', r: 6 },
  group: { label: '群', color: '#e0a23d', shape: 'rect' },
  topic: { label: '话题', color: '#36b79b', shape: 'rect' },
};
const MEM_EDGE_COLOR = { favor: 'var(--accent)', member: '#e0a23d', topic: '#36b79b', relation: '#de6b8c' };

function memoryGraphSvg(nodes, edges) {
  if (!nodes.length) return emptyState('还没有记忆数据', '群聊互动后会逐渐形成图谱。', 'memory');
  const W = 760, H = 560, cx = W / 2, cy = H / 2;
  const pos = {}, vel = {};
  const center = nodes.find((n) => n.kind === 'self');
  nodes.forEach((n, i) => {
    if (n.kind === 'self') { pos[n.id] = [cx, cy]; }
    else {
      const ang = (Math.PI * 2 * i) / nodes.length;
      pos[n.id] = [cx + Math.cos(ang) * (150 + (i % 4) * 30), cy + Math.sin(ang) * (130 + (i % 3) * 26)];
    }
    vel[n.id] = [0, 0];
  });
  const adj = {};
  edges.forEach((e) => {
    if (!pos[e.a] || !pos[e.b]) return;
    (adj[e.a] = adj[e.a] || []).push(e.b);
    (adj[e.b] = adj[e.b] || []).push(e.a);
  });
  const deg = (id) => Math.min(6, (adj[id] || []).length);
  for (let iter = 0; iter < 180; iter += 1) {
    const force = {};
    nodes.forEach((n) => { force[n.id] = [0, 0]; });
    for (let i = 0; i < nodes.length; i += 1) {
      for (let j = i + 1; j < nodes.length; j += 1) {
        const A = nodes[i].id, B = nodes[j].id;
        let dx = pos[A][0] - pos[B][0], dy = pos[A][1] - pos[B][1];
        let d2 = dx * dx + dy * dy; if (d2 < 1) { d2 = 1; dx = 1; }
        const d = Math.sqrt(d2);
        const rep = (nodes[i].kind === 'self' || nodes[j].kind === 'self' ? 60000 : 26000) / d2;
        force[A][0] += (dx / d) * rep; force[A][1] += (dy / d) * rep;
        force[B][0] -= (dx / d) * rep; force[B][1] -= (dy / d) * rep;
      }
    }
    edges.forEach((e) => {
      if (!force[e.a] || !force[e.b]) return;
      const dx = pos[e.b][0] - pos[e.a][0], dy = pos[e.b][1] - pos[e.a][1];
      const d = Math.max(1, Math.hypot(dx, dy));
      const pull = (d - 120) * 0.05;
      force[e.a][0] += (dx / d) * pull; force[e.a][1] += (dy / d) * pull;
      force[e.b][0] -= (dx / d) * pull; force[e.b][1] -= (dy / d) * pull;
    });
    nodes.forEach((n) => {
      const id = n.id;
      vel[id][0] = (vel[id][0] + force[id][0]) * 0.8;
      vel[id][1] = (vel[id][1] + force[id][1]) * 0.8;
      if (n.kind !== 'self') {
        pos[id][0] = Math.min(W - 50, Math.max(50, pos[id][0] + vel[id][0]));
        pos[id][1] = Math.min(H - 40, Math.max(40, pos[id][1] + vel[id][1]));
      }
    });
  }
  const wrap = document.createElement('div');
  wrap.className = 'relation-graph-wrap';
  const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, class: 'chart-svg relation-svg', role: 'img' });
  const defs = svg('defs');
  Object.entries(MEM_EDGE_COLOR).forEach(([kind, color]) => {
    const marker = svg('marker', { id: `mem-arrow-${kind}`, viewBox: '0 0 10 10', refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: 'auto-start-reverse' });
    marker.append(svg('path', { d: 'M 0 1 L 10 5 L 0 9 z', fill: color }));
    defs.append(marker);
  });
  root.append(defs);
  edges.forEach((e) => {
    if (!pos[e.a] || !pos[e.b]) return;
    const [x1, y1] = pos[e.a], [x2, y2] = pos[e.b];
    const dx = x2 - x1, dy = y2 - y1;
    const d = Math.max(1, Math.hypot(dx, dy));
    const trim = (e.a === 'self' ? 20 : 10) + 4;
    const line = svg('line', {
      x1: x1 + (dx / d) * trim, y1: y1 + (dy / d) * trim,
      x2: x2 - (dx / d) * trim, y2: y2 - (dy / d) * trim,
      stroke: MEM_EDGE_COLOR[e.kind] || 'var(--line)', 'stroke-width': Math.min(3, 0.9 + (e.weight || 1) / 25),
      opacity: e.kind === 'favor' ? 0.9 : 0.5, 'marker-end': `url(#mem-arrow-${e.kind})`,
    });
    line.addEventListener('mouseenter', () => toast(e.label || e.kind, 'info', 4000));
    root.append(line);
  });
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  nodes.forEach((n) => {
    const [x, y] = pos[n.id];
    const meta = MEM_KIND_META[n.kind] || MEM_KIND_META.user;
    const g = svg('g', { style: 'cursor:pointer' });
    let shape;
    if (meta.shape === 'rect') {
      shape = svg('rect', { x: x - 7, y: y - 7, width: 14, height: 14, rx: 3, fill: meta.color, stroke: 'var(--surface)', 'stroke-width': 2 });
    } else {
      shape = svg('circle', { cx: x, cy: y, r: (meta.r || 8) + deg(n.id), fill: meta.color, stroke: 'var(--surface)', 'stroke-width': 2 });
    }
    const label = svg('text', { x, y: y + ((meta.r || 8) + deg(n.id)) + 13, class: 'relation-label', 'text-anchor': 'middle' });
    label.textContent = n.label || n.id;
    const detail = Object.entries(n.meta || {}).map(([k, v]) => `${k}: ${v}`).join(' · ');
    g.addEventListener('click', () => toast(`${n.label || n.id}${detail ? ' — ' + detail : ''}`, 'info', 6000));
    g.append(shape, label);
    root.append(g);
  });
  wrap.append(root);
  return wrap;
}

async function memoryGraphPage(host, ctx) {
  const data = await api('chat/memory-graph');
  const legend = el('div', { class: 'profile-badges', style: { justifyContent: 'center' } },
    Object.entries(MEM_KIND_META).map(([k, m]) => el('span', { class: 'pill', style: { borderColor: m.color } }, m.label)));
  const kindCount = {};
  (data.nodes || []).forEach((n) => { kindCount[n.kind] = (kindCount[n.kind] || 0) + 1; });
  host.append(el('div', { class: 'page-head' },
    el('div', null, el('h2', { text: '记忆图谱' }),
      el('p', { text: `以她为中心的记忆知识图谱：${(data.nodes || []).length} 个实体 / ${(data.edges || []).length} 条关联。圆=人（大小=连接数），方块=群/话题，紫心=她自己；点击节点看详情。` }))),
    panel('记忆结构', { subtitle: '好感（紫）/ 群归属（金）/ 话题参与（青）/ 人物关系（粉）' },
      memoryGraphSvg(data.nodes || [], data.edges || []),
      el('div', { class: 'row', style: { marginTop: '10px', flexWrap: 'wrap', gap: '6px', justifyContent: 'center' } }, legend,
        el('span', { class: 'pill outline' }, `共 ${kindCount.user || 0} 位用户 · ${kindCount.group || 0} 个群 · ${kindCount.topic || 0} 个话题`))));
}

/* ==========================================================================
 * Dispatch
 * ========================================================================== */
export async function render(host, ctx) {
  if (ctx.path === 'chat') return chatOverview(host, ctx);
  if (ctx.path === 'users') return usersPage(host, ctx);
  if (ctx.path.startsWith('users/')) return userDetail(host, ctx);
  if (ctx.path === 'favor' || ctx.path === 'favor-ledger') return favorPage(host, ctx);
  if (ctx.path === 'chat-quality') return chatQuality(host, ctx);
  if (ctx.path === 'eval') return evalPage(host, ctx);
  if (ctx.path === 'relations') return relationsPage(host, ctx);
  if (ctx.path === 'memory-graph') return memoryGraphPage(host, ctx);
  if (ctx.path === 'human') return humanPage(host, ctx);
  if (ctx.path === 'social') return socialPage(host, ctx);
  if (ctx.path === 'bots') return botsPage(host, ctx);
  if (ctx.path === 'growth') return growthPage(host, ctx);
  if (ctx.path === 'whitelist') return whitelistPage(host, ctx);
  if (ctx.path === 'emotes') return emotesPage(host, ctx);
  throw new Error(`未知聊天页面：${ctx.path}`);
}
