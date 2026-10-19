"""真实 Go HTTP 用户入口；临时数据库、Redis、服务端口、新会话。"""
import json,os,socket,subprocess,sys,time,uuid
from pathlib import Path
from dotenv import dotenv_values
import httpx,pymysql
ROOT=Path(__file__).resolve().parents[3];BASE=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'ai-py'))
def port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
def main():
    run_id=sys.argv[1];out=BASE/('run_'+run_id);out.mkdir(exist_ok=True)
    if (out/'entry.jsonl').exists():raise RuntimeError('入口结果已存在，禁止覆盖')
    env=dict(os.environ,**{k:v for k,v in dotenv_values(ROOT/'ai-py/.env').items() if v is not None})
    for k in ['MYSQL_HOST','MYSQL_PORT','MYSQL_USER','MYSQL_PASSWORD']:env[k]=os.environ.get(k,env.get(k,{'MYSQL_HOST':'127.0.0.1','MYSQL_PORT':'3307','MYSQL_USER':'devsupport','MYSQL_PASSWORD':'devsupport123'}[k]))
    original_db=env.get('MYSQL_DB','devsupport');db='rag_entry_'+uuid.uuid4().hex[:10]
    admin=pymysql.connect(host=env['MYSQL_HOST'],port=int(env['MYSQL_PORT']),user=os.environ.get('EVAL_ADMIN_USER','root'),password=os.environ.get('EVAL_ADMIN_PASSWORD','root123'),charset='utf8mb4',autocommit=True)
    procs=[];log=(out/'entry-services.log').open('w');summary={'database':db,'original_database_read_only':original_db,'cache_bypass':True,'temporary_db_removed':False}
    try:
        with admin.cursor() as c:
            c.execute(f'CREATE DATABASE `{db}` CHARACTER SET utf8mb4')
            c.execute(f'SELECT code,name,http_status,cause,fix_steps FROM `{original_db}`.error_code');codes=c.fetchall()
        gp,ap,rp=port(),port(),port()
        env.update(MYSQL_DB=db,MYSQL_USER=os.environ.get('EVAL_ADMIN_USER','root'),MYSQL_PASSWORD=os.environ.get('EVAL_ADMIN_PASSWORD','root123'),REDIS_URL=f'redis://127.0.0.1:{rp}/0',MILVUS_COLLECTION='rag_small_'+run_id,GO_LISTEN_ADDR=f'127.0.0.1:{gp}',GO_TOOLS_URL=f'http://127.0.0.1:{gp}',PYTHON_AI_URL=f'http://127.0.0.1:{ap}',AI_TIMEOUT_SECONDS='90',BUSINESS_TOOLS_BACKEND='go',ENTRY_EVENTS=str(out/'entry-events.jsonl'),PYTHONPATH=str(BASE)+':'+str(ROOT/'ai-py'),KNOWLEDGE_DIR=str(ROOT/'data/knowledge'))
        os.environ.update(env)
        from app.db import Base,sync_engine
        from app.models import Plan,Tenant,User,ErrorCode
        from app.security import create_access_token
        from sqlalchemy.orm import Session
        Base.metadata.create_all(sync_engine)
        with Session(sync_engine) as s:
            s.add(Plan(id='p',name='隔离入口测试',qps_limit=10,monthly_quota=100));s.flush();s.add(Tenant(id='eval',name='隔离',plan_id='p'));s.flush();s.add(User(id='eval_user',tenant_id='eval',username='eval',password_hash='not-used',display_name='隔离测试',role='customer_dev'))
            for row in codes:s.add(ErrorCode(**dict(zip(['code','name','http_status','cause','fix_steps'],row))))
            s.commit()
        procs.append(subprocess.Popen(['redis-server','--bind','127.0.0.1','--port',str(rp),'--save','','--appendonly','no'],stdout=log,stderr=log))
        procs.append(subprocess.Popen([sys.executable,'-m','uvicorn','entry_app:app','--host','127.0.0.1','--port',str(ap)],cwd=ROOT/'ai-py',env=env,stdout=log,stderr=log))
        procs.append(subprocess.Popen([os.environ.get('EVAL_GO_BINARY','/tmp/devassist-rag-small-go')],cwd=ROOT/'backend-go',env=env,stdout=log,stderr=log))
        for url in [f'http://127.0.0.1:{ap}/health',f'http://127.0.0.1:{gp}/health']:
            for _ in range(100):
                try:
                    if httpx.get(url,timeout=.5,trust_env=False).status_code==200:break
                except httpx.HTTPError:pass
                if any(p.poll() is not None for p in procs):raise RuntimeError('入口测试进程退出，见日志')
                time.sleep(.1)
            else:raise RuntimeError('入口未就绪')
        token=create_access_token(user_id='eval_user',tenant_id='eval',role='customer_dev')
        queries=['SIGN_INVALID 是什么原因？','本次调用报错 AUTH_KEY_EXPIRED，请帮我诊断。','本次调用报错 WEBHOOK_SIGN_INVALID，请定位失败原因。','本次调用报错 UNKNOWN_TEST_CODE，请帮我诊断。','接口返回 401，请帮我定位。','接口返回 DATA_EMPTY，请帮我排查本次失败。']
        for q in queries:
            eventfile=out/'entry-events.jsonl';before=len(eventfile.read_text().splitlines()) if eventfile.exists() else 0;t=time.perf_counter()
            res=httpx.post(f'http://127.0.0.1:{gp}/api/chat',headers={'Authorization':'Bearer '+token},json={'message':q,'client_request_id':uuid.uuid4().hex},timeout=110,trust_env=False)
            events=[json.loads(x) for x in eventfile.read_text().splitlines()[before:]] if eventfile.exists() else []
            row={'query':q,'http_status':res.status_code,'elapsed_ms':(time.perf_counter()-t)*1000,'sse':res.text,'events':events}
            with (out/'entry.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
            print(q,res.status_code,[e['stage'] for e in events],flush=True)
            if any(e.get('error_type')=='RateLimitError' and e['stage']=='classify' for e in events):summary['blocked']='intent model rate limit';break
        sync_engine.dispose()
    except Exception as e:summary.update(blocked=type(e).__name__,error=str(e)[:500]);print(summary,flush=True)
    finally:
        for p in reversed(procs):
            p.terminate()
            try:p.wait(timeout=5)
            except subprocess.TimeoutExpired:p.kill();p.wait()
        with admin.cursor() as c:c.execute(f'DROP DATABASE IF EXISTS `{db}`')
        summary['temporary_db_removed']=True;admin.close();log.close();(out/'entry-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
