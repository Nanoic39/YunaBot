"""Group-scoped game meetups. Only the actual sender may join/leave."""
from contextlib import contextmanager
import json,sqlite3,time,math,re
from pathlib import Path
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo

JOIN_RE=re.compile(r'^(?:我也(?:来|去|要|想|算|加入|参加)?(?:一个|个)?|我来(?:一个|个)?|加我(?:一个|个)?|算我(?:一个|个)?|带我一个|我加入|我参加|报名)(?:[~～!！。，,\s]*)$')
LEAVE_RE=re.compile(r'^(?:那)?(?:我)?(?:也)?(?:先)?\s*(?:退出|退|不打了|不玩了|下车|退赛|退坑|撤了?)(?:一下|了|啦|吧|呗|呀|啊|[~～!！。，,\s])*$')
WAITLIST_RE=re.compile(r'^(?:给(?:这局|本局)?|这局|本局)?\s*(?:开|加|增加|设置|留|放)\s*(\d{1,2})\s*个?\s*(?:候补|替补)(?:位)?(?:吧|呗|呀|啊|谢谢|。|！|!|~|～|\s)*$')
JOIN_WAITLIST_RE=re.compile(r'^(?:我|我要|我想|让我|给我|先|就)?\s*(?:(?:上|进|排|当|做)(?:一个|个)?|(?:一个|个))?\s*(?:候补|替补)(?:位)?(?:吧|呗|呀|啊|谢谢|。|！|!|~|～|\s)*$')
WAITLIST_RE2=re.compile(r'^(?:给(?:这局|本局)?|这局|本局)?\s*(?:候补|替补)(?:位)?\s*(?:开|加|增加|设置|留|放)?\s*(\d{1,2})\s*个?(?:吧|呗|呀|啊|谢谢|。|！|!|~|～|\s)*$')
WAITLIST_RE3=re.compile(r'^(?:给(?:这局|本局)?|这局|本局)?\s*(\d{1,2})\s*个?\s*(?:候补|替补)(?:位)?(?:吧|呗|呀|啊|谢谢|。|！|!|~|～|\s)*$')

def short_join_phrase(text):
 return bool(JOIN_RE.fullmatch(str(text or '').strip()))

def short_leave_phrase(text):
 return bool(LEAVE_RE.fullmatch(str(text or '').strip()))

def short_join_waitlist_phrase(text):
 return bool(JOIN_WAITLIST_RE.fullmatch(str(text or '').strip()))

def parse_waitlist_phrase(text):
 text=str(text or '').strip()
 m=WAITLIST_RE.fullmatch(text) or WAITLIST_RE2.fullmatch(text) or WAITLIST_RE3.fullmatch(text)
 if not m:return None
 try:n=int(m.group(1))
 except (TypeError,ValueError):return None
 return n if 0<=n<=20 else None

TRIGGER_REL_RE=re.compile(r'(\d{1,2})\s*(?:[:：点时])\s*(\d{1,2})?\s*(?:分)?\s*(之后|以后|后|之前|以前|前)')
TRIGGER_PEOPLE_RE=re.compile(r'(\d{1,2})\s*人')

def _trigger_parts(match):
 hour=int(match.group(1));minute=int(match.group(2) or 0)
 if not 0<=hour<=23 or not 0<=minute<=59:return None
 when='after' if match.group(3) in ('之后','以后','后') else 'before'
 return when,f'{hour:02d}:{minute:02d}'

def parse_trigger_rules(text):
 '''Best-effort parser for “21点后4人以上就开 / 21点前4人开，否则4人21点后开”。'''
 raw=str(text or '').strip()
 if not raw:return []
 parts=re.split(r'否则|不然',raw,maxsplit=1)
 pre=parts[0];fallback=parts[1] if len(parts)>1 else ''
 rules=[]
 def collect(part):
  found=[];matches=list(TRIGGER_REL_RE.finditer(part))
  for i,m in enumerate(matches):
   parsed=_trigger_parts(m)
   if not parsed:continue
   when,time_value=parsed
   tail=part[m.end():matches[i+1].start() if i+1<len(matches) else len(part)]
   pm=TRIGGER_PEOPLE_RE.search(tail)
   found.append({'when':when,'time':time_value,'min_players':int(pm.group(1)) if pm else 0})
  return found
 rules.extend(collect(pre))
 if fallback:
  fallback_rules=collect(fallback)
  if fallback_rules:
   pm=TRIGGER_PEOPLE_RE.search(fallback)
   if pm and not any(TRIGGER_PEOPLE_RE.search(fallback[m.end():]) for m in TRIGGER_REL_RE.finditer(fallback)):
    fallback_rules[0]['min_players']=int(pm.group(1))
   rules.extend(fallback_rules)
  elif rules:
   pm=TRIGGER_PEOPLE_RE.search(fallback)
   rules.append({'when':'after','time':rules[-1]['time'],'min_players':int(pm.group(1)) if pm else 0})
 # de-dupe, keep stable order
 out=[];seen=set()
 for rule in rules:
  key=(rule['when'],rule['time'],rule['min_players'])
  if key not in seen:seen.add(key);out.append(rule)
 return out[:12]

def game_phase(row,now=None):
 now=time.time() if now is None else now
 if str(row.get('state'))=='cancelled':return 'cancelled'
 if str(row.get('state'))=='ended':return 'ended'
 return 'signup' if float(row.get('start') or 0)>now else 'active'

def phase_name(row,now=None):
 return {'signup':'正在报名','active':'正在进行','ended':'已结束','cancelled':'已取消'}.get(game_phase(row,now),'—')

def _same_game_name(a: str, b: str) -> bool:
    """判断两个游戏名是否指同一款（含 L4D2/求生之路别名）。"""
    aa=re.sub(r'[\s@/、,，|·\-—_]+','',str(a or '').lower())
    bb=re.sub(r'[\s@/、,，|·\-—_]+','',str(b or '').lower())
    if not aa or not bb:return False
    if aa==bb or aa in bb or bb in aa:return True
    aliases=('l4d2','l4d','left4dead','leftdead','求生之路')
    return any(x in aa for x in aliases) and any(x in bb for x in aliases)


_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def _cn_number(value: str) -> int | None:
    value = str(value or "").strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    if value in _CN_DIGITS:
        return _CN_DIGITS[value]
    if len(value) == 2 and value[1] == "十" and value[0] in _CN_DIGITS:
        return _CN_DIGITS[value[0]] * 10
    if len(value) == 2 and value[0] == "十" and value[1] in _CN_DIGITS:
        return 10 + _CN_DIGITS[value[1]]
    if len(value) == 3 and value[0] in _CN_DIGITS and value[1] == "十" and value[2] in _CN_DIGITS:
        return _CN_DIGITS[value[0]] * 10 + _CN_DIGITS[value[2]]
    if len(value) == 2 and value[0] == "零" and value[1] in _CN_DIGITS:
        return _CN_DIGITS[value[1]]
    return None


def parse_explicit_start(text, now=None):
    """明确写了“今晚九点/九点半/21:00”时，以原文为准，避免模型把分钟算错。"""
    raw = str(text or "")
    if re.search(r"明晚|明天|次日|后天", raw):
        return None
    m = re.search(
        r"(?:(今|明)?(?:天)?(?:晚上|晚|今)?\s*)?"
        r"(上午|早上|早晨|下午|傍晚|晚上|晚)?\s*"
        r"([零〇一二两三四五六七八九十]{1,3}|\d{1,2})\s*(?:点|时|:)\s*"
        r"(半|一刻|三刻|[零〇一二两三四五六七八九十]{1,3}|\d{1,2})?\s*(?:分)?",
        raw,
    )
    if not m:
        return None
    prefix, period, hour_raw, minute_raw = m.group(1), m.group(2), m.group(3), m.group(4)
    hour = _cn_number(hour_raw)
    if hour is None or not 0 <= hour <= 23:
        return None
    # “九点”在晚间语境按 21:00；明确上午/早上不转。
    evening_context = bool(period in {"下午", "傍晚", "晚上", "晚"}) or bool(
        not period and re.search(r"今晚|今天晚上|晚上|晚", raw)
    )
    if period in {"下午", "傍晚", "晚上", "晚"} and hour < 12:
        hour += 12
    elif not period and evening_context and 1 <= hour <= 11:
        hour += 12
    elif not period and not evening_context and 1 <= hour <= 11:
        try:
            cur = datetime.fromtimestamp(float(now or time.time()), ZoneInfo("Asia/Shanghai"))
        except Exception:
            cur = datetime.now(ZoneInfo("Asia/Shanghai"))
        if cur.hour >= 17:
            hour += 12
    minute = 0
    if minute_raw:
        if minute_raw == "半":
            minute = 30
        elif minute_raw == "一刻":
            minute = 15
        elif minute_raw == "三刻":
            minute = 45
        else:
            value = _cn_number(minute_raw)
            if value is not None:
                minute = value
    if not 0 <= minute <= 59:
        return None
    try:
        tz = ZoneInfo("Asia/Shanghai")
        cur = datetime.fromtimestamp(float(now or time.time()), tz)
        target = cur.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return target.timestamp()
    except Exception:
        return None


def normalize_same_evening_start(start, text, now=None):
    """把“今晚八点”被模型误算成明天时拉回今天；明确说明天/明晚时不动。"""
    now=time.time() if now is None else float(now)
    explicit=parse_explicit_start(text,now)
    if explicit is not None:
        return explicit
    raw=str(text or '')
    try:start=float(start)
    except (TypeError,ValueError):return start
    if re.search(r'明晚|明天|次日|后天',raw):return start
    if not re.search(r'今晚|今天晚上|今夜|晚上|今天.{0,4}(?:点|时)',raw):return start
    try:
        tz=ZoneInfo('Asia/Shanghai')
        cur=datetime.fromtimestamp(now,tz);target=datetime.fromtimestamp(start,tz)
        if target.date()==cur.date()+timedelta(days=1):
            candidate=start-86400
            if candidate>now+60:return candidate
    except (TypeError,ValueError,OSError):pass
    return start


class GameStore:
 def __init__(self,path,clock=time.time):
  self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.clock=clock
  with self.db() as d:
   d.executescript('''CREATE TABLE IF NOT EXISTS games(id INTEGER PRIMARY KEY,account TEXT,gid TEXT,host TEXT,game TEXT,start REAL,capacity INTEGER,waitlist_capacity INTEGER DEFAULT 0,state TEXT DEFAULT 'open',reminded INTEGER DEFAULT 0,trigger_rules TEXT DEFAULT '[]',triggered INTEGER DEFAULT 0,triggered_at REAL DEFAULT 0,triggered_text TEXT DEFAULT '',short_triggered INTEGER DEFAULT 0,short_triggered_at REAL DEFAULT 0,short_triggered_text TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS members(game_id INTEGER,qq TEXT,name TEXT,status TEXT DEFAULT 'formal',PRIMARY KEY(game_id,qq));
CREATE TABLE IF NOT EXISTS game_notes(game_id INTEGER,note_key TEXT,note_value TEXT,PRIMARY KEY(game_id,note_key));
CREATE TABLE IF NOT EXISTS receipts(account TEXT,gid TEXT,message TEXT,result TEXT,PRIMARY KEY(account,gid,message));''')
   self._migrate(d)
 def _migrate(self,d):
  gcols={r[1] for r in d.execute('PRAGMA table_info(games)')}
  if 'waitlist_capacity' not in gcols:d.execute('ALTER TABLE games ADD COLUMN waitlist_capacity INTEGER DEFAULT 0')
  if 'trigger_rules' not in gcols:d.execute("ALTER TABLE games ADD COLUMN trigger_rules TEXT DEFAULT '[]'")
  if 'triggered' not in gcols:d.execute('ALTER TABLE games ADD COLUMN triggered INTEGER DEFAULT 0')
  if 'triggered_at' not in gcols:d.execute('ALTER TABLE games ADD COLUMN triggered_at REAL DEFAULT 0')
  if 'triggered_text' not in gcols:d.execute("ALTER TABLE games ADD COLUMN triggered_text TEXT DEFAULT ''")
  if 'short_triggered' not in gcols:d.execute('ALTER TABLE games ADD COLUMN short_triggered INTEGER DEFAULT 0')
  if 'short_triggered_at' not in gcols:d.execute('ALTER TABLE games ADD COLUMN short_triggered_at REAL DEFAULT 0')
  if 'short_triggered_text' not in gcols:d.execute("ALTER TABLE games ADD COLUMN short_triggered_text TEXT DEFAULT ''")
  mcols={r[1] for r in d.execute('PRAGMA table_info(members)')}
  if 'status' not in mcols:d.execute("ALTER TABLE members ADD COLUMN status TEXT DEFAULT 'formal'")
 @contextmanager
 def db(self):
  d=sqlite3.connect(self.path);d.row_factory=sqlite3.Row
  try:
   with d:yield d
  finally:d.close()
 def _expire(self,d):
  d.execute("UPDATE games SET state='ended' WHERE state='open' AND start<?",(self.clock()-21600,))
 def _member_rows(self,d,rid):
  return [dict(r) for r in d.execute("SELECT qq,name,status FROM members WHERE game_id=? ORDER BY CASE status WHEN 'formal' THEN 0 ELSE 1 END,rowid",(rid,))]
 def _notes(self,d,rid):
  return [{'key':r['note_key'],'value':r['note_value']} for r in d.execute('SELECT note_key,note_value FROM game_notes WHERE game_id=? ORDER BY rowid',(rid,))]
 def _with_members(self,d,rows):
  for r in rows:
   r['members']=self._member_rows(d,r['id'])
   r['notes']=self._notes(d,r['id'])
   r['formal_count']=sum(m.get('status')=='formal' for m in r['members'])
   r['waitlist_count']=sum(m.get('status')=='waitlist' for m in r['members'])
   try:rules=json.loads(r.get('trigger_rules') or '[]')
   except (TypeError,ValueError):rules=[]
   r['trigger_rules']=rules if isinstance(rules,list) else []
   r['triggered']=bool(r.get('triggered'))
   r['triggered_at']=float(r.get('triggered_at') or 0)
   r['triggered_text']=str(r.get('triggered_text') or '')
   r['short_triggered']=bool(r.get('short_triggered'))
   r['short_triggered_at']=float(r.get('short_triggered_at') or 0)
   r['short_triggered_text']=str(r.get('short_triggered_text') or '')
  return rows
 def _card_row(self,d,rid,account=None):
  if account is None:raw=d.execute('SELECT * FROM games WHERE id=?',(rid,)).fetchone()
  else:raw=d.execute('SELECT * FROM games WHERE id=? AND account=?',(rid,account)).fetchone()
  return self._with_members(d,[dict(raw)])[0] if raw else None
 def rows(self,account,gid):
  with self.db() as d:
   self._expire(d)
   rows=[dict(r) for r in d.execute("SELECT * FROM games WHERE account=? AND gid=? AND state='open' ORDER BY start LIMIT 15",(account,gid))]
   return self._with_members(d,rows)
 def all_rows(self,account,limit=200):
  """管理面板用：当前局优先，随后是最近已结束/取消的局。"""
  with self.db() as d:
   self._expire(d)
   rows=[dict(r) for r in d.execute("SELECT * FROM games WHERE account=? ORDER BY CASE state WHEN 'open' THEN 0 ELSE 1 END, start DESC LIMIT ?",(account,max(1,min(int(limit),500))))]
   return self._with_members(d,rows)
 def get(self,rid,account):
  with self.db() as d:
   self._expire(d)
   row=d.execute("SELECT * FROM games WHERE id=? AND account=?",(rid,account)).fetchone()
   return (self._with_members(d,[dict(row)])[0] if row else None)
 def cancel(self,rid,account):
  with self.db() as d:return d.execute("UPDATE games SET state='cancelled' WHERE id=? AND account=? AND state='open'",(rid,account)).rowcount==1
 def mark_reminded(self,rid,account):
  with self.db() as d:return d.execute("UPDATE games SET reminded=1 WHERE id=? AND account=?",(rid,account)).rowcount==1
 def _clean_game(self,game):
  game=str(game or '').strip()
  if not game or len(game)>60:raise ValueError('游戏名称需为 1—60 个字符')
  return game
 def _clean_capacity(self,capacity):
  if capacity in (None,'','不限','无上限','不限人数'):return 0
  if type(capacity) is not int or capacity!=0 and not 2<=capacity<=20:raise ValueError('正式位需为 0（不限）或 2—20')
  return capacity
 def _clean_waitlist(self,value):
  if value in (None,'','不限','无上限','不限人数'):return 0
  if type(value) is not int or not 0<=value<=20:raise ValueError('候补位需为 0—20')
  return value
 def _clean_start(self,start):
  if type(start) not in (int,float) or not math.isfinite(start):raise ValueError('开始时间不合法')
  return float(start)
 def _clean_state(self,state):
  if state not in ('open','cancelled','ended'):raise ValueError('状态只能是 open、cancelled 或 ended')
  return state
 def _clean_notes(self,notes):
  if notes is None:return None
  if isinstance(notes,dict):notes=[{'key':k,'value':v} for k,v in notes.items()]
  if not isinstance(notes,list):raise ValueError('备注需是键值对列表')
  result=[];seen=set()
  for item in notes:
   if not isinstance(item,dict):raise ValueError('备注格式不合法')
   key=str(item.get('key') or '').strip();value=str(item.get('value') or '').strip()
   if not key:
    if not value:continue
    raise ValueError('备注名不能为空')
   if len(key)>30 or len(value)>300:raise ValueError('备注名最多30字，内容最多300字')
   if key in seen:raise ValueError('备注名不能重复')
   seen.add(key);result.append({'key':key,'value':value})
  if len(result)>10:raise ValueError('每局最多添加10条备注')
  return result
 def _clean_trigger_rules(self,rules):
  if rules is None or rules=='':return []
  if isinstance(rules,str):
   try:rules=json.loads(rules)
   except (TypeError,ValueError):raise ValueError('触发条件格式不合法')
  if isinstance(rules,dict):rules=[rules]
  if not isinstance(rules,list):raise ValueError('触发条件需是列表')
  if len(rules)>12:raise ValueError('触发条件最多设置12条')
  result=[];seen=set()
  for item in rules:
   if not isinstance(item,dict):raise ValueError('触发条件格式不合法')
   when=str(item.get('when') or item.get('relation') or 'after').strip().lower()
   when={'后':'after','之后':'after','以后':'after','前':'before','之前':'before','以前':'before'}.get(when,when)
   if when not in ('before','after'):raise ValueError('触发时间只能是之前或之后')
   raw_time=str(item.get('time') or '').strip()
   m=re.fullmatch(r'(\d{1,2})(?:[:：点时](\d{1,2})?)?',raw_time)
   if not m:raise ValueError('触发时间格式应为 HH:MM')
   hour=int(m.group(1));minute=int(m.group(2) or 0)
   if not 0<=hour<=23 or not 0<=minute<=59:raise ValueError('触发时间不合法')
   raw_min=item.get('min_players',item.get('min',0))
   if isinstance(raw_min,str) and raw_min.isdigit():raw_min=int(raw_min)
   if type(raw_min) is not int or not 0<=raw_min<=50:raise ValueError('触发人数需为 0—50（0 表示不设人数门槛）')
   key=(when,f'{hour:02d}:{minute:02d}',raw_min)
   if key in seen:continue
   seen.add(key);result.append({'when':when,'time':f'{hour:02d}:{minute:02d}','min_players':raw_min})
  return result
 def _trigger_time(self,row,rule):
  hour,minute=(int(x) for x in str(rule.get('time') or '00:00').split(':'))
  base=datetime.fromtimestamp(float(row.get('start') or 0),ZoneInfo('Asia/Shanghai'))
  return datetime(base.year,base.month,base.day,hour,minute,tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()
 def _trigger_state(self,row,rule,now,count):
  """full=人齐可开；short=到点但人未齐；空=还没到判断时间。"""
  at=self._trigger_time(row,rule);need=int(rule.get('min_players') or 0)
  before=rule.get('when')=='before'
  if need>count:
   return 'short' if now>=at else ''
  return 'full' if (now<at if before else now>=at) else ''
 def trigger_due(self,account):
  now=self.clock();out=[]
  with self.db() as d:
   self._expire(d)
   rows=[dict(r) for r in d.execute("SELECT * FROM games WHERE account=? AND state='open' AND triggered=0 AND trigger_rules IS NOT NULL AND trigger_rules<>'[]'",(account,))]
   for row in rows:
    try:rules=json.loads(row.get('trigger_rules') or '[]')
    except (TypeError,ValueError):continue
    if not isinstance(rules,list):continue
    count=self._counts(d,row['id'])[0]
    full=None;short=None
    for rule in rules:
     if not isinstance(rule,dict):continue
     state=self._trigger_state(row,rule,now,count)
     if state=='full':full=rule;break
     if state=='short' and short is None:short=rule
    if full:
     label=trigger_rule_text(full)
     changed=d.execute("UPDATE games SET triggered=1,triggered_at=?,triggered_text=? WHERE id=? AND triggered=0",(now,label,row['id'])).rowcount
     if changed:
      row.update(triggered=1,triggered_at=now,triggered_text=label,_short_trigger=False)
      out.append(row)
    elif short and not row.get('short_triggered'):
     label=trigger_rule_text(short)
     changed=d.execute("UPDATE games SET short_triggered=1,short_triggered_at=?,short_triggered_text=? WHERE id=? AND short_triggered=0",(now,label,row['id'])).rowcount
     if changed:
      row.update(short_triggered=1,short_triggered_at=now,short_triggered_text=label,_short_trigger=True)
      out.append(row)
   return self._with_members(d,out) if out else []
 def _replace_notes(self,d,rid,notes):
  d.execute('DELETE FROM game_notes WHERE game_id=?',(rid,))
  for note in notes or []:
   d.execute('INSERT INTO game_notes VALUES(?,?,?)',(rid,note['key'],note['value']))
 def _counts(self,d,rid):
  formal,waitlist=d.execute("SELECT sum(CASE WHEN status='formal' THEN 1 ELSE 0 END),sum(CASE WHEN status='waitlist' THEN 1 ELSE 0 END) FROM members WHERE game_id=?",(rid,)).fetchone()
  return formal or 0,waitlist or 0
 def _seat_status(self,row,formal,waitlist,preferred='auto'):
  if preferred=='formal':
   if row['capacity'] and formal>=row['capacity']:raise ValueError('正式位已经满了')
   return 'formal'
  if preferred=='waitlist':
   if not row['waitlist_capacity']:raise ValueError('这局还没有开候补位')
   if waitlist>=row['waitlist_capacity']:raise ValueError('候补位已经满了')
   return 'waitlist'
  if not row['capacity'] or formal<row['capacity']:return 'formal'
  if row['waitlist_capacity'] and waitlist<row['waitlist_capacity']:return 'waitlist'
  raise ValueError('正式位和候补位都满了')
 def _insert_member(self,d,rid,qq,name,status):
  exists=d.execute('SELECT 1 FROM members WHERE game_id=? AND qq=?',(rid,qq)).fetchone()
  if exists:d.execute('UPDATE members SET name=?,status=? WHERE game_id=? AND qq=?',(name,status,rid,qq))
  else:d.execute('INSERT INTO members VALUES(?,?,?,?)',(rid,qq,name,status))
 def _promote_waitlist(self,d,rid):
  row=d.execute('SELECT * FROM games WHERE id=?',(rid,)).fetchone()
  if not row:return
  while True:
   formal,waitlist=self._counts(d,rid)
   if not waitlist or (row['capacity'] and formal>=row['capacity']):break
   candidate=d.execute("SELECT qq FROM members WHERE game_id=? AND status='waitlist' ORDER BY rowid LIMIT 1",(rid,)).fetchone()
   if not candidate:break
   d.execute("UPDATE members SET status='formal' WHERE game_id=? AND qq=?",(rid,candidate['qq']))
 def admin_create(self,account,gid,host,host_name,game,start,capacity,state='open',members=None,waitlist_capacity=0,notes=None,trigger_rules=None):
  game=self._clean_game(game);capacity=self._clean_capacity(capacity);waitlist_capacity=self._clean_waitlist(waitlist_capacity);start=self._clean_start(start);state=self._clean_state(state);notes=self._clean_notes(notes) or [];trigger_rules=self._clean_trigger_rules(trigger_rules)
  gid=str(gid or '').strip();host=str(host or '').strip()
  if not gid or not host:raise ValueError('群号和发起人不能为空')
  with self.db() as d:
   cur=d.execute('INSERT INTO games(account,gid,host,game,start,capacity,waitlist_capacity,state,trigger_rules) VALUES(?,?,?,?,?,?,?,?,?)',(account,gid,host,game,start,capacity,waitlist_capacity,state,json.dumps(trigger_rules,ensure_ascii=False)))
   rid=cur.lastrowid
   d.execute('INSERT INTO members VALUES(?,?,?,?)',(rid,host,str(host_name or host).strip()[:60] or host,'formal'))
   for m in members or []:
    qq=str(m.get('qq') or '').strip();name=str(m.get('name') or qq).strip()[:60]
    if not qq:continue
    if qq==host:
     d.execute('UPDATE members SET name=? WHERE game_id=? AND qq=?',(name,rid,qq));continue
    preferred=str(m.get('status') or 'formal')
    if preferred not in ('auto','formal','waitlist'):raise ValueError('成员状态不合法')
    formal,waitlist=self._counts(d,rid);game_row=dict(d.execute('SELECT * FROM games WHERE id=?',(rid,)).fetchone());status=self._seat_status(game_row,formal,waitlist,preferred)
    self._insert_member(d,rid,qq,name,status)
   self._replace_notes(d,rid,notes)
  return self.get(rid,account)
 def admin_update(self,rid,account,fields):
  allowed={'gid','host','host_name','game','start','capacity','waitlist_capacity','state','notes','trigger_rules'}
  if not isinstance(fields,dict) or not fields or set(fields)-allowed:raise ValueError('没有可编辑的字段')
  with self.db() as d:
   self._expire(d)
   current=d.execute('SELECT * FROM games WHERE id=? AND account=?',(rid,account)).fetchone()
   if not current:raise ValueError('约局不存在')
   row=dict(current);updates={}
   if 'game' in fields:updates['game']=self._clean_game(fields['game'])
   if 'start' in fields:updates['start']=self._clean_start(fields['start'])
   if 'state' in fields:updates['state']=self._clean_state(fields['state'])
   if 'gid' in fields:
    gid=str(fields['gid'] or '').strip()
    if not gid:raise ValueError('群号不能为空')
    updates['gid']=gid
   formal,waitlist=self._counts(d,rid)
   if 'capacity' in fields:
    cap=self._clean_capacity(fields['capacity'])
    if cap and formal>cap:raise ValueError('当前正式位成员超过新的正式位上限')
    updates['capacity']=cap
   if 'waitlist_capacity' in fields:
    wcap=self._clean_waitlist(fields['waitlist_capacity'])
    if waitlist>wcap:raise ValueError('当前候补成员超过新的候补位上限')
    updates['waitlist_capacity']=wcap
   if 'host' in fields or 'host_name' in fields:
    host=str(fields.get('host') or row['host'] or '').strip()
    if not host:raise ValueError('发起人不能为空')
    if 'host' in fields:updates['host']=host
    member=d.execute('SELECT name,status FROM members WHERE game_id=? AND qq=?',(rid,host)).fetchone()
    host_name=str(fields.get('host_name') or '').strip()[:60]
    if member:
     if member['status']!='formal':
      if row['capacity'] and formal>=row['capacity']:raise ValueError('正式位已满，不能把该成员设为组织者')
      d.execute("UPDATE members SET status='formal' WHERE game_id=? AND qq=?",(rid,host))
     if host_name:d.execute('UPDATE members SET name=? WHERE game_id=? AND qq=?',(host_name,rid,host))
    else:
     if row['capacity'] and formal>=row['capacity']:raise ValueError('正式位已满，不能新增组织者')
     d.execute('INSERT INTO members VALUES(?,?,?,?)',(rid,host,host_name or host,'formal'))
   if ('start' in updates and updates['start']!=row['start']) or ('state' in updates and updates['state']!=row['state']):
    updates['reminded']=0;updates['triggered']=0;updates['triggered_at']=0;updates['triggered_text']=''
    updates['short_triggered']=0;updates['short_triggered_at']=0;updates['short_triggered_text']=''
   if updates:d.execute('UPDATE games SET '+','.join(f'{k}=?' for k in updates)+' WHERE id=?',[*updates.values(),rid])
   if 'notes' in fields:self._replace_notes(d,rid,self._clean_notes(fields['notes']) or [])
   if 'trigger_rules' in fields:
    rules=self._clean_trigger_rules(fields['trigger_rules'])
    try:old_rules=json.loads(row.get('trigger_rules') or '[]')
    except (TypeError,ValueError):old_rules=[]
    updates={'trigger_rules':json.dumps(rules,ensure_ascii=False)}
    if rules!=old_rules:
     updates.update({'triggered':0,'triggered_at':0,'triggered_text':'','short_triggered':0,'short_triggered_at':0,'short_triggered_text':''})
   if updates:d.execute('UPDATE games SET '+','.join(f'{k}=?' for k in updates)+' WHERE id=?',[*updates.values(),rid])
   if 'capacity' in fields:self._promote_waitlist(d,rid)
  return self.get(rid,account)
 def add_member(self,rid,account,qq,name,status='auto'):
  qq=str(qq or '').strip();name=str(name or qq).strip()[:60]
  if not qq:raise ValueError('成员 QQ 不能为空')
  if status not in ('auto','formal','waitlist'):raise ValueError('成员状态不合法')
  with self.db() as d:
   row=d.execute('SELECT * FROM games WHERE id=? AND account=?',(rid,account)).fetchone()
   if not row:raise ValueError('约局不存在')
   row=dict(row);existing=d.execute('SELECT status FROM members WHERE game_id=? AND qq=?',(rid,qq)).fetchone()
   formal,waitlist=self._counts(d,rid)
   if existing:
    if qq==row['host'] and status=='waitlist':raise ValueError('组织者必须在正式位')
    target=existing['status'] if status=='auto' else status
    if target!=existing['status']:
     if target=='formal' and row['capacity'] and formal>=row['capacity']:raise ValueError('正式位已经满了')
     if target=='waitlist' and (not row['waitlist_capacity'] or waitlist>=row['waitlist_capacity']):raise ValueError('候补位已经满了')
    d.execute('UPDATE members SET name=?,status=? WHERE game_id=? AND qq=?',(name,target,rid,qq))
   else:
    if qq==row['host']:target='formal'
    else:target=self._seat_status(row,formal,waitlist,status)
    self._insert_member(d,rid,qq,name,target)
  return self.get(rid,account)
 def remove_member(self,rid,account,qq):
  qq=str(qq or '').strip()
  with self.db() as d:
   row=d.execute('SELECT * FROM games WHERE id=? AND account=?',(rid,account)).fetchone()
   if not row:raise ValueError('约局不存在')
   if str(row['host'])==qq:raise ValueError('不能移除组织者，请先更换组织者')
   member=d.execute('SELECT status FROM members WHERE game_id=? AND qq=?',(rid,qq)).fetchone()
   if not member:return False
   d.execute('DELETE FROM members WHERE game_id=? AND qq=?',(rid,qq))
   if member['status']=='formal':self._promote_waitlist(d,rid)
  return True
 def apply(self,account,gid,qq,name,message,plan):
  with self.db() as d:
   d.execute('BEGIN IMMEDIATE')
   receipt=d.execute('SELECT result FROM receipts WHERE account=? AND gid=? AND message=?',(account,gid,message)).fetchone()
   if receipt:return receipt[0]
   action=plan.get('action');now=self.clock()
   if action in ('create','create_new'):
    game=str(plan.get('game') or '').strip();cap=plan.get('capacity');start=plan.get('start');wait=self._clean_waitlist(plan.get('waitlist_capacity'));trigger_rules=self._clean_trigger_rules(plan.get('trigger_rules'))
    if cap in (None,'','不限','无上限','不限人数'):cap=0
    if not game or len(game)>60 or type(cap) is not int or cap!=0 and not 2<=cap<=20 or type(start) not in (int,float) or not math.isfinite(start) or not now+60<=start<=now+30*86400:raise ValueError('请说清游戏和开始时间；正式位可以不填（默认不限），也可以填 2—20 人，时间需在未来30天内。')
    force_new=action=='create_new' or bool(plan.get('force_new'))
    if not force_new:
     same=[dict(r) for r in d.execute("SELECT * FROM games WHERE account=? AND gid=? AND state='open' AND start>?",(account,gid,now-21600))]
     same=[r for r in same if _same_game_name(r.get('game'),game)]
     if same:
      same=self._with_members(d,same)
      result=(game_notice('本群已经有同款游戏的未结束车队。先看看现有局；要报名请回复「报名 #编号」，'
                          '要另外开一支新车队请明确说「新建车队」。','开黑 · 已有对局')
              + '\n' + game_summary(same,game))
      d.execute('INSERT INTO receipts VALUES(?,?,?,?)',(account,gid,message,result));return result
    if d.execute("SELECT count(*) FROM games WHERE account=? AND gid=? AND host=? AND state='open' AND start>?",(account,gid,qq,now-21600)).fetchone()[0]>=3:raise ValueError('你已经有三个未结束的局啦，先处理已有的吧。')
    rid=d.execute('INSERT INTO games(account,gid,host,game,start,capacity,waitlist_capacity,trigger_rules) VALUES(?,?,?,?,?,?,?,?)',(account,gid,qq,game,start,cap,wait,json.dumps(trigger_rules,ensure_ascii=False))).lastrowid
    d.execute('INSERT INTO members VALUES(?,?,?,?)',(rid,qq,name[:60],'formal'))
    full=self._card_row(d,rid,account)
    result=game_card(full,'开黑 · 已创建',f'✅ 约上了，正式{cap}人，你已算在内。' if cap else '✅ 约上了，正式位不限，你已算在内。',footer='回复「我也来」报名；退出说「我退出」；开场前会提醒一次。')
   else:
    if type(plan.get('id')) is not int or plan['id']<=0:raise ValueError('没有确定是哪一局。')
    row=d.execute("SELECT * FROM games WHERE id=? AND account=? AND gid=? AND state='open' AND start>?",(plan.get('id'),account,gid,now-21600)).fetchone()
    if not row:raise ValueError('没有找到本群对应的有效约局，请说一下是哪一局。')
    rid=row['id']
    if action=='waitlist':
     if row['host']!=qq:raise ValueError('只有组织者能调整候补位；你可以先报名。')
     n=self._clean_waitlist(plan.get('waitlist_capacity'))
     wait=d.execute("SELECT count(*) FROM members WHERE game_id=? AND status='waitlist'",(rid,)).fetchone()[0]
     if n<wait:raise ValueError(f'现在已有{wait}个候补，不能把候补位减到{n}个。')
     d.execute('UPDATE games SET waitlist_capacity=? WHERE id=?',(n,rid))
     full=self._card_row(d,rid,account)
     status=f'✅ 好，{row["game"]}现在有{n}个候补位。' if n else f'✅ 好，{row["game"]}的候补位已关闭。'
     result=game_card(full,'开黑 · 候补位已调整',status,footer='回复「我也来」可以继续报名。')
    elif action in ('join_waitlist','waitlist_join'):
     if row['start']<=now:raise ValueError('这局已经到开始时间，先和组织者确认还能否加入吧。')
     if not int(row['waitlist_capacity'] or 0):raise ValueError('组织者还没有开候补位，先让 TA 开一个吧')
     member=d.execute('SELECT status FROM members WHERE game_id=? AND qq=?',(rid,qq)).fetchone()
     formal,waitlist=self._counts(d,rid)
     if member:
      if str(row['host'])==qq:raise ValueError('组织者不能去候补位')
      if member['status']=='waitlist':
       full=self._card_row(d,rid,account);result=game_card(full,'开黑 · 报名信息','✅ 你已经在候补位啦。')
      else:
       d.execute("UPDATE members SET status='waitlist' WHERE game_id=? AND qq=?",(rid,qq))
       full=self._card_row(d,rid,account);result=game_card(full,'开黑 · 已转候补','✅ 已经把你从正式位挪到候补位，等有人退出会自动补上。')
     else:
      if waitlist>=int(row['waitlist_capacity'] or 0):raise ValueError('候补位已经满了')
      d.execute('INSERT INTO members VALUES(?,?,?,?)',(rid,qq,name[:60],'waitlist'))
      full=self._card_row(d,rid,account);result=game_card(full,'开黑 · 候补成功',f'✅ 已排进{row["game"]}候补{waitlist+1}/{row["waitlist_capacity"]}。',footer='要退出直接回复「我退出」。')
    elif action=='join':
     if row['start']<=now:raise ValueError('这局已经到开始时间，先和组织者确认还能否加入吧。')
     member=d.execute('SELECT status FROM members WHERE game_id=? AND qq=?',(rid,qq)).fetchone()
     formal,waitlist=self._counts(d,rid)
     if member:
      full=self._card_row(d,rid,account)
      status='✅ 你已经在正式位啦。' if member['status']=='formal' else '✅ 你已经在候补位啦。'
      result=game_card(full,'开黑 · 报名信息',status)
     else:
      try:st=self._seat_status(row,formal,waitlist,'auto')
      except ValueError:
       full=self._card_row(d,rid,account)
       result=game_card(full,'开黑 · 报名未成功','❌ 这局暂时满员了，正式位和候补位都不能再加人。',footer='可以等有人退出，或让组织者增加候补位。')
      else:
       d.execute('INSERT INTO members VALUES(?,?,?,?)',(rid,qq,name[:60],st))
       full=self._card_row(d,rid,account)
       if st=='formal':status=f'✅ 算上你了，{row["game"]}正式位现在{formal+1}'+(f'/{row["capacity"]}' if row['capacity'] else '')+'。';title='开黑 · 报名成功'
       else:status=f'✅ 正式位满了，已把你放进{row["game"]}候补{waitlist+1}/{row["waitlist_capacity"]}。';title='开黑 · 候补成功'
       result=game_card(full,title,status,footer='要退出直接回复「我退出」。')
    elif action=='leave':
     if row['host']==qq:raise ValueError('你是组织者，要结束这局请明确说取消。')
     member=d.execute('SELECT status FROM members WHERE game_id=? AND qq=?',(rid,qq)).fetchone()
     if not member:
      full=self._card_row(d,rid,account);result=game_card(full,'开黑 · 报名信息','ℹ️ 你还没加入这一局。')
     else:
      d.execute('DELETE FROM members WHERE game_id=? AND qq=?',(rid,qq));self._promote_waitlist(d,rid)
      full=self._card_row(d,rid,account);result=game_card(full,'开黑 · 已退出','✅ 已经帮你退出这局。',footer='想重新报名直接回复「我也来」。')
    elif action=='cancel':
     if row['host']!=qq:raise ValueError('只有组织者能取消整局；你可以退出自己的报名。')
     d.execute("UPDATE games SET state='cancelled' WHERE id=?",(rid,))
     full=self._card_row(d,rid,account);result=game_card(full,'开黑 · 已取消',f'✅ {row["game"]}这局已取消。',footer='这局不会再提醒；需要重新约可以直接告诉我。')
    else:raise ValueError('没有执行约局操作。')
   d.execute('INSERT INTO receipts VALUES(?,?,?,?)',(account,gid,message,result));return result
 def due(self,account):
  with self.db() as d:
   rows=[dict(r) for r in d.execute("SELECT * FROM games WHERE account=? AND state='open' AND reminded=0 AND start BETWEEN ? AND ?",(account,self.clock(),self.clock()+600))]
   for r in rows:
    r['members']=self._member_rows(d,r['id']);r['notes']=self._notes(d,r['id'])
   return rows
 def claim_reminder(self,rid):
  with self.db() as d:return d.execute('UPDATE games SET reminded=1 WHERE id=? AND reminded=0',(rid,)).rowcount==1

def trigger_rule_text(rule):
 when='后' if rule.get('when')=='after' else '前'
 count=int(rule.get('min_players') or 0)
 return f'{rule.get("time") or "00:00"}{when}'+(f'满{count}人开' if count else '开')

CARD_RULE='────────────'
WEEKDAY_CN='一二三四五六日'

def _game_time_text(ts,weekday=True):
 try:dt=datetime.fromtimestamp(float(ts),ZoneInfo('Asia/Shanghai'))
 except (TypeError,ValueError,OSError):return '—'
 text=f'{dt.month}月{dt.day}日 {dt.strftime("%H:%M")}'
 return text+(f'（周{WEEKDAY_CN[dt.weekday()]}）' if weekday else '')

def _clip_text(value,limit=28):
 text=str(value or '').strip().replace('\n',' ')
 return text if len(text)<=limit else text[:max(1,limit-1)]+'…'

def _game_members(row,members=None):
 return members if members is not None else (row.get('members') or [])

def _game_counts(row,members=None):
 members=_game_members(row,members)
 return (sum(m.get('status')!='waitlist' for m in members),sum(m.get('status')=='waitlist' for m in members))

def _game_seat_text(row,members=None):
 formal,wait=_game_counts(row,members)
 cap=int(row.get('capacity') or 0);wcap=int(row.get('waitlist_capacity') or 0)
 formal_text=f'{formal}/{cap}' if cap else f'{formal}/不限'
 wait_text=f'候补{wait}/{wcap}' if wcap else (f'候补{wait}' if wait else '候补未开')
 if wcap:wait_text+=f'（候补{wcap}位）'
 return f'正式 {formal_text} · {wait_text}'

def _game_name_list(members,status=None):
 return [_clip_text(m.get('name') or m.get('qq'),20) for m in members if (status is None or (status=='formal' and m.get('status')!='waitlist') or (status=='waitlist' and m.get('status')=='waitlist')) and (m.get('name') or m.get('qq'))]

def _member_block(title,names):
 if not names:return []
 return [f'{title}（{len(names)}）：']+[f'   · {name}' for name in names]

def _game_notes_text(notes):
 if not notes:return ''
 return ' · '.join(f'{_clip_text(n.get("key"),16)}={_clip_text(n.get("value"),40)}' for n in notes[:4] if n.get('key') or n.get('value'))

def _game_trigger_text(row):
 rules=row.get('trigger_rules')
 if isinstance(rules,str):
  try:rules=json.loads(rules)
  except (TypeError,ValueError):rules=[]
 if not isinstance(rules,list):return ''
 return '；'.join(trigger_rule_text(x) for x in rules if isinstance(x,dict))[:120]

def game_card(row,title='开黑',status=None,footer=None,members=None,notes=None,show_members=True,show_notes=True):
 if not isinstance(row,dict):row=dict(row)
 members=_game_members(row,members);notes=row.get('notes') if notes is None else notes
 lines=[f'【{title}】']
 if status:lines.append(status)
 lines.extend((f'🎮 游戏：{row.get("game") or "—"}',f'🕘 时间：{_game_time_text(row.get("start"))}',f'📊 状态：{phase_name(row)}',f'👥 人数：{_game_seat_text(row,members)}'))
 host=str(row.get('host') or '').strip();host_name=_clip_text(row.get('host_name'),20) or next((_clip_text(m.get('name') or m.get('qq'),20) for m in members if str(m.get('qq'))==host and (m.get('name') or m.get('qq'))),'')
 if host_name:lines.append(f'👤 组织者：{host_name}')
 trigger=_game_trigger_text(row)
 if trigger:lines.append(f'📌 触发条件：{trigger}')
 if row.get('triggered'):
  lines.append(f'✅ 触发状态：已达成{(" · "+str(row.get("triggered_text"))) if row.get("triggered_text") else ""}')
 if show_notes:
  note_text=_game_notes_text(notes)
  if note_text:lines.append(f'📝 备注：{note_text}')
 if show_members:
  lines.extend(_member_block('✅ 正式',_game_name_list(members,'formal')))
  lines.extend(_member_block('🕒 候补',_game_name_list(members,'waitlist')))
 if row.get('id'):lines.append(f'🆔 编号：#{row["id"]}')
 if footer:
  lines.append(CARD_RULE)
  if isinstance(footer,(list,tuple)):lines.extend(footer)
  else:lines.append(footer)
 return '\n'.join(lines)

def game_notice(text,title='开黑 · 提示'):
 return f'【{title}】\n{text}'

def game_help():
 return game_notice('告诉我游戏和时间就好，人数不填默认不限。\n· 约局：「/开黑 今晚八点 MC 四个人」\n· 查看：「/开黑 看看」或 @我 说「本群有哪些局」\n· 报名：回复约局卡片或 @我 说「我也来」\n· 候补：@我说「我要候补」；组织者说「开3个候补位」\n· 退出：「我退出」','开黑 · 怎么用')

def trigger_message(row):
 label=row.get('triggered_text') or _game_trigger_text(row) or '条件达成'
 return game_card(row,'开黑 · 条件达成',f'✅ 已满足：{label}，可以开了。',footer='组织者确认后开局；报名或时间有变动直接回复我。',show_members=True)

def trigger_notification(row):
 """条件达成时实际 @ 所有正式成员；只发文本在 QQ 群里不会通知到人。"""
 from nonebot.adapters.onebot.v11 import Message,MessageSegment
 members=row.get('members') or []
 mentions=[];seen=set()
 for member in members:
  if str(member.get('status') or 'formal')!='formal':continue
  qq=str(member.get('qq') or '').strip()
  if qq and qq not in seen:
   seen.add(qq);mentions.append(qq)
 host=str(row.get('host') or '').strip()
 if host and host not in seen:
  mentions.insert(0,host)
 segments=[]
 for qq in mentions:
  segments.extend((MessageSegment.at(qq),MessageSegment.text(' ')))
 segments.append(MessageSegment.text(trigger_message(row)))
 return Message(segments)

def short_trigger_notification(row):
 """到点但人数未齐：只提醒组织者，不按“人齐可开”通知全队。"""
 from nonebot.adapters.onebot.v11 import Message,MessageSegment
 host=str(row.get('host') or '').strip()
 members=row.get('members') or []
 formal=sum(1 for m in members if str(m.get('status') or 'formal')!='waitlist')
 cap=int(row.get('capacity') or 0)
 cap_text=f'/{cap}' if cap else ''
 trigger=row.get('short_triggered_text') or _game_trigger_text(row) or '约定时间'
 text=(f"【开黑 · 人数未齐】{row.get('game') or '这局'}到点了，当前正式 {formal}{cap_text}，"
       f"还没有达到「{trigger}」。这条只提醒组织者：要改时间、降低人数、继续等人还是取消，直接回复我。"
       f"\n{CARD_RULE}\n当前卡片：\n" + game_card(row,show_members=True))
 seg=[MessageSegment.at(host)] if host else []
 seg.append(MessageSegment.text(' '+text if host else text))
 return Message(seg)


def reminder_message(row):
 """群提醒必须实际 @ 已报名成员；单纯发文本在 QQ 群里不会通知到人。"""
 from nonebot.adapters.onebot.v11 import Message,MessageSegment
 members=row.get('members') or []
 now=time.time()
 try:minutes=max(0,int((float(row.get('start') or 0)-now+59)//60))
 except (TypeError,ValueError):minutes=0
 title='开黑提醒 · 即将开始' if minutes<=0 else f'开黑提醒 · {minutes} 分钟后开始'
 card=game_card(row,title,show_members=False)
 names=[_clip_text(m.get('name') or m.get('qq'),24)+('（候补）' if m.get('status')=='waitlist' else '') for m in members if m.get('name') or m.get('qq')]
 segments=[MessageSegment.text(card+f'\n{CARD_RULE}\n')]
 seen=set()
 for m in members:
  qq=str(m.get('qq') or '').strip()
  if not qq or qq in seen:continue
  seen.add(qq);segments.extend((MessageSegment.at(qq),MessageSegment.text(' ')))
 roster=['✅ 已报名（'+str(len(names) or 0)+'）：']+[f'   · {name}' for name in names] if names else ['✅ 已报名（0）：','   · 暂无']
 segments.append(MessageSegment.text('\n'.join(roster)+'\n⏰ 请准时上线；人数、时间有变动直接回复我。'))
 return Message(segments)

async def send_game_reminder(bot,row):
 await bot.send_group_msg(group_id=int(row['gid']),message=reminder_message(row))

L4D_ALIASES=('求生之路','l4d2','l4d','left4dead','leftdead')

def _compact_game_text(value):
 return re.sub(r'[\s@/、,，|·\-—_]+','',str(value or '').lower())

def _game_query_label(value):
 text=re.sub(r'本群|群里|车队|开黑|约局|列表|查询|看看|有哪些|有什么|哪个|什么|有(?:哪些|什么|几个)?',' ',str(value or '')).strip(' /、,，|局')
 return _clip_text(text,20)

def filter_game_rows(rows,query=''):
 raw=str(query or '').strip()
 if not raw:return list(rows)
 q=_compact_game_text(raw)
 q=re.sub(r'本群|群里|车队|开黑|约局|列表|查询|看看|有哪些|有什么|哪个|什么|有(?:哪些|什么|几个)?','',q).strip('局')
 if not q:return list(rows)
 aliases=[alias for alias in L4D_ALIASES if alias in q]
 hit=[]
 for row in rows:
  name=_compact_game_text(row.get('game'))
  if q in name or (aliases and any(alias in name for alias in L4D_ALIASES)):hit.append(row)
 return hit

def game_summary(rows,query=''):
 rows=filter_game_rows(rows,query)
 if query and not rows:return game_notice(f'本群没有找到「{_clip_text(query,24)}」相关的开黑局。\n可以试试只说游戏名，例如「求生之路」或「L4D2」。','开黑 · 没有找到')
 if not rows:return game_notice('本群暂时没有未结束的约局。\n\n可以告诉我游戏、时间和总人数，比如：\n「今晚八点约MC，总共四个人」。','开黑 · 本群暂无局')
 label=_game_query_label(query)
 title=f'【开黑 · 本群 {len(rows)} 局'+(f' · {label}' if label else '')+'】'
 lines=[title]
 for i,row in enumerate(rows[:10],1):
  members=row.get('members') or []
  lines.append(f'{i}. #{row.get("id")} {_clip_text(row.get("game"),36)}')
  lines.append(f'   🕘 {_game_time_text(row.get("start"))} · 📊 {phase_name(row)} · 👥 {_game_seat_text(row,members)}')
  host=str(row.get('host') or '');host_name=next((_clip_text(m.get('name') or m.get('qq'),18) for m in members if str(m.get('qq'))==host),'')
  if host_name:lines.append(f'   👤 组织者：{host_name}')
  trigger=_game_trigger_text(row)
  if trigger:lines.append(f'   📌 触发条件：{trigger}')
  note_text=_game_notes_text(row.get('notes'))
  if note_text:lines.append(f'   📝 备注：{note_text}')
  for block in (_member_block('✅ 正式',_game_name_list(members,'formal')),_member_block('🕒 候补',_game_name_list(members,'waitlist'))):
   for line in block:lines.append('   '+line)
 if len(rows)>10:lines.append(f'…还有 {len(rows)-10} 局未展开')
 lines.extend((CARD_RULE,'回复「我也来」报名；说游戏名或编号可以指定某一局。'))
 return '\n'.join(lines)[:2500]

def _forward_node(bot_id,nickname,text):
 return {'type':'node','data':{'user_id':str(bot_id),'nickname':_clip_text(nickname,24),'content':[{'type':'text','data':{'text':text}}]}}

def game_forward_nodes(rows,bot_id,query=''):
 rows=filter_game_rows(rows,query)
 if not rows:return []
 label=_game_query_label(query)
 header=game_notice(f'本群共 {len(rows)} 局'+(f'，已筛选「{label}」' if label else '')+'\n按游戏分条展示，点开聊天记录查看。','开黑 · 列表')
 nodes=[_forward_node(bot_id,'开黑列表',header)]
 for row in rows[:30]:
  nodes.append(_forward_node(bot_id,f'开黑 #{row.get("id")}',game_card(row,f'开黑 · #{row.get("id")}',show_members=True)))
 return nodes
