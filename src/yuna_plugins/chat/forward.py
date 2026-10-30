"""Structured recursive forward transcripts; no fetched content can become instructions."""
from __future__ import annotations
import asyncio,html,json,re,time
from datetime import datetime,timedelta,timezone
from xml.etree import ElementTree
TZ=timezone(timedelta(hours=8))

def segment(value):
 if isinstance(value,dict):return value
 return {'type':getattr(value,'type',''),'data':getattr(value,'data',{}) or {}}
def segments(value):
 if isinstance(value,str):
  result=[];pos=0
  for m in re.finditer(r'\[CQ:(\w+)((?:,[^\]]*)?)\]',value):
   if m.start()>pos:result.append({'type':'text','data':{'text':html.unescape(value[pos:m.start()])}})
   data={}
   for pair in m.group(2).lstrip(',').split(','):
    k,sep,v=pair.partition('=')
    if sep:data[k]=html.unescape(v)
   result.append({'type':m.group(1),'data':data});pos=m.end()
  if pos<len(value):result.append({'type':'text','data':{'text':html.unescape(value[pos:])}})
  return result
 if isinstance(value,dict):return [value] if value.get('type') else [{'type':'forward','data':value}]
 return [segment(s) for s in value or []]
def raw_nodes(payload):
 for _ in range(5):
  if isinstance(payload,list):return payload
  if not isinstance(payload,dict):return []
  if payload.get('type')=='node':return [payload]
  payload=next((payload[k] for k in ('messages','message','data','content') if isinstance(payload.get(k),(list,dict))),None)
 return []
def nested_id(seg):
 kind=seg.get('type');d=seg.get('data') or {}
 if kind in ('forward','node'):return str(d.get('id') or d.get('res_id') or d.get('message_id') or '')[:512]
 if kind=='json':
  try:
   j=json.loads(d.get('data') or '{}') if isinstance(d.get('data'),str) else d.get('data',{})
   if j.get('app')=='com.tencent.multimsg':return str(j.get('meta',{}).get('detail',{}).get('resid') or '')[:512]
  except (ValueError,TypeError,AttributeError):pass
 if kind=='xml':
  text=str(d.get('data') or '')
  if len(text)<=32768 and '<!DOCTYPE' not in text.upper():
   try:
    el=ElementTree.fromstring(text)
    if el.get('action')=='viewMultiMsg':return str(el.get('m_resid') or '')[:512]
   except ElementTree.ParseError:pass
 return ''
def _time(value):
 try:
  v=float(value);v=v/1000 if v>10_000_000_000 else v
  return datetime.fromtimestamp(v,TZ).strftime('%m-%d %H:%M:%S')
 except (ValueError,TypeError,OSError,OverflowError):return ''

def parse_forward(payload,*,max_nodes=200,max_chars=24000,max_depth=12):
 nodes=[];images=[];lines=[];warnings=[];used=0
 def emit(line):
  nonlocal used
  if used+len(line)>max_chars:
   if '字数上限' not in warnings:warnings.append('字数上限')
   remaining=max(0,max_chars-used)
   if remaining:lines.append(line[:remaining]);used=max_chars
   return bool(remaining)
  lines.append(line);used+=len(line);return True
 def walk(data,depth=0,parent=''):
  if depth>max_depth:
   warnings.append('嵌套深度上限');return
  rows=raw_nodes(data)
  for i,original in enumerate(rows,1):
   if len(nodes)>=max_nodes:
    warnings.append('节点数量上限');return
   if '字数上限' in warnings:return
   if not isinstance(original,dict):continue
   item=original.get('data') if original.get('type')=='node' and isinstance(original.get('data'),dict) else original
   sender=item.get('sender') if isinstance(item.get('sender'),dict) else {}
   qq=str(sender.get('user_id') or item.get('user_id') or item.get('uin') or '')
   name=str(sender.get('card') or sender.get('nickname') or item.get('nickname') or item.get('name') or qq or '未知')
   value=next((item[k] for k in ('message','content','raw_message') if item.get(k) is not None),[])
   path=parent+'.'+str(i) if parent else str(i)
   ss=segments(value);text=[];children=[];node_images=[]
   for seg in ss:
    k=seg.get('type','unknown');d=seg.get('data') if isinstance(seg.get('data'),dict) else {}
    if k=='text':text.append(str(d.get('text') or ''))
    elif k in ('forward','node') or d.get('_resolved') is not None or nested_id(seg):
     nested=d.get('_resolved') or ({'messages':[{'type':'node','data':d}]} if k=='node' and any(x in d for x in ('message','content')) else d if any(x in d for x in ('messages','message','content')) else None)
     if d.get('_unresolved'):nested=None
     if nested is not None:children.append(nested);text.append('[内含聊天记录，见下级 '+path+']')
     else:
      reason=d.get('_unresolved') or '仅有引用，尚未取得正文'
      text.append('[未解析的嵌套记录：'+str(reason)+']');warnings.append(str(reason))
    elif k in ('image','mface','video'):
     # NapCat/OneBot 的转发图片有时 url 为空，只给 file（http/base64/file 或本地 ID）。
     # 视频只取封面帧进入视觉链路，不把视频 URL 冒充成图片。
     url=str(d.get('url') or '') if k!='video' else str(d.get('cover') or d.get('cover_url') or '')
     file=str(d.get('file') or '') if k!='video' else ''
     source=url or file
     if source.startswith(('https://','http://','base64://','file://','data:')):
      images.append(source);node_images.append(source)
      text.append(f'[图片 图{len(images)}]' if k!='video' else f'[视频封面 图{len(images)}]')
     else:
      if k=='video':
       text.append('[video '+json.dumps(d,ensure_ascii=False,default=str)+']')
      else:
       text.append('[图片：'+str(d.get('summary') or file or '未提供可读取地址')+']')
    elif k=='at':text.append('@全体' if str(d.get('qq'))=='all' else '@'+str(d.get('qq') or '?'))
    elif k=='reply':text.append('[引用消息 '+str(d.get('id') or '?')+']')
    elif k=='face':text.append('[表情 id='+str(d.get('id') or '')+']')
    else:
     # Preserve file/audio/video/JSON/XML and unknown types, instead of dropping them.
     text.append('['+str(k)+' '+json.dumps(d,ensure_ascii=False,default=str)+']')
   text=''.join(text)
   stamp=_time(item.get('time') or original.get('time'))
   row={'qq':qq,'name':name,'time':stamp,'timestamp':item.get('time') or original.get('time'),'text':text,'images':node_images,'path':path,'depth':depth,'segments':ss}
   if not emit(f'{path}. {stamp+" " if stamp else ""}{name}（QQ{qq or "?"}）：{text}'):return
   nodes.append(row)
   for child in children:walk(child,depth+1,path)
 walk(payload)
 if not nodes:return {'ok':False,'text':'','nodes':[],'images':[],'reason':'no_messages','truncated':False}
 warning='\n【未完整读取：'+'；'.join(dict.fromkeys(warnings))+'】' if warnings else ''
 return {'ok':True,'text':f'【转发聊天记录｜实际解析 {len(nodes)} 条，保留嵌套层级】\n'+'\n'.join(lines)+warning,'nodes':nodes,'images':images,'reason':'','truncated':bool(warnings),'warnings':list(dict.fromkeys(warnings))}

async def resolve_forwards(bot,*sources,max_fetches=16,max_depth=12,max_chars=96000):
 """Expand actual inline nodes and provider references, with cycle and total-time limits."""
 started=time.monotonic();calls=0;cache={};expanded_nodes=0
 async def expand(seg,ancestors=(),depth=0):
  nonlocal calls
  seg=dict(segment(seg));data=dict(seg.get('data') or {});seg['data']=data
  fid=nested_id(seg)
  if depth>max_depth:data['_unresolved']='嵌套深度上限';return seg
  if fid:
   if fid in ancestors:data['_unresolved']='循环引用，已停止展开';return seg
   if time.monotonic()-started>=20 or calls>=max_fetches:data['_unresolved']='读取时间或接口次数上限';return seg
   if fid not in cache:
    calls+=1
    try:cache[fid]=await asyncio.wait_for(bot.call_api('get_forward_msg',id=fid),min(5,max(.1,20-(time.monotonic()-started))))
    except Exception:data['_unresolved']='接口未返回正文';return seg
   rows=raw_nodes(cache[fid])
   if not rows:data['_unresolved']='返回格式没有消息节点';return seg
   data['_resolved']=await expand_nodes(rows,ancestors+(fid,),depth+1)
  elif seg.get('type') in ('node','forward'):
   for key in ('messages','message','content'):
    if key in data:
     if seg.get('type')=='node':data[key]=[await expand(s,ancestors,depth+1) for s in segments(data[key])]
     else:data['_resolved']=await expand_nodes(raw_nodes({key:data[key]}),ancestors,depth+1)
     break
  return seg
 async def expand_nodes(rows,ancestors,depth):
  nonlocal expanded_nodes
  result=[]
  for original in rows[:200]:
   if expanded_nodes>=500 or time.monotonic()-started>=20:
    result.append({'message':[{'type':'forward','data':{'_unresolved':'展开节点或时间上限'}}]});break
   expanded_nodes+=1
   if not isinstance(original,dict):continue
   node=dict(original)
   wrapped=node.get('type')=='node';item=dict(node.get('data') or {}) if wrapped else node
   for key in ('message','content','raw_message'):
    if item.get(key) is not None:
     item[key]=[await expand(s,ancestors,depth) for s in segments(item[key])];break
   if wrapped:node['data']=item
   result.append(node)
  if len(rows)>200:result.append({'message':[{'type':'forward','data':{'_unresolved':'单层记录超过200条，未完整读取'}}]})
  return {'messages':result}
 roots=[]
 for source in sources:
  for seg in segments(source):
   if seg.get('type') in ('forward','node') or nested_id(seg):
    expanded=await expand(seg)
    if seg.get('type')=='node':roots.append(expanded)
    else:roots.append({'sender':{'nickname':'转发记录'},'message':[expanded]})
 if not roots:return {'ok':False,'text':'','images':[],'nodes':[]}
 return parse_forward({'messages':roots},max_depth=max_depth,max_chars=max_chars)
