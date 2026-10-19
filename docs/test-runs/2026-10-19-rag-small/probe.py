"""模型预检：每类一次、无应用/SDK重试，不输出配置中的密钥。"""
import asyncio,json,sys,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]; sys.path.insert(0,str(ROOT/'ai-py'))
from app.llm import client
from app.config import settings
from app.llm.router import model_for
OUT=Path(__file__).resolve().parent
TARGET=OUT/'probe.json'
if TARGET.exists(): TARGET=OUT/('probe_'+uuid.uuid4().hex[:10]+'.json')
async def main():
    sdk=client._client();sdk.max_retries=0;sdk.timeout=30
    result={'models':{'embedding':settings.embedding_model,'rerank':settings.rerank_model,'generation':model_for('rag_generate')},'attempts_per_stage':1,'stages':[]}
    for name,fn in [('embedding',lambda:client.embed.__wrapped__(['Webhook 回调必须返回 HTTP 200'])),('rerank',lambda:client.rerank('回调成功条件',['客户服务端必须返回 HTTP 200 表示已接收。'],top_n=1)),('generation',lambda:client.chat.__wrapped__([{'role':'user','content':'仅回答：收到'}],model=model_for('rag_generate'),temperature=0))]:
        try:
            r=await asyncio.wait_for(fn(),40)
            if name=='rerank' and not r: raise RuntimeError('Rerank returned no result; inspect provider availability')
            result['stages'].append({'stage':name,'status':'ok','dimension':len(r[0])} if name=='embedding' else {'stage':name,'status':'ok','output':r if name=='rerank' else r.content})
        except Exception as e:
            result['stages'].append({'stage':name,'status':'blocked','error_type':type(e).__name__,'http_status':getattr(e,'status_code',None),'provider_code':getattr(e,'code',None),'error':str(e).replace(settings.dashscope_api_key,'[REDACTED]')[:1500]})
        TARGET.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(result,ensure_ascii=False))
asyncio.run(main())
