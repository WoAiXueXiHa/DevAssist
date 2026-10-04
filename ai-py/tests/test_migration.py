"""新增 AI 入口/工具适配验收：模拟 HTTP 与模型，零付费。"""

from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import ai_main
from app.config import settings
from app.tools import go_client
from app.tools.registry import REGISTRY, ToolContext, load_tools

TOKEN = "test-private-service-token-0123456789"


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setattr(settings, "internal_service_token", TOKEN)
    monkeypatch.setattr(settings, "business_tools_backend", "go")
    with TestClient(ai_main.app) as client:
        yield client


def test_private_routes_and_pure_cleaning(service):
    routes = {route.path for route in ai_main.app.routes}
    assert "/api/chat" not in routes and "/api/auth/login" not in routes
    assert service.post("/internal/ai/desensitize", json={"texts": ["hello"]}).status_code == 401
    response = service.post(
        "/internal/ai/desensitize", json={"texts": ["hello", "手机号 13812345678"]},
        headers={"X-Internal-Token": TOKEN},
    )
    assert response.status_code == 200
    assert response.json()["texts"][0] == "hello"
    assert "13812345678" not in response.json()["texts"][1]


def test_internal_identity_and_model_output(service, monkeypatch):
    from app.agents import supervisor

    received = {}

    async def fake_run(**kwargs):
        received.update(kwargs)
        return {"answer": "中文答案", "intent": "doc_qa", "citations": []}

    monkeypatch.setattr(supervisor, "run", fake_run)
    body = {"query": "问", "tenant_id": "tenant", "user_id": "user", "conversation_id": "conv", "is_internal": False}
    r = service.post("/internal/ai/run", json=body, headers={"X-Internal-Token": TOKEN})
    assert r.status_code == 200 and r.json()["answer"] == "中文答案"
    assert received == body
    body["model_args"] = {"is_internal": True}
    assert service.post("/internal/ai/run", json=body, headers={"X-Internal-Token": TOKEN}).status_code == 422


@pytest.mark.asyncio
async def test_eight_adapters_and_identity(monkeypatch):
    monkeypatch.setattr(settings, "internal_service_token", TOKEN)
    monkeypatch.setattr(settings, "business_tools_backend", "go")
    names = ["query_call_log", "query_recent_call_stats", "query_apikey_status", "query_plan", "query_usage", "query_bill", "create_ticket", "query_ticket"]
    seen = []

    def receive(request):
        import json

        payload = json.loads(request.content)
        assert request.headers["X-Internal-Token"] == TOKEN
        assert payload["context"] == {"tenant_id": "trusted", "trace_id": "trace", "is_internal": False, "user_id": "user", "conversation_id": "conv"}
        assert "user_id" not in payload["args"] and "tenant_id" not in payload["args"]
        seen.append(payload["name"])
        return httpx.Response(200, json={"found": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(receive), base_url="http://go") as client:
        monkeypatch.setattr(go_client, "_client", client)
        load_tools()
        ctx = ToolContext("trusted", "trace", False, "user", "conv")
        for name in names:
            result = await REGISTRY[name].func({"user_id": "attacker", "tenant_id": "other", "is_internal": True}, ctx)
            assert result == {"found": True}  # 薄适配不能重复包装 ok/data。
    monkeypatch.setattr(go_client, "_client", None)
    assert seen == names
    assert all(REGISTRY[n].high_risk for n in ["refund", "change_plan", "reset_api_key"])


@pytest.mark.asyncio
async def test_failure_has_no_sql_fallback(monkeypatch):
    monkeypatch.setattr(settings, "internal_service_token", TOKEN)
    monkeypatch.setattr(settings, "business_tools_backend", "go")
    calls = []

    def unavailable(request):
        calls.append(request)
        return httpx.Response(503, text="private-response-must-not-leak")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable), base_url="http://go") as client:
        monkeypatch.setattr(go_client, "_client", client)
        with pytest.raises(RuntimeError, match="Go 业务工具返回状态 503"):
            await go_client.execute_business("query_bill", {}, ToolContext("t"))
    monkeypatch.setattr(go_client, "_client", None)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_registry_retains_timeout_retry_and_single_wrapper(monkeypatch):
    from app.tools import registry

    load_tools()
    calls, logs = [], []

    async def fail_then_return(args, ctx):
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError("temporary")
        return {"found": False}

    async def log(*args):
        logs.append(args)

    monkeypatch.setitem(REGISTRY, "query_ticket", replace(REGISTRY["query_ticket"], func=fail_then_return))
    monkeypatch.setattr(registry, "_log_call", log)
    assert await registry.execute("query_ticket", {}, ToolContext("t")) == {"ok": True, "data": {"found": False}}
    assert len(calls) == 2 and len(logs) == 1
    assert not (await registry.execute("refund", {}, ToolContext("t")))["ok"]
