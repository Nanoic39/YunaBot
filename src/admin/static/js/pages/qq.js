'use strict';
import { api, busy, el, notice, panel, toast, errorText, modal, field } from '../core.js';
import { dataTable } from './agent-common.js';

export async function render(host, ctx) {
  if (ctx.user?.role !== 'owner') { host.append(notice('仅面板所有者可以查询 QQ 连接与群资源。', 'warn')); return; }
  const info = el('div');
  host.append(panel('QQ 连接与接口', { subtitle: '本页查询不会发送QQ消息；自主表情按下方策略执行。' }, info));
  let bridge;
  try {
    bridge = await api('qq/bridge');
    info.append(el('p', { text: `${bridge.implementation.app_name || '未知实现'} ${bridge.implementation.app_version || ''} · QQ ${bridge.account} · ${bridge.status?.good ? '收取状态正常' : '收取状态待检查'}` }),
      el('a', { href: '#/overview', text: '返回运行概览查看扫码登录' }));
  } catch (error) { info.append(notice(errorText(error), 'warn')); }
  await renderAutonomy(host, bridge);
  if (bridge) {
    const group = el('select', { class: 'input', 'aria-label': '选择群聊' }, ...bridge.groups.map(g => el('option', { value: g.id, text: `${g.name}（${g.id}）` })));
    const kind = el('select', { class: 'input', 'aria-label': '资源类型' },
      ...[['history','最近聊天'],['files','群文件根目录'],['todos','群待办'],['essence','群精华'],['notices','群公告'],['storage','群文件容量'],['honor','群荣誉']].map(([v,t]) => el('option', {value:v,text:t})));
    const keyword = el('input', { class: 'input', placeholder: '可选关键词（仅过滤本次读取结果）', maxLength: '80' });
    const results = el('div');
    const read = el('button', { class: 'button primary small', type: 'button', text: '读取当前群资源' });
    read.disabled = !bridge.groups.length;
    read.addEventListener('click', async () => {
      busy(read, true);
      try {
        const data = await api(`qq/groups/${encodeURIComponent(group.value)}/${kind.value}?keyword=${encodeURIComponent(keyword.value.trim())}`);
        const rows = data.items.map(r => el('tr', null,
          el('td', { text: r.name || r.speaker || (r.folder ? '文件夹' : '') }),
          el('td', { text: r.text || r.name || '无可读取文本' }),
          el('td', { text: String(r.message_id ?? r.id ?? '') }),
          el('td', null, data.kind === 'files' && !r.folder ? el('button', {type:'button',class:'button secondary small',text:'读取内容',onclick:()=>showFile(data.group_id,r.id,'')}) : null)));
        results.replaceChildren(notice(`本次返回 ${data.returned} 项。${data.scope === 'root_only' ? '仅根目录，不递归子目录。' : data.scope === 'current_snapshot' ? '接口当前快照，不推断未返回的数据。' : '仅本次实际读取的记录，不代表全部历史。'}${data.truncated ? '结果已截取。' : ''}`, 'info'),
          dataTable(['发送者 / 名称','内容','消息或文件标识','查看'], rows));
      } catch (error) { results.replaceChildren(notice(errorText(error), 'warn')); }
      finally { busy(read, false); }
    });
    host.append(panel('群资源查询', { subtitle: '群内可用 /群文件 [词]、/群文件容量、/群公告 [词]、/群荣誉、/群待办、/群精华、/回顾聊天 [词]。历史查询限定当前会话；不会重放为新消息。' },
      el('div', { class: 'stack' }, group, kind, keyword, read), results));
  }
  try {
    const audit = await api('qq/audit');
    const rows = audit.plugins.map(p => el('tr', null, el('td', { text: p.plugin }),
      el('td', { text: p.result }), el('td', { text: p.notes }), el('td', { text: p.actions.join('、') || '无 QQ 专属接口' })));
    host.append(panel('插件适配检查', { subtitle: '逐项列出依赖和限制；接口存在不代表已实测全部业务，写操作使用模拟验证。' },
      dataTable(['插件','检查结果','适配说明','QQ 接口'], rows)));
  } catch (error) { toast(errorText(error), 'error'); }
}


async function showFile(group, id, folder) {
  try {
    const note = await api(`qq/groups/${encodeURIComponent(group)}/file/read`, {method:'POST',body:{file_id:id,folder_id:folder || ''}});
    modal({title:note.name,wide:true,body:el('div',null,
      notice(`来源：当前群文件 · 读取时间 ${new Date(note.time*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})} · ${note.cached?'使用已核对版本的缓存':'已读取当前版本'}。展示最多6000字，PDF最多前20页。`,'info'),
      el('pre',{class:'agent-result-text',text:note.excerpt}))});
  } catch(error) { toast(errorText(error),'error'); }
}

async function renderAutonomy(host, bridge) {
  try {
    const state = await api('qq/autonomy');
    if(state.warning)host.append(notice(state.warning,'warn'));
    const p=state.policy;
    const toggle=(value)=>{const node=el('input',{type:'checkbox'});node.checked=value;return node;};
    const number=(value,min,max)=>el('input',{class:'input',type:'number',value,min,max,required:true});
    const history=toggle(p.history), files=toggle(p.files), reactions=toggle(p.reactions);
    const daily=number(p.reaction_daily,0,20), cooldown=number(p.reaction_cooldown,60,86400);
    const quietStart=number(p.quiet_start,0,23),quietEnd=number(p.quiet_end,0,23);
    const form=el('form',{class:'stack'},field('按需补齐当前会话历史',history),field('按请求读取本群文件',files),
      field('允许用表情轻量回应',reactions),field('每群每日表情次数',daily),field('同群表情间隔（秒）',cooldown),
      field('表情静默开始（北京时间，小时）',quietStart),field('表情静默结束（相同则不设此静默时段）',quietEnd));
    const scopes=JSON.parse(JSON.stringify(p.scopes || {}));
    const group=el('select',{'aria-label':'独立设置的群聊',class:'input'},el('option',{value:'',text:'选择群聊，设置独立开关'}),
      ...(bridge?.groups || []).map(g=>el('option',{value:'group:'+g.id,text:g.name+'（'+g.id+'）'})));
    const choice=()=>el('select',{class:'input'},el('option',{value:'',text:'继承全局'}),el('option',{value:'true',text:'开启'}),el('option',{value:'false',text:'关闭'}));
    const scopeHistory=choice(),scopeFiles=choice(),scopeReactions=choice();
    const syncScope=()=>{for(const [key,node] of [['history',scopeHistory],['files',scopeFiles],['reactions',scopeReactions]]) {
      node.disabled=!group.value;const value=(scopes[group.value] || {})[key];node.value=value===undefined?'':String(value);
    }};
    group.addEventListener('change',syncScope);syncScope();
    const activity=el('div'), notes=el('div');
    const showActivity=current=>{
      const statuses={pending:'执行中',succeeded:'已完成',failed:'失败',unknown:'结果待核实',blocked:'已避免'};
      activity.replaceChildren(dataTable(['时间','会话','概要','状态','查看'],current.events.map(r=>el('tr',null,
        el('td',{text:new Date(r.time*1000).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'})}),el('td',{text:r.scope}),
        el('td',{text:r.summary}),el('td',{text:statuses[r.status] || r.status}),
        el('td',null,el('button',{type:'button',class:'button secondary small',text:'详情',onclick:async()=>{
          try {const detail=await api('qq/activity/'+r.id);modal({title:'QQ行为详情',wide:true,body:el('pre',{class:'agent-result-text',text:JSON.stringify(detail,null,2)})});}
          catch(error){toast(errorText(error),'error');}
        }}))))));
      notes.replaceChildren(...current.notes.map(n=>el('p',null,el('span',{text:n.name+' · '+n.scope+' · 版本 '+n.hash.slice(0,10)+' '}),
        el('button',{type:'button',class:'button secondary small',text:'查看 / 更新摘录',onclick:()=>showFile(n.scope.split(':')[1],n.file_id,n.folder_id)}))));
      if(!current.notes.length)notes.append(el('p',{class:'muted',text:'尚未读取群文件。文件资料保留来源与版本，不自动变成人格记忆。'}));
    };
    showActivity(state);
    const save=el('button',{class:'button primary small',type:'submit',text:'保存QQ自主设置'});
    form.append(field('独立设置会话',group),field('该群历史查询',scopeHistory),field('该群文件读取',scopeFiles),field('该群表情回应',scopeReactions),save);
    form.addEventListener('submit',async event=>{
      event.preventDefault();busy(save,true);
      try {
        if(group.value) {
          const rule={};for(const [key,node] of [['history',scopeHistory],['files',scopeFiles],['reactions',scopeReactions]])if(node.value!=='')rule[key]=node.value==='true';
          if(Object.keys(rule).length)scopes[group.value]=rule;else delete scopes[group.value];
        }
        const current=await api('qq/autonomy',{method:'PUT',body:{history:history.checked,files:files.checked,reactions:reactions.checked,
          reaction_daily:Number(daily.value),reaction_cooldown:Number(cooldown.value),quiet_start:Number(quietStart.value),quiet_end:Number(quietEnd.value),scopes}});
        showActivity(current);toast('QQ自主设置已保存','ok');
      }catch(error){toast(errorText(error),'error');}finally{busy(save,false);}
    });
    const refresh=el('button',{type:'button',class:'button secondary small',text:'刷新行为记录',onclick:async()=>{
      try{showActivity(await api('qq/autonomy'));}catch(error){toast(errorText(error),'error');}
    }});
    host.append(panel('QQ自主能力',{subtitle:'只在相关对话中按需调用；单群独立开关优先于全局。表情每人至少间隔30分钟，求助和认真提问继续文字回复。历史不跨会话，文件最多2MB，支持文本、DOCX和文字PDF。'},form),
      panel('QQ行为记录',{subtitle:'默认只显示概要，详情保留来源标识和执行结果。'},refresh,activity),
      panel('群文件资料',{subtitle:'读取前核对当前版本；版本变化或过期后重新读取，失败不会回用旧摘录。'},notes));
  }catch(error){host.append(notice('QQ自主设置读取失败：'+errorText(error),'warn'));}
}
