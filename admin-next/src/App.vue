<script setup>
import { computed, onMounted, onUnmounted, ref, watch, nextTick } from 'vue';
import Pager from './Pager.vue';
import AppearanceSettings from './AppearanceSettings.vue';
import {loadAppearance,applyAppearance,normalizeAppearance,appearanceKey} from './appearance.js';
import MigrationPages from './pages/MigrationPages.vue';
import QQWorkbench from './pages/QQWorkbench.vue';
import GameSessions from './pages/GameSessions.vue';
import RiskReview from './pages/RiskReview.vue';
import VisionChain from './pages/VisionChain.vue';
import { reconcile } from './pages/resources.js';

const user = ref(null);
const password = ref('');
const username = ref('admin');
const loginBusy = ref(false);
const loading = ref(false);
const _allowedTabs = ['overview', 'human', 'world', 'favor', 'ledger', 'slang', 'relations', 'whitelist', 'emotes', 'sessions', 'metrics', 'system', 'ops', 'jobs', 'qq', 'games', 'agent-apps', 'agent-payments', 'risk', 'vision'];
const _initialTab = new URLSearchParams(location.search).get('tab') || location.hash.replace('#', '') || localStorage.getItem('yuna_admin_tab') || 'overview';
const tab = ref(_allowedTabs.includes(_initialTab) ? _initialTab : 'overview');
const mobileNavOpen = ref(false), navSearchRef=ref(null), mobileButton=ref(null);
const mobileMedia=matchMedia('(max-width: 900px)'), isMobile=ref(mobileMedia.matches);
const resized=()=>{isMobile.value=mobileMedia.matches;if(!isMobile.value)mobileNavOpen.value=false;};
mobileMedia.addEventListener('change',resized);onUnmounted(()=>mobileMedia.removeEventListener('change',resized));
function openMobileNav(){mobileNavOpen.value=true;nextTick(()=>navSearchRef.value?.focus());}
function closeMobileNav(){mobileNavOpen.value=false;mobileButton.value?.focus();}
watch(mobileNavOpen,value=>document.documentElement.dataset.navOpen=value?'true':'false');
const appearance=ref(loadAppearance(localStorage));
const appearanceOpen=ref(false), navQuery=ref(''), topMenu=ref(null);
const themeMedia=matchMedia('(prefers-color-scheme: dark)');
const refreshAppearance=()=>applyAppearance(appearance.value,document.documentElement,themeMedia.matches);
function changeAppearance(value){appearance.value=normalizeAppearance(value);}
watch(appearance,value=>{refreshAppearance();try{localStorage.setItem(appearanceKey,JSON.stringify(value));}catch{}topMenu.value=null;},{deep:true});
themeMedia.addEventListener('change',refreshAppearance);
onUnmounted(()=>themeMedia.removeEventListener('change',refreshAppearance));
let savedGroups={};try{savedGroups=JSON.parse(localStorage.getItem('yuna_next_nav_collapsed')||'{}')||{};}catch{}
if(typeof savedGroups!=='object'||Array.isArray(savedGroups))savedGroups={};
const collapsedGroups=ref(Object.fromEntries(Object.entries(savedGroups).filter(([k,v])=>['work','persona','agent','system'].includes(k)&&typeof v==='boolean')));
const navGroups=[
 {id:'work',label:'工作台',items:[['overview','运行概览','◉'],['metrics','运行指标','▥'],['world','世界地图','◈']]},
 {id:'persona',label:'人格与关系',items:[['human','拟人状态','◐'],['favor','好感度','♡'],['ledger','好感账本','◈'],['slang','玩梗接梗','✧'],['relations','关系与记忆','◍'],['sessions','会话记忆','▤'],['emotes','表情包','☺']]},
 {id:'agent',label:'自主与社交',items:[['qq','QQ群工作台','◎'],['games','开黑管理','⚔'],['agent-apps','APP 使用','▣'],['agent-payments','支付管理','◇']]},
 {id:'system',label:'系统管理',items:[['whitelist','白名单','☷'],['risk','风控复核','⚠'],['vision','模型链路','⇄'],['system','插件与系统','⚙'],['jobs','定时任务','◷'],['ops','日志与备份','▤']]},
];
const visibleNavGroups=computed(()=>navGroups.map(g=>({...g,items:g.items.filter(([,label])=>!navQuery.value||g.label.includes(navQuery.value)||label.toLowerCase().includes(navQuery.value.toLowerCase()))})).filter(g=>g.items.length));
const activeNavigation=computed(()=>{for(const g of navGroups){const item=g.items.find(([id])=>id===tab.value);if(item)return {group:g.label,label:item[1]};}return {group:'工作台',label:'运行概览'};});
function groupOpen(id){return !!navQuery.value||(appearance.value.layout==='top'?topMenu.value===id:!collapsedGroups.value[id]);}
function toggleGroup(id){if(appearance.value.layout==='top'){topMenu.value=topMenu.value===id?null:id;return;}collapsedGroups.value={...collapsedGroups.value,[id]:!collapsedGroups.value[id]};try{localStorage.setItem('yuna_next_nav_collapsed',JSON.stringify(collapsedGroups.value));}catch{}}
watch(tab,value=>{const group=navGroups.find(g=>g.items.some(([id])=>id===value));if(group)collapsedGroups.value[group.id]=false;topMenu.value=null;navQuery.value='';},{immediate:true});
const migrationRef = ref(null);
const migrationState = ref({jobs:{},qq:{},'agent-apps':{},'agent-payments':{}});
const migrationTitles = {jobs:'任务调度，有序运行。',qq:'把群里的信息，接到实际事情上。','agent-apps':'她在电脑上做了什么。','agent-payments':'额度、授权与支付记录。'};
const migrationDescriptions = {jobs:'查询、筛选和暂停或恢复定时任务。',qq:'交接简报、群文件资料和群内能力排查。','agent-apps':'节点状态、行为概要和按需查看的详细信息。','agent-payments':'独立的额度管理与支付项目明细。'};
const _ledgerStatuses = ['pending', 'rejected', 'accepted'];
const _initialLedgerStatus = new URLSearchParams(location.search).get('status') || localStorage.getItem('yuna_ledger_status') || 'pending';
const ledgerStatus = ref(_ledgerStatuses.includes(_initialLedgerStatus) ? _initialLedgerStatus : 'pending');
const ledger = ref({ items: [], stats: {} });
const favorUsers = ref([]);
const favorQuery = ref('');
const favorSort = ref('favor');
const favorDrafts = ref({});
const favorBusy = ref({});
const overview = ref({});
const human = ref({});
const world = ref({});
const plugins = ref([]);
const pluginBusy = ref({});
const logs = ref([]);
const backups = ref({ items: [] });
const auditLog = ref([]);
const relations = ref({ items: [], stats: {} });
const memoryGraph = ref({ nodes: [], edges: [] });
const relationQuery = ref('');
const memoryQuery = ref('');
const relationForm = ref({ a: '', b: '', kind: 'friend', label: '' });
const relationBusy = ref(false);
const relationKindLabels = { friend: '朋友', address: '称呼', couple: '亲密', family: '家人', rival: '不对付', colleague: '同学/同事', online: '网友', other: '其他' };
const nodeKindLabels = { self: '自己', user: '用户', group: '群聊', topic: '话题', bot: '机器人', memory: '记忆' };
function relationKindName(kind) { return relationKindLabels[kind] || '其他'; }
function relationDescription(row) { return row.label || `${relationKindName(row.kind)}关系`; }
function nodeKindName(kind) { return nodeKindLabels[kind] || kind || '其他'; }
const whitelist = ref({ groups: [], users: [] });
const whitelistForm = ref({ kind: 'group', id: '', enabled: true });
const emotes = ref({ files: [], categories: [] });
const emoteQuery = ref('');
const emoteCategory = ref('');
const emoteBusy = ref(false);
const emoteUploadCategory = ref('emotes');
const emoteWarmed = ref(false);
const emoteTab = ref('owned');
const emoteUploadOpen = ref(false);
const emoteUploadFile = ref(null);
const emoteUploadPreview = ref('');
const emoteUploadName = ref('');
const emotePreview = ref(null);
const learnableTarget = ref(null);
const learnableNote = ref('');
const learnableOpen = ref(false);
const learnableOriginal = ref('');
const sessions = ref({ items: [] });
const sessionDetail = ref(null);
const metrics = ref({ days: [], llm: {}, consolidate: {}, emote: {} });
const chatStats = ref({ favor_buckets: [], tokens: [] });
const configPlugin = ref('chat');
const configItems = ref([]);
const configQuery = ref('');
const configDraft = ref({});
const configSaving = ref(false);
const messageForm = ref({ target_type: 'group', target_id: '', message: '' });
const messageBusy = ref(false);
const life = ref({});
const slang = ref({ items: [], observations: [], stats: {} });
const testText = ref('我破防了');
const testResult = ref(null);
const memeOutcomeFilter = ref('review');
const memeModeFilter = ref('');
const memeVariantFilter = ref('');
const memeDetailOpen = ref(false);
const memeDetail = ref(null);
const memeDraft = ref({});
const memeBusy = ref(false);
const memeOriginal = ref({});
const memeDraftRestored = ref(false);
const memeSaveError = ref('');
const search = ref('');
const PAGE_SIZE = 15;
const pageState = ref({});
const statusFilter = ref('');
const detail = ref(null);
const currentGroup = ref(null);
const triggerIndex = ref(0);
const appealNote = ref('');
const appealBusy = ref(false);
const typeReason = ref('');
const anomalyTypes = [
  '攻击性', '辱骂/人身攻击', '绕过限制', '关系不够熟的低俗玩笑', '熟人低俗打趣',
  'AI 口头禅', 'AI 语言风格', '重复提及其他 AI', '刷好感', '关系边界',
  '冒犯性称呼', '过度亲密', '骚扰/纠缠', '隐私越界', '歧视/偏见', '引战/煽动',
  '低俗内容', '其他（手动输入）',
];
const typePreset = ref('');

const detailOpen = ref(false);
const detailLoading = ref(false);
const toastText = ref('');
let toastTimer = 0;

function toast(text) {
  toastText.value = text;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { toastText.value = ''; }, 3200);
}

function memeDraftKey(item) { return `yuna:meme-review-draft:${item?.id || ''}`; }
function memeSignature(item) { return `${item?.id || ''}|${item?.reviewed_at || 0}|${item?.time || ''}`; }
const memeDirty = computed(() => memeDetailOpen.value && JSON.stringify(memeDraft.value || {}) !== JSON.stringify(memeOriginal.value || {}));
watch(memeDraft, (value) => {
  if (!memeDetailOpen.value || !memeDetail.value?.id) return;
  try {
    if (JSON.stringify(value || {}) === JSON.stringify(memeOriginal.value || {})) {
      localStorage.removeItem(memeDraftKey(memeDetail.value));
      return;
    }
    localStorage.setItem(memeDraftKey(memeDetail.value), JSON.stringify({
      signature: memeSignature(memeDetail.value), draft: value, saved_at: Date.now(),
    }));
  } catch (_) {}
}, { deep: true });

function syncUrl() {
  const params = new URLSearchParams({ tab: tab.value });
  if (tab.value === 'ledger') params.set('status', ledgerStatus.value);
  history.replaceState(null, '', `?${params.toString()}`);
}

function refreshTab(value) {
  if (migrationTitles[value]) return;
  if (value === 'overview') loadOverview();
  else if (value === 'human') loadHuman();
  else if (value === 'world') loadWorld();
  else if (value === 'favor') loadFavor();
  else if (value === 'metrics') loadMetrics();
  else if (value === 'ledger') loadLedger();
  else if (value === 'slang') loadSlang();
  else if (value === 'relations') loadRelations();
  else if (value === 'whitelist') loadWhitelist();
  else if (value === 'emotes') loadEmotes();
  else if (value === 'sessions') loadSessions();
  else if (value === 'system') loadPlugins();
  else if (value === 'ops') loadOps();
}

function setTab(value) {
  tab.value = value;
  search.value = '';
  mobileNavOpen.value = false;
  localStorage.setItem('yuna_admin_tab', value);
  syncUrl();
  refreshTab(value);
}

function pageFor(key, total) {
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  return Math.min(Number(pageState.value[key] || 0), pages - 1);
}

function setPage(key, value) {
  pageState.value = { ...pageState.value, [key]: Math.max(0, Number(value) || 0) };
}

function pageRows(key, rows) {
  const start = pageFor(key, rows.length) * PAGE_SIZE;
  return rows.slice(start, start + PAGE_SIZE);
}

function setLedgerStatus(value) {
  ledgerStatus.value = value;
  localStorage.setItem('yuna_ledger_status', value);
  syncUrl();
}

function entryLedgerStatus(entry) {
  if (entry?.appeal === 'accepted') return 'accepted';
  if (entry?.appeal === 'rejected') return 'rejected';
  return 'pending';
}

const ledgerStatusCounts = computed(() => {
  const counts = { pending: 0, rejected: 0, accepted: 0, typeRejected: 0 };
  for (const entry of ledger.value.items || []) {
    counts[entryLedgerStatus(entry)] += 1;
    if (entry.appeal === 'rejected' && entry.anomaly_kind === 'type') counts.typeRejected += 1;
  }
  return counts;
});

const ledgerPageRows = computed(() => pageRows('ledger', ledgerGroups.value));
const favorPageRows = computed(() => pageRows('favor', filteredFavorUsers.value));
const relationsPageRows = computed(() => pageRows('relations', filteredRelations.value));
const whitelistRows = computed(() => [
  ...(whitelist.value.groups || []).map((row) => ({ ...row, _kind: 'group' })),
  ...(whitelist.value.users || []).map((row) => ({ ...row, _kind: 'user' })),
]);
const whitelistPageRows = computed(() => pageRows('whitelist', whitelistRows.value));
const pluginPageRows = computed(() => pageRows('plugins', plugins.value));
const backupPageRows = computed(() => pageRows('backups', backups.value.items || []));
const sessionPageRows = computed(() => pageRows('sessions', sessions.value.items || []));

async function request(path, options = {}) {
  const response = await fetch(`/api/${path}`, {
    credentials: 'same-origin',
    ...options,
    headers: {
      ...(options.body ? { 'Content-Type': 'application/json' } : {}),
      ...(options.method && options.method !== 'GET' ? { 'X-Yuna-Admin': '1' } : {}),
      ...(options.headers || {}),
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
  return data;
}

async function login() {
  loginBusy.value = true;
  try {
    await request('login', { method: 'POST', body: JSON.stringify({ username: username.value, password: password.value }) });
    password.value = '';
    await boot();
  } catch (error) { toast(error.message); }
  finally { loginBusy.value = false; }
}

async function boot() {
  try { user.value = await request('me'); }
  catch (error) { user.value = null; return; }
  await Promise.allSettled([
    loadOverview(), loadHuman(), loadWorld(), loadFavor(), loadMetrics(), loadOps(), loadPlugins(), loadLedger(),
    loadSlang(), loadRelations(), loadWhitelist(), loadEmotes(), loadSessions(),
  ]);
  refreshTab(tab.value);
}

async function loadFavor() {
  try {
    const data = await request('chat/favor');
    reconcile(favorUsers.value, data.items || [], 'qq');
  } catch (error) { toast(error.message); }
}

const filteredFavorUsers = computed(() => {
  const q = favorQuery.value.trim().toLowerCase();
  const rows = (favorUsers.value || []).filter((row) => !q
    || `${row.nickname || ''} ${row.qq} ${row.notes || ''} ${(row.tags || []).join(' ')}`.toLowerCase().includes(q));
  if (favorSort.value === 'stable') return rows;
  return [...rows].sort((a, b) => favorSort.value === 'interactions'
    ? Number(b.interactions || 0) - Number(a.interactions || 0)
    : Number(b.favorability || 0) - Number(a.favorability || 0));
});

async function saveFavor(row) {
  if (favorBusy.value[row.qq]) return;
  if (favorDrafts.value[row.qq] === '') { toast('好感度不能为空'); return; }
  const value = Number(favorDrafts.value[row.qq] ?? row.favorability);
  if (!Number.isFinite(value)) { toast('好感度必须是数字'); return; }
  favorBusy.value = { ...favorBusy.value, [row.qq]: true };
  try {
    await request(`chat/favor/${row.qq}`, {
      method: 'PUT', body: JSON.stringify({ favorability: value }),
    });
    favorUsers.value.sort((a,b) => favorSort.value==='interactions' ? Number(b.interactions||0)-Number(a.interactions||0) : favorSort.value==='favor' ? Number(b.favorability||0)-Number(a.favorability||0) : 0);
    favorSort.value='stable';
    row.favorability=value;
    delete favorDrafts.value[row.qq];
    toast('好感度已保存');
  } catch (error) { toast(error.message); }
  finally { favorBusy.value = { ...favorBusy.value, [row.qq]: false }; }
}

async function loadMetrics() {
  try {
    const [m, s] = await Promise.all([request('chat/metrics?days=14'), request('chat/stats')]);
    metrics.value = m || {};
    chatStats.value = s || { favor_buckets: [], tokens: [] };
  } catch (error) { toast(error.message); }
}

const metricsTokenPageRows = computed(() => pageRows('metricsTokens', chatStats.value.tokens || []));

async function loadWorld() {
  try { world.value = await request('chat/world'); }
  catch (error) { toast(error.message); }
}

async function loadRelations() {
  try {
    const [rel, graph] = await Promise.all([request('chat/relations'), request('chat/memory-graph')]);
    relations.value = rel || { items: [], stats: {} };
    memoryGraph.value = graph || { nodes: [], edges: [] };
  } catch (error) { toast(error.message); }
}

async function loadEmotes() {
  try {
    emotes.value = await request('chat/emotes');
    if (!emoteWarmed.value && (emotes.value.files || []).length) {
      emoteWarmed.value = true;
      request('media/warm', { method: 'POST', body: JSON.stringify({ width: 360 }) })
        .then(() => loadEmotes())
        .catch(() => {});
    }
  } catch (error) { toast(error.message); }
}

async function warmEmotes() {
  emoteBusy.value = true;
  try {
    const result = await request('media/warm', { method: 'POST', body: JSON.stringify({ width: 360 }) });
    toast(`已预热 ${result.generated || 0} 张缩略图`);
    await loadEmotes();
  } catch (error) { toast(error.message); }
  finally { emoteBusy.value = false; }
}

const filteredEmotes = computed(() => {
  const q = emoteQuery.value.trim().toLowerCase();
  return (emotes.value.files || []).filter((file) => (!emoteCategory.value || file.category === emoteCategory.value)
    && (!q || `${file.category} ${file.label} ${file.path}`.toLowerCase().includes(q))).slice(0, 160);
});

const rejectedManagedEmotes = computed(() => {
  const q = emoteQuery.value.trim().toLowerCase();
  return (emotes.value.managed || []).filter((item) => item.status === 'rejected' && (!q
    || `${item.label || ''} ${item.reason || ''} ${item.status || ''}`.toLowerCase().includes(q)));
});

const learnableManagedEmotes = computed(() => {
  const q = emoteQuery.value.trim().toLowerCase();
  return (emotes.value.learnable_items || emotes.value.managed || []).filter((item) => item.status === 'learnable' && (!q
    || `${item.label || ''} ${item.reason || ''} ${item.status || ''} ${item.manual_note || ''}`.toLowerCase().includes(q)));
});

function openEmotePreview(item, type = 'owned') {
  emotePreview.value = { ...item, _type: type };
}

function openLearnable(item) {
  learnableTarget.value = item;
  learnableNote.value = item.manual_note || '';
  learnableOriginal.value = learnableNote.value;
  learnableOpen.value = true;
}

function closeLearnable(force = false) {
  if (!force && learnableNote.value.trim() !== learnableOriginal.value.trim()
      && !confirm('有未保存的判断提示，关闭会丢失。确定关闭？')) return;
  learnableOpen.value = false;
}

async function submitLearnable() {
  if (!learnableTarget.value) return;
  emoteBusy.value = true;
  try {
    await request('chat/emotes/learnable', {
      method: 'POST',
      body: JSON.stringify({ hash: learnableTarget.value.hash, note: learnableNote.value }),
    });
    toast('已加入可学习，后续出现会参考记录重新判断');
    closeLearnable(true);
    await loadEmotes();
  } catch (error) { toast(error.message); }
  finally { emoteBusy.value = false; }
}

function openEmoteUpload() {
  emoteUploadOpen.value = true;
  emoteUploadFile.value = null;
  emoteUploadPreview.value = '';
  emoteUploadName.value = '';
}

function closeEmoteUpload(force = false) {
  if (!force && (emoteUploadFile.value || emoteUploadName.value.trim())
      && !confirm('已经选择了图片或填写了文件名，关闭会清空当前内容。确定关闭？')) return;
  emoteUploadOpen.value = false;
  emoteUploadFile.value = null;
  emoteUploadPreview.value = '';
  emoteUploadName.value = '';
}

function pickEmoteFile(event) {
  const file = event.target.files?.[0];
  if (!file) return;
  emoteUploadFile.value = file;
  emoteUploadName.value = file.name.replace(/\s+/g, '_');
  const reader = new FileReader();
  reader.onload = () => { emoteUploadPreview.value = String(reader.result || ''); };
  reader.readAsDataURL(file);
}

async function submitEmoteUpload() {
  if (!emoteUploadFile.value || !emoteUploadPreview.value) { toast('先选择一张图片'); return; }
  emoteBusy.value = true;
  try {
    const raw = emoteUploadPreview.value;
    const data = raw.includes(',') ? raw.split(',', 2)[1] : raw;
    await request('chat/emotes', {
      method: 'POST',
      body: JSON.stringify({ name: emoteUploadName.value, category: emoteUploadCategory.value, data }),
    });
    toast('表情已上传');
    closeEmoteUpload(true);
    await loadEmotes();
  } catch (error) { toast(error.message); }
  finally { emoteBusy.value = false; }
}

async function deleteEmote(file) {
  if (!confirm(`将「${file.label || file.path}」移入拒绝区？`)) return;
  emoteBusy.value = true;
  try {
    await request(`chat/emote-file?path=${encodeURIComponent(file.path)}`, { method: 'DELETE' });
    toast('已移入拒绝区');
    await loadEmotes();
  } catch (error) { toast(error.message); }
  finally { emoteBusy.value = false; }
}

async function acceptManagedEmote(item) {
  emoteBusy.value = true;
  try {
    await request('chat/emotes/accept', { method: 'POST', body: JSON.stringify({ hash: item.hash }) });
    toast('已恢复为可用表情');
    await loadEmotes();
  } catch (error) { toast(error.message); }
  finally { emoteBusy.value = false; }
}

async function deleteManagedEmote(item) {
  if (!item.path) { toast('该记录没有可删除的图片文件'); return; }
  if (!confirm('永久删除这张拒绝区图片？')) return;
  emoteBusy.value = true;
  try {
    await request('chat/emotes/managed', { method: 'DELETE', body: JSON.stringify({ path: item.path }) });
    toast('已删除');
    await loadEmotes();
  } catch (error) { toast(error.message); }
  finally { emoteBusy.value = false; }
}

async function loadSessions() {
  try { sessions.value = await request('chat/sessions'); }
  catch (error) { toast(error.message); }
}

async function openSession(session) {
  try { sessionDetail.value = await request(`chat/session/${session.kind}/${encodeURIComponent(session.key)}`); }
  catch (error) { toast(error.message); }
}

const filteredRelations = computed(() => {
  const q = relationQuery.value.trim().toLowerCase();
  return (relations.value.items || []).filter((row) => !q
    || `${row.a} ${row.b} ${row.nickname_a || ''} ${row.nickname_b || ''} ${row.label || ''} ${row.kind || ''} ${relationKindName(row.kind)}`.toLowerCase().includes(q));
});

const filteredMemoryNodes = computed(() => {
  const q = memoryQuery.value.trim().toLowerCase();
  return (memoryGraph.value.nodes || []).filter((node) => !q
    || `${node.label || ''} ${node.kind || ''} ${JSON.stringify(node.meta || {})}`.toLowerCase().includes(q)).slice(0, 80);
});

async function addRelation() {
  if (!/^\d{5,12}$/.test(relationForm.value.a) || !/^\d{5,12}$/.test(relationForm.value.b)) {
    toast('两端都要填写有效 QQ 号'); return;
  }
  relationBusy.value = true;
  try {
    await request('chat/relations', {
      method: 'PUT', body: JSON.stringify(relationForm.value),
    });
    toast('关系已保存');
    relationForm.value = { a: '', b: '', kind: 'friend', label: '' };
    await loadRelations();
  } catch (error) { toast(error.message); }
  finally { relationBusy.value = false; }
}

async function removeRelation(row) {
  if (!confirm(`删除关系 ${row.a} ↔ ${row.b}？`)) return;
  relationBusy.value = true;
  try {
    await request(`chat/relations?a=${encodeURIComponent(row.a)}&b=${encodeURIComponent(row.b)}`, { method: 'DELETE' });
    toast('关系已删除');
    await loadRelations();
  } catch (error) { toast(error.message); }
  finally { relationBusy.value = false; }
}

async function inferRelations() {
  relationBusy.value = true;
  try {
    const result = await request('chat/relations/infer', { method: 'POST', body: '{}' });
    toast(`已重新推断，新增 ${result.added || 0} 条`);
    await loadRelations();
  } catch (error) { toast(error.message); }
  finally { relationBusy.value = false; }
}

async function loadWhitelist() {
  try { whitelist.value = await request('chat/whitelist'); }
  catch (error) { toast(error.message); }
}

async function toggleWhitelist(kind, row) {
  const id = String(kind === 'group' ? row.id_group : row.id_user);
  try {
    await request('chat/whitelist', {
      method: 'PUT', body: JSON.stringify({ kind, id, enabled: !row.enabled }),
    });
    await loadWhitelist();
  } catch (error) { toast(error.message); }
}

async function addWhitelist() {
  if (!whitelistForm.value.id.trim()) { toast('填写群号或 QQ'); return; }
  try {
    await request('chat/whitelist', {
      method: 'POST',
      body: JSON.stringify({ kind: whitelistForm.value.kind, id: whitelistForm.value.id.trim(), enabled: whitelistForm.value.enabled }),
    });
    toast('已加入白名单');
    whitelistForm.value.id = '';
    await loadWhitelist();
  } catch (error) { toast(error.message); }
}

async function removeWhitelist(kind, row) {
  const id = String(kind === 'group' ? row.id_group : row.id_user);
  if (!confirm(`移出白名单：${id}？`)) return;
  try {
    await request(`chat/whitelist?kind=${kind}&id=${encodeURIComponent(id)}`, { method: 'DELETE' });
    await loadWhitelist();
  } catch (error) { toast(error.message); }
}

async function loadOps() {
  try {
    const [logData, backupData, auditData] = await Promise.all([
      request('logs'), request('backups'), request('audit'),
    ]);
    logs.value = logData.items || [];
    backups.value = backupData || { items: [] };
    auditLog.value = auditData.items || [];
  } catch (error) { toast(error.message); }
}

async function sendMessage() {
  if (!messageForm.value.target_id || !messageForm.value.message.trim()) { toast('请填写目标和消息'); return; }
  const bot = overview.value.bots?.[0]?.id;
  if (!bot) { toast('没有在线机器人'); return; }
  messageBusy.value = true;
  try {
    await request('messages', {
      method: 'POST',
      body: JSON.stringify({ bot_id: bot, ...messageForm.value, message: messageForm.value.message.trim() }),
    });
    toast('消息已发送');
    messageForm.value.message = '';
  } catch (error) { toast(error.message); }
  finally { messageBusy.value = false; }
}

async function createBackup() {
  try {
    await request('backups', { method: 'POST', body: JSON.stringify({ kind: 'manual' }) });
    toast('备份已创建');
    await loadOps();
  } catch (error) { toast(error.message); }
}

async function deleteBackup(item) {
  if (!confirm(`删除备份 ${item.name}？`)) return;
  try {
    await request(`backups/${encodeURIComponent(item.name)}`, { method: 'DELETE' });
    toast('备份已删除');
    await loadOps();
  } catch (error) { toast(error.message); }
}

async function restoreBackup(item) {
  if (!confirm(`从 ${item.name} 恢复？会覆盖当前数据，恢复前会自动创建快照。`)) return;
  try {
    await request(`backups/${encodeURIComponent(item.name)}/restore`, {
      method: 'POST', body: JSON.stringify({ confirm: true }),
    });
    toast('备份已恢复，请刷新页面');
    await loadOps();
  } catch (error) { toast(error.message); }
}

async function loadPluginConfig(plugin = configPlugin.value) {
  if (!plugin) return;
  configPlugin.value = plugin;
  try {
    const data = await request(`plugins/${encodeURIComponent(plugin)}/config`);
    configItems.value = data.items || [];
    configDraft.value = {};
  } catch (error) { toast(error.message); }
}

const filteredConfig = computed(() => {
  const q = configQuery.value.trim().toLowerCase();
  return (configItems.value || []).filter((item) => !q || `${item.key} ${item.label || ''} ${item.help || ''}`.toLowerCase().includes(q));
});

const pagedConfig = computed(() => pageRows('config', filteredConfig.value));

const configGroups = computed(() => {
  const groups = new Map();
  for (const item of pagedConfig.value) {
    const key = item.group || '其他';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  return [...groups.entries()].map(([name, items]) => ({ name, items }));
});

async function saveConfig() {
  const values = Object.fromEntries(Object.entries(configDraft.value).filter(([, value]) => value !== undefined));
  if (!Object.keys(values).length) { toast('没有修改'); return; }
  configSaving.value = true;
  try {
    const result = await request(`plugins/${encodeURIComponent(configPlugin.value)}/config`, {
      method: 'PUT', body: JSON.stringify({ values }),
    });
    toast(result.restart_required ? '已保存，部分配置需要重启' : '配置已保存');
    await loadPluginConfig();
  } catch (error) { toast(error.message); }
  finally { configSaving.value = false; }
}

async function loadPlugins(refreshConfig = true) {
  try {
    reconcile(plugins.value, await request('plugins'));
    const available = new Set(plugins.value.map((plugin) => plugin.id));
    if (!available.has(configPlugin.value)) configPlugin.value = plugins.value[0]?.id || '';
    if (refreshConfig && configPlugin.value && !Object.keys(configDraft.value).length) await loadPluginConfig(configPlugin.value);
  } catch (error) { toast(error.message); }
}

async function togglePlugin(plugin) {
  if (pluginBusy.value[plugin.id]) return;
  pluginBusy.value[plugin.id]=true;
  const enabled=!plugin.enabled;
  try {
    await request(`plugins/${encodeURIComponent(plugin.id)}`, {method:'PUT',body:JSON.stringify({enabled})});
    plugin.enabled=enabled;
    toast(`${plugin.name||plugin.id} 已${enabled?'启用':'停用'}`);
    await loadPlugins(false);
  } catch(error){toast(error.message);}
  finally{pluginBusy.value[plugin.id]=false;}
}

async function loadLedger() {
  loading.value = true;
  try { ledger.value = await request('chat/favor/ledger?limit=300'); }
  catch (error) { toast(error.message); }
  finally { loading.value = false; }
}

async function loadSlang() {
  try { slang.value = await request('chat/meme?limit=300'); }
  catch (error) { toast(error.message); }
}

async function loadOverview() {
  try { overview.value = await request('overview'); }
  catch (error) { toast(error.message); }
}

async function loadHuman() {
  try {
    const [h, l] = await Promise.all([request('chat/human'), request('chat/life')]);
    human.value = h;
    life.value = l;
  } catch (error) { toast(error.message); }
}

const ledgerGroups = computed(() => {
  const q = search.value.trim().toLowerCase();
  const groups = [];
  for (const entry of ledger.value.items || []) {
    if (entryLedgerStatus(entry) !== ledgerStatus.value) continue;
    const qq = entry.qq || '';
    const groupId = entry.group || '';
    const keys = new Set(entry.context_keys || []);
    let group = null;
    for (const candidate of groups) {
      if (candidate.qq !== qq || candidate.group !== groupId) continue;
      if (keys.size) {
        // 只有上下文消息键有交集才归入同一段对话；不能把所有历史都并到一起。
        if ([...keys].some((key) => candidate.contextKeys.has(key))) { group = candidate; break; }
      } else if (!candidate.contextKeys.size && entry.context_hash
                 && candidate.contextHashes.has(entry.context_hash)) {
        group = candidate;
        break;
      }
    }
    if (!group) {
      group = {
        key: `${qq}|${groupId}|${entry.id}`,
        qq, group: groupId,
        nickname: entry.nickname || entry.qq || '',
        entries: [],
        contextKeys: new Set(),
        contextHashes: new Set(),
      };
      groups.push(group);
    }
    group.entries.push(entry);
    group.nickname = entry.nickname || group.nickname;
    for (const key of keys) group.contextKeys.add(key);
    if (entry.context_hash) group.contextHashes.add(entry.context_hash);
  }
  return groups
    .map((group) => {
      const reasons = [...new Set(group.entries.map((e) => e.reason || '未注明'))];
      const penalty = group.entries.reduce((sum, e) => sum + Number(e.penalty || 0), 0);
      const requested = group.entries.reduce((sum, e) => sum + Number(e.requested || 0), 0);
      return {
        ...group,
        penalty,
        requested,
        reasons,
        finalReasons: [...new Set(group.entries.map((e) => {
          if (e.appeal === 'accepted') return '申诉成功';
          if (e.appeal === 'rejected') return e.corrected_reason || (e.anomaly_kind === 'type' ? '类型异常' : '认定异常');
          return '待处理';
        }))],
        time: Math.max(...group.entries.map((e) => Number(e.time || 0))),
        has_text: group.entries.some((e) => e.has_text),
        count: group.entries.length,
        source: group.entries[0]?.source || 'message',
        latestId: group.entries[0]?.id || '',
      };
    })
    .filter((group) => !q || `${group.nickname} ${group.qq} ${group.group} ${group.reasons.join(' ')}`.toLowerCase().includes(q));
});
const filteredSlang = computed(() => {
  const q = search.value.trim().toLowerCase();
  return (slang.value.items || []).filter((entry) => (!statusFilter.value || entry.status === statusFilter.value)
    && (!q || `${entry.term || ''} ${entry.meaning || ''} ${entry.usage || ''}`.toLowerCase().includes(q)));
});
const stats = computed(() => ledger.value.stats || {});
const slangStats = computed(() => slang.value.stats || {});
const memeModeLabels = { literal: '字面', typo: '打错字', banter: '玩梗', uncertain: '不确定' };
const memeVariantLabels = {
  none: '无变体', typo: '错字', homophone: '谐音', near_homophone: '近音',
  shape_similar: '形近', combined_char: '合体字', abbreviation: '缩写',
  lyric: '歌词/台词', group_jargon: '群黑话', reference: '典故/引用', unknown: '未知',
};
const memeSpeechLabels = {
  request: '请求', question: '提问', statement: '陈述', emotion: '情绪',
  roleplay: '扮演', quote: '引用', lyric: '歌词', other: '其他',
};
const memeStrategyLabels = {
  literal: '字面回答', play_along: '顺着接', deadpan: '冷面配合',
  self_deprecate: '自嘲', callout: '点破', ask_back: '轻问', ignore: '不接',
};
const memeOutcomeLabels = { success: '接住了', missed: '漏接', wrong: '接错', wrong_term: '接错梗', false_trigger: '误接梗', literal_ok: '字面正常', pending: '待评估', uncertain: '不确定' };
const memeReviewLabels = { reviewed: '已复核', auto: '自动评估', pending: '待复核' };
function memeOutcomeLabel(value) { return memeOutcomeLabels[value] || value || '待评估'; }
function memeModeLabel(value) { return memeModeLabels[value] || value || '未分类'; }
function memeVariantLabel(value) { return memeVariantLabels[value] || value || '未知'; }
function memeSpeechLabel(value) { return memeSpeechLabels[value] || value || '其他'; }
function memeStrategyLabel(value) { return memeStrategyLabels[value] || value || '未定'; }
function memeReviewLabel(value) { return memeReviewLabels[value] || value || '待复核'; }
function memeOutcomeTone(value) {
  if (value === 'success' || value === 'literal_ok') return 'ok';
  if (['missed', 'wrong', 'wrong_term', 'false_trigger'].includes(value)) return 'danger';
  return 'warn';
}
function memeReviewTone(value) {
  if (value === 'reviewed') return 'ok';
  if (value === 'auto') return '';
  return 'warn';
}
function memeBadRows(item) {
  return ['missed', 'wrong', 'wrong_term', 'false_trigger'].includes(item.outcome);
}
const memeOutcomeCounts = computed(() => {
  const rows = slang.value.observations || [];
  return {
    all: rows.length,
    review: rows.filter((row) => row.review_status === 'pending').length,
    pending: rows.filter((row) => row.outcome === 'pending').length,
    bad: rows.filter(memeBadRows).length,
    success: rows.filter((row) => row.outcome === 'success').length,
    literal: rows.filter((row) => row.outcome === 'literal_ok').length,
  };
});
const memeFilteredObservations = computed(() => {
  const q = search.value.trim().toLowerCase();
  return (slang.value.observations || []).filter((item) => {
    if (memeOutcomeFilter.value === 'review' && item.review_status !== 'pending') return false;
    if (memeOutcomeFilter.value === 'pending' && item.outcome !== 'pending') return false;
    if (memeOutcomeFilter.value === 'bad' && !memeBadRows(item)) return false;
    if (memeOutcomeFilter.value === 'success' && item.outcome !== 'success') return false;
    if (memeOutcomeFilter.value === 'literal' && item.outcome !== 'literal_ok') return false;
    if (memeModeFilter.value && item.mode !== memeModeFilter.value) return false;
    if (memeVariantFilter.value && item.variant_type !== memeVariantFilter.value) return false;
    if (!q) return true;
    const hay = `${item.message || ''} ${item.quote || ''} ${item.recognized_term || ''} ${item.meaning || ''} ${item.reply || ''} ${item.outcome_reason || ''} ${item.qq || ''} ${item.group || ''}`.toLowerCase();
    return hay.includes(q);
  });
});
const memePageRows = computed(() => pageRows('meme', memeFilteredObservations.value));
const slangPageRows = computed(() => pageRows('slangEntries', filteredSlang.value));
const triggerEvidence = computed(() => {
  const rows = detail.value?.entry?.triggers || [];
  return rows[triggerIndex.value] || null;
});

function applyTypePreset() {
  typeReason.value = typePreset.value === '其他（手动输入）' ? '' : typePreset.value;
}

function selectTrigger(index) {
  triggerIndex.value = index;
  appealNote.value = triggerEvidence.value?.appeal_note || '';
  typeReason.value = triggerEvidence.value?.corrected_reason || triggerEvidence.value?.reason || '';
  typePreset.value = anomalyTypes.includes(typeReason.value) ? typeReason.value : '';
}

function time(value) {
  if (!value) return '—';
  return new Date(value * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' });
}
function money(value) { return `-${Number(value || 0).toFixed(1)}`; }
async function copyHash(value) {
  try { await navigator.clipboard.writeText(value); toast('哈希已复制'); }
  catch (_) { toast('复制失败，请手动选择'); }
}

async function openGroup(group) {
  currentGroup.value = group;
  detailOpen.value = true;
  detailLoading.value = true;
  detail.value = { entry: { ...group, triggers: [] }, records: [] };
  try {
    const responses = await Promise.all(group.entries.map((entry) =>
      request(`chat/favor/ledger/${encodeURIComponent(entry.id)}`)));
    const entries = responses.map((response) => response.entry || {});
    const triggers = entries.flatMap((entry, index) => {
      const rows = entry.triggers?.length ? entry.triggers : [{
        time: entry.time, penalty: entry.penalty, requested: entry.requested,
        reason: entry.reason, text_hash: entry.text_hash, source: entry.source, text: entry.text,
      }];
      return rows.map((trigger) => ({
        ...trigger,
        record_id: entry.id,
        record_index: index + 1,
        archive_context: entry.archive_context || null,
        threshold: entry.threshold || {},
        favor_before: entry.favor_before ?? entry.relation?.favorability ?? null,
        favor_after: entry.favor_after ?? entry.relation?.favorability ?? null,
        relation_stage: entry.relation_stage || '',
        target_factor: Number(entry.relation?.target_factor ?? 1),
        reason_explanation: entry.reason_explanation || '',
        appeal: entry.appeal || 'none',
        appeal_note: entry.appeal_note || '',
        restored: Boolean(entry.restored),
        anomaly_kind: entry.anomaly_kind || '',
        corrected_reason: entry.corrected_reason || '',
        resolution: entry.resolution || '',
      }));
    });
    const context = [];
    const seen = new Set();
    for (const entry of entries) {
      for (const turn of entry.context || []) {
        const key = `${turn.role}|${turn.qq}|${turn.content}`;
        if (seen.has(key)) continue;
        seen.add(key);
        context.push(turn);
      }
    }
    const latest = entries[entries.length - 1] || {};
    const first = entries[0] || {};
    detail.value = {
      entry: {
        ...latest,
        id: group.key,
        penalty: group.penalty,
        requested: group.requested,
        reason: group.reasons.join('、'),
        reason_explanation: [...new Set(entries.map((e) => e.reason_explanation).filter(Boolean))].join(' '),
        group: group.group || latest.group || first.group || '',
        qq: group.qq,
        nickname: group.nickname,
        time: latest.time,
        count: triggers.length,
        triggers,
        context,
        text: first.text || latest.text || '',
        backfilled: entries.some((e) => e.backfilled),
        archive_context: first.archive_context || latest.archive_context || null,
        relation_stage: latest.relation_stage || first.relation_stage || '',
        favor_before: first.favor_before ?? first.relation?.favorability ?? latest.favor_before,
        favor_after: latest.favor_after ?? latest.relation?.favorability,
      },
      records: entries,
      feedback: responses[0]?.feedback || {},
    };
    triggerIndex.value = 0;
    appealNote.value = triggerEvidence.value?.appeal_note || '';
    typeReason.value = triggerEvidence.value?.corrected_reason || triggerEvidence.value?.reason || '';
  typePreset.value = anomalyTypes.includes(typeReason.value) ? typeReason.value : '';
  } catch (error) { toast(error.message); }
  finally { detailLoading.value = false; }
}

async function refreshCurrentGroup() {
  await loadLedger();
  const latest = ledgerGroups.value.find((group) => currentGroup.value && group.key === currentGroup.value.key);
  if (latest) await openGroup(latest);
  else detailOpen.value = false;
}

async function submitAppeal() {
  const trigger = triggerEvidence.value;
  if (!trigger?.record_id || appealBusy.value) return;
  appealBusy.value = true;
  try {
    await request(`chat/favor/ledger/${encodeURIComponent(trigger.record_id)}/appeal`, {
      method: 'POST', body: JSON.stringify({ note: appealNote.value }),
    });
    toast('申诉已提交');
    await refreshCurrentGroup();
  } catch (error) { toast(error.message); }
  finally { appealBusy.value = false; }
}

async function resolveAppeal(accepted, kind = 'normal', correctedReason = '') {
  const trigger = triggerEvidence.value;
  if (!trigger?.record_id || appealBusy.value) return;
  appealBusy.value = true;
  try {
    await request(`chat/favor/ledger/${encodeURIComponent(trigger.record_id)}/resolve`, {
      method: 'POST', body: JSON.stringify({ accepted, kind, corrected_reason: correctedReason }),
    });
    toast(accepted ? '已恢复该次扣分' : (kind === 'type' ? '已标记为类型异常' : '已认定异常'));
    await refreshCurrentGroup();
  } catch (error) { toast(error.message); }
  finally { appealBusy.value = false; }
}

async function updateAnomaly(kind) {
  const trigger = triggerEvidence.value;
  if (!trigger?.record_id || appealBusy.value) return;
  if (kind === 'type' && !typeReason.value.trim()) { toast('填写或选择正确类型'); return; }
  appealBusy.value = true;
  try {
    await request(`chat/favor/ledger/${encodeURIComponent(trigger.record_id)}/anomaly`, {
      method: 'PUT',
      body: JSON.stringify({ kind, corrected_reason: kind === 'type' ? typeReason.value : '' }),
    });
    toast(kind === 'type' ? '异常类型已更新' : '已改为普通异常');
    await refreshCurrentGroup();
  } catch (error) { toast(error.message); }
  finally { appealBusy.value = false; }
}

async function backfill() {
  try {
    const result = await request('chat/favor/ledger/backfill', { method: 'POST', body: '{}' });
    toast(`回填完成：${result.filled || 0} 条`);
    await loadLedger();
    if (currentGroup.value) {
      const latest = ledgerGroups.value.find((group) => group.key === currentGroup.value.key);
      if (latest) await openGroup(latest);
    }
  } catch (error) { toast(error.message); }
}

async function removeSlang(entry) {
  if (!confirm(`删除「${entry.term}」？`)) return;
  try {
    await request(`chat/slang/${encodeURIComponent(entry.term)}`, { method: 'DELETE' });
    toast(`已删除「${entry.term}」`);
    await loadSlang();
  } catch (error) { toast(error.message); }
}

async function setSlang(entry, status) {
  try {
    await request(`chat/slang/${encodeURIComponent(entry.term)}`, {
      method: 'PUT', body: JSON.stringify({ status }),
    });
    await loadSlang();
    toast(`「${entry.term}」已标记为 ${status}`);
  } catch (error) { toast(error.message); }
}

async function testSlang() {
  try {
    testResult.value = await request('chat/meme/test', {
      method: 'POST',
      body: JSON.stringify({ text: testText.value, group: '' }),
    });
    toast(testResult.value.analysis ? '语义分析完成' : '暂时无法分析');
  } catch (error) { toast(error.message); }
}

function openMeme(item, options = {}) {
  const base = {
    outcome: item.outcome === 'pending' ? '' : (item.outcome || ''),
    mode: item.mode || 'uncertain',
    variant_type: item.variant_type || 'unknown',
    speech_act: item.speech_act || 'other',
    reply_strategy: item.reply_strategy || 'ask_back',
    recognized_term: item.recognized_term || '',
    meaning: item.meaning || '',
    corrected_term: item.corrected_term || '',
    corrected_meaning: item.corrected_meaning || '',
    corrected_usage: item.corrected_usage || '',
    note: item.review_note || '',
  };
  const key = memeDraftKey(item);
  let draft = { ...base };
  memeDetail.value = item;
  memeOriginal.value = { ...base };
  memeDraftRestored.value = false;
  memeSaveError.value = '';
  if (options.restore !== false) {
    try {
      const saved = JSON.parse(localStorage.getItem(key) || 'null');
      if (saved?.draft && typeof saved.draft === 'object' && saved.signature === memeSignature(item)) {
        draft = { ...base, ...saved.draft };
        memeDraftRestored.value = true;
      }
    } catch (_) {}
  } else {
    try { localStorage.removeItem(key); } catch (_) {}
  }
  memeDraft.value = draft;
  memeDetailOpen.value = true;
}

function discardMemeDraft() {
  if (!memeDetail.value) return;
  try { localStorage.removeItem(memeDraftKey(memeDetail.value)); } catch (_) {}
  memeDraft.value = { ...memeOriginal.value };
  memeDraftRestored.value = false;
}

function closeMeme(force = false) {
  if (memeBusy.value) return;
  if (!force && memeDirty.value) {
    const ok = confirm('有未保存的复核修改。关闭后会保留草稿，下次打开可恢复；确定关闭？');
    if (!ok) return;
  }
  memeDetailOpen.value = false;
}

async function saveMeme(closeAfter = false) {
  if (!memeDetail.value || memeBusy.value) return;
  const draft = memeDraft.value || {};
  if (draft.outcome === 'wrong_term' && !String(draft.corrected_term || '').trim()) {
    memeSaveError.value = '接错梗时请填写正确的梗/作品名，便于长期学习';
    toast(memeSaveError.value);
    return;
  }
  if (String(draft.corrected_term || '').trim() && !String(draft.corrected_meaning || '').trim()) {
    memeSaveError.value = '填写了正确梗名时，还需要填写正确释义，否则不会写入长期语义记忆';
    toast(memeSaveError.value);
    return;
  }
  memeBusy.value = true;
  memeSaveError.value = '';
  try {
    const result = await request(`chat/meme/review/${encodeURIComponent(memeDetail.value.id)}`, {
      method: 'POST',
      body: JSON.stringify({
        outcome: draft.outcome || '',
        note: draft.note || '',
        mode: draft.mode,
        variant_type: draft.variant_type,
        speech_act: draft.speech_act,
        reply_strategy: draft.reply_strategy,
        recognized_term: draft.recognized_term,
        meaning: draft.meaning,
        corrected_term: draft.corrected_term,
        corrected_meaning: draft.corrected_meaning,
        corrected_usage: draft.corrected_usage,
      }),
    });
    try { localStorage.removeItem(memeDraftKey(memeDetail.value)); } catch (_) {}
    toast('复核已保存，并写入语义记忆');
    await loadSlang();
    const latest = (slang.value.observations || []).find((row) => row.id === result.id);
    if (latest) openMeme(latest, { restore: false });
    else {
      memeDetail.value = { ...memeDetail.value, ...result };
      memeOriginal.value = { ...draft };
    }
    if (closeAfter) closeMeme(true);
  } catch (error) {
    memeSaveError.value = error.message;
    toast(error.message);
  } finally {
    memeBusy.value = false;
  }
}

async function deleteMeme() {
  if (!memeDetail.value || memeBusy.value) return;
  if (!confirm('删除这条判断记录？不会删除长期语义记忆。')) return;
  memeBusy.value = true;
  try {
    try { localStorage.removeItem(memeDraftKey(memeDetail.value)); } catch (_) {}
    await request(`chat/meme/${encodeURIComponent(memeDetail.value.id)}`, { method: 'DELETE' });
    toast('判断记录已删除');
    closeMeme(true);
    await loadSlang();
  } catch (error) { toast(error.message); }
  finally { memeBusy.value = false; }
}

async function evaluateMeme(item) {
  const followUp = prompt('输入对方随后说的话（用于判断这次接梗是否接住）', '');
  if (!followUp) return;
  try {
    const result = await request('chat/meme/evaluate', {
      method: 'POST', body: JSON.stringify({ group: item.group || '', qq: item.qq || '', text: followUp }),
    });
    toast(result.ok ? `评估完成：${memeOutcomeLabel(result.item?.outcome)}` : '没有可评估的待定记录');
    await loadSlang();
  } catch (error) { toast(error.message); }
}

async function toggleSlang() {
  try {
    const enabled = slang.value.enabled === false;
    await request('chat/slang/settings', { method: 'PUT', body: JSON.stringify({ enabled }) });
    toast(enabled ? '玩梗接梗已开启' : '玩梗接梗已关闭');
    await loadSlang();
  } catch (error) { toast(error.message); }
}

async function pruneSlang() {
  try {
    const result = await request('chat/slang/prune', { method: 'POST', body: JSON.stringify({ days: 14, include_active: false, force: true }) });
    toast(`已清理 ${result.count || 0} 条冷门候选`);
    await loadSlang();
  } catch (error) { toast(error.message); }
}

async function learn(scope = 'group') {
  try {
    const result = await request('chat/slang/learn', { method: 'POST', body: JSON.stringify({ scope: 'group' }) });
    toast(`群聊学习完成：${result.learned || 0} 条`);
    await loadSlang();
  } catch (error) { toast(error.message); }
}

async function clearSlang() {
  if (!confirm('清空全部长期语义记忆（含内置词条）？此操作不可撤销，之后只从群聊重新学习。')) return;
  try {
    const result = await request('chat/slang/clear-all', { method: 'POST' });
    toast(`已清空 ${result.cleared || 0} 条长期语义记忆`);
    await loadSlang();
  } catch (error) { toast(error.message); }
}

onMounted(() => {
  boot();
});
</script>

<template>
  <div v-if="!user" class="login-wrap">
    <div class="login-card">
      <p class="eyebrow">Yuna Console · Next</p>
      <h2>语言与关系</h2>
      <p>管理控制台</p>
      <label class="small muted">用户名</label>
      <input v-model="username" class="input" autocomplete="username" />
      <label class="small muted" style="display:block;margin-top:10px">管理口令</label>
      <input v-model="password" class="input" type="password" autocomplete="current-password" @keyup.enter="login" />
      <button class="button primary" :disabled="loginBusy" @click="login">{{ loginBusy ? '正在登录…' : '进入控制台' }}</button>
    </div>
  </div>

  <div v-else class="shell" @keydown.esc="topMenu=null">
    <AppearanceSettings v-if="appearanceOpen" :model-value="appearance" @update:model-value="changeAppearance" @close="appearanceOpen=false" />
    <header class="topbar">
      <div class="brand">
        <button class="mobile-menu" type="button" ref="mobileButton" aria-label="打开导航" @click="openMobileNav">☰</button>
        <div class="logo">Y</div>
        <div><h1>Yuna Console</h1><p>language · relation · memory</p></div>
      </div>
      <div class="top-actions">
        <button class="button small" @click="appearanceOpen=true;topMenu=null" aria-label="打开外观设置">◒ 外观</button>
        <span class="pill"><span class="dot"></span>在线</span>
        <span class="pill">v{{ user.version }}</span>
        <a class="button small ghost" href="/">旧版控制台</a>
      </div>
    </header>

    <div class="grid-layout">
      <aside class="side" :class="{open:mobileNavOpen}" :inert="isMobile&&!mobileNavOpen" :aria-hidden="isMobile&&!mobileNavOpen?true:undefined" aria-label="工作台导航" @keydown.esc="topMenu=null;mobileNavOpen=false">
        <div class="side-head"><span class="side-label">工作台导航</span><button class="close nav-close" aria-label="关闭导航" @click="closeMobileNav">×</button><input ref="navSearchRef" v-model="navQuery" class="input nav-search" type="search" placeholder="查找功能" aria-label="查找导航功能"></div>
        <nav class="nav-scroll"><section v-for="group in visibleNavGroups" :key="group.id" class="nav-group"><button class="nav-group-toggle" :aria-expanded="groupOpen(group.id)" :aria-controls="'nav-'+group.id" :title="group.label" @click="toggleGroup(group.id)"><span>{{group.label}}</span><span aria-hidden="true">{{groupOpen(group.id)?'⌃':'⌄'}}</span></button><div v-show="groupOpen(group.id)" :id="'nav-'+group.id" class="nav-group-body"><button v-for="[value,label,glyph] in group.items" :key="value" class="nav-btn" :class="{active:tab===value}" :title="label" :aria-label="label" :aria-current="tab===value?'page':undefined" @click="setTab(value)"><span class="nav-icon" aria-hidden="true">{{glyph}}</span><span class="nav-text">{{label}}</span></button></div></section><p v-if="!visibleNavGroups.length" class="nav-empty">没有匹配功能</p></nav>
        <div class="side-foot"><span>{{user.username}} · {{user.role}}</span><a href="/">旧版控制台</a></div>
      </aside>

      <div v-if="mobileNavOpen" class="nav-scrim" @click="closeMobileNav"></div>
      <div v-if="!isMobile&&appearance.layout==='top'&&topMenu" class="nav-menu-dismiss" @click="topMenu=null"></div>
      <main class="content">
        <section class="hero">
          <p class="eyebrow">Human texture</p>
          <h2>{{ migrationTitles[tab] || (tab === 'slang' ? '先听懂，再接住。' : tab === 'overview' ? '此刻，Yuna 正在这里。' : tab === 'human' ? '情绪、生活与关系，都有迹可循。' : tab === 'world' ? '此刻所在，以及能走到的地方。' : tab === 'system' ? '运行中的插件与能力开关。' : tab === 'ops' ? '运行日志、审计记录与数据备份。' : tab === 'relations' ? '人物关系与记忆网络。' : tab === 'favor' ? '谁被记住，谁被偏爱。' : tab === 'metrics' ? '模型、记忆和互动的运行数据。' : tab === 'whitelist' ? '群聊与私聊接入范围。' : tab === 'emotes' ? '收藏的表情与图片素材。' : tab === 'sessions' ? '会话记忆与近期上下文。' : tab === 'games' ? '把群里的约局，收在一个地方。' : tab === 'risk' ? '哪些判断值得再看一眼。' : tab === 'vision' ? '主对话、Agent 与视觉，走同一条可控链路。' : '关系不是一条线，是一本账。') }}</h2>
          <p>{{ migrationDescriptions[tab] || (tab === 'slang'
            ? '用户这句话是字面、打错字还是故意谐音/形近/合体字/群内黑话，由上下文语义判断，不靠词表。接完后的对话会自动评估接住、漏接或接错，并进入人工复核。'
            : tab === 'vision'
              ? '统一配置主对话、Agent、识图、表情判断和 OCR 模型；按用途和顺序自动降级，并按近期稳定性自动择优。'
            : tab === 'overview'
              ? '运行状态、连接、插件、事件和健康告警。'
              : tab === 'human'
                ? '实时心情、体力、当前活动、生活维度和关系分布。称呼与回复语气会跟随关系阶段变化。'
                : tab === 'world'
                  ? '当前位置、天气、场景细节和可走路线。'
                  : tab === 'system'
                    ? '插件列表与启用状态。'
                    : tab === 'ops'
                      ? '最近的运行日志、审计操作和可恢复的数据备份。'
                      : tab === 'relations'
                        ? '人物关系、记忆节点与连接。'
                        : tab === 'favor'
                          ? '查看与管理所有用户的好感度、互动次数和备注。'
                            : tab === 'metrics'
                              ? '调用量、延迟、Token、记忆固化和好感分布。'
                              : tab === 'whitelist'
                            ? '允许接入的群与用户。'
                            : tab === 'emotes'
                            ? '按分类浏览收藏的表情包。'
                            : tab === 'sessions'
                              ? '查看记忆摘要、事实和最近对话。'
                              : tab === 'games'
                                ? '查看当前群的约局、组织者和报名成员；需要时在群里逐个 @ 提醒。'
                              : tab === 'risk'
                                ? '检测命中、语义拿不准和随机抽查会进入人工复核；每条只需判断正确处理、误判或漏判。标注反馈只作为语义 few-shot，不生成关键词硬规则。'
                              : '从这里查看扣分原因、触发原文、上下文和申诉结果。旧记录缺失原文时会标记原因；能从原文存档恢复的会直接回填。') }}</p>
          <div v-if="tab==='overview'" class="stats">
            <div class="stat"><small>运行时长</small><strong>{{ Math.floor((overview.uptime || 0) / 3600) }}h</strong></div>
            <div class="stat"><small>在线机器人</small><strong>{{ overview.bots?.length || 0 }}</strong></div>
            <div class="stat"><small>插件</small><strong>{{ overview.plugins || 0 }}</strong></div>
            <div class="stat"><small>健康</small><strong :style="{color: overview.health?.status === 'ok' ? 'var(--ok)' : 'var(--warn)'}">{{ overview.health?.status || '—' }}</strong></div>
          </div>
          <div v-else-if="tab==='human'" class="stats">
            <div class="stat"><small>心情</small><strong>{{ human.label || '—' }}</strong></div>
            <div class="stat"><small>活力</small><strong>{{ Math.round((human.mood?.energy || 0) * 100) }}%</strong></div>
            <div class="stat"><small>当前活动</small><strong>{{ human.activity || life.activity?.name || '发呆' }}</strong></div>
            <div class="stat"><small>位置</small><strong>{{ human.location || life.location || '—' }}</strong></div>
          </div>
          <div v-else-if="tab==='ledger'" class="stats">
            <div class="stat"><small>累计扣分</small><strong>{{ money(stats.total_penalty) }}</strong></div>
            <div class="stat"><small>账本条目</small><strong>{{ stats.count || 0 }}</strong></div>
            <div class="stat"><small>待申诉</small><strong>{{ stats.pending || 0 }}</strong></div>
            <div class="stat"><small>今日扣分</small><strong>{{ money(stats.today_penalty) }}</strong></div>
          </div>
          <div v-else-if="tab==='slang'" class="stats">
            <div class="stat"><small>判断记录</small><strong>{{ slangStats.observations || 0 }}</strong></div>
            <div class="stat"><small>待复核</small><strong>{{ slangStats.review_pending || 0 }}</strong></div>
            <div class="stat"><small>误接 / 接错</small><strong>{{ (slangStats.wrong || 0) + (slangStats.wrong_term || 0) + (slangStats.false_trigger || 0) }}</strong></div>
            <div class="stat"><small>漏接</small><strong>{{ slangStats.missed || 0 }}</strong></div>
            <div class="stat"><small>接住</small><strong>{{ slangStats.success || 0 }}</strong></div>
            <div class="stat"><small>歌词/引用</small><strong>{{ slangStats.lyric || 0 }}</strong></div>
            <div class="stat"><small>语义词条</small><strong>{{ slangStats.active || 0 }}<span class="muted"> / {{ slangStats.total || 0 }}</span></strong></div>
          </div>
        </section>

        <div class="page-breadcrumb"><span>{{activeNavigation.group}}</span><span aria-hidden="true">/</span><strong>{{activeNavigation.label}}</strong></div>

        <div class="page-stack">
        <VisionChain v-if="tab==='vision'" :request="request" :notify="toast" />
      <RiskReview v-if="tab==='risk'" :request="request" :notify="toast" />
        <QQWorkbench v-if="tab==='qq'" ref="migrationRef" :request="request" :notify="toast" :user="user" :view-state="migrationState.qq" :navigate="setTab" />
        <GameSessions v-if="tab==='games'" :request="request" :notify="toast" />
        <MigrationPages v-if="migrationTitles[tab] && tab!=='qq'" :key="tab" ref="migrationRef" :kind="tab" :request="request" :notify="toast" :user="user" :view-state="migrationState[tab]" />
        <section v-if="tab==='overview'" class="panel">
          <div class="panel-head"><div><h3>运行概览</h3><p>{{ overview.environment || '' }} · v{{ overview.version || user.version }}</p></div><button class="button small ghost" @click="loadOverview">刷新</button></div>
          <div class="panel-body">
            <div class="info-grid">
              <div><small>机器人</small><strong>{{ (overview.bots || []).map(b => `${b.id} · ${b.adapter}`).join(' / ') || '暂无在线连接' }}</strong></div>
              <div><small>已处理事件</small><strong>{{ overview.events ?? '—' }}</strong></div>
              <div><small>已停用插件</small><strong>{{ overview.disabled ?? 0 }}</strong></div>
              <div><small>进程内存</small><strong>{{ overview.metrics?.memory_mb ? `${overview.metrics.memory_mb} MB` : '—' }}</strong></div>
              <div><small>磁盘</small><strong>{{ overview.health?.disk ? `${overview.health.disk.used_percent ?? '—'}% 已用` : '—' }}</strong></div>
              <div><small>最近备份</small><strong>{{ overview.health?.backups?.latest || '—' }}</strong></div>
            </div>
            <div v-if="overview.health?.warnings?.length" class="notice" style="margin-top:12px">告警：{{ overview.health.warnings.join('；') }}</div>
            <h3 style="font-size:13px;margin:16px 0 8px">快速发送</h3>
            <div class="row">
              <select v-model="messageForm.target_type" class="input" style="width:auto">
                <option value="group">群聊</option><option value="private">私聊</option>
              </select>
              <input v-model="messageForm.target_id" class="input" style="max-width:160px" placeholder="目标 ID" />
              <input v-model="messageForm.message" class="input" style="flex:1;min-width:200px" placeholder="消息内容" @keyup.enter="sendMessage" />
              <button class="button small" :disabled="messageBusy" @click="sendMessage">{{ messageBusy ? '发送中…' : '发送' }}</button>
            </div>
          </div>
        </section>

        <section v-if="tab==='human'" class="panel">
          <div class="panel-head"><div><h3>此刻的 Yuna</h3><p>{{ human.label || '平静' }} · {{ human.energy_label || '正常' }} · {{ human.activity || '发呆' }}</p></div><button class="button small ghost" @click="loadHuman">刷新</button></div>
          <div class="panel-body">
            <div class="info-grid">
              <div><small>心情</small><strong>{{ human.label || '—' }}（愉悦 {{ Math.round((human.mood?.valence || 0) * 100) }} / 烦躁 {{ Math.round((human.mood?.irritation || 0) * 100) }} / 孤独 {{ Math.round((human.mood?.loneliness || 0) * 100) }}）</strong></div>
              <div><small>体力</small><strong>{{ Math.round((human.mood?.energy || 0) * 100) }}% · {{ human.energy_label || '—' }}</strong></div>
              <div><small>位置</small><strong>{{ human.location || life.location || '—' }}</strong></div>
              <div><small>活动</small><strong>{{ human.activity || life.activity?.name || '发呆' }}</strong></div>
              <div><small>夜间</small><strong>{{ human.night ? '是' : '否' }}</strong></div>
              <div><small>最近更新</small><strong>{{ time(human.updated) }}</strong></div>
            </div>
            <h3 style="font-size:13px;margin:16px 0 8px">生活维度</h3>
            <div class="info-grid">
              <div v-for="(value, key) in (life.dimensions || {})" :key="key"><small>{{ key }}</small><strong>{{ Math.round(Number(value) * 100) }}%</strong></div>
              <div v-if="!Object.keys(life.dimensions || {}).length" style="grid-column:1/-1"><small>暂无</small><strong>—</strong></div>
            </div>
            <h3 style="font-size:13px;margin:16px 0 8px">关系分布</h3>
            <div class="row">
              <span v-for="row in (human.relationships || [])" :key="row.label" class="pill">{{ row.label }} · {{ row.count }}</span>
              <span v-if="!human.relationships?.length" class="muted small">暂无关系数据</span>
            </div>
            <p class="small muted" style="margin-top:14px">称呼会按关系阶段变化：生疏时用昵称/你，熟悉后才允许更随意的叫法；「哥哥」仍然只属于唯一指定的人。</p>
          </div>
        </section>

        <section v-if="tab==='world'" class="panel">
          <div class="panel-head"><div><h3>{{ world.current?.name || '未知位置' }}</h3><p>{{ world.current?.weather?.text || '—' }} {{ world.current?.weather?.temperature ?? '' }}°C · {{ world.current?.kind || '' }}</p></div><button class="button small ghost" @click="loadWorld">刷新</button></div>
          <div class="panel-body">
            <div class="info-grid">
              <div><small>当前位置</small><strong>{{ world.current?.name || '—' }}</strong></div>
              <div><small>天气</small><strong>{{ world.current?.weather?.text || '—' }} {{ world.current?.weather?.temperature ?? '' }}°C</strong></div>
              <div><small>场景细节</small><strong>{{ Object.entries(world.details || {}).map(([k,v]) => k + '=' + v).slice(0,4).join(' · ') || '—' }}</strong></div>
              <div><small>可走路线</small><strong>{{ (world.routes || []).length }} 条</strong></div>
            </div>
            <h3 style="font-size:13px;margin:16px 0 8px">可前往</h3>
            <div class="row">
              <span v-for="route in (world.routes || [])" :key="route.to || route.node" class="pill">{{ route.name || route.to || route.node }} · {{ route.minutes || '?' }} 分钟</span>
              <span v-if="!world.routes?.length" class="muted small">暂无路线</span>
            </div>
            <h3 style="font-size:13px;margin:16px 0 8px">已知地点</h3>
            <div class="row">
              <span v-for="node in (world.nodes || []).slice(0, 30)" :key="node.id" class="badge" :class="node.id === world.current?.id ? 'ok' : ''">{{ node.name }}</span>
            </div>
          </div>
        </section>

        <section v-if="tab==='ops'" class="panel">
          <div class="panel-head"><div><h3>日志与备份</h3><p>{{ logs.length }} 条日志 · {{ backups.items?.length || 0 }} 个备份</p></div><div class="row"><button class="button small" @click="createBackup">创建备份</button><button class="button small ghost" @click="loadOps">刷新</button></div></div>
          <div class="panel-body">
            <h3 style="font-size:13px;margin:0 0 8px">数据备份</h3>
            <div class="table-wrap" style="border:1px solid var(--line);border-radius:12px">
              <table>
                <thead><tr><th>名称</th><th>类型</th><th>大小</th><th>时间</th><th>操作</th></tr></thead>
                <tbody>
                  <tr v-for="item in backupPageRows" :key="item.name">
                    <td class="mono" style="max-width:260px;overflow-wrap:anywhere">{{ item.name }}</td>
                    <td><span class="badge">{{ item.kind }}</span></td>
                    <td>{{ Math.round((item.size || 0) / 1024) }} KB</td>
                    <td class="muted">{{ time(item.mtime) }}</td>
                    <td><div class="row"><a class="button small ghost" :href="`/api/backups/${encodeURIComponent(item.name)}`" download>下载</a><button class="button small ghost" @click="restoreBackup(item)">恢复</button><button class="button small danger" @click="deleteBackup(item)">删除</button></div></td>
                  </tr>
                  <tr v-if="!backups.items?.length"><td colspan="5" class="empty">暂无备份</td></tr>
                </tbody>
              </table>
            </div>
            <Pager :total="backups.items?.length || 0" :page="pageFor('backups', backups.items?.length || 0)" @update:page="setPage('backups', $event)" />
            <h3 style="font-size:13px;margin:16px 0 8px">最近日志</h3>
            <div class="log-list">
              <div v-for="(row, index) in logs.slice(-80).reverse()" :key="'log'+index" class="log-row">
                <span class="mono muted">{{ row.time || row.ts || '' }}</span>
                <span class="badge" :class="String(row.level || '').toLowerCase().includes('error') ? 'danger' : String(row.level || '').toLowerCase().includes('warn') ? 'warn' : ''">{{ row.level || row.type || 'info' }}</span>
                <span>{{ row.message || row.text || row.msg || JSON.stringify(row) }}</span>
              </div>
              <div v-if="!logs.length" class="empty">暂无日志</div>
            </div>
            <h3 style="font-size:13px;margin:16px 0 8px">最近审计</h3>
            <div class="row">
              <span v-for="(row, index) in auditLog.slice(0, 12)" :key="'audit'+index" class="pill">{{ row.action || row.event || '—' }} · {{ row.actor || row.user || '' }}</span>
              <span v-if="!auditLog.length" class="muted small">暂无记录</span>
            </div>
          </div>
        </section>

        <section v-if="tab==='relations'" class="panel">
          <div class="panel-head">
            <div><h3>关系与记忆</h3><p>{{ filteredRelations.length }} / {{ relations.items?.length || 0 }} 条关系 · {{ memoryGraph.edges?.length || 0 }} 条记忆连接</p></div>
            <div class="row"><button class="button small ghost" :disabled="relationBusy" @click="inferRelations">重新推断</button><button class="button small ghost" @click="loadRelations">刷新</button></div>
          </div>
          <div class="panel-body">
            <div class="row" style="margin-bottom:10px">
              <input v-model="relationQuery" class="input search" placeholder="搜索 QQ / 昵称 / 标签" />
              <select v-model="relationForm.kind" class="input" style="width:auto">
                <option value="friend">朋友</option><option value="address">称呼</option><option value="couple">亲密</option>
                <option value="family">家人</option><option value="rival">不对付</option><option value="colleague">同学/同事</option>
                <option value="online">网友</option><option value="other">其他</option>
              </select>
              <input v-model="relationForm.a" class="input" style="max-width:130px" placeholder="QQ A" />
              <input v-model="relationForm.b" class="input" style="max-width:130px" placeholder="QQ B" />
              <input v-model="relationForm.label" class="input" style="flex:1;min-width:160px" placeholder="标签，例如：常一起接梗" />
              <button class="button small" :disabled="relationBusy" @click="addRelation">保存关系</button>
            </div>
            <div class="table-wrap" style="border:1px solid var(--line);border-radius:12px;max-height:340px">
              <table>
                <thead><tr><th>A</th><th>B</th><th>关系</th><th>标签</th><th>操作</th></tr></thead>
                <tbody>
                  <tr v-for="row in relationsPageRows" :key="row.a + '-' + row.b">
                    <td>{{ row.nickname_a || row.a }}<div class="mono">{{ row.a }}</div></td>
                    <td>{{ row.nickname_b || row.b }}<div class="mono">{{ row.b }}</div></td>
                    <td><span class="badge">{{ relationKindName(row.kind) }}</span></td>
                    <td class="muted">{{ relationDescription(row) }}</td>
                    <td><button class="button small danger" :disabled="relationBusy" @click="removeRelation(row)">删除</button></td>
                  </tr>
                  <tr v-if="!filteredRelations.length"><td colspan="5" class="empty">没有匹配的人物关系</td></tr>
                </tbody>
              </table>
            </div>
            <Pager :total="filteredRelations.length" :page="pageFor('relations', filteredRelations.length)" @update:page="setPage('relations', $event)" />
            <div class="row" style="justify-content:space-between;margin:16px 0 8px">
              <h3 style="font-size:13px;margin:0">记忆网络</h3>
              <input v-model="memoryQuery" class="input search" placeholder="搜索节点" />
            </div>
            <div class="row">
              <span v-for="node in filteredMemoryNodes" :key="node.id" class="pill">{{ node.label }} · {{ nodeKindName(node.kind) }}<span v-if="node.meta?.facts"> · {{ node.meta.facts }} 条事实</span></span>
              <span v-if="!filteredMemoryNodes.length" class="muted small">没有匹配的记忆节点</span>
            </div>
          </div>
        </section>

        <section v-if="tab==='whitelist'" class="panel">
          <div class="panel-head"><div><h3>白名单</h3><p>允许接入的群与用户</p></div><button class="button small ghost" @click="loadWhitelist">刷新</button></div>
          <div class="panel-body">
            <div class="row" style="margin-bottom:12px">
              <select v-model="whitelistForm.kind" class="input" style="width:auto"><option value="group">群</option><option value="user">用户</option></select>
              <input v-model="whitelistForm.id" class="input" style="max-width:180px" placeholder="群号 / QQ" />
              <button class="button small" @click="addWhitelist">添加</button>
            </div>
            <div class="table-wrap" style="border:1px solid var(--line);border-radius:12px">
              <table>
                <thead><tr><th>类型</th><th>ID</th><th>状态</th><th>操作</th></tr></thead>
                <tbody>
                  <tr v-for="row in whitelistPageRows" :key="row._kind + '-' + (row.id_group || row.id_user)">
                    <td>{{ row._kind === 'group' ? '群' : '用户' }}</td>
                    <td class="mono">{{ row.id_group || row.id_user }}</td>
                    <td><span class="badge" :class="row.enabled ? 'ok' : 'danger'">{{ row.enabled ? '启用' : '停用' }}</span></td>
                    <td><div class="row"><button class="button small" @click="toggleWhitelist(row._kind, row)">{{ row.enabled ? '停用' : '启用' }}</button><button class="button small danger" @click="removeWhitelist(row._kind, row)">移除</button></div></td>
                  </tr>
                  <tr v-if="!(whitelist.groups?.length || whitelist.users?.length)"><td colspan="4" class="empty">暂无白名单</td></tr>
                </tbody>
              </table>
            </div>
            <Pager :total="whitelistRows.length" :page="pageFor('whitelist', whitelistRows.length)" @update:page="setPage('whitelist', $event)" />
          </div>
        </section>

        <section v-if="tab==='emotes'" class="panel">
          <div class="panel-head">
            <div><h3>表情包</h3><p>{{ emotes.saved || 0 }} 个已拥有 · {{ emotes.rejected || 0 }} 个拒绝学习 · {{ emotes.learnable || 0 }} 个可学习</p></div>
            <div class="row">
              <input v-model="emoteQuery" class="input search" placeholder="搜索分类 / 标签 / 原因" />
              <select v-model="emoteCategory" class="input" style="width:auto">
                <option value="">全部分类</option>
                <option v-for="cat in emotes.categories || []" :key="cat.label" :value="cat.label">{{ cat.label }} · {{ cat.count }}</option>
              </select>
              <button class="button small ghost" :disabled="emoteBusy" @click="warmEmotes">预热缓存</button>
              <button class="button small ghost" @click="loadEmotes">刷新</button>
            </div>
          </div>
          <div class="panel-body">
            <div class="emote-toolbar">
              <div class="emote-tabs">
                <button :class="{active: emoteTab === 'owned'}" @click="emoteTab = 'owned'">已拥有 <span>{{ emotes.files?.length || 0 }}</span></button>
                <button :class="{active: emoteTab === 'rejected'}" @click="emoteTab = 'rejected'">拒绝学习 <span>{{ emotes.rejected || 0 }}</span></button>
                <button :class="{active: emoteTab === 'learnable'}" @click="emoteTab = 'learnable'">可学习 <span>{{ emotes.learnable || 0 }}</span></button>
              </div>
              <button class="button primary small" @click="openEmoteUpload">上传表情</button>
            </div>

            <div v-if="emoteTab === 'owned'">
              <p class="small muted" style="margin:0 0 10px">已拥有会用于聊天；移入拒绝学习后不再主动使用。</p>
              <div class="emote-grid">
                <figure v-for="file in filteredEmotes" :key="file.path" class="emote-card">
                  <img :src="file.animated ? file.url : file.thumb" :alt="file.label || file.category" loading="lazy" @click="openEmotePreview(file, 'owned')" />
                  <figcaption @click="openEmotePreview(file, 'owned')">
                    <strong>{{ file.label || file.category || file.path }}</strong>
                    <small>{{ file.category }} · {{ Math.round((file.size || 0) / 1024) }} KB</small>
                  </figcaption>
                  <button class="emote-action danger" type="button" :disabled="emoteBusy" @click.stop="deleteEmote(file)">移入拒绝学习</button>
                </figure>
                <div v-if="!filteredEmotes.length" class="empty" style="grid-column:1/-1">没有匹配的已拥有表情。</div>
              </div>
            </div>

            <div v-else-if="emoteTab === 'rejected'">
              <p class="small muted" style="margin:0 0 10px">拒绝学习不会参与聊天；可以恢复为已拥有，或加入可学习并记录下次判断提示。</p>
              <div class="emote-grid managed">
                <figure v-for="item in rejectedManagedEmotes" :key="item.hash + (item.path || '')" class="emote-card">
                  <img v-if="item.thumb" :src="item.animated ? item.url : item.thumb" :alt="item.label" loading="lazy" @click="openEmotePreview(item, 'rejected')" />
                  <div v-else class="empty" style="height:110px;display:grid;place-items:center;font-size:10px" @click="openEmotePreview(item, 'rejected')">无图片文件</div>
                  <figcaption>
                    <strong>{{ item.label || '未命名' }}</strong>
                    <small>{{ item.reason || item.status }}</small>
                  </figcaption>
                  <div class="managed-actions">
                    <button class="button small" :disabled="emoteBusy" @click="openLearnable(item)">加入可学习</button>
                    <button class="button small danger" :disabled="emoteBusy || !item.path" @click="deleteManagedEmote(item)">永久删除</button>
                  </div>
                </figure>
                <div v-if="!rejectedManagedEmotes.length" class="empty" style="grid-column:1/-1">拒绝学习区为空</div>
              </div>
            </div>

            <div v-else>
              <p class="small muted" style="margin:0 0 10px">可学习会在相同图片再次出现时，带着人工提示重新判断；不会直接进入已拥有。</p>
              <div class="emote-grid managed">
                <figure v-for="item in learnableManagedEmotes" :key="item.hash + (item.path || '')" class="emote-card">
                  <img v-if="item.thumb" :src="item.animated ? item.url : item.thumb" :alt="item.label" loading="lazy" @click="openEmotePreview(item, 'learnable')" />
                  <div v-else class="empty" style="height:110px;display:grid;place-items:center;font-size:10px" @click="openEmotePreview(item, 'learnable')">无图片文件</div>
                  <figcaption>
                    <strong>{{ item.label || '未命名' }}</strong>
                    <small>{{ item.manual_note || item.reason || '等待再次出现' }}</small>
                  </figcaption>
                  <div class="managed-actions">
                    <button class="button small danger" :disabled="emoteBusy || !item.path" @click="deleteManagedEmote(item)">永久删除</button>
                  </div>
                </figure>
                <div v-if="!learnableManagedEmotes.length" class="empty" style="grid-column:1/-1">可学习区为空</div>
              </div>
            </div>
          </div>
        </section>

        <Teleport to="body">
        <div v-if="emotePreview" class="overlay" role="dialog" aria-modal="true" aria-label="表情预览">
          <div class="emote-preview-card">
            <div class="drawer-head">
              <div><h3>{{ emotePreview.label || '表情预览' }}</h3><p>{{ emotePreview.category || emotePreview.status || '' }}</p></div>
              <button class="close" @click="emotePreview = null">×</button>
            </div>
            <div class="drawer-body">
              <div class="emote-preview-image">
                <img v-if="emotePreview.url || emotePreview.thumb" :src="emotePreview.url || emotePreview.thumb" :alt="emotePreview.label" />
                <div v-else class="empty">无图片文件</div>
              </div>
              <div class="info-grid">
                <div><small>分类 / 状态</small><strong>{{ emotePreview.category || '—' }} · {{ emotePreview.status || emotePreview._type }}</strong></div>
                <div><small>大小</small><strong>{{ emotePreview.size ? `${Math.round(emotePreview.size / 1024)} KB` : '—' }}</strong></div>
                <div><small>路径</small><strong class="mono">{{ emotePreview.path || '—' }}</strong></div>
                <div><small>原因 / 提示</small><strong>{{ emotePreview.manual_note || emotePreview.reason || '—' }}</strong></div>
              </div>
            </div>
          </div>
        </div>

        <div v-if="learnableOpen" class="overlay" role="dialog" aria-modal="true" aria-label="加入可学习">
          <div class="emote-upload-card">
            <div class="drawer-head">
              <div><h3>加入可学习</h3><p>写一句判断提示，下次出现时会带给视觉模型</p></div>
              <button class="close" @click="closeLearnable()">×</button>
            </div>
            <div class="drawer-body">
              <textarea v-model="learnableNote" class="input" rows="4" maxlength="300" placeholder="例如：这是群友常用猫猫表情，虽然像截图但确实是表情包"></textarea>
              <div class="row" style="margin-top:14px;justify-content:flex-end">
                <button class="button secondary small" @click="closeLearnable()">取消</button>
                <button class="button primary small" :disabled="emoteBusy" @click="submitLearnable">{{ emoteBusy ? '保存中…' : '加入可学习' }}</button>
              </div>
            </div>
          </div>
        </div>

        <div v-if="emoteUploadOpen" class="overlay" role="dialog" aria-modal="true" aria-label="上传表情">
          <div class="emote-upload-card">
            <div class="drawer-head">
              <div><h3>上传表情</h3><p>PNG / JPEG / GIF / WebP，最大 6MB</p></div>
              <button class="close" @click="closeEmoteUpload()">×</button>
            </div>
            <div class="drawer-body">
              <label class="upload-zone">
                <input type="file" accept="image/png,image/jpeg,image/gif,image/webp" @change="pickEmoteFile" />
                <span v-if="!emoteUploadPreview">点击选择图片</span>
                <img v-else :src="emoteUploadPreview" alt="待上传预览" />
              </label>
              <div class="row" style="margin-top:12px">
                <select v-model="emoteUploadCategory" class="input" style="width:auto">
                  <option value="emotes">表情</option><option value="misc">杂项</option>
                </select>
                <input v-model="emoteUploadName" class="input" style="flex:1;min-width:180px" placeholder="文件名，例如 happy.png" />
              </div>
              <div class="row" style="margin-top:14px;justify-content:flex-end">
                <button class="button secondary small" @click="closeEmoteUpload()">取消</button>
                <button class="button primary small" :disabled="emoteBusy || !emoteUploadFile" @click="submitEmoteUpload">{{ emoteBusy ? '上传中…' : '上传' }}</button>
              </div>
            </div>
          </div>
        </div>

        </Teleport>

        <section v-if="tab==='sessions'" class="panel">
          <div class="panel-head"><div><h3>会话记忆</h3><p>{{ sessions.items?.length || 0 }} 个会话</p></div><button class="button small ghost" @click="loadSessions">刷新</button></div>
          <div class="panel-body">
            <div class="table-wrap" style="border:1px solid var(--line);border-radius:12px;max-height:300px">
              <table>
                <thead><tr><th>类型</th><th>会话</th><th>待处理</th><th>事件</th><th>事实</th><th>最近</th></tr></thead>
                <tbody>
                  <tr v-for="row in sessionPageRows" :key="row.kind + '-' + row.key" @click="openSession(row)" style="cursor:pointer">
                    <td>{{ row.kind === 'group' ? '群' : '用户' }}</td>
                    <td class="mono">{{ row.key }}</td>
                    <td>{{ row.pending }}</td>
                    <td>{{ row.episodes }}</td>
                    <td>{{ row.facts }}</td>
                    <td class="muted">{{ time(row.last) }}</td>
                  </tr>
                  <tr v-if="!sessions.items?.length"><td colspan="6" class="empty">暂无会话记忆</td></tr>
                </tbody>
              </table>
            </div>
            <Pager :total="sessions.items?.length || 0" :page="pageFor('sessions', sessions.items?.length || 0)" @update:page="setPage('sessions', $event)" />
            <div v-if="sessionDetail" class="session-detail">
              <div class="row" style="justify-content:space-between"><h3 style="margin:0;font-size:14px">{{ sessionDetail.kind }} / {{ sessionDetail.key }}</h3><button class="close" @click="sessionDetail = null">×</button></div>
              <p v-if="sessionDetail.summary" class="notice" style="margin:10px 0">{{ sessionDetail.summary }}</p>
              <div v-if="sessionDetail.facts?.length"><h4>长期事实</h4><p v-for="(fact, i) in sessionDetail.facts" :key="'f'+i" class="small muted">· {{ fact }}</p></div>
              <div v-if="sessionDetail.episodes?.length"><h4>近期事件</h4><p v-for="(ep, i) in sessionDetail.episodes.slice(-12)" :key="'e'+i" class="small muted">· {{ ep.text || ep.summary || ep }}</p></div>
            </div>
          </div>
        </section>

        <section v-if="tab==='system'" class="panel">
          <div class="panel-head"><div><h3>插件与系统</h3><p>{{ plugins.filter(p => p.enabled).length }} / {{ plugins.length }} 个插件启用</p></div><button class="button small ghost" @click="loadPlugins">刷新</button></div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>插件</th><th class="hide-mobile">说明</th><th>状态</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="plugin in pluginPageRows" :key="plugin.id">
                  <td><div class="name"><span class="avatar">{{ (plugin.name || plugin.id).slice(0,1) }}</span><div><div>{{ plugin.name || plugin.id }}</div><div class="mono">{{ plugin.id }}</div></div></div></td>
                  <td class="hide-mobile muted" style="max-width:440px">{{ plugin.description || '—' }}</td>
                  <td><span class="badge" :class="plugin.enabled ? 'ok' : 'danger'">{{ plugin.enabled ? '启用' : '停用' }}</span></td>
                  <td><button class="button small" :disabled="pluginBusy[plugin.id]" @click="togglePlugin(plugin)">{{ plugin.enabled ? '停用' : '启用' }}</button></td>
                </tr>
                <tr v-if="!plugins.length"><td colspan="4" class="empty">没有读取到插件</td></tr>
              </tbody>
            </table>
          </div>
          <Pager :total="plugins.length" :page="pageFor('plugins', plugins.length)" @update:page="setPage('plugins', $event)" />
          <div class="panel-body" style="border-top:1px solid var(--line)">
            <div class="row" style="justify-content:space-between;margin-bottom:8px">
              <div><h3 style="font-size:13px;margin:0">插件配置</h3><p class="small muted" style="margin:3px 0 0">按插件分区；密文配置不回传。</p></div>
              <div class="row">
                <select v-model="configPlugin" class="input" style="width:auto" @change="loadPluginConfig(configPlugin)">
                  <option v-for="plugin in plugins" :key="plugin.id" :value="plugin.id">{{ plugin.name || plugin.id }}</option>
                </select>
                <input v-model="configQuery" class="input search" placeholder="搜索 key / 标签" />
                <button class="button small" :disabled="configSaving" @click="saveConfig">{{ configSaving ? '保存中…' : '保存修改' }}</button>
              </div>
            </div>
            <div class="config-groups">
              <div v-for="group in configGroups" :key="group.name" class="config-group">
                <h4>{{ group.name }}</h4>
                <div class="table-wrap" style="border:1px solid var(--line);border-radius:12px">
                  <table>
                    <thead><tr><th>KEY</th><th>VALUE</th><th>状态</th></tr></thead>
                    <tbody>
                      <tr v-for="item in group.items" :key="item.key">
                        <td class="mono" style="max-width:280px;overflow-wrap:anywhere">{{ item.key }}<div v-if="item.label" class="small muted">{{ item.label }}</div></td>
                        <td><input v-if="!item.secret" class="input" :value="configDraft[item.key] ?? item.value" @input="configDraft[item.key] = $event.target.value" /></td>
                        <td class="muted small">{{ item.secret ? (item.configured ? '已配置（隐藏）' : '未配置') : '可编辑' }}</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
              <div v-if="!configGroups.length" class="empty">没有匹配的配置</div>
            </div>
            <Pager :total="filteredConfig.length" :page="pageFor('config', filteredConfig.length)" @update:page="setPage('config', $event)" />
          </div>
        </section>

        <section v-if="tab==='metrics'" class="panel">
          <div class="panel-head"><div><h3>运行指标</h3><p>最近 14 天</p></div><button class="button small ghost" @click="loadMetrics">刷新</button></div>
          <div class="panel-body">
            <div class="stats" style="margin-top:0">
              <div class="stat"><small>调用</small><strong>{{ metrics.llm?.calls ?? '—' }}</strong></div>
              <div class="stat"><small>成功率</small><strong>{{ metrics.llm?.success_rate != null ? Math.round(metrics.llm.success_rate * 100) + '%' : '—' }}</strong></div>
              <div class="stat"><small>P95 延迟</small><strong>{{ metrics.llm?.p95_ms ?? '—' }}ms</strong></div>
              <div class="stat"><small>Token</small><strong>{{ metrics.llm?.tokens ?? '—' }}</strong></div>
            </div>
            <h3 style="font-size:13px;margin:16px 0 8px">每日调用</h3>
            <div class="table-wrap" style="border:1px solid var(--line);border-radius:12px">
              <table>
                <thead><tr><th>日期</th><th>调用</th><th>失败</th><th>Token</th><th>平均延迟</th></tr></thead>
                <tbody>
                  <tr v-for="day in metrics.days || []" :key="day.date">
                    <td>{{ day.date }}</td><td>{{ day.calls }}</td><td>{{ day.fail }}</td><td>{{ day.tokens }}</td><td>{{ day.ms_avg }}ms</td>
                  </tr>
                  <tr v-if="!metrics.days?.length"><td colspan="5" class="empty">暂无指标</td></tr>
                </tbody>
              </table>
            </div>
            <h3 style="font-size:13px;margin:16px 0 8px">Token 使用排行</h3>
            <div class="table-wrap" style="border:1px solid var(--line);border-radius:12px">
              <table>
                <thead><tr><th>用户</th><th>调用</th><th>总 Token</th></tr></thead>
                <tbody>
                  <tr v-for="row in metricsTokenPageRows" :key="row.qq">
                    <td>{{ row.nickname || row.qq }}<div class="mono">{{ row.qq }}</div></td>
                    <td>{{ row.calls }}</td><td>{{ row.total_tokens }}</td>
                  </tr>
                  <tr v-if="!chatStats.tokens?.length"><td colspan="3" class="empty">暂无 Token 数据</td></tr>
                </tbody>
              </table>
            </div>
            <Pager :total="chatStats.tokens?.length || 0" :page="pageFor('metricsTokens', chatStats.tokens?.length || 0)" @update:page="setPage('metricsTokens', $event)" />
          </div>
        </section>

        <section v-if="tab==='favor'" class="panel">
          <div class="panel-head">
            <div><h3>好感度</h3><p>{{ filteredFavorUsers.length }} 位用户</p></div>
            <div class="row">
              <input v-model="favorQuery" class="input search" placeholder="搜索昵称 / QQ / 备注" />
              <select v-model="favorSort" class="input" style="width:auto"><option value="stable">保持当前顺序</option><option value="favor">按好感度</option><option value="interactions">按互动</option></select>
              <button class="button small ghost" @click="loadFavor">刷新</button>
            </div>
          </div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>用户</th><th>好感度</th><th>互动</th><th class="hide-mobile">标签 / 备注</th><th class="hide-mobile">最近活跃</th><th>操作</th></tr></thead>
              <tbody>
                <tr v-for="row in favorPageRows" :key="row.qq">
                  <td><div class="name"><span class="avatar">{{ (row.nickname || row.qq || '?').slice(0,1) }}</span><div><div>{{ row.nickname || row.qq }}</div><div class="mono">{{ row.qq }}</div></div></div></td>
                  <td><input v-model.number="favorDrafts[row.qq]" class="input" style="width:100px" :placeholder="String(row.favorability ?? 0)" /></td>
                  <td>{{ row.interactions ?? 0 }}</td>
                  <td class="hide-mobile muted">{{ (row.tags || []).join('、') || row.notes || '—' }}</td>
                  <td class="hide-mobile muted">{{ time(row.last_active) }}</td>
                  <td><button class="button small" :disabled="favorBusy[row.qq]" @click="saveFavor(row)">{{ favorBusy[row.qq] ? '保存中…' : '保存' }}</button></td>
                </tr>
                <tr v-if="!filteredFavorUsers.length"><td colspan="6" class="empty">没有匹配的用户</td></tr>
              </tbody>
            </table>
          </div>
          <Pager :total="filteredFavorUsers.length" :page="pageFor('favor', filteredFavorUsers.length)" @update:page="setPage('favor', $event)" />
        </section>

        <section v-if="tab==='ledger'" class="panel">
          <div class="panel-head">
            <div><h3>扣分记录</h3><p>{{ ledgerGroups.length }} 段对话 · 已按上下文范围合并</p></div>
            <div class="row">
              <div class="status-tabs">
                <button :class="{active: ledgerStatus === 'pending'}" @click="setLedgerStatus('pending')">待处理 <span>{{ ledgerStatusCounts.pending }}</span></button>
                <button :class="{active: ledgerStatus === 'rejected'}" @click="setLedgerStatus('rejected')">认定异常 <span>{{ ledgerStatusCounts.rejected }}<template v-if="ledgerStatusCounts.typeRejected"> · 类型 {{ ledgerStatusCounts.typeRejected }}</template></span></button>
                <button :class="{active: ledgerStatus === 'accepted'}" @click="setLedgerStatus('accepted')">申诉成功 <span>{{ ledgerStatusCounts.accepted }}</span></button>
              </div>
              <input v-model="search" class="input search" placeholder="搜索昵称 / QQ / 原因" />
              <button class="button small" @click="backfill">回填旧原文</button>
              <button class="button small ghost" @click="loadLedger">刷新</button>
            </div>
          </div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>用户</th><th>扣分</th><th>原因</th><th>最终认定</th><th class="hide-mobile">来源</th><th class="hide-mobile">时间</th><th>原文</th></tr></thead>
              <tbody>
                <tr v-for="group in ledgerPageRows" :key="group.key" @click="openGroup(group)">
                  <td><div class="name"><span class="avatar">{{ (group.nickname || group.qq || '?').slice(0,1) }}</span><div><div>{{ group.nickname || group.qq }}<span v-if="group.count > 1" class="badge warn" style="margin-left:6px">合并 {{ group.count }} 条</span></div><div class="mono">{{ group.qq }} · {{ group.group || '未知群' }}</div></div></div></td>
                  <td style="color:var(--danger);font-weight:700">{{ money(group.penalty) }}</td>
                  <td>{{ group.reasons.join('、') }}</td>
                  <td><span v-for="reason in group.finalReasons" :key="reason" class="badge" :class="reason === '申诉成功' ? 'ok' : reason === '待处理' ? 'warn' : 'danger'" style="margin-right:4px">{{ reason }}</span></td>
                  <td class="hide-mobile"><span class="badge">{{ group.source || 'message' }}</span></td>
                  <td class="hide-mobile muted">{{ time(group.time) }}</td>
                  <td><span class="badge" :class="group.has_text ? 'ok' : 'warn'">{{ group.has_text ? '有原文' : '可回填/缺失' }}</span></td>
                </tr>
                <tr v-if="!ledgerGroups.length"><td colspan="7" class="empty">没有匹配的记录</td></tr>
              </tbody>
            </table>
          </div>
          <Pager :total="ledgerGroups.length" :page="pageFor('ledger', ledgerGroups.length)" @update:page="setPage('ledger', $event)" />
        </section>

        <section v-else-if="tab==='slang'" class="panel">
          <div class="panel-head">
            <div><h3>玩梗接梗</h3><p>语义判断工作台：不是关键词命中。先看分类和效果，再纠正错判；纠正过的梗会写回长期语义记忆。总开关：{{ slang.enabled === false ? '已关闭' : '已开启' }}。</p></div>
            <div class="row">
              <button class="button small ghost" @click="loadSlang">刷新</button>
              <button class="button small" @click="learn('group')">学习群聊</button>
              <button class="button small ghost" @click="pruneSlang">清理冷门</button>
              <button class="button small danger" @click="clearSlang">清空长期记忆</button>
              <button class="button small" :class="slang.enabled === false ? '' : 'ghost'" @click="toggleSlang">{{ slang.enabled === false ? '开启玩梗' : '关闭玩梗' }}</button>
            </div>
          </div>
          <div class="panel-body">
            <div class="row" style="margin-bottom:12px">
              <input v-model="testText" class="input" style="max-width:380px" placeholder="输入一句真实对话，做语义判断（不是命中测试）" @keyup.enter="testSlang" />
              <button class="button small" @click="testSlang">语义分析</button>
              <span v-if="testResult" class="small muted">{{ testResult.enabled ? '已启用' : '已关闭' }}</span>
            </div>
            <div v-if="testResult?.analysis" class="notice" style="margin-bottom:12px">
              <strong>{{ memeModeLabel(testResult.analysis.mode) }} · {{ memeVariantLabel(testResult.analysis.variant_type) }}</strong>
              · {{ memeSpeechLabel(testResult.analysis.speech_act) }}
              · 置信 {{ Math.round((testResult.analysis.confidence || 0) * 100) }}%
              · 策略 {{ memeStrategyLabel(testResult.analysis.reply_strategy) }}
              <span v-if="testResult.analysis.recognized_term"> · 识别为「{{ testResult.analysis.recognized_term }}」</span>
              <span v-if="testResult.analysis.meaning"> · {{ testResult.analysis.meaning }}</span>
              <div class="mono small" style="margin-top:8px;white-space:pre-wrap">{{ testResult.prompt }}</div>
            </div>

            <div class="section-head" style="margin:18px 0 8px">
              <div><h3>近期判断与效果复核</h3><p>{{ memeFilteredObservations.length }} 条 · 先处理误接/接错/漏接，再确认接住</p></div>
              <div class="row">
                <select v-model="memeModeFilter" class="input" style="width:auto">
                  <option value="">全部分类</option>
                  <option v-for="(label, value) in memeModeLabels" :key="value" :value="value">{{ label }}</option>
                </select>
                <select v-model="memeVariantFilter" class="input" style="width:auto">
                  <option value="">全部变体</option>
                  <option v-for="(label, value) in memeVariantLabels" :key="value" :value="value">{{ label }}</option>
                </select>
                <input v-model="search" class="input search" placeholder="搜索原话 / 梗 / 回复 / 用户" />
              </div>
            </div>
            <div class="status-tabs" style="margin-bottom:10px">
              <button :class="{active: memeOutcomeFilter === 'all'}" @click="memeOutcomeFilter = 'all'">全部 <span>{{ memeOutcomeCounts.all }}</span></button>
              <button :class="{active: memeOutcomeFilter === 'review'}" @click="memeOutcomeFilter = 'review'">待复核 <span>{{ memeOutcomeCounts.review }}</span></button>
              <button :class="{active: memeOutcomeFilter === 'bad'}" @click="memeOutcomeFilter = 'bad'">误接/接错 <span>{{ memeOutcomeCounts.bad }}</span></button>
              <button :class="{active: memeOutcomeFilter === 'success'}" @click="memeOutcomeFilter = 'success'">接住 <span>{{ memeOutcomeCounts.success }}</span></button>
              <button :class="{active: memeOutcomeFilter === 'literal'}" @click="memeOutcomeFilter = 'literal'">字面正常 <span>{{ memeOutcomeCounts.literal }}</span></button>
            </div>
            <div class="row small" style="margin:0 0 10px;color:var(--muted)">
              <span>分类分布</span>
              <span class="badge" v-for="(label, value) in memeModeLabels" :key="value">{{ label }} {{ (slangStats.by_mode || {})[value] || 0 }}</span>
              <span class="badge">联网 {{ slangStats.web_used || 0 }}</span>
              <span class="badge">歌词/引用 {{ slangStats.lyric || 0 }}</span>
            </div>
            <div class="table-wrap">
              <table>
                <thead><tr><th>时间</th><th>对象</th><th>原话</th><th>语义判断</th><th>接法</th><th>回复</th><th>效果</th><th>状态</th><th>操作</th></tr></thead>
                <tbody>
                  <tr v-for="item in memePageRows" :key="item.id" @click="openMeme(item)">
                    <td class="muted">{{ time(item.time) }}</td>
                    <td><div class="mono">{{ item.qq || '—' }}</div><small class="muted">{{ item.group || '私聊' }}</small></td>
                    <td><div class="meme-message">{{ item.message || '（空消息）' }}</div><small v-if="item.quote" class="muted">引用：{{ item.quote.slice(0, 42) }}…</small></td>
                    <td><span class="badge" :class="item.mode === 'banter' ? 'ok' : item.mode === 'uncertain' ? 'warn' : ''">{{ memeModeLabel(item.mode) }}</span><span class="badge" style="margin-left:4px">{{ memeVariantLabel(item.variant_type) }}</span><div v-if="item.recognized_term" class="small" style="margin-top:4px">「{{ item.recognized_term }}」</div><small v-if="item.confidence" class="muted">{{ Math.round(item.confidence * 100) }}%</small></td>
                    <td>{{ memeStrategyLabel(item.reply_strategy) }}</td>
                    <td><div class="meme-reply">{{ item.reply || '—' }}</div></td>
                    <td><span class="badge" :class="memeOutcomeTone(item.outcome)">{{ memeOutcomeLabel(item.outcome) }}</span><div v-if="item.outcome_reason" class="small muted" style="margin-top:4px">{{ item.outcome_reason.slice(0, 36) }}</div></td>
                    <td><span class="badge" :class="memeReviewTone(item.review_status)">{{ memeReviewLabel(item.review_status) }}</span><div v-if="item.web_used" class="small muted" style="margin-top:4px">联网</div></td>
                    <td><button class="button small ghost" @click.stop="openMeme(item)">复核</button></td>
                  </tr>
                  <tr v-if="!memePageRows.length"><td colspan="9" class="empty">没有匹配的判断记录</td></tr>
                </tbody>
              </table>
            </div>
            <Pager :total="memeFilteredObservations.length" :page="pageFor('meme', memeFilteredObservations.length)" @update:page="setPage('meme', $event)" />

            <div class="section-head" style="margin:22px 0 8px">
              <div><h3>长期语义记忆</h3><p>这里是参考目录，不是命中表；只把确认过的释义、用法和例句交给分析模型。</p></div>
              <div class="row">
                <select v-model="statusFilter" class="input" style="width:auto">
                  <option value="">全部词条状态</option><option value="active">可用</option><option value="candidate">候选</option><option value="blocked">屏蔽</option>
                </select>
              </div>
            </div>
            <div class="table-wrap">
              <table>
                <thead><tr><th>词条</th><th>状态</th><th>释义 / 用法</th><th>范围</th><th>置信 / 热度</th><th>反馈</th><th>操作</th></tr></thead>
                <tbody>
                  <tr v-for="entry in slangPageRows" :key="entry.term">
                    <td><strong>{{ entry.term }}</strong><div class="small muted">{{ entry.kind || 'internet' }}</div></td>
                    <td><span class="badge" :class="entry.status==='active' ? 'ok' : entry.status==='blocked' ? 'danger' : 'warn'">{{ entry.status === 'active' ? '可用' : entry.status === 'blocked' ? '屏蔽' : '候选' }}</span></td>
                    <td><div>{{ entry.meaning || '尚未整理释义' }}</div><small v-if="entry.usage" class="muted">{{ entry.usage }}</small></td>
                    <td><span class="badge">{{ entry.scope === 'global' ? '通用' : '群内' }}</span></td>
                    <td><div class="small">{{ Math.round((entry.confidence || 0) * 100) }}% / {{ Math.round((entry.popularity || 0) * 100) }}%</div><small class="muted">出现 {{ entry.count || 0 }} 次</small></td>
                    <td><div class="small"><span class="badge ok">接住 {{ entry.feedback_success || 0 }}</span> <span class="badge danger">错 {{ entry.feedback_wrong || 0 }}</span></div></td>
                    <td><div class="row"><button v-if="entry.status!=='active'" class="button small" @click="setSlang(entry, 'active')">启用</button><button v-if="entry.status!=='blocked'" class="button small danger" @click="setSlang(entry, 'blocked')">屏蔽</button><button class="button small ghost" @click="removeSlang(entry)">删除</button></div></td>
                  </tr>
                  <tr v-if="!slangPageRows.length"><td colspan="7" class="empty">没有匹配的词条</td></tr>
                </tbody>
              </table>
            </div>
            <Pager :total="filteredSlang.length" :page="pageFor('slangEntries', filteredSlang.length)" @update:page="setPage('slangEntries', $event)" />
          </div>
        </section>
        </div>

      </main>
    </div>
  </div>

  <div v-if="memeDetailOpen" class="overlay" role="dialog" aria-modal="true" aria-label="玩梗复核">
    <article class="drawer">
      <div class="drawer-head">
        <div><h3>玩梗复核 · {{ memeDetail?.qq || '' }}</h3><p>{{ time(memeDetail?.time) }} · {{ memeDetail?.group || '私聊' }} · {{ memeModeLabel(memeDetail?.mode) }} / {{ memeVariantLabel(memeDetail?.variant_type) }}</p></div>
        <button class="close" @click="closeMeme()">×</button>
      </div>
      <div class="drawer-body" v-if="memeDetail">
        <div v-if="memeDraftRestored" class="notice" style="margin-bottom:12px">
          <strong>已恢复上次未保存的草稿。</strong>
          <button class="button small ghost" type="button" style="margin-left:8px" @click="discardMemeDraft">丢弃草稿</button>
        </div>
        <div v-if="memeSaveError" class="notice danger" style="margin-bottom:12px"><strong>保存失败：</strong>{{ memeSaveError }}</div>
        <div class="detail-stats">
          <div class="detail-stat"><small>言语行为</small><strong>{{ memeSpeechLabel(memeDetail.speech_act) }}</strong></div>
          <div class="detail-stat"><small>自动结论</small><strong>{{ memeOutcomeLabel(memeDetail.outcome) }}</strong></div>
          <div class="detail-stat"><small>置信度</small><strong>{{ Math.round((memeDetail.confidence || 0) * 100) }}%</strong></div>
          <div class="detail-stat"><small>联网</small><strong>{{ memeDetail.web_used ? '已使用' : '未使用' }}</strong></div>
        </div>
        <div class="notice" style="margin-bottom:12px">
          <strong>{{ memeDetail.message || '（空消息）' }}</strong>
          <div v-if="memeDetail.quote" class="small" style="margin-top:6px">引用：{{ memeDetail.quote }}</div>
        </div>
        <div v-if="memeDetail.context?.length" style="margin-bottom:14px">
          <h4 style="margin:0 0 6px">对话上下文</h4>
          <div v-for="(turn, index) in memeDetail.context" :key="index" class="small muted" style="padding:6px 0;border-bottom:1px solid var(--line)">
            <span class="mono">{{ turn.role }} {{ turn.speaker }}</span> {{ turn.text }}
          </div>
        </div>
        <h4 style="margin:14px 0 6px">语义判断</h4>
        <div class="row" style="margin-bottom:8px">
          <label class="small muted">分类
            <select v-model="memeDraft.mode" class="input" style="width:auto">
              <option v-for="(label, value) in memeModeLabels" :key="value" :value="value">{{ label }}</option>
            </select>
          </label>
          <label class="small muted">变体
            <select v-model="memeDraft.variant_type" class="input" style="width:auto">
              <option v-for="(label, value) in memeVariantLabels" :key="value" :value="value">{{ label }}</option>
            </select>
          </label>
          <label class="small muted">言语行为
            <select v-model="memeDraft.speech_act" class="input" style="width:auto">
              <option v-for="(label, value) in memeSpeechLabels" :key="value" :value="value">{{ label }}</option>
            </select>
          </label>
          <label class="small muted">接法
            <select v-model="memeDraft.reply_strategy" class="input" style="width:auto">
              <option v-for="(label, value) in memeStrategyLabels" :key="value" :value="value">{{ label }}</option>
            </select>
          </label>
        </div>
        <div class="row" style="margin-bottom:8px">
          <input v-model="memeDraft.recognized_term" class="input" placeholder="识别到的梗/歌名/作品名" />
        </div>
        <textarea v-model="memeDraft.meaning" class="input" rows="2" placeholder="当前语境下的含义" style="resize:vertical"></textarea>
        <div class="notice" style="margin:12px 0">
          <strong>实际回复：</strong>{{ memeDetail.reply || '—' }}
          <div v-if="memeDetail.outcome_reason" class="small" style="margin-top:6px">自动评估依据：{{ memeDetail.outcome_reason }}</div>
          <div v-if="memeDetail.web_query" class="small" style="margin-top:6px">检索词：{{ memeDetail.web_query }}</div>
          <div v-if="memeDetail.web_sources?.length" class="small" style="margin-top:6px">联网来源：{{ memeDetail.web_sources.slice(0, 2).join(' · ') }}</div>
          <div v-if="memeDetail.review_note" class="small" style="margin-top:6px">上次复核备注：{{ memeDetail.review_note }}</div>
        </div>
        <h4 style="margin:14px 0 6px">效果与学习</h4>
        <div class="row" style="margin-bottom:8px">
          <label class="small muted">最终效果
            <select v-model="memeDraft.outcome" class="input" style="width:auto">
              <option value="">保持自动结论</option>
              <option v-for="(label, value) in memeOutcomeLabels" :key="value" :value="value">{{ label }}</option>
            </select>
          </label>
        </div>
        <p class="small muted" style="margin:0 0 10px">只需选择实际发生的结果；不确定就保持“保持自动结论”。保存后会按这个结果调整长期记忆。</p>
        <div class="row" style="margin-bottom:8px">
          <input v-model="memeDraft.corrected_term" class="input" placeholder="正确梗/作品名（接错时填写）" />
          <input v-model="memeDraft.corrected_usage" class="input" placeholder="适用语境（可选）" />
        </div>
        <textarea v-model="memeDraft.corrected_meaning" class="input" rows="2" placeholder="正确释义（接错/漏接时填写，会写入长期语义记忆）" style="resize:vertical"></textarea>
        <textarea v-model="memeDraft.note" class="input" rows="2" placeholder="复核备注（可留空）" style="resize:vertical;margin-top:8px"></textarea>
        <div class="row drawer-actions" style="margin-top:12px">
          <button class="button small primary" @click="saveMeme()" :disabled="memeBusy">{{ memeBusy ? '保存中…' : '保存复核' }}</button>
          <span v-if="memeDirty" class="badge warn">未保存</span><span v-else class="badge ok">已同步</span>
          <span class="grow"></span>
          <button class="button small danger" @click="deleteMeme" :disabled="memeBusy">删除记录</button>
          <button class="button small ghost" @click="closeMeme()">关闭</button>
        </div>
      </div>
    </article>
  </div>

  <div v-if="detailOpen" class="overlay" role="dialog" aria-modal="true" aria-label="扣分详情">
    <article class="drawer">
      <div class="drawer-head">
        <div><h3>扣分详情 · {{ detail?.entry?.nickname || detail?.entry?.qq || '' }}</h3><p>{{ detail?.entry?.reason || '' }} · {{ time(detail?.entry?.time) }} · 合并 {{ detail?.entry?.count || 0 }} 次触发</p></div>
        <button class="close" @click="detailOpen=false">×</button>
      </div>
      <div class="drawer-body">
        <div v-if="detailLoading" class="empty">正在读取…</div>
        <template v-else-if="detail">
          <div class="detail-stats">
            <div class="detail-stat"><small>扣分</small><strong style="color:var(--danger)">{{ money(detail.entry?.penalty) }}</strong></div>
            <div class="detail-stat"><small>好感变化</small><strong>{{ detail.entry?.favor_before ?? '—' }} <span class="muted">→</span> {{ detail.entry?.favor_after ?? '—' }}</strong></div>
            <div class="detail-stat"><small>互动</small><strong>{{ detail.entry?.relation?.interactions ?? '—' }}</strong></div>
            <div class="detail-stat"><small>关系阶段</small><strong>{{ detail.entry?.relation_stage || '—' }}</strong></div>
            <div class="detail-stat"><small>申诉</small><strong>{{ detail.entry?.appeal === 'accepted' ? '申诉成功' : detail.entry?.appeal === 'rejected' ? '认定异常' : detail.entry?.appeal === 'pending' ? '待处理' : '待处理' }}</strong></div>
            <div class="detail-stat"><small>误判统计</small><strong>{{ detail.feedback?.accepted || 0 }}/{{ (detail.feedback?.accepted || 0) + (detail.feedback?.rejected || 0) }}</strong></div>
          </div>
          <div class="info-grid">
            <div><small>判定原因</small><strong>{{ detail.entry?.reason || '未注明' }}</strong></div>
            <div><small>来源 / 群</small><strong>{{ detail.entry?.source || 'message' }} · {{ detail.entry?.group || '未记录群号' }}</strong></div>
            <div><small>时间</small><strong>{{ time(detail.entry?.time) }}</strong></div>
            <div><small>记录 ID</small><strong class="mono">{{ detail.entry?.id }}</strong></div>
          </div>
          <div class="notice" style="margin:10px 0">规则解释：{{ detail.entry?.reason_explanation || '暂无说明' }}</div>
          <template v-if="detail.entry?.triggers?.length">
            <h3 style="font-size:13px;margin:14px 0 7px">触发记录（{{ detail.entry.triggers.length }}）</h3>
            <div class="trigger-tabs">
              <button v-for="(trigger, index) in detail.entry.triggers" :key="'tab'+index"
                      class="trigger-tab" :class="{active: triggerIndex === index}"
                      @click="selectTrigger(index)">
                <span>#{{ index + 1 }}</span>
                <strong>{{ money(trigger.penalty) }}</strong>
                <small>{{ trigger.reason || '未注明' }}</small>
              </button>
            </div>
            <div v-if="triggerEvidence" class="evidence-card">
              <div class="evidence-head">
                <div>
                  <strong>触发 #{{ triggerIndex + 1 }} · {{ triggerEvidence.reason || '未注明' }}</strong>
                  <div class="small muted">{{ time(triggerEvidence.time) }} · 记录 #{{ triggerEvidence.record_index }} · {{ triggerEvidence.text_hash || '无哈希' }}</div>
                </div>
                <div class="row">
                  <button class="button small ghost" :disabled="triggerIndex <= 0" @click="triggerIndex--">上一条</button>
                  <button class="button small ghost" :disabled="triggerIndex >= detail.entry.triggers.length - 1" @click="triggerIndex++">下一条</button>
                </div>
              </div>
              <div class="evidence-body">
                <blockquote v-if="triggerEvidence.text" class="quote">{{ triggerEvidence.text }}</blockquote>
                <p v-else class="muted small">该条触发没有可恢复的原文。</p>
                <div class="evidence-grid">
                  <div class="evidence-block">
                    <h4>判定阈值（当时）</h4>
                    <div class="info-grid">
                      <div><small>熟人好感阈值</small><strong>{{ triggerEvidence.threshold?.close_favor ?? '—' }}</strong></div>
                      <div><small>高互动熟人阈值</small><strong>{{ triggerEvidence.threshold?.close_favor_with_turns ?? '—' }}</strong></div>
                      <div><small>熟人互动阈值</small><strong>{{ triggerEvidence.threshold?.close_interactions ?? '—' }}</strong></div>
                      <div><small>当时好感 / 互动</small><strong>{{ triggerEvidence.threshold?.actual_favor ?? '—' }} / {{ triggerEvidence.threshold?.actual_interactions ?? '—' }}</strong></div>
                    </div>
                    <p class="small muted" style="margin:8px 0 0">好感：{{ triggerEvidence.favor_before ?? '—' }} → {{ triggerEvidence.favor_after ?? '—' }} · 关系：{{ triggerEvidence.relation_stage || '—' }}<span v-if="triggerEvidence.target_factor < 1"> · 非直接指向，扣分按 {{ Math.round(triggerEvidence.target_factor * 100) }}% 折算</span></p>
                  </div>
                  <div class="evidence-block">
                    <h4>原文存档邻接内容</h4>
                    <template v-if="triggerEvidence.archive_context">
                      <p class="small muted">来自 {{ triggerEvidence.archive_context.file }}；只有文本顺序。</p>
                      <div v-for="(line, i) in triggerEvidence.archive_context.before" :key="'eb'+i" class="quote compact">
                        <strong style="font-size:10px;color:var(--muted)">前 {{ triggerEvidence.archive_context.before.length - i }}</strong>
                        <div>{{ line }}</div>
                      </div>
                      <div class="quote compact" style="border-left-color:var(--danger)">
                        <strong style="font-size:10px;color:var(--danger)">这 条 触 发</strong>
                        <div>{{ triggerEvidence.text }}</div>
                      </div>
                      <div v-for="(line, i) in triggerEvidence.archive_context.after" :key="'ea'+i" class="quote compact">
                        <strong style="font-size:10px;color:var(--muted)">后 {{ i + 1 }}</strong>
                        <div>{{ line }}</div>
                      </div>
                    </template>
                    <p v-else class="muted small">该条没有可用的原文存档邻接内容。</p>
                  </div>
                </div>
                <div class="appeal-box">
                  <div class="row" style="justify-content:space-between">
                    <h4 style="margin:0">扣分申诉</h4>
                    <span class="badge" :class="triggerEvidence.appeal === 'accepted' ? 'ok' : triggerEvidence.appeal === 'pending' ? 'warn' : triggerEvidence.appeal === 'rejected' ? 'danger' : ''">
                      {{ triggerEvidence.appeal === 'accepted' ? '申诉成功' : triggerEvidence.appeal === 'pending' ? '待处理' : triggerEvidence.appeal === 'rejected' ? (triggerEvidence.anomaly_kind === 'type' ? '类型异常' : '认定异常') : '待处理' }}
                    </span>
                  </div>
                  <p v-if="triggerEvidence.appeal_note" class="small muted" style="margin:6px 0">申诉说明：{{ triggerEvidence.appeal_note }}</p>
                  <template v-if="(triggerEvidence.appeal === 'pending' || triggerEvidence.appeal === 'none') && user.role === 'owner'">
                    <div class="row">
                      <button class="button small" :disabled="appealBusy" @click="resolveAppeal(true)">申诉成功</button>
                      <button class="button small ghost" :disabled="appealBusy" @click="resolveAppeal(false)">认定异常</button>
                    </div>
                    <div class="row" style="margin-top:7px">
                      <div class="type-picker">
  <select v-model="typePreset" class="input type-preset" @change="applyTypePreset">
    <option value="">选择预设类型…</option>
    <option v-for="name in anomalyTypes" :key="name" :value="name">{{ name }}</option>
  </select>
  <input v-model="typeReason" class="input" maxlength="80" placeholder="或手动输入类型" />
</div>
                      <button class="button small" :disabled="appealBusy || !typeReason.trim()" @click="resolveAppeal(false, 'type', typeReason)">类型异常</button>
                    </div>
                    <p class="small muted" style="margin:6px 0 0">认定异常会作为正例学习；类型异常额外记录正确类型，避免下次按错误类型判断。</p>
                  </template>
                  <template v-else-if="triggerEvidence.appeal === 'pending'">
                    <p class="small muted" style="margin:6px 0 0">已提交申诉，等待所有者处理。</p>
                  </template>
                  <template v-else-if="triggerEvidence.appeal === 'accepted'">
                    <p class="small" style="color:var(--ok);margin:6px 0 0">该次扣分已恢复，并会作为误判样本，跳过后续相似表达的自动扣分。</p>
                  </template>
                  <template v-else-if="triggerEvidence.appeal === 'rejected'">
                    <p class="small muted" style="margin:6px 0 0">
                      已认定为{{ triggerEvidence.anomaly_kind === 'type' ? '类型异常' : '异常' }}并作为正例学习，不会被误判样本跳过。
                      <span v-if="triggerEvidence.corrected_reason">正确类型：{{ triggerEvidence.corrected_reason }}</span>
                    </p>
                    <div v-if="user.role === 'owner'" class="row" style="margin-top:7px">
                      <div class="type-picker">
  <select v-model="typePreset" class="input type-preset" @change="applyTypePreset">
    <option value="">选择预设类型…</option>
    <option v-for="name in anomalyTypes" :key="name" :value="name">{{ name }}</option>
  </select>
  <input v-model="typeReason" class="input" maxlength="80" placeholder="或手动输入类型" />
</div>
                      <button class="button small" :disabled="appealBusy" @click="updateAnomaly('type')">保存类型</button>
                      <button class="button small ghost" :disabled="appealBusy" @click="updateAnomaly('normal')">改为普通异常</button>
                    </div>
                    <div class="row" style="margin-top:7px">
                      <input v-model="appealNote" class="input" style="max-width:360px" maxlength="300" placeholder="填写申诉理由（可留空）" />
                      <button class="button small" :disabled="appealBusy" @click="submitAppeal">再次申诉</button>
                    </div>
                  </template>
                  <template v-else>
                    <div class="row">
                      <input v-model="appealNote" class="input" style="max-width:360px" maxlength="300" placeholder="填写申诉理由（可留空）" />
                      <button class="button small" :disabled="appealBusy" @click="submitAppeal">提交申诉</button>
                    </div>
                  </template>
                </div>
              </div>
            </div>
          </template>
          <details v-if="detail.entry?.context?.length" class="trigger-evidence" style="margin-top:12px">
            <summary><span>合并后的对话上下文（{{ detail.entry.context.length }} 条）</span></summary>
            <div style="padding:0 12px 12px">
              <div v-for="(turn, index) in detail.entry.context" :key="'ctx'+index" class="quote">
                <strong style="font-size:11px;color:var(--accent)">{{ turn.role === 'assistant' ? 'Yuna' : (turn.name || turn.qq || '群友') }}</strong>
                <span v-if="turn.recalled" class="badge warn" style="margin-left:6px">已撤回</span>
                <div>{{ turn.content }}</div>
              </div>
            </div>
          </details>
        </template>
      </div>
    </article>
  </div>

  <div v-if="toastText" class="toast">{{ toastText }}</div>
</template>
