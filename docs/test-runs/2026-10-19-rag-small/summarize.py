"""只做确定性统计；语义评分来自单独保存的Codex逐题复核，不用关键词评分冒充人工。"""
import hashlib,json,statistics,sys
from pathlib import Path
BASE=Path(__file__).resolve().parent
run=BASE/('run_'+sys.argv[1])
def lines(name):
 p=run/name
 return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []
def save(name,obj): (run/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
spec=json.loads((BASE/'questions.frozen.json').read_text());review=json.loads((BASE/'reviews.json').read_text());scores={r['id']:r for r in review['reviews']};raw={r['label']:r for r in lines('quality.jsonl')}
assert review['reviewed_run']==sys.argv[1] and hashlib.sha256((run/'quality.jsonl').read_bytes()).hexdigest()==review['reviewed_quality_sha256'], '新结果需要重新进行Codex逐题复核，禁止沿用旧评分'
scored=[]
for q in spec['questions']:
 r=raw.get(q['id']);s=scores[q['id']];top=[];fused=[]
 if r:
  for ev in r['stages']:
   if ev['stage']=='rerank_candidates':top=ev.get('top5',[])[:5]
   if ev['stage']=='hybrid_search':fused=ev.get('candidates',[])[:5]
 def match(chunks):return any((c['doc_title'],c['section'])==(g['doc_title'],g['section']) for c in chunks for g in q['relevant'])
 citation_match=match(r.get('result',{}).get('citations',[])) if r else False
 fact_checks=[{'fact':fact,'coverage':'partial_or_missing' if i in s.get('missing_fact_indices',[]) else 'covered'} for i,fact in enumerate(q['required_facts'])] if r and r.get('status')=='ok' else []
 scored.append({'id':q['id'],'query':q['query'],'answerable':q['answerable'],'status':r.get('status') if r else 'not_run','answer':r.get('result',{}).get('answer') if r else None,'citations':r.get('result',{}).get('citations',[]) if r else [],'evidence_hit_at_5':match(top) if q['answerable'] else None,'rrf_hit_at_5':match(fused) if q['answerable'] else None,'citation_matches_gold':citation_match if q['answerable'] else None,'review':s if r and r.get('status')=='ok' else {'pass':False,'reason':'未成功运行，不属于语义答案失败或正确拒答'},'fact_checks':fact_checks})
save('scored.json',{'reviewer':review['reviewer'],'independent_human_review':False,'rubric':review['rubric'],'questions':scored})
a=[r for r in scored if r['answerable']];u=[r for r in scored if not r['answerable']]
summary={'total_frozen':len(scored),'completed':sum(r['status']=='ok' for r in scored),'answerable':len(a),'evidence_hit_at_5':{'numerator':sum(r['evidence_hit_at_5'] for r in a),'denominator':len(a)},'rrf_hit_at_5':{'numerator':sum(r['rrf_hit_at_5'] for r in a),'denominator':len(a)},'answer_pass':{'numerator':sum(r['review']['pass'] for r in a),'denominator':len(a)},'all_question_pass':{'numerator':sum(r['review']['pass'] for r in scored),'denominator':len(scored)},'correct_refusal':{'numerator':sum(r['review'].get('correct_refusal',False) for r in u),'denominator':len(u)},'verbal_abstention':sum(r['review'].get('verbal_abstention',False) for r in u),'format_issues':[r['id'] for r in scored if r['review'].get('format_issue')]}
perf={}
for r in lines('performance.jsonl'):
 parts=r['label'].split(':')
 if len(parts)!=3 or parts[-1]=='warmup':continue
 code,group,rep=parts;bucket=perf.setdefault(code,{}).setdefault(group,{'samples_ms':[],'failures':0,'stages':set(),'missing_basis':0})
 for e in r.get('stages',[]):bucket['stages'].add(e['stage'])
 if r['status']=='ok':
  bucket['samples_ms'].append(r['elapsed_ms'])
  if (group=='rag' and not r.get('result',{}).get('citations')) or (group=='fast' and r.get('result') is None):bucket['missing_basis']+=1
 else:bucket['failures']+=1
for code,groups in perf.items():
 for group,b in groups.items():
  b['stages']=sorted(b['stages']);b['mean_ms']=statistics.mean(b['samples_ms']) if b['samples_ms'] else None;b['p50_ms']=statistics.median(b['samples_ms']) if b['samples_ms'] else None
save('metrics.json',summary);save('performance-summary.json',perf)
# 对完整trace核验，而非只看源码或耗时。
verification={'vector_dimensions':sorted({d for r in lines('ingest.jsonl') for e in r.get('stages',[]) for d in e.get('dimensions',[])}),'questions_with_nonempty_two_rankings':0,'rerank_top5_records':0,'compress_called':0,'compress_reduced_records':[],'citations_built':0,'no_hit_returns':[]}
for r in raw.values():
 for e in r['stages']:
  if e['stage']=='_rrf_fuse' and all(e['rankings']):verification['questions_with_nonempty_two_rankings']+=1
  if e['stage']=='rerank_candidates' and e.get('top5'):verification['rerank_top5_records']+=1
  if e['stage']=='compress':
   verification['compress_called']+=1
   if e['chars_after']<e['chars_before']:verification['compress_reduced_records'].append({'id':r['label'],'before':e['chars_before'],'after':e['chars_after']})
  if e['stage']=='build_context' and e.get('citations'):verification['citations_built']+=1
 if not r.get('result',{}).get('hit'):verification['no_hit_returns'].append(r['label'])
save('verification.json',verification)
print(json.dumps(summary,ensure_ascii=False,indent=2))
