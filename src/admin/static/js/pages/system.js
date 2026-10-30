'use strict';

import {
  api, badge, busy, bytes, clear, copyText, download, el, emptyState, errorText, field,
  icon, iconButton, loading, notice, num, panel, searchField, statCard, statusBadge,
  table, toast,
} from '../core.js';

export async function render(host, ctx) {
  if (ctx.path === 'jobs') return renderJobs(host, ctx);
  if (ctx.path === 'config') return renderConfig(host, ctx);
  if (ctx.path === 'ops') return renderOps(host, ctx);
  if (ctx.path === 'logs') return renderLogs(host, ctx);
  if (ctx.path === 'messages') return renderMessages(host, ctx);
  if (ctx.path === 'admins') return renderAdmins(host, ctx);
  throw new Error(`未知系统页面：${ctx.path}`);
}

/* ==========================================================================
 * Jobs
 * ========================================================================== */
async function renderJobs(host, ctx) {
  const data = await api('jobs');
  const status = el('span', { class: 'pill', text: data.available ? (data.running ? '调度器运行中' : '调度器未运行') : '未安装 apscheduler' });
  const body = el('div');
  host.append(el('div', { class: 'page-head' },
    el('div', null, el('h2', { text: '定时任务' }), el('p', { text: '来自 nonebot-plugin-apscheduler；暂停不会删除任务。' })),
    el('div', { class: 'page-actions' }, status,
      el('button', { class: 'button secondary small', type: 'button', onclick: () => ctx.rerender() }, icon('refresh', 14), '刷新'))),
    body);

  if (!data.available) {
    body.append(panel('定时任务', {}, emptyState('未安装 apscheduler', '安装 nonebot-plugin-apscheduler 后即可在这里管理。', 'clock')));
    return;
  }
  if (!data.items.length) {
    body.append(panel('定时任务', {}, emptyState('暂无定时任务', '插件注册任务后会出现在这里。', 'clock')));
    return;
  }
  const rows = data.items.map((job) => {
    const toggle = el('button', { class: `button ${job.paused ? 'secondary' : 'danger'} xs`, type: 'button' }, job.paused ? '恢复' : '暂停');
    toggle.addEventListener('click', async () => {
      busy(toggle, true, '处理中…');
      try {
        await api(`jobs/${encodeURIComponent(job.id)}`, { method: 'PUT', body: { enabled: job.paused } });
        toast(`任务 ${job.name} 已${job.paused ? '恢复' : '暂停'}`, 'ok');
        ctx.rerender();
      } catch (error) { toast(errorText(error), 'error'); busy(toggle, false); }
    });
    return el('tr', null,
      el('td', null, el('strong', { text: job.name }), el('div', { class: 'mono muted', text: job.id })),
      el('td', { class: 'mono', text: job.trigger }),
      el('td', { class: 'muted-cell', text: job.next_run ? new Date(job.next_run).toLocaleString('zh-CN', { hour12: false }) : '已暂停' }),
      el('td', null, statusBadge(!job.paused, '运行中', '已暂停')),
      el('td', { class: 'actions' }, toggle));
  });
  body.append(panel('任务列表', { subtitle: `${data.items.length} 个任务` },
    el('div', { class: 'table-wrap' }, el('table', { class: 'data-table' },
      el('thead', null, el('tr', null, el('th', { text: '任务' }), el('th', { text: '触发规则' }), el('th', { text: '下次运行' }), el('th', { text: '状态' }), el('th'))),
      el('tbody', null, ...rows)))));
}

/* ==========================================================================
 * Config
 * ========================================================================== */
async function renderConfig(host, ctx) {
  const data = await api('config');
  const state = { query: '', changes: new Map(), source: data };
  const searchSlot = el('div');
  const form = el('div', { class: 'panel' });
  const dirty = el('span', { class: 'dirty', text: '未修改' });
  const save = el('button', { class: 'button primary', type: 'button' }, icon('check', 15), '保存配置');
  const reset = el('button', { class: 'button ghost small', type: 'button' }, '撤销');
  const exportButton = el('button', { class: 'button secondary small', type: 'button' }, icon('download', 14), '导出脱敏 JSON');
  const saveBar = el('div', { class: 'save-bar' }, dirty, el('div', { class: 'row' }, reset, exportButton, save));
  host.append(el('div', { class: 'page-head' },
    el('div', null, el('h2', { text: '环境配置' }), el('p', { text: `当前文件 ${data.file}。敏感项不回显，输入新值才会覆盖。` })),
    el('div', { class: 'page-actions' }, badge(`${data.items.length} 项`, 'outline'), badge('chat 支持热加载', 'ok'))),
    el('div', { class: 'toolbar between' }, searchSlot),
    form, saveBar,
    notice('保存前会自动备份到 data/admin/config.backup；chat 配置会立即热加载，其余配置仍需重启 bot。', 'info'));

  searchSlot.append(searchField('搜索配置键…', (value) => { state.query = value; renderConfig(); }));

  function groupKey(key) {
    const prefix = key.split('_')[0] || '其它';
    return prefix;
  }

  function updateDirty() {
    dirty.textContent = state.changes.size ? `${state.changes.size} 项待保存` : '未修改';
    dirty.classList.toggle('active', state.changes.size > 0);
    save.disabled = !state.changes.size;
    reset.disabled = !state.changes.size;
    ctx.setDirty(state.changes.size > 0);
  }

  function renderConfig() {
    clear(form);
    const q = state.query.toLowerCase();
    const groups = new Map();
    for (const item of state.source.items) {
      if (q && !item.key.toLowerCase().includes(q)) continue;
      const group = groupKey(item.key);
      if (!groups.has(group)) groups.set(group, []);
      groups.get(group).push(item);
    }
    if (!groups.size) {
      form.append(emptyState('没有匹配的配置项', '换一个关键词。', 'sliders'));
      return;
    }
    for (const [group, items] of groups) {
      const block = el('div', { class: 'config-group' }, el('h3', { text: group }));
      for (const item of items) {
        const input = el('input', {
          class: 'input',
          type: item.secret ? 'password' : 'text',
          value: state.changes.has(item.key) ? state.changes.get(item.key) : item.value,
          placeholder: item.secret ? (item.configured ? '已配置；留空保持原值' : '尚未配置') : '',
          autocomplete: 'off',
          spellcheck: 'false',
        });
        input.addEventListener('input', () => {
          if (input.value === item.value || (item.secret && !input.value)) state.changes.delete(item.key);
          else state.changes.set(item.key, input.value);
          updateDirty();
        });
        const inputWrap = item.secret ? el('div', { class: 'input-with-action' }, input,
          iconButton('eye', '显示内容', (event) => {
            const eye = event.currentTarget;
            const visible = input.type === 'text';
            input.type = visible ? 'password' : 'text';
            eye.replaceChildren(icon(visible ? 'eye' : 'eye-off'));
          })) : input;
        block.append(el('div', { class: 'config-row' },
          el('div', { class: 'config-key' },
            el('span', { class: 'config-label', text: item.key }),
            el('code', { text: item.secret ? '敏感项' : '明文' }),
            el('small', { text: item.secret ? (item.configured ? '已配置' : '未配置') : '保存后写入环境文件' })),
          el('div', { class: 'config-input' }, inputWrap)));
      }
      form.append(block);
    }
  }

  function buildRedactedJson() {
    const values = Object.fromEntries(state.source.items.map((item) => [
      item.key,
      item.secret ? '' : (state.changes.has(item.key) ? state.changes.get(item.key) : item.value),
    ]));
    return JSON.stringify(values, null, 2);
  }

  reset.addEventListener('click', () => { state.changes.clear(); renderConfig(); updateDirty(); toast('已撤销未保存的修改'); });
  exportButton.addEventListener('click', () => download(`yuna-env-${ctx.user?.environment || 'prod'}-redacted.json`, buildRedactedJson(), 'application/json'));
  save.addEventListener('click', async () => {
    if (!state.changes.size) return;
    busy(save, true, '保存中…');
    try {
      const result = await api('config', { method: 'PUT', body: { values: Object.fromEntries(state.changes) } });
      if (result.restart_required) {
        toast('配置已保存并备份；部分配置需要重启 bot 后生效。', 'warn', 6500);
      } else {
        toast('配置已保存并热加载，无需重启。', 'ok');
      }
      state.changes.clear();
      ctx.setDirty(false);
      const fresh = await api('config');
      state.source = fresh;
      renderConfig();
      updateDirty();
    } catch (error) { toast(errorText(error), 'error'); } finally { busy(save, false); }
  });
  renderConfig();
  updateDirty();
}

/* ==========================================================================
 * Ops
 * ========================================================================== */
async function renderOps(host, ctx) {
  const [plugins, overview, cache, backupData, alertData] = await Promise.all([
    api('plugins'),
    api('overview'),
    api('media/cache').catch(() => null),
    api('backups').catch(() => null),
    api('alerts').catch(() => null),
  ]);
  const pluginSelect = el('select', { class: 'select' }, ...plugins.map((plugin) => el('option', { value: plugin.id }, `${plugin.name} (${plugin.id})`)));
  const reloadButton = el('button', { class: 'button primary', type: 'button' }, icon('refresh', 15), '热重载');
  reloadButton.addEventListener('click', async () => {
    if (!pluginSelect.value) return;
    busy(reloadButton, true, '重载中…');
    try {
      await api('ops/reload', { method: 'POST', body: { plugin: pluginSelect.value } });
      toast(`插件 ${pluginSelect.value} 已热重载`, 'ok');
    } catch (error) { toast(errorText(error), 'error'); } finally { busy(reloadButton, false); }
  });
  const reloadPanel = panel('热重载插件', { subtitle: '应用 .py 改动，不重启整个进程' },
    el('div', { class: 'form-grid', style: { alignItems: 'end' } }, field('选择插件', pluginSelect), el('div', { class: 'row', style: { paddingBottom: '15px' } }, reloadButton)),
    notice('热重载会清理该插件已注册的事件响应器再重新导入；正在执行的任务可能被中断。', 'warn'));

  const exportArea = el('textarea', { class: 'textarea', rows: '10', readonly: true, placeholder: '点击读取当前配置（敏感项留空）' });
  const readExport = el('button', { class: 'button secondary small', type: 'button' }, icon('download', 14), '读取配置');
  const copyExport = el('button', { class: 'button ghost small', type: 'button' }, icon('copy', 14), '复制');
  const downloadExport = el('button', { class: 'button ghost small', type: 'button' }, icon('download', 14), '下载 JSON');
  let exportData = null;
  readExport.addEventListener('click', async () => {
    busy(readExport, true, '读取中…');
    try {
      exportData = await api('ops/export');
      exportArea.value = JSON.stringify(exportData.values, null, 2);
      toast(`已读取 ${exportData.file}`, 'ok');
    } catch (error) { toast(errorText(error), 'error'); } finally { busy(readExport, false); }
  });
  copyExport.addEventListener('click', () => copyText(exportArea.value || '{}', '配置已复制'));
  downloadExport.addEventListener('click', () => download('yuna-env-export.json', exportArea.value || JSON.stringify(exportData?.values || {}, null, 2), 'application/json'));
  const exportPanel = panel('导出配置', { subtitle: '敏感项留空，适合备份与迁移' },
    el('div', { class: 'row', style: { marginBottom: '8px' } }, readExport, copyExport, downloadExport), exportArea);

  const importArea = el('textarea', { class: 'textarea', rows: '8', placeholder: '{\n  "CHAT_LLM_MODEL": "deepseek-flash"\n}' });
  const importButton = el('button', { class: 'button primary', type: 'button' }, icon('upload', 15), '导入配置');
  importButton.addEventListener('click', async () => {
    let values;
    try {
      values = JSON.parse(importArea.value || '{}');
      if (!values || typeof values !== 'object' || Array.isArray(values)) throw new Error('需要一个 JSON 对象');
    } catch (error) { toast(`JSON 格式无效：${error.message}`, 'error'); return; }
    const count = Object.keys(values).length;
    if (!count) { toast('没有可导入的配置项', 'warn'); return; }
    busy(importButton, true, '导入中…');
    try {
      const result = await api('ops/import', { method: 'POST', body: { config: values } });
      toast(result.restart_required
        ? `已导入 ${result.imported} 项配置；部分配置需要重启 bot`
        : `已导入 ${result.imported} 项配置并热加载`, result.restart_required ? 'warn' : 'ok', 6500);
    } catch (error) { toast(errorText(error), 'error'); } finally { busy(importButton, false); }
  });
  const importPanel = panel('导入配置', { subtitle: '粘贴导出的 JSON；空值会被忽略以保护敏感信息' }, importArea,
    el('div', { class: 'row end', style: { marginTop: '8px' } }, importButton));

  const clearCache = el('button', { class: 'button danger small', type: 'button' }, icon('trash', 14), '清空缓存');
  clearCache.addEventListener('click', async () => {
    if (!(await ctx.confirm('删除全部缩略图缓存？原图不受影响，下次浏览会重新生成。', { title: '清空媒体缓存', confirmText: '清空', danger: true }))) return;
    busy(clearCache, true, '清理中…');
    try {
      const result = await api('media/cache', { method: 'DELETE' });
      toast(`已清空 ${result.removed} 个缩略图`, 'ok');
      ctx.rerender();
    } catch (error) { toast(errorText(error), 'error'); } finally { busy(clearCache, false); }
  });
  const warmCache = el('button', { class: 'button secondary small', type: 'button' }, icon('bolt', 14), '预热全部');
  warmCache.addEventListener('click', async () => {
    busy(warmCache, true, '预热中…');
    try {
      const result = await api('media/warm', { method: 'POST', body: { width: 360 }, timeout: 180000 });
      toast(`已生成 ${result.generated} 张缩略图${result.failed ? `，${result.failed} 张失败` : ''}`, 'ok');
      ctx.rerender();
    } catch (error) { toast(errorText(error), 'error'); } finally { busy(warmCache, false); }
  });
  const cachePanel = panel('媒体缓存', { subtitle: '表情缩略图磁盘缓存' },
    el('div', { class: 'metric-row' }, el('span', { text: '缩略图' }), el('strong', { text: cache ? `${num(cache.files)} 张` : '—' }), el('span')),
    el('div', { class: 'metric-row' }, el('span', { text: '占用 / 上限' }), el('strong', { text: cache ? `${(cache.bytes / 1024 ** 2).toFixed(1)} / ${(cache.max_bytes / 1024 ** 2).toFixed(0)} MB` : '—' }), el('span')),
    el('div', { class: 'metric-row' }, el('span', { text: '命中 / 未命中' }), el('strong', { text: cache ? `${num(cache.hits)} / ${num(cache.misses)}` : '—' }), el('span')),
    el('div', { class: 'row end', style: { marginTop: '10px' } }, warmCache, clearCache));

  const backupPanel = buildBackupPanel(backupData, ctx);
  const alertPanel = buildAlertPanel(alertData, ctx);

  const infoPanel = panel('运行信息', { subtitle: '当前管理服务与项目版本' },
    el('div', { class: 'metric-row' }, el('span', { text: '版本' }), el('strong', { text: `v${overview.version || ctx.version}` }), el('span')),
    el('div', { class: 'metric-row' }, el('span', { text: '环境' }), el('strong', { text: overview.environment || '—' }), el('span')),
    el('div', { class: 'metric-row' }, el('span', { text: '运行时间' }), el('strong', { text: `${Math.floor((overview.uptime || 0) / 3600)} 小时` }), el('span')),
    el('div', { class: 'metric-row' }, el('span', { text: '在线连接' }), el('strong', { text: `${overview.bots?.length || 0} 个` }), el('span')),
    el('div', { class: 'metric-row' }, el('span', { text: '插件' }), el('strong', { text: `${plugins.length} 个` }), el('span')));

  host.append(el('div', { class: 'grid grid-2' }, reloadPanel, infoPanel),
    backupPanel,
    alertPanel,
    el('div', { class: 'grid grid-2' }, exportPanel, importPanel), cachePanel);
}

function buildBackupPanel(data, ctx) {
  const isOwner = ctx.user?.role === 'owner';
  const items = data?.items || [];
  const createButton = el('button', { class: 'button primary small', type: 'button' }, icon('plus', 14), '立即备份');
  createButton.addEventListener('click', async () => {
    busy(createButton, true, '备份中…');
    try {
      const result = await api('backups', { method: 'POST', body: { kind: 'manual' }, timeout: 120000 });
      toast(`已创建备份 ${result.name}（${result.files} 个文件）`, 'ok');
      ctx.rerender();
    } catch (error) { toast(errorText(error), 'error'); } finally { busy(createButton, false); }
  });
  const kindLabel = { manual: '手动', auto: '每日自动', 'pre-restore': '恢复前快照' };
  const kindTone = { manual: 'accent', auto: 'ok', 'pre-restore': 'warn' };
  const rows = items.map((item) => {
    const downloadLink = el('a', {
      class: 'button ghost xs',
      href: `/api/backups/${encodeURIComponent(item.name)}`,
      download: item.name,
      title: '下载备份',
    }, icon('download', 12), '下载');
    const restore = el('button', { class: 'button secondary xs', type: 'button' }, icon('upload', 12), '恢复');
    const remove = el('button', { class: 'button danger xs', type: 'button' }, icon('trash', 12), '删除');
    restore.addEventListener('click', async () => {
      if (!isOwner) { toast('仅所有者可以恢复备份', 'warn'); return; }
      const ok = await ctx.confirm(
        `从 ${item.name} 恢复数据？会覆盖当前 .env、记忆、画像、用户与白名单；恢复前会自动创建一份 pre-restore 快照。`,
        { title: '恢复数据备份', confirmText: '确认恢复', danger: true },
      );
      if (!ok) return;
      busy(restore, true, '恢复中…');
      try {
        const result = await api(`backups/${encodeURIComponent(item.name)}/restore`, {
          method: 'POST', body: { confirm: true }, timeout: 120000,
        });
        toast(result.restart_required
          ? `已恢复 ${result.files} 个文件；请重启 bot 使部分配置生效`
          : `已恢复 ${result.files} 个文件并热加载`, result.restart_required ? 'warn' : 'ok', 7000);
        ctx.rerender();
      } catch (error) { toast(errorText(error), 'error'); } finally { busy(restore, false); }
    });
    remove.addEventListener('click', async () => {
      if (!isOwner) { toast('仅所有者可以删除备份', 'warn'); return; }
      const ok = await ctx.confirm(`删除备份 ${item.name}？`, { title: '删除备份', confirmText: '删除', danger: true });
      if (!ok) return;
      try {
        await api(`backups/${encodeURIComponent(item.name)}`, { method: 'DELETE' });
        toast('备份已删除', 'ok');
        ctx.rerender();
      } catch (error) { toast(errorText(error), 'error'); }
    });
    return el('tr', null,
      el('td', { class: 'mono break', text: item.name }),
      el('td', null, badge(kindLabel[item.kind] || item.kind, kindTone[item.kind] || 'outline')),
      el('td', { text: bytes(item.size) }),
      el('td', { class: 'muted-cell', text: item.created }),
      el('td', { class: 'actions' }, el('div', { class: 'row end' }, isOwner ? downloadLink : null, isOwner ? restore : null, isOwner ? remove : null)));
  });
  const body = items.length
    ? el('div', { class: 'table-wrap' }, el('table', { class: 'data-table' },
      el('thead', null, el('tr', null, el('th', { text: '文件' }), el('th', { text: '类型' }), el('th', { text: '大小' }), el('th', { text: '创建时间' }), el('th'))),
      el('tbody', null, ...rows)))
    : emptyState('还没有数据备份', '备份包含 .env 与聊天记忆/画像/用户/白名单，不包含表情原图和缩略图缓存。', 'database');
  return panel('数据备份', { subtitle: '配置 + 记忆 + 画像 + 白名单；自动排除图片与缓存', actions: isOwner ? createButton : null },
    body,
    el('p', { class: 'notice info', style: { marginTop: '12px' } },
      icon('info', 16),
      el('div', { text: `备份目录：${data?.dir || 'data/admin/backups'}。每天 04:00 后自动创建一份每日备份，最多保留 ${data?.max_files || 20} 份；恢复前会自动创建 pre-restore 快照。` })));
}

function buildAlertPanel(data, ctx) {
  const enabled = data?.enabled !== false;
  const isOwner = ctx.user?.role === 'owner';
  const targets = data?.targets || [];
  const history = data?.history || [];
  const testButton = el('button', { class: 'button secondary small', type: 'button' }, icon('send', 14), '发送测试告警');
  testButton.addEventListener('click', async () => {
    const ok = await ctx.confirm('将向配置的 OneBot 管理员和/或 HTTP Webhook 发送测试告警。', {
      title: '发送测试告警', confirmText: '发送测试', danger: false,
    });
    if (!ok) return;
    busy(testButton, true, '发送中…');
    try {
      const result = await api('alerts/test', { method: 'POST', timeout: 30000 });
      toast(`测试告警已发送 ${result.sent} 个通道`, 'ok');
      ctx.rerender();
    } catch (error) { toast(errorText(error), 'error'); } finally { busy(testButton, false); }
  });
  const rows = history.slice(0, 8).map((entry) => el('div', { class: 'log-row', style: { gridTemplateColumns: '150px 70px minmax(0,1fr)' } },
    el('span', { class: 'log-time', text: entry.created || '—' }),
    el('span', { class: `log-level ${entry.delivered ? 'SUCCESS' : 'ERROR'}`, text: entry.delivered ? '已发送' : '失败' }),
    el('span', { class: 'log-message', text: (entry.warnings || []).join('；') + (entry.error ? ` · ${entry.error}` : '') })));
  const body = el('div', { class: 'stack-sm' },
    el('div', { class: 'metric-row', style: { gridTemplateColumns: '110px minmax(0,1fr) auto' } },
      el('span', { text: '告警开关' }), el('span', { class: 'muted small', text: enabled ? '已启用（ADMIN_ALERTS_ENABLED）' : '已关闭（ADMIN_ALERTS_ENABLED=false）' }), statusBadge(enabled, '启用', '关闭')),
    el('div', { class: 'metric-row', style: { gridTemplateColumns: '110px minmax(0,1fr) auto' } },
      el('span', { text: '通知对象' }), el('span', { class: 'muted small', text: targets.length ? targets.map((qq) => `QQ ${qq}`).join('、') : '未配置，且 SUPERUSERS 为空' }), el('span')),
    el('div', { class: 'metric-row', style: { gridTemplateColumns: '110px minmax(0,1fr) auto' } },
      el('span', { text: 'Webhook' }), el('span', { class: 'muted small', text: data?.webhook_enabled ? `已配置${data.webhook_host ? ` · ${data.webhook_host}` : ''}（ADMIN_ALERT_WEBHOOK）` : '未配置；可用于 OneBot 完全离线时的兜底' }), statusBadge(!!data?.webhook_enabled, '启用', '未配置')),
    el('div', { class: 'metric-row', style: { gridTemplateColumns: '110px minmax(0,1fr) auto' } },
      el('span', { text: '冷却时间' }), el('span', { class: 'muted small', text: `${Math.round((data?.cooldown_seconds || 0) / 60)} 分钟；新增告警类型会立即发送` }), el('span')),
    el('div', { class: 'metric-row', style: { gridTemplateColumns: '110px minmax(0,1fr) auto' } },
      el('span', { text: '上次发送' }), el('span', { class: 'muted small', text: data?.last_sent ? new Date(data.last_sent * 1000).toLocaleString('zh-CN', { hour12: false }) : '从未发送' }), el('span')),
    history.length ? el('div', { class: 'log-list', style: { maxHeight: '220px', marginTop: '8px' } }, ...rows) : el('p', { class: 'muted small', style: { margin: '8px 0 0' }, text: '暂无告警历史。' }),
    el('div', { class: 'row end', style: { marginTop: '10px' } }, isOwner ? testButton : null));
  return panel('健康告警', { subtitle: 'OneBot 私聊 + 可选 HTTP Webhook' }, body);
}

/* ==========================================================================
 * Logs
 * ========================================================================== */
async function renderLogs(host, ctx) {
  let items = (await api('logs')).items;
  const state = { query: '', level: '', live: true, paused: false };
  const list = el('div', { class: 'log-list tall' });
  const searchSlot = el('div');
  const levelSelect = el('select', { class: 'select', style: { maxWidth: '130px' } },
    el('option', { value: '' }, '全部级别'), ...[ 'INFO', 'WARNING', 'ERROR', 'CRITICAL' ].map((level) => el('option', { value: level }, level)));
  const liveBox = el('label', { class: 'checkbox' }, el('input', { type: 'checkbox', checked: true }), '自动刷新');
  const pauseButton = el('button', { class: 'button ghost small', type: 'button' }, icon('pause', 14), '暂停');
  const copyButton = el('button', { class: 'button ghost small', type: 'button' }, icon('copy', 14), '复制');
  const downloadButton = el('button', { class: 'button ghost small', type: 'button' }, icon('download', 14), '下载');
  let timer = null;

  host.append(el('div', { class: 'page-head' },
    el('div', null, el('h2', { text: '运行日志' }), el('p', { text: '显示管理模块启动后的最近 600 条 INFO 及以上日志。' })),
    el('div', { class: 'page-actions' }, badge('实时', 'ok'))),
    el('div', { class: 'toolbar between' }, searchSlot,
      el('div', { class: 'row' }, levelSelect, liveBox, pauseButton, copyButton, downloadButton)),
    panel('日志流', { subtitle: '最近的记录在最下方' }, list));

  searchSlot.append(searchField('搜索日志内容…', (value) => { state.query = value; renderList(); }));
  levelSelect.addEventListener('change', () => { state.level = levelSelect.value; renderList(); });
  liveBox.querySelector('input').addEventListener('change', (event) => { state.live = event.target.checked; schedule(); });

  function filtered() {
    const q = state.query.toLowerCase();
    return items.filter((entry) => (!state.level || entry.level === state.level) && (!q || entry.message.toLowerCase().includes(q)));
  }

  function renderList(keepScroll = true) {
    const nearBottom = !keepScroll || list.scrollHeight - list.scrollTop - list.clientHeight < 60;
    clear(list);
    const rows = filtered();
    if (!rows.length) { list.append(emptyState('暂无匹配日志', '调整筛选条件，或等待新的日志。', 'terminal')); return; }
    rows.forEach((entry) => {
      list.append(el('div', { class: 'log-row' },
        el('span', { class: 'log-time', text: new Date(entry.time).toLocaleTimeString('zh-CN', { hour12: false }) }),
        el('span', { class: `log-level ${entry.level}`, text: entry.level }),
        el('span', { class: 'log-message', text: entry.message })));
    });
    if (nearBottom) list.scrollTop = list.scrollHeight;
  }

  async function poll() {
    if (!state.live || state.paused || document.hidden) return;
    try {
      const fresh = await api('logs');
      if (fresh.items.length !== items.length) {
        items = fresh.items;
        renderList();
      }
    } catch { /* 静默失败，避免日志页反复弹错 */ }
  }

  function schedule() {
    if (timer) clearInterval(timer);
    timer = null;
    if (state.live && !state.paused) timer = setInterval(poll, 5000);
  }

  pauseButton.addEventListener('click', () => {
    state.paused = !state.paused;
    pauseButton.replaceChildren(icon(state.paused ? 'play' : 'pause', 14), document.createTextNode(state.paused ? '继续' : '暂停'));
    schedule();
  });
  copyButton.addEventListener('click', () => copyText(filtered().map((entry) => `[${entry.time}] ${entry.level} ${entry.message}`).join('\n'), '日志已复制'));
  downloadButton.addEventListener('click', () => download(`yuna-logs-${Date.now()}.log`, filtered().map((entry) => `[${entry.time}] ${entry.level} ${entry.message}`).join('\n')));
  renderList(false);
  schedule();
  ctx.onCleanup(() => { if (timer) clearInterval(timer); });
}

/* ==========================================================================
 * Messages
 * ========================================================================== */
async function renderMessages(host, ctx) {
  const data = await api('overview');
  const bots = (data.bots || []).filter((bot) => bot.adapter === 'OneBot V11');
  const botSelect = el('select', { class: 'select', required: true }, ...bots.map((bot) => el('option', { value: bot.id }, `${bot.id} · ${bot.adapter}`)));
  const typeSelect = el('select', { class: 'select' }, el('option', { value: 'group' }, '群聊'), el('option', { value: 'private' }, '私聊'));
  const target = el('input', { class: 'input', inputmode: 'numeric', pattern: '[0-9]+', placeholder: '群号 / 用户 QQ' });
  const text = el('textarea', { class: 'textarea', rows: '8', maxlength: '4000', placeholder: '输入要发送的文字…' });
  const counter = el('span', { class: 'muted small', text: '0 / 4000' });
  const send = el('button', { class: 'button primary', type: 'button' }, icon('send', 15), '发送消息');
  text.addEventListener('input', () => { counter.textContent = `${text.value.length} / 4000`; });

  if (!bots.length) {
    host.append(panel('发送消息', {}, emptyState('没有可用的 OneBot V11 机器人', '等待客户端连接后刷新页面。', 'send')));
    return;
  }

  send.addEventListener('click', async () => {
    if (!botSelect.value) { toast('请选择机器人', 'error'); return; }
    if (!/^\d+$/.test(target.value.trim())) { toast('请填写有效的群号或 QQ 号', 'error'); target.focus(); return; }
    if (!text.value.trim()) { toast('消息内容不能为空', 'error'); text.focus(); return; }
    const ok = await ctx.confirm(`确认通过 ${botSelect.value} 向${typeSelect.value === 'group' ? '群聊' : '私聊'} ${target.value.trim()} 发送消息？`, {
      title: '发送消息', confirmText: '发送',
    });
    if (!ok) return;
    busy(send, true, '发送中…');
    try {
      await api('messages', { method: 'POST', body: {
        bot_id: botSelect.value,
        target_type: typeSelect.value,
        target_id: target.value.trim(),
        message: text.value,
      } });
      toast('消息发送成功', 'ok');
      text.value = '';
      counter.textContent = '0 / 4000';
    } catch (error) { toast(errorText(error), 'error'); } finally { busy(send, false); }
  });

  const form = el('div', { class: 'stack' },
    el('div', { class: 'form-grid' }, field('发送机器人', botSelect), field('目标类型', typeSelect)),
    field('群号 / 用户 QQ', target),
    field('消息内容', text, { hint: '纯文本；OneBot V11 会按当前字体发送。' }),
    el('div', { class: 'row between' }, counter, send));
  host.append(el('div', { class: 'page-head' }, el('div', null, el('h2', { text: '发送消息' }), el('p', { text: '通过在线 OneBot V11 机器人发送纯文本。' }))),
    panel('撰写消息', { subtitle: '发送前会再次确认' }, form),
    notice('请确认目标 ID 与内容；消息一旦发送无法撤回（取决于客户端是否支持）。', 'warn'));
}

/* ==========================================================================
 * Admins
 * ========================================================================== */
async function renderAdmins(host, ctx) {
  const [data, audit] = await Promise.all([api('admins'), api('audit').catch(() => ({ items: [] }))]);
  const owner = data.role === 'owner';
  const body = el('div');
  const listHost = el('div');
  const auditHost = el('div');
  const username = el('input', { class: 'input', placeholder: '2–32 位字母、数字、_ . -' });
  const password = el('input', { class: 'input', type: 'password', minlength: '12', placeholder: '至少 12 位' });
  const create = el('button', { class: 'button primary', type: 'button' }, icon('plus', 14), '新增管理员');
  create.addEventListener('click', async () => {
    if (!/^[A-Za-z0-9_.-]{2,32}$/.test(username.value.trim())) { toast('用户名格式无效', 'error'); return; }
    if (password.value.length < 12) { toast('口令至少 12 位', 'error'); return; }
    busy(create, true, '创建中…');
    try {
      await api('admins', { method: 'POST', body: { username: username.value.trim(), password: password.value } });
      toast('管理员已新增', 'ok');
      ctx.rerender();
    } catch (error) { toast(errorText(error), 'error'); } finally { busy(create, false); }
  });
  const createForm = owner ? el('div', { class: 'inline-form' }, field('用户名', username), field('初始口令', password), create) : null;

  host.append(el('div', { class: 'page-head' },
    el('div', null, el('h2', { text: '管理员' }), el('p', { text: '所有者可新增或删除管理员；每个账号使用独立用户名登录。' })),
    el('div', { class: 'page-actions' }, badge(data.current, 'accent'), badge(data.role === 'owner' ? '所有者' : '管理员', 'outline'))),
    listHost, auditHost);
  if (createForm) host.insertBefore(panel('新增管理员', { subtitle: '仅所有者可操作' }, createForm), auditHost);

  const rows = data.items.map((row) => {
    const del = owner && row.role !== 'owner' ? el('button', { class: 'button danger xs', type: 'button' }, icon('trash', 12), '删除') : null;
    if (del) del.addEventListener('click', async () => {
      const ok = await ctx.confirm(`删除管理员 ${row.username}？该账号的在线会话会立即失效。`, { title: '删除管理员', confirmText: '删除', danger: true });
      if (!ok) return;
      try {
        await api(`admins/${encodeURIComponent(row.username)}`, { method: 'DELETE' });
        toast('管理员已删除', 'ok');
        ctx.rerender();
      } catch (error) { toast(errorText(error), 'error'); }
    });
    return el('tr', null,
      el('td', null, el('div', { class: 'user-cell' }, el('span', { class: 'avatar', text: row.username.slice(0, 1) }),
        el('div', { class: 'user-cell-text' }, el('strong', { text: row.username }), el('small', { text: row.role === 'owner' ? '所有者账号' : '管理员账号' })))),
      el('td', null, badge(row.role === 'owner' ? '所有者' : '管理员', row.role === 'owner' ? 'accent' : 'outline')),
      el('td', { text: num(row.active) }),
      el('td', { class: 'actions' }, del || el('span', { class: 'muted small', text: row.username === data.current ? '当前账号' : '—' })));
  });
  listHost.append(panel('账号列表', { subtitle: `${data.items.length} 个账号` }, el('div', { class: 'table-wrap' },
    el('table', { class: 'data-table' },
      el('thead', null, el('tr', null, el('th', { text: '账号' }), el('th', { text: '角色' }), el('th', { text: '在线会话' }), el('th'))),
      el('tbody', null, ...rows)))));

  const auditItems = audit.items || [];
  auditHost.append(panel('操作审计', { subtitle: `最近 ${auditItems.length} 条` },
    auditItems.length ? el('div', { class: 'log-list tall' }, ...auditItems.map((entry) => el('div', { class: 'audit-row' },
      el('span', { text: entry.time }), el('span', { text: entry.user }), el('span', { text: `${entry.action}${entry.detail ? ` · ${entry.detail}` : ''}` }))))
      : emptyState('暂无审计记录', '管理操作会记录在这里。', 'shield')));
}
