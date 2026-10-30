"""One continuous life projection shared by QQ presence and chat; verified status only."""
import asyncio,time
from .qq_bridge import is_snowluma

def desired_presence(life,activity=None):
 profile=life.get('activity_profile') or {};name=str(life.get('activity') or '')
 actual=activity or {}
 if actual.get('status')=='running':return (50,0,'正在使用电脑处理事情')
 if profile.get('kind')=='sleep':return (10,1016,'睡觉中')
 if profile.get('kind')=='outside':return (30,0,'离开')
 if '使用电脑' in name or '玩手机' in name:return (50,0,'忙碌')
 return (10,0,'在线')

class PresenceSync:
 def __init__(self,clock=time.time):self.clock=clock;self.states={};self.locks={}
 async def sync(self,bot,life,activity=None):
  account=str(bot.self_id);state=self.states.setdefault(account,{})
  async with self.locks.setdefault(account,asyncio.Lock()):
   now=self.clock();target=desired_presence(life,activity)
   if now<state.get('retry_at',0):
    # QQ may acknowledge the write before its read model catches up. One
    # deferred read verifies delivery without repeating the status write.
    if state.get('pending_verify') and now>=state.get('verify_after',0):
     state['pending_verify']=False
     try:
      observed=await asyncio.wait_for(bot.call_api('nc_get_user_status',user_id=int(account)),6)
      verified=(int(observed['status']),int(observed['ext_status']))==target[:2]
      state.update(observed=observed,checked=now,target=target,verified=verified)
      if verified:state['retry_at']=0
     except Exception as e:state['error']=type(e).__name__
    return state
   if state.get('target')==target and now-state.get('checked',0)<300:return state
   if now-state.get('attempted',0)<120:return state
   if not await is_snowluma(bot):return state
   state.update(attempted=now,target=target,verified=False)
   try:
    observed=await asyncio.wait_for(bot.call_api('nc_get_user_status',user_id=int(account)),6)
    if (int(observed['status']),int(observed['ext_status']))!=target[:2]:
     await asyncio.wait_for(bot.call_api('set_online_status',status=target[0],ext_status=target[1],battery_status=0),8)
     observed=await asyncio.wait_for(bot.call_api('nc_get_user_status',user_id=int(account)),6)
    state.update(observed=observed,checked=now,verified=(int(observed['status']),int(observed['ext_status']))==target[:2])
    if not state['verified']:state.update(retry_at=now+600,pending_verify=True,verify_after=now+20)
   except Exception as e:state.update(error=type(e).__name__,retry_at=now+600)
   return state
 def prompt(self):
  current=[s for s in self.states.values() if s.get('checked') and self.clock()-s['checked']<=360]
  if not current:return 'QQ展示状态尚未核实，不要声称已切换在线状态。'
  s=current[0]
  return ('QQ展示状态已核对：'+s['target'][2]+'。实际行为仍以当前执行状态和生活上下文为准，展示状态不是完成事情的证据。') if s.get('verified') else 'QQ展示状态与生活状态尚未同步，不能声称切换成功。'
presence=PresenceSync()
