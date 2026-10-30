'use strict';

import {
  api, badge, busy, clear, el, emptyState, errorText, field, icon, iconButton,
  loading, notice, pageHead, searchField, segments, statusBadge, toast,
} from '../core.js';

const state = {
  plugins: [],
  filter: 'all',
  query: '',
  detail: null,
  changes: new Map(),
};

export async function render(host, ctx) {
  state.detail = null;
  state.changes.clear();
  ctx.setDirty(false);
  if (ctx.params.id) {
    await renderDetail(host, ctx, ctx.params.id);
  } else {
    await renderList(host, ctx);
  }
}

async function loadPlugins() {
  state.plugins = await api('plugins');
  return state.plugins;
}

function pluginCard(plugin, ctx) {
  const enabled = plugin.enabled;
  const card = el('article', { class: `plugin-card${enabled ? '' : ' disabled'}` });
  const toggle = el('label', { class: 'switch toggle-plugin', title: enabled ? '停用该插件的事件响应' : '启用该插件的事件响应' });
  const input = el('input', { type: 'checkbox', checked: enabled, 'aria-label': `切换 ${plugin.name}` });
  input.addEventListener('change', async () => {
    input.disabled = true;
    try {
      await api(`plugins/${encodeURIComponent(plugin.id)}`, { method: 'PUT', body: { enabled: input.checked } });
      plugin.enabled = input.checked;
      card.classList.toggle('disabled', !plugin.enabled);
      toggle.title = plugin.enabled ? '停用该插件的事件响应' : '启用该插件的事件响应';
      toast(`插件 ${plugin.name} 事件响应已${plugin.enabled ? '启用' : '停用'}`, 'ok');
    } catch (error) {
      input.checked = !input.checked;
      toast(errorText(error), 'error');
    } finally { input.disabled = false; }
  });
  toggle.append(input, el('span', { class: 'switch-track' }));

  const configButton = el('button', { type: 'button', class: 'button secondary small' }, icon('sliders', 14), '配置');
  configButton.addEventListener('click', () => ctx.navigate(`plugins/${encodeURIComponent(plugin.id)}`));
  const reloadButton = iconButton('refresh', '热重载插件', async () => {
    reloadButton.disabled = true;
    try {
      await api('ops/reload', { method: 'POST', body: { plugin: plugin.id } });
      toast(`插件 ${plugin.name} 已热重载`, 'ok');
    } catch (error) { toast(errorText(error), 'error'); } finally { reloadButton.disabled = false; }
  });

  const top = el('div', { class: 'plugin-top' },
    el('div', { class: 'plugin-identity' },
      el('div', { class: 'plugin-icon', text: String(plugin.name || plugin.id).slice(0, 1).toUpperCase() }),
      el('div', null, el('h3', { text: plugin.name || plugin.id }), el('small', { text: plugin.id }))),
    el('div', { class: 'row', style: { gap: '5px' } }, toggle, reloadButton));
  const actions = el('div', { class: 'plugin-actions' }, configButton);
  card.append(top,
    el('p', { class: 'plugin-desc', text: plugin.description || '这个插件还没有填写描述。' }),
    el('div', { class: 'plugin-meta' },
      statusBadge(plugin.enabled, '事件响应已启用', '事件响应已停用'),
      badge(`${plugin.matchers ?? 0} 个响应器`, 'outline')),
    actions);
  if (plugin.usage) {
    const details = el('details', { class: 'plugin-usage' }, el('summary', { text: '查看使用说明' }), el('p', { text: plugin.usage }));
    card.append(details);
  }
  return card;
}

async function renderList(host, ctx) {
  const searchSlot = el('div');
  const chips = el('div', { class: 'pill-row' });
  const grid = el('div', { class: 'plugin-grid' });
  const count = el('span', { class: 'pill', text: '加载中' });
  host.append(el('div', { class: 'page-head' },
    el('div', null, el('h2', { text: '插件工作台' }), el('p', { text: '开关只控制事件响应，不会卸载插件代码。' })),
    el('div', { class: 'page-actions' }, count, el('button', { class: 'button secondary small', type: 'button', onclick: () => ctx.rerender() }, icon('refresh', 14), '刷新'))),
  el('div', { class: 'toolbar between' }, searchSlot, chips), grid);

  function renderFilters() {
    const total = state.plugins.length;
    const enabled = state.plugins.filter((plugin) => plugin.enabled).length;
    clear(chips).append(
      el('button', { type: 'button', class: `chip${state.filter === 'all' ? ' active' : ''}`, onclick: () => { state.filter = 'all'; renderFilters(); renderGrid(); } }, '全部', el('span', { class: 'count', text: String(total) })),
      el('button', { type: 'button', class: `chip${state.filter === 'on' ? ' active' : ''}`, onclick: () => { state.filter = 'on'; renderFilters(); renderGrid(); } }, '已启用', el('span', { class: 'count', text: String(enabled) })),
      el('button', { type: 'button', class: `chip${state.filter === 'off' ? ' active' : ''}`, onclick: () => { state.filter = 'off'; renderFilters(); renderGrid(); } }, '已停用', el('span', { class: 'count', text: String(total - enabled) })),
    );
    count.textContent = `${total} 个插件 · ${enabled} 个启用`;
  }

  function renderGrid() {
    const query = state.query.toLowerCase();
    const rows = state.plugins.filter((plugin) => {
      if (state.filter === 'on' && !plugin.enabled) return false;
      if (state.filter === 'off' && plugin.enabled) return false;
      return !query || `${plugin.name} ${plugin.id} ${plugin.description}`.toLowerCase().includes(query);
    });
    clear(grid);
    if (!rows.length) {
      grid.append(emptyState('没有匹配的插件', '换一个关键词，或切换到“全部”。', 'plugins'));
      return;
    }
    rows.forEach((plugin) => grid.append(pluginCard(plugin, ctx)));
  }

  searchSlot.append(searchField('搜索插件名称、ID 或描述…', (value) => { state.query = value; renderGrid(); }));
  try {
    await loadPlugins();
    renderFilters();
    renderGrid();
  } catch (error) {
    grid.append(emptyState('插件列表加载失败', errorText(error), 'warning'));
  }
}

async function renderDetail(host, ctx, id) {
  const back = el('button', { class: 'button ghost small', type: 'button', onclick: () => ctx.navigate('plugins') }, icon('arrow-left', 15), '返回插件列表');
  const title = el('h2', { text: '插件配置' });
  const subtitle = el('p', { text: '正在读取配置…' });
  const badgeSlot = el('div', { class: 'row' });
  const form = el('div', { class: 'panel' });
  const saveBar = el('div', { class: 'save-bar' });
  const dirty = el('span', { class: 'dirty', text: '未修改' });
  const save = el('button', { class: 'button primary', type: 'button' }, icon('check', 15), '保存配置');
  const reset = el('button', { class: 'button ghost small', type: 'button' }, '撤销修改');
  const reload = el('button', { class: 'button secondary small', type: 'button' }, icon('refresh', 14), '热重载插件');
  saveBar.append(dirty, el('div', { class: 'row' }, reset, reload, save));
  host.append(back,
    el('div', { class: 'page-head' }, el('div', null, title, subtitle), badgeSlot),
    form, saveBar);

  let config = null;
  let plugin = null;
  let searchInput = null;
  let body = null;

  function updateDirty() {
    const count = state.changes.size;
    dirty.textContent = count ? `${count} 项待保存` : '未修改';
    dirty.classList.toggle('active', count > 0);
    ctx.setDirty(count > 0);
    save.disabled = count === 0;
    reset.disabled = count === 0;
  }

  function setChange(key, value, original, secret = false) {
    const emptySecret = secret && !value;
    if (value === original || emptySecret) state.changes.delete(key);
    else state.changes.set(key, value);
    updateDirty();
  }

  function inputFor(item) {
    if (item.type === 'bool') {
      const input = el('input', { type: 'checkbox', checked: String(item.value).toLowerCase() === 'true' });
      const wrapper = el('label', { class: 'switch' }, input, el('span', { class: 'switch-track' }), el('span', { text: input.checked ? '开启' : '关闭' }));
      input.addEventListener('change', () => {
        wrapper.lastElementChild.textContent = input.checked ? '开启' : '关闭';
        setChange(item.key, input.checked ? 'true' : 'false', item.value);
      });
      return wrapper;
    }
    const input = el('input', {
      class: 'input',
      value: state.changes.has(item.key) ? state.changes.get(item.key) : item.value,
      type: item.secret ? 'password' : (item.type === 'int' || item.type === 'float' ? 'number' : 'text'),
      step: item.type === 'float' ? '0.1' : undefined,
      placeholder: item.secret ? (item.configured ? '已配置；留空保持原值' : '尚未配置') : (item.help || ''),
      autocomplete: 'off',
    });
    input.addEventListener('input', () => setChange(item.key, input.value, item.value, item.secret));
    if (item.secret) {
      const wrap = el('div', { class: 'input-with-action' });
      const eye = iconButton('eye', '显示内容', () => {
        const visible = input.type === 'text';
        input.type = visible ? 'password' : 'text';
        eye.replaceChildren(icon(visible ? 'eye' : 'eye-off'));
      });
      wrap.append(input, eye);
      return wrap;
    }
    return input;
  }

  function renderConfig(filter = '') {
    if (!body || !config) return;
    clear(body);
    const groups = new Map();
    const q = filter.toLowerCase();
    for (const item of config.items) {
      if (q && !`${item.key} ${item.label} ${item.help}`.toLowerCase().includes(q)) continue;
      const group = item.group || '其他';
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(item);
    }
    if (!groups.size) {
      body.append(emptyState('没有匹配的配置项', '换一个关键词试试。', 'sliders'));
      return;
    }
    for (const [group, items] of groups) {
      const groupNode = el('div', { class: 'config-group' }, el('h3', { text: group }));
      for (const item of items) {
        const keyNode = el('div', { class: 'config-key' },
          el('span', { class: 'config-label', text: item.label || item.key }),
          el('code', { text: item.key }),
          el('small', { text: item.help || '' }));
        const inputNode = el('div', { class: 'config-input' }, inputFor(item),
          item.secret ? el('small', { text: item.configured ? '敏感项已配置；不回显原值' : '敏感项未配置' }) : null);
        groupNode.append(el('div', { class: 'config-row' }, keyNode, inputNode));
      }
      body.append(groupNode);
    }
  }

  function renderHeader() {
    const enabled = plugin?.enabled;
    title.textContent = `插件配置 · ${plugin?.name || id}`;
    subtitle.textContent = `环境变量前缀 ${config?.prefix || '—'}；保存写入 .env.${ctx.user?.environment || 'prod'}；${id === 'chat' ? 'chat 配置会尝试热加载' : '保存后需要重启'}。`;
    clear(badgeSlot).append(
      statusBadge(enabled, '事件响应已启用', '事件响应已停用'),
      badge(plugin?.id || id, 'outline'));
  }

  try {
    const [plugins, data] = await Promise.all([api('plugins'), api(`plugins/${encodeURIComponent(id)}/config`)]);
    plugin = plugins.find((row) => row.id === id) || { id, name: id };
    config = data;
    state.detail = { plugin, config };
    renderHeader();
    searchInput = searchField('搜索配置键、中文名或说明…', (value) => renderConfig(value));
    body = el('div');
    form.append(el('div', { class: 'toolbar between' }, searchInput, el('span', { class: 'pill', text: `${config.items.length} 个配置项` })), body);
    renderConfig();
    updateDirty();
  } catch (error) {
    subtitle.textContent = errorText(error);
    form.append(emptyState('配置读取失败', errorText(error), 'warning'));
    return;
  }

  reset.addEventListener('click', () => {
    state.changes.clear();
    renderConfig(searchInput?.querySelector('input')?.value || '');
    updateDirty();
    toast('已撤销未保存的修改');
  });
  save.addEventListener('click', async () => {
    if (!state.changes.size) return;
    busy(save, true, '正在保存…');
    try {
      const result = await api(`plugins/${encodeURIComponent(id)}/config`, {
        method: 'PUT',
        body: { values: Object.fromEntries(state.changes) },
      });
      state.changes.clear();
      updateDirty();
      if (result.restart_required) {
        toast('配置已保存；部分配置需要重启 bot 后生效。', 'warn', 6500);
      } else {
        toast('配置已保存并热加载，无需重启。', 'ok');
      }
      const data = await api(`plugins/${encodeURIComponent(id)}/config`);
      config = data;
      renderHeader();
      renderConfig(searchInput?.querySelector('input')?.value || '');
      updateDirty();
    } catch (error) {
      toast(errorText(error), 'error');
    } finally {
      busy(save, false);
    }
  });
  reload.addEventListener('click', async () => {
    busy(reload, true, '重载中…');
    try {
      await api('ops/reload', { method: 'POST', body: { plugin: id } });
      toast('插件已热重载', 'ok');
    } catch (error) { toast(errorText(error), 'error'); } finally { busy(reload, false); }
  });
}
