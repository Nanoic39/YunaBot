'use strict';

import { api, busy, clear, el, errorText, field, modal, notice, panel, toast } from '../core.js';

import { stateNames, money, newKey, input, amount, minor, checkbox, choice, submit, dataTable } from './agent-common.js';

export async function render(host, ctx) {
  if (ctx.user?.role !== 'owner') {
    host.append(panel('支付管理', {}, notice('只有所有者可以查看和管理任务、授权及额度。', 'warn')));
    return;
  }
  const data = await api('agent/payments');
  const settingsBody = el('div', {class:'stack'});
  host.append(el('a', { class: 'button secondary small', href: '/api/agent/plan', download: 'AGENT_PLATFORM.md', text: '下载完整实现方案' }));
  host.append(notice('真实支付尚未接入，当前仅演练授权与额度。额度内支付完成仅保留后台账本，不主动私聊；超限按维度审批；模拟记录会明确标记未实际付款。', 'warn'));
  const balanceResult = el('p', { class: 'muted', text: '按需查询官方余额，不会充值。' });
  const balanceButton = el('button', { type: 'button', class: 'button secondary small', text: '查询 DeepSeek 余额' });
  balanceButton.addEventListener('click', async () => {
    busy(balanceButton, true);
    try {
      const result = await api('agent/deepseek/balance', { method: 'POST' });
      balanceResult.textContent = result.balances.map((b) => `${b.currency} 总余额 ${b.total_balance} / 充值余额 ${b.topped_up_balance}`).join('；') || '未返回余额明细';
    } catch (error) { toast(errorText(error), 'error'); }
    finally { busy(balanceButton, false); }
  });
  settingsBody.append(panel('充值账户余额', {}, balanceButton, balanceResult));
  const refresh = el('button', { type: 'button', class: 'button secondary small', text: '刷新状态', onclick: () => ctx.rerender() });
  host.append(el('div', { class: 'page-head' }, el('div', null,
    el('h2', { text: data.policy.paused ? '支付任务已暂停' : '支付授权演练可用' }),
    el('p', { text: `实际今日 ${money(data.live_usage.daily_minor)} / 本月 ${money(data.live_usage.monthly_minor)} · 模拟今日 ${money(data.usage.daily_minor)} / 本月 ${money(data.usage.monthly_minor)} · 待执行预占 ${money(data.usage.reserved_minor)}。按北京时间统计。` })), refresh));

  const paused = checkbox(data.policy.paused);
  const automatic = amount(data.policy.auto_daily_minor);
  const single = amount(data.policy.single_minor), daily = amount(data.policy.daily_minor), monthly = amount(data.policy.monthly_minor);
  const policyForm = el('form', { class: 'stack agent-form' }, field('暂停所有任务（仍可取消任务和修改授权）', paused),
    el('div', { class: 'grid grid-3' }, field('全局单笔上限 / 元', single), field('全局每日上限 / 元', daily), field('全局每月上限 / 元', monthly)), field('每日免审核阈值 / 元（超出后完整订单转审批）', automatic));
  submit(policyForm, '保存全局设置', () => api('agent/policy', { method: 'PUT', body: {
    auto_daily_minor: minor(automatic), paused: paused.checked, single_minor: minor(single), daily_minor: minor(daily), monthly_minor: minor(monthly),
  } }), ctx);
  settingsBody.append(panel('额度与紧急暂停', { subtitle: `单笔${money(data.policy.single_minor)} / 每日${money(data.policy.daily_minor)} / 每月${money(data.policy.monthly_minor)}；超限按维度和当前任务独立审批，不永久提额。` }, policyForm));

  const grantEditor = el('div');
  const showGrant = (grant = {}) => {
    clear(grantEditor);
    const id = input(grant.id || `grant-${newKey()}`);
    id.readOnly = !!grant.id;
    const capability = choice(data.capabilities.map((c) => [c.id, c.label]), grant.capability || 'payment.pay');
    const merchant = input(grant.merchant || ''), recipient = input(grant.recipient || ''), purpose = input(grant.purpose || '');
    const enabled = checkbox(grant.enabled ?? false), approval = checkbox(grant.require_approval ?? false);
    const single = amount(grant.single_minor ?? data.policy.single_minor), daily = amount(grant.daily_minor ?? data.policy.daily_minor), monthly = amount(grant.monthly_minor ?? data.policy.monthly_minor);
    const days = el('input', { type: 'number', value: '7', min: '1', max: '365', required: true });
    const form = el('form', { class: 'stack agent-form' },
      el('div', { class: 'grid grid-2' }, field('授权 ID', id), field('能力', capability),
        field('平台 / 商户标识（精确匹配）', merchant), field('收件人 / 目标账号标识（精确匹配）', recipient),
        field('用途标识（精确匹配）', purpose), field('从保存时起有效天数', days)),
      el('div', { class: 'grid grid-3' }, field('单笔上限 / 元', single), field('每日上限 / 元', daily), field('每月上限 / 元', monthly)),
      field('启用本授权', enabled), field('每个任务须所有者审批', approval));
    submit(form, grant.id ? '更新授权（重设有效期）' : '创建模拟授权', () => api('agent/grants', { method: 'PUT', body: {
      id: id.value, capability: capability.value, mode: grant.mode || 'simulation', merchant: merchant.value,
      recipient: recipient.value, purpose: purpose.value, enabled: enabled.checked, require_approval: approval.checked,
      single_minor: minor(single), daily_minor: minor(daily), monthly_minor: minor(monthly),
      expires_at: Date.now() / 1000 + Number(days.value) * 86400,
    } }), ctx);
    grantEditor.append(form);
  };
  showGrant();
  const grantRows = data.grants.map((g) => {
    const edit = el('button', { type: 'button', class: 'button secondary xs', text: '编辑', onclick: () => { showGrant(g); grantEditor.scrollIntoView({ behavior: 'smooth' }); } });
    return el('tr', null, el('td', { text: data.capabilities.find((c) => c.id === g.capability)?.label || g.capability }),
      el('td', { text: `${g.merchant} / ${g.recipient} / ${g.purpose}` }),
      el('td', { text: `${money(g.single_minor)} / ${money(g.daily_minor)} / ${money(g.monthly_minor)}` }),
      el('td', { text: `${g.enabled ? '启用' : '停用'} · ${g.require_approval ? '逐笔审批' : '限额自动授权'} · ${g.mode}` }),
      el('td', { text: new Date(g.expires_at * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) }), el('td', null, edit));
  });
  settingsBody.append(panel('能力与用途授权', { subtitle: '平台、收件人和用途精确匹配；好感度只能用于提出建议，不能授予付款权限。' },
    dataTable(['能力', '范围', '单笔 / 每日 / 每月', '授权方式', '到期时间', '操作'], grantRows)),
  notice('app.use（使用应用）和 payment.pay（资金支付）是两个独立能力。允许应用操作不代表允许付款；组合计划中的支付步骤必须单独授权和审批。', 'info'),
  panel('新增或编辑授权', { subtitle: '不在这里填写密码、支付凭据、银行卡或收货地址。收件人使用内部标识。' }, grantEditor));

  if (data.grants.length) {
    const grant = choice(data.grants.map((g) => [g.id, `${g.id} · ${g.purpose}`]), data.grants[0].id);
    const cost = amount(0), description = input('测试一次任务授权与额度预占');
    const form = el('form', { class: 'stack agent-form' }, field('选择授权', grant), field('模拟金额 / 元', cost), field('任务说明', description));
    submit(form, '创建演练任务', () => {
      const g = data.grants.find((g) => g.id === grant.value);
      return api('agent/tasks', { method: 'POST', body: {
        idempotency_key: newKey(), grant_id: g.id, capability: g.capability, mode: g.mode,
        merchant: g.merchant, recipient: g.recipient, purpose: g.purpose,
        amount_minor: minor(cost), currency: 'CNY', description: description.value,
      } });
    }, ctx);
    settingsBody.append(panel('任务演练', { subtitle: '检查暂停、范围、审批和额度规则。模拟不会实际完成相应平台任务。' }, form));
  }
  const taskRows = data.tasks.map((t) => {
    const actions = el('div', { class: 'row' }, el('button', { type: 'button', class: 'button secondary xs', text: '查看详情', onclick: () => showPaymentTask(t.id) }));
    const addAction = (action, label) => {
      const button = el('button', { type: 'button', class: 'button secondary xs', text: label });
      button.addEventListener('click', async () => {
        busy(button, true);
        try { await api(`agent/tasks/${encodeURIComponent(t.id)}/${action}`, { method: 'POST' }); await ctx.rerender(); }
        catch (error) { toast(errorText(error), 'error'); }
        finally { busy(button, false); }
      });
      actions.append(button);
    };
    if (['pending', 'authorized'].includes(t.state)) {
      const names = {single: '批准本单单笔超限', daily: '批准本单今日超限', monthly: '批准本单本月超限'};
      for (const dimension of (t.required_approvals || [])) {
        if (dimension !== 'review') addAction(`approve-${dimension}`, names[dimension]);
      }
      if (t.state === 'pending' && !(t.required_approvals || []).some((d) => d !== 'review')) addAction('approve', '批准本单');
    }
    if (t.state === 'authorized' && !(t.required_approvals || []).length) addAction('simulate', '模拟执行');
    if (['pending', 'authorized'].includes(t.state)) addAction('cancel', '取消');
    return el('tr', null, el('td', null, el('strong', { text: t.summary }), el('div', { class: 'mono muted', text: t.id })),
      el('td', { text: `${t.merchant} / ${t.recipient}` }), el('td', { text: money(t.amount_minor) }),
      el('td', null, el('span', { text: stateNames[t.state] || t.state }), ...((t.requirements || []).filter((r) => !r.approved && r.dimension !== 'review').map((r) => el('div', { class: 'muted', text: `${r.scope === 'global' ? '全局' : '用途'} ${r.dimension === 'single' ? '本单' : r.period}：${money(r.projected_minor)} / ${money(r.limit_minor)}` })))), el('td', null, actions));
  });
  host.append(panel('支付任务概要', { subtitle: '最多100条；任务内容不可修改，变更需取消后重新提交。待审批或授权任务一小时后过期。' },
    dataTable(['任务', '目标', '模拟金额', '状态', '操作'], taskRows)));
  const records = data.records.map((r) => el('tr', null,
    el('td', { text: new Date(r.created * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) }),
    el('td', { text: r.summary.slice(0, 80) }), el('td', { text: money(r.amount_minor) }),
    el('td', { text: r.simulation ? '模拟完成 · 未付款' : '已支付' }),
    el('td', null, el('button', { class: 'button secondary xs', text: '查看支付明细', type: 'button', onclick: () => showReceipt(r.id) }))));
  host.append(panel('历史支付项目', { subtitle: '默认只展示概要，详细内容、订单、审批和通知状态可单独查看。' },
    dataTable(['时间', '内容概要', '金额', '状态', '详情'], records)));
  host.append(el('details', {class:'panel agent-details'}, el('summary', {text:'管理支付能力、额度与授权'}), settingsBody));
}

function facts(items) {
  return el('dl', { class: 'agent-facts' }, ...items.flatMap(([label, value]) => [el('dt', { text: label }), el('dd', { text: String(value ?? '—') })]));
}
async function showPaymentTask(id) {
  try {
    const task = await api(`agent/task-detail/${encodeURIComponent(id)}`);
    modal({ title: '支付任务详情', wide: true, body: el('div', { class: 'stack' }, facts([
      ['任务ID', task.id], ['内容', task.description], ['平台', task.merchant], ['目标标识', task.recipient],
      ['用途', task.purpose], ['金额', money(task.amount_minor)], ['状态', stateNames[task.state] || task.state],
      ['环境', task.mode], ['仍需审批', task.required_approvals.join('、') || '无'],
    ]), panel('审批与执行记录', {}, dataTable(['时间', '操作者', '动作', '说明'], task.audit.map((e) => el('tr', null,
      el('td', { text: new Date(e.time * 1000).toLocaleString('zh-CN') }), el('td', { text: e.actor }), el('td', { text: e.action }), el('td', { text: e.detail })))))) });
  } catch (error) { toast(errorText(error), 'error'); }
}
async function showReceipt(id) {
  try {
    const r = await api(`agent/payments/records/${encodeURIComponent(id)}`);
    const t = r.task;
    modal({ title: r.simulation ? '模拟支付明细（未实际付款）' : '支付明细', wide: true, body: el('div', { class: 'stack' }, facts([
      ['支付记录', r.id], ['任务', r.task_id], ['内容', r.summary], ['金额', `${r.currency} ${money(r.amount_minor)}`],
      ['平台', t.merchant], ['目标标识', t.recipient], ['用途', t.purpose], ['渠道', r.provider],
      ['订单号', r.provider_order_id], ['交易号', r.transaction_id || '无（模拟）'],
      ['支付时间', new Date(r.created * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })],
    ]), panel('超管QQ通知', { subtitle: '通知独立于审批，未送达会保留重试。' }, r.deliveries.length ? dataTable(['超管', '尝试次数', '送达状态'], r.deliveries.map((d) => el('tr', null,
      el('td', { text: d.recipient }), el('td', { text: d.attempts }), el('td', { text: d.sent_at ? '已送达' : '等待重试' })))) : el('p', { class: 'muted', text: '等待通知任务向已配置超管发送。' }))) });
  } catch (error) { toast(errorText(error), 'error'); }
}
