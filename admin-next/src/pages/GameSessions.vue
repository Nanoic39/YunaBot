<script setup>
import { computed, onMounted, ref, watch } from 'vue';
import Pager from '../Pager.vue';

const props = defineProps(['request', 'notify']);
const data = ref({ items: [], stats: {}, groups: {}, warning: '' });
const view = ref('live');
const query = ref('');
const busy = ref(false);
const busyId = ref(null);
const error = ref('');
const page = ref(0);
const createOpen = ref(false);
const createForm = ref(null);
const detail = ref(null);
const detailEditing = ref(false);
const detailForm = ref(null);
const notesDraft = ref([]);
const notesBusy = ref(false);
const memberSearch = ref('');
const candidates = ref([]);
const searchBusy = ref(false);
const memberForm = ref({ qq: '', name: '', status: 'auto' });
const memberBusy = ref(false);
const memberEditing = ref('');
const PAGE_SIZE = 15;
let searchTimer = null;

const api = (path, options = {}) => props.request(path, {
  ...options,
  body: options.body === undefined ? undefined : JSON.stringify(options.body),
});

const tabs = [
  { id: 'live', label: '进行中 / 报名中', stat: 'open' },
  { id: 'signup', label: '正在报名', stat: 'signup' },
  { id: 'active', label: '正在进行', stat: 'active' },
  { id: 'reminded', label: '已提醒', stat: 'reminded' },
  { id: 'finished', label: '已结束', stat: 'finished' },
];
const groupOptions = computed(() => Object.entries(data.value.groups || {}).map(([id, name]) => ({ id, name: name || `群 ${id}` })));
const filtered = computed(() => {
  const q = query.value.trim().toLowerCase();
  return (data.value.items || []).filter((row) => !q
    || `${row.game || ''} ${groupName(row)} ${row.gid || ''} ${row.host_name || ''} ${(row.members || []).map(memberName).join(' ')} ${(row.notes || []).map((n) => `${n.key} ${n.value}`).join(' ')}`.toLowerCase().includes(q));
});
const pageRows = computed(() => filtered.value.slice(page.value * PAGE_SIZE, (page.value + 1) * PAGE_SIZE));
const formalMembers = computed(() => (detail.value?.members || []).filter((m) => (m.status || 'formal') === 'formal'));
const waitlistMembers = computed(() => (detail.value?.members || []).filter((m) => m.status === 'waitlist'));

function stamp(value) {
  const n = Number(value || 0);
  return n ? new Date(n * 1000).toLocaleString('zh-CN', { hour12: false }) : '—';
}
function localInput(value) {
  const d = new Date((Number(value || Date.now() / 1000)) * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
function groupName(row) {
  return row.group_name || data.value.groups?.[row.gid] || `群 ${row.gid}`;
}
function phaseLabel(row) {
  if (row.phase_name) return row.phase_name;
  if (row.state === 'cancelled') return '已取消';
  if (row.state === 'ended') return '已结束';
  return Number(row.start) > Number(data.value.now || Date.now() / 1000) ? '正在报名' : '正在进行';
}
function phaseClass(row) {
  if (row.phase === 'signup') return 'ok';
  if (row.phase === 'active') return 'accent';
  if (row.phase === 'cancelled' || row.state === 'cancelled') return 'danger';
  return '';
}
function memberName(member) {
  return member.name || member.qq || '成员';
}
function memberCount(row) {
  return row.member_count ?? (row.members || []).length;
}
function formalCount(row) {
  return row.formal_count ?? (row.members || []).filter((m) => (m.status || 'formal') === 'formal').length;
}
function waitlistCount(row) {
  return row.waitlist_count ?? (row.members || []).filter((m) => m.status === 'waitlist').length;
}
function seatText(row) {
  const formal = `${formalCount(row)}/${row.capacity || '不限'}`;
  const wait = row.waitlist_capacity ? `${waitlistCount(row)}/${row.waitlist_capacity}` : '未开';
  return `正式 ${formal} · 候补 ${wait} · 合计 ${memberCount(row)}`;
}
function formFrom(row) {
  return {
    id: row.id, gid: row.gid, game: row.game, start: localInput(row.start),
    capacity: Number(row.capacity || 0), waitlist_capacity: Number(row.waitlist_capacity || 0),
    state: row.state, host: row.host, host_name: row.host_name || '',
    trigger_rules: copyRules(row.trigger_rules),
  };
}
function notesFrom(row) {
  return (row.notes || []).map((n) => ({ key: n.key || '', value: n.value || '' }));
}
function blankRule() {
  return { when: 'after', time: '21:00', min_players: 0 };
}
function copyRules(rules) {
  return (rules || []).map((rule) => ({
    when: rule.when === 'before' ? 'before' : 'after',
    time: rule.time || '21:00',
    min_players: Number(rule.min_players || 0),
  }));
}
function addRule(form) {
  if (!form) return;
  if (!Array.isArray(form.trigger_rules)) form.trigger_rules = [];
  if (form.trigger_rules.length >= 12) return props.notify?.('最多设置 12 条触发条件');
  form.trigger_rules.push(blankRule());
}
function removeRule(form, index) {
  form?.trigger_rules?.splice(index, 1);
}
function moveRule(form, index, delta) {
  if (!form?.trigger_rules) return;
  const next = index + delta;
  if (next < 0 || next >= form.trigger_rules.length) return;
  const [rule] = form.trigger_rules.splice(index, 1);
  form.trigger_rules.splice(next, 0, rule);
}
function normalizeRules(rules) {
  return (rules || [])
    .filter((rule) => rule && rule.time)
    .map((rule) => ({
      when: rule.when === 'before' ? 'before' : 'after',
      time: String(rule.time).slice(0, 5),
      min_players: Math.max(0, Math.min(50, Number(rule.min_players || 0))),
    }));
}
function ruleSummary(rules) {
  return normalizeRules(rules).map((rule) => `${rule.time}${rule.when === 'after' ? '后' : '前'}${rule.min_players ? `满${rule.min_players}人` : ''}开`).join('；');
}

async function load() {
  busy.value = true;
  error.value = '';
  try {
    data.value = await api(`qq/games?view=${view.value}`);
    page.value = 0;
    if (detail.value) {
      const fresh = data.value.items.find((row) => row.id === detail.value.id);
      if (fresh) {
        detail.value = fresh;
        if (!detailEditing.value) notesDraft.value = notesFrom(fresh);
      } else {
        detail.value = null;
        detailEditing.value = false;
      }
    }
  } catch (e) {
    error.value = e.message || '读取开黑局失败';
  } finally {
    busy.value = false;
  }
}

function setView(next) {
  view.value = next;
  page.value = 0;
  load();
}
function openCreate() {
  createOpen.value = true;
  createForm.value = {
    gid: groupOptions.value[0]?.id || '', game: '', start: localInput(Date.now() / 1000 + 3600),
    capacity: 5, waitlist_capacity: 0, state: 'open', host: '', host_name: '', trigger_rules: [],
  };
}
function closeCreate() {
  createOpen.value = false;
  createForm.value = null;
}
async function saveCreate() {
  if (!createForm.value || busy.value) return;
  const value = createForm.value;
  const start = new Date(value.start).getTime() / 1000;
  if (!Number.isFinite(start) || !String(value.gid || '').trim() || !String(value.game || '').trim() || !String(value.host || '').trim()) {
    props.notify?.('请填写群号、游戏、开始时间和组织者');
    return;
  }
  busy.value = true;
  try {
    await api('qq/games', { method: 'POST', body: {
      gid: String(value.gid).trim(), game: String(value.game).trim(), start,
      capacity: Number(value.capacity || 0), waitlist_capacity: Number(value.waitlist_capacity || 0),
      state: value.state, host: String(value.host).trim(), host_name: String(value.host_name || '').trim(),
      trigger_rules: normalizeRules(value.trigger_rules),
    } });
    props.notify?.('已新建开黑局');
    closeCreate();
    await load();
  } catch (e) {
    props.notify?.(e.message || '新建失败');
  } finally {
    busy.value = false;
  }
}

function openDetail(row) {
  detail.value = JSON.parse(JSON.stringify(row));
  detailEditing.value = false;
  detailForm.value = null;
  notesDraft.value = notesFrom(row);
  memberSearch.value = '';
  candidates.value = [];
  memberEditing.value = '';
  memberForm.value = { qq: '', name: '', status: 'auto' };
}
function closeDetail(force = false) {
  if (!force && detailEditing.value && detailForm.value
      && !confirm('正在编辑开黑信息，关闭会丢失未保存的修改。确定关闭？')) return;
  detail.value = null;
  detailEditing.value = false;
  detailForm.value = null;
}
function startDetailEdit() {
  if (!detail.value) return;
  detailForm.value = formFrom(detail.value);
  detailEditing.value = true;
}
function cancelDetailEdit() {
  detailEditing.value = false;
  detailForm.value = null;
}
async function saveDetail() {
  if (!detail.value || !detailForm.value || busy.value) return;
  const value = detailForm.value;
  const start = new Date(value.start).getTime() / 1000;
  if (!Number.isFinite(start) || !String(value.gid || '').trim() || !String(value.game || '').trim() || !String(value.host || '').trim()) {
    props.notify?.('请填写群号、游戏、开始时间和组织者');
    return;
  }
  busy.value = true;
  try {
    await api(`qq/games/${value.id}`, { method: 'PUT', body: {
      gid: String(value.gid).trim(), game: String(value.game).trim(), start,
      capacity: Number(value.capacity || 0), waitlist_capacity: Number(value.waitlist_capacity || 0),
      state: value.state, host: String(value.host).trim(), host_name: String(value.host_name || '').trim(),
      trigger_rules: normalizeRules(value.trigger_rules),
      notes: notesDraft.value.filter((n) => String(n.key || '').trim() || String(n.value || '').trim()),
    } });
    props.notify?.('已保存开黑局');
    detailEditing.value = false;
    detailForm.value = null;
    await load();
  } catch (e) {
    props.notify?.(e.message || '保存失败');
  } finally {
    busy.value = false;
  }
}

async function saveNotes() {
  if (!detail.value || notesBusy.value) return;
  notesBusy.value = true;
  try {
    await api(`qq/games/${detail.value.id}`, { method: 'PUT', body: {
      notes: notesDraft.value.filter((n) => String(n.key || '').trim() || String(n.value || '').trim()),
    } });
    props.notify?.('已保存备注');
    await load();
  } catch (e) {
    props.notify?.(e.message || '保存备注失败');
  } finally {
    notesBusy.value = false;
  }
}
function addNote() {
  if (notesDraft.value.length >= 10) return props.notify?.('每局最多添加 10 条备注');
  notesDraft.value.push({ key: '', value: '' });
}
function removeNote(index) {
  notesDraft.value.splice(index, 1);
}

function scheduleCandidateSearch() {
  clearTimeout(searchTimer);
  const q = memberSearch.value.trim();
  if (!q) { candidates.value = []; return; }
  searchTimer = setTimeout(searchCandidates, 260);
}
async function searchCandidates() {
  if (!detail.value || searchBusy.value) return;
  const q = memberSearch.value.trim();
  if (!q) { candidates.value = []; return; }
  searchBusy.value = true;
  try {
    const result = await api(`qq/games/${detail.value.id}/member-candidates?q=${encodeURIComponent(q)}`);
    candidates.value = result.items || [];
  } catch (e) {
    candidates.value = [];
    props.notify?.(e.message || '成员查询失败');
  } finally {
    searchBusy.value = false;
  }
}
function chooseCandidate(candidate) {
  memberForm.value.qq = candidate.qq;
  memberForm.value.name = candidate.name || candidate.qq;
  memberEditing.value = candidate.qq;
  memberSearch.value = '';
  candidates.value = [];
}
function editMember(member) {
  memberEditing.value = String(member.qq);
  memberForm.value = { qq: String(member.qq), name: member.name || '', status: member.status || 'formal' };
  memberSearch.value = '';
  candidates.value = [];
}
function resetMemberForm() {
  memberEditing.value = '';
  memberForm.value = { qq: '', name: '', status: 'auto' };
}
async function saveMember() {
  if (!detail.value || memberBusy.value || !String(memberForm.value.qq || '').trim()) {
    if (!String(memberForm.value.qq || '').trim()) props.notify?.('请输入或选择成员');
    return;
  }
  memberBusy.value = true;
  try {
    await api(`qq/games/${detail.value.id}/members`, { method: 'POST', body: {
      qq: String(memberForm.value.qq).trim(), name: String(memberForm.value.name || '').trim(), status: memberForm.value.status,
    } });
    props.notify?.('已保存成员');
    resetMemberForm();
    await load();
  } catch (e) {
    props.notify?.(e.message || '保存成员失败');
  } finally {
    memberBusy.value = false;
  }
}
async function removeMember(member) {
  if (!detail.value || busyId.value || !confirm(`从「${detail.value.game}」移除 @${memberName(member)}？`)) return;
  busyId.value = detail.value.id;
  try {
    await api(`qq/games/${detail.value.id}/members/${encodeURIComponent(member.qq)}`, { method: 'DELETE' });
    props.notify?.('已移除成员');
    await load();
  } catch (e) {
    props.notify?.(e.message || '移除成员失败');
  } finally {
    busyId.value = null;
  }
}
async function atMember(member) {
  if (!detail.value || busyId.value) return;
  busyId.value = `at-${member.qq}`;
  try {
    await api(`qq/games/${detail.value.id}/members/${encodeURIComponent(member.qq)}/at`, { method: 'POST', body: {} });
    props.notify?.(`已在群里单独 @ ${memberName(member)}`);
  } catch (e) {
    props.notify?.(e.message || '单独 @ 失败');
  } finally {
    busyId.value = null;
  }
}

async function remind(row) {
  if (busyId.value) return;
  busyId.value = row.id;
  try {
    await api(`qq/games/${row.id}/remind`, { method: 'POST', body: {} });
    props.notify?.(`已在群里提醒 ${memberCount(row)} 位报名成员（逐个 @）`);
    await load();
  } catch (e) {
    props.notify?.(e.message || '提醒发送失败');
  } finally {
    busyId.value = null;
  }
}
async function cancel(row) {
  if (busyId.value || !confirm(`取消「${row.game}」这局？`)) return;
  busyId.value = row.id;
  try {
    await api(`qq/games/${row.id}/cancel`, { method: 'POST', body: {} });
    props.notify?.('已取消这局开黑');
    await load();
  } catch (e) {
    props.notify?.(e.message || '取消失败');
  } finally {
    busyId.value = null;
  }
}

watch(query, () => { page.value = 0; });
onMounted(load);
</script>

<template>
  <section class="panel game-admin">
    <div class="panel-head">
      <div>
        <h3>开黑管理</h3>
        <p>默认只看进行中和报名中的局；点卡片看详情、改信息和成员，提醒会逐个 @ 对应成员。</p>
      </div>
      <div class="row">
        <button class="button small primary" @click="openCreate">新建约局</button>
        <button class="button small" :disabled="busy" @click="load">{{ busy ? '读取中…' : '刷新' }}</button>
      </div>
    </div>
    <div class="panel-body">
      <div class="stats game-stats">
        <div class="stat"><small>正在报名</small><strong>{{ data.stats?.signup || 0 }}</strong></div>
        <div class="stat"><small>正在进行</small><strong>{{ data.stats?.active || 0 }}</strong></div>
        <div class="stat"><small>正式位</small><strong>{{ data.stats?.formal || 0 }}</strong></div>
        <div class="stat"><small>候补位</small><strong>{{ data.stats?.waitlist || 0 }}</strong></div>
        <div class="stat"><small>十分钟内开始</small><strong>{{ data.stats?.due_soon || 0 }}</strong></div>
      </div>

      <form v-if="createOpen && createForm" class="game-editor" @submit.prevent="saveCreate">
        <div class="game-editor-head">
          <strong>新建开黑局</strong>
          <button type="button" class="button small ghost" @click="closeCreate">关闭</button>
        </div>
        <div class="game-form-grid">
          <label>群号
            <input v-model="createForm.gid" class="input" list="game-group-options" placeholder="群号" required>
            <datalist id="game-group-options"><option v-for="g in groupOptions" :key="g.id" :value="g.id">{{ g.name }}</option></datalist>
          </label>
          <label>游戏
            <input v-model="createForm.game" class="input" maxlength="60" placeholder="例如 MC、CS2" required>
          </label>
          <label>开始时间
            <input v-model="createForm.start" class="input" type="datetime-local" required>
          </label>
          <label>正式位人数
            <input v-model.number="createForm.capacity" class="input" type="number" min="0" max="20" step="1" placeholder="0 表示不限">
          </label>
          <label>候补位数量
            <input v-model.number="createForm.waitlist_capacity" class="input" type="number" min="0" max="20" step="1" placeholder="0 表示不设候补">
          </label>
          <label>状态
            <select v-model="createForm.state" class="input">
              <option value="open">进行中</option>
              <option value="cancelled">已取消</option>
              <option value="ended">已结束</option>
            </select>
          </label>
          <label>组织者 QQ
            <input v-model="createForm.host" class="input" placeholder="QQ号" required>
          </label>
          <label>组织者备注
            <input v-model="createForm.host_name" class="input" maxlength="60" placeholder="可选，未填则用 QQ">
          </label>
        </div>
        <div class="trigger-editor">
          <div class="detail-section-head">
            <h4>开黑触发条件</h4>
            <span class="small muted">可组合多条；按顺序命中第一条</span>
          </div>
          <p class="small muted">例如「21:00 后满 4 人开」；也可以先加「21:00 前满 4 人」，再加「21:00 后满 0 人」作为到点兜底。0 人表示到点即开。</p>
          <div v-for="(rule, index) in createForm.trigger_rules" :key="index" class="trigger-rule-row">
            <select v-model="rule.when" class="input" aria-label="触发时间关系"><option value="before">之前</option><option value="after">之后</option></select>
            <input v-model="rule.time" class="input" type="time" aria-label="触发时间">
            <input v-model.number="rule.min_players" class="input" type="number" min="0" max="50" step="1" aria-label="最低人数">
            <span class="small muted">人以上开</span>
            <button type="button" class="button small ghost" @click="moveRule(createForm, index, -1)" :disabled="index === 0">↑</button>
            <button type="button" class="button small ghost" @click="moveRule(createForm, index, 1)" :disabled="index === createForm.trigger_rules.length - 1">↓</button>
            <button type="button" class="button small danger" @click="removeRule(createForm, index)">删除</button>
          </div>
          <button type="button" class="button small ghost" @click="addRule(createForm)">添加触发条件</button>
          <p v-if="!createForm.trigger_rules.length" class="small muted">不设置时只按开始时间提醒，不自动判断开局条件。</p>
        </div>
        <div class="row game-editor-actions">
          <button class="button primary small" :disabled="busy">{{ busy ? '保存中…' : '保存' }}</button>
          <span class="small muted">正式位含组织者；候补位是正式位满后自动进入的位置。</span>
        </div>
      </form>

      <div class="game-tabs" role="tablist" aria-label="开黑状态">
        <button v-for="tab in tabs" :key="tab.id" type="button" class="game-tab" :class="{ active: view === tab.id }" role="tab" :aria-selected="view === tab.id" @click="setView(tab.id)">
          {{ tab.label }} <span>{{ data.stats?.[tab.stat] || 0 }}</span>
        </button>
      </div>
      <div class="row game-toolbar">
        <input v-model="query" class="input search" type="search" placeholder="搜索游戏、群、组织者、成员或备注" aria-label="搜索开黑局">
      </div>
      <p class="small muted game-hint">已结束/已取消的局只用于查看，不允许提醒或取消；点卡片进入详情。</p>
      <p v-if="error" class="notice" role="alert">{{ error }}</p>
      <p v-if="data.warning" class="notice warn">{{ data.warning }}</p>
      <div v-if="!busy && !pageRows.length" class="empty-state">
        <strong>{{ query ? '没有匹配的开黑局' : '这个状态下暂时没有开黑局' }}</strong>
        <p>{{ query ? '换个关键词试试。' : '默认只展示正在报名和正在进行的局。' }}</p>
      </div>
      <div v-else class="game-list">
        <article v-for="row in pageRows" :key="row.id" class="game-card" role="button" tabindex="0" @click="openDetail(row)" @keydown.enter="openDetail(row)">
          <div class="game-card-head">
            <div class="game-title">
              <strong>{{ row.game }}</strong>
              <span class="badge" :class="phaseClass(row)">{{ phaseLabel(row) }}</span>
              <span v-if="row.reminded" class="badge warn">已提醒</span>
              <span v-if="row.triggered" class="badge accent">条件已达成</span>
            </div>
            <div class="row game-actions" @click.stop>
              <button class="button small primary" :disabled="!row.can_remind || busyId === row.id" @click="remind(row)">{{ busyId === row.id ? '发送中…' : '提醒全部' }}</button>
              <button class="button small danger" :disabled="!row.can_cancel || busyId === row.id" @click="cancel(row)">取消</button>
            </div>
          </div>
          <div class="game-meta">
            <span><b>群</b> {{ groupName(row) }} <span class="mono">{{ row.gid }}</span></span>
            <span><b>组织者</b> {{ row.host_name || row.host }}</span>
            <span><b>开始</b> {{ stamp(row.start) }}</span>
            <span><b>人数</b> {{ seatText(row) }}</span>
          </div>
          <div class="game-members card-members">
            <span v-for="member in (row.members || []).slice(0, 8)" :key="member.qq" class="game-member" :class="{ waitlist: member.status === 'waitlist' }">
              @{{ memberName(member) }} <small>{{ member.status === 'waitlist' ? '候补' : '正式' }}</small>
            </span>
            <span v-if="(row.members || []).length > 8" class="small muted">+{{ row.members.length - 8 }} 人</span>
          </div>
          <div v-if="row.trigger_rules?.length" class="card-notes">
            <span>触发：{{ ruleSummary(row.trigger_rules) }}</span>
          </div>
          <div v-if="row.notes?.length" class="card-notes">
            <span v-for="note in row.notes.slice(0, 3)" :key="note.key">{{ note.key }}：{{ note.value }}</span>
          </div>
          <div class="card-open-hint">点击查看详情与编辑 →</div>
        </article>
      </div>
      <Pager :total="filtered.length" :page="page" @update:page="page = $event" />
    </div>

    <Teleport to="body">
    <div v-if="detail" class="game-drawer-backdrop">
      <aside class="game-drawer" role="dialog" aria-modal="true" aria-label="开黑详情">
        <div class="game-drawer-head">
          <div>
            <div class="game-title"><strong>{{ detail.game }}</strong><span class="badge" :class="phaseClass(detail)">{{ phaseLabel(detail) }}</span><span v-if="detail.reminded" class="badge warn">已提醒</span></div>
            <p class="small muted">#{{ detail.id }} · {{ groupName(detail) }}</p>
          </div>
          <button type="button" class="button small ghost" @click="closeDetail()">关闭</button>
        </div>
        <div class="game-drawer-body">
          <div class="detail-actions">
            <button v-if="!detailEditing" type="button" class="button small" @click="startDetailEdit">编辑信息</button>
            <button v-if="!detailEditing" type="button" class="button small primary" :disabled="!detail.can_remind || busyId === detail.id" @click="remind(detail)">提醒全部（逐个 @）</button>
            <button v-if="!detailEditing" type="button" class="button small danger" :disabled="!detail.can_cancel || busyId === detail.id" @click="cancel(detail)">取消这局</button>
            <span v-if="!detail.can_remind" class="small muted">已结束/已取消的局不可提醒或取消</span>
          </div>

          <form v-if="detailEditing && detailForm" class="detail-form" @submit.prevent="saveDetail">
            <div class="game-form-grid">
              <label>群号<input v-model="detailForm.gid" class="input" list="game-group-options" required></label>
              <label>游戏<input v-model="detailForm.game" class="input" maxlength="60" required></label>
              <label>开始时间<input v-model="detailForm.start" class="input" type="datetime-local" required></label>
              <label>正式位人数<input v-model.number="detailForm.capacity" class="input" type="number" min="0" max="20" step="1"></label>
              <label>候补位数量<input v-model.number="detailForm.waitlist_capacity" class="input" type="number" min="0" max="20" step="1"></label>
              <label>状态<select v-model="detailForm.state" class="input"><option value="open">进行中</option><option value="cancelled">已取消</option><option value="ended">已结束</option></select></label>
              <label>组织者 QQ<input v-model="detailForm.host" class="input" required></label>
              <label>组织者备注<input v-model="detailForm.host_name" class="input" maxlength="60"></label>
            </div>
            <div class="trigger-editor">
              <div class="detail-section-head"><h4>开黑触发条件</h4><span class="small muted">按顺序命中第一条</span></div>
              <div v-for="(rule, index) in detailForm.trigger_rules" :key="index" class="trigger-rule-row">
                <select v-model="rule.when" class="input" aria-label="触发时间关系"><option value="before">之前</option><option value="after">之后</option></select>
                <input v-model="rule.time" class="input" type="time" aria-label="触发时间">
                <input v-model.number="rule.min_players" class="input" type="number" min="0" max="50" step="1" aria-label="最低人数">
                <span class="small muted">人以上开</span>
                <button type="button" class="button small ghost" @click="moveRule(detailForm, index, -1)" :disabled="index === 0">↑</button>
                <button type="button" class="button small ghost" @click="moveRule(detailForm, index, 1)" :disabled="index === detailForm.trigger_rules.length - 1">↓</button>
                <button type="button" class="button small danger" @click="removeRule(detailForm, index)">删除</button>
              </div>
              <button type="button" class="button small ghost" @click="addRule(detailForm)">添加触发条件</button>
            </div>
            <div class="row game-editor-actions"><button class="button primary small" :disabled="busy">{{ busy ? '保存中…' : '保存信息' }}</button><button type="button" class="button small ghost" @click="cancelDetailEdit">取消编辑</button></div>
          </form>
          <div v-else>
            <div class="detail-grid">
              <span><b>群</b>{{ groupName(detail) }} <span class="mono">{{ detail.gid }}</span></span>
              <span><b>组织者</b>{{ detail.host_name || detail.host }}</span>
              <span><b>开始</b>{{ stamp(detail.start) }}</span>
              <span><b>正式位</b>{{ formalCount(detail) }} / {{ detail.capacity || '不限' }}</span>
              <span><b>候补位</b>{{ waitlistCount(detail) }} / {{ detail.waitlist_capacity || '未开' }}</span>
              <span><b>合计</b>{{ memberCount(detail) }} 人</span>
            </div>
            <div class="trigger-summary">
              <b>触发条件</b>
              <span v-if="detail.triggered" class="badge warn">条件已达成</span>
              <span>{{ ruleSummary(detail.trigger_rules) || '未设置，按开始时间提醒' }}</span>
              <small v-if="detail.triggered_text" class="muted">（已触发：{{ detail.triggered_text }}）</small>
            </div>
          </div>

          <section class="detail-section">
            <div class="detail-section-head"><h4>备注</h4><span class="small muted">可自定义地图、玩法、规则等键值</span></div>
            <div v-for="(note, index) in notesDraft" :key="index" class="note-row">
              <input v-model="note.key" class="input" maxlength="30" placeholder="键名，如地图">
              <input v-model="note.value" class="input" maxlength="300" placeholder="内容，如 Erangel">
              <button type="button" class="button small ghost" @click="removeNote(index)">删除</button>
            </div>
            <div class="row detail-section-actions">
              <button type="button" class="button small ghost" @click="addNote">添加备注</button>
              <button type="button" class="button small" :disabled="notesBusy" @click="saveNotes">{{ notesBusy ? '保存中…' : '保存备注' }}</button>
            </div>
          </section>

          <section class="detail-section">
            <div class="detail-section-head"><h4>正式位成员</h4><span class="small muted">{{ formalMembers.length }} 人</span></div>
            <div class="member-list">
              <div v-for="member in formalMembers" :key="member.qq" class="member-row">
                <span class="member-row-name">@{{ memberName(member) }} <small class="mono">{{ member.qq }}</small><span v-if="String(member.qq) === String(detail.host)" class="badge">组织者</span></span>
                <span class="member-row-actions">
                  <button type="button" class="button small ghost" :disabled="busyId === 'at-' + member.qq" @click="atMember(member)">@</button>
                  <button type="button" class="button small ghost" @click="editMember(member)">改</button>
                  <button v-if="String(member.qq) !== String(detail.host)" type="button" class="button small danger" :disabled="busyId === detail.id" @click="removeMember(member)">移除</button>
                </span>
              </div>
              <p v-if="!formalMembers.length" class="small muted">暂无正式成员</p>
            </div>
            <div class="detail-section-head sub"><h4>候补位成员</h4><span class="small muted">{{ waitlistMembers.length }} 人</span></div>
            <div class="member-list">
              <div v-for="member in waitlistMembers" :key="member.qq" class="member-row">
                <span class="member-row-name">@{{ memberName(member) }} <small class="mono">{{ member.qq }}</small><span class="badge">候补</span></span>
                <span class="member-row-actions">
                  <button type="button" class="button small ghost" :disabled="busyId === 'at-' + member.qq" @click="atMember(member)">@</button>
                  <button type="button" class="button small ghost" @click="editMember(member)">改</button>
                  <button type="button" class="button small danger" :disabled="busyId === detail.id" @click="removeMember(member)">移除</button>
                </span>
              </div>
              <p v-if="!waitlistMembers.length" class="small muted">暂无候补成员</p>
            </div>

            <div class="member-add">
              <div class="member-search-row">
                <input v-model="memberSearch" class="input" type="search" placeholder="模糊搜索群成员：QQ号 / 昵称 / 群名片" @input="scheduleCandidateSearch">
                <span v-if="searchBusy" class="small muted">查询中…</span>
              </div>
              <div v-if="candidates.length" class="candidate-list">
                <button v-for="candidate in candidates" :key="candidate.qq" type="button" class="candidate" @click="chooseCandidate(candidate)">
                  <span>@{{ candidate.name }} <small class="mono">{{ candidate.qq }}</small></span>
                  <small v-if="candidate.in_game" class="muted">已在{{ candidate.status === 'waitlist' ? '候补' : '正式' }}位</small>
                  <small v-else class="muted">选择</small>
                </button>
              </div>
              <div class="member-form-row">
                <input v-model="memberForm.qq" class="input" placeholder="QQ号或从上方选择" aria-label="成员 QQ">
                <input v-model="memberForm.name" class="input" maxlength="60" placeholder="备注名（可选）" aria-label="成员备注">
                <select v-model="memberForm.status" class="input" aria-label="成员位置">
                  <option value="auto">自动排位</option>
                  <option value="formal">正式位</option>
                  <option value="waitlist">候补位</option>
                </select>
                <button type="button" class="button small primary" :disabled="memberBusy" @click="saveMember">{{ memberBusy ? '保存中…' : (memberEditing ? '保存成员' : '添加成员') }}</button>
                <button v-if="memberEditing" type="button" class="button small ghost" @click="resetMemberForm">取消</button>
              </div>
            </div>
          </section>
        </div>
      </aside>
    </div>
    </Teleport>
  </section>
</template>

<style scoped>
.game-stats { margin-bottom: 14px; flex-wrap: wrap; }
.game-toolbar { margin: 10px 0 8px; }
.game-hint { margin: 0 0 12px; }
.game-tabs { display: flex; flex-wrap: wrap; gap: 6px; margin: 14px 0 0; }
.game-tab { border: 1px solid var(--line); border-radius: 999px; background: transparent; color: var(--muted); padding: 5px 11px; font: inherit; font-size: 12px; cursor: pointer; }
.game-tab span { margin-left: 4px; opacity: .7; }
.game-tab.active { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 55%, var(--line)); background: color-mix(in srgb, var(--accent) 8%, transparent); }
.game-list { display: grid; gap: 10px; }
.game-card { border: 1px solid var(--line); border-radius: 13px; padding: 13px 14px; background: color-mix(in srgb, var(--surface) 88%, transparent); cursor: pointer; transition: border-color .15s, background .15s; }
.game-card:hover, .game-card:focus-visible { border-color: color-mix(in srgb, var(--accent) 45%, var(--line)); background: color-mix(in srgb, var(--accent) 4%, var(--surface)); outline: none; }
.game-card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.game-title { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-width: 0; }
.game-title strong { font-size: 14px; overflow-wrap: anywhere; }
.game-actions { flex: none; }
.game-meta { display: flex; flex-wrap: wrap; gap: 7px 18px; margin-top: 9px; color: var(--muted); font-size: 12px; }
.game-meta b { color: var(--ink-soft); font-weight: 600; margin-right: 4px; }
.game-members { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 10px; }
.game-member { border: 1px solid var(--line); border-radius: 999px; padding: 3px 8px; font-size: 12px; color: var(--ink-soft); }
.game-member.waitlist { border-style: dashed; color: var(--muted); }
.game-member small { color: var(--muted); margin-left: 2px; }
.card-notes { display: flex; flex-wrap: wrap; gap: 5px 12px; margin-top: 8px; color: var(--muted); font-size: 11px; }
.card-open-hint { color: var(--accent-2); font-size: 11px; margin-top: 9px; }
.game-editor { border: 1px solid color-mix(in srgb, var(--accent) 38%, var(--line)); border-radius: 13px; padding: 12px 14px; margin: 14px 0; background: color-mix(in srgb, var(--accent) 5%, var(--surface)); }
.game-editor-head { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin-bottom: 10px; }
.game-form-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 9px 12px; }
.game-form-grid label { display: grid; gap: 4px; color: var(--muted); font-size: 11px; }
.game-form-grid .input { width: 100%; }
.game-editor-actions { margin-top: 10px; }
.game-drawer-backdrop { position: fixed; inset: 0; z-index: 50; background: rgba(15, 23, 42, .38); display: flex; align-items: center; justify-content: center; padding: 24px; }
.game-drawer { width: min(860px, 100%); max-height: calc(100vh - 48px); overflow: hidden; display: flex; flex-direction: column; background: var(--surface); border: 1px solid var(--line); border-radius: 16px; box-shadow: 0 20px 60px rgba(0,0,0,.22); }
.game-drawer-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; padding: 16px 18px 10px; border-bottom: 1px solid var(--line); }
.game-drawer-head p { margin: 5px 0 0; }
.game-drawer-body { overflow: auto; padding: 14px 18px 22px; }
.detail-actions { display: flex; flex-wrap: wrap; gap: 7px; align-items: center; margin-bottom: 12px; }
.detail-grid { display: flex; flex-wrap: wrap; gap: 7px 18px; padding: 10px 0; color: var(--muted); font-size: 12px; }
.detail-grid b { color: var(--ink-soft); font-weight: 600; margin-right: 4px; }
.detail-form { padding: 4px 0 12px; }
.detail-section { border-top: 1px solid var(--line); margin-top: 14px; padding-top: 12px; }
.detail-section-head { display: flex; align-items: baseline; justify-content: space-between; gap: 10px; margin-bottom: 8px; }
.detail-section-head.sub { margin-top: 14px; }
.detail-section-head h4 { margin: 0; font-size: 13px; }
.detail-section-actions { margin-top: 7px; }
.trigger-editor { margin-top: 12px; border-top: 1px dashed var(--line); padding-top: 10px; }
.trigger-rule-row { display: flex; align-items: center; gap: 5px; margin-bottom: 6px; flex-wrap: wrap; }
.trigger-rule-row .input:first-child { width: 78px; flex: none; }
.trigger-rule-row .input[type="time"] { width: 105px; flex: none; }
.trigger-rule-row .input[type="number"] { width: 70px; flex: none; }
.trigger-summary { display: flex; flex-wrap: wrap; align-items: center; gap: 7px; padding: 8px 10px; border-radius: 9px; background: color-mix(in srgb, var(--accent) 5%, transparent); color: var(--muted); font-size: 12px; }
.trigger-summary b { color: var(--ink-soft); }
.note-row { display: flex; gap: 6px; margin-bottom: 6px; }
.note-row .input:first-child { max-width: 150px; }
.member-list { display: grid; gap: 5px; }
.member-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 5px 7px; border: 1px solid var(--line); border-radius: 8px; font-size: 12px; }
.member-row-name { min-width: 0; overflow-wrap: anywhere; }
.member-row-actions { display: flex; gap: 4px; flex: none; }
.member-add { margin-top: 12px; border-top: 1px dashed var(--line); padding-top: 10px; }
.member-search-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.member-search-row .input { flex: 1; }
.candidate-list { border: 1px solid var(--line); border-radius: 9px; overflow: hidden; margin: 5px 0 8px; max-height: 170px; overflow-y: auto; }
.candidate { display: flex; justify-content: space-between; width: 100%; border: 0; border-bottom: 1px solid var(--line); background: transparent; color: var(--ink); text-align: left; padding: 7px 9px; font: inherit; font-size: 12px; cursor: pointer; }
.candidate:last-child { border-bottom: 0; }
.candidate:hover { background: color-mix(in srgb, var(--accent) 6%, transparent); }
.member-form-row { display: flex; flex-wrap: wrap; gap: 6px; }
.member-form-row .input { max-width: 160px; }
.badge.accent { color: var(--accent); border-color: color-mix(in srgb, var(--accent) 50%, var(--line)); }
@media (max-width: 900px) { .game-form-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media (max-width: 700px) {
  .game-card-head { display: block; }
  .game-actions { margin-top: 10px; justify-content: flex-start; }
  .game-form-grid { grid-template-columns: 1fr; }
  .game-drawer-backdrop { padding: 0; }
  .game-drawer { border-radius: 0; max-height: 100vh; }
  .member-row { align-items: flex-start; flex-direction: column; }
  .note-row { flex-wrap: wrap; }
  .note-row .input:first-child { max-width: none; }
}
</style>
