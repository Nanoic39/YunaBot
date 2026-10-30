import { ref, reactive, onUnmounted } from 'vue';
export const stamp = value => !value ? '—' : new Date(typeof value === 'number' ? value * 1000 : value).toLocaleString('zh-CN', {hour12:false});
export const errorMessage = e => /^\s*</.test(e.message || '') ? '服务暂时不可用，请稍后重试' : String(e.message || '操作失败').slice(0, 300);
export const labels = {none:'未申诉',pending:'申诉中',accepted:'已恢复',rejected:'已驳回'};
// Existing records retain identity and order. New records append until an explicit sort.
export function reconcile(target, incoming, key = 'id') {
  const next = new Map(incoming.map(row => [String(row[key]), row]));
  const rows = target.filter(row => next.has(String(row[key]))).map(row => {
    Object.assign(row, next.get(String(row[key]))); next.delete(String(row[key])); return row;
  });
  rows.push(...next.values()); target.splice(0, target.length, ...rows);
}
export function usePage(ctx) {
  const loading = ref(false), error = ref(''), updated = ref(''), pending = reactive(new Set());
  let alive = true, revision = 0;
  onUnmounted(() => { alive = false; revision++; });
  async function load(fetch, apply) {
    const id = ++revision; loading.value = true;
    try {
      const value = await fetch();
      if (!alive || id !== revision) return;
      apply(value); error.value = ''; updated.value = new Date().toLocaleTimeString('zh-CN', {hour12:false});
    } catch (e) { if (alive && id === revision) { error.value = errorMessage(e); ctx.toast(error.value, 'error'); } }
    finally { if (alive && id === revision) loading.value = false; }
  }
  async function run(key, fn, message = '已保存') {
    if (pending.has(key)) return false;
    pending.add(key);
    try { await fn(); if (alive && message) ctx.toast(message, 'ok'); return true; }
    catch (e) { if (alive) ctx.toast(errorMessage(e), 'error'); return false; }
    finally { pending.delete(key); }
  }
  return {loading,error,updated,pending,load,run};
}
