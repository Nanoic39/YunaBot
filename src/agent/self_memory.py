"""Yuna 的统一自我记忆：跨场景（群聊 / 电脑 / 手机）唯一连续的记忆层。

人的记忆不分场景割裂——在群里、用电脑、玩手机的是同一个人。
这里存放「关于我自己」的记忆：
* facts   长期自我认知（我有什么能力、我在电脑上习惯用什么、我的规则）
* events  近期真实经历（什么时候用电脑做了什么、结果如何）

群聊侧注入这些内容（她知道自己有电脑、记得干过什么），
电脑/手机 Agent 侧同样注入（操作电脑的也是 Yuna 本人，带人设与记忆），
任务结束后把做过的事回写到这里，形成闭环。
"""

from __future__ import annotations

import json
import hashlib
from datetime import datetime,timezone,timedelta
import os
import threading
import time
from pathlib import Path
from src.agent.memory_retrieval import relevance

MAX_EVENTS = 300
EVENT_DAYS = 180

# 首次初始化写入的自我认知基线：能力与规则，可被管理面板/固化流程增改
DEFAULT_FACTS = [
    "我在获批时段可以使用 Linux 虚拟电脑和浏览器；具体能力以接入的执行器为准",
    "想玩电脑要先向哥哥申请「电脑时间」，批准后才开始，有时间限制",
    "在群里聊到我用电脑做过的事时，只说真实发生过的事，不编造没做过的经历",
]


class SelfMemory:
    def __init__(self, path: Path, clock=time.time):
        self.path = Path(path)
        self.clock = clock
        self.lock = threading.RLock()
        self._needs_save=False
        self.data = self._load()
        if self._needs_save:self._save()

    def _load(self) -> dict:
        data = {"version": 2, "facts": list(DEFAULT_FACTS), "events": [], "updated": 0, "activity":{}, "intentions":[], "reflections":[]}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                if isinstance(raw.get("facts"), list) and raw["facts"]:
                    data["facts"] = [str(f) for f in raw["facts"]][:60]
                if isinstance(raw.get("events"), list):
                    data["events"] = [e for e in raw["events"] if isinstance(e, dict)]
                data["updated"] = int(raw.get("updated") or 0)
                for key,typ in [('activity',dict),('intentions',list),('reflections',list)]:
                    if isinstance(raw.get(key),typ):data[key]=raw[key]
                legacy={'我还有一部手机可以用来装 App、聊天和发动态',
                        '我有一台自己的 Linux 电脑，带浏览器，可以上网查资料、看视频、逛网站、操作文件'}
                upgrade=raw.get('version',1)<2 or any(f in legacy for f in data['facts'])
                data['facts']=[f for f in data['facts'] if f not in legacy]
                if upgrade and DEFAULT_FACTS[0] not in data['facts']:data['facts'].insert(0,DEFAULT_FACTS[0])
                self._needs_save=upgrade
                for event in data['events']:
                    event.setdefault('id',hashlib.sha256(json.dumps(event,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20])
        except (OSError, ValueError):
            pass
        return data

    def _save(self) -> None:
        self.data["updated"] = int(self.clock())
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.chmod(0o600)
            tmp.replace(self.path)
        except OSError:
            pass  # 记忆写盘失败不阻断主流程

    def _trim(self) -> None:
        cutoff = self.clock() - EVENT_DAYS * 86400
        events = [e for e in self.data["events"] if float(e.get("time") or 0) >= cutoff]
        self.data["events"] = events[-MAX_EVENTS:]

    # ---------- 写入 ----------

    def record_event(self, scene: str, kind: str, text: str, goal: str = "", event_id: str = "") -> dict:
        """记录一段真实经历。scene: computer/phone/phone_play/chat；kind: task_done/task_failed/…"""
        event = {
            "time": int(self.clock()),
            "scene": str(scene or "")[:16],
            "kind": str(kind or "")[:16],
            "text": str(text or "").strip()[:300],
            "goal": str(goal or "").strip()[:200],
        }
        event['id']=event_id or hashlib.sha256((json.dumps(event,sort_keys=True,ensure_ascii=False)+str(time.time_ns())).encode()).hexdigest()[:20]
        if not event["text"]:
            return event
        with self.lock:
            existing=next((e for e in self.data['events'] if e.get('id')==event['id']),None)
            if existing:return dict(existing)
            self.data["events"].append(event)
            self._trim()
            self._save()
        return event

    def start_activity(self, task_id, goal, scene='computer', ttl=180):
        """只由真实执行器登记；意向不能当成已经完成的经历。"""
        now=self.clock()
        with self.lock:
            activity={'task_id':str(task_id),'goal':str(goal)[:300],'scene':scene,
                      'status':'running','updated':now,'expires':now+ttl,'source':'executor'}
            self.data['activity']=activity
            rows=[r for r in self.data['intentions'] if r.get('task_id')!=str(task_id)]
            rows.append({'task_id':str(task_id),'goal':str(goal)[:300],'status':'active','updated':now})
            self.data['intentions']=rows[-30:];self._save()

    def update_activity(self,task_id,status='running',ttl=180):
        if status not in ('running','waiting_aid'):raise ValueError('未知活动状态')
        with self.lock:
            activity=self.data['activity']
            if activity.get('task_id')!=str(task_id):return False
            activity.update(status=status,updated=self.clock(),expires=self.clock()+ttl)
            self._save();return True

    def finish_activity(self,task_id,status,summary=''):
        if status not in ('done','reported','failed','cancelled','interrupted','unknown'):raise ValueError('未知结束状态')
        with self.lock:
            for row in self.data['intentions']:
                if row.get('task_id')==str(task_id):row.update(status=status,updated=self.clock(),result=str(summary)[:200])
            if self.data['activity'].get('task_id')==str(task_id):self.data['activity']={}
            self._save()

    def add_reflection(self,text,evidence_ids):
        """心得是带来源的待检验推测，不能写成确定能力或永久事实。"""
        text=str(text).strip()
        if not text or len(text)>200 or not isinstance(evidence_ids,list) or not evidence_ids:return None
        with self.lock:
            known={e.get('id') for e in self.data['events']}
            if any(not isinstance(i,str) or i not in known for i in evidence_ids):return None
            if any(r.get('text')==text for r in self.data['reflections']):return None
            row={'text':text,'evidence_ids':list(dict.fromkeys(evidence_ids))[:8],
                 'status':'hypothesis','created':self.clock()}
            self.data['reflections'].append(row);self.data['reflections']=self.data['reflections'][-30:]
            self._save();return dict(row)

    def continuity_text(self,max_chars=900,include_history=True):
        snapshot=self.snapshot();activity=snapshot['activity'];parts=[]
        if activity:
            label={'running':'正在执行','waiting_aid':'等待超管答复，尚未完成','unknown':'最近状态已过期，需重新观察，不能声称仍在执行'}.get(activity['status'],activity['status'])
            parts.append('当前真实活动：'+label+'；目标：'+activity['goal'][:120])
        else:parts.append('当前没有执行器登记的活动；不要凭记忆声称正在操作设备。')
        recent=snapshot['intentions'][-2:] if include_history else []
        labels={'active':'进行中','done':'已核验完成','reported':'已汇报结束，待独立核验','failed':'失败，未完成','cancelled':'已取消','interrupted':'已中断','unknown':'待核实'}
        if recent:parts.append('目标进展（计划不等于完成）：'+'；'.join(
            r.get('goal','')[:100]+'：'+labels.get(r.get('status'),str(r.get('status')))+'；'+r.get('result','')[:80] for r in recent))
        reflections=snapshot['reflections'][-2:] if include_history else []
        if reflections:parts.append('待检验心得（只是推测，不是既定事实）：'+'；'.join(r['text'] for r in reflections))
        return '\n'.join(parts)[:max_chars]

    def add_fact(self, text: str, source: str = "manual") -> dict | None:
        text = str(text or "").strip()
        if not text or len(text) > 200:
            return None
        with self.lock:
            if any(str(f) == text for f in self.data["facts"]):
                return None
            self.data["facts"].append(text)
            self.data["facts"] = self.data["facts"][-60:]
            self._save()
        return {"fact": text, "source": source}

    def remove_fact(self, text: str) -> bool:
        with self.lock:
            before = len(self.data["facts"])
            self.data["facts"] = [f for f in self.data["facts"] if str(f) != str(text)]
            removed = before != len(self.data["facts"])
            if removed:
                self._save()
            return removed

    # ---------- 读取（供提示词注入） ----------

    def recent_events(self, days: int = 3, scene: str = "", limit: int = 10) -> list[dict]:
        cutoff = self.clock() - max(1, int(days)) * 86400
        with self.lock:
            rows = [dict(e) for e in self.data["events"] if float(e.get("time") or 0) >= cutoff]
        if scene:
            rows = [e for e in rows if not scene or e.get("scene") == scene]
        return rows[-max(1, min(50, limit)):]

    def facts_text(self, max_chars: int = 400) -> str:
        with self.lock:
            facts = [str(f) for f in self.data["facts"]]
        text = "；".join(facts)
        return text[:max_chars]

    def events_text(self, days: int = 3, limit: int = 6, max_chars: int = 500, query: str | None = None, event_ids: list[str] | None = None) -> str:
        """query=None维持执行器的最近经历模式；显式查询只返回相关记录。"""
        limit = max(0, min(50, int(limit)))
        if not limit or max_chars <= 0 or event_ids == [] or (event_ids is None and query is not None and not query.strip()):
            return ''
        if event_ids is not None:
            cutoff = self.clock() - max(1, int(days)) * 86400
            with self.lock:
                available = {e.get('id'):dict(e) for e in self.data['events'] if float(e.get('time') or 0) >= cutoff}
            rows = [available[i] for i in dict.fromkeys(event_ids) if i in available]
        elif query is None:
            rows = self.recent_events(days=days, limit=limit)
        else:
            cutoff = self.clock() - max(1, int(days)) * 86400
            with self.lock:
                rows = [dict(e) for e in self.data['events'] if float(e.get('time') or 0) >= cutoff]
            rows = [e for e in rows if relevance(e.get('text',''),query) >= 0.15]
            rows.sort(key=lambda e:(relevance(e.get('text',''),query),float(e.get('time') or 0)),reverse=True)
        lines = []
        for event in rows:
            stamp = datetime.fromtimestamp(float(event.get("time") or 0),timezone(timedelta(hours=8))).strftime("%m-%d %H:%M")
            label = {"computer": "电脑", "phone": "手机", "phone_play": "电脑时间", "chat": "群聊"}.get(
                event.get("scene"), event.get("scene") or "")
            stage={'self_decide':'意向/决定，不代表完成','task_failed':'未完成','task_unknown':'结果待核实','task_done':'已核验的执行记录','task_reported':'执行器汇报，尚未独立核验'}.get(event.get('kind'),'记录')
            line = f"[{stamp}][{label}][{stage}] {event.get('text') or ''}"
            if len(line) + sum(len(item)+1 for item in lines) > max_chars:
                continue
            if line not in lines:
                lines.append(line)
            if len(lines) >= limit:
                break
        return "\n".join(lines)

    # ---------- 面板 ----------

    def snapshot(self) -> dict:
        with self.lock:
            activity=dict(self.data['activity'])
            if activity and float(activity.get('expires',0))<=self.clock():activity['status']='unknown'
            intentions=[dict(r) for r in self.data['intentions']]
            if activity.get('status')=='unknown':
                for row in intentions:
                    if row.get('task_id')==activity.get('task_id') and row.get('status')=='active':row['status']='unknown'
            return {
                'activity':activity,'intentions':intentions,
                'reflections':[dict(r) for r in self.data['reflections']],
                "facts": list(self.data["facts"]),
                "events": list(self.data["events"])[-50:],
                "updated": int(self.data["updated"] or 0),
                "total_events": len(self.data["events"]),
            }


self_memory = SelfMemory(Path(os.getenv("SELF_MEMORY_FILE") or "data/agent/self_memory.json"))
if not self_memory.path.exists():
    # 首次运行落一份基线（能力认知），之后由事件回写与固化流程维护
    try:
        self_memory._save()
    except Exception:
        pass
