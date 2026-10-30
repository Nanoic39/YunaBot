"""固定合成样例评测；--live显式调用已配置模型，不发送QQ或执行任务。"""
import argparse,asyncio,json,os,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.agent.semantic_retrieval import select_events
from src.agent.memory_retrieval import relevance

async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--live',action='store_true');parser.add_argument('--output');parser.add_argument('--timeout',type=float,default=3)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    cases=json.loads((root/'tests/fixtures/semantic_retrieval.json').read_text())
    if not args.live:
        print(json.dumps({'cases':len(cases),'live':False}));return
    from dotenv import dotenv_values
    import httpx
    env={**dotenv_values(root/'.env'),**dotenv_values(root/'.env.dev'),**os.environ}
    base=env.get('CHAT_LLM_API_BASE') or 'https://api.deepseek.com/v1'
    key=env.get('CHAT_LLM_API_KEY');model=env.get('CHAT_LLM_MODEL') or 'deepseek-flash'
    if not key:raise SystemExit('未配置聊天模型密钥')
    async with httpx.AsyncClient(timeout=args.timeout+1,follow_redirects=True) as client:
        class Model:
            available=True
            async def chat(self,messages,**kw):
                response=await client.post(base.rstrip('/')+'/chat/completions',headers={'Authorization':'Bearer '+key},json={
                    'model':model,'messages':messages,'temperature':0.0,'max_tokens':180,
                    'thinking':{'type':'disabled'},'response_format':{'type':'json_object'}})
                response.raise_for_status()
                return response.json()['choices'][0]['message']['content']
        results=[]
        for case in cases:
            started=time.monotonic()
            prediction=await select_events(Model(),case['query'],case['context'],case['events'],case.get('quote',''),timeout=args.timeout)
            ranked=sorted(case['events'],key=lambda e:(relevance(e.get('text',''),case['query']),e.get('time',0)),reverse=True)
            baseline=[e['id'] for e in ranked if relevance(e.get('text',''),case['query'])>=0.15][:4]
            results.append({'baseline':baseline,'baseline_passed':set(baseline)==set(case['expected']),'id':case['id'],'expected':case['expected'],'selected':prediction,
                'passed':prediction is not None and set(prediction)==set(case['expected']),
                'seconds':round(time.monotonic()-started,3)})
        report={'model':model,'synthetic_cases':True,'timeout':args.timeout,'passed':sum(r['passed'] for r in results),
                'total':len(results),'baseline_passed':sum(r['baseline_passed'] for r in results),'results':results}
        if args.output:Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':asyncio.run(main())
