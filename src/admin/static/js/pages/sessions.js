'use strict';

import {
  api, badge, busy, clear, confirmDialog, copyText, download, downloadCSV, el, emptyState,
  icon, iconButton, loading, num, panel, searchField, stamp, stampShort, toast,
} from '../core.js';

export async function render(host, ctx) {
  if (ctx.params.kind && ctx.params.key) {
    await renderDetail(host, ctx);
  } else {
    await renderList(host, ctx);
  }
}

async function renderList(host, ctx) {
  const data = await api('chat/sessions');
  const state = { query: '', kind: 'all', sort: 'last' };
  const count = el('span', { class: 'pill', text: `${data.items.length} 个会话` });
  const listHost = el('div');
  const searchSlot = el('div');
  host.append(el('div', { class: 'page-head' },
    el('div', null, el('h2', { text: '会话记忆' }), el('p', { text: '记忆按群号 / QQ 号分文件保存；删除不可恢复。' })),
    el('div', { class: 'page-actions' }, count,
      el('button', { class: 'button secondary small', type: 'button', onclick: () => downloadCSV('yuna-sessions.csv', [
        { label: '类型', key: 'kind', csv: (row) => row.kind === 'group' ? '群聊' : '私聊' },
        { label: '标识', key: 'key' }, { label: '待处理', key: 'pending' }, { label: '情景', key: 'episodes' },
        { label: '事实', key: 'facts' }, { label: '最近', key: 'last', csv: (row) => stamp(row.last) },
      ], data.items) }, icon('download', 14), '导出'))),
    el('div', { class: 'toolbar between' }, searchSlot,
      el('div', { class: 'pill-row' },
        ...[['all', '全部'], ['group', '群聊'], ['user', '私聊']].map(([value, label]) => el('button', {
          type: 'button', class: `chip${value === 'all' ? ' active' : ''}`, onclick: (event) => {
            state.kind = value;
            event.currentTarget.parentElement.querySelectorAll('.chip').forEach((node) => node.classList.toggle('active', node === event.currentTarget));
            renderList();
          },
        }, label))),
      el('select', { class: 'select', style: { maxWidth: '140px' }, onchange: (event) => { state.sort = event.target.value; renderList(); } },
        el('option', { value: 'last' }, '最近活跃'),
        el('option', { value: 'pending' }, '待处理最多'),
        el('option', { value: 'episodes' }, '情景最多'))),
    listHost);

  searchSlot.append(searchField('搜索群号、QQ 或摘要…', (value) => { state.query = value; renderList(); }));

  function renderList() {
    const q = state.query.toLowerCase();
    let items = data.items.filter((item) => {
      if (state.kind !== 'all' && item.kind !== state.kind) return false;
      return !q || `${item.kind} ${item.key} ${item.summary ? '有摘要' : ''}`.toLowerCase().includes(q);
    });
    if (state.sort === 'last') items.sort((a, b) => (b.last || 0) - (a.last || 0));
    if (state.sort === 'pending') items.sort((a, b) => (b.pending || 0) - (a.pending || 0));
    if (state.sort === 'episodes') items.sort((a, b) => (b.episodes || 0) - (a.episodes || 0));
    clear(listHost);
    if (!items.length) {
      listHost.append(panel('会话列表', {}, emptyState('没有匹配的会话', '换一个关键词或类型。', 'sessions')));
      return;
    }
    const body = el('tbody');
    items.forEach((item) => {
      body.append(el('tr', { style: { cursor: 'pointer' }, onclick: () => ctx.navigate(`sessions/${item.kind}/${item.key}`) },
        el('td', null, item.kind === 'group' ? badge('群聊', 'info') : badge('私聊', 'outline')),
        el('td', { class: 'mono', text: item.key }),
        el('td', { text: num(item.pending) }),
        el('td', { text: num(item.episodes) }),
        el('td', { text: num(item.facts) }),
        el('td', null, item.summary ? badge('有摘要', 'ok') : badge('无摘要', 'outline')),
        el('td', { class: 'muted-cell', text: stamp(item.last) }),
        el('td', { class: 'actions' }, icon('arrow-right', 15))));
    });
    listHost.append(panel('会话列表', { subtitle: `${items.length} 个` },
      el('div', { class: 'table-wrap' }, el('table', { class: 'data-table' },
        el('thead', null, el('tr', null,
          el('th', { text: '类型' }), el('th', { text: '标识' }), el('th', { text: '待处理' }), el('th', { text: '情景' }),
          el('th', { text: '事实' }), el('th', { text: '摘要' }), el('th', { text: '最近活跃' }), el('th'))),
        body))));
  }
  renderList();
}

async function renderDetail(host, ctx) {
  const { kind, key } = ctx.params;
  if (!['group', 'user'].includes(kind)) {
    host.append(panel('记忆详情', {}, emptyState('类型无效', '只支持群聊或私聊。', 'warning')));
    return;
  }
  const data = await api(`chat/session/${encodeURIComponent(kind)}/${encodeURIComponent(key)}`);
  const back = el('button', { class: 'button ghost small', type: 'button', onclick: () => ctx.navigate('sessions') }, icon('arrow-left', 15), '返回会话列表');
  const title = `${kind === 'group' ? '群聊' : '私聊'} ${key}`;
  const deleteButton = el('button', { class: 'button danger small', type: 'button' }, icon('trash', 14), '删除该记忆');
  deleteButton.addEventListener('click', async () => {
    const ok = await confirmDialog(`删除 ${title} 的全部记忆文件？此操作不可恢复。`, { title: '删除记忆', confirmText: '删除', danger: true });
    if (!ok) return;
    busy(deleteButton, true, '删除中…');
    try {
      await api(`chat/session/${encodeURIComponent(kind)}/${encodeURIComponent(key)}`, { method: 'DELETE' });
      toast('记忆已删除', 'ok');
      ctx.navigate('sessions');
    } catch (error) { toast(error.message, 'error'); } finally { busy(deleteButton, false); }
  });
  const rawToggle = el('button', { class: 'button secondary small', type: 'button' }, icon('code', 14), '查看原始 JSON');
  host.append(back, el('div', { class: 'page-head' },
    el('div', null, el('h2', { text: title }), el('p', { text: `情景 ${data.episodes.length} · 事实 ${data.facts.length} · 近期对话 ${data.pending.length}` })),
    el('div', { class: 'page-actions' },
      kind === 'user' ? el('button', { class: 'button secondary small', type: 'button', onclick: () => ctx.navigate(`users/${encodeURIComponent(key)}`) }, icon('users', 14), '用户画像') : null,
      rawToggle, deleteButton)));

  const body = el('div', { class: 'grid grid-2' });
  const left = el('div', { class: 'stack' });
  const right = el('div', { class: 'stack' });

  const summary = panel('主题摘要', { subtitle: '由记忆固化生成' },
    data.summary ? el('p', { text: data.summary }) : emptyState('暂无摘要', '记忆固化后会生成一句话摘要。', 'message'));
  const facts = panel('长期事实', { subtitle: `${data.facts.length} 条 · 只增不删` },
    data.facts.length
      ? el('ul', { class: 'analysis-list' }, ...data.facts.map((fact) => el('li', { text: typeof fact === 'string' ? fact : JSON.stringify(fact) })))
      : emptyState('暂无长期事实', '', 'book'));
  const episodes = panel('情景记忆', { subtitle: `最近 ${data.episodes.length} 条` },
    data.episodes.length
      ? el('div', { class: 'timeline' }, ...data.episodes.slice().reverse().map((episode) => el('div', { class: 'timeline-item' },
        el('span', { class: 'timeline-time', text: stampShort(episode.time) }),
        el('span', { class: 'timeline-body', text: episode.text || '' }))))
      : emptyState('暂无情景记忆', '', 'sessions'));
  left.append(summary, facts, episodes);

  const pending = panel('近期对话', { subtitle: `${data.pending.length} 条` },
    data.pending.length
      ? el('div', { class: 'timeline' }, ...data.pending.slice().reverse().map((turn) => el('div', { class: 'timeline-item' },
        el('span', { class: 'timeline-time', text: stampShort(turn.time) }),
        el('span', { class: 'timeline-body' },
          badge(turn.role === 'assistant' ? '她' : (turn.name || turn.qq || '?'), turn.role === 'assistant' ? 'accent' : 'outline'),
          turn.recalled ? badge('已撤回', 'danger') : null,
          document.createTextNode(turn.content || (turn.images?.length ? '[图片]' : '') )))))
      : emptyState('暂无近期对话', '', 'message'));
  right.append(pending);

  const raw = el('pre', { class: 'code-block', hidden: true, text: JSON.stringify(data, null, 2) });
  rawToggle.addEventListener('click', () => {
    raw.hidden = !raw.hidden;
    rawToggle.lastChild.textContent = raw.hidden ? '查看原始 JSON' : '收起原始 JSON';
  });
  const rawPanel = panel('原始数据', { subtitle: '只读；可用于调试或导出' },
    el('div', { class: 'row', style: { marginBottom: '8px' } },
      el('button', { class: 'button ghost small', type: 'button', onclick: () => copyText(JSON.stringify(data, null, 2), 'JSON 已复制') }, icon('copy', 14), '复制'),
      el('button', { class: 'button ghost small', type: 'button', onclick: () => download(`yuna-${kind}-${key}.json`, JSON.stringify(data, null, 2), 'application/json') }, icon('download', 14), '下载')),
    raw);

  body.append(left, right);
  host.append(body, rawPanel);
}
