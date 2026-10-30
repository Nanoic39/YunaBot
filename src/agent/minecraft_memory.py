"""Persistent game goals and spatial evidence; model prose never advances milestones."""
import math
import time
import uuid
import hashlib
import json
from copy import deepcopy


def cell(observation):
    if not observation.get('connected') or observation.get('predicted_not_observed'):return None
    p=observation.get('position') or {}
    if not all(type(p.get(k)) in (int,float) and math.isfinite(p[k]) for k in ('x','y','z')):return None
    return str(observation.get('dimension') or 'overworld')+':'+str(math.floor(p['x']/4))+':'+str(math.floor(p['z']/4))


def validate_success(value):
    if not isinstance(value,dict):raise ValueError('里程碑验收条件无效')
    kind=value.get('kind');target=value.get('target')
    bounds={'explore':(2,30),'inventory':(1,16),'observe':(1,1),'visit':(1,8),'food':(10,20)}
    if kind not in bounds or type(target) not in (int,float) or not math.isfinite(target):raise ValueError('里程碑验收条件无效')
    if not bounds[kind][0]<=target<=bounds[kind][1] or (kind in ('visit','inventory','food') and int(target)!=target):raise ValueError('里程碑验收范围无效')
    if kind=='inventory' and (not isinstance(value.get('item'),str) or not 1<=len(value['item'])<=60):raise ValueError('目标物品无效')
    return deepcopy(value)


class ResumePlanError(ValueError):
    """A blocked plan was not unlocked; safe to ask the model to correct once."""


class GameMemory:
    def __init__(self,data,clock=time.time):
        self.data=data;self.clock=clock
        data.setdefault('experience',{});data.setdefault('learning_reviews',[]);data.setdefault('atlas',{});data.setdefault('projects',[]);data.setdefault('skill_evidence',{})
        if not data['atlas']:
            for run in data.get('runs',[]):
                for obs in (run.get('initial',{}),run.get('observation',{})):self.observe(obs)

    def active(self):
        return next((p for p in reversed(self.data['projects']) if p['status']=='active'),None)

    def milestone(self,row):
        p=next((p for p in self.data['projects'] if p['id']==row.get('project_id')),None)
        return (p,p['milestones'][row['milestone_index']]) if p else (None,None)

    def attach(self,plan,row,initial):
        current=self.active()
        if current and isinstance(plan.get('abandon_project'),str) and 6<=len(plan['abandon_project'])<=200:
            current.update(status='abandoned',reason=plan['abandon_project'],updated=self.clock());current=None
        resume=plan.get('resume_project')
        if resume and not current:
            candidate=next((p for p in self.data['projects'] if p['id']==resume and p['status']=='blocked'),None)
            revision=initial.get('movement_revision');reason=plan.get('resume_reason')
            if not candidate:raise ResumePlanError('恢复目标引用无效，必须原样使用blocked_projects中的id')
            if not isinstance(reason,str) or not 6<=len(reason)<=200:raise ResumePlanError('恢复原因必须是6到200字的字符串')
            if not isinstance(revision,str) or revision==candidate.get('blocked_revision','legacy'):raise ResumePlanError('恢复受阻计划的控制器版本没有改善，保持暂停')
            if revision in candidate.get('retry_revisions',[]):raise ResumePlanError('当前控制器版本已尝试恢复，保持暂停')
            if initial.get('health',0)<16 or initial.get('food',0)<14 or initial.get('nearby_hostiles'):raise ResumePlanError('恢复受阻计划的身体或威胁条件不安全，保持暂停')
            candidate.setdefault('retry_revisions',[]).append(revision)
            candidate.update(status='active',resumed_at=self.clock(),resume_reason=reason)
            for milestone in candidate['milestones']:
                if milestone['status']=='blocked':
                    milestone.setdefault('failure_history',[]).append({'rounds':milestone.get('failed_rounds',0),'last_error':milestone.get('last_error'),'revision':candidate.get('blocked_revision','legacy')})
                    milestone.update(status='in_progress',failed_rounds=0)
            current=candidate
        proposal=plan.get('project')
        if not current and proposal is not None:
            if not isinstance(proposal,dict) or not isinstance(proposal.get('title'),str) or not 2<=len(proposal['title'])<=160:raise ValueError('长期目标格式无效')
            milestones=proposal.get('milestones')
            if not isinstance(milestones,list) or not 2<=len(milestones)<=4:raise ValueError('长期目标需包含2到4个里程碑')
            rows=[]
            for m in milestones:
                if not isinstance(m,dict) or not isinstance(m.get('goal'),str) or not 2<=len(m['goal'])<=200:raise ValueError('里程碑目标无效')
                rows.append({'goal':m['goal'],'success':validate_success(m.get('success')),'status':'pending','visits':[],'distance':0})
            current={'id':uuid.uuid4().hex,'title':proposal['title'],'milestones':rows,'status':'active','created':self.clock(),'updated':self.clock()}
            self.data['projects'].append(current);self.data['projects']=self.data['projects'][-20:]
        if current:
            index=next(i for i,m in enumerate(current['milestones']) if m['status']!='done');m=current['milestones'][index]
            if 'baseline' not in m:
                m.update(baseline=deepcopy(initial),known_cells=list(self.data['atlas']),status='in_progress')
            row.update(project_id=current['id'],milestone_index=index,project_title=current['title'])
            row['starting_progress']={'new_cells':len(m['visits']),'distance':m['distance'],'food':initial.get('food',0),'inventory':sum(i['count'] for i in initial.get('inventory',[]) if i['name']==m['success'].get('item'))}
            plan={**plan,'goal':m['goal'],'success':deepcopy(m['success'])}
        validate_success(plan.get('success'))
        row['success']=deepcopy(plan['success'])
        return plan

    def observe(self,obs,row=None,distance=0):
        key=cell(obs)
        if not key:return
        previous=self.data['atlas'].get(key,{})
        self.data['atlas'][key]={'cell':key,'position':deepcopy(obs['position']),'first_seen':previous.get('first_seen',self.clock()),'last_seen':self.clock(),'blocks':list((obs.get('nearby_blocks') or {}).keys())[:20]}
        if len(self.data['atlas'])>1000:
            oldest=min(self.data['atlas'],key=lambda k:self.data['atlas'][k]['last_seen']);self.data['atlas'].pop(oldest)
        if row:
            _,m=self.milestone(row)
            if m:
                if key not in m.get('known_cells',[]) and key not in m['visits']:m['visits'].append(key)
                m['distance']+=distance
                row['progress']={'new_cells':len(m['visits']),'distance':round(m['distance'],2),'success':deepcopy(m['success'])}
        # Add provenance to candidate destinations; this is observed history, not a route guarantee.
        for target in obs.get('walkable_targets',[]):
            k=cell({'connected':True,'position':{**target,'y':target.get('y',0)},'dimension':obs.get('dimension')})
            target['visited_before']=k in self.data['atlas']
        for route in obs.get('route_candidates',[]):
            unknown=set()
            for target in route.get('waypoints',[]):
                k=cell({'connected':True,'position':target,'dimension':obs.get('dimension')})
                target['visited_before']=k in self.data['atlas']
                if k and k not in self.data['atlas']:unknown.add(k)
            route['unvisited_cells']=len(unknown)
        home=(obs.get('home') or {}).get('position');position=obs.get('position')
        if home and position:
            distance=lambda p:math.hypot(p['x']-home['x'],p['z']-home['z'])
            for target in obs.get('walkable_targets',[]):target['home_distance_gain']=round(distance(position)-distance(target),2)
            for route in obs.get('route_candidates',[]):
                if route.get('waypoints'):route['home_distance_gain']=round(distance(position)-distance(route['waypoints'][-1]),2)
        obs['visited_cells']=list(self.data['atlas'])[-80:]

    def verified(self,row,observation,initial,verify):
        _,m=self.milestone(row);goal=row.get('success',{})
        if goal.get('kind')=='visit':return bool(observation.get('connected') and m and len(m['visits'])>=goal['target'])
        if goal.get('kind')=='food':return bool(observation.get('connected') and (m['baseline'] if m else initial).get('food',20)<goal['target']<=observation.get('food',0))
        return verify(goal,observation,m['baseline'] if m else initial,m['distance'] if m else row['distance'])

    def finish(self,row):
        p,m=self.milestone(row)
        if not p:return
        p['updated']=self.clock();m['last_run']=row['id'];m['last_result']=row['status']
        if row['status']=='done':
            m.update(status='done',verified_at=self.clock(),verification='server_state')
            if all(x['status']=='done' for x in p['milestones']):p.update(status='done',completed=self.clock())
        else:
            m['last_error']=row.get('summary','')[:300]
            m['failed_steps']=[deepcopy(s) for s in row.get('steps',[]) if not s.get('ok')][-3:]
            begin=row.get('starting_progress') or {};kind=m['success']['kind']
            last=(row.get('observation') if (row.get('observation') or {}).get('connected') else row.get('last_live_observation')) or {}
            advanced=(kind=='visit' and len(m['visits'])>begin.get('new_cells',0)
                      or kind=='explore' and m['distance']>begin.get('distance',0)+.5
                      or kind=='food' and last.get('food',0)>begin.get('food',0)
                      or kind=='inventory' and sum(i['count'] for i in last.get('inventory',[]) if i['name']==m['success'].get('item'))>begin.get('inventory',0))
            if advanced:m['failed_rounds']=0
            elif row['status']!='cancelled':m['failed_rounds']=m.get('failed_rounds',0)+1
            if m.get('failed_rounds',0)>=3:p.update(status='blocked',reason='连续三轮没有目标进展，保留进度并暂停此计划',blocked_revision=(row.get('initial') or {}).get('movement_revision','legacy'));m['status']='blocked'

    def skill(self,action,result):
        if result.get('evidence')!='server_state':return
        before=result.get('before') or {};after=result.get('after') or {};verified=False
        if action in ('goto','route'):verified=result.get('distance_moved',0)>.2
        elif action=='rest':verified=after.get('health',0)>before.get('health',20)
        elif action=='eat':verified=after.get('food',0)>before.get('food',20)
        elif action=='craft':verified='inventory' in before and 'inventory' in after and before['inventory']!=after['inventory']
        elif action=='equip':verified=(before.get('held_item')!=after.get('held_item') and bool(after.get('held_item'))) or ('equipment' in before and 'equipment' in after and before['equipment']!=after['equipment'])
        if verified:
            entry=self.data['skill_evidence'].setdefault(action,{'verified_uses':0})
            entry.update(verified_uses=entry['verified_uses']+1,last_verified=self.clock(),status='observed_success_not_general_mastery')

    def learn(self,step,row):
        """Aggregate real attempts. Model prose cannot create successful experience."""
        action=step.get('action',{}).get('action')
        if not action:return
        before=step.get('before') or {};after=step.get('after') or {}
        vertical='unknown'
        if all(type(p.get('y')) in (int,float) for p in (before,after)):
            delta=after['y']-before['y'];vertical='up' if delta>.3 else 'down' if delta<-.3 else 'flat'
        observation=row.get('observation') or {};initial=row.get('initial') or {}
        condition={'action':action,'vertical':vertical,'dimension':observation.get('dimension',initial.get('dimension')),'revision':observation.get('movement_revision',initial.get('movement_revision')),'hurt':(step.get('health_before') if step.get('health_before') is not None else 20)<12,'near_threat':any(t.get('distance',99)<=7 for t in observation.get('nearby_hostiles',[]))}
        key=hashlib.sha256(json.dumps(condition,sort_keys=True).encode()).hexdigest()[:16]
        entry=self.data['experience'].setdefault(key,{'id':key,'condition':condition,'attempts':0,'confirmed_success':0,'observed_failure':0,'unconfirmed_failure':0,'action_ms_sum':0,'timed_attempts':0,'examples':[]})
        entry['attempts']+=1;entry['updated']=self.clock()
        confirmed=step.get('ok') and step.get('evidence')=='server_state'
        respawn=step.get('respawns_after',0)!=step.get('respawns_before',0)
        success=False
        if confirmed and not respawn:
            if action in ('goto','route'):success=step.get('distance',0)>.2
            elif action=='rest':success=step.get('health_after',0)>step.get('health_before',20)
            elif action=='eat':success=step.get('food_after',0)>step.get('food_before',20)
            elif action=='craft':success=step.get('inventory_changed',False)
            elif action=='equip':success=step.get('equipment_changed',False)
        if success:entry['confirmed_success']+=1
        elif not step.get('ok'):entry['observed_failure' if step.get('observed_failure') else 'unconfirmed_failure']+=1
        duration=(step.get('timing') or {}).get('action_ms')
        if type(duration) in (int,float) and math.isfinite(duration) and duration>=0:entry['action_ms_sum']+=duration;entry['timed_attempts']+=1
        entry['examples']=(entry['examples']+[{'run_id':row.get('id'),'before':before,'after':after,'success':bool(success),'error':str(step.get('error',''))[:180],'respawn_transition':respawn}])[-3:]
        if len(self.data['experience'])>80:
            oldest=min(self.data['experience'],key=lambda k:self.data['experience'][k]['updated']);self.data['experience'].pop(oldest)

    def experience_context(self):
        entries=sorted(self.data['experience'].values(),key=lambda e:e.get('updated',0),reverse=True)[:8]
        return [{k:e[k] for k in ('id','condition','attempts','confirmed_success','observed_failure','unconfirmed_failure','examples')}|{'mean_action_ms':round(e['action_ms_sum']/e['timed_attempts'],1) if e['timed_attempts'] else None,'scope':'observed_conditions_only_not_general_mastery'} for e in entries]

    def context(self):
        project=self.active()
        summary=None if not project else {k:v for k,v in project.items() if k!='milestones'} | {'milestones':[{k:m.get(k) for k in ('goal','success','status','distance','last_error','failed_steps','failed_rounds')} | {'new_cells':len(m['visits'])} for m in project['milestones']]}
        return {'experience':self.experience_context(),'learning_reviews':self.data.get('learning_reviews',[])[-4:],'active_project':summary,'blocked_projects':[{'id':p['id'],'title':p['title'],'reason':p.get('reason'),'blocked_revision':p.get('blocked_revision','legacy'),'retry_revisions':p.get('retry_revisions',[]),'failed_goals':[m['goal'] for m in p['milestones'] if m['status']=='blocked']} for p in self.data['projects'][-5:] if p['status']=='blocked'],'visited_cells':list(self.data['atlas'])[-80:],
                'skill_evidence':deepcopy(self.data['skill_evidence'])}
