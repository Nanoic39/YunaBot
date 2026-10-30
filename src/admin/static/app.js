'use strict';

import {
  api, apiState, applyTheme, bindSearchShortcut, busy, clear, clock, closeModal,
  confirmDialog, currentTheme, el, errorText, hydrateIcons, icon, iconButton, modal,
  toast, toggleTheme, startLiveEvents, stopLiveEvents,
} from './js/core.js';

/* ==========================================================================
 * Routes
 * ========================================================================== */
const ROUTES = [
  { path: 'agent', nav: 'agent-payments', title: '支付管理', subtitle: '额度、支付权限、审批和历史支付明细。', icon: 'shield', module: './js/pages/agent.js' },
  { path: 'agent-payments', nav: 'agent-payments', title: '支付管理', subtitle: '额度、支付权限、审批和历史支付明细。', icon: 'shield', module: './js/pages/agent.js' },
  { path: 'agent-apps', nav: 'agent-apps', title: 'APP 使用', subtitle: '执行节点实时状态、历史行为概要和详情。', icon: 'activity', module: './js/pages/agent-apps.js' },
  { path: 'qq', nav: 'qq', title: 'QQ 连接与群资源', subtitle: 'SnowLuma 接口诊断、插件适配与群资源查询。', icon: 'server', module: './js/pages/qq.js?v=20261026-autonomy-1' },
  { path: 'overview', nav: 'overview', title: '运行概览', subtitle: '一眼掌握 Yuna 的运行状态。', icon: 'overview', module: './js/pages/overview.js?v=20261026-snowluma-1' },
  { path: 'plugins', nav: 'plugins', title: '插件管理', subtitle: '控制事件响应、查看配置与热重载。', icon: 'plugins', module: './js/pages/plugins.js' },
  { path: 'plugins/:id', nav: 'plugins', title: '插件配置', subtitle: '按插件独立编辑环境变量。', icon: 'plugins', module: './js/pages/plugins.js', detail: true },
  { path: 'chat', nav: 'chat', title: '聊天概览', subtitle: '记忆、人格、表情与拟人状态的总览。', icon: 'chat', module: './js/pages/chat.js' },
  { path: 'users', nav: 'users', title: '用户画像', subtitle: '搜索群友，查看画像标签、记忆、Token 与最近发言。', icon: 'users', module: './js/pages/chat.js' },
  { path: 'users/:qq', nav: 'users', title: '用户详情', subtitle: '一份关于这个人的长期观察。', icon: 'users', module: './js/pages/chat.js', detail: true },
  { path: 'favor', nav: 'favor', title: '好感度', subtitle: '调整好感数值，查看关系阶段与互动情况。', icon: 'heart', module: './js/pages/chat.js' },
  { path: 'favor-ledger', nav: 'favor-ledger', title: '好感扣分账本', subtitle: '扣分依据、原文、申诉与恢复记录。', icon: 'book', module: './js/pages/chat.js' },
  { path: 'human', nav: 'human', title: '拟人状态', subtitle: '心情、精力、关系与防重复策略。', icon: 'sparkles', module: './js/pages/chat.js' },
  { path: 'world', nav: 'world', title: '世界地图', subtitle: 'AI 生成的地点关系、路线、天气与场景细节。', icon: 'map', module: './js/pages/world.js' },
  { path: 'social', nav: 'social', title: '社交能力', subtitle: '资料卡、生日、点赞与 QQ 空间。', icon: 'heart', module: './js/pages/chat.js' },
  { path: 'bots', nav: 'bots', title: '机器人识别', subtitle: '管理机器人 / 其它 AI 名单，避免学错说话风格。', icon: 'users', module: './js/pages/chat.js' },
  { path: 'growth', nav: 'growth', title: '成长人格', subtitle: '她在长期相处里学会的说话方式。', icon: 'sprout', module: './js/pages/chat.js' },
  { path: 'whitelist', nav: 'whitelist', title: '白名单', subtitle: '控制哪些群聊与私聊可以触发聊天插件。', icon: 'shield', module: './js/pages/chat.js' },
  { path: 'emotes', nav: 'emotes', title: '表情仓库', subtitle: '缩略图缓存、分类浏览与上传管理。', icon: 'image', module: './js/pages/chat.js' },
  { path: 'stats', nav: 'stats', title: '统计总览', subtitle: '好感分布、Token 与互动趋势。', icon: 'chart', module: './js/pages/stats.js' },
  { path: 'chat-quality', nav: 'chat-quality', title: '聊天质量', subtitle: '模板化、复读与回复长度的可解释基线。', icon: 'activity', module: './js/pages/chat.js' },
  { path: 'eval', nav: 'eval', title: '对话评测集', subtitle: '真实场景采样、人工标注与提示词变更后的回归对比。', icon: 'check', module: './js/pages/chat.js' },
  { path: 'relations', nav: 'relations', title: '关系图谱', subtitle: '用户之间的称呼与关系，注入群聊防止张冠李戴。', icon: 'users', module: './js/pages/chat.js' },
  { path: 'memory-graph', nav: 'memory-graph', title: '记忆图谱', subtitle: '以她为中心的记忆知识图谱：人、群、话题与关联。', icon: 'memory', module: './js/pages/chat.js' },
  { path: 'sessions', nav: 'sessions', title: '会话记忆', subtitle: '浏览群聊与私聊的记忆文件。', icon: 'sessions', module: './js/pages/sessions.js' },
  { path: 'sessions/:kind/:key', nav: 'sessions', title: '记忆详情', subtitle: '长期事实、情景记忆与近期对话。', icon: 'sessions', module: './js/pages/sessions.js', detail: true },
  { path: 'jobs', nav: 'jobs', title: '定时任务', subtitle: '暂停或恢复 APScheduler 任务。', icon: 'clock', module: './js/pages/system.js' },
  { path: 'config', nav: 'config', title: '环境配置', subtitle: '直接编辑 .env 文件，保存后重启生效。', icon: 'sliders', module: './js/pages/system.js' },
  { path: 'ops', nav: 'ops', title: '运维中心', subtitle: '热重载、数据备份、导入导出与媒体缓存维护。', icon: 'bolt', module: './js/pages/system.js' },
  { path: 'logs', nav: 'logs', title: '运行日志', subtitle: '实时查看管理服务捕获的日志。', icon: 'terminal', module: './js/pages/system.js' },
  { path: 'messages', nav: 'messages', title: '发送消息', subtitle: '通过在线 OneBot V11 机器人发送文本。', icon: 'send', module: './js/pages/system.js' },
  { path: 'admins', nav: 'admins', title: '管理员', subtitle: '账号、在线会话与操作审计。', icon: 'user-cog', module: './js/pages/system.js' },
  { path: 'roadmap', nav: 'roadmap', title: '开发路线', subtitle: '这个项目接下来要往哪里走。', icon: 'map', module: './js/pages/roadmap.js' },
];

function compileRoute(route) {
  const segments = route.path.split('/');
  return { ...route, segments };
}

const COMPILED = ROUTES.map(compileRoute);
const ROUTE_BY_PATH = new Map(ROUTES.map((route) => [route.path, route]));

function parseLocation() {
  const raw = (location.hash || '#/overview').replace(/^#\/?/, '');
  const [pathPart, queryPart] = raw.split('?');
  const path = pathPart.replace(/\/+$/, '') || 'overview';
  const query = new URLSearchParams(queryPart || '');
  return { path, query };
}

function matchRoute(path) {
  const segments = path.split('/').filter(Boolean);
  for (const route of COMPILED) {
    if (route.segments.length !== segments.length) continue;
    const params = {};
    let matched = true;
    for (let index = 0; index < segments.length; index += 1) {
      const pattern = route.segments[index];
      if (pattern.startsWith(':')) {
        params[pattern.slice(1)] = decodeURIComponent(segments[index]);
      } else if (pattern !== segments[index]) {
        matched = false;
        break;
      }
    }
    if (matched) return { route, params };
  }
  return null;
}

/* ==========================================================================
 * App state
 * ========================================================================== */
const app = {
  user: null,
  version: '2.1.0',
  current: null,
  params: {},
  query: new URLSearchParams(),
  routeToken: 0,
  cleanups: [],
  dirty: false,
  timers: new Set(),
  renderedModule: null,
  booted: false,
  listenersBound: false,
  refreshing: false,
  refreshData: null,
  refreshPending: false,
  pageState: null,
};

function addCleanup(fn) {
  if (typeof fn === 'function') app.cleanups.push(fn);
}

function cleanupPage() {
  for (const fn of app.cleanups.splice(0)) {
    try { fn(); } catch (error) { console.warn('cleanup failed', error); }
  }
  for (const timer of app.timers) clearInterval(timer);
  app.timers.clear();
  // 页面级错误提示不应跟着用户跑到下一个页面
  document.getElementById('toast-host')?.replaceChildren();
}

function setDirty(value) {
  app.dirty = !!value;
  document.body.dataset.dirty = app.dirty ? '1' : '';
}

function setHeader(title, subtitle, eyebrow = 'YUNA / ADMINISTRATION') {
  document.getElementById('page-title').textContent = title;
  document.getElementById('page-subtitle').textContent = subtitle || '';
  document.getElementById('page-eyebrow').textContent = eyebrow;
  document.title = `${title} · Yuna 管理`;
}

function setActiveNav(nav) {
  document.querySelectorAll('.nav-item[data-nav]').forEach((button) => {
    button.classList.toggle('active', button.dataset.nav === nav);
  });
}

function closeSidebar() {
  document.getElementById('workspace')?.classList.remove('nav-open');
}

function openSidebar() {
  document.getElementById('workspace')?.classList.add('nav-open');
}

function navigate(path, { replace = false, force = false } = {}) {
  const target = `#/${String(path).replace(/^#?\/?/, '')}`;
  if (location.hash === target) {
    if (force) void renderRoute();
    return;
  }
  if (replace) history.replaceState(null, '', target);
  else history.pushState(null, '', target);
  void renderRoute();
}

async function guardDirty() {
  if (!app.dirty) return true;
  return confirmDialog('当前页面还有未保存的修改，离开后会丢失。', {
    title: '离开当前页面？',
    confirmText: '放弃修改并离开',
    danger: true,
  });
}

/* ==========================================================================
 * Login
 * ========================================================================== */
let loginBusy = false;

function showLogin(message = '') {
  cleanupPage();
  stopLiveEvents();
  closeSidebar();
  closeModal();
  closePalette();
  app.booted = false;
  document.getElementById('workspace').hidden = true;
  document.getElementById('login-screen').hidden = false;
  const error = document.getElementById('login-error');
  if (error) error.textContent = message || '';
  setDirty(false);
  const password = document.getElementById('password');
  if (password) password.value = '';
  setTimeout(() => document.getElementById('password')?.focus(), 80);
}

async function showWorkspace(me) {
  app.user = me || app.user;
  app.version = me?.version || app.version;
  document.getElementById('login-screen').hidden = true;
  document.getElementById('workspace').hidden = false;
  const name = app.user?.username || 'admin';
  const role = app.user?.role === 'owner' ? '所有者' : '管理员';
  document.getElementById('user-name').textContent = name;
  document.getElementById('user-role').textContent = role;
  document.getElementById('user-avatar').textContent = name.slice(0, 1).toUpperCase();
  document.getElementById('sidebar-version').textContent = `v${app.version}`;
  document.getElementById('foot-meta').textContent = `Admin v${app.version} · ${app.user?.environment || 'prod'}`;
  document.getElementById('login-version').textContent = `v${app.version}`;
  applyTheme(currentTheme(), false);
  await renderRoute();
  void loadSidebarMeta();
}

async function loadSidebarMeta() {
  try {
    const [overview, plugins] = await Promise.all([api('overview'), api('plugins')]);
    const disabled = plugins.filter((plugin) => !plugin.enabled).length;
    const badge = document.getElementById('nav-plugin-badge');
    if (badge) {
      badge.textContent = disabled ? String(disabled) : '';
      badge.title = disabled ? `${disabled} 个插件的事件响应已停用` : '';
    }
    const uptime = document.getElementById('sidebar-uptime');
    if (uptime) uptime.textContent = `已运行 ${Math.floor(overview.uptime / 3600)} 小时 · ${overview.bots.length} 个连接`;
  } catch (error) {
    if (error.status !== 401) console.warn('sidebar meta failed', error);
  }
}

function bindLogin() {
  const form = document.getElementById('login-form');
  const password = document.getElementById('password');
  const username = document.getElementById('username');
  const toggle = document.getElementById('password-toggle');
  const error = document.getElementById('login-error');
  try {
    const remembered = localStorage.getItem('yuna-username');
    if (remembered) username.value = remembered;
  } catch { /* ignore */ }
  toggle?.addEventListener('click', () => {
    const visible = password.type === 'text';
    password.type = visible ? 'password' : 'text';
    toggle.replaceChildren(icon(visible ? 'eye' : 'eye-off'));
    toggle.setAttribute('aria-label', visible ? '显示口令' : '隐藏口令');
  });
  form?.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (loginBusy) return;
    const submit = form.querySelector('button[type="submit"]');
    if (!password.value) {
      error.textContent = '请输入管理口令';
      password.focus();
      return;
    }
    loginBusy = true;
    busy(submit, true, '正在验证…');
    error.textContent = '';
    try {
      const me = await api('login', {
        method: 'POST',
        body: { password: password.value, username: username.value.trim() },
      });
      try {
        if (username.value.trim()) localStorage.setItem('yuna-username', username.value.trim());
        else localStorage.removeItem('yuna-username');
      } catch { /* ignore */ }
      password.value = '';
      await bootApp({ ...me, version: me.version || app.version });
    } catch (err) {
      error.textContent = errorText(err);
      password.select();
    } finally {
      loginBusy = false;
      busy(submit, false);
    }
  });
}

/* ==========================================================================
 * Router
 * ========================================================================== */
function controlIdentity(node) {
  return [node.tagName, node.getAttribute('aria-label'), node.name, node.type, node.getAttribute('placeholder')].join('|');
}
function captureView(host) {
  const controls = [...host.querySelectorAll('input, textarea, select, button')];
  const active = document.activeElement;
  return {
    scroll: [document.querySelector('.main'), host, ...host.querySelectorAll('.table-wrap, pre')]
      .filter(Boolean).map((node, index) => ({ index, top: node.scrollTop, left: node.scrollLeft })),
    drafts: controls.flatMap((node, index) => {
      if (!node.matches('input:not([type=password]):not([type=file]):not([type=hidden]), textarea, select')) return [];
      const changed = node.matches('[type=checkbox], [type=radio]') ? node.checked !== node.defaultChecked : node.value !== node.defaultValue;
      return changed ? [{ index, identity: controlIdentity(node), value: node.value,
        baseline: node.defaultValue, checked: node.checked, baselineChecked: node.defaultChecked }] : [];
    }),
    focus: controls.indexOf(active),
    identity: active ? controlIdentity(active) : '',
    value: active && 'value' in active ? active.value : null,
    start: active?.selectionStart, end: active?.selectionEnd,
  };
}
function restoreView(host, view) {
  if (!view) return;
  const scroll = [document.querySelector('.main'), host, ...host.querySelectorAll('.table-wrap, pre')].filter(Boolean);
  view.scroll.forEach(({ index, top, left }) => {
    const node = scroll[index];
    if (node) { node.scrollTo({ top, left, behavior: 'instant' }); }
  });
  const active = host.querySelectorAll('input, textarea, select, button')[view.focus];
  if (active && controlIdentity(active) === view.identity) {
    active.focus({ preventScroll: true });
    if (typeof view.start === 'number') { try { active.setSelectionRange(view.start, view.end); } catch {} }
  }
}

function finishRefresh() {
  app.refreshing = false;
  if (app.refreshPending) {
    app.refreshPending = false;
    queueMicrotask(() => { if (app.user) void renderRoute({ refresh: true }); });
  }
}

async function renderRoute({ refresh = false } = {}) {
  if (!app.user) return;
  const { path, query } = parseLocation();
  const match = matchRoute(path);
  if (!match) {
    setHeader('找不到页面', '这个地址不存在，回到运行概览吧。');
    const host = document.getElementById('page-host');
    host.replaceChildren(el('div', { class: 'panel' },
      el('div', { class: 'empty-state' }, icon('map', 34), el('strong', { text: '页面不存在' }),
        el('p', { text: '链接可能已经过期，或者你输错了地址。' }),
        el('button', { class: 'button primary', type: 'button', onclick: () => navigate('overview', { replace: true }) }, '回到运行概览'))));
    return;
  }
  const { route, params } = match;
  if (!refresh && app.dirty && app.current !== route.path) {
    const canLeave = await guardDirty();
    if (!canLeave) {
      // 恢复地址栏，避免路由与页面不一致
      history.replaceState(null, '', `#/${app.current || 'overview'}`);
      return;
    }
  }
  const samePage = refresh && app.current === path;
  if (samePage && app.refreshing) { app.refreshPending = true; return; }
  if (samePage && app.refreshData) {
    app.refreshing = true;
    const refreshToken = app.routeToken;
    const viewport = captureView(document.getElementById('page-host'));
    try {
      await app.refreshData();
      if (app.routeToken === refreshToken) restoreView(document.getElementById('page-host'), viewport);
    } catch (error) { toast(errorText(error), 'error'); }
    finally { if (app.routeToken === refreshToken) finishRefresh(); }
    return;
  }
  const previousCleanups = samePage ? app.cleanups.splice(0) : [];
  const previousTimers = samePage ? [...app.timers] : [];
  if (samePage) app.timers.clear();
  else { cleanupPage(); app.refreshData = null; app.refreshPending = false; app.pageState = null; }
  if (!samePage) closeModal();
  closePalette();
  closeSidebar();
  setDirty(false);
  app.refreshing = refresh;
  app.current = path;
  app.currentRoute = route;
  app.params = params;
  app.query = query;
  const token = ++app.routeToken;
  setActiveNav(route.nav);
  setHeader(route.title, route.subtitle);
  const visibleHost = document.getElementById('page-host');
  const viewport = samePage ? captureView(visibleHost) : null;
  const host = samePage ? el('div', { class: 'page-content' }) : visibleHost;
  if (!samePage) host.replaceChildren(el('div', { class: 'page-skeleton' },
    el('div', { class: 'skeleton skeleton-title' }),
    el('div', { class: 'skeleton-grid' }, ...Array.from({ length: 4 }, () => el('div', { class: 'skeleton skeleton-card' }))),
    el('div', { class: 'skeleton skeleton-block' })));
  const pageState = (app.pageState ||= { value: null });
  const controller = new AbortController();
  addCleanup(() => controller.abort());
  try {
    const module = await import(route.module);
    if (token !== app.routeToken) return;
    const render = module.render || module.default;
    if (typeof render !== 'function') throw new Error('页面模块缺少 render 导出');
    app.renderedModule = module;
    const ctx = {
      route,
      path,
      params,
      query,
      user: app.user,
      version: app.version,
      signal: controller.signal,
      refresh: app.refreshing,
      navigate,
      setDirty,
      setHeader,
      toast,
      api,
      onCleanup: addCleanup,
      interval: (fn, ms) => {
        const timer = setInterval(fn, ms);
        app.timers.add(timer);
        return timer;
      },
      confirm: confirmDialog,
      modal,
      openLightbox: module.openLightbox,
      onRefresh: (fn) => { ctx.refreshData = fn; },
      state: (create) => (pageState.value ||= create()),
      rerender: () => renderRoute({ refresh: true }),
    };
    host.replaceChildren();
    await render(host, ctx);
    if (token !== app.routeToken) return;
    app.refreshData = ctx.refreshData || null;
    if (samePage) {
      previousCleanups.forEach((fn) => { try { fn(); } catch (error) { console.warn(error); } });
      previousTimers.forEach(clearInterval);
      // Keep the page shell mounted; commit only when the next data view is ready.
      const latestView = captureView(visibleHost);
      const controls = host.querySelectorAll('input, textarea, select, button');
      for (const draft of latestView.drafts) {
        const node = controls[draft.index];
        if (!node || controlIdentity(node) !== draft.identity || node.defaultValue !== draft.baseline || node.defaultChecked !== draft.baselineChecked) continue;
        if (node.matches('[type=checkbox], [type=radio]')) node.checked = draft.checked;
        else node.value = draft.value;
      }
      visibleHost.replaceChildren(host);
      restoreView(visibleHost, latestView);
    } else {
      host.scrollTop = 0;
      host.focus({ preventScroll: true });
    }
    document.getElementById('refresh').disabled = false;
  } catch (error) {
    if (token !== app.routeToken) return;
    console.error(error);
    if (error?.name === 'AbortError') return;
    // 会话过期：直接回到登录页，让用户重新登录而不是留下难排查的错误页
    if (error?.status === 401) {
      if (app.user) { app.user = null; showLogin('登录已过期（服务可能刚重启过），请重新登录'); }
      return;
    }
    if (samePage) {
      cleanupPage();
      app.cleanups.push(...previousCleanups);
      previousTimers.forEach((timer) => app.timers.add(timer));
      toast(`更新失败，保留当前页面：${errorText(error)}`, 'error');
      return;
    }
    setHeader(route.title, '页面加载遇到问题');
    const notReady = error?.status === 501;
    host.replaceChildren(el('div', { class: 'panel' },
      el('div', { class: 'empty-state' },
        icon('warning', 34),
        el('strong', { text: notReady ? '功能还没就绪' : '这个页面没有加载成功' }),
        el('p', { text: notReady ? '依赖的插件尚未加载完成（服务可能刚重启），稍等几秒重试即可。' : errorText(error) }),
        el('div', { class: 'row' },
          el('button', { class: 'button primary', type: 'button', onclick: () => renderRoute({ refresh: true }) }, '重试'),
          el('button', { class: 'button secondary', type: 'button', onclick: () => navigate('overview') }, '回到概览')))));
    toast(notReady ? '插件尚未就绪，稍后重试' : errorText(error), notReady ? 'warn' : 'error');
  } finally {
    if (samePage && token !== app.routeToken) {
      previousCleanups.forEach((fn) => { try { fn(); } catch {} });
      previousTimers.forEach(clearInterval);
    }
    if (token === app.routeToken) finishRefresh();
  }
}

async function refreshCurrent() {
  if (app.refreshing) return;
  if (app.dirty) {
    const ok = await confirmDialog('刷新会丢弃当前页面未保存的修改。', {
      title: '刷新页面？', confirmText: '丢弃并刷新', danger: true,
    });
    if (!ok) return;
  }
  const button = document.getElementById('refresh');
  button.disabled = true;
  button.classList.add('spinning');
  try {
    await renderRoute({ refresh: true });
  } finally {
    button.disabled = false;
    button.classList.remove('spinning');
  }
}

/* ==========================================================================
 * Command palette
 * ========================================================================== */
let paletteItems = [];
let paletteIndex = 0;

function paletteEntries() {
  const entries = ROUTES.filter((route) => !route.detail).map((route) => ({
    id: route.path,
    title: route.title,
    subtitle: route.subtitle,
    icon: route.icon,
    run: () => navigate(route.path),
  }));
  entries.push(
    { id: 'action-refresh', title: '刷新当前页面', subtitle: '重新加载数据', icon: 'refresh', run: () => refreshCurrent() },
    { id: 'action-theme', title: '切换深色 / 浅色模式', subtitle: '界面主题', icon: currentTheme() === 'dark' ? 'sun' : 'moon', run: () => toggleTheme() },
    { id: 'action-emotes', title: '清空图片缩略图缓存', subtitle: '需要时会自动重新生成', icon: 'cache', run: async () => {
      const ok = await confirmDialog('清空后将删除 data/admin/cache/media 下的缩略图，原图不受影响。', {
        title: '清空图片缓存？', confirmText: '清空缓存', danger: true,
      });
      if (!ok) return;
      try {
        const result = await api('media/cache', { method: 'DELETE' });
        toast(`已清空 ${result.removed} 个缩略图`, 'ok');
      } catch (error) { toast(errorText(error), 'error'); }
    } },
    { id: 'action-logout', title: '退出登录', subtitle: '结束当前管理会话', icon: 'logout', run: () => logout() },
  );
  return entries;
}

function openPalette() {
  const palette = document.getElementById('palette');
  const input = document.getElementById('palette-input');
  if (!palette || !input) return;
  palette.hidden = false;
  document.body.style.overflow = 'hidden';
  input.value = '';
  paletteItems = paletteEntries();
  paletteIndex = 0;
  renderPalette('');
  setTimeout(() => input.focus(), 30);
}

function closePalette() {
  const palette = document.getElementById('palette');
  if (!palette) return;
  palette.hidden = true;
  document.body.style.overflow = '';
}

function renderPalette(query) {
  const list = document.getElementById('palette-list');
  if (!list) return;
  const q = query.trim().toLowerCase();
  paletteItems = paletteEntries().filter((item) => !q || `${item.title} ${item.subtitle}`.toLowerCase().includes(q));
  paletteIndex = Math.min(paletteIndex, Math.max(0, paletteItems.length - 1));
  clear(list);
  if (!paletteItems.length) {
    list.append(el('div', { class: 'palette-empty', text: '没有匹配的页面或操作' }));
    return;
  }
  paletteItems.forEach((item, index) => {
    const button = el('button', {
      type: 'button',
      class: `palette-item${index === paletteIndex ? ' active' : ''}`,
      role: 'option',
      onclick: () => { closePalette(); item.run(); },
    },
    el('span', { class: 'palette-icon' }, icon(item.icon, 15)),
    el('span', null, item.title),
    el('small', { text: item.subtitle }));
    button.addEventListener('mousemove', () => {
      if (paletteIndex === index) return;
      paletteIndex = index;
      list.querySelectorAll('.palette-item').forEach((node, i) => node.classList.toggle('active', i === index));
    });
    list.append(button);
  });
  list.querySelector('.palette-item.active')?.scrollIntoView({ block: 'nearest' });
}

function bindPalette() {
  const palette = document.getElementById('palette');
  const input = document.getElementById('palette-input');
  document.getElementById('command-open')?.addEventListener('click', openPalette);
  input?.addEventListener('input', () => { paletteIndex = 0; renderPalette(input.value); });
  input?.addEventListener('keydown', (event) => {
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      paletteIndex = Math.min(paletteIndex + 1, paletteItems.length - 1);
      renderPalette(input.value);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      paletteIndex = Math.max(paletteIndex - 1, 0);
      renderPalette(input.value);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      const item = paletteItems[paletteIndex];
      if (item) { closePalette(); item.run(); }
    }
  });
  palette?.addEventListener('mousedown', (event) => { if (event.target === palette) closePalette(); });
  document.addEventListener('keydown', (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      event.preventDefault();
      if (palette.hidden) openPalette(); else closePalette();
    } else if (event.key === 'Escape' && !palette.hidden) {
      closePalette();
    }
  });
}

/* ==========================================================================
 * Logout
 * ========================================================================== */
async function logout() {
  const ok = await confirmDialog('退出后需要重新输入管理口令。', { title: '退出登录？', confirmText: '退出', danger: true });
  if (!ok) return;
  try { await api('logout', { method: 'POST' }); } catch { /* ignore */ }
  app.user = null;
  app.current = null;
  setDirty(false);
  showLogin();
  toast('已退出登录', 'ok');
}

/* ==========================================================================
 * Boot
 * ========================================================================== */
function bindShell() {
  document.querySelectorAll('.nav-item[data-nav]').forEach((button) => {
    button.addEventListener('click', async () => {
      if (button.dataset.nav === app.current) { closeSidebar(); return; }
      if (!(await guardDirty())) return;
      navigate(button.dataset.nav);
    });
  });
  document.getElementById('refresh')?.addEventListener('click', refreshCurrent);
  document.getElementById('logout')?.addEventListener('click', logout);
  document.getElementById('sidebar-open')?.addEventListener('click', openSidebar);
  document.getElementById('sidebar-close')?.addEventListener('click', closeSidebar);
  document.getElementById('sidebar-scrim')?.addEventListener('click', closeSidebar);
  document.getElementById('theme-toggle')?.addEventListener('click', () => toggleTheme());
  document.getElementById('theme-toggle-sidebar')?.addEventListener('click', () => toggleTheme());
  document.getElementById('user-chip')?.addEventListener('click', () => navigate('admins'));
}

async function bootApp(me) {
  await showWorkspace(me);
  if (!app.listenersBound) {
    bindShell();
    bindPalette();
    bindSearchShortcut();
    window.addEventListener('beforeunload', (event) => {
      if (!app.dirty) return;
      event.preventDefault();
      event.returnValue = '';
    });
    window.addEventListener('hashchange', () => {
      // 初次登录时路由可能还在加载，不应丢掉用户在这段时间里点开的页面。
      if (!app.user) return;
      void renderRoute();
    });
    window.addEventListener('yuna:theme', () => hydrateIcons());
    window.addEventListener('yuna:auth-lost', () => {
      if (!app.user) return;
      app.user = null;
      showLogin('登录已过期，请重新登录');
    });
    app.listenersBound = true;
  }
  hydrateIcons();
  startLiveEvents();
  app.booted = true;
}

function boot() {
  hydrateIcons();
  applyTheme(currentTheme(), false);
  bindLogin();
  apiState.onAuthLost = () => {
    if (app.user) {
      app.user = null;
      showLogin('登录已过期，请重新登录');
    }
  };
  api('me')
    .then((me) => bootApp(me))
    .catch((error) => {
      if (error.status === 401) {
        showLogin();
      } else {
        showLogin(errorText(error));
      }
    });
  document.getElementById('login-version').textContent = `v${app.version}`;
}

boot();
