'use strict';

/* 轻量 SVG 图表：不引入图表库，保持管理面板零构建、零外部依赖。 */

const NS = 'http://www.w3.org/2000/svg';
const PALETTE = ['#7c6cf0', '#36b79b', '#e0a23d', '#de6b8c', '#5a9fe0', '#a78bfa', '#4ecdc4', '#f18f6d'];

export function svg(tag, attrs = {}) {
  const node = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== null && value !== undefined) node.setAttribute(key, String(value));
  }
  return node;
}

function text(content, attrs = {}) {
  const node = svg('text', attrs);
  node.textContent = content;
  return node;
}

function clipLabel(label, max = 8) {
  const value = String(label ?? '');
  return value.length > max ? `${value.slice(0, max)}…` : value;
}

function formatValue(value) {
  const n = Number(value) || 0;
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${(n / 10_000).toFixed(1)}万`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(Math.round(n));
}

let tooltip = null;
function ensureTooltip() {
  if (!tooltip) {
    tooltip = document.createElement('div');
    tooltip.className = 'chart-tooltip';
    tooltip.hidden = true;
    document.body.append(tooltip);
  }
  return tooltip;
}
function bindTooltip(node, content) {
  node.addEventListener('mouseenter', () => {
    const tip = ensureTooltip();
    tip.textContent = content;
    tip.hidden = false;
  });
  node.addEventListener('mousemove', (event) => {
    const tip = ensureTooltip();
    tip.style.left = `${event.clientX}px`;
    tip.style.top = `${event.clientY}px`;
  });
  node.addEventListener('mouseleave', () => {
    if (tooltip) tooltip.hidden = true;
  });
}

export function barChart(rows, { height = 250, tone = 'primary', maxLabels = 16, valueSuffix = '' } = {}) {
  const data = (rows || []).slice(0, maxLabels).map((row) => ({
    label: String(row.label ?? ''),
    value: Number(row.value) || 0,
    extra: row.extra || '',
  }));
  const wrap = document.createElement('div');
  if (!data.length || data.every((row) => row.value === 0)) {
    wrap.className = 'chart-empty';
    wrap.textContent = '暂无数据';
    return wrap;
  }
  const width = 680;
  const pad = { top: 24, right: 16, bottom: 34, left: 38 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const max = Math.max(1, ...data.map((row) => row.value));
  const root = svg('svg', {
    viewBox: `0 0 ${width} ${height}`,
    class: 'chart-svg',
    role: 'img',
    'aria-label': '柱状图',
  });
  const defs = svg('defs');
  const gradient = svg('linearGradient', { id: `bar-gradient-${tone}`, x1: '0', y1: '0', x2: '0', y2: '1' });
  gradient.append(
    svg('stop', { offset: '0', 'stop-color': tone === 'teal' ? '#36b79b' : tone === 'amber' ? '#e0a23d' : '#7c6cf0' }),
    svg('stop', { offset: '1', 'stop-color': tone === 'teal' ? '#a7ead9' : tone === 'amber' ? '#f5dda5' : '#c8c0ff' }),
  );
  defs.append(gradient);
  root.append(defs);

  const gridCount = 4;
  for (let i = 0; i <= gridCount; i += 1) {
    const y = pad.top + (plotH / gridCount) * i;
    root.append(svg('line', { x1: pad.left, y1: y, x2: width - pad.right, y2: y, class: 'chart-grid-line' }));
    root.append(text(formatValue(max * (1 - i / gridCount)), {
      x: pad.left - 8, y: y + 3, class: 'chart-axis-label', 'text-anchor': 'end',
    }));
  }

  const slot = plotW / data.length;
  const barWidth = Math.max(8, Math.min(42, slot * 0.58));
  data.forEach((row, index) => {
    const barHeight = Math.max(2, (row.value / max) * plotH);
    const x = pad.left + slot * index + (slot - barWidth) / 2;
    const y = pad.top + plotH - barHeight;
    const rect = svg('rect', {
      x, y, width: barWidth, height: barHeight, rx: Math.min(6, barWidth / 2),
      fill: `url(#bar-gradient-${tone})`,
      class: 'chart-bar',
    });
    bindTooltip(rect, `${row.label}：${formatValue(row.value)}${valueSuffix}${row.extra ? ` · ${row.extra}` : ''}`);
    root.append(rect);
    if (data.length <= 12) {
      root.append(text(formatValue(row.value), {
        x: x + barWidth / 2, y: y - 6, class: 'chart-value-label', 'text-anchor': 'middle',
      }));
    }
    const labelY = height - 11;
    root.append(text(clipLabel(row.label, slot < 44 ? 5 : 8), {
      x: x + barWidth / 2, y: labelY, class: 'chart-axis-label', 'text-anchor': 'middle',
    }));
  });
  wrap.append(root);
  return wrap;
}

export function donutChart(parts, { height = 230, centerLabel = '总计', format = formatValue } = {}) {
  const data = (parts || []).filter((part) => Number(part.value) > 0);
  const wrap = document.createElement('div');
  if (!data.length) {
    wrap.className = 'chart-empty';
    wrap.textContent = '暂无数据';
    return wrap;
  }
  const total = data.reduce((sum, part) => sum + Number(part.value), 0);
  const size = Math.min(280, height * 1.15);
  const radius = size / 2 - 20;
  const inner = radius * 0.62;
  const center = size / 2;
  const root = svg('svg', {
    viewBox: `0 0 ${size} ${size}`,
    class: 'chart-donut',
    role: 'img',
    'aria-label': '环形图',
    style: `max-width:${size}px`,
  });
  let angle = -Math.PI / 2;
  data.forEach((part, index) => {
    const sweep = (Number(part.value) / total) * Math.PI * 2;
    const end = angle + sweep;
    const large = sweep > Math.PI ? 1 : 0;
    const x1 = center + radius * Math.cos(angle);
    const y1 = center + radius * Math.sin(angle);
    const x2 = center + radius * Math.cos(end);
    const y2 = center + radius * Math.sin(end);
    const x3 = center + inner * Math.cos(end);
    const y3 = center + inner * Math.sin(end);
    const x4 = center + inner * Math.cos(angle);
    const y4 = center + inner * Math.sin(angle);
    const path = svg('path', {
      d: `M ${x1} ${y1} A ${radius} ${radius} 0 ${large} 1 ${x2} ${y2} L ${x3} ${y3} A ${inner} ${inner} 0 ${large} 0 ${x4} ${y4} Z`,
      fill: PALETTE[index % PALETTE.length],
      opacity: '0.92',
    });
    bindTooltip(path, `${part.label}：${format(part.value)}（${((Number(part.value) / total) * 100).toFixed(1)}%）`);
    root.append(path);
    angle = end;
  });
  root.append(text(format(total), {
    x: center, y: center + 2, 'text-anchor': 'middle',
    fill: 'var(--ink)', 'font-size': '20', 'font-weight': '700',
  }));
  root.append(text(centerLabel, {
    x: center, y: center + 20, 'text-anchor': 'middle',
    fill: 'var(--muted)', 'font-size': '10',
  }));
  wrap.append(root);
  const legend = document.createElement('div');
  legend.className = 'chart-legend';
  data.forEach((part, index) => {
    const item = document.createElement('span');
    item.className = 'legend-item';
    const swatch = document.createElement('span');
    swatch.className = 'legend-swatch';
    swatch.style.background = PALETTE[index % PALETTE.length];
    item.append(swatch, document.createTextNode(`${part.label} ${format(part.value)}`));
    legend.append(item);
  });
  wrap.append(legend);
  wrap.style.display = 'grid';
  wrap.style.justifyItems = 'center';
  return wrap;
}

export function sparkline(values, { width = 160, height = 36, color = '#7c6cf0' } = {}) {
  const data = (values || []).map(Number);
  const wrap = document.createElement('div');
  if (data.length < 2) return wrap;
  const max = Math.max(...data, 1);
  const min = Math.min(...data, 0);
  const span = max - min || 1;
  const points = data.map((value, index) => {
    const x = (index / (data.length - 1)) * width;
    const y = height - 4 - ((value - min) / span) * (height - 8);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(' ');
  const root = svg('svg', { viewBox: `0 0 ${width} ${height}`, class: 'chart-svg', style: `max-width:${width}px` });
  root.append(svg('polyline', {
    points, fill: 'none', stroke: color, 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
  }));
  wrap.append(root);
  return wrap;
}

export function barList(rows, { format = formatValue } = {}) {
  const list = document.createElement('div');
  list.className = 'stack-sm';
  const max = Math.max(1, ...rows.map((row) => Number(row.value) || 0));
  for (const row of rows) {
    const item = document.createElement('div');
    item.className = 'metric-row';
    item.style.gridTemplateColumns = '84px minmax(0, 1fr) auto';
    const label = document.createElement('span');
    label.textContent = row.label;
    const track = document.createElement('div');
    track.className = 'meter';
    const fill = document.createElement('div');
    fill.className = `meter-fill ${row.tone || ''}`;
    fill.style.width = `${((Number(row.value) || 0) / max) * 100}%`;
    track.append(fill);
    const value = document.createElement('strong');
    value.textContent = format(row.value);
    item.append(label, track, value);
    list.append(item);
  }
  return list;
}


export function radarChart(rows, { size = 260, max = 100 } = {}) {
  /* 维度雷达图：rows = [{ label, value(0-max) }]，至少 3 维 */
  const data = (rows || []).filter((row) => row && row.label).slice(0, 8);
  const wrap = document.createElement('div');
  if (data.length < 3) {
    wrap.className = 'chart-empty';
    wrap.textContent = '维度不足，无法绘制雷达图';
    return wrap;
  }
  const cx = size / 2;
  const cy = size / 2;
  const radius = size / 2 - 42;
  const step = (Math.PI * 2) / data.length;
  const point = (index, ratio) => {
    const angle = -Math.PI / 2 + index * step;
    return [cx + Math.cos(angle) * radius * ratio, cy + Math.sin(angle) * radius * ratio];
  };
  const root = svg('svg', { viewBox: `0 0 ${size} ${size}`, class: 'chart-svg radar-svg', role: 'img' });
  for (let ring = 1; ring <= 4; ring += 1) {
    const ratio = ring / 4;
    const poly = svg('polygon', {
      points: data.map((_, index) => point(index, ratio).join(',')).join(' '),
      class: 'radar-ring',
    });
    root.append(poly);
  }
  data.forEach((row, index) => {
    const [x, y] = point(index, 1);
    root.append(svg('line', { x1: cx, y1: cy, x2: x, y2: y, class: 'radar-axis' }));
    const [lx, ly] = point(index, 1.18);
    const label = text(clipLabel(row.label, 6), {
      x: lx, y: ly, class: 'radar-label',
      'text-anchor': lx > cx + 6 ? 'start' : lx < cx - 6 ? 'end' : 'middle',
      'dominant-baseline': 'middle',
    });
    root.append(label);
  });
  const shape = svg('polygon', {
    points: data.map((row, index) => {
      const value = Math.max(0, Math.min(max, Number(row.value) || 0));
      return point(index, value / max).join(',');
    }).join(' '),
    class: 'radar-shape',
  });
  bindTooltip(shape, data.map((row) => `${row.label}：${Math.round(Number(row.value) || 0)}`).join(' · '));
  root.append(shape);
  data.forEach((row, index) => {
    const value = Math.max(0, Math.min(max, Number(row.value) || 0));
    const [px, py] = point(index, value / max);
    root.append(svg('circle', { cx: px, cy: py, r: 3, class: 'radar-dot' }));
  });
  wrap.append(root);
  return wrap;
}
