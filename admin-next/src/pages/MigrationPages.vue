<script setup>
import { ref, reactive, computed, onMounted, onUnmounted, watch, nextTick } from 'vue';
import Pager from '../Pager.vue';
import { usePage, reconcile, stamp, errorMessage } from './resources.js';
const props=defineProps(['kind','request','notify','user','viewState']);
const controller=new AbortController();
const api=(path,options={})=>props.request(path,{...options,signal:AbortSignal.any([controller.signal,AbortSignal.timeout(options.timeout||25000)]),body:options.body===undefined?undefined:JSON.stringify(options.body)});
const ui=usePage({toast:props.notify});
const data=ref({}), rows=reactive([]), nodes=reactive([]), query=ref(props.viewState.query||''), filter=ref(props.viewState.filter||'all'), page=ref(props.viewState.page||0);
watch(query,v=>{props.viewState.query=v;page.value=0;});watch(filter,v=>{props.viewState.filter=v;page.value=0;});watch(page,v=>{props.viewState.page=v;});
const group=ref(props.viewState.group||''), resource=ref(props.viewState.resource||'notices'), resourceRows=reactive([]), resourceMeta=ref(null), resourcePage=ref(0), resourceBusy=ref(false), resourceError=ref('');
const detail=ref(null), detailTitle=ref(''), dialog=ref(null), detailBusy=ref(false), detailError=ref('');let detailVersion=0, resourceVersion=0;
const titles={jobs:'定时任务',qq:'QQ 连接与群资源','agent-apps':'APP 使用','agent-payments':'支付管理'};
const states={queued:'排队',running:'执行中',succeeded:'完成',failed:'失败',pending:'待审批',done:'完成',waiting_aid:'等待协助',interrupted:'中断',authorized:'已授权',simulated:'模拟完成',cancelled:'取消',expired:'超时'};
const money=v=>`¥${(Number(v||0)/100).toFixed(2)}`;
const owned=computed(()=>props.user?.role==='owner'||props.kind==='jobs');
const policy=reactive({single:20,daily:100,monthly:1500,automatic:100});let policyLoaded=false;
const policyDirty=ref(false);
const paymentType=ref(props.viewState.paymentType||'records');watch(paymentType,v=>{props.viewState.paymentType=v;page.value=0;});
function host(url){try{return new URL(url).hostname;}catch{return '未知平台';}}
async function reload(){
 if(!owned.value)return;
 const endpoint={jobs:'jobs',qq:'qq/bridge','agent-apps':'agent/apps','agent-payments':'agent/payments'}[props.kind];
 await ui.load(()=>api(endpoint),d=>{
  data.value=d;
  if(props.kind==='jobs')reconcile(rows,d.items||[]);
  if(props.kind==='qq'){
   reconcile(rows,d.groups||[]);if(!group.value||!rows.some(g=>g.id===group.value))group.value=rows[0]?.id||'';
  }
  if(props.kind==='agent-apps'){
   reconcile(nodes,d.nodes||[]);
   const entries=[...(d.browser_jobs||[]).map(j=>({...j,_id:`browser:${j.id}`,kind:'browser',platform:host(j.url),summary:j.summary||'公开页面阅读',time:j.created})),...(d.tasks||[]).map(t=>({...t,_id:`task:${t.id}`,kind:'task',platform:t.merchant,summary:t.summary,time:t.created_at}))];
   entries.sort((a,b)=>Number(b.time||0)-Number(a.time||0));reconcile(rows,entries,'_id');
  }
  if(props.kind==='agent-payments'){
   reconcile(rows,d.records||[]);
   if(!policyLoaded||!policyDirty.value){Object.assign(policy,{single:d.policy?.single_minor/100,daily:d.policy?.daily_minor/100,monthly:d.policy?.monthly_minor/100,automatic:d.policy?.auto_daily_minor/100});policyLoaded=true;}
  }
 });
}
onMounted(reload);
const poll=setInterval(()=>{if(props.kind==='agent-apps'&&!document.hidden&&!ui.loading.value)void reload();},10000);
onUnmounted(()=>{clearInterval(poll);controller.abort();detailVersion++;resourceVersion++;});
defineExpose({refresh:reload});
const filtered=computed(()=>{
 const source=props.kind==='agent-payments'&&paymentType.value==='tasks'?(data.value.tasks||[]):rows;
 return source.filter(r=>{
  if(props.kind==='jobs'&&filter.value!=='all'&&r.paused!==(filter.value==='paused'))return false;
  if(props.kind==='agent-apps'&&filter.value!=='all'&&r.kind!==filter.value)return false;
  return [r.id,r.name,r.trigger,r.platform,r.merchant,r.summary,r.state,r.group_name].filter(Boolean).join(' ').toLowerCase().includes(query.value.trim().toLowerCase());
 });
});
const pageRows=computed(()=>{const current=Math.min(page.value,Math.max(0,Math.ceil(filtered.value.length/15)-1));return filtered.value.slice(current*15,current*15+15);});
const groupOptions=computed(()=>rows);
const resourcePageRows=computed(()=>resourceRows.slice(resourcePage.value*15,resourcePage.value*15+15));
async function toggleJob(j){const enabled=j.paused;await ui.run(j.id,async()=>{await api(`jobs/${encodeURIComponent(j.id)}`,{method:'PUT',body:{enabled}});j.paused=!enabled;await reload();},enabled?'任务已恢复':'任务已暂停');}
async function toggleNode(n){await ui.run('node:'+n.id,async()=>{await api(`agent/nodes/${encodeURIComponent(n.id)}`,{method:'PUT',body:{enabled:!n.enabled}});await reload();},'节点状态已保存');}
watch([group,resource],()=>{props.viewState.group=group.value;props.viewState.resource=resource.value;resourceVersion++;resourceBusy.value=false;resourceRows.splice(0);resourceMeta.value=null;resourceError.value='';});
async function readResource(){if(!group.value||resourceBusy.value)return;const rev=++resourceVersion;resourceBusy.value=true;resourceError.value='';try{const result=await api(`qq/groups/${encodeURIComponent(group.value)}/${resource.value}?keyword=${encodeURIComponent(query.value.trim().slice(0,80))}`);if(rev!==resourceVersion)return;resourceRows.splice(0,resourceRows.length,...result.items);resourceMeta.value=result;resourcePage.value=0;}catch(e){if(rev===resourceVersion)resourceError.value=errorMessage(e);}finally{if(rev===resourceVersion)resourceBusy.value=false;}}
async function showDetails(title,path,options){detailTitle.value=title;detail.value=null;detailError.value='';detailBusy.value=true;const rev=++detailVersion;await nextTick();if(!dialog.value.open)dialog.value.showModal();try{const d=await api(path,options);if(rev===detailVersion)detail.value=d;}catch(e){if(rev===detailVersion)detailError.value=errorMessage(e);}finally{if(rev===detailVersion)detailBusy.value=false;}}
function closeDetail(){detailVersion++;dialog.value.close();detail.value=null;detailError.value='';detailBusy.value=false;}
function behaviorDetail(r){return showDetails(r.summary||'行为详情',r.kind==='browser'?`agent/browser-jobs/${encodeURIComponent(r.id)}`:`agent/task-detail/${encodeURIComponent(r.id)}`);}
async function saveLimits(){
 const vals=Object.values(policy);if(vals.some(v=>!Number.isFinite(Number(v))||Number(v)<0||Number(v)>1000000)){props.notify('额度必须为有效非负金额');return;}
 await ui.run('limits',async()=>{const latest=await api('agent/payments');await api('agent/policy',{method:'PUT',body:{paused:latest.policy.paused,single_minor:Math.round(policy.single*100),daily_minor:Math.round(policy.daily*100),monthly_minor:Math.round(policy.monthly*100),auto_daily_minor:Math.round(policy.automatic*100)}});policyDirty.value=false;await reload();},'额度已保存，超限仍按维度独立审批');
}
</script>
<template><div class="migration-pages">
 <section v-if="!owned" class="panel"><div class="empty">仅所有者可管理 {{titles[kind]}}</div></section>
 <template v-else>
 <section class="panel"><div class="panel-head"><div><h3>{{titles[kind]}}</h3><p v-if="kind==='agent-apps'">执行节点每 10 秒局部更新；只展示有执行记录的行为。</p><p v-else-if="kind==='agent-payments'">支付能力与应用操作分开管理；真实支付仍保持现有接入状态。</p><p v-else-if="kind==='qq'">SnowLuma 只读资源查询，结果不会重放为群内新消息。</p><p v-else>暂停保留任务和规则；状态、筛选与页码独立更新。</p></div><div class="row"><small class="mono">{{ui.updated.value}}</small><button class="button small" :disabled="ui.loading.value" @click="reload">{{ui.loading.value?'更新中…':'更新数据'}}</button></div></div>
 <p v-if="ui.error.value" class="notice" role="alert">{{ui.error.value}}；保留上次成功数据。</p>
 <div v-if="kind!=='qq'" class="panel-body row"><input v-model="query" class="input search" type="search" placeholder="搜索当前列表" :aria-label="`搜索${titles[kind]}`"><select v-if="kind==='jobs'||kind==='agent-apps'" v-model="filter" class="input search" aria-label="状态筛选"><option value="all">全部</option><template v-if="kind==='jobs'"><option value="running">运行中</option><option value="paused">已暂停</option></template><template v-else><option value="browser">网页研究</option><option value="task">应用任务</option></template></select></div>
 <template v-if="kind==='jobs'"><div v-if="data.available===false" class="empty">未安装任务调度器</div><div class="table-wrap"><table><thead><tr><th>任务</th><th>触发规则</th><th>下次运行</th><th>状态</th><th>操作</th></tr></thead><tbody><tr v-for="j in pageRows" :key="j.id"><td>{{j.name}}<div class="mono">{{j.id}}</div></td><td class="mono">{{j.trigger}}</td><td>{{j.paused?'已暂停':stamp(j.next_run)}}</td><td><span class="badge" :class="j.paused?'warn':'ok'">{{j.paused?'已暂停':'运行中'}}</span></td><td><button class="button small" :disabled="ui.pending.has(j.id)" @click="toggleJob(j)">{{j.paused?'恢复':'暂停'}}</button></td></tr></tbody></table></div></template>
 <template v-if="kind==='qq'"><div class="panel-body"><p>{{data.implementation?.app_name||'等待连接'}} {{data.implementation?.app_version}} · QQ {{data.account||'—'}} · {{data.status?.good?'连接正常':'状态待核对'}}</p><div class="migration-controls"><label>当前群<select v-model="group" class="input" aria-label="当前群"><option v-for="g in groupOptions" :key="g.id" :value="g.id">{{g.name}}（{{g.id}}）</option></select></label><label>资源<select v-model="resource" class="input" aria-label="资源类型"><option v-for="[value,label] in [['notices','群公告'],['storage','群文件容量'],['honor','群荣誉'],['files','群文件根目录'],['todos','群待办'],['essence','群精华'],['history','最近聊天']]" :key="value" :value="value">{{label}}</option></select></label><label>关键词<input v-model="query" class="input" maxlength="80" placeholder="过滤本次读取结果" aria-label="资源关键词"></label><button class="button primary" :disabled="!group||resourceBusy" @click="readResource">{{resourceBusy?'读取中…':'读取资源'}}</button></div><p class="notice">群内可用 /群公告 [词]、/群文件容量、/群荣誉。图片公告不推测文字；容量、荣誉只展示接口实际结果。</p><p v-if="resourceError" class="notice" role="alert">{{resourceError}}</p><p v-if="resourceMeta" class="mono">本次 {{resourceMeta.returned}} 项 · {{resourceMeta.scope==='root_only'?'仅根目录':resourceMeta.scope==='current_snapshot'?'当前快照':'当前读取范围，不代表全部历史'}}</p></div><div class="table-wrap"><table><thead><tr><th>名称 / 来源</th><th>内容</th><th>详情</th></tr></thead><tbody><tr v-for="(r,index) in resourcePageRows" :key="r.id||r.message_id||index"><td>{{r.name||r.speaker||'—'}}<small v-if="r.time" class="mono migration-block">{{stamp(r.time)}}</small><span v-if="r.pinned" class="badge">置顶</span></td><td class="migration-text">{{r.text||r.name||'无文字内容'}}</td><td><button v-if="resource==='files'&&!r.folder" class="button small" @click="showDetails(r.name,`qq/groups/${encodeURIComponent(group)}/file/read`,{method:'POST',body:{file_id:r.id,folder_id:''}})">读取内容</button></td></tr></tbody></table></div><Pager :total="resourceRows.length" v-model:page="resourcePage"/></template>
 <template v-if="kind==='agent-apps'"><div class="panel-body"><h4>执行节点实时状态</h4><div class="migration-nodes"><article v-for="n in nodes" :key="n.id" class="slang-card"><strong>{{n.name}}</strong><p>{{!n.enabled?'已停用':Date.now()/1000-n.last_seen<90?'在线':'等待连接'}}</p><small>{{stamp(n.last_seen)}}</small><p>{{(n.domains||[]).join('、')}}</p><button class="button small" :disabled="ui.pending.has('node:'+n.id)" @click="toggleNode(n)">{{n.enabled?'停用节点':'启用节点'}}</button></article></div><p v-if="!nodes.length" class="mono">没有注册的执行节点</p><h4>历史行为记录</h4></div><div class="table-wrap"><table><thead><tr><th>时间</th><th>平台</th><th>行为概要</th><th>状态</th><th>详情</th></tr></thead><tbody><tr v-for="r in pageRows" :key="r._id"><td>{{stamp(r.time)}}</td><td>{{r.platform||'—'}}</td><td>{{r.summary||'未提供概要'}}</td><td>{{states[r.state]||r.state}}</td><td><button class="button small" @click="behaviorDetail(r)">查看详情</button></td></tr></tbody></table></div><div class="panel-body"><a href="/#/agent-apps" class="button small ghost">剩余授权、电脑接管与游戏设置</a></div></template>
 <template v-if="kind==='agent-payments'"><div class="panel-body"><p class="notice">{{data.live_available?'真实支付能力可用':'真实支付尚未接入，本页不会发起付款。'}} 超限按单笔、当日和当月独立审批。</p><div class="stats"><div class="stat"><small>实际今日</small><strong>{{money(data.live_usage?.daily_minor)}}</strong></div><div class="stat"><small>实际本月</small><strong>{{money(data.live_usage?.monthly_minor)}}</strong></div><div class="stat"><small>模拟今日</small><strong>{{money(data.usage?.daily_minor)}}</strong></div><div class="stat"><small>待执行预占</small><strong>{{money(data.usage?.reserved_minor)}}</strong></div></div><form class="migration-controls" @submit.prevent="saveLimits"><label v-for="[key,label] in [['single','单笔 / 元'],['daily','每日 / 元'],['monthly','每月 / 元'],['automatic','每日免审核 / 元']]" :key="key">{{label}}<input v-model.number="policy[key]" type="number" class="input" min="0" max="1000000" step="0.01" :aria-label="label" @input="policyDirty=true"></label><button class="button primary" :disabled="ui.pending.has('limits')||!policyDirty">保存额度</button></form><div class="row"><button class="button small" @click="paymentType='records'">历史支付项目</button><button class="button small" @click="paymentType='tasks'">支付任务</button></div></div><div class="table-wrap"><table><thead><tr><th>时间</th><th>项目概要</th><th>金额</th><th>状态</th><th>详情</th></tr></thead><tbody><tr v-for="r in pageRows" :key="r.id"><td>{{stamp(r.created||r.created_at)}}</td><td>{{r.summary||r.purpose||'未提供概要'}}</td><td>{{money(r.amount_minor)}}</td><td>{{r.simulation?'模拟 · ':''}}{{states[r.state]||r.state||r.status||'已记录'}}</td><td><button class="button small" @click="showDetails('支付详情',paymentType==='records'?`agent/payments/records/${encodeURIComponent(r.id)}`:`agent/task-detail/${encodeURIComponent(r.id)}`)">查看详情</button></td></tr></tbody></table></div><div class="panel-body"><a href="/#/agent-payments" class="button small ghost">剩余用途授权与超限审批</a></div></template>
 <div v-if="kind!=='qq'&&filtered.length===0&&!ui.loading.value" class="empty">暂无匹配记录</div><Pager v-if="kind!=='qq'" :total="filtered.length" v-model:page="page"/>
 </section></template>
 <dialog ref="dialog" class="migration-dialog" aria-labelledby="migration-detail-title" @cancel.prevent="closeDetail"><div class="drawer-head"><h3 id="migration-detail-title">{{detailTitle}}</h3><button class="close" aria-label="关闭详情" @click="closeDetail">×</button></div><div class="drawer-body"><p v-if="detailBusy">正在读取详细信息…</p><p v-else-if="detailError" class="notice" role="alert">{{detailError}}</p><template v-else-if="detail"><p v-if="detail.excerpt" class="quote migration-text">{{detail.excerpt}}</p><template v-else><section v-for="(value,key) in detail" :key="key"><h4>{{key}}</h4><pre class="migration-text">{{typeof value==='object'?JSON.stringify(value,null,2):value}}</pre></section></template></template></div></dialog>
</div></template>
