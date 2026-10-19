"""原内部AI入口，添加观测；测试进程关闭路由/答案缓存和重试。"""
import json,os,time
from pathlib import Path
from app.ai_main import app
from app.llm import client
from app.agents import api_diagnostic,doc_rag,intent_router
from app.cache import semantic_cache,route_cache
TARGET=Path(os.environ['ENTRY_EVENTS'])
sdk=client._client();sdk.max_retries=0;sdk.timeout=25
client.chat=client.chat.__wrapped__;client.embed=client.embed.__wrapped__
async def cache_miss(*a,**kw):return None
async def semantic_miss(*a,**kw):return None,None
async def no_write(*a,**kw):pass
route_cache.get=cache_miss;route_cache.put=no_write
semantic_cache.get=semantic_miss;semantic_cache.put=no_write
calls=0

def observe(obj,name):
    original=getattr(obj,name)
    async def wrapped(*a,**kw):
        global calls
        if obj is client:
            calls+=1
            if calls>75:raise RuntimeError('entry call cap exceeded')
        t=time.perf_counter();ev={'stage':name,'args':a[0] if name in ['classify','answer','_error_doc_fast'] and a else None}
        try:
            r=await original(*a,**kw);ev['status']='ok'
            if name=='classify':ev['classification']=r
            if name=='_error_doc_fast':ev['found']=r is not None
            return r
        except Exception as e:ev.update(status='error',error_type=type(e).__name__);raise
        finally:
            ev['ms']=(time.perf_counter()-t)*1000
            with TARGET.open('a') as f:f.write(json.dumps(ev,ensure_ascii=False)+'\n')
    setattr(obj,name,wrapped)
for obj,name in [(intent_router,'classify'),(api_diagnostic,'_error_doc_fast'),(doc_rag,'answer'),(client,'chat'),(client,'embed'),(client,'rerank')]:observe(obj,name)
