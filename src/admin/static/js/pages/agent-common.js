import { api, busy, el, errorText, toast } from '../core.js';
const money = (minor) => `¥${(minor / 100).toFixed(2)}`;
function newKey() { return crypto.randomUUID?.() || `task-${Date.now()}-${Math.random().toString(36).slice(2)}`; }
const stateNames = { pending: '待审批', authorized: '已授权 · 已预占', simulated: '模拟完成', cancelled: '已取消', expired: '已过期', running: '执行中', unknown: '结果待核实' };

function input(value = '', type = 'text') {
  return el('input', { type, value: String(value), required: true });
}
function amount(value = 0) {
  return el('input', { type: 'number', value: (value / 100).toFixed(2), min: '0', max: '1000000', step: '0.01', required: true });
}
function minor(node) {
  const text = node.value.trim();
  if (!/^\d+(\.\d{1,2})?$/.test(text)) throw new Error('额度须为非负金额，最多两位小数');
  const [whole, fraction = ''] = text.split('.');
  const result = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
  if (!Number.isSafeInteger(result) || result > 100000000) throw new Error('金额超过允许范围');
  return result;
}
function checkbox(value) { return el('input', { type: 'checkbox', checked: value }); }
function choice(items, selected) {
  return el('select', null, ...items.map(([value, text]) => el('option', { value, text, selected: value === selected })));
}
function submit(form, label, callback, ctx) {
  const button = el('button', { type: 'submit', class: 'button primary small', text: label });
  form.append(button);
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    busy(button, true);
    try { await callback(); toast('已保存', 'ok'); await ctx.rerender(); }
    catch (error) { toast(errorText(error), 'error'); }
    finally { busy(button, false); }
  });
}
function dataTable(headers, rows) {
  return el('div', { class: 'table-wrap' }, el('table', { class: 'data-table' },
    el('thead', null, el('tr', null, ...headers.map((text) => el('th', { text })))),
    el('tbody', null, ...rows)));
}


export {stateNames, money, newKey, input, amount, minor, checkbox, choice, submit, dataTable};
