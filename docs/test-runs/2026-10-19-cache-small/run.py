"""小规模真实缓存对照：独立Redis，现有评测索引只读，无会话/业务库写入。"""
import argparse,asyncio,dataclasses,hashlib,json,os,socket,statistics,subprocess,sys,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'ai-py'))
CASES=[
 {'id':'C1','query':'回调失败重试几次，间隔是多少？','section':'回调重试策略','paraphrase':'Webhook 投递失败最多重试多少次？每次间隔多久？'},
 {'id':'C2','query':'同一回调来了两次，怎样防止重复处理？','section':'回调重复','paraphrase':'重复收到 Webhook 时，应该用哪个字段做幂等去重？'},
 {'id':'C3','query':'基础版和专业版 QPS、月配额各是多少？','section':'套餐档位','paraphrase':'基础套餐和专业套餐分别允许多少 QPS 和每月调用量？'}]
def port():
 with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
def main():
 p=argparse.ArgumentParser();p.add_argument('--run-id',default=uuid.uuid4().hex[:10]);p.add_argument('--collection',default='rag_small_20261019a');a=p.parse_args()
 out=BASE/('run_'+a.run_id);out.mkdir(exist_ok=False)
 rp=port();os.environ['REDIS_URL']=f'redis://127.0.0.1:{rp}/0';os.environ['MILVUS_COLLECTION']=a.collection
 def save(name,obj):(out/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str)+'\n')
 def append(name,obj):
  with (out/name).open('a') as f:f.write(json.dumps(obj,ensure_ascii=False,default=str)+'\n')
 sources=[{'path':str(f.relative_to(ROOT)),'sha256':hashlib.sha256(f.read_bytes()).hexdigest()} for f in sorted((ROOT/'data/knowledge').glob('*.md'))]
 save('cases.frozen.json',{'cases':CASES,'repetitions_per_case_per_group':4,'sources':sources,'extra_probes':['same_query','paraphrase','unrelated_query','other_tenant','similarity_threshold_boundary'], 'frozen_before_calls':True})
 log=(out/'redis.log').open('w');proc=subprocess.Popen(['redis-server','--bind','127.0.0.1','--port',str(rp),'--save','','--appendonly','no'],stdout=log,stderr=log)
 try:asyncio.run(run(a,out,save,append))
 finally:
  proc.terminate()
  try:proc.wait(timeout=5)
  except subprocess.TimeoutExpired:proc.kill();proc.wait()
  log.close();save('cleanup.json',{'dedicated_redis_stopped':proc.poll() is not None,'existing_redis_untouched':True,'index_access':'read only','session_and_business_database_writes':False})

async def run(args,out,save,append):
 from app.config import settings
 from app.llm import client
 from app.agents import doc_rag,intent_router
 from app.cache import semantic_cache,route_cache
 from app.cache.redis_client import get_redis
 from app.rag import store,retriever
 from app.rag.ingest import chunk_markdown
 import numpy as np
 import dashscope
 sdk=client._client();sdk.max_retries=0;sdk.timeout=25
 client.chat=client.chat.__wrapped__;client.embed=client.embed.__wrapped__
 # SDK非200/空结果必须记服务失败，不能用空精排当正常拒答。
 raw_rerank=dashscope.TextReRank.call
 def checked_rerank(**kw):
  kw['timeout']=25;r=raw_rerank(**kw)
  if getattr(r,'status_code',None)!=200:raise RuntimeError('Rerank HTTP '+str(getattr(r,'status_code',None))+' code='+str(getattr(r,'code',None)))
  return r
 dashscope.TextReRank.call=checked_rerank
 current={};calls=0
 def err(e):return {'error_type':type(e).__name__,'http_status':getattr(e,'status_code',None),'provider_code':getattr(e,'code',None),'error':str(e).replace(settings.dashscope_api_key,'[REDACTED]')[:1400]}
 def observe(obj,name,provider=False):
  original=getattr(obj,name)
  async def wrapped(*a,**kw):
   nonlocal calls
   ev={'stage':name};current.setdefault('stages',[]).append(ev);t=time.perf_counter()
   if provider:
    calls+=1
    if calls>120:raise RuntimeError('120 model-method call budget exceeded')
   try:
    r=await original(*a,**kw);ev['status']='ok'
    if name=='chat':ev.update(model=r.model,tokens=r.total_tokens)
    if name=='embed':ev['dimensions']=[len(v) for v in r]
    if name=='rerank' and not r:raise RuntimeError('empty rerank result')
    return r
   except Exception as e:ev.update(status='error',**err(e));raise
   finally:ev['ms']=(time.perf_counter()-t)*1000
  setattr(obj,name,wrapped)
 for name in ['embed','chat','rerank']:observe(client,name,True)
 observe(doc_rag,'answer');observe(intent_router,'classify')
 async def trial(label,fn,file='samples.jsonl'):
  nonlocal current
  current={'label':label};t=time.perf_counter()
  try:current.update(status='ok',result=await asyncio.wait_for(fn(),60))
  except Exception as e:current.update(status='error',**err(e))
  current['elapsed_ms']=(time.perf_counter()-t)*1000
  current['model_calls']={name:sum(e['stage']==name for e in current.get('stages',[])) for name in ['embed','rerank','chat']}
  append(file,current);print(label,current['status'],round(current['elapsed_ms'],2),current['model_calls'],flush=True)
  return current
 r=get_redis()
 try:
  await r.ping()
  # 重用上一轮隔离索引，但先逐片核对当前资料；不重建/不清空任何已有数据。
  actual=store.all_chunks();expected=[]
  for f in sorted((ROOT/'data/knowledge').glob('*.md')):
   md=f.read_text();title=md.splitlines()[0][2:];expected.extend((title,c['section'],c['content']) for c in chunk_markdown(md,title))
  assert sorted((c['doc_title'],c['section'],c['content']) for c in actual)==sorted(expected),'评测索引与当前知识源不一致，停止调用'
  retriever.reset_bm25()
  files=['ai-py/app/cache/route_cache.py','ai-py/app/cache/semantic_cache.py','ai-py/app/agents/supervisor.py']
  save('metadata.json',{'scope':'cache+RAG document-answer stage; excludes intent classification, supervisor persistence, HTTP/SSE','collection':args.collection,'chunks':len(actual),'redis':'dedicated ephemeral process','concurrency':1,'semantic_threshold':settings.semantic_cache_sim_threshold,'semantic_max_entries':semantic_cache.MAX_ENTRIES,'application_and_openai_sdk_retries':0,'dashscope_retry':'may resend once on pooled connection drop','call_limit':120,'models':{k:getattr(settings,k) for k in ['embedding_model','llm_model_small','llm_model_large','rerank_model']},'source_hashes':{f:hashlib.sha256((ROOT/f).read_bytes()).hexdigest() for f in files}})
  async def route_query(q):
   cached=await route_cache.get(q)
   if cached:return {'cache_hit':True,'classification':cached}
   result=await intent_router.classify(q)
   if result['tokens']<=0 or result['confidence']<settings.intent_confidence_threshold:raise RuntimeError('classification unsuitable for route cache benchmark')
   await route_cache.put(q,result)
   return {'cache_hit':False,'classification':result}
  q=CASES[0]['query']
  pre=await trial('route:preflight',lambda:route_query(q),'route.jsonl')
  if pre['status']=='ok':
   for i in range(4):
    await r.delete(route_cache._key(q))
    cold=await trial(f'route:cold:{i+1}',lambda:route_query(q),'route.jsonl')
    if cold['status']!='ok':break
    await trial(f'route:hit:{i+1}',lambda:route_query(q),'route.jsonl')
  else:save('route-BLOCKED.json',{'stage':'intent classification','details':pre,'conclusion':'no route cache speedup claimed; no synthetic classification seeded'})
  async def semantic_pipeline(q,tenant,expected_section):
   cached,vec=await semantic_cache.get(tenant,q)
   if cached:return {'cache_hit':True,'similarity':cached['similarity'],'answer':cached['answer'],'citations':cached['citations'],'card':cached.get('card')}
   result=await doc_rag.answer(q)
   if not result.hit or not result.answer or not any(c['section']==expected_section for c in result.citations):raise RuntimeError('No valid cited RAG answer; do not seed cache')
   payload={'answer':result.answer,'citations':result.citations,'card':result.card,'intent':'doc_qa'}
   await semantic_cache.put(tenant,q,payload,vec)
   return {'cache_hit':False,**payload}
  seeds={}
  for case in CASES:
   q=case['query'];tenant='warm_'+case['id']
   pre=await trial(case['id']+':warmup',lambda:semantic_pipeline(q,tenant,case['section']))
   if pre['status']!='ok':save('semantic-BLOCKED.json',pre);break
   seeds[case['id']]=pre['result']
   stopped=False
   for i in range(4):
    # 冷组使用全新租户key；热组使用刚由真实RAG写入的原答案。
    coldtenant='cold_'+case['id']+'_'+str(i)
    order=['cold','hit'] if i%2==0 else ['hit','cold']
    for group in order:
     chosen=coldtenant if group=='cold' else tenant
     sample=await trial(f"{case['id']}:{group}:{i+1}",lambda:semantic_pipeline(q,chosen,case['section']))
     if sample['status']!='ok':stopped=True;break
     assert sample['result']['cache_hit']==(group=='hit'),'unexpected cache branch'
     if group=='hit':assert all(sample['result'][k]==seeds[case['id']][k] for k in ['answer','citations','card']),'cached payload changed'
    if stopped:break
   if stopped:save('semantic-BLOCKED.json',sample);break
  # 不足以估算真实流量命中率；仅测试三个固定改写以及隔离/不相关样本。
  for case in CASES:
   if case['id'] not in seeds:continue
   for kind,query,tenant in [('paraphrase',case['paraphrase'],'warm_'+case['id']),('other_tenant',case['query'],'empty_'+case['id']),('unrelated','请给出 Java SDK 官方 Maven 坐标和最新版本。','warm_'+case['id'])]:
    async def lookup(q=query,t=tenant):
     cached,vec=await semantic_cache.get(t,q)
     entries=[json.loads(x) for x in await r.lrange(semantic_cache._key(t),0,-1)]
     best=max((semantic_cache._cosine(np.array(vec),np.array(x['embedding'])) for x in entries),default=None)
     return {'cache_hit':cached is not None,'best_similarity':best,'cached_payload':cached}
    res=await trial(case['id']+':'+kind,lookup,'probes.jsonl')
    if res['status']!='ok':break
  save('completion.json',{'model_method_calls':calls,'seeded_cases':list(seeds),'redis_keys':await r.dbsize(),'status':'see individual stage records'})
 except Exception as e:save('BLOCKED.json',err(e));print(err(e),flush=True)
 finally:
  await r.aclose()
  store.get_client().close()

if __name__=='__main__':main()
