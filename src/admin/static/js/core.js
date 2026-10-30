'use strict';

/* ==========================================================================
 * Yuna Admin · core helpers
 * 无第三方依赖：DOM、API、格式化、主题、提示、弹窗、路由与少量通用组件。
 * ========================================================================== */

const ICONS = {
  overview: '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  plugins: '<path d="M10 3h4a1 1 0 0 1 1 1v3h3a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1h-3v3a1 1 0 0 1-1 1h-4a1 1 0 0 1-1-1v-3H6a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1h3V4a1 1 0 0 1 1-1Z"/>',
  chat: '<path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 9.6 9.6 0 0 1-3.8-.8L3 21l1.9-4.1A8.3 8.3 0 0 1 3 11.5 8.4 8.4 0 0 1 12 3a8.4 8.4 0 0 1 9 8.5Z"/><path d="M8 10h.01M12 10h.01M16 10h.01"/>',
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13A4 4 0 0 1 16 11"/>',
  heart: '<path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1.1-1.1a5.5 5.5 0 0 0-7.8 7.8l1.1 1.1L12 21l7.8-7.5 1.1-1.1a5.5 5.5 0 0 0-.1-7.8Z"/>',
  sparkles: '<path d="m12 3-1.9 4.8a2 2 0 0 1-1.1 1.1L4 10.8l4.9 1.9a2 2 0 0 1 1.1 1.1L12 18.6l1.9-4.8a2 2 0 0 1 1.1-1.1l4.9-1.9-4.9-1.9a2 2 0 0 1-1.1-1.1L12 3Z"/><path d="M5 3v4M3 5h4M19 17v4M17 19h4"/>',
  sprout: '<path d="M7 20h10M12 20v-8"/><path d="M12 12C12 7 8 4 3 5c0 5 3 8 9 7Z"/><path d="M12 12c0-3 2-5 5-5 2 0 4 1 4 4 0 3-2 5-5 5"/>',
  shield: '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/>',
  image: '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/>',
  chart: '<path d="M3 3v18h18"/><path d="M7 16v-5M12 16V7M17 16v-3"/>',
  sessions: '<path d="M4 4h16v16H4z"/><path d="M4 9h16M9 9v11"/>',
  sliders: '<path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3"/><path d="M1 14h6M9 8h6M17 16h6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  bolt: '<path d="M13 2 3 14h9l-1 8 10-12h-9l1-8Z"/>',
  terminal: '<path d="m4 17 6-6-6-6M12 19h8"/>',
  send: '<path d="m22 2-7 20-4-9-9-4 20-7Z"/><path d="M22 2 11 13"/>',
  'user-cog': '<circle cx="9" cy="7" r="4"/><path d="M2 21v-2a4 4 0 0 1 4-4h5"/><circle cx="17" cy="17" r="3"/><path d="M17 12v2M17 20v2M12 17h2M20 17h2"/>',
  map: '<path d="m3 6 6-3 6 3 6-3v15l-6 3-6-3-6 3V6Z"/><path d="M9 3v15M15 6v15"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/>',
  refresh: '<path d="M20 11a8.1 8.1 0 0 0-14.8-4L3 10"/><path d="M3 4v6h6M4 13a8.1 8.1 0 0 0 14.8 4L21 14"/><path d="M21 20v-6h-6"/>',
  moon: '<path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8Z"/>',
  sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
  menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
  close: '<path d="M18 6 6 18M6 6l12 12"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 15H6L5 6M10 11v5M14 11v5"/>',
  edit: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5Z"/>',
  check: '<path d="m20 6-11 11-5-5"/>',
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  download: '<path d="M12 3v12M7 10l5 5 5-5"/><path d="M5 21h14"/>',
  upload: '<path d="M12 21V9M7 14l5-5 5 5"/><path d="M5 3h14"/>',
  external: '<path d="M15 3h6v6M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
  'arrow-left': '<path d="M19 12H5M12 19l-7-7 7-7"/>',
  'arrow-right': '<path d="M5 12h14M12 5l7 7-7 7"/>',
  more: '<circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/><circle cx="5" cy="12" r="1"/>',
  play: '<path d="m6 3 14 9-14 9V3Z"/>',
  pause: '<path d="M8 4v16M16 4v16"/>',
  cache: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v6c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/><path d="M4 11v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6"/>',
  database: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5"/>',
  warning: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/>',
  ban: '<circle cx="12" cy="12" r="9"/><path d="m5.6 5.6 12.8 12.8"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  filter: '<path d="M3 4h18l-7 8v6l-4 2v-8L3 4Z"/>',
  eye: '<path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6-10-6-10-6Z"/><circle cx="12" cy="12" r="2.5"/>',
  'eye-off': '<path d="m3 3 18 18"/><path d="M10.6 6.1A10.9 10.9 0 0 1 12 6c6.5 0 10 6 10 6a16 16 0 0 1-3 3.6M6.2 6.2A16 16 0 0 0 2 12s3.5 6 10 6a10.6 10.6 0 0 0 4.2-.8"/><path d="M9.9 9.9a3 3 0 0 0 4.2 4.2"/>',
  lock: '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5M21 12H9"/>',
  inbox: '<path d="M4 4h16v16H4z"/><path d="M4 13h4l2 3h4l2-3h4"/>',
  activity: '<path d="M3 12h4l3 8 4-16 3 8h4"/>',
  cpu: '<rect x="4" y="4" width="16" height="16" rx="2"/><rect x="9" y="9" width="6" height="6"/><path d="M9 1v3M15 1v3M9 20v3M15 20v3M1 9h3M1 15h3M20 9h3M20 15h3"/>',
  memory: '<path d="M4 4h16v16H4z"/><path d="M8 4v16M16 4v16M4 8h4M4 16h4M16 8h4M16 16h4"/>',
  tag: '<path d="M20.6 13.4 12 22l-9-9V4h9l8.6 8.6a2 2 0 0 1 0 2.8Z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
  link: '<path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7"/><path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"/>',
  play_circle: '<circle cx="12" cy="12" r="9"/><path d="m10 8 6 4-6 4V8Z"/>',
  server: '<rect x="3" y="4" width="18" height="7" rx="2"/><rect x="3" y="13" width="18" height="7" rx="2"/><path d="M7 7.5h.01M7 16.5h.01"/>',
  message: '<path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h10a4 4 0 0 1 4 4v8Z"/>',
  smile: '<circle cx="12" cy="12" r="9"/><path d="M8 14s1.5 2 4 2 4-2 4-2M9 9h.01M15 9h.01"/>',
  book: '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z"/>',
  layers: '<path d="m12 2 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 17l9 5 9-5"/>',
  flag: '<path d="M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1v18"/>',
  code: '<path d="m8 7-5 5 5 5M16 7l5 5-5 5M13 4l-2 16"/>',
};

/* ---------- DOM ---------- */
export function icon(name, size = 18, className = '') {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('width', String(size));
  svg.setAttribute('height', String(size));
  svg.setAttribute('fill', 'none');
  svg.setAttribute('stroke', 'currentColor');
  svg.setAttribute('stroke-width', '1.8');
  svg.setAttribute('stroke-linecap', 'round');
  svg.setAttribute('stroke-linejoin', 'round');
  svg.setAttribute('aria-hidden', 'true');
  if (className) svg.setAttribute('class', className);
  svg.innerHTML = ICONS[name] || ICONS.info;
  return svg;
}

export function hydrateIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach((node) => {
    if (node.dataset.iconReady === '1') return;
    const svg = icon(node.dataset.icon, Number(node.dataset.iconSize) || 18);
    if (node.tagName === 'SPAN' && !node.textContent.trim()) {
      node.replaceChildren(svg);
    } else {
      node.prepend(svg);
    }
    node.dataset.iconReady = '1';
  });
}

export function el(tag, props = null, ...children) {
  const node = document.createElement(tag);
  if (props) {
    for (const [key, value] of Object.entries(props)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === 'class' || key === 'className') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key === 'html') node.innerHTML = value;
      else if (key === 'dataset') Object.assign(node.dataset, value);
      else if (key === 'style') Object.assign(node.style, value);
      else if (key.startsWith('on') && typeof value === 'function') {
        node.addEventListener(key.slice(2).toLowerCase(), value);
      } else if (key === 'attrs' && value && typeof value === 'object') {
        for (const [name, attrValue] of Object.entries(value)) {
          if (attrValue !== null && attrValue !== undefined && attrValue !== false) {
            node.setAttribute(name, String(attrValue));
          }
        }
      } else if (value === true) node.setAttribute(key, '');
      else node.setAttribute(key, String(value));
    }
  }
  appendChildren(node, children);
  return node;
}

export function appendChildren(node, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
}

export function clear(node) {
  if (node) node.replaceChildren();
  return node;
}

export function svgIcon(name, size) {
  return icon(name, size);
}

export function iconButton(name, label, onClick, className = '') {
  const button = el('button', {
    type: 'button',
    class: `icon-button ${className}`.trim(),
    'aria-label': label,
    title: label,
  }, icon(name));
  if (onClick) button.addEventListener('click', onClick);
  return button;
}

/* ---------- API ---------- */
export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

export const apiState = { onAuthLost: null };

export async function api(path, options = {}) {
  const {
    method = 'GET',
    body,
    timeout = 30000,
    signal: externalSignal,
  } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const onAbort = () => controller.abort();
  if (externalSignal) externalSignal.addEventListener('abort', onAbort, { once: true });
  const headers = { Accept: 'application/json', 'X-Yuna-Admin': '1' };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  try {
    const response = await fetch(`/api/${String(path).replace(/^\/+/, '')}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      credentials: 'same-origin',
      cache: 'no-store',
      signal: controller.signal,
    });
    if (response.status === 401) {
      apiState.onAuthLost?.();
      throw new ApiError('登录已过期，请重新登录', 401);
    }
    const text = await response.text();
    let data = null;
    if (text) {
      try { data = JSON.parse(text); } catch { data = text; }
    }
    if (!response.ok) {
      const detail = data && typeof data === 'object' ? data.detail : data;
      throw new ApiError(typeof detail === 'string' && detail ? detail : `请求失败（${response.status}）`, response.status);
    }
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new ApiError('请求超时，请稍后重试', 0);
    if (error instanceof ApiError) throw error;
    throw new ApiError('网络连接失败，请确认管理服务仍在运行', 0);
  } finally {
    clearTimeout(timer);
    if (externalSignal) externalSignal.removeEventListener('abort', onAbort);
  }
}

export async function apiText(path, options = {}) {
  const response = await fetch(`/api/${String(path).replace(/^\/+/, '')}`, {
    method: options.method || 'GET',
    headers: { 'X-Yuna-Admin': '1' },
    credentials: 'same-origin',
    cache: 'no-store',
  });
  if (response.status === 401) {
    apiState.onAuthLost?.();
    throw new ApiError('登录已过期，请重新登录', 401);
  }
  if (!response.ok) throw new ApiError('文档尚未部署', response.status);
  return response.text();
}

/* ---------- store ---------- */
export const store = {
  map: new Map(),
  get(key) {
    const item = this.map.get(key);
    if (!item) return null;
    if (item.expires && item.expires < Date.now()) {
      this.map.delete(key);
      return null;
    }
    return item.value;
  },
  set(key, value, ttl = 15000) {
    this.map.set(key, { value, expires: ttl ? Date.now() + ttl : 0 });
    return value;
  },
  invalidate(prefix = '') {
    for (const key of [...this.map.keys()]) {
      if (!prefix || key.startsWith(prefix)) this.map.delete(key);
    }
  },
};

/* ---------- format ---------- */
const numberFormat = new Intl.NumberFormat('zh-CN');
const compactFormat = new Intl.NumberFormat('zh-CN', { notation: 'compact', maximumFractionDigits: 1 });

export function num(value) {
  const n = Number(value);
  return Number.isFinite(n) ? numberFormat.format(n) : '—';
}
export function compact(value) {
  const n = Number(value);
  return Number.isFinite(n) ? compactFormat.format(n) : '—';
}
export function bytes(value) {
  const n = Number(value) || 0;
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}
export function duration(seconds) {
  const n = Math.max(0, Number(seconds) || 0);
  const days = Math.floor(n / 86400);
  const hours = Math.floor((n % 86400) / 3600);
  const minutes = Math.floor((n % 3600) / 60);
  if (days) return `${days} 天 ${hours} 小时`;
  if (hours) return `${hours} 小时 ${minutes} 分`;
  if (minutes) return `${minutes} 分`;
  return `${Math.round(n)} 秒`;
}
export function toDate(value) {
  if (!value) return null;
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return null;
  return new Date(n > 1e12 ? n : n * 1000);
}
export function stamp(value, fallback = '—') {
  const date = toDate(value);
  if (!date) return fallback;
  return date.toLocaleString('zh-CN', { hour12: false });
}
export function stampShort(value, fallback = '—') {
  const date = toDate(value);
  if (!date) return fallback;
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false });
}
export function timeAgo(value) {
  const date = toDate(value);
  if (!date) return '—';
  const diff = (Date.now() - date.getTime()) / 1000;
  if (diff < 60) return '刚刚';
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 30) return `${Math.floor(diff / 86400)} 天前`;
  return date.toLocaleDateString('zh-CN');
}
export function clock(value = new Date()) {
  const date = value instanceof Date ? value : toDate(value) || new Date();
  return date.toLocaleTimeString('zh-CN', { hour12: false });
}
export function greeting() {
  const hour = new Date().getHours();
  if (hour < 6) return '夜深了';
  if (hour < 12) return '早上好';
  if (hour < 14) return '中午好';
  if (hour < 18) return '下午好';
  return '晚上好';
}
export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, Number(value) || 0));
}
export function pct(value, total) {
  const t = Number(total) || 0;
  return t ? clamp((Number(value) || 0) / t * 100, 0, 100) : 0;
}

/* ---------- timing ---------- */
export function debounce(fn, wait = 250) {
  let timer = null;
  const wrapped = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
  wrapped.cancel = () => clearTimeout(timer);
  return wrapped;
}
export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/* ---------- live events ---------- */
let liveSocket = null;
let liveRetry = null;
let liveStopped = true;

function setLiveState(online) {
  const chip = document.getElementById('live-chip');
  if (!chip) return;
  chip.hidden = !online;
  chip.classList.toggle('online', online);
  chip.title = online ? '实时事件流已连接' : '实时事件流断开，正在重连';
}

export function startLiveEvents() {
  if (liveSocket && liveSocket.readyState <= 1) return;
  liveStopped = false;
  clearTimeout(liveRetry);
  const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
  try {
    liveSocket = new WebSocket(`${protocol}//${location.host}/api/events/ws`);
  } catch {
    liveRetry = setTimeout(startLiveEvents, 5000);
    return;
  }
  liveSocket.addEventListener('open', () => setLiveState(true));
  liveSocket.addEventListener('message', (message) => {
    try {
      const event = JSON.parse(message.data);
      if (event.type === 'ping') return;
      document.dispatchEvent(new CustomEvent('yuna:event', { detail: event }));
    } catch { /* ignore malformed events */ }
  });
  liveSocket.addEventListener('close', () => {
    setLiveState(false);
    liveSocket = null;
    if (liveStopped) return;
    // 服务器重启或会话过期时不要每 5 秒制造一次 403；先确认登录状态
    fetch('/api/me', { credentials: 'same-origin' }).then((response) => {
      if (response.status === 401) {
        liveStopped = true;
        document.dispatchEvent(new CustomEvent('yuna:auth-lost'));
        return;
      }
      liveRetry = setTimeout(startLiveEvents, 5000);
    }).catch(() => {
      liveRetry = setTimeout(startLiveEvents, 5000);
    });
  });
  liveSocket.addEventListener('error', () => liveSocket?.close());
}

export function stopLiveEvents() {
  liveStopped = true;
  clearTimeout(liveRetry);
  liveRetry = null;
  const socket = liveSocket;
  liveSocket = null;
  try { socket?.close(); } catch { /* ignore */ }
  setLiveState(false);
}

/* ---------- theme ---------- */
export function currentTheme() {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
}
export function applyTheme(theme, persist = true) {
  const next = theme === 'dark' ? 'dark' : 'light';
  document.documentElement.dataset.theme = next;
  if (persist) {
    try { localStorage.setItem('yuna-theme', next); } catch { /* ignore */ }
  }
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute('content', next === 'dark' ? '#12111b' : '#f4f3f8');
  document.querySelectorAll('#theme-toggle, #theme-toggle-sidebar').forEach((button) => {
    button.replaceChildren(icon(next === 'dark' ? 'sun' : 'moon'), document.createTextNode(next === 'dark' ? '浅色模式' : '深色模式'));
    button.dataset.iconReady = '1';
  });
  document.dispatchEvent(new CustomEvent('yuna:theme', { detail: next }));
  return next;
}
export function toggleTheme() {
  return applyTheme(currentTheme() === 'dark' ? 'light' : 'dark');
}

/* ---------- toast ---------- */
const TOAST_ICONS = { ok: 'check', error: 'warning', warn: 'warning', info: 'info' };
export function toast(message, type = 'info', timeout = 4800) {
  const host = document.getElementById('toast-host');
  if (!host) return;
  const node = el('div', { class: `toast ${type}`, role: 'status' },
    icon(TOAST_ICONS[type] || 'info', 15),
    el('div', { text: String(message) }));
  host.append(node);
  const remove = () => {
    if (!node.isConnected) return;
    node.classList.add('leaving');
    setTimeout(() => node.remove(), 220);
  };
  const timer = setTimeout(remove, timeout);
  node.addEventListener('click', () => { clearTimeout(timer); remove(); });
}

/* ---------- modal ---------- */
let modalCleanup = null;
export function closeModal() {
  const host = document.getElementById('modal-host');
  if (!host) return;
  host.hidden = true;
  clear(host);
  document.body.style.overflow = '';
  if (modalCleanup) {
    modalCleanup();
    modalCleanup = null;
  }
}

export function modal({ title, subtitle = '', body, actions = [], wide = false, onClose = null }) {
  closeModal();
  const host = document.getElementById('modal-host');
  const card = el('div', { class: `modal${wide ? ' wide' : ''}`, role: 'dialog', 'aria-modal': 'true', 'aria-label': title || '对话框' });
  const head = el('div', { class: 'modal-head' },
    el('div', null, el('h2', { text: title || '' }), subtitle ? el('p', { text: subtitle }) : null),
    iconButton('close', '关闭', () => { closeModal(); onClose?.(); }));
  const bodyNode = el('div', { class: 'modal-body' });
  if (body) bodyNode.append(body);
  const foot = el('div', { class: 'modal-foot' });
  if (actions.length) {
    for (const action of actions) {
      if (action.spacer) {
        foot.append(el('span', { class: 'spacer' }));
        continue;
      }
      const button = el('button', {
        type: 'button',
        class: `button ${action.className || 'secondary'}`,
        onclick: () => action.onClick?.(closeModal),
      }, action.label);
      foot.append(button);
    }
  } else {
    foot.append(el('button', { type: 'button', class: 'button secondary', onclick: closeModal }, '关闭'));
  }
  card.append(head, bodyNode, foot);
  host.replaceChildren(card);
  host.hidden = false;
  document.body.style.overflow = 'hidden';
  const onKey = (event) => {
    if (event.key === 'Escape') { closeModal(); onClose?.(); }
    if (event.key === 'Tab') {
      const focusable = [...card.querySelectorAll('button, input, select, textarea, a[href]')].filter((n) => !n.disabled);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    }
  };
  const onBackdrop = (event) => {
    if (event.target === host) { closeModal(); onClose?.(); }
  };
  document.addEventListener('keydown', onKey);
  host.addEventListener('mousedown', onBackdrop);
  modalCleanup = () => {
    document.removeEventListener('keydown', onKey);
    host.removeEventListener('mousedown', onBackdrop);
  };
  requestAnimationFrame(() => {
    const focusable = card.querySelector('input, textarea, select, button.primary, button');
    focusable?.focus();
  });
  return card;
}

export function confirmDialog(message, { title = '请确认', confirmText = '确认', danger = false, detail = '' } = {}) {
  return new Promise((resolve) => {
    const body = el('div', { class: 'stack' },
      el('p', { text: message, style: { marginBottom: detail ? '4px' : '0' } }),
      detail ? el('p', { class: 'muted small', text: detail, style: { margin: '0' } }) : null);
    modal({
      title,
      body,
      onClose: () => resolve(false),
      actions: [
        { label: '取消', className: 'secondary', onClick: (close) => { close(); resolve(false); } },
        { label: confirmText, className: danger ? 'danger' : 'primary', onClick: (close) => { close(); resolve(true); } },
      ],
    });
  });
}

/* ---------- UI building blocks ---------- */
export function badge(text, tone = '') {
  return el('span', { class: `pill ${tone}`.trim(), text });
}

export function statusBadge(ok, textOk = '正常', textOff = '停用') {
  return el('span', { class: `pill ${ok ? 'ok' : ''}`.trim() },
    el('span', { class: 'badge-dot' }), ok ? textOk : textOff);
}

export function avatar(text, extra = '') {
  const label = String(text || '?').trim().slice(0, 1).toUpperCase() || '?';
  return el('span', { class: `avatar ${extra}`.trim(), 'aria-hidden': 'true', text: label });
}

export function emptyState(title, text = '', iconName = 'inbox', action = null) {
  const node = el('div', { class: 'empty-state' },
    icon(iconName, 34),
    el('strong', { text: title }),
    text ? el('p', { text }) : null);
  if (action) node.append(action);
  return node;
}

export function notice(text, tone = 'info', iconName = 'info') {
  return el('div', { class: `notice ${tone}`.trim() }, icon(iconName, 16), el('div', { text }));
}

export function statCard({ label, value, hint = '', icon: iconName = 'activity', accent = false, unit = '' }) {
  return el('div', { class: `stat-card${accent ? ' accent' : ''}` },
    el('div', { class: 'stat-label' }, icon(iconName, 13), label),
    el('div', { class: 'stat-value' }, String(value), unit ? el('small', { text: unit }) : null),
    hint ? el('div', { class: 'stat-hint', text: hint }) : null);
}

export function panel(title, { subtitle = '', icon: iconName = '', actions = null, className = '' } = {}, ...children) {
  const head = el('div', { class: 'panel-head' },
    el('div', { class: 'panel-title' },
      iconName ? icon(iconName, 17) : null,
      el('div', null, el('h2', { text: title }), subtitle ? el('p', { text: subtitle }) : null)),
    actions ? el('div', { class: 'panel-actions' }, actions) : null);
  return el('section', { class: `panel ${className}`.trim() }, head, ...children);
}

export function pageHead(title, subtitle = '', actions = null) {
  return el('div', { class: 'page-head' },
    el('div', null, el('h2', { text: title }), subtitle ? el('p', { text: subtitle }) : null),
    actions ? el('div', { class: 'page-actions' }, actions) : null);
}

export function switchControl(checked, onChange, label = '') {
  const input = el('input', { type: 'checkbox', checked: !!checked, 'aria-label': label || '切换' });
  const wrapper = el('label', { class: 'switch' },
    input, el('span', { class: 'switch-track' }),
    label ? el('span', { text: label }) : null);
  input.addEventListener('change', () => onChange(input.checked));
  wrapper.input = input;
  return wrapper;
}

export function meter(value, total, tone = '') {
  const width = `${clamp(pct(value, total), 0, 100)}%`;
  return el('div', { class: 'meter', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-valuenow': String(Math.round(clamp(pct(value, total), 0, 100))) },
    el('div', { class: `meter-fill ${tone}`.trim(), style: { width } }));
}

export function table(columns, rows, { empty = '暂无数据', onRow = null } = {}) {
  if (!rows.length) return emptyState(empty);
  const tableNode = el('table', { class: 'data-table' });
  const thead = el('thead');
  const headRow = el('tr');
  for (const column of columns) {
    headRow.append(el('th', {
      class: [column.className, column.sortable ? 'sortable' : ''].filter(Boolean).join(' '),
      scope: 'col',
    }, column.label));
  }
  thead.append(headRow);
  const tbody = el('tbody');
  for (const row of rows) {
    const tr = el('tr');
    for (const column of columns) {
      const td = el('td', { class: column.cellClass || '' });
      const value = column.render ? column.render(row) : row[column.key];
      if (value instanceof Node) td.append(value);
      else td.textContent = value === null || value === undefined ? '—' : String(value);
      tr.append(td);
    }
    if (onRow) {
      tr.style.cursor = 'pointer';
      tr.addEventListener('click', (event) => {
        if (event.target.closest('button, a, input, select, label')) return;
        onRow(row);
      });
    }
    tbody.append(tr);
  }
  tableNode.append(thead, tbody);
  return el('div', { class: 'table-wrap' }, tableNode);
}

export function segments(options, value, onChange) {
  const wrap = el('div', { class: 'pill-row' });
  for (const option of options) {
    const button = el('button', {
      type: 'button',
      class: `chip${option.value === value ? ' active' : ''}`,
      onclick: () => onChange(option.value),
    }, option.label, option.count !== undefined ? el('span', { class: 'count', text: String(option.count) }) : null);
    wrap.append(button);
  }
  return wrap;
}

export function searchField(placeholder, onInput, value = '') {
  const input = el('input', { type: 'search', placeholder, value, 'aria-label': placeholder });
  input.addEventListener('input', debounce(() => onInput(input.value.trim()), 180));
  return el('div', { class: 'search-field' }, icon('search', 15), input);
}

export function field(label, input, { hint = '', extra = '' } = {}) {
  return el('label', { class: 'field' },
    el('span', null, el('span', { text: label }), extra ? el('small', { text: extra }) : null),
    input,
    hint ? el('small', { class: 'form-hint', text: hint }) : null);
}

export function busy(button, isBusy = true, label = '处理中…') {
  if (!button) return;
  if (isBusy) {
    if (!button.dataset.idleHtml) button.dataset.idleHtml = button.innerHTML;
    button.disabled = true;
    button.replaceChildren(el('span', { class: 'spinner' }), document.createTextNode(label));
  } else {
    button.disabled = false;
    if (button.dataset.idleHtml) {
      button.innerHTML = button.dataset.idleHtml;
      delete button.dataset.idleHtml;
    }
  }
}

export function copyText(text, successMessage = '已复制到剪贴板') {
  const value = String(text ?? '');
  const done = () => toast(successMessage, 'ok');
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(value).then(done).catch(() => fallbackCopy(value, done));
  } else {
    fallbackCopy(value, done);
  }
}
function fallbackCopy(value, done) {
  const area = el('textarea', { style: { position: 'fixed', opacity: '0' } });
  area.value = value;
  document.body.append(area);
  area.select();
  try { document.execCommand('copy'); done(); } catch { toast('复制失败，请手动选择文本', 'error'); }
  area.remove();
}

export function download(filename, content, type = 'text/plain;charset=utf-8') {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const link = el('a', { href: url, download: filename });
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function pagerControls({ page, pageCount, total, onPage, unit = '条' }) {
  /* 通用分页条：page 从 1 开始；pageCount <= 1 时仍显示总数但隐藏翻页按钮 */
  const bar = el('div', { class: 'pager' });
  const info = el('span', { class: 'muted small', text: pageCount > 1 ? `第 ${page} / ${pageCount} 页 · 共 ${num(total)} ${unit}` : `共 ${num(total)} ${unit}` });
  if (pageCount > 1) {
    const prev = el('button', { class: 'button ghost xs', type: 'button' }, '‹ 上一页');
    prev.disabled = page <= 1;
    prev.addEventListener('click', () => onPage(Math.max(1, page - 1)));
    const next = el('button', { class: 'button ghost xs', type: 'button' }, '下一页 ›');
    next.disabled = page >= pageCount;
    next.addEventListener('click', () => onPage(Math.min(pageCount, page + 1)));
    bar.append(prev, info, next);
  } else {
    bar.append(info);
  }
  return bar;
}

export function pageSlice(items, page, pageSize = 20) {
  const total = (items || []).length;
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const current = Math.min(Math.max(1, page), pageCount);
  return { rows: (items || []).slice((current - 1) * pageSize, current * pageSize), page: current, pageCount, total };
}

export function downloadCSV(filename, columns, rows) {
  const escape = (value) => {
    const text = value === null || value === undefined ? '' : String(value);
    return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
  };
  const lines = [columns.map((column) => escape(column.label)).join(',')];
  for (const row of rows) {
    lines.push(columns.map((column) => escape(column.csv ? column.csv(row) : row[column.key])).join(','));
  }
  download(filename, '\ufeff' + lines.join('\r\n'), 'text/csv;charset=utf-8');
}

/* ---------- lightbox ---------- */
export function openLightbox(items, startIndex = 0) {
  let index = clamp(startIndex, 0, items.length - 1);
  const overlay = el('div', { class: 'lightbox', role: 'dialog', 'aria-modal': 'true', 'aria-label': '图片预览' });
  const image = el('img', { alt: '' });
  const title = el('strong');
  const meta = el('small');
  const counter = el('span');
  const prev = iconButton('arrow-left', '上一张', () => move(-1));
  const next = iconButton('arrow-right', '下一张', () => move(1));
  const close = iconButton('close', '关闭', () => destroy());
  const head = el('div', { class: 'lightbox-head' }, el('div', { class: 'stack-sm' }, title, meta), el('div', { class: 'row' }, el('span', { class: 'pill outline' }, counter), close));
  const body = el('div', { class: 'lightbox-body' }, image);
  const foot = el('div', { class: 'lightbox-foot' }, prev, el('span', { text: '← → 切换 · Esc 关闭' }), next);
  overlay.append(head, body, foot);
  document.body.append(overlay);
  document.body.style.overflow = 'hidden';
  const render = () => {
    const item = items[index];
    title.textContent = item.label || item.name || item.path || `图片 ${index + 1}`;
    meta.textContent = [item.category, item.size ? bytes(item.size) : ''].filter(Boolean).join(' · ');
    counter.textContent = `${index + 1} / ${items.length}`;
    image.src = item.url || item.src;
    image.alt = title.textContent;
    prev.disabled = index <= 0;
    next.disabled = index >= items.length - 1;
  };
  const move = (delta) => {
    index = clamp(index + delta, 0, items.length - 1);
    render();
  };
  const onKey = (event) => {
    if (event.key === 'Escape') destroy();
    else if (event.key === 'ArrowLeft') { event.preventDefault(); move(-1); }
    else if (event.key === 'ArrowRight') { event.preventDefault(); move(1); }
  };
  const destroy = () => {
    document.removeEventListener('keydown', onKey);
    overlay.remove();
    document.body.style.overflow = '';
  };
  document.addEventListener('keydown', onKey);
  overlay.addEventListener('click', (event) => { if (event.target === overlay || event.target === body) destroy(); });
  render();
  close.focus();
  return { close: destroy, refresh: render };
}

/* ---------- markdown-lite ---------- */
function inlineMarkdown(value) {
  const text = String(value ?? '');
  const nodes = [];
  const pattern = /(\*\*[^*\n]+\*\*|`[^`\n]+`|\*[^*\n]+\*|\[[^\]]+\]\((?:https?:\/\/|\/)[^)]+\))/g;
  let last = 0;
  let match;
  while ((match = pattern.exec(text))) {
    if (match.index > last) nodes.push(document.createTextNode(text.slice(last, match.index)));
    const token = match[0];
    if (token.startsWith('**')) nodes.push(el('strong', { text: token.slice(2, -2) }));
    else if (token.startsWith('`')) nodes.push(el('code', { text: token.slice(1, -1) }));
    else if (token.startsWith('[')) {
      const link = /^\[([^\]]+)\]\((.+)\)$/.exec(token);
      nodes.push(el('a', { href: link?.[2] || '#', target: '_blank', rel: 'noreferrer' }, link?.[1] || token));
    } else nodes.push(el('em', { text: token.slice(1, -1) }));
    last = match.index + token.length;
  }
  if (last < text.length) nodes.push(document.createTextNode(text.slice(last)));
  return nodes;
}

export function markdownLite(text) {
  const root = el('div', { class: 'markdown-body' });
  const lines = String(text || '').replaceAll('\r\n', '\n').split('\n');
  let list = null;
  let code = null;
  const flushList = () => { if (list) { root.append(list); list = null; } };
  const flushCode = () => {
    if (code) {
      root.append(el('pre', { class: 'code-block' }, el('code', { text: code.lines.join('\n') })));
      code = null;
    }
  };
  const splitRow = (line) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((cell) => cell.trim());
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (line.trim().startsWith('```')) {
      if (code) flushCode();
      else { flushList(); code = { lines: [] }; }
      index += 1;
      continue;
    }
    if (code) { code.lines.push(line); index += 1; continue; }
    if (!line.trim()) { flushList(); index += 1; continue; }

    // 表格：至少需要表头 + 分隔行
    const next = lines[index + 1] || '';
    if (/^\s*\|/.test(line) && /^\s*\|?[\s:|-]+\|?\s*$/.test(next) && next.includes('-')) {
      flushList();
      const header = splitRow(line);
      const tableNode = el('table', { class: 'data-table' });
      const thead = el('thead');
      const headRow = el('tr');
      header.forEach((cell) => headRow.append(el('th', null, ...inlineMarkdown(cell))));
      thead.append(headRow);
      const tbody = el('tbody');
      index += 2;
      while (index < lines.length && /^\s*\|/.test(lines[index])) {
        const cells = splitRow(lines[index]);
        const row = el('tr');
        for (let i = 0; i < header.length; i += 1) {
          row.append(el('td', null, ...inlineMarkdown(cells[i] || '')));
        }
        tbody.append(row);
        index += 1;
      }
      tableNode.append(thead, tbody);
      root.append(el('div', { class: 'table-wrap' }, tableNode));
      continue;
    }

    if (/^\s*(?:---+|\*\*\*+|___+)\s*$/.test(line)) {
      flushList();
      root.append(el('hr'));
      index += 1;
      continue;
    }
    const heading = /^(#{1,4})\s+(.*)$/.exec(line);
    if (heading) {
      flushList();
      const level = Math.min(4, heading[1].length + 1);
      root.append(el(`h${level}`, null, ...inlineMarkdown(heading[2])));
      index += 1;
      continue;
    }
    if (/^\s*[-*]\s+/.test(line)) {
      if (!list) list = el('ul');
      list.append(el('li', null, ...inlineMarkdown(line.replace(/^\s*[-*]\s+/, ''))));
      index += 1;
      continue;
    }
    if (/^\s*\d+[.、]\s+/.test(line)) {
      if (!list) list = el('ol');
      list.append(el('li', null, ...inlineMarkdown(line.replace(/^\s*\d+[.、]\s+/, ''))));
      index += 1;
      continue;
    }
    const quote = /^>\s?(.*)$/.exec(line);
    if (quote) {
      flushList();
      root.append(el('blockquote', null, ...inlineMarkdown(quote[1])));
      index += 1;
      continue;
    }
    flushList();
    root.append(el('p', null, ...inlineMarkdown(line)));
    index += 1;
  }
  flushList();
  flushCode();
  return root;
}

/* ---------- misc ---------- */
export function sortBy(rows, key, direction = 1, numeric = false) {
  return [...rows].sort((a, b) => {
    const av = typeof key === 'function' ? key(a) : a[key];
    const bv = typeof key === 'function' ? key(b) : b[key];
    if (numeric) return ((Number(av) || 0) - (Number(bv) || 0)) * direction;
    return String(av ?? '').localeCompare(String(bv ?? ''), 'zh-CN') * direction;
  });
}

export function groupBy(rows, key) {
  const map = new Map();
  for (const row of rows) {
    const value = typeof key === 'function' ? key(row) : row[key];
    if (!map.has(value)) map.set(value, []);
    map.get(value).push(row);
  }
  return map;
}

export function errorText(error) {
  return error instanceof Error ? error.message : String(error);
}

export function loading(text = '加载中…') {
  return el('div', { class: 'loading-line' }, el('span', { class: 'spinner' }), text);
}

export function bindSearchShortcut() {
  document.addEventListener('keydown', (event) => {
    if (event.key !== '/' || event.metaKey || event.ctrlKey || event.altKey) return;
    const target = event.target;
    if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
    const input = document.querySelector('.page-host input[type="search"], .page-host input[type="text"]');
    if (input) { event.preventDefault(); input.focus(); }
  });
}
