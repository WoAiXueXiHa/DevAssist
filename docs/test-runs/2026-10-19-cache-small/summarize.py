"""确定性复算缓存耗时、模型调用和隔离样本；不调用模型。"""
import json,statistics,sys
from pathlib import Path
BASE=Path(__file__).resolve().parent;out=BASE/('run_'+sys.argv[1])
def read(name):return [json.loads(x) for x in (out/name).read_text().splitlines()]
rows=read('samples.jsonl');probes=read('probes.jsonl')
def stats(rs):
 good=[r for r in rs if r['status']=='ok'];values=[r['elapsed_ms'] for r in good]
 return {'count':len(rs),'successful':len(good),'failures':len(rs)-len(good),'samples_ms':values,'mean_ms':statistics.mean(values) if values else None,'p50_ms':statistics.median(values) if values else None,'calls':{k:sum(r['model_calls'][k] for r in rs) for k in ['embed','rerank','chat']}}
def group(ids):
 cold=[r for r in rows if len(r['label'].split(':'))==3 and r['label'].split(':')[0] in ids and ':cold:' in r['label']]
 hit=[r for r in rows if len(r['label'].split(':'))==3 and r['label'].split(':')[0] in ids and ':hit:' in r['label']]
 a,b=stats(cold),stats(hit)
 return {'cold_miss':a,'warm_hit':b,'latency_reduction_pct':100*(1-b['mean_ms']/a['mean_ms']) if a['mean_ms'] and b['mean_ms'] else None,'speed_ratio':a['mean_ms']/b['mean_ms'] if a['mean_ms'] and b['mean_ms'] else None,'hits':sum(r.get('result',{}).get('cache_hit',False) for r in hit)}
result={'scope':'cache+RAG function pipeline; not HTTP or full supervisor; cold is cache miss, not cache disabled','primary_doc_questions':group(['C1','C2']),'all_module_questions':group(['C1','C2','C3']),'per_case':{c:group([c]) for c in ['C1','C2','C3']},'C3_caveat':'套餐问题可能路由billing；仅说明缓存模块行为，排除出可用于简历的文档问答主统计。入口分类因限流均未实测。','probes':[{'label':r['label'],'status':r['status'],**r.get('result',{})} for r in probes], 'same_query_payload_preserved':[],'unperformed_planned_probe':['similarity_threshold_boundary']}
for r in rows:
 if ':hit:' in r['label']:
  seed=next(x for x in rows if x['label']==r['label'].split(':')[0]+':warmup')
  result['same_query_payload_preserved'].append({'label':r['label'],'equal':all(seed['result'][k]==r['result'][k] for k in ['answer','citations','card'])})
(out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items() if k not in ['probes','same_query_payload_preserved']},ensure_ascii=False,indent=2))
for r in probes:print(r['label'],r['result']['cache_hit'],r['result']['best_similarity'])
