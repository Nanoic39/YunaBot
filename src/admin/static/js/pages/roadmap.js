'use strict';

import { apiText, download, el, emptyState, icon, markdownLite, panel, toast } from '../core.js';

export async function render(host, ctx) {
  let text = '';
  try {
    text = await apiText('roadmap');
  } catch (error) {
    host.append(panel('开发路线', {}, emptyState('路线文档尚未部署', '把 docs/ROADMAP.md 随项目一起部署后，这里会自动显示。', 'map')));
    return;
  }
  const downloadButton = el('button', { class: 'button secondary small', type: 'button' }, icon('download', 14), '下载 Markdown');
  downloadButton.addEventListener('click', () => {
    download('ROADMAP.md', text, 'text/markdown;charset=utf-8');
    toast('已下载 ROADMAP.md', 'ok');
  });
  const content = markdownLite(text);
  host.append(el('div', { class: 'page-head' },
    el('div', null, el('h2', { text: '开发路线' }), el('p', { text: '从“能用的面板”走向“可持续运营的智能体工作台”。' })),
    el('div', { class: 'page-actions' }, downloadButton)),
    panel('路线图', { subtitle: '与 docs/ROADMAP.md 同步', className: 'roadmap-panel' }, content));
}
