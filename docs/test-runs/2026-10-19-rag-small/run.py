"""小样本真实 RAG + 只读错误码热路径对照；从 ai-py 工作目录执行。"""
import argparse, asyncio, dataclasses, hashlib, json, os, statistics, sys, time, uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/'ai-py'))
OUT=Path(__file__).resolve().parent

def save(name,obj): (OUT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str)+'\n')
def append(name,obj):
    with (OUT/name).open('a') as f:f.write(json.dumps(obj,ensure_ascii=False,default=str)+'\n')

async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run-id',default=uuid.uuid4().hex[:10]);args=parser.parse_args()
    # 每轮独立目录，保留前一轮所有记录，避免重跑覆盖。
    global OUT
    base=OUT;OUT=base/('run_'+args.run_id);OUT.mkdir(exist_ok=False)
    spec_bytes=(base/'questions.frozen.json').read_bytes()
    expected=(base/'questions.sha256').read_text().split()[0]
    assert hashlib.sha256(spec_bytes).hexdigest()==expected
    spec=json.loads(spec_bytes)
    for source in spec['sources']:
        assert hashlib.sha256((ROOT/source['path']).read_bytes()).hexdigest()==source['sha256'], '知识源变化，请另建冻结版本'
    os.environ['MILVUS_COLLECTION']='rag_small_'+args.run_id
    from app.config import settings
    from app.llm import client
    from app.agents import doc_rag,api_diagnostic
    from app.rag import store,retriever,reranker,compressor
    from app.rag.ingest import chunk_markdown,_category
    from app.db import AsyncSessionLocal,async_engine,sync_engine
    from app.models import ErrorCode
    from sqlalchemy import select
    sdk=client._client();sdk.max_retries=0;sdk.timeout=30
    # 不改变提示词/检索算法；关闭嵌套重试，一次失败即记原始失败，不当成拒答。
    client.chat=client.chat.__wrapped__;client.embed=client.embed.__wrapped__
    current={};calls=0
    def clean_error(e):return str(e).replace(settings.dashscope_api_key,'[REDACTED]')[:2000]
    def wrap_async(obj,name):
        original=getattr(obj,name)
        async def wrapped(*a,**kw):
            nonlocal calls
            t=time.perf_counter();ev={'stage':name};current.setdefault('stages',[]).append(ev)
            if obj is client:
                calls+=1
                if calls>230:raise RuntimeError('230 provider call cap')
            try:
                r=await original(*a,**kw)
                ev['status']='ok'
                if name=='embed':ev['dimensions']=[len(v) for v in r]
                if name=='hybrid_search':ev['candidates']=r[0];ev['top_vec']=r[1]
                if name=='rerank_candidates':ev['top5']=r
                if name=='chat':ev.update(model=r.model,tokens=r.total_tokens)
                if name=='_error_doc_fast':ev['found']=r is not None
                return r
            except Exception as e:ev.update(status='error',error_type=type(e).__name__,error=clean_error(e));raise
            finally:ev['ms']=(time.perf_counter()-t)*1000
        setattr(obj,name,wrapped)
    def wrap_sync(obj,name):
        original=getattr(obj,name)
        def wrapped(*a,**kw):
            t=time.perf_counter();r=original(*a,**kw);ev={'stage':name,'ms':(time.perf_counter()-t)*1000}
            if name=='search':ev['vector_hits']=r
            elif name=='_rrf_fuse':ev.update(rankings=a[0],scores=r)
            elif name=='compress':ev.update(before=len(a[0]),after=len(r),chars_before=sum(len(c['content']) for c in a[0]),chars_after=sum(len(c['content']) for c in r))
            elif name=='build_context':ev.update(context=r[0],citations=r[1])
            current.setdefault('stages',[]).append(ev);return r
        setattr(obj,name,wrapped)
    for obj,name in [(client,'embed'),(client,'chat'),(client,'rerank'),(retriever,'hybrid_search'),(reranker,'rerank_candidates'),(api_diagnostic,'_error_doc_fast')]:wrap_async(obj,name)
    for obj,name in [(store,'search'),(retriever,'_rrf_fuse'),(compressor,'compress'),(compressor,'build_context')]:wrap_sync(obj,name)
    meta={'run_id':args.run_id,'questions_sha256':expected,'collection':store.COLLECTION,'cache':'doc_rag.answer direct; no answer/route/session cache','database':'existing error_code read only; no changes','concurrency':1,'retries':0,'timeout_seconds':60,'models':{k:getattr(settings,k) for k in ['embedding_model','embedding_dim','rerank_model','llm_model_small','llm_model_large']}}
    save('metadata.json',meta)
    stopped=False
    async def trial(label,coro,filename='quality.jsonl'):
        nonlocal current
        current={'label':label};t=time.perf_counter()
        try:
            r=await asyncio.wait_for(coro,60);current['result']=dataclasses.asdict(r) if dataclasses.is_dataclass(r) else r;current['status']='ok'
        except Exception as e:current.update(status='error',error_type=type(e).__name__,error=clean_error(e))
        current['elapsed_ms']=(time.perf_counter()-t)*1000
        append(filename,current);print(label,current['status'],round(current['elapsed_ms'],1),flush=True)
        return current
    try:
        store.ensure_collection(recreate=False)
        meta['schema']=store.get_client().describe_collection(store.COLLECTION);save('metadata.json',meta)
        current={'label':'ingest'}
        for source in spec['sources']:
            f=ROOT/source['path'];md=f.read_text();title=md.splitlines()[0][2:];chunks=chunk_markdown(md,title)
            for start in range(0,len(chunks),10):
                batch=chunks[start:start+10];vec=await asyncio.wait_for(client.embed([c['content'] for c in batch]),40)
                store.insert([dict(c,embedding=v,doc_title=title,category=_category(f.name),version='v1') for c,v in zip(batch,vec)])
        append('ingest.jsonl',current);meta['chunks']=store.count();save('metadata.json',meta);retriever.reset_bm25()
        # 第一题兼作端到端试跑；失败即停止质量批次，避免整批请求撞限流。
        for q in spec['questions']:
            r=await trial(q['id'],doc_rag.answer(q['query']))
            if r['status']!='ok':stopped=True;break
        # 两边都有原文依据的候选码，读取现有表的交集，不写测试数据冒充实际表。
        possible=['AUTH_KEY_EXPIRED','WEBHOOK_DELIVERY_FAILED','WEBHOOK_SIGN_INVALID','DATA_EMPTY','DATA_INCONSISTENT']
        async with AsyncSessionLocal() as s:
            rows=(await s.execute(select(ErrorCode).where(ErrorCode.code.in_(possible)))).scalars().all()
            selected=[{'code':r.code,'name':r.name,'cause':r.cause,'fix_steps':r.fix_steps,'document_sources':[x for x in spec['sources'] if r.code in (ROOT/x['path']).read_text()]} for r in rows]
        save('error_codes.json',selected)
        for entry in selected:
            code=entry['code'];query=f'{code} 含义、原因与处理步骤'
            # 每码每组各预热一次；只对成功预热的组测量。
            for group in ['rag','fast']:
                fn=(lambda:doc_rag.answer(query,error_code=code)) if group=='rag' else (lambda:api_diagnostic._error_doc_fast(code))
                if group=='rag' and stopped:
                    append('performance.jsonl',{'label':code,'group':group,'status':'blocked','reason':'quality batch service failure; circuit open'});continue
                r=await trial(code+':'+group+':warmup',fn(),'performance.jsonl')
                if r['status']!='ok':
                    if group=='rag':stopped=True
                    continue
                for i in range(4):
                    r=await trial(code+':'+group+':'+str(i+1),fn(),'performance.jsonl')
                    if r['status']!='ok' and group=='rag':stopped=True;break
        meta['provider_calls']=calls;meta['quality_circuit_open']=stopped;save('metadata.json',meta)
    except Exception as e:
        save('BLOCKED.json',{'error_type':type(e).__name__,'error':clean_error(e),'provider_calls':calls})
        print(type(e).__name__,clean_error(e),flush=True)
    finally:
        await async_engine.dispose();sync_engine.dispose()
        # 保留本轮专用索引供复核；不清空、不删除任何已有索引/缓存/会话。
        save('completion.json',{'provider_calls':calls,'collection_retained':store.COLLECTION})

if __name__=='__main__':asyncio.run(main())
