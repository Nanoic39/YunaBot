<script setup>
import { reactive, ref, onMounted } from 'vue';
const props=defineProps(['request','notify','user']);
const contacts=reactive([]), groups=reactive([]), busy=ref(false), error=ref('');
const contact=reactive({nickname:'',wxid:'',whitelist:true});
const group=reactive({name:'',wxid:'',whitelist:true});
const api=(path,options={})=>props.request(path,{...options,body:options.body===undefined?undefined:JSON.stringify(options.body)});
async function load(){
  busy.value=true;error.value='';
  try{
    const [c,g]=await Promise.all([api('wechat/contacts'),api('wechat/groups')]);
    contacts.splice(0,contacts.length,...(c.items||[]));
    groups.splice(0,groups.length,...(g.items||[]));
  }catch(e){error.value=String(e.message||e);}
  finally{busy.value=false;}
}
async function addContact(){
  if(!contact.nickname.trim()){props.notify('请填写微信昵称');return;}
  try{const row=await api('wechat/contacts',{method:'POST',body:{...contact}});props.notify(`已登记 ${row.id6}-${row.nickname}`);contact.nickname='';contact.wxid='';await load();}
  catch(e){props.notify(String(e.message||e));}
}
async function toggleContact(row){
  try{await api(`wechat/contacts/${encodeURIComponent(row.id6)}/whitelist`,{method:'POST',body:{enabled:!row.whitelist}});await load();}
  catch(e){props.notify(String(e.message||e));}
}
async function addGroup(){
  if(!group.name.trim()){props.notify('请填写群名');return;}
  try{const row=await api('wechat/groups',{method:'POST',body:{...group}});props.notify(`已登记群 ${row.id6}-${row.name}`);group.name='';group.wxid='';await load();}
  catch(e){props.notify(String(e.message||e));}
}
async function toggleGroup(row){
  try{await api(`wechat/groups/${encodeURIComponent(row.id6)}/whitelist`,{method:'POST',body:{enabled:!row.whitelist}});await load();}
  catch(e){props.notify(String(e.message||e));}
}
onMounted(load);
</script>
<template>
<div class="migration-pages">
 <section class="panel"><div class="panel-head"><div><h3>微信白名单</h3><p>来源统一标记为微信；联系人编号格式为 6位ID-昵称。好友申请不自动同意。</p></div><button class="button small" :disabled="busy" @click="load">刷新</button></div>
 <p v-if="error" class="notice">{{error}}</p>
 <div class="panel-body"><h4>登记联系人</h4><div class="migration-controls">
  <label>昵称<input v-model="contact.nickname" class="input" placeholder="微信昵称" maxlength="80"></label>
  <label>wxid（可选）<input v-model="contact.wxid" class="input" placeholder="wxid_..." maxlength="120"></label>
  <label class="row"><input v-model="contact.whitelist" type="checkbox"> 加入白名单</label>
  <button class="button primary" @click="addContact">登记并编号</button>
 </div></div>
 <div class="table-wrap"><table><thead><tr><th>编号-昵称</th><th>来源</th><th>白名单</th><th>操作</th></tr></thead><tbody>
  <tr v-for="r in contacts" :key="r.id6"><td><strong>{{r.id6}}-{{r.remark||r.nickname}}</strong><div class="mono">{{r.wxid||'未获取 wxid'}}</div></td><td><span class="badge">微信</span></td><td><span class="badge" :class="r.whitelist?'ok':'warn'">{{r.whitelist?'已加入':'未加入'}}</span></td><td><button class="button small" @click="toggleContact(r)">{{r.whitelist?'移出':'加入'}}</button></td></tr>
  <tr v-if="!contacts.length"><td colspan="4" class="empty">暂无微信联系人</td></tr>
 </tbody></table></div>
 </section>
 <section class="panel"><div class="panel-head"><div><h3>微信群白名单</h3><p>只允许把内容转发到已加入白名单的微信群；群名和群号都会记录。</p></div></div>
 <div class="panel-body"><div class="migration-controls">
  <label>群名<input v-model="group.name" class="input" placeholder="微信群名" maxlength="120"></label>
  <label>群号/ID（可选）<input v-model="group.wxid" class="input" placeholder="群 ID" maxlength="120"></label>
  <label class="row"><input v-model="group.whitelist" type="checkbox"> 加入白名单</label>
  <button class="button primary" @click="addGroup">登记群</button>
 </div></div>
 <div class="table-wrap"><table><thead><tr><th>编号-群名</th><th>来源</th><th>白名单</th><th>操作</th></tr></thead><tbody>
  <tr v-for="r in groups" :key="r.id6"><td><strong>{{r.id6}}-{{r.name}}</strong></td><td><span class="badge">微信</span></td><td><span class="badge" :class="r.whitelist?'ok':'warn'">{{r.whitelist?'已加入':'未加入'}}</span></td><td><button class="button small" @click="toggleGroup(r)">{{r.whitelist?'移出':'加入'}}</button></td></tr>
  <tr v-if="!groups.length"><td colspan="4" class="empty">暂无微信群</td></tr>
 </tbody></table></div>
 </section>
</div>
</template>
