"""真实诊断函数分支补测；不代替被限流阻断的Go用户入口测试。"""
import asyncio,dataclasses,json,os,sys,time
from pathlib import Path
BASE=Path(__file__).resolve().parent;ROOT=BASE.parents[2];sys.path.insert(0,str(ROOT/'ai-py'))
async def main():
 run_id=sys.argv[1];target=BASE/('run_'+run_id)/'branches.jsonl'
 if target.exists():raise RuntimeError('分支记录已存在，禁止覆盖')
 os.environ['MILVUS_COLLECTION']='rag_small_'+run_id
 from app.llm import client
 from app.agents import api_diagnostic,doc_rag
 from app.tools.registry import ToolContext
 from app.db import async_engine,sync_engine
 from app.config import settings
 sdk=client._client();sdk.max_retries=0;sdk.timeout=25
 client.chat=client.chat.__wrapped__;client.embed=client.embed.__wrapped__
 events=[]
 def observe(obj,name):
  original=getattr(obj,name)
  async def wrapped(*a,**kw):
   t=time.perf_counter();ev={'stage':name};events.append(ev)
   try:
    r=await original(*a,**kw);ev['status']='ok'
    if name=='_error_doc_fast':ev['found']=r is not None
    return r
   except Exception as e:ev.update(status='error',error_type=type(e).__name__,error=str(e).replace(settings.dashscope_api_key,'[REDACTED]')[:1500]);raise
   finally:ev['ms']=(time.perf_counter()-t)*1000
  setattr(obj,name,wrapped)
 for obj,name in [(api_diagnostic,'_error_doc_fast'),(doc_rag,'answer'),(client,'embed'),(client,'rerank'),(client,'chat')]:observe(obj,name)
 try:
  for code in ['AUTH_KEY_EXPIRED','WEBHOOK_SIGN_INVALID','UNKNOWN_TEST_CODE']:
   events=[];row={'code':code,'scope':'api_diagnostic.diagnose direct, no intent model / HTTP','events':events}
   try:
    r=await asyncio.wait_for(api_diagnostic.diagnose('本次调用报错 '+code+'，请诊断。',{'error_code':code},ToolContext(tenant_id='rag_small_readonly')),60)
    row.update(status='ok',result=dataclasses.asdict(r))
   except Exception as e:row.update(status='error',error_type=type(e).__name__,error=str(e).replace(settings.dashscope_api_key,'[REDACTED]')[:1500])
   with target.open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
   print(code,row['status'],[(e['stage'],e.get('found')) for e in events],flush=True)
   if row['status']!='ok':break
 finally:await async_engine.dispose();sync_engine.dispose()
if __name__=='__main__':asyncio.run(main())
