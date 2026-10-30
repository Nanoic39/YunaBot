<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue';

const props = defineProps(['request', 'notify']);
const api = (path, options = {}) => props.request(path, {
  ...options,
  body: options.body === undefined ? undefined : JSON.stringify(options.body),
});
const data = ref({ nodes: [], stats: {}, purposes: {}, recent: [], legacy: {} });
const loading = ref(false);
const busy = ref(false);
const error = ref('');
const editing = ref(false);
const editId = ref('');
const form = ref(emptyForm());
const probeText = ref('');
const probeImage = ref('');
const probe = ref({});
const timer = ref(0);

const presets = [
  { value: 'doubao', label: '豆包 Ark', base: 'https://ark.cn-beijing.volces.com/api/v3', hint: '模型名填 Ark 推理接入点 ID' },
  { value: 'qwen', label: '通义千问', base: 'https://dashscope.aliyuncs.com/compatible-mode/v1', hint: '模型名填 qwen-vl-max 等可用视觉模型' },
  { value: 'glm', label: '智谱 GLM', base: 'https://open.bigmodel.cn/api/paas/v4', hint: '模型名填 glm-4v-plus 等' },
  { value: 'siliconflow', label: 'SiliconFlow', base: 'https://api.siliconflow.cn/v1', hint: '模型名填 Qwen/Qwen2.5-VL-72B-Instruct 等' },
  { value: 'ollama', label: 'Ollama 本地', base: 'http://127.0.0.1:11434/v1', hint: '本地服务 Key 可留空' },
];
function applyPreset(value) {
  const preset = presets.find((item) => item.value === value);
  if (!preset) return;
  form.value.base_url = preset.base;
  if (!form.value.name) form.value.name = preset.label;
  props.notify?.(preset.hint);
}

function emptyForm() {
  return {
    name: '', base_url: '', api_key: '', model: '',
    purposes: ['image'], enabled: true, detail: 'auto', timeout: 45, max_tokens: 900, prompt: '',
  };
}
function purposeLabel(key) { return data.value.purposes?.[key] || key; }
function keyText(node) { return node.has_key ? node.key_hint : '未配置 Key'; }
function statusClass(ok) { return ok ? 'ok' : 'danger'; }
function stamp(value) {
  const n = Number(value || 0);
  return n ? new Date(n * 1000).toLocaleTimeString('zh-CN', { hour12: false }) : '—';
}
const successRate = computed(() => {
  const stats = data.value.stats || {};
  const total = Number(stats.success || 0) + Number(stats.failed || 0);
  return total ? Math.round(Number(stats.success || 0) / total * 100) : 0;
});

async function load(silent = false) {
  if (!silent) loading.value = true;
  try {
    data.value = await api('vision/chain');
    error.value = '';
  } catch (e) {
    error.value = e.message || '读取链路失败';
    if (!silent) props.notify?.(error.value);
  } finally { loading.value = false; }
}

function startCreate() {
  editing.value = true; editId.value = ''; form.value = emptyForm();
  probeText.value = '请回复：连接成功';
  probeImage.value = '';
}
function startEdit(node) {
  editing.value = true; editId.value = node.id;
  form.value = {
    name: node.name, base_url: node.base_url, api_key: '', model: node.model,
    purposes: [...(node.purposes || ['image'])], enabled: Boolean(node.enabled),
    detail: node.detail || 'auto', timeout: node.timeout || 45,
    max_tokens: node.max_tokens || 900, prompt: node.prompt || '',
  };
  probeText.value = '请回复：连接成功';
  probeImage.value = '';
}
function closeEditor() { editing.value = false; editId.value = ''; form.value = emptyForm(); }
function togglePurpose(value) {
  const set = new Set(form.value.purposes || []);
  if (set.has(value)) set.delete(value); else set.add(value);
  form.value.purposes = [...set];
}

async function save() {
  if (busy.value) return;
  if (!String(form.value.base_url || '').trim() || !String(form.value.model || '').trim()) {
    props.notify?.('请填写接口地址和模型名');
    return;
  }
  if (!Array.isArray(form.value.purposes) || !form.value.purposes.length) {
    props.notify?.('至少选择一个用途');
    return;
  }
  busy.value = true;
  try {
    const path = editId.value ? `vision/chain/nodes/${encodeURIComponent(editId.value)}` : 'vision/chain/nodes';
    await api(path, { method: editId.value ? 'PUT' : 'POST', body: form.value });
    props.notify?.(editId.value ? '节点已保存' : '节点已添加');
    closeEditor();
    await load(true);
  } catch (e) { props.notify?.(e.message); }
  finally { busy.value = false; }
}
async function remove(node) {
  if (!confirm(`删除视觉节点「${node.name}」？`)) return;
  busy.value = true;
  try {
    await api(`vision/chain/nodes/${encodeURIComponent(node.id)}`, { method: 'DELETE' });
    props.notify?.('节点已删除');
    if (editId.value === node.id) closeEditor();
    await load(true);
  } catch (e) { props.notify?.(e.message); }
  finally { busy.value = false; }
}
async function move(node, delta) {
  const nodes = [...(data.value.nodes || [])];
  const index = nodes.findIndex((item) => item.id === node.id);
  const target = index + delta;
  if (index < 0 || target < 0 || target >= nodes.length) return;
  [nodes[index], nodes[target]] = [nodes[target], nodes[index]];
  data.value = { ...data.value, nodes };
  busy.value = true;
  try { await api('vision/chain/order', { method: 'PUT', body: { ids: nodes.map((item) => item.id) } }); await load(true); }
  catch (e) { props.notify?.(e.message); await load(true); }
  finally { busy.value = false; }
}
async function toggleNode(node) {
  busy.value = true;
  try {
    await api(`vision/chain/nodes/${encodeURIComponent(node.id)}`, { method: 'PUT', body: { enabled: !node.enabled } });
    await load(true);
  } catch (e) { props.notify?.(e.message); }
  finally { busy.value = false; }
}
async function runProbe(node) {
  busy.value = true;
  probe.value = { ...probe.value, [node.id]: { running: true } };
  try {
    const result = await api(`vision/chain/nodes/${encodeURIComponent(node.id)}/probe`, {
      method: 'POST', body: { text: probeText.value || '请回复：连接成功', image_url: probeImage.value || '' },
    });
    probe.value = { ...probe.value, [node.id]: result };
    props.notify?.(result.ok ? `连接成功 · ${result.latency_ms}ms` : `测试失败：${result.error || '未知错误'}`);
  } catch (e) {
    probe.value = { ...probe.value, [node.id]: { ok: false, error: e.message } };
    props.notify?.(e.message);
  } finally { busy.value = false; }
}
async function toggleAuto(event) {
  busy.value = true;
  try {
    const result = await api('vision/chain/settings', { method: 'PUT', body: { auto_select: Boolean(event.target.checked) } });
    data.value = { ...data.value, auto_select: Boolean(result.auto_select) };
    props.notify?.(result.auto_select ? '已开启自动择优' : '已关闭自动择优，严格按手动顺序');
    await load(true);
  } catch (e) { props.notify?.(e.message); await load(true); }
  finally { busy.value = false; }
}
async function importLegacy() {
  busy.value = true;
  try {
    const result = await api('vision/chain/import', { method: 'POST' });
    props.notify?.(result.message || (result.imported ? '已导入当前视觉配置' : '无需重复导入'));
    await load(true);
  } catch (e) { props.notify?.(e.message); }
  finally { busy.value = false; }
}
async function clearCache() {
  if (!confirm('清空所有识图结果缓存？切换视觉模型后建议执行。')) return;
  busy.value = true;
  try {
    const result = await api('vision/cache/clear', { method: 'POST' });
    props.notify?.(`识图缓存已清空（${result.entries ?? 0} 条）`);
  } catch (e) { props.notify?.(e.message); }
  finally { busy.value = false; }
}

onMounted(() => {
  load();
  timer.value = window.setInterval(() => load(true), 4000);
});
onUnmounted(() => { if (timer.value) window.clearInterval(timer.value); });
</script>

<template>
  <section class="vision-page">
    <section class="panel">
      <div class="panel-head">
        <div>
          <h3>模型链路</h3>
          <p>统一管理主对话 LLM、Agent 规划、识图、表情判断和 OCR。节点按用途筛选、按顺序调用；前一个失败或超时会自动尝试下一个。真实 Key 不会回传到浏览器。</p>
        </div>
        <div class="row">
          <span class="badge" :class="data.nodes?.length ? 'ok' : 'warn'">{{ data.nodes?.length || 0 }} 个节点</span>
          <label class="checkbox" style="margin-right:4px"><input type="checkbox" :checked="data.auto_select" :disabled="busy" @change="toggleAuto"> 自动择优</label>
          <button class="button small ghost" :disabled="busy" @click="importLegacy">导入当前模型配置</button>
          <button class="button small ghost" :disabled="busy" @click="clearCache">清理识图缓存</button>
          <button class="button small primary" @click="startCreate">新增节点</button>
          <button class="button small ghost" @click="load()">刷新</button>
        </div>
      </div>
      <div class="vision-stats">
        <div><small>启用节点</small><strong>{{ data.stats?.enabled || 0 }}</strong></div>
        <div><small>最近调用</small><strong>{{ data.stats?.calls || 0 }}</strong></div>
        <div><small>成功率</small><strong>{{ successRate }}%</strong></div>
        <div><small>失败</small><strong>{{ data.stats?.failed || 0 }}</strong></div>
        <div><small>旧版回退</small><strong>{{ data.legacy?.enabled ? data.legacy?.model || '已启用' : '关闭' }}</strong></div>
      </div>
      <div v-if="error" class="notice danger" style="margin:12px 0">{{ error }}</div>
      <p v-if="!data.nodes?.length" class="notice" style="margin:12px 0">
        还没有链路节点，当前会回退到旧版配置。可以点「导入当前 LLM 配置」保留现有模型，或新增豆包 / 通义 / GLM / SiliconFlow / Ollama 等节点。
      </p>
      <div class="vision-layout">
        <div class="vision-chain">
          <div class="vision-section-title"><h4>调用顺序</h4><small>{{ data.auto_select ? '自动择优：按近期成功率/延迟排序，手动顺序兜底' : '严格按上方手动顺序' }}</small></div>
          <article v-for="(node, index) in data.nodes" :key="node.id" class="vision-node" :class="{ disabled: !node.enabled }">
            <div class="node-index">{{ String(index + 1).padStart(2, '0') }}</div>
            <div class="node-main">
              <div class="node-head">
                <strong>{{ node.name }}</strong>
                <span class="badge" :class="node.enabled ? 'ok' : 'danger'">{{ node.enabled ? '启用' : '停用' }}</span>
                <span v-for="purpose in node.purposes" :key="purpose" class="badge">{{ purposeLabel(purpose) }}</span>
              </div>
              <div class="node-meta">
                <span class="mono">{{ node.model }}</span>
                <span class="mono">{{ node.base_url }}</span>
                <span>{{ keyText(node) }}</span>
                <span>{{ node.detail }} · {{ node.timeout }}s · {{ node.max_tokens }} tokens</span>
              </div>
              <p v-if="node.prompt" class="small muted">节点提示：{{ node.prompt }}</p>
              <div v-if="probe[node.id]" class="probe" :class="probe[node.id].running ? '' : (probe[node.id].ok ? 'ok' : 'danger')">
                <template v-if="probe[node.id].running">测试中…</template>
                <template v-else-if="probe[node.id].ok">连接成功 · {{ probe[node.id].latency_ms }}ms · {{ probe[node.id].text }}</template>
                <template v-else>测试失败：{{ probe[node.id].error }}</template>
              </div>
            </div>
            <div class="node-actions">
              <button class="button small ghost" :disabled="busy || index === 0" title="上移" @click="move(node, -1)">↑</button>
              <button class="button small ghost" :disabled="busy || index === data.nodes.length - 1" title="下移" @click="move(node, 1)">↓</button>
              <button class="button small ghost" :disabled="busy" @click="toggleNode(node)">{{ node.enabled ? '停用' : '启用' }}</button>
              <button class="button small ghost" :disabled="busy" @click="runProbe(node)">测试</button>
              <button class="button small" @click="startEdit(node)">编辑</button>
              <button class="button small danger" :disabled="busy" @click="remove(node)">删除</button>
            </div>
          </article>
          <div v-if="!data.nodes?.length" class="empty">暂无视觉节点</div>
        </div>

        <aside class="vision-editor">
          <div class="vision-section-title"><h4>{{ editing ? (editId ? '编辑节点' : '新增节点') : '节点配置' }}</h4><small v-if="editing">Key 留空表示保留原值</small></div>
          <form v-if="editing" class="vision-form" @submit.prevent="save">
            <label>名称<input v-model="form.name" class="input" maxlength="60" placeholder="例如：豆包视觉 / 通义千问"></label>
            <label>常用服务商<select class="input" @change="applyPreset($event.target.value)"><option value="">选择后填充接口地址</option><option v-for="preset in presets" :key="preset.value" :value="preset.value">{{ preset.label }}</option></select></label>
            <label>接口地址<input v-model="form.base_url" class="input" maxlength="400" placeholder="https://ark.cn-beijing.volces.com/api/v3" required></label>
            <label>API Key<input v-model="form.api_key" class="input" type="password" autocomplete="new-password" :placeholder="editId ? '留空保留原 Key' : '可留空（本地免鉴权服务）'"></label>
            <label>模型名<input v-model="form.model" class="input" maxlength="120" placeholder="例如：doubao-vision-pro-32k 或端点 ID" required></label>
            <p class="small muted" style="margin:0">主对话/Agent 节点应选择支持文本对话的模型；识图/表情/OCR 节点需要支持图片输入。</p>
            <div class="vision-purposes">
              <small>用途</small>
              <label v-for="(label, value) in data.purposes" :key="value" class="checkbox">
                <input type="checkbox" :checked="form.purposes.includes(value)" @change="togglePurpose(value)"> {{ label }}
              </label>
            </div>
            <div class="vision-inline">
              <label>细节<select v-model="form.detail" class="input"><option value="auto">auto</option><option value="low">low</option><option value="high">high</option><option value="original">original</option></select></label>
              <label>超时(s)<input v-model.number="form.timeout" class="input" type="number" min="5" max="120"></label>
              <label>输出上限<input v-model.number="form.max_tokens" class="input" type="number" min="64" max="4096"></label>
            </div>
            <label class="checkbox"><input v-model="form.enabled" type="checkbox"> 启用此节点</label>
            <label>节点级提示（可选）<textarea v-model="form.prompt" class="input" rows="3" maxlength="1200" placeholder="例如：优先识别二次元角色、表情包出处和图片里的文字"></textarea></label>
            <div class="vision-probe">
              <small>测试连接（可选图片 URL）</small>
              <input v-model="probeText" class="input" placeholder="测试文本" maxlength="200">
              <input v-model="probeImage" class="input" placeholder="https://... 图片地址，留空只测文本">
            </div>
            <div class="row" style="margin-top:12px">
              <button class="button small primary" type="submit" :disabled="busy">{{ busy ? '保存中…' : '保存节点' }}</button>
              <button class="button small ghost" type="button" @click="closeEditor">取消</button>
            </div>
          </form>
          <div v-else class="empty" style="text-align:left">
            左侧节点按用途和顺序调用。可分别配置主对话、Agent、识图、表情判断、OCR，也可以让同一节点服务多种用途。自动择优会按近期成功率和延迟调整本次尝试顺序，不改动手动链路。常见 OpenAI 兼容地址：<br>
            <span class="mono">豆包 Ark /api/v3</span> · <span class="mono">Qwen compatible-mode/v1</span> · <span class="mono">GLM /api/paas/v4</span> · <span class="mono">SiliconFlow /v1</span> · <span class="mono">Ollama /v1</span>
          </div>
        </aside>
      </div>

      <div class="vision-section-title" style="margin-top:18px"><h4>最近调用</h4><small>每 4 秒自动刷新</small></div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>时间</th><th>用途</th><th>节点</th><th>模型</th><th>结果</th><th>耗时</th><th>错误</th></tr></thead>
          <tbody>
            <tr v-for="(row, index) in (data.recent || []).slice(0, 30)" :key="index">
              <td>{{ stamp(row.time) }}</td>
              <td>{{ purposeLabel(row.purpose) }}</td>
              <td>{{ row.name }}</td>
              <td class="mono">{{ row.model }}</td>
              <td><span class="badge" :class="statusClass(row.ok)">{{ row.ok ? '成功' : '失败' }}</span></td>
              <td>{{ row.latency_ms }}ms</td>
              <td class="small muted">{{ row.error || '—' }}</td>
            </tr>
            <tr v-if="!data.recent?.length"><td colspan="7" class="empty">还没有调用记录</td></tr>
          </tbody>
        </table>
      </div>
    </section>
  </section>
</template>

<style scoped>
.vision-page { display: grid; gap: 16px; }
.vision-stats { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 8px; margin: 12px 0; }
.vision-stats > div { border: 1px solid var(--line); border-radius: 12px; padding: 10px 12px; background: var(--soft-fill); }
.vision-stats small { display: block; color: var(--muted); font-size: 10px; }
.vision-stats strong { font-size: 15px; }
.vision-layout { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(320px, .75fr); gap: 16px; }
.vision-chain { display: grid; gap: 9px; align-content: start; }
.vision-node { display: grid; grid-template-columns: 34px minmax(0, 1fr); gap: 10px; border: 1px solid var(--line); border-radius: 14px; padding: 12px; background: var(--soft-fill); }
.vision-node.disabled { opacity: .58; }
.node-index { font-family: monospace; font-weight: 700; color: var(--accent); font-size: 12px; padding-top: 2px; }
.node-main { min-width: 0; }
.node-head { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; }
.node-head strong { font-size: 13px; }
.node-meta { display: flex; flex-wrap: wrap; gap: 5px 12px; margin-top: 6px; color: var(--muted); font-size: 10px; }
.node-meta .mono { color: var(--ink-soft); overflow-wrap: anywhere; }
.node-main p { margin: 7px 0 0; overflow-wrap: anywhere; }
.node-actions { grid-column: 2; display: flex; flex-wrap: wrap; gap: 6px; }
.probe { margin-top: 8px; padding: 7px 9px; border-radius: 9px; font-size: 11px; background: #ffffff08; }
.probe.ok { color: #94e2b5; background: #24a35a18; }
.probe.danger { color: #ffc4cb; background: #ff788b18; }
.vision-editor { border: 1px solid var(--line); border-radius: 14px; padding: 12px; background: var(--soft-fill); align-self: start; }
.vision-section-title { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; margin-bottom: 8px; }
.vision-section-title h4 { margin: 0; font-size: 13px; }
.vision-section-title small { color: var(--muted); font-size: 10px; }
.vision-form { display: grid; gap: 9px; }
.vision-form > label { display: grid; gap: 4px; font-size: 11px; color: var(--muted); }
.vision-purposes { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; font-size: 11px; color: var(--muted); }
.vision-purposes > small { width: 100%; }
.checkbox { display: flex !important; grid-template-columns: none !important; align-items: center; gap: 5px; color: var(--ink-soft) !important; }
.vision-inline { display: grid; grid-template-columns: repeat(3, 1fr); gap: 7px; }
.vision-inline label { display: grid; gap: 4px; font-size: 11px; color: var(--muted); }
.vision-probe { display: grid; gap: 6px; border-top: 1px solid var(--line); padding-top: 8px; }
.vision-probe small { color: var(--muted); font-size: 10px; }
@media (max-width: 900px) {
  .vision-layout { grid-template-columns: 1fr; }
  .vision-stats { grid-template-columns: repeat(2, 1fr); }
  .vision-inline { grid-template-columns: 1fr; }
}
</style>
