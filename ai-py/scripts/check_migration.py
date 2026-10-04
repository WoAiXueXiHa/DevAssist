"""最终隔离 MySQL + 原 API 对照 + 两个真实进程验收，零模型调用。

从 ai-py 运行 python -m scripts.check_migration。
仅创建/删除 devsupport_migration_test_* 临时库，不重建当前业务库/Redis/Milvus。
需要测试管理员创建数据库权限；开发 Compose root 密码默认 root123，可用
MIGRATION_TEST_ADMIN_USER / MIGRATION_TEST_ADMIN_PASSWORD 覆盖（不得写入报告）。
"""
import asyncio
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import uuid

import httpx
import pymysql
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def normalized(value):
    if isinstance(value, dict):
        return {k: normalized(v) for k, v in value.items() if k not in {"created_at", "updated_at", "access_token", "message_id"}}
    if isinstance(value, (list, tuple)):
        return [normalized(v) for v in value]
    if isinstance(value, str):
        value = re.sub(r"tk_20260615_[0-9a-f]{6}", "tk_normalized", value)
        value = re.sub(r"msg_[0-9a-f]{12}", "msg_normalized", value)
        value = re.sub(r"conv_[0-9a-f]{12}", "conv_normalized", value)
    return value


def main():
    shared = {k: v for k, v in dotenv_values(ROOT / "ai-py/.env").items() if v is not None}
    shared.update({k: v for k, v in os.environ.items() if k in shared or k.startswith("MYSQL_")})
    admin = pymysql.connect(host=shared.get("MYSQL_HOST", "127.0.0.1"), port=int(shared.get("MYSQL_PORT", "3307")),
                            user=os.environ.get("MIGRATION_TEST_ADMIN_USER", "root"),
                            password=os.environ.get("MIGRATION_TEST_ADMIN_PASSWORD", "root123"), charset="utf8mb4", autocommit=True)
    suffix = uuid.uuid4().hex[:10]
    original_db = f"devsupport_migration_test_{suffix}_original"
    migrated_db = f"devsupport_migration_test_{suffix}_go"
    processes = []
    evidence = {"mysql": "isolated", "model_calls": 0, "checks": []}
    try:
        with admin.cursor() as cursor:
            for name in (original_db, migrated_db):
                cursor.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
        mysql_user = os.environ.get("MIGRATION_TEST_ADMIN_USER", "root")
        mysql_password = os.environ.get("MIGRATION_TEST_ADMIN_PASSWORD", "root123")
        os.environ.update(shared)
        os.environ.update(MYSQL_DB=original_db, MYSQL_USER=mysql_user, MYSQL_PASSWORD=mysql_password,
                          BUSINESS_TOOLS_BACKEND="legacy_sql", DASHSCOPE_API_KEY="")
        from sqlalchemy import create_engine, select, func, text
        from sqlalchemy.orm import Session
        from sqlalchemy.engine import URL
        from app.db import Base
        from app import models as m
        from app.security import hash_password, create_access_token, decode_access_token
        from app.config import settings
        from datetime import datetime, timedelta

        def engine(name):
            return create_engine(URL.create("mysql+pymysql", username=mysql_user, password=mysql_password,
                    host=shared.get("MYSQL_HOST", "127.0.0.1"), port=int(shared.get("MYSQL_PORT", "3307")), database=name), pool_pre_ping=True)
        old_engine, go_engine = engine(original_db), engine(migrated_db)
        password_hash = hash_password("password123")
        baseline = datetime(2026, 6, 15, 12, 0, 0)
        def seed(db):
            Base.metadata.create_all(db)
            with Session(db) as s:
                s.add(m.Plan(id="p", name="基础套餐", qps_limit=10, monthly_quota=100, price_per_call=.01, overage_price_per_call=.02))
                s.flush()
                s.add_all([m.Tenant(id="a", name="客户A", plan_id="p"), m.Tenant(id="b", name="客户B", plan_id="p")]);s.flush()
                for uid, tenant, role in [("u","a","customer_dev"),("peer","a","customer_admin"),("other","b","customer_dev"),("staff","a","support")]:
                    s.add(m.User(id=uid, tenant_id=tenant, username=uid, password_hash=password_hash, display_name=uid, role=role))
                s.add(m.User(id="longpass", tenant_id="a", username="longpass", password_hash=hash_password("中"*25), display_name="longpass", role="customer_dev"))
                s.add(m.App(id="app", tenant_id="a", name="测试应用"));s.flush()
                s.add(m.ApiKey(id="key", app_id="app", tenant_id="a", key_masked="ak_****1234", status="ACTIVE", expire_at=baseline))
                for i, status in enumerate([200,429,200]):
                    s.add(m.ApiCallLog(request_id=f"req{i}", tenant_id="a", app_id="app", api_key_id="key", endpoint="/v1/test", http_status=status, created_at=baseline+timedelta(minutes=i)))
                s.add(m.UsageRecord(tenant_id="a", month="2026-06", call_count=123, overage_count=23))
                s.add(m.Invoice(id="invoice", tenant_id="a", month="2026-06", items={"base":1,"overage":.23}, amount=1.23, status="ISSUED"))
                for cid,tenant,user in [("conv","a","u"),("peerconv","a","peer"),("otherconv","b","other")]:
                    s.add(m.Conversation(id=cid, tenant_id=tenant, user_id=user, updated_at=baseline))
                s.flush()
                s.add(m.Message(id="initial", conversation_id="conv", role="user", content="需要支持", created_at=baseline.replace(microsecond=123456)))
                s.add(m.Ticket(ticket_id="initialticket", tenant_id="a", user_id="u", category="咨询", priority="P2", title="初始工单", conversation_id="conv", created_at=baseline))
                s.add(m.Ticket(ticket_id="otherticket", tenant_id="b", user_id="other", category="咨询", priority="P2", title="其他租户工单", created_at=baseline))
                s.add(m.AgentTrace(trace_id="trace_initial", conversation_id="conv", tenant_id="a", agent_name="fixture", step_order=1, duration_ms=10, token_usage=0, created_at=baseline))
                s.add(m.ToolCallLog(trace_id="trace_initial", tenant_id="a", tool_name="query_call_log", args_summary='{"request_id":"req1"}', created_at=baseline))
                s.add(m.TokenUsage(tenant_id="a", model="fake", total_tokens=10));s.commit()
        seed(old_engine);seed(go_engine)
        gport, aport = port(), port()
        token = shared.get("INTERNAL_SERVICE_TOKEN", "")
        if len(token)<32:
            raise RuntimeError("内部服务凭证缺失")
        env = dict(os.environ, MYSQL_DB=migrated_db, BUSINESS_TOOLS_BACKEND="go", GO_LISTEN_ADDR=f"127.0.0.1:{gport}",
                   GO_TOOLS_URL=f"http://127.0.0.1:{gport}", PYTHON_AI_URL=f"http://127.0.0.1:{aport}", AI_TIMEOUT_SECONDS="2",
                   KNOWLEDGE_DIR=str(ROOT / "data/knowledge"), PYTHONPATH=str(ROOT / "ai-py"))
        logs = tempfile.TemporaryFile(mode="w+")
        processes.append(subprocess.Popen([sys.executable,"-m","uvicorn","tests.fake_ai_server:app","--host","127.0.0.1","--port",str(aport)], cwd=ROOT / "ai-py", env=env, stdout=logs, stderr=logs))
        processes.append(subprocess.Popen([str(ROOT / "backend-go/bin/server")], cwd=ROOT / "backend-go", env=env, stdout=logs, stderr=logs))
        new = httpx.Client(base_url=f"http://127.0.0.1:{gport}", timeout=10, trust_env=False)
        for url in [f"http://127.0.0.1:{aport}/health",f"http://127.0.0.1:{gport}/health"]:
            for _ in range(100):
                try:
                    if httpx.get(url, timeout=.5, trust_env=False).status_code==200:break
                except httpx.HTTPError:pass
                if any(p.poll() is not None for p in processes):raise RuntimeError("验收进程启动失败；私有日志留在临时文件")
                time.sleep(.1)
            else:raise RuntimeError("验收服务未就绪")
        from fastapi.testclient import TestClient
        from app.main import app
        from app.agents import supervisor
        from app.llm import client as llm_client
        from app.ai_main import generate_suggestion
        from types import SimpleNamespace
        async def no_model(*args, **kwargs):return SimpleNamespace(content="推荐回复：请补充调用日志。")
        llm_client.chat = no_model
        original_calls = []
        async def original_fake(**kwargs):
            original_calls.append(kwargs)
            return {"answer":"中文🙂答案\n第二行：套餐查询已回调 Go。","intent":"doc_qa","citations":[],"card":None,"trace_id":"trace_fixture","ticket_id":None,"need_human":False,"from_cache":False,"need_clarify":False}
        supervisor.run = original_fake
        from eval import run_eval
        async def fake_eval():return {"fixture":True,"model_calls":0}
        run_eval.evaluate = fake_eval
        import logging
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpx2").setLevel(logging.WARNING)
        old = TestClient(app)
        old.__enter__()
        tokens = {uid:create_access_token(user_id=uid, tenant_id="forged", role="admin") for uid in ["u","peer","other","staff"]}
        def headers(uid):return {"Authorization":"Bearer "+tokens[uid]}
        def compare(method,path,uid="u",body=None):
            kwargs={"headers":headers(uid)}
            if body is not None:kwargs["json"]=body
            a=old.request(method,path,**kwargs);b=new.request(method,path,**kwargs)
            assert a.status_code==b.status_code,(method,path,a.status_code,b.status_code)
            assert normalized(a.json())==normalized(b.json()),(method,path,a.json(),b.json())
            if path=="/api/auth/login" and b.status_code==200:
                payload=decode_access_token(b.json()["access_token"])
                assert payload is not None and payload["sub"]==b.json()["user"]["user_id"]
                assert payload["tenant_id"]==b.json()["user"]["tenant_id"]
            evidence["checks"].append(f"{method} {path} {uid} {b.status_code}")
            return b
        for body in [{},{"username":None,"password":"x"},{"username":1,"password":"x"},{"username":"u","password":"wrong"},{"username":"u","password":"password123","extra":1}]:compare("POST","/api/auth/login",body=body)
        compare("POST","/api/auth/login",body={"username":"longpass","password":"中"*24+"不同后缀"})
        for path in ["/api/auth/login","/api/conversations/conv/messages"]:
            for raw in ["", "null", "[]", '"text"', "42", "{", '{"content":"x"} {}']:
                kwargs={"headers":dict(headers("u"),**{"Content-Type":"application/json"}),"content":raw}
                a=old.post(path,**kwargs);b=new.post(path,**kwargs)
                assert a.status_code==b.status_code and a.json()==b.json(),(path,raw,a.json(),b.json())
                evidence["checks"].append(f"validation {path} raw={raw!r}")
        for path in ["/api/auth/me","/api/conversations","/api/conversations/conv","/api/conversations/peerconv","/api/conversations/otherconv","/api/conversations/missing","/api/tickets","/api/tickets/initialticket","/api/tickets/otherticket","/api/docs","/api/docs/01","/api/docs/missing"]:compare("GET",path)
        for path in ["/api/workbench/tickets","/api/traces","/api/metrics"]:compare("GET",path)
        compare("POST","/api/eval/run","u")
        compare("POST","/api/eval/run","staff")
        for path in ["/api/workbench/tickets","/api/workbench/tickets?status=new&priority=P2&tenant_id=a","/api/workbench/tickets/initialticket","/api/traces","/api/traces?request_id=req1","/api/traces?ticket_id=initialticket","/api/traces?limit=invalid","/api/traces/trace_initial","/api/traces/missing","/api/metrics","/api/workbench/conversations/conv/suggest_reply"]:compare("GET",path,"staff")
        # 八个工具对照同一份独立 fixture。创建工具仅比较字段/副作用，不硬比随机 ID。
        from app.tools import legacy_sql
        from app.tools.registry import ToolContext
        contexts=[ToolContext("a","",False,"u","conv"),ToolContext("b","",False,"other","otherconv"),ToolContext("b","",True,"staff","conv")]
        for name,args in [("query_call_log",{}),("query_call_log",{"request_id":"req1"}),("query_call_log",{"request_id":"missing"}),("query_recent_call_stats",{}),("query_recent_call_stats",{"endpoint":"/v1/test","minutes":1}),("query_apikey_status",{}),("query_apikey_status",{"app_id":"app"}),("query_plan",{}),("query_usage",{}),("query_usage",{"month":"missing"}),("query_bill",{}),("query_ticket",{"ticket_id":"initialticket"}),("query_ticket",{"ticket_id":"missing"}),("create_ticket",{"title":"工具建单","category":"咨询","summary":"测试"})]:
            for ctx in contexts:
                args_old=dict(args,user_id=ctx.user_id,conversation_id=ctx.conversation_id)
                expected=old.portal.call(getattr(legacy_sql,name),args_old,ctx)
                actual=new.post("/internal/tools/execute",headers={"X-Internal-Token":token},json={"name":name,"args":dict(args,user_id="attacker",tenant_id="b"),"context":vars(ctx)})
                assert actual.status_code==200,(name,actual.status_code,actual.text)
                # 原 Python dict 的状态码为 int，JSON 传输后键变为 str。
                expected=json.loads(json.dumps(expected,ensure_ascii=False))
                assert normalized(expected)==normalized(actual.json()),(name,expected,actual.json())
                evidence["checks"].append(f"tool {name} internal={ctx.is_internal} tenant={ctx.tenant_id}")
        for body in [{},{"content":None},{"content":123},{"content":"手机号13812345678"}]:compare("POST","/api/conversations/conv/messages",body=body)
        for body in [{"status":"invalid"},{"status":"processing","assignee":"staff","note":"接单"},{"status":None,"assignee":"","note":None}]:compare("POST","/api/workbench/tickets/initialticket","staff",body)
        for kind in ["resolved","unresolved","need_human"]:compare("POST","/api/feedback",body={"conversation_id":"conv","type":kind})
        compare("POST","/api/workbench/conversations/conv/reply","staff",{"content":"人工回复 13812345678"})
        compare("POST","/api/workbench/conversations/conv/takeover","staff")
        compare("GET","/api/conversations/conv")
        # 以上工具生成随机工单后，核对状态变更/审计/默认 JSON 和关联身份。
        with Session(go_engine) as db:
            conv=db.get(m.Conversation,"conv");assert conv.transferred_to_human and conv.resolved_by_ai is False
            assert db.scalar(select(func.count()).select_from(m.AuditLog))==4
            ticket=db.get(m.Ticket,"initialticket");assert ticket.assignee=="" and ticket.status=="processing"
            for t in db.scalars(select(m.Ticket).where(m.Ticket.title=="工具建单")):
                assert t.user_id!="attacker" and t.conversation_id!="attacker" and t.related_request_ids==[]
        def stream(path_body):
            r=new.post("/api/chat",headers=headers("u"),json=path_body)
            assert r.status_code==200,r.text
            assert "event: meta" in r.text and "event: done" in r.text
            assert "�" not in r.text
            return r.text
        # 人工模式不应新增 trace；然后新会话走真实内部 HTTP 与 Go 工具回调。
        with Session(go_engine) as db:before=db.scalar(select(func.count()).select_from(m.AgentTrace))
        assert '"human_mode":true' in stream({"message":"人工补充","conversation_id":"conv"})
        with Session(go_engine) as db:assert db.scalar(select(func.count()).select_from(m.AgentTrace))==before
        normal=stream({"message":"正常中文🙂","conversation_id":None});assert "第二行" in normal
        tool=stream({"message":"__tool_ticket__","conversation_id":None});assert '"need_human":true' in tool
        with Session(go_engine) as db:
            ticket=db.scalar(select(m.Ticket).where(m.Ticket.title=="模拟诊断建单"));assert ticket.user_id=="u" and ticket.tenant_id=="a" and ticket.conversation_id!="attacker"
            assert db.get(m.Conversation,ticket.conversation_id).transferred_to_human
            assert db.scalar(select(func.count()).select_from(m.ToolCallLog).where(m.ToolCallLog.trace_id=="trace_fixture"))==3
        for query in ["__fail__","__slow__"]:
            r=new.post("/api/chat",headers=headers("u"),json={"message":query});assert r.status_code==502 and "event: done" not in r.text
        # AI 生成后落库失败：用隔离库触发器模拟，必须回滚助手消息，不回滚已提交的用户消息。
        with go_engine.begin() as con:
            con.execute(text("CREATE TRIGGER fail_assistant BEFORE INSERT ON message FOR EACH ROW BEGIN IF NEW.role='assistant' THEN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='fixture failure'; END IF; END"))
        r=new.post("/api/chat",headers=headers("u"),json={"message":"数据库失败测试"});assert r.status_code==500 and "event: done" not in r.text
        with go_engine.begin() as con:con.execute(text("DROP TRIGGER fail_assistant"))
        # 新建未接管会话，才能证明失败时标记和新消息均回滚（已接管 fixture 无法证明）。
        with Session(go_engine) as db:
            db.add(m.Conversation(id="rollbackconv", tenant_id="a", user_id="u", updated_at=baseline));db.commit()
        with go_engine.begin() as con:
            con.execute(text("CREATE TRIGGER fail_audit BEFORE INSERT ON audit_log FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='fixture failure'"))
        r=new.post("/api/workbench/tickets/initialticket",headers=headers("staff"),json={"status":"closed"});assert r.status_code==500
        with Session(go_engine) as db:assert db.get(m.Ticket,"initialticket").status=="processing"
        for action,body in [("reply",{"content":"回滚人工回复"}),("takeover",None)]:
            r=new.post(f"/api/workbench/conversations/rollbackconv/{action}",headers=headers("staff"),json=body);assert r.status_code==500
            with Session(go_engine) as db:
                conv=db.get(m.Conversation,"rollbackconv")
                assert not conv.transferred_to_human and conv.updated_at==baseline
                assert db.scalar(select(func.count()).select_from(m.Message).where(m.Message.conversation_id==conv.id))==0
            evidence["checks"].append(f"audit failure rolls back {action} message/state")
        with go_engine.begin() as con:con.execute(text("DROP TRIGGER fail_audit"))
        with go_engine.begin() as con:
            con.execute(text("CREATE TRIGGER fail_ticket BEFORE INSERT ON ticket FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='fixture failure'"))
        r=new.post("/api/feedback",headers=headers("u"),json={"conversation_id":"rollbackconv","type":"need_human"});assert r.status_code==500
        with Session(go_engine) as db:
            conv=db.get(m.Conversation,"rollbackconv")
            assert not conv.transferred_to_human and conv.updated_at==baseline
            assert db.scalar(select(func.count()).select_from(m.Feedback).where(m.Feedback.conversation_id==conv.id))==0
            assert db.scalar(select(func.count()).select_from(m.Ticket).where(m.Ticket.conversation_id==conv.id))==0
        with go_engine.begin() as con:con.execute(text("DROP TRIGGER fail_ticket"))
        evidence["checks"].append("ticket failure rolls back feedback/state")
        def events(response):
            assert response.status_code==200 and "text/event-stream" in response.headers["content-type"]
            result=[]
            for block in response.text.replace("\r\n","\n").split("\n\n"):
                lines=block.splitlines()
                name=next((line[6:].strip() for line in lines if line.startswith("event:")),None)
                if name:
                    data="\n".join(line[6:] if line.startswith("data: ") else line[5:] for line in lines if line.startswith("data:"))
                    result.append((name,json.loads(data) if name in {"meta","done"} else data))
            return result
        # 同租户同伴、跨租户、丢失会话、内部跨租户及人工模式逐个对照事件顺序/中文块/完整答案。
        for uid,cid,reuse in [("u","peerconv",True),("u","otherconv",False),("u","missing",False),("staff","otherconv",True),("u","conv",True)]:
            body={"message":"会话复用中文🙂\n下一行","conversation_id":cid}
            a=events(old.post("/api/chat",headers=headers(uid),json=body))
            b=events(new.post("/api/chat",headers=headers(uid),json=body))
            assert normalized(a)==normalized(b),(uid,cid,a,b)
            actual_id=b[0][1]["conversation_id"]
            assert (actual_id==cid)==reuse
            with Session(go_engine) as db:
                conversation=db.get(m.Conversation,actual_id)
                assert conversation.tenant_id==("b" if uid=="staff" else "a")
                if cid=="conv":assert b[-1][1]=={"human_mode":True}
                else:
                    call=original_calls[-1]
                    assert call["tenant_id"]==conversation.tenant_id and call["user_id"]==uid
                    assert db.scalar(select(func.count()).select_from(m.AgentTrace).where(m.AgentTrace.conversation_id==actual_id,m.AgentTrace.tenant_id==conversation.tenant_id))>0
            evidence["checks"].append(f"old/Go SSE and conversation reuse uid={uid} requested={cid}")
        if os.environ.get("MIGRATION_BROWSER_CHECK") == "1":
            fport = port()
            node = os.environ.get("MIGRATION_NODE", "node")
            browser_env = dict(env, GO_API_TARGET=f"http://127.0.0.1:{gport}")
            processes.append(subprocess.Popen([node,str(ROOT / "frontend/node_modules/vite/bin/vite.js"),"--host","127.0.0.1","--port",str(fport),"--strictPort"],cwd=ROOT / "frontend",env=browser_env,stdout=logs,stderr=logs))
            for _ in range(100):
                try:
                    if httpx.get(f"http://127.0.0.1:{fport}",trust_env=False,timeout=.5).status_code==200:break
                except httpx.HTTPError:pass
                time.sleep(.1)
            else:raise RuntimeError("浏览器验收前端未就绪")
            run = subprocess.run([node,str(ROOT / "frontend/tests/browser-migration.mjs"),f"http://127.0.0.1:{fport}"],cwd=ROOT / "frontend",env=browser_env,capture_output=True,text=True,timeout=90)
            if run.returncode:raise RuntimeError("浏览器验收失败: "+run.stderr[-3000:])
            browser_result=json.loads(run.stdout.strip().splitlines()[-1])
            evidence["browser"]=browser_result
            evidence["checks"].extend(browser_result["checks"])
        # 停 AI 后脱敏不能绕过，消息总数必须不变。
        processes[0].terminate();processes[0].wait(timeout=10)
        with Session(go_engine) as db:before=db.scalar(select(func.count()).select_from(m.Message))
        r=new.post("/api/conversations/conv/messages",headers=headers("u"),json={"content":"13812345678"});assert r.status_code==502
        with Session(go_engine) as db:assert db.scalar(select(func.count()).select_from(m.Message))==before
        assert new.post("/internal/tools/execute",json={}).status_code==401
        evidence["checks"].extend(["Go -> Python -> Go -> MySQL -> SSE", "human mode no AI", "AI 503/timeout", "assistant DB failure", "audit transaction rollback", "AI unavailable no plaintext write"])
        evidence["passed"]=True
        target=ROOT / "docs/GO_PYTHON_MIGRATION_TEST_EVIDENCE.json"
        target.write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+"\n")
        print(f"PASS: {len(evidence['checks'])} isolated migration checks; model_calls=0; temporary databases removed on exit")
        from app.db import async_engine
        old.portal.call(async_engine.dispose)
        new.close();old.__exit__(None,None,None);old_engine.dispose();go_engine.dispose()
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait()
        with admin.cursor() as cursor:
            for name in (original_db,migrated_db):cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")
        admin.close()


if __name__=="__main__":
    main()
