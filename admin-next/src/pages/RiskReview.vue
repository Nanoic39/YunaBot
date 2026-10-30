<script setup>
import { computed, onMounted, ref } from 'vue';
const props = defineProps(['request', 'notify']);
const data = ref({ items: [], stats: { status: {}, labels: {}, sources: {} }, labels: {}, sampling: {} });
const status = ref('pending');
const query = ref('');
const busy = ref(false);
const error = ref('');
const missRow = ref(null);
const missCategory = ref('abuse');

const api = (path, options = {}) => props.request(path, {
  ...options,
  body: options.body === undefined ? undefined : JSON.stringify(options.body),
});
const labelNames = {
  false_positive: '误判',
  false_negative: '漏判',
  uncertain: '拿不准',
  correct: '正确处理',
  correct_safe: '正确处理',
};
const sourceNames = { detected: '已检测', uncertain: '拿不准', sample: '随机抽查' };
const items = computed(() => data.value.items || []);
const stats = computed(() => data.value.stats || {});
const activeCount = computed(() => Number(stats.value.status?.pending || 0));
const labeledCount = computed(() => Number(stats.value.status?.labeled || 0));
const falsePositiveCount = computed(() => Number(stats.value.labels?.false_positive || 0));
const falseNegativeCount = computed(() => Number(stats.value.labels?.false_negative || 0));

function time(value) {
  const n = Number(value || 0);
  return n ? new Date(n * 1000).toLocaleString('zh-CN', { hour12: false }) : '—';
}
function title(hit) {
  return hit?.label || data.value.labels?.[hit?.category] || hit?.category || '';
}
function labelName(label) {
  return labelNames[label] || label || '';
}
function targetText(target) {
  if (!target || !target.kind) return '未明确';
  if (target.kind === 'bot') return `${target.name || 'Yuna'}（bot自己）`;
  if (target.kind === 'other') {
    const who = target.name || '其他对象';
    return target.qq ? `${who}（QQ ${target.qq}）` : who;
  }
  if (target.kind === 'self') return `说话人自己${target.name ? `（${target.name}）` : ''}`;
  if (target.kind === 'group') return target.name || '群内群体';
  return '未明确';
}
function decisionText(row) {
  if (!row.label) return '';
  return `已标记：${labelName(row.label)}`;
}
function predictedCategories(row) {
  return (row.predicted || []).map((hit) => hit.category).filter(Boolean);
}
async function load() {
  busy.value = true;
  error.value = '';
  try {
    data.value = await api(`risk/reviews?status=${status.value}&query=${encodeURIComponent(query.value.trim())}&limit=200`);
  } catch (e) {
    error.value = e.message || '读取复核队列失败';
  } finally {
    busy.value = false;
  }
}
async function sendLabel(row, label, categories = []) {
  busy.value = true;
  try {
    await api(`risk/reviews/${row.id}/label`, {
      method: 'POST',
      body: { label, categories, note: '' },
    });
    props.notify?.(`已标记：${labelName(label)}`);
    missRow.value = null;
    await load();
  } catch (e) {
    props.notify?.(e.message || '保存失败');
  } finally {
    busy.value = false;
  }
}
function markCorrect(row) {
  const label = row.predicted?.length ? 'correct' : 'correct_safe';
  return sendLabel(row, label, predictedCategories(row));
}
function markFalsePositive(row) {
  if (!row.predicted?.length) {
    props.notify?.('这条模型没有命中，不能标为误判；请选择“正确处理”或“漏判”。');
    return;
  }
  return sendLabel(row, 'false_positive', predictedCategories(row));
}
function startMiss(row) {
  missCategory.value = row.candidates?.[0]?.category || row.predicted?.[0]?.category || 'abuse';
  missRow.value = row;
}
function confirmMiss() {
  if (!missRow.value) return;
  return sendLabel(missRow.value, 'false_negative', [missCategory.value]);
}
async function replay(row) {
  busy.value = true;
  try {
    const result = await api('risk/reviews/replay', { method: 'POST', body: { sample_ids: [row.id] }, timeout: 120000 });
    const item = result.results?.[0] || {};
    props.notify?.(item.error ? `重放失败：${item.error}` : `重放完成：${(item.after || []).map(title).join('、') || '未命中'}${item.uncertain ? '（拿不准）' : ''}`);
    await load();
  } catch (e) {
    props.notify?.(e.message || '重放失败');
  } finally {
    busy.value = false;
  }
}
async function remove(row) {
  if (!confirm(`删除复核样本 #${row.id}？`)) return;
  busy.value = true;
  try {
    await api(`risk/reviews/${row.id}`, { method: 'DELETE' });
    await load();
  } catch (e) {
    props.notify?.(e.message || '删除失败');
  } finally {
    busy.value = false;
  }
}
onMounted(load);
</script>

<template>
  <section class="panel risk-review">
    <div class="panel-head">
      <div>
        <h3>风控人工复核</h3>
        <p>每条只需要判断一件事：模型这次处理是否正确。三个按钮分别对应“处理对了 / 不该报 / 该报没报”。</p>
      </div>
      <button class="button small" :disabled="busy" @click="load">{{ busy ? '更新中…' : '刷新' }}</button>
    </div>
    <div class="panel-body">
      <div class="stats risk-stats">
        <div class="stat"><small>待复核</small><strong>{{ activeCount }}</strong></div>
        <div class="stat"><small>已复核</small><strong>{{ labeledCount }}</strong></div>
        <div class="stat"><small>误判</small><strong>{{ falsePositiveCount }}</strong></div>
        <div class="stat"><small>漏判</small><strong>{{ falseNegativeCount }}</strong></div>
      </div>
      <div class="row risk-toolbar">
        <select v-model="status" class="input risk-status" aria-label="复核状态" @change="load">
          <option value="pending">待复核</option>
          <option value="labeled">已复核</option>
          <option value="all">全部</option>
        </select>
        <input v-model="query" class="input search" type="search" placeholder="搜索内容、QQ、昵称或标签" aria-label="搜索复核样本" @keydown.enter="load">
        <button class="button primary small" :disabled="busy" @click="load">查询</button>
      </div>
      <p class="risk-help">
        <strong>正确处理</strong>：模型报对了，或者没有报、实际也确实安全。
        <strong>误判</strong>：模型报了风险，但实际没问题。
        <strong>漏判</strong>：实际有风险，模型没有报出来；标漏判时再选一个风险类型。
        若模型把“骂/要求”的对象认错了（例如其实在说别人，却被当成对 bot 说），也属于<strong>误判</strong>，先看下方上下文再判断。
      </p>
      <p v-if="error" class="notice" role="alert">{{ error }}</p>
      <p v-if="!busy && !items.length" class="empty">没有匹配的复核样本。检测命中、拿不准和随机抽查会逐步积累。</p>
    </div>
    <div v-if="items.length" class="table-wrap risk-table-wrap">
      <table class="risk-table">
        <colgroup>
          <col class="risk-col-time">
          <col class="risk-col-content">
          <col class="risk-col-model">
          <col class="risk-col-decision">
        </colgroup>
        <thead><tr><th>时间 / 来源</th><th>消息</th><th>模型判断</th><th>人工复核</th></tr></thead>
        <tbody>
          <tr v-for="row in items" :key="row.id">
            <td class="risk-meta">
              <span class="mono">{{ time(row.created) }}</span>
              <div class="risk-badges">
                <span class="badge" :class="row.source === 'detected' ? 'ok' : row.source === 'uncertain' ? 'warn' : ''">{{ sourceNames[row.source] || row.source }}</span>
                <span class="badge">{{ row.chat_type === 'group' ? '群' : '私聊' }}</span>
              </div>
              <small class="mono">{{ row.name || '—' }}（{{ row.qq }}）</small>
              <small v-if="row.gid" class="mono">群 {{ row.gid }}</small>
            </td>
            <td class="migration-text risk-content">
              <div>{{ row.content || '（空）' }}</div>
              <small v-if="row.quoted" class="mono">引用：{{ row.quoted }}</small>
              <small v-if="row.candidates?.length" class="mono">候选：{{ row.candidates.map((x) => x.label || x.category).join(' / ') }}</small>
              <details v-if="row.context?.length" class="risk-context">
                <summary>上下文（{{ row.context.length }} 条，像好感账本一样回看）</summary>
                <div class="risk-context-list">
                  <div v-for="(turn, i) in row.context" :key="`${row.id}-ctx-${i}`" class="risk-context-turn" :class="{ current: turn.current }">
                    <div class="risk-context-meta">
                      <strong>{{ turn.role === 'assistant' ? 'Yuna' : (turn.name || turn.qq || '群友') }}</strong>
                      <span class="mono">{{ turn.role === 'assistant' ? 'bot' : turn.qq }}</span>
                      <span v-if="turn.current" class="badge warn">本条</span>
                      <span v-if="turn.recalled" class="badge">已撤回</span>
                    </div>
                    <p>{{ turn.content }}</p>
                  </div>
                </div>
              </details>
            </td>
            <td class="risk-model">
              <div v-if="row.predicted?.length" class="risk-hits">
                <div v-for="hit in row.predicted" :key="`${row.id}-${hit.category}`" class="risk-hit">
                  <span class="badge ok">{{ title(hit) }}</span>
                  <small class="risk-target">指向：{{ targetText(hit.target) }}</small>
                </div>
              </div>
              <template v-else>
                <span class="badge">未命中</span>
                <small v-if="row.target?.kind" class="risk-target">指向：{{ targetText(row.target) }}</small>
              </template>
              <small class="mono">置信度 {{ Math.round(Number(row.confidence || 0) * 100) }}%</small>
            </td>
            <td class="risk-decision">
              <div class="risk-actions">
                <button class="button small" :disabled="busy" @click="markCorrect(row)">正确处理</button>
                <button class="button small" :disabled="busy || !row.predicted?.length" :title="row.predicted?.length ? '模型报了风险，但实际没问题；指向对象判断错了也选这个' : '模型没有命中，不能标为误判'" @click="markFalsePositive(row)">误判</button>
                <button class="button small" :disabled="busy" @click="startMiss(row)">漏判</button>
              </div>
              <p v-if="row.label" class="risk-decision-current">{{ decisionText(row) }}</p>
              <div v-if="missRow?.id === row.id" class="risk-miss-picker">
                <label>
                  <span class="small muted">漏掉的风险类型</span>
                  <select v-model="missCategory" class="input" aria-label="漏判风险类型">
                    <option v-for="(label, key) in data.labels" :key="key" :value="key">{{ label }}</option>
                  </select>
                </label>
                <div class="row">
                  <button class="button primary small" :disabled="busy" @click="confirmMiss">保存漏判</button>
                  <button class="button small ghost" :disabled="busy" @click="missRow = null">取消</button>
                </div>
              </div>
              <details v-if="row.source === 'detected' || row.source === 'uncertain'" class="risk-more">
                <summary>更多操作</summary>
                <div class="row">
                  <button class="button small ghost" :disabled="busy" @click="replay(row)">重放判断</button>
                  <button class="button small ghost danger" :disabled="busy" @click="remove(row)">删除样本</button>
                </div>
              </details>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.risk-review :deep(.panel-head p) { max-width: 760px; line-height: 1.65; }
.risk-stats { margin-top: 0; }
.risk-toolbar { margin-top: 14px; }
.risk-status { width: auto; min-width: 116px; }
.risk-help { margin: 12px 0 0; color: var(--muted); font-size: 12px; line-height: 1.75; }
.risk-help strong { color: var(--ink-soft); font-weight: 600; }
.risk-table-wrap { border-top: 1px solid var(--line); }
.risk-table { table-layout: fixed; min-width: 860px; }
.risk-table th, .risk-table td { vertical-align: top; }
.risk-col-time { width: 16%; }
.risk-col-content { width: 46%; }
.risk-col-model { width: 15%; }
.risk-col-decision { width: 23%; }
.risk-meta { display: table-cell; }
.risk-meta > small { display: block; margin-top: 3px; }
.risk-badges { display: flex; gap: 4px; flex-wrap: wrap; margin: 5px 0; }
.risk-content { white-space: pre-wrap; overflow-wrap: anywhere; word-break: break-word; line-height: 1.65; }
.risk-content > small { display: block; margin-top: 5px; }
.risk-model { display: table-cell; }
.risk-model .badge { margin-bottom: 5px; }
.risk-model > small { display: block; }
.risk-hits { display: grid; gap: 6px; }
.risk-hit { display: grid; gap: 3px; }
.risk-target { color: var(--muted); font-size: 10px; line-height: 1.5; overflow-wrap: anywhere; }
.risk-context { margin-top: 8px; }
.risk-context summary { cursor: pointer; color: var(--accent-2); font-size: 11px; }
.risk-context-list { display: grid; gap: 5px; margin-top: 7px; padding: 8px; border: 1px solid var(--line); border-radius: 10px; background: var(--soft-fill); }
.risk-context-turn { padding: 5px 7px; border-left: 2px solid transparent; border-radius: 6px; }
.risk-context-turn.current { border-left-color: var(--accent-2); background: rgba(255,255,255,.045); }
.risk-context-meta { display: flex; align-items: center; flex-wrap: wrap; gap: 5px; }
.risk-context-meta strong { color: var(--ink-soft); font-size: 11px; }
.risk-context-meta .mono { color: var(--muted); font-size: 9px; }
.risk-context-turn p { margin: 3px 0 0; color: var(--ink-soft); font-size: 11px; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; }
.risk-decision { min-width: 0; }
.risk-actions { display: flex; flex-wrap: wrap; gap: 6px; }
.risk-actions .button { white-space: nowrap; }
.risk-decision-current { margin: 8px 0 0; color: var(--ok); font-size: 11px; }
.risk-miss-picker { margin-top: 9px; padding: 9px; border: 1px solid var(--line); border-radius: 11px; background: var(--soft-fill); }
.risk-miss-picker label { display: grid; gap: 5px; margin-bottom: 7px; }
.risk-miss-picker .input { width: 100%; }
.risk-more { margin-top: 8px; }
.risk-more summary { cursor: pointer; color: var(--muted); font-size: 11px; }
.risk-more .row { margin-top: 6px; }
@media (max-width: 720px) {
  .risk-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .risk-table-wrap { overflow: visible; }
  .risk-table { display: block; min-width: 0; }
  .risk-table thead { display: none; }
  .risk-table tbody { display: grid; gap: 0; }
  .risk-table tr { display: grid; gap: 6px; padding: 11px 12px; border-bottom: 1px solid var(--line); }
  .risk-table td { display: block; width: auto; padding: 0; border: 0; }
  .risk-meta { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; }
  .risk-meta .mono:first-child { width: auto; }
  .risk-badges { margin: 0; }
  .risk-meta > small { display: inline; margin: 0; }
  .risk-model { display: flex; align-items: flex-start; gap: 7px; flex-wrap: wrap; }
  .risk-model .badge { margin-bottom: 0; }
  .risk-hits { width: 100%; }
  .risk-hit { display: flex; align-items: baseline; flex-wrap: wrap; gap: 6px; }
  .risk-decision { margin-top: 2px; }
  .risk-actions { gap: 8px; }
  .risk-more { display: none; }
}
</style>
