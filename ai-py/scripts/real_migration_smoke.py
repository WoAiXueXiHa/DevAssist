"""一批仅一个真实文档问答，经 Go -> 原 Python AI -> 原知识库 -> Go SSE。

需要现有 Milvus 已有知识数据，以及独立测试 Redis 地址；不改已有业务数据库。
费用与 Token 为按 API 用量估算，非账号账单。仅在零付费回归完成后显式执行。
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

import httpx
import pymysql
from dotenv import dotenv_values
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

from scripts.check_migration import port

ROOT=Path(__file__).resolve().parents[2]


def main():
    shared={k:v for k,v in dotenv_values(ROOT / "ai-py/.env").items() if v is not None}
    for k,v in os.environ.items():
        if k in shared or k.startswith("MYSQL_") or k in {"MILVUS_URI","REDIS_URL"}:shared[k]=v
    if "MIGRATION_REAL_REDIS_URL" not in os.environ:raise RuntimeError("必须显式提供独立测试 Redis，防止改动原缓存")
    admin_user=os.environ.get("MIGRATION_TEST_ADMIN_USER","root")
    admin_password=os.environ.get("MIGRATION_TEST_ADMIN_PASSWORD","root123")
    name="devsupport_migration_test_real_"+uuid.uuid4().hex[:10]
    admin=pymysql.connect(host=shared.get("MYSQL_HOST","127.0.0.1"),port=int(shared.get("MYSQL_PORT",3307)),user=admin_user,password=admin_password,autocommit=True,charset="utf8mb4")
    processes=[];engine=None
    evidence={"sample_count":1,"query":"签名算法怎么生成？请说明参与签名的参数和服务端验签步骤。","database":"isolated","redis":"isolated ephemeral","milvus":"existing collection read only","corpus_reingested":False}
    target=ROOT / "docs/GO_PYTHON_REAL_AI_EVIDENCE.json"
    ledger=ROOT / "docs/GO_PYTHON_REAL_AI_COST.json"
    try:
        with admin.cursor() as cursor:cursor.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
        env=dict(os.environ,**shared)
        env.update(MYSQL_DB=name,MYSQL_USER=admin_user,MYSQL_PASSWORD=admin_password,BUSINESS_TOOLS_BACKEND="go",REDIS_URL=os.environ["MIGRATION_REAL_REDIS_URL"])
        os.environ.update(env)
        from app.db import Base
        from app.models import Plan,Tenant,User,Message,Ticket,AgentTrace
        from app.security import create_access_token,hash_password
        from app.rag.store import count
        # 先确认已有知识库可读取，未就绪不发任何付费请求。
        evidence["knowledge_chunks"]=count()
        if evidence["knowledge_chunks"]<=0:raise RuntimeError("现有知识库无数据，禁止自动付费入库")
        engine=create_engine(URL.create("mysql+pymysql",username=admin_user,password=admin_password,host=env["MYSQL_HOST"],port=int(env["MYSQL_PORT"]),database=name))
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            db.add(Plan(id="p",name="测试套餐",qps_limit=10,monthly_quota=100));db.flush()
            db.add(Tenant(id="migration_real",name="隔离真实验收",plan_id="p"));db.flush()
            db.add(User(id="migration_user",tenant_id="migration_real",username="migration",display_name="测试用户",role="customer_dev",password_hash=hash_password("not-used")));db.commit()
        gport,aport=port(),port()
        env.update(GO_LISTEN_ADDR=f"127.0.0.1:{gport}",GO_TOOLS_URL=f"http://127.0.0.1:{gport}",PYTHON_AI_URL=f"http://127.0.0.1:{aport}",AI_TIMEOUT_SECONDS="180",MIGRATION_REAL_LEDGER=str(ledger),KNOWLEDGE_DIR=str(ROOT / "data/knowledge"),PYTHONPATH=str(ROOT / "ai-py"))
        logs=tempfile.TemporaryFile(mode="w+")
        processes.append(subprocess.Popen([sys.executable,"-m","uvicorn","tests.bounded_real_ai:app","--host","127.0.0.1","--port",str(aport)],cwd=ROOT / "ai-py",env=env,stdout=logs,stderr=logs))
        processes.append(subprocess.Popen([str(ROOT / "backend-go/bin/server")],cwd=ROOT / "backend-go",env=env,stdout=logs,stderr=logs))
        for url in [f"http://127.0.0.1:{aport}/health",f"http://127.0.0.1:{gport}/health"]:
            for _ in range(100):
                try:
                    if httpx.get(url,timeout=.5,trust_env=False).status_code==200:break
                except httpx.HTTPError:pass
                if any(p.poll() is not None for p in processes):raise RuntimeError("真实验收进程启动失败")
                time.sleep(.1)
            else:raise RuntimeError("真实验收进程未就绪")
        token=create_access_token(user_id="migration_user",tenant_id="migration_real",role="customer_dev")
        with httpx.Client(base_url=f"http://127.0.0.1:{gport}",timeout=180,trust_env=False) as http:
            response=http.post("/api/chat",headers={"Authorization":"Bearer "+token},json={"message":evidence["query"]})
            evidence["http_status"]=response.status_code
            if response.status_code!=200:raise RuntimeError("真实聊天调用失败")
            current="";done=None;meta=None
            for line in response.text.splitlines():
                if line.startswith("event:"):current=line[6:].strip()
                elif line.startswith("data:") and current in {"meta","done"}:
                    value=json.loads(line[5:].strip())
                    if current=="meta":meta=value
                    else:done=value
            if done is None:raise RuntimeError("真实 SSE 缺少 done")
            evidence["result"]=done
            with Session(engine) as db:
                messages=db.query(Message).filter(Message.conversation_id==meta["conversation_id"]).order_by(Message.created_at).all()
                evidence["messages_persisted"]=[m.role for m in messages]
                evidence["answer_matches_mysql"]=len(messages)==2 and messages[-1].content==done["answer"]
                evidence["traces"]=db.query(AgentTrace).filter(AgentTrace.trace_id==done.get("trace_id")).count()
                evidence["tickets_created"]=db.query(Ticket).count()
            cost=json.loads(ledger.read_text());evidence["provider_calls"]=len(cost["calls"])
            evidence["estimated_cny"]=sum(c.get("estimated_cny",0) for c in cost["calls"])
            evidence["reserved_upper_cny"]=cost["reserved_upper_cny"]
            evidence["unknown_usage_calls"]=sum("usage" not in c for c in cost["calls"])
            evidence["account_bill_verified"]=False
            evidence["passed"]=bool(evidence["answer_matches_mysql"] and done.get("citations") and not done.get("need_human") and evidence["provider_calls"]>0)
            target.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+"\n")
            print(json.dumps({k:evidence[k] for k in ["passed","provider_calls","estimated_cny","reserved_upper_cny","unknown_usage_calls"]},ensure_ascii=False))
            if not evidence["passed"]:raise RuntimeError("真实样本未满足文档问答验收；结果已记录，停止本批，不追加请求")
    finally:
        for p in reversed(processes):
            if p.poll() is None:
                p.terminate()
                try:p.wait(timeout=10)
                except subprocess.TimeoutExpired:p.kill();p.wait()
        if engine is not None:engine.dispose()
        with admin.cursor() as cursor:cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")
        admin.close()


if __name__=="__main__":main()
