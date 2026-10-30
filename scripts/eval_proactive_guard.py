"""主动分享固定合成样例；只调用模型，不发送任何群消息。"""
import argparse,asyncio,json,os,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.agent.proactive_guard import approve_share,classify_feedback

async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');parser.add_argument('--output');parser.add_argument('--feedback',action='store_true')
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    cases=json.loads((root/'tests/fixtures'/('share_feedback.json' if args.feedback else 'proactive_guard.json')).read_text())
    if not args.live:print(json.dumps({'cases':len(cases),'live':False}));return
    import httpx
    from dotenv import dotenv_values
    env={**dotenv_values(root/'.env'),**dotenv_values(root/'.env.dev'),**os.environ}
    key=env.get('CHAT_LLM_API_KEY');base=env.get('CHAT_LLM_API_BASE') or 'https://api.deepseek.com/v1';model=env.get('CHAT_LLM_MODEL') or 'deepseek-flash'
    if not key:raise SystemExit('未配置聊天模型密钥')
    async with httpx.AsyncClient(timeout=4,follow_redirects=True) as client:
        class Model:
            available=True
            last=None
            called=False
            error=None
            async def chat(self,messages,**kwargs):
                Model.called=True
                r=await client.post(base.rstrip('/')+'/chat/completions',headers={'Authorization':'Bearer '+key},json={
                    'model':model,'messages':messages,'temperature':0.0,'max_tokens':kwargs.get('max_tokens',80),
                    'thinking':{'type':'disabled'},'response_format':{'type':'json_object'}})
                if r.status_code!=200:Model.error='HTTP '+str(r.status_code)
                r.raise_for_status();Model.last=r.json()['choices'][0]['message']['content'];return Model.last
        results=[]
        for case in cases:
            Model.last=None;Model.error=None;Model.called=False
            start=time.monotonic()
            if args.feedback:
                result=await classify_feedback(Model(),case['text'],[{'event_id':'craft','message_id':123,'text':'书签做好了，配了蓝色流苏。'}],case['context'],case.get('quote_id'))
                value=[result['event_id'],result['sentiment']] if result else None
            else:
                value=await approve_share(Model(),case['candidate'],[{'text':s} for s in case['previous']])
            results.append({'id':case['id'],'expected':case['expected'],'allowed':value,'passed':Model.error is None and (not Model.called or Model.last is not None) and value==case['expected'],'raw':Model.last,'error':Model.error,'seconds':round(time.monotonic()-start,3)})
        report={'model':model,'synthetic_cases':True,'passed':sum(r['passed'] for r in results),'total':len(results),'results':results}
        if args.output:Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':asyncio.run(main())
