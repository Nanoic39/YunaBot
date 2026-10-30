"""合成群聊指向评测，仅模型调用，不发群消息。"""
import argparse,asyncio,json,os,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.agent.conversation_target import resolve_target,validate_target
async def main():
 p=argparse.ArgumentParser();p.add_argument('--live',action='store_true');p.add_argument('--output');args=p.parse_args()
 root=Path(__file__).resolve().parents[1];cases=json.loads((root/'tests/fixtures/conversation_target.json').read_text())
 if not args.live:print(json.dumps({'cases':len(cases),'live':False}));return
 import httpx
 from dotenv import dotenv_values
 env={**dotenv_values(root/'.env'),**dotenv_values(root/'.env.dev'),**os.environ}
 key=env.get('CHAT_LLM_API_KEY');base=env.get('CHAT_LLM_API_BASE') or 'https://api.deepseek.com/v1';model=env.get('CHAT_LLM_MODEL') or 'deepseek-flash'
 if not key:raise SystemExit('未配置模型')
 async with httpx.AsyncClient(timeout=4,follow_redirects=True) as client:
  class Model:
   available=True
   raw=None
   async def chat(self,messages,**kw):
    r=await client.post(base.rstrip('/')+'/chat/completions',headers={'Authorization':'Bearer '+key},json={'model':model,'messages':messages,'temperature':0,'max_tokens':500,'thinking':{'type':'disabled'},'response_format':{'type':'json_object'}})
    r.raise_for_status();Model.raw=r.json()['choices'][0]['message']['content'];return Model.raw
  results=[]
  for c in cases:
   Model.raw=None;start=time.monotonic();r=await resolve_target(Model(),c['text'],c['info'],c['recent'],'bot',c.get('quote',''),force=True)
   valid=Model.raw is not None and validate_target(Model.raw,c['text']) is not None
   results.append({'id':c['id'],'expected':c['expected'],'target':r.get('semantic_target'),'focus':r.get('bot_focus'),
     'passed':valid and r.get('semantic_target')==c['expected'] and ('focus' not in c or r.get('bot_focus')==c['focus']), 'seconds':round(time.monotonic()-start,3)})
  report={'model':model,'synthetic_cases':True,'passed':sum(r['passed'] for r in results),'total':len(results),'results':results}
  if args.output:Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2))
  print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':asyncio.run(main())
