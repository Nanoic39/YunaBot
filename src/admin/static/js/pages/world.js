'use strict';

import { api, badge, clear, el, emptyState, icon, loading, panel } from '../core.js';

const SVG_NS = 'http://www.w3.org/2000/svg';
const W = 1200;
const H = 800;

function svgEl(name, attrs = {}, text = '') {
  const node = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value !== null && value !== undefined) node.setAttribute(key, String(value));
  }
  if (text) node.textContent = text;
  return node;
}

const GROUP_ANCHORS = {
  home: [285, 430], street: [590, 430], plaza: [720, 430],
  shop: [760, 320], park: [650, 650], school: [980, 170], mall: [980, 440],
};

function layout(nodes) {
  const positions = new Map();
  const byParent = new Map();
  nodes.forEach((node) => {
    const key = node.parent || node.kind || 'street';
    if (!byParent.has(key)) byParent.set(key, []);
    byParent.get(key).push(node);
  });
  const fixed = {
    home: [280, 430], bedroom: [215, 300], living: [320, 360], study: [180, 450],
    kitchen: [330, 485], bathroom: [240, 550], porch: [370, 430],
    street_home: [500, 430], alley: [590, 330], main_street: [585, 535], plaza: [710, 430],
    convenience: [555, 245], cafe: [780, 270], park: [700, 640], school: [980, 140], school_gate: [880, 190],
    mall: [990, 440], mall_1f: [930, 440], mall_2f: [1015, 350], mall_food: [1080, 430], mall_shop: [955, 540],
  };
  for (const node of nodes) {
    if (fixed[node.id]) { positions.set(node.id, fixed[node.id]); continue; }
    const anchor = GROUP_ANCHORS[node.parent] || GROUP_ANCHORS[node.kind] || [600, 400];
    const siblings = byParent.get(node.parent || node.kind) || [];
    const index = siblings.indexOf(node);
    const angle = (index / Math.max(1, siblings.length)) * Math.PI * 2;
    const radius = 80 + (index % 3) * 35;
    positions.set(node.id, [anchor[0] + Math.cos(angle) * radius, anchor[1] + Math.sin(angle) * radius]);
  }
  return positions;
}

function kindTone(kind) {
  return { room: 'home', home: 'home', street: 'street', plaza: 'plaza', shop: 'shop', park: 'park', area: 'area' }[kind] || 'street';
}

export async function render(host, ctx) {
  const data = await api('chat/world');
  const nodes = data.nodes || [];
  const routes = data.all_routes || [];
  const current = data.current || {};
  const positions = layout(nodes);
  const shell = el('div', { class: 'world-shell' });
  const svg = svgEl('svg', { viewBox: `0 0 ${W} ${H}`, class: 'world-svg', role: 'img', 'aria-label': 'Yuna 世界地图' });
  const viewport = svgEl('g', { class: 'world-viewport' });
  svg.append(viewport);
  const detail = el('aside', { class: 'world-detail' });
  const canvas = el('div', { class: 'world-canvas' }, svg);
  shell.append(canvas, detail);
  const head = el('div', { class: 'page-head' },
    el('div', null, el('h2', { text: '世界地图' }), el('p', { text: 'AI 根据地点关系生成布局；拖动平移，滚轮/双指缩放。当前版本只读，不提供编辑。' })),
    el('div', { class: 'page-actions' },
      badge(`${nodes.length} 个地点 · ${routes.length} 条路线`, 'outline'),
      el('button', { class: 'button secondary small', type: 'button', onclick: () => resetView(true) }, icon('map', 14), '回到当前位置'),
      el('button', { class: 'button ghost small', type: 'button', onclick: () => resetView(false) }, icon('map', 14), '全图'),
      el('button', { class: 'button ghost small', type: 'button', onclick: () => ctx.rerender() }, icon('refresh', 14), '刷新')));
  const weather = el('div', { class: 'world-status' },
    el('span', null, icon('sparkles', 14), ` ${data.weather?.text || '未知天气'} · ${data.weather?.temperature ?? '—'}°C`),
    el('span', null, icon('map', 14), ` 当前位置：${current.name || '—'}`),
    el('span', null, icon('map', 14), ` ${nodes.length} 节点 / ${routes.length} 路线`));

  const gEdges = svgEl('g', { class: 'world-edges' });
  const gNodes = svgEl('g', { class: 'world-nodes' });
  viewport.append(gEdges, gNodes);

  const lineFor = (a, b, route) => {
    const p1 = positions.get(a), p2 = positions.get(b);
    if (!p1 || !p2) return;
    const line = svgEl('line', { x1: p1[0], y1: p1[1], x2: p2[0], y2: p2[1], class: 'world-edge' });
    line.append(svgEl('title', {}, `${route.description || ''} · ${route.minutes} 分钟`));
    gEdges.append(line);
  };
  routes.forEach((route) => lineFor(route.from, route.to, route));

  const nodeEls = new Map();
  for (const node of nodes) {
    const pos = positions.get(node.id);
    if (!pos) continue;
    const group = svgEl('g', { class: `world-node ${kindTone(node.kind)}${node.id === current.id ? ' current' : ''}`, transform: `translate(${pos[0]} ${pos[1]})`, tabindex: '0', role: 'button' });
    group.append(svgEl('circle', { r: node.id === current.id ? 21 : 15, class: 'world-node-dot' }));
    group.append(svgEl('text', { x: 0, y: 31, class: 'world-node-label', 'text-anchor': 'middle' }, node.name || node.id));
    group.addEventListener('click', (event) => { event.stopPropagation(); selectNode(node); });
    group.addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); selectNode(node); } });
    nodeEls.set(node.id, group);
    gNodes.append(group);
  }

  function selectNode(node) {
    clear(detail);
    const localRoutes = routes.filter((route) => route.from === node.id || route.to === node.id);
    const parts = [
      el('div', { class: 'world-detail-head' }, el('span', { class: 'world-detail-dot ' + kindTone(node.kind) }), el('div', null, el('h3', { text: node.name || node.id }), el('p', { text: node.kind || '地点' }))),
      node.id === current.id ? badge('当前位置', 'ok') : null,
      el('p', { class: 'muted small', text: node.parent ? `属于 ${nodes.find((x) => x.id === node.parent)?.name || node.parent}` : '独立区域' }),
      el('h4', { text: '可达路线' }),
      localRoutes.length ? el('div', { class: 'world-route-list' }, ...localRoutes.map((route) => {
        const other = route.from === node.id ? route.to : route.from;
        const name = nodes.find((x) => x.id === other)?.name || other;
        return el('div', { class: 'world-route-row' },
          el('span', { text: name }),
          el('small', { text: `${route.minutes} 分钟 · ${route.description || '步行'}` }));
      })) : emptyState('没有记录路线', '这个地点暂时没有可达路线。', 'map'),
      (data.lessons || []).length ? el('p', { class: 'notice info', text: '世界教训：' + data.lessons.slice(-3).map((row) => row.note || row.key).join('；') }) : null,
    ].filter(Boolean);
    detail.append(...parts);
  }

  let scale = 1, tx = 0, ty = 0;
  function applyTransform() {
    const limit = Math.max(0.6, Math.min(4, scale));
    scale = limit;
    viewport.setAttribute('transform', `translate(${tx} ${ty}) scale(${scale})`);
    nodeEls.forEach((node) => node.classList.toggle('zoomed', scale > 1.8));
  }
  function resetView(focusCurrent = true) {
    if (focusCurrent) {
      const p = positions.get(current.id) || [W / 2, H / 2];
      scale = 1.15; tx = W / 2 - p[0] * scale; ty = H / 2 - p[1] * scale;
      selectNode(current);
    } else {
      scale = 0.72; tx = W / 2 - (W / 2) * scale; ty = H / 2 - (H / 2) * scale;
      selectNode(current);
    }
    applyTransform();
  }
  let drag = null;
  svg.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    drag = { x: event.clientX, y: event.clientY, tx, ty };
    svg.setPointerCapture(event.pointerId);
  });
  svg.addEventListener('pointermove', (event) => {
    if (!drag) return;
    const ratio = W / svg.getBoundingClientRect().width;
    tx = drag.tx + (event.clientX - drag.x) * ratio;
    ty = drag.ty + (event.clientY - drag.y) * ratio;
    applyTransform();
  });
  const stopDrag = () => { drag = null; };
  svg.addEventListener('pointerup', stopDrag);
  svg.addEventListener('pointercancel', stopDrag);
  svg.addEventListener('wheel', (event) => {
    event.preventDefault();
    const rect = svg.getBoundingClientRect();
    const mx = (event.clientX - rect.left) * (W / rect.width);
    const my = (event.clientY - rect.top) * (H / rect.height);
    const next = Math.max(0.6, Math.min(4, scale * (event.deltaY < 0 ? 1.12 : 0.89)));
    tx = mx - ((mx - tx) / scale) * next;
    ty = my - ((my - ty) / scale) * next;
    scale = next;
    applyTransform();
  }, { passive: false });

  // 移动端双指缩放
  let lastPinch = 0;
  const pointers = new Map();
  svg.addEventListener('pointerdown', (event) => pointers.set(event.pointerId, { x: event.clientX, y: event.clientY }));
  svg.addEventListener('pointermove', (event) => {
    if (!pointers.has(event.pointerId)) return;
    pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
    if (pointers.size === 2) {
      const [a, b] = [...pointers.values()];
      const dist = Math.hypot(a.x - b.x, a.y - b.y);
      if (lastPinch && dist > 0) {
        const ratio = dist / lastPinch;
        scale = Math.max(0.6, Math.min(4, scale * ratio));
        applyTransform();
      }
      lastPinch = dist;
      drag = null;
    }
  }, { passive: true });
  svg.addEventListener('pointerup', (event) => { pointers.delete(event.pointerId); if (pointers.size < 2) lastPinch = 0; });
  svg.addEventListener('pointercancel', (event) => { pointers.delete(event.pointerId); lastPinch = 0; });

  host.append(head, weather, shell);
  resetView(false);
}
