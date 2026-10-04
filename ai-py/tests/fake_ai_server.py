"""仅隔离集成验收使用：禁止真实模型；真实 Python 入口 + 工具 registry + MySQL 记录。"""
import asyncio
from types import SimpleNamespace

from fastapi import HTTPException

from app.ai_main import app
from app.agents import supervisor
from app.config import settings
from app.db import AsyncSessionLocal
from app.llm import client
from app.models import AgentTrace, TokenUsage
from app.tools.registry import ToolContext, execute, load_tools

settings.dashscope_api_key = ""
load_tools()


async def fake_chat(*args, **kwargs):
    return SimpleNamespace(content="推荐回复：请补充调用日志。")


async def fake_run(*, query, tenant_id, user_id, conversation_id, is_internal=False):
    if query == "__fail__":
        raise HTTPException(503, "模拟 AI 服务不可用")
    if query == "__slow__":
        await asyncio.sleep(3)
    ctx = ToolContext(tenant_id, "trace_fixture", is_internal, user_id, conversation_id)
    # 保留真实工具 HTTP 适配、registry 包装/日志，检查 Go -> Python -> Go 回调。
    plan = await execute("query_plan", {"tenant_id": "attacker"}, ctx)
    if not plan["ok"]:
        raise HTTPException(503, "工具回调失败")
    tid = None
    if query == "__tool_ticket__":
        ticket = await execute("create_ticket", {
            "title": "模拟诊断建单", "category": "API报错", "summary": "测试上下文",
            "user_id": "attacker", "conversation_id": "attacker", "is_internal": True,
        }, ctx)
        if not ticket["ok"]:
            raise HTTPException(503, "建单回调失败")
        tid = ticket["data"]["ticket_id"]
    async with AsyncSessionLocal() as db:
        db.add(AgentTrace(trace_id="trace_fixture", tenant_id=tenant_id, conversation_id=conversation_id,
                          agent_name="fixture", step_order=1, duration_ms=12, token_usage=0))
        db.add(TokenUsage(tenant_id=tenant_id, conversation_id=conversation_id, model="fake", total_tokens=0))
        await db.commit()
    return {"answer": "中文🙂答案\n第二行：套餐查询已回调 Go。", "intent": "doc_qa", "citations": [],
            "card": None, "trace_id": "trace_fixture", "ticket_id": tid, "need_human": tid is not None,
            "from_cache": False, "need_clarify": False}


client.chat = fake_chat
supervisor.run = fake_run

from eval import run_eval

async def fake_eval():
    return {"fixture": True, "model_calls": 0}

run_eval.evaluate = fake_eval
