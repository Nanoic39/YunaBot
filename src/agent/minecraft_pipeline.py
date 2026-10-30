"""Speculative short action queues. Predictions never count as executed evidence."""
import asyncio
import math
import time
from copy import deepcopy


class PipelineError(ValueError):pass


def compact(obs):
    keys=('connected','position','dimension','health','food','respawns','deaths','respawning','home','nearby_hostiles','inventory','safe_food','craftable','held_item','walkable_targets','route_candidates','local_standable','nearby_blocks','capabilities','movement_revision')
    return {k:deepcopy(obs[k]) for k in keys if k in obs}


def destination(action):
    if action.get('action')=='goto':return {k:action.get(k) for k in ('x','y','z')}
    points=action.get('waypoints')
    if action.get('action')=='route' and isinstance(points,list) and points:return points[-1]
    return None


def finite_point(p):return isinstance(p,dict) and all(type(p.get(k)) in (int,float) and math.isfinite(p[k]) for k in ('x','y','z'))


def predict(obs,action):
    result=compact(obs);point=destination(action)
    if point:
        if not finite_point(point):raise PipelineError('预案坐标无效')
        result['position']={k:point[k] for k in ('x','y','z')}
        known=obs.get('local_standable') or obs.get('walkable_targets',[])
        result['walkable_targets']=[p for p in known if finite_point(p) and .3<=math.hypot(p['x']-point['x'],p['z']-point['z'])<=1.45 and abs(p['y']-point['y'])<=1]
        result['route_candidates']=[]
    result['predicted_not_observed']=True
    return result


def valid(expected,actual,action):
    if not actual.get('connected') or actual.get('respawning'):return False
    for key in ('dimension','respawns','deaths'):
        if expected.get(key)!=actual.get(key):return False
    a,b=expected.get('position'),actual.get('position')
    if not finite_point(a) or not finite_point(b) or math.hypot(a['x']-b['x'],a['z']-b['z'])>.65 or abs(a['y']-b['y'])>.6:return False
    if actual.get('health',20)<expected.get('health',20)-.5 or abs(actual.get('food',20)-expected.get('food',20))>2:return False
    if expected.get('inventory')!=actual.get('inventory'):return False
    if any(t.get('distance',99)<=7 for t in actual.get('nearby_hostiles',[])):return False
    if action.get('action') in ('goto','route'):
        points=action.get('waypoints') if action['action']=='route' else [destination(action)]
        if not isinstance(points,list) or not 1<=len(points)<=4:return False
        known=actual.get('local_standable') or actual.get('walkable_targets',[])+[p for r in actual.get('route_candidates',[]) for p in r.get('waypoints',[])]
        previous=b
        for point in points:
            if not finite_point(point) or math.hypot(point['x']-previous['x'],point['z']-previous['z'])>1.6 or abs(point['y']-previous['y'])>1.05:return False
            if not any(finite_point(k) and all(abs(k[x]-point[x])<.05 for x in ('x','y','z')) for k in known):return False
            previous=point
    return True


class ActionPipeline:
    def __init__(self,decide,prompt_context,stamp=lambda:'',clock=time.monotonic):
        self.decide=decide;self.prompt_context=prompt_context;self.stamp=stamp;self.clock=clock
        self.queue=[];self.pending=None;self.last_end=None;self.last_timing={}
        self.metrics={'prefetch_started':0,'prefetched_used':0,'buffered_used':0,'discarded':0,'decision_wait_ms':0,'actions':0,'action_ms':0,'idle_ms':0}

    async def invalidate(self,reason):
        self.metrics['discarded']+=len(self.queue)+int(self.pending is not None)
        self.metrics['last_invalidation']=reason;self.queue=[]
        if self.pending:
            self.pending.cancel()
            try:await self.pending
            except (asyncio.CancelledError,Exception):pass
            self.pending=None

    def _enqueue(self,raw,obs,stamp,speculative=False):
        actions=raw.get('actions') if isinstance(raw,dict) else None
        if actions is None:actions=[raw]
        if not isinstance(actions,list) or not 1<=len(actions)<=3:raise PipelineError('动作缓冲需包含1到3个动作')
        expected=deepcopy(obs);queued=[]
        for action in actions:
            if not isinstance(action,dict) or action.get('action') not in ('goto','route','rest','wait','look','eat','equip','craft','observe','finish'):raise PipelineError('预案动作无效')
            if speculative and action['action']=='finish':break
            queued.append((deepcopy(action),expected,stamp,speculative))
            # Inventory changes cannot be reliably predicted; do not pre-queue dependent actions.
            if action['action'] in ('eat','equip','craft'):break
            expected=predict(expected,action)
        self.queue.extend(queued)

    async def choose(self,prompt,obs):
        start=self.clock()
        threats=[t for t in obs.get('nearby_hostiles',[]) if t.get('distance',99)<=5 and finite_point(t.get('position'))]
        if threats:
            candidates=[]
            for point in obs.get('walkable_targets',[]):
                if not finite_point(point):continue
                distances=[math.dist([point[k] for k in ('x','y','z')],[t['position'][k] for k in ('x','y','z')]) for t in threats]
                if all(distance>t['distance']+.15 for distance,t in zip(distances,threats)):candidates.append((min(distances),point))
            if candidates:
                await self.invalidate('近处威胁触发已验证落脚点的快速避险')
                point=max(candidates,key=lambda p:p[0])[1]
                self.metrics['reflex_actions']=self.metrics.get('reflex_actions',0)+1
                self.last_timing={'decision_wait_ms':0,'source':'survival_reflex'}
                return {'action':'goto',**{k:point[k] for k in ('x','y','z')},'reason':'按实际邻近落脚点远离逼近威胁'}
        if not self.queue and self.pending:
            task=self.pending;self.pending=None
            try:
                raw,expected,stamp=await task
                self._enqueue(raw,expected,stamp,True)
            except asyncio.CancelledError:raise
            except Exception:self.metrics['discarded']+=1
        if self.queue:
            action,expected,stamp,speculative=self.queue[0]
            if stamp!=self.stamp() or not valid(expected,obs,action):await self.invalidate('实际位置、生命、地形、物品、威胁或聊天约束已变化')
            else:
                self.queue.pop(0);self.metrics['prefetched_used' if speculative else 'buffered_used']+=1
                wait=(self.clock()-start)*1000;self.metrics['decision_wait_ms']+=wait
                self.last_timing={'decision_wait_ms':wait,'source':'prefetch' if speculative else 'buffer'}
                return action
        raw=await self.decide(prompt+'\n为减少停顿，可输出{"actions":[动作1,动作2,动作3]}提前给1到3个动作，也兼容单个动作JSON。动作总数没有上限，这只是短期缓冲深度。只规划已加载地形，不能假设预测已执行。')
        self._enqueue(raw,obs,self.stamp())
        action=self.queue.pop(0)[0]
        wait=(self.clock()-start)*1000;self.metrics['decision_wait_ms']+=wait
        self.last_timing={'decision_wait_ms':wait,'source':'model'}
        return action

    async def execute(self,action,obs,call):
        # Refill while current I/O/physics is running, using a clearly labelled prediction.
        if self.pending is None and not self.queue and not any(t.get('distance',99)<=7 for t in obs.get('nearby_hostiles',[])) and action.get('action') in ('goto','route','rest','wait'):
            expected=predict(obs,action);stamp=self.stamp()
            async def plan():
                raw=await self.decide(self.prompt_context(expected)+'\n这是执行中的后续预案：position是预计终点，不是已经到达；仅可使用local_standable里已加载的坐标。输出{"actions":[至多3个后续动作]}，goto的x/y/z必须为顶层数字，route使用waypoints数组。单步<=1.45格，高差<=1格，每条route<=4点。不要重复正在执行的动作、不要finish，不猜测未加载地形；程序会在真实到达后重新校验。')
                return raw,expected,stamp
            self.pending=asyncio.create_task(plan());self.metrics['prefetch_started']+=1
        started=self.clock();idle=0 if self.last_end is None else (started-self.last_end)*1000
        try:return await call('/action',action)
        except BaseException:
            await self.invalidate('实际执行失败，取消所有预测');raise
        finally:
            elapsed=(self.clock()-started)*1000;self.last_end=self.clock()
            self.last_timing.update(action_ms=elapsed,idle_before_ms=idle)
            self.metrics['action_ms']+=elapsed;self.metrics['idle_ms']+=idle;self.metrics['actions']+=1

    async def close(self):
        if self.pending:
            self.pending.cancel()
            try:await self.pending
            except (asyncio.CancelledError,Exception):pass
            self.pending=None
        self.queue=[]
