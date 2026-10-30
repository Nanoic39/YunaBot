'use strict';

import {
  api, badge, bytes, clear, clock, compact, duration, el, emptyState, greeting, icon,
  loading, meter, notice, num, panel, statCard, timeAgo, toast,
} from '../core.js';
import { barList } from '../charts.js';

export async function render(host, ctx) {
  const nodes = {};
  const hero = el('section', { class: 'hero' });
  const heroContent = el('div', { class: 'hero-content' });
  const eyebrow = el('p', { class: 'eyebrow', text: 'LIVE WORKSPACE' });
  const title = el('h2', { text: `${greeting()}，Yuna 在这里。` });
  const lead = el('p', { text: '连接、插件、聊天人格与图片资源都在正常运转。下面是此刻的状态。' });
  const heroActions = el('div', { class: 'hero-actions' });
  const uptimePill = el('span', { class: 'pill outline', text: '正在读取运行时间…' });
  const heroButton = el('button', { class: 'button secondary small', type: 'button' }, icon('refresh', 15), '刷新状态');
  heroButton.addEventListener('click', () => ctx.rerender());
  heroActions.append(uptimePill, heroButton);
  heroContent.append(eyebrow, title, lead, heroActions);
  const heroSide = el('div', { class: 'hero-side' },
    el('div', { class: 'hero-orb', text: 'Y' }),
    el('small', { text: 'YUNA / ONLINE' }));
  hero.append(heroContent, heroSide);

  const healthBox = el('div');
  const stats = el('div', { class: 'stat-grid' });
  const mainGrid = el('div', { class: 'grid grid-2' });
  const botsPanel = el('section', { class: 'panel' });
  const resourcesPanel = el('section', { class: 'panel' });
  const moodPanel = el('section', { class: 'panel' });
  const logsPanel = el('section', { class: 'panel' });
  const quickPanel = el('section', { class: 'panel' });
  const cachePanel = el('section', { class: 'panel' });

  mainGrid.append(botsPanel, resourcesPanel);
  const bottomGrid = el('div', { class: 'grid grid-2' });
  bottomGrid.append(moodPanel, logsPanel);
  const lowerGrid = el('div', { class: 'grid grid-2' });
  lowerGrid.append(quickPanel, cachePanel);
  host.append(hero, healthBox, stats, mainGrid, bottomGrid, lowerGrid);

  function renderSkeletonPanels() {
    botsPanel.replaceChildren(el('div', { class: 'panel-head' }, el('div', { class: 'panel-title' }, icon('server', 17), el('div', null, el('h2', { text: '机器人连接' }), el('p', { text: 'OneBot / Telegram 适配器' })))),
      loading('正在读取连接状态…'));
    resourcesPanel.replaceChildren(el('div', { class: 'panel-head' }, el('div', { class: 'panel-title' }, icon('cpu', 17), el('div', null, el('h2', { text: '系统资源' }), el('p', { text: '管理进程的资源占用' })))), loading());
    moodPanel.replaceChildren(el('div', { class: 'panel-head' }, el('div', { class: 'panel-title' }, icon('sparkles', 17), el('div', null, el('h2', { text: '拟人状态' }), el('p', { text: '此刻的心情与精力' })))), loading());
    logsPanel.replaceChildren(el('div', { class: 'panel-head' }, el('div', { class: 'panel-title' }, icon('terminal', 17), el('div', null, el('h2', { text: '最近日志' }), el('p', { text: '来自管理模块的实时记录' })))), loading());
    quickPanel.replaceChildren(el('div', { class: 'panel-head' }, el('div', { class: 'panel-title' }, icon('bolt', 17), el('div', null, el('h2', { text: '快速入口' }), el('p', { text: '常用操作' })))), loading());
    cachePanel.replaceChildren(el('div', { class: 'panel-head' }, el('div', { class: 'panel-title' }, icon('cache', 17), el('div', null, el('h2', { text: '媒体缓存' }), el('p', { text: '缩略图磁盘缓存' })))), loading());
  }
  renderSkeletonPanels();

  function renderStats(data, chat) {
    const bots = data.bots || [];
    const disabled = Number(data.disabled) || 0;
    const memory = chat?.memories ?? 0;
    const users = chat?.users ?? 0;
    const tokens = chat?.tokens ?? 0;
    stats.replaceChildren(
      statCard({ label: '在线机器人', value: bots.length, hint: bots.length ? '连接保持正常' : '等待客户端连接', icon: 'server', accent: bots.length > 0 }),
      statCard({ label: '已加载插件', value: data.plugins ?? 0, hint: disabled ? `${disabled} 个事件的响应已停用` : '全部事件响应正常', icon: 'plugins' }),
      statCard({ label: '运行时间', value: duration(data.uptime), hint: `启动于 ${clock(data.started)}`, icon: 'clock' }),
      statCard({ label: '接收事件', value: compact(data.events), hint: '本次启动累计', icon: 'activity' }),
      statCard({ label: '聊天用户', value: num(users), hint: `${chat?.memories ?? 0} 个记忆会话`, icon: 'users' }),
      statCard({ label: '长期记忆', value: num(memory), hint: `${chat?.growth_buckets ?? 0} 个成长分桶`, icon: 'memory' }),
      statCard({ label: '累计 Token', value: compact(tokens), hint: `${compact(chat?.calls ?? 0)} 次模型调用`, icon: 'chart', accent: true }),
      statCard({ label: '表情文件', value: num(chat?.emotes ?? 0), hint: `${num(chat?.learned ?? 0)} 条学习记录`, icon: 'image' }),
    );
    uptimePill.textContent = `已运行 ${duration(data.uptime)} · ${data.environment || 'prod'}`;
    title.textContent = `${greeting()}，Yuna 在这里。`;
    lead.textContent = bots.length
      ? `${bots.length} 个机器人连接保持正常，${data.plugins ?? 0} 个插件已加载。`
      : '还没有机器人在线。等待 SnowLuma 或其它 OneBot 客户端连接后，这里会自动更新。';
  }

  function renderBots(data) {
    const bots = data.bots || [];
    const head = el('div', { class: 'panel-head' },
      el('div', { class: 'panel-title' }, icon('server', 17), el('div', null, el('h2', { text: '机器人连接' }), el('p', { text: '当前已建立的适配器连接' }))),
      el('span', { class: `pill ${bots.length ? 'ok' : 'warn'}` }, el('span', { class: 'badge-dot' }), bots.length ? '在线' : '离线'));
    botsPanel.replaceChildren(head);
    if (!bots.length) {
      botsPanel.append(emptyState('暂无在线机器人', '等待 OneBot V11 / Telegram 客户端连接。', 'server'));
      return;
    }
    const list = el('div', { class: 'list-view' });
    bots.forEach((bot) => {
      list.append(el('div', { class: 'bot-row' },
        el('div', { class: 'bot-id' },
          el('span', { class: 'bot-avatar', text: String(bot.id).slice(-2) }),
          el('div', null, el('strong', { text: bot.id }), el('small', { text: bot.adapter }))),
        el('span', { class: 'pill ok' }, el('span', { class: 'badge-dot' }), '在线')));
    });
    botsPanel.append(list, el('p', { class: 'notice info', text: '连接由 SnowLuma / OneBot 适配器维护；如果这里为空，请检查客户端与访问令牌。' }));
  }

  const qqLoginPanel = el('section', { class: 'panel' });
  mainGrid.before(qqLoginPanel);
  qqLoginPanel.append(el('h2', { text: 'QQ 扫码登录' }), loading('正在读取 QQ 登录状态…'));
  let qqBusy = false;
  async function loadQqLogin() {
    if (qqBusy || document.hidden) return;
    qqBusy = true;
    try {
      const data = await api('qq/login');
      const refresh = el('button', { class: 'button secondary small', type: 'button', text: '刷新二维码 / 状态' });
      refresh.addEventListener('click', () => loadQqLogin());
      qqLoginPanel.replaceChildren(el('div', { class: 'panel-head' },
        el('h2', { text: 'QQ 扫码登录' }), refresh),
        notice(data.message, data.logged_in ? 'info' : 'warn'));
      if (data.logged_in) {
        qqLoginPanel.append(el('p', { text: `已登录 QQ：${data.account}` }));
      } else if (data.qr) {
        qqLoginPanel.append(el('img', { src: data.qr, alt: 'QQ 登录二维码', width: '294', height: '294',
          style: { maxWidth: '100%', height: 'auto', imageRendering: 'pixelated', background: 'white', padding: '12px' } }),
          el('p', { text: '页面每 10 秒同步二维码及状态；过期后客户端会自动更新。扫码后请在手机 QQ 确认。' }));
      }
      qqLoginPanel.hidden = false;
    } catch (error) {
      qqLoginPanel.replaceChildren(el('h2', { text: 'QQ 扫码登录' }),
        notice(error.status === 403 ? '请使用面板所有者账号登录后查看二维码。' : `QQ 登录状态读取失败：${error.message}`, 'warn'));
    } finally { qqBusy = false; }
  }
  void loadQqLogin();
  ctx.interval(loadQqLogin, 10000);

  function renderResources(data, cache) {
    const metrics = data.metrics || {};
    const head = el('div', { class: 'panel-head' },
      el('div', { class: 'panel-title' }, icon('cpu', 17), el('div', null, el('h2', { text: '系统资源' }), el('p', { text: '管理进程与管理服务' }))),
      el('span', { class: 'pill', text: data.environment || 'prod' }));
    const cpu = Number(metrics.cpu);
    const memory = Number(metrics.memory_mb);
    const cachePct = cache ? (cache.bytes / Math.max(1, cache.max_bytes)) * 100 : 0;
    const rows = el('div', { class: 'resource-grid' });
    const addMeter = (label, valueText, percent, tone = '') => {
      rows.append(el('div', { class: 'resource-item' },
        el('div', { class: 'resource-label' }, el('span', { text: label }), el('strong', { text: valueText })),
        el('div', { class: 'meter' }, el('div', { class: `meter-fill ${tone}`, style: { width: `${Math.max(1, Math.min(100, percent))}%` } }))));
    };
    addMeter('CPU 使用率', Number.isFinite(cpu) ? `${cpu}%` : '不可用', Number.isFinite(cpu) ? cpu : 0);
    addMeter('进程内存', Number.isFinite(memory) ? `${Math.round(memory)} MB` : '不可用', Number.isFinite(memory) ? Math.min(100, memory / 8) : 0, 'teal');
    addMeter('图片缩略图缓存', cache ? `${bytes(cache.bytes)} / ${bytes(cache.max_bytes)}` : '—', cachePct, 'amber');
    resourcesPanel.replaceChildren(head, rows,
      el('div', { class: 'divider' }),
      el('div', { class: 'metric-row' }, el('span', { text: '环境' }), el('strong', { text: data.environment || '—' }), el('span', { class: 'muted small', text: `v${data.version || ctx.version}` })),
      el('div', { class: 'metric-row' }, el('span', { text: '管理进程' }), el('strong', { text: '运行中' }), el('span', { class: 'status-dot' })),
      el('div', { class: 'metric-row' }, el('span', { text: '缓存命中' }), el('strong', { text: cache ? `${num(cache.hits)} / ${num(cache.hits + cache.misses)}` : '—' }), el('span', { class: 'muted small', text: cache ? `${cache.files} 张` : '' })));
  }

  function renderMood(data) {
    const human = data.human || {};
    const mood = human.mood || {};
    const label = human.label || '平静';
    const valence = Number(mood.valence) || 0;
    const energy = Number(mood.energy) || 0;
    const irritation = Number(mood.irritation) || 0;
    const head = el('div', { class: 'panel-head' },
      el('div', { class: 'panel-title' }, icon('sparkles', 17), el('div', null, el('h2', { text: '拟人状态' }), el('p', { text: '心情只影响语气与回复长度' }))),
      el('a', { class: 'chip', href: '#/human' }, '详情', icon('arrow-right', 12)));
    const orb = el('div', { class: 'mood-hero', style: { padding: '16px', gridTemplateColumns: 'auto minmax(0,1fr)' } },
      el('div', { class: 'mood-title' }, el('div', { class: 'mood-orb', text: label.slice(0, 1) }),
        el('div', null, el('h2', { text: label, style: { fontSize: '21px' } }), el('p', { text: human.stale ? '还没有互动记录，使用默认状态' : `更新于 ${timeAgo(mood.updated)}` }))),
      el('div', { class: 'mood-meters' },
        moodMeter('心情', valence, -1, 1, ['低落', '开心'], Math.round((valence + 1) * 50), 'teal'),
        moodMeter('精力', energy, 0, 1, ['疲惫', '充沛'], Math.round(energy * 100), 'amber'),
        moodMeter('烦躁', irritation, 0, 1, ['平静', '烦躁'], Math.round(irritation * 100), 'rose')));
    moodPanel.replaceChildren(head, orb);
  }

  function moodMeter(label, value, min, max, words, percent, tone) {
    return el('div', { class: 'mood-meter' },
      el('div', { class: 'mood-meter-label' }, el('span', { text: `${label} · ${words[0]} / ${words[1]}` }), el('strong', { text: percent })),
      el('div', { class: 'meter' }, el('div', { class: `meter-fill ${tone}`, style: { width: `${Math.max(1, Math.min(100, percent))}%` } })));
  }

  function renderLogs(logs) {
    const items = (logs?.items || []).slice(-8).reverse();
    const head = el('div', { class: 'panel-head' },
      el('div', { class: 'panel-title' }, icon('terminal', 17), el('div', null, el('h2', { text: '最近日志' }), el('p', { text: '管理模块捕获的日志' }))),
      el('a', { class: 'chip', href: '#/logs' }, '全部', icon('arrow-right', 12)));
    logsPanel.replaceChildren(head);
    if (!items.length) {
      logsPanel.append(emptyState('暂无日志', '管理服务启动后的 INFO 及以上日志会出现在这里。', 'terminal'));
      return;
    }
    const list = el('div', { class: 'log-list', style: { maxHeight: '250px' } });
    items.forEach((entry) => {
      list.append(el('div', { class: 'log-row' },
        el('span', { class: 'log-time', text: clock(entry.time) }),
        el('span', { class: `log-level ${entry.level}`, text: entry.level }),
        el('span', { class: 'log-message', text: entry.message })));
    });
    logsPanel.append(list);
  }

  function renderQuick() {
    const actions = [
      ['emotes', 'image', '管理表情', '缩略图与分类'],
      ['human', 'sparkles', '调整心情', '查看拟人状态'],
      ['plugins', 'plugins', '插件配置', '开关与参数'],
      ['messages', 'send', '发送消息', '群聊 / 私聊'],
    ];
    const grid = el('div', { class: 'quick-grid' });
    actions.forEach(([path, iconName, titleText, sub]) => {
      const button = el('button', { type: 'button', class: 'quick-action', onclick: () => ctx.navigate(path) },
        icon(iconName, 17), el('div', null, el('strong', { text: titleText }), el('small', { text: sub })));
      grid.append(button);
    });
    quickPanel.replaceChildren(el('div', { class: 'panel-head' }, el('div', { class: 'panel-title' }, icon('bolt', 17), el('div', null, el('h2', { text: '快速入口' }), el('p', { text: '常用操作一步可达' })))), grid);
  }

  function renderCache(cache) {
    const head = el('div', { class: 'panel-head' },
      el('div', { class: 'panel-title' }, icon('cache', 17), el('div', null, el('h2', { text: '媒体缓存' }), el('p', { text: '表情缩略图磁盘缓存' }))),
      el('button', { type: 'button', class: 'button ghost small', onclick: async (event) => {
        const button = event.currentTarget;
        button.disabled = true;
        try {
          const result = await api('media/cache', { method: 'DELETE' });
          toast(`已清空 ${result.removed} 个缩略图，下次访问会重新生成`, 'ok');
          ctx.rerender();
        } catch (error) { toast(error.message, 'error'); } finally { button.disabled = false; }
      } }, icon('trash', 14), '清空'));
    const stats = [
      ['缩略图文件', cache ? `${num(cache.files)} 张` : '—'],
      ['占用空间', cache ? bytes(cache.bytes) : '—'],
      ['命中 / 未命中', cache ? `${num(cache.hits)} / ${num(cache.misses)}` : '—'],
      ['默认宽度', cache ? `${cache.default_width}px` : '—'],
    ];
    const list = el('div', { style: { display: 'grid', gap: '2px' } });
    stats.forEach(([label, value]) => {
      list.append(el('div', { class: 'metric-row', style: { gridTemplateColumns: '1fr auto' } }, el('span', { text: label }), el('strong', { text: value }), el('span')));
    });
    const warm = el('button', { type: 'button', class: 'button secondary small', onclick: async (event) => {
      const button = event.currentTarget;
      button.disabled = true;
      button.replaceChildren(el('span', { class: 'spinner' }), document.createTextNode('正在预热…'));
      try {
        const result = await api('media/warm', { method: 'POST', body: { width: 360 }, timeout: 180000 });
        toast(`已生成 ${result.generated} 张缩略图${result.failed ? `，${result.failed} 张失败` : ''}`, 'ok');
        ctx.rerender();
      } catch (error) { toast(error.message, 'error'); } finally { button.disabled = false; }
    } }, icon('bolt', 14), '预热全部');
    cachePanel.replaceChildren(head, list, el('div', { class: 'row', style: { marginTop: '10px' } }, warm));
  }

  async function load() {
    const [overview, chat, logs, cache] = await Promise.all([
      api('overview'),
      api('chat/overview').catch(() => null),
      api('logs').catch(() => ({ items: [] })),
      api('media/cache').catch(() => null),
    ]);
    renderStats(overview, chat);
    clear(healthBox);
    const warnings = (overview.health?.warnings || []).filter((item) => !item.includes('没有在线机器人连接'));
    if (warnings.length) {
      healthBox.append(notice(`健康检查：${warnings.join('；')}`, 'warn', 'warning'));
    }
    renderBots(overview);
    renderResources(overview, cache);
    renderMood(chat || {});
    renderLogs(logs);
    renderQuick();
    renderCache(cache);
    const badgeNode = document.getElementById('nav-plugin-badge');
    if (badgeNode && overview.disabled) {
      badgeNode.textContent = String(overview.disabled);
      badgeNode.title = `${overview.disabled} 个插件的事件响应已停用`;
    }
    void loadSidebar(overview);
  }

  async function loadSidebar(overview) {
    const node = document.getElementById('sidebar-uptime');
    if (node) node.textContent = `已运行 ${Math.floor((overview.uptime || 0) / 3600)} 小时 · ${(overview.bots || []).length} 个连接`;
  }

  try {
    await load();
  } catch (error) {
    host.replaceChildren(el('div', { class: 'panel' }, emptyState('概览加载失败', error.message, 'warning',
      el('button', { class: 'button secondary', type: 'button', onclick: () => ctx.rerender() }, '重试'))));
    return;
  }
  ctx.interval(() => {
    if (document.hidden) return;
    load().catch(() => {});
  }, 15000);
}
