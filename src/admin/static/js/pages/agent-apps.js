'use strict';
import { api, busy, el, errorText, field, modal, notice, panel, toast } from '../core.js';
import { input, choice, checkbox, submit, dataTable, newKey } from './agent-common.js';

const states = {queued:'排队中', running:'执行中', succeeded:'已完成', failed:'失败', expired:'超时', cancelled:'已取消', pending:'待审批', done:'已完成', waiting_aid:'等待协助', interrupted:'已中断', authorized:'已授权', simulated:'模拟完成'};
const time = (value) => value ? new Date(value * 1000).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai'}) : '尚未连接';

export async function render(host, ctx) {
  if (ctx.user?.role !== 'owner') { host.append(notice('仅所有者可管理执行节点和行为记录。', 'warn')); return; }
  const data = await api('agent/apps');
  const extra = await Promise.allSettled([
    api('agent/computer',{signal:AbortSignal.timeout(7000)}), api('agent/aid'),
    api('agent/computer/tasks'), api('agent/computer/play'), api('agent/social'), api('agent/learning'), api('agent/autonomy'), api('agent/minecraft'),
  ]);
  let updateComputerTasks = null;
  const computerState = el('p', {class:'muted'});
  let [phone,aid,phoneTasks,play,social,learning,autonomy,game] = extra.map(r=>r.status==='fulfilled'?r.value:null);
  host.append(notice('公开网页节点负责只读研究；电脑 Agent 可在获批时段操作应用，普通条款和获取验证码自主执行，缺少信息或涉及敏感动作时再通过超管QQ协助。首帖必须先通过评论回复验收，真实付款尚未接入。', 'info'));
  if (autonomy) {
    const quiet=checkbox(autonomy.quiet), payment=checkbox(autonomy.notify_payments);
    const settings=el('form',{class:'stack agent-form'},field('静默自主（仅必要凭据与超限审批私聊）',quiet),field('支付完成私聊通知',payment));
    submit(settings,'保存自主偏好',()=>api('agent/autonomy',{method:'PUT',body:{quiet:quiet.checked,notify_payments:payment.checked}}),ctx);
    const memory=input('');memory.placeholder='记住的禁忌、偏好或建议';
    const add=el('form',{class:'row'},memory);
    submit(add,'记住行为要求',()=>api('agent/preferences',{method:'POST',body:{text:memory.value}}),ctx);
    const rows=autonomy.preferences.map(r=>el('tr',null,el('td',{text:r.kind==='avoid'?'尽力避免':r.kind==='suggestion'?'可选建议':'偏好'}),el('td',{text:r.text}),el('td',{text:r.owner?'超管':'朋友（不是授权）'}),el('td',null,el('button',{type:'button',class:'button secondary xs',text:'移除',onclick:async()=>{await api('agent/preferences/'+r.id,{method:'DELETE'});await ctx.rerender();}}))));
    const blocks=el('div',null,...Object.entries(autonomy.site_blocks).filter(([,r])=>r.until>Date.now()/1000).map(([host,r])=>el('p',{text:host+'：冷却至 '+time(r.until)+'；'+r.reason})));
    host.append(panel('自主决策与聊天行为记忆',{subtitle:'常规进度不私聊；支付账本照常保留，超限审批不受影响。聊天建议不是授权，禁忌按原来的条件和对象核对。'},settings,blocks,add,dataTable(['类型','内容','来源','管理'],rows)));
  }
  if (game) {
    const enabled=checkbox(game.policy.enabled);
    const continuous=checkbox(game.policy.continuous);
    const pipeline=checkbox(game.policy.pipeline);
    const syncLimits=()=>{daily.disabled=session.disabled=cooldown.disabled=continuous.checked;};
    const daily=el('input',{type:'number',min:'5',max:'120',value:String(game.policy.daily_minutes)});
    const session=el('input',{type:'number',min:'1',max:'15',value:String(game.policy.session_minutes)});
    const cooldown=el('input',{type:'number',min:'1',max:'24',value:String(game.policy.cooldown_hours)});
    const settings=el('form',{class:'stack agent-form'},field('允许空闲时自主玩MC',enabled),field('持续推进（不限制步骤和游玩总时长）',continuous),field('动作预取：边执行边规划',pipeline),field('限时模式每日预算（分钟）',daily),field('每次预算（分钟）',session),field('两次间隔（小时）',cooldown));
    continuous.addEventListener('change',syncLimits);syncLimits();
    submit(settings,'保存游戏策略',()=>api('agent/minecraft/policy',{method:'PUT',body:{enabled:enabled.checked,continuous:continuous.checked,pipeline:pipeline.checked,daily_minutes:Number(daily.value),session_minutes:Number(session.value),cooldown_hours:Number(cooldown.value)}}),ctx);
    const start=el('button',{type:'button',class:'button primary small',text:'让Yuna自己选目标并游玩',onclick:async()=>{busy(start,true);try{await api('agent/minecraft/start',{method:'POST'});await ctx.rerender();}catch(e){toast(errorText(e),'error');}finally{busy(start,false);}}});
    const stop=el('button',{type:'button',class:'button ghost small',text:'停止并断开游戏',onclick:async()=>{try{await api('agent/minecraft/stop',{method:'POST'});await ctx.rerender();}catch(e){toast(errorText(e),'error');}}});
    const labels={planning:'规划中',running:'游玩中',done:'已核验',incomplete:'尚未完成',failed:'失败',cancelled:'已停止',interrupted:'重启中断'};
    const gameRows=el('div'), gameProjects=el('div'), gameEvidence=el('p',{class:'muted'}), liveGameState=el('p');
    const renderGameRows=current=>{const rows=current.runs.map(r=>el('tr',null,el('td',{text:time(r.time)}),el('td',null,el('span',{text:r.goal}),r.project_title?el('p',{class:'muted',text:r.project_title+' · 阶段 '+(r.milestone_index+1)}):null),el('td',{text:labels[r.status]||r.status}),el('td',{text:r.summary||'正在观察环境'}),el('td',null,el('button',{type:'button',class:'button secondary xs',text:'详细状态与步骤',onclick:async()=>{try{const detail=await api('agent/minecraft/runs/'+r.id);modal({title:'MC游玩详情',wide:true,body:el('pre',{class:'agent-result-text',text:JSON.stringify(detail,null,2)})});}catch(e){toast(errorText(e),'error');}}}))));
    const projectStates={active:'推进中',blocked:'暂时受阻',done:'已核验完成',abandoned:'已放弃',pending:'尚未开始',in_progress:'进行中'};
    gameProjects.replaceChildren(...(current.projects||[]).slice().reverse().map(p=>el('div',{class:'stack'},el('strong',{text:p.title+' · '+(projectStates[p.status]||p.status)}),...p.milestones.map((m,i)=>el('p',{class:'muted',text:(i+1)+'. '+m.goal+' · '+(projectStates[m.status]||m.status)+(m.success.kind==='visit'?' · 新区域 '+m.new_cells+'/'+m.success.target:'')})),el('button',{type:'button',class:'button secondary xs',text:'查看计划与跨轮次证据',onclick:async()=>{try{const detail=await api('agent/minecraft/projects/'+p.id);modal({title:'跨轮次游戏计划',wide:true,body:el('pre',{class:'agent-result-text',text:JSON.stringify(detail,null,2)})});}catch(e){toast(errorText(e),'error');}}}))));
    const actionNames={goto:'寻路',eat:'进食',equip:'装备',craft:'合成'};
    gameEvidence.textContent='已到访 '+(current.atlas?.visited_cells||0)+' 个4×4区域。实际成功操作：'+(Object.entries(current.skill_evidence||{}).map(([name,r])=>(actionNames[name]||name)+' '+r.verified_uses+'次').join('、')||'尚无新增证据')+'；成功操作记录不代表已经熟练掌握。';
    gameRows.replaceChildren(dataTable(['时间','目标','状态','结果概要','查看'],rows));
    liveGameState.textContent=current.active?(current.policy.continuous?'持续推进中：':'正在游玩：')+(current.runs.find(r=>r.id===current.active)?.goal||'观察环境'):'当前未连接游戏；'+(current.configured?'节点已配置':'节点未配置');};
    renderGameRows(game);
    let polling=false;const gameTimer=setInterval(async()=>{if(!liveGameState.isConnected){clearInterval(gameTimer);return;}if(polling)return;polling=true;try{renderGameRows(await api('agent/minecraft',{signal:AbortSignal.timeout(6000)}));}catch{}finally{polling=false;}},5000);
    host.append(panel('MC自主游玩',{subtitle:'自主目标拆成跨轮次里程碑，真实到访新区才计探索进度。支持自身背包进食、装备和基础合成；不挖掘、不放置、不攻击或拿容器物品。'},liveGameState,settings,el('div',{class:'row'},start,stop),gameEvidence,gameProjects,gameRows));
  }
  if (learning) {
    const enabled=el('input',{type:'checkbox'});enabled.checked=!!learning.policy.enabled;
    const minutes=el('input',{type:'number',min:'5',max:'60',value:String(learning.policy.daily_minutes || 20)});
    const cooldown=el('input',{type:'number',min:'3',max:'48',value:String(learning.policy.cooldown_hours || 6)});
    const form=el('form',{class:'stack agent-form'},field('允许限时自主阅读',enabled),field('每日最多分钟',minutes),field('两次至少间隔小时',cooldown));
    submit(form,'保存阅读策略',()=>api('agent/learning/policy',{method:'PUT',body:{enabled:enabled.checked,daily_minutes:Number(minutes.value),cooldown_hours:Number(cooldown.value)}}),ctx);
    const readTopic=el('input',{placeholder:'这次想读什么？留空让Yuna自己选'});
    const read=el('button',{type:'button',class:'button secondary small',text:'手动阅读一次（计入预算）',onclick:async()=>{
      busy(read,true);try{const result=await api('agent/learning/read',{method:'POST',body:readTopic.value.trim()?{topic:readTopic.value.trim()}:{}});toast(result.started?'已启动限时自主阅读':'本次未阅读：可能正在忙、预算或间隔未到，或她选择暂时不读',result.started?'ok':'info');await ctx.rerender();}
      catch(error){toast(errorText(error),'error');}finally{busy(read,false);}
    }});
    const rows=learning.notes.map(n=>el('tr',null,el('td',{text:time(n.time)}),el('td',{text:n.summary}),el('td',{text:'已读摘录 · 心得未验证'}),el('td',null,el('button',{type:'button',class:'button secondary xs',text:'来源与原文',onclick:async()=>{
      try{const detail=await api('agent/learning/notes/'+encodeURIComponent(n.id));modal({title:'阅读笔记详情',wide:true,body:el('pre',{class:'agent-result-text',text:JSON.stringify(detail,null,2)})});}
      catch(error){toast(errorText(error),'error');}
    }}))));
    host.append(panel('自主阅读与学习笔记',{subtitle:'每次最多5分钟，自动阅读受生活状态、每日预算与间隔约束，手动阅读可跳过间隔；只能打开公开页面、滚动和摘记，不点击、输入、登录或消费。读过不等于掌握技能。'},form,readTopic,read,dataTable(['时间','心得概要','证据状态','详情'],rows)));
  }
  if (phone) {
    computerState.textContent = `${phone.activity || '已连接'} · ${phone.size || ''}`;
    const shot = el('button', {type:'button', class:'button secondary small', text:'查看电脑截图'});
    shot.addEventListener('click', () => showComputerView());
    const pkg = el('input', {placeholder:'browser'});
    const launch = el('button', {type:'submit', class:'button secondary small', text:'启动应用'});
    const form = el('form', {class:'row'}, pkg, launch);
    form.addEventListener('submit', async (event) => {
      event.preventDefault(); busy(launch, true);
      try { await api('agent/computer/action', {method:'POST', body:{action:'launch', package:pkg.value.trim()}}); toast('已发送启动动作', 'ok'); }
      catch (error) { toast(errorText(error), 'error'); }
      finally { busy(launch, false); }
    });
    host.append(panel('虚拟电脑', {subtitle:'Linux 虚拟显示器与持久 Chromium 运行在独立节点，支持截图、点击、滚动和表单输入。'}, 
      computerState, shot, form));
  }
  if (social) {
    const body = el('div', {class:'stack'}, notice(social.reply_ready ? '评论回复已有真实发送核验记录；白名单内自动回复，仍核验发送结果。' : '评论回复尚未完成真实验收，首帖发布保持锁定。先在已有帖子中验证读取、审核、发送和核验。', social.reply_ready?'ok':'warn'));
    body.append(el('p',{class:'muted',text:'已登记 '+social.posts.length+' 个帖子；最多每5分钟检查一次，仅在获批电脑时间内执行；未知结果不会自动重发。'}));
    const external=input(''), title=input(''), account=input('');
    const register=el('form',{class:'stack agent-form'},field('已有帖子链接或ID',external),field('帖子完整可见标题',title),field('自己的账号显示名称',account));
    submit(register,'登记并启用评论监控',()=>api('agent/social/posts',{method:'POST',body:{platform:'xiaoheihe',external_id:external.value.trim(),title:title.value.trim(),account:account.value.trim(),enabled:true}}),ctx);
    const more=el('details',{class:'panel'},el('summary',{text:'登记评论测试或监控帖子'}),register);body.append(more);
    body.append(dataTable(['时间','回复状态','详情'],social.replies.map(r=>el('tr',null,el('td',{text:time(r.created)}),el('td',{text:r.state}),el('td',null,el('button',{type:'button',class:'button secondary xs',text:'查看详情',onclick:async()=>{
      try { const item=await api('agent/social/replies/'+r.id); modal({title:'评论回复详情',wide:true,body:el('pre',{class:'agent-result-text',text:JSON.stringify(item,null,2)})}); }
      catch(e){toast(errorText(e),'error');}
    }}))))));
    host.append(panel('小黑盒评论与回复', {subtitle:'新评论去重、白名单自动决策、同帖同账号核验后才记录成功。'},body));
  }
  const runtime = el('div'), history = el('div'), management = el('div', {class:'stack'});
  async function refreshRuntime(snapshot = null) {
    const current = snapshot || await api('agent/apps', {signal:ctx.signal});
    if (!snapshot) {
      const live = await Promise.allSettled([api('agent/computer',{signal:AbortSignal.timeout(7000)}), api('agent/computer/tasks')]);
      computerState.textContent = live[0].status==='fulfilled' ? `${live[0].value.activity || '已连接'} · ${live[0].value.size || ''}` : '电脑连接暂不可用';
      if (live[1].status==='fulfilled' && updateComputerTasks) updateComputerTasks(live[1].value.tasks || []);
    }
    runtime.replaceChildren(); history.replaceChildren();
    const nodeRows = current.nodes.map((n) => {
      const toggle = el('button', {type:'button', class:'button secondary xs', text:n.enabled ? '停用节点' : '启用节点'});
      toggle.addEventListener('click', async () => {
        busy(toggle, true);
        try { await api(`agent/nodes/${n.id}`, {method:'PUT', body:{enabled:!n.enabled}}); await ctx.rerender(); }
        catch(error) { toast(errorText(error), 'error'); }
        finally { busy(toggle, false); }
      });
      const active = current.browser_jobs.filter((j) => j.node_id === n.id && j.state === 'running');
      return el('tr', null, el('td', {text:n.name}),
        el('td', {text:!n.enabled ? '已停用' : Date.now()/1000-n.last_seen < 90 ? '在线' : '等待连接'}),
        el('td', {text:active.length ? `读取网页：${active[0].url}` : '空闲'}),
        el('td', {text:time(n.last_seen)}), el('td', {text:n.domains.join('、')}), el('td', null, toggle));
    });
    runtime.append(panel('执行节点实时状态', {subtitle:'每10秒刷新；只表示已接入执行器状态，不将规划或模拟显示为真实操作。'},
      dataTable(['节点','连接','当前行为','最近心跳','允许域名','操作'], nodeRows)));
    const entries = [
      ...current.browser_jobs.map((j) => ({...j, type:'browser', summary:j.summary, platform:new URL(j.url).hostname})),
      ...current.tasks.map((t) => ({...t, type:'task', created:t.created_at, platform:t.merchant})),
    ].sort((a,b) => b.created-a.created);
    history.append(panel('历史行为记录', {subtitle:'默认展示概要，点击详情单独读取正文、截图、任务参数和执行记录。'}, dataTable(
      ['时间','平台 / 应用','行为概要','状态','详情'], entries.map((j) => el('tr', null,
        el('td', {text:time(j.created)}), el('td', {text:j.platform}), el('td', {text:j.summary || '公开网页阅读'}),
        el('td', {text:states[j.state] || j.state}), el('td', null, el('button', {type:'button', class:'button secondary xs', text:'查看详情', onclick:()=>showBehavior(j)})))))));
  }
  if (play) {
    const playBody = el('div', {class:'stack'});
    if (play.status === 'idle') {
      const plan = el('input', {placeholder:'例：刷一会儿电脑，看看感兴趣的内容；继续上次没做完的事'});
      const duration = el('input', {type:'number', min:'5', max:'240', value:'20'});
      const ask = el('button', {type:'submit', class:'button secondary small', text:'向超管申请玩一会儿电脑'});
      const form = el('form', {class:'row'}, plan, duration, ask);
      form.addEventListener('submit', async (event) => {
        event.preventDefault(); busy(ask, true);
        try { await api('agent/computer/play/request', {method:'POST', body:{plan:plan.value.trim(), duration_minutes:Number(duration.value), reason:'自主想玩一会儿电脑'}}); toast('已发送电脑时间请求，等待超管批准', 'ok'); await ctx.rerender(); }
        catch (error) { toast(errorText(error), 'error'); }
        finally { busy(ask, false); }
      });
      playBody.append(form);
    } else if (play.status === 'pending') {
      const req = play.request || {};
      const approve = el('button', {type:'button', class:'button primary small', text:'批准'});
      const deny = el('button', {type:'button', class:'button ghost small', text:'拒绝'});
      approve.addEventListener('click', async () => { busy(approve,true); try { await api(`agent/computer/play/${encodeURIComponent(req.id)}/approve`, {method:'POST'}); await ctx.rerender(); } catch(error){toast(errorText(error),'error');} finally{busy(approve,false);} });
      deny.addEventListener('click', async () => { busy(deny,true); try { await api(`agent/computer/play/${encodeURIComponent(req.id)}/deny`, {method:'POST'}); await ctx.rerender(); } catch(error){toast(errorText(error),'error');} finally{busy(deny,false);} });
      playBody.append(el('p', {text:`请求：${req.plan || ''} · 预计 ${req.duration_minutes || '?'} 分钟`}), el('p', {class:'mono muted', text:req.id || ''}), el('div', {class:'row'}, approve, deny));
    } else if (play.status === 'active') {
      const ses = play.session || {};
      const end = el('button', {type:'button', class:'button ghost small', text:'结束电脑时间'});
      end.addEventListener('click', async () => { busy(end,true); try { await api('agent/computer/play/end', {method:'POST'}); await ctx.rerender(); } catch(error){toast(errorText(error),'error');} finally{busy(end,false);} });
      playBody.append(el('p', {text:`电脑时段已授权：${ses.plan || ''}`}), el('p', {class:'muted', text:`剩余约 ${play.remaining_minutes ?? '—'} 分钟 · 预计结束 ${new Date((ses.planned_end||0)*1000).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai'})}`}), end);
    }
    host.append(panel('电脑时间', {subtitle:'超管直接下达任务即可开始，无需再回一次“可以”；自主阅读使用上方独立预算。预计时长和计划可中途修改。'}, playBody));
  }

  if (phoneTasks) {
    const goal = el('input', {placeholder:'只给目标，例如：打开小黑盒官方网站，检查网页版登录和评论功能'});
    const max = el('input', {type:'number', min:'1', max:'60', value:'24'});
    const start = el('button', {type:'submit', class:'button primary small', text:'开始自主操作'});
    const form = el('form', {class:'row'}, goal, max, start);
    if ((!play || play.status !== 'active') && !autonomy?.quiet) form.prepend(notice('先申请并获批电脑时间，才能启动电脑任务。', 'info'));
    form.addEventListener('submit', async (event) => {
      event.preventDefault(); busy(start, true);
      try { await api('agent/computer/tasks', {method:'POST', body:{goal:goal.value.trim(), max_steps:Number(max.value)}}); toast('自主任务已启动', 'ok'); await ctx.rerender(); }
      catch (error) { toast(errorText(error), 'error'); }
      finally { busy(start, false); }
    });
    const taskTable = el('div');
    updateComputerTasks = (items) => {
    const taskRows = items.slice(0, 10).map((task) => {
      const resultState=task.verification==='unknown'?'结果待核实':task.status==='done'
        ?(task.verification==='excerpt_verified'?'已读并核对原文，心得未验证':task.verification==='platform_verified'?'已完成并核验':task.verification==='agent_reported'?'已结束，待独立核验':'已结束（历史记录）')
        :(states[task.status] || task.status);
      const cancel = el('button', {type:'button', class:'button ghost xs', text:'停止'});
      cancel.addEventListener('click', async () => {
        busy(cancel, true);
        try { await api(`agent/computer/tasks/${encodeURIComponent(task.id)}/cancel`, {method:'POST'}); await ctx.rerender(); }
        catch (error) { toast(errorText(error), 'error'); }
        finally { busy(cancel, false); }
      });
      return el('tr', null, el('td', null, el('strong', {text:(task.environment==='computer'?'电脑 · ':'旧手机记录 · ')+(task.goal || '').slice(0,70)+((task.goal || '').length>70?'…':'')})),
        el('td', {text:`${resultState} · ${task.step} 步`}), el('td', {text:(task.summary || task.error || (task.waiting_for_aid ? '等待你的协助' : '')).slice(0,120)+((task.summary || task.error || '').length>120?'…':'')}),
        el('td', null, el('button',{type:'button',class:'button secondary xs',text:'查看详情',onclick:async()=>{
          try { const item=await api('agent/computer/tasks/'+task.id); modal({title:'电脑行为详情',wide:true,body:el('pre',{class:'agent-result-text',text:JSON.stringify(item,null,2)})}); }
          catch(e){toast(errorText(e),'error');}
        }}), ...(['running','waiting_aid'].includes(task.status) ? [cancel] : [])));
    });
    taskTable.replaceChildren(dataTable(['目标', '状态', '结果 / 等待', '操作'], taskRows));
    };
    updateComputerTasks(phoneTasks.tasks || []);
    host.append(panel('自主电脑任务', {subtitle: phoneTasks.llm_ready ? '只给目标，Agent 自己观察、规划并操作；需要手机号/验证码/密码时会私聊找你。' : 'LLM 未配置，无法启动。'},
      form,
      taskTable));
  }

  if (aid?.items?.length) {
    const labels = {sms:'短信验证码', phone:'手机号',  payment:'支付/超额确认', tool:'工具能力确认', manual:'人工协助',password:'密码',registration:'注册与条款',authorization:'操作授权',publish:'发布审核'};
    const rows = aid.items.map((item) => {
      const field = el('input', {type: ['sms','phone','password'].includes(item.kind) ? 'password' : 'text', autocomplete: 'one-time-code', placeholder: item.kind === 'sms' ? '输入验证码' : '输入协助内容'});
      const send = el('button', {type:'button', class:'button secondary xs', text:'提交'});
      send.addEventListener('click', async () => {
        busy(send, true);
        try { await api(`agent/aid/${encodeURIComponent(item.id)}/respond`, {method:'POST', body:{value:field.value}}); field.value=''; toast('已收到，内容不会写入日志', 'ok'); await ctx.rerender(); }
        catch (error) { toast(errorText(error), 'error'); }
        finally { busy(send, false); }
      });
      const fill = el('button', {type:'button', class:'button ghost xs', text:'填入电脑'});
      fill.addEventListener('click', async () => {
        busy(fill, true);
        try {
          await api(`agent/aid/${encodeURIComponent(item.id)}/respond`, {method:'POST', body:{value:field.value}});
          await api(`agent/computer/aid/${encodeURIComponent(item.id)}`, {method:'POST'});
          field.value=''; toast('已填入电脑当前焦点，内容不会写入日志', 'ok'); await ctx.rerender();
        } catch (error) { toast(errorText(error), 'error'); }
        finally { busy(fill, false); }
      });
      return el('tr', null, el('td', {text:labels[item.kind] || item.kind}), el('td', null, el('strong', {text:item.title}), el('div', {class:'muted small', text:item.detail || ''})), el('td', {class:'mono muted', text:item.id}), el('td', null, field, send, ...((phone && ['sms','phone','password'].includes(item.kind)) ? [fill] : [])));
    });
    host.append(panel('等待你的协助', {subtitle:'验证码/手机号只在内存中短暂保存，提交后由执行器消费；不要发到群里。'}, dataTable(['类型','请求','ID','一次性填写'], rows)));
  }

  await refreshRuntime(data);
  host.append(runtime, history);
  let refreshing = false;
  ctx.interval(async () => {
    if (document.hidden || refreshing) return;
    refreshing = true;
    try { await refreshRuntime(); } catch (_) { /* keep the last known state */ }
    finally { refreshing = false; }
  }, 10000);
  const pauseButton = el('button', {type:'button', class:'button secondary small', text:data.policy.paused ? '恢复自主任务' : '暂停全部自主任务'});
  pauseButton.addEventListener('click', async () => {
    busy(pauseButton, true);
    try { await api('agent/policy', {method:'PUT', body:{...data.policy, paused:!data.policy.paused}}); await ctx.rerender(); }
    catch(error) { toast(errorText(error),'error'); }
    finally { busy(pauseButton,false); }
  });
  management.append(panel('全局运行开关', {subtitle:'影响全部自主任务。恢复运行不会自动连接账号或开通支付渠道。'}, pauseButton));
  const name = input('独立浏览器节点'), domains = input('example.com');
  const nodeForm = el('form', {class:'stack agent-form'}, field('节点名称',name), field('允许访问的精确域名（逗号分隔）',domains));
  const createNode = el('button', {type:'submit',class:'button secondary small',text:'创建节点凭据'});
  nodeForm.append(createNode);
  nodeForm.addEventListener('submit', async (event) => {
    event.preventDefault(); busy(createNode,true);
    try {
      const node = await api('agent/nodes',{method:'POST',body:{name:name.value,domains:domains.value.split(/[，,\s]+/).filter(Boolean)}});
      modal({title:'节点凭据（仅本次显示）',body:el('div',{class:'stack'},
        el('p',{text:'将凭据保存到独立节点配置文件，再在实时状态列表启用节点。'}),
        el('pre',{class:'agent-result-text',text:JSON.stringify({node_id:node.id,control_url:'http://127.0.0.1:19400',token:node.token},null,2)})),
        onClose:()=>ctx.rerender()});
    } catch(error) { toast(errorText(error),'error'); } finally {busy(createNode,false);}
  });
  management.append(panel('新增执行节点', {subtitle:'凭据只允许领取该节点的只读任务，不授予支付、审批或系统命令权限。'}, nodeForm));
  if(data.nodes.length) {
    const nid = choice(data.nodes.map((n)=>[n.id,n.name]),data.nodes[0].id), url = input('https://example.com');
    const form = el('form',{class:'stack agent-form'},field('执行节点',nid),field('公开网页URL',url));
    submit(form,'提交只读网页任务',()=>api('agent/browser-jobs',{method:'POST',body:{node_id:nid.value,url:url.value}}),ctx);
    management.append(panel('网页读取', {subtitle:'只允许HTTPS和节点白名单域名；不登录、不提交表单。'},form));
  }

  const grants = data.grants.map((g)=>el('tr',null,el('td',{text:g.capability}),el('td',{text:`${g.merchant} / ${g.recipient} / ${g.purpose}`}),el('td',{text:g.enabled?'已授权':'停用'})));
  management.append(panel('应用操作授权', {subtitle:'应用操作不获得付款权限；尚未接入的能力只能演练。'}, dataTable(['能力','范围','状态'],grants)));
  const platformRows = data.providers.map((p)=>el('tr',null,el('td',{text:p.label}),el('td',{text:p.note})));
  management.append(panel('平台接入进度', {},dataTable(['平台','进度'],platformRows)));
  const planRows = data.plans.map((p)=>el('tr',null,el('td',{text:p.goal}),el('td',{text:p.state}),el('td',null,
    el('button',{type:'button',class:'button secondary xs',text:'查看计划详情',onclick:async()=>{
      try {const plan=await api(`agent/plans/${p.id}`);modal({title:plan.goal,body:el('div',{class:'stack'},el('p',{text:plan.description}),...plan.steps.map((s,i)=>el('p',{text:`${i+1}. ${s.description}（${s.capability}，${s.amount_minor/100}元）`})))});}
      catch(error){toast(errorText(error),'error');}
    }}))));
  management.append(panel('行为计划概要', {subtitle:'草案只表示计划，不会被记为已执行。'},dataTable(['目标','状态','详情'],planRows)));
  const goal=input(''), platform=input(''), target=input(''), purpose=input('');
  const form=el('form',{class:'stack agent-form'},field('计划目标',goal),field('应用 / 平台',platform),field('账号标识',target),field('用途',purpose));
  submit(form,'创建应用计划草案',()=>api('agent/plans',{method:'POST',body:{goal:goal.value,description:'应用操作计划，不含支付',idempotency_key:newKey(),steps:[{capability:'app.use',mode:'simulation',merchant:platform.value,recipient:target.value,purpose:purpose.value,amount_minor:0,description:goal.value,depends_on:null}]}}),ctx);
  management.append(panel('新增应用计划', {subtitle:'执行需要单独的应用授权；支付步骤在支付管理中独立提交。'},form));
  host.append(el('details', {class:'panel agent-details'}, el('summary', {text:'管理节点、平台与行为计划'}), management));
}

function showComputerView() {
  let control=false, sending=false, point=null;
  const image=el('img',{class:'agent-screenshot',alt:'虚拟电脑实时画面'});
  image.draggable=false; image.style.touchAction='none';
  const state=el('p',{class:'muted',text:'查看模式。需要操作时点击“接管操作”；完成验证后在QQ回复继续。'});
  const take=el('button',{type:'button',class:'button secondary small',text:'接管操作'});
  const refresh=()=>{if(image.isConnected&&!sending&&!point)image.src=`/api/agent/computer/screenshot?t=${Date.now()}`;};
  const coords=(e)=>{const r=image.getBoundingClientRect();return [Math.max(0,Math.min(image.naturalWidth-1,Math.round((e.clientX-r.left)*image.naturalWidth/r.width))),Math.max(0,Math.min(image.naturalHeight-1,Math.round((e.clientY-r.top)*image.naturalHeight/r.height)))];};
  async function send(body) {
    if(!control||sending)return;
    sending=true;
    try {await api('agent/computer/manual/action',{method:'POST',body});state.textContent='已执行。完成验证后请在QQ回复继续。';}
    catch(error){state.textContent=errorText(error);toast(errorText(error),'error');}
    finally{sending=false;refresh();}
  }
  take.addEventListener('click',()=>{control=!control;take.textContent=control?'结束接管':'接管操作';image.style.cursor=control?'crosshair':'default';state.textContent=control?'可直接点击或拖动画面。Bot执行中会拒绝人工动作；等待人工协助时可以操作。':'已回到查看模式。';});
  image.addEventListener('pointerdown',(e)=>{if(!control||sending||!image.naturalWidth)return;e.preventDefault();point=coords(e);image.setPointerCapture(e.pointerId);});
  image.addEventListener('pointerup',(e)=>{if(!point)return;const start=point,end=coords(e);point=null;if(image.hasPointerCapture(e.pointerId))image.releasePointerCapture(e.pointerId);if(Math.hypot(end[0]-start[0],end[1]-start[1])>6)send({action:'drag',x1:start[0],y1:start[1],x2:end[0],y2:end[1]});else send({action:'tap',x:end[0],y:end[1]});});
  image.addEventListener('pointercancel',()=>{point=null;});
  const text=input('');text.type='password';text.autocomplete='off';text.placeholder='输入到当前焦点，提交后清空';
  const type=el('button',{type:'button',class:'button secondary small',text:'输入文字'});
  type.addEventListener('click',()=>{if(!control||sending)return;const value=text.value;text.value='';send({action:'text',text:value});});
  const escape=el('button',{type:'button',class:'button secondary small',text:'Esc'});escape.addEventListener('click',()=>send({action:'key',keycode:4}));
  modal({title:'虚拟电脑实时画面',wide:true,body:el('div',{class:'stack'},state,el('div',{class:'row'},take,escape),image,el('div',{class:'row'},text,type))});
  refresh();const timer=setInterval(()=>{if(!image.isConnected){clearInterval(timer);return;}refresh();},2000);
}

async function showBehavior(entry) {
  try {
    if(entry.type==='browser') {
      const job=await api(`agent/browser-jobs/${entry.id}`), r=job.result;
      modal({title:'APP 行为详情',wide:true,body:el('div',{class:'stack'},el('p',{text:`时间：${time(job.created)} · 状态：${states[job.state] || job.state}`}),el('p',{text:job.url}),
        r?el('div',{class:'stack'},el('h3',{text:r.title || '网页读取结果'}),el('p',{text:r.error || '公开网页只读，无表单提交、发帖或支付'}),el('div',{class:'agent-result-text',text:r.text}),r.has_screenshot?el('img',{class:'agent-screenshot',src:`/api/agent/browser-jobs/${job.id}/screenshot`,alt:'执行时网页截图'}):null):el('p',{text:'任务尚未返回执行结果。'}))});
    } else {
      const task=await api(`agent/task-detail/${entry.id}`);
      modal({title:'应用任务详情',wide:true,body:el('div',{class:'stack'},el('p',{text:task.description}),el('p',{text:`${task.merchant} / ${task.recipient} / ${task.purpose}`}),el('p',{text:`状态：${states[task.state] || task.state} · 环境：${task.mode}`}),el('p',{text:task.result?.message || '尚无执行结果'}),dataTable(['时间','动作'],task.audit.map((a)=>el('tr',null,el('td',{text:time(a.time)}),el('td',{text:a.action})))))});
    }
  }catch(error){toast(errorText(error),'error');}
}
