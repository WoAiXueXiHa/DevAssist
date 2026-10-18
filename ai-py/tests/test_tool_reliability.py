"""工具副作用与日志故障隔离测试，无真实模型或外部服务。"""

import json

import httpx
import pytest

from app.config import settings
from app.tools import go_client, registry
from app.tools.registry import ToolContext, ToolSpec


@pytest.mark.asyncio
async def test_log_failure_does_not_repeat_successful_write(monkeypatch):
    writes = []

    async def write(args, ctx):
        writes.append(args)
        return {"ticket_id": "ticket-1"}

    async def failed_log(*args):
        raise RuntimeError("database unavailable")

    monkeypatch.setitem(registry.REGISTRY, "test_write", ToolSpec(
        "test_write", "test", {}, write, retries=2,
    ))
    monkeypatch.setattr(registry, "_log_call", failed_log)
    result = await registry.execute("test_write", {}, ToolContext("tenant", "trace"))
    assert result == {"ok": True, "data": {"ticket_id": "ticket-1"}}
    assert len(writes) == 1


@pytest.mark.asyncio
async def test_log_failure_preserves_business_failure(monkeypatch):
    async def failed_business(args, ctx):
        raise ValueError("business rejected")

    async def failed_log(*args):
        raise RuntimeError("log rejected")

    monkeypatch.setitem(registry.REGISTRY, "test_failure", ToolSpec(
        "test_failure", "test", {}, failed_business, retries=0,
    ))
    monkeypatch.setattr(registry, "_log_call", failed_log)
    assert await registry.execute("test_failure", {}, ToolContext("tenant")) == {
        "ok": False, "error": "ValueError: business rejected",
    }


@pytest.mark.asyncio
async def test_ticket_response_lost_retry_uses_trusted_same_key(monkeypatch):
    monkeypatch.setattr(settings, "internal_service_token", "test-service-token-01234567890123456789")
    monkeypatch.setattr(settings, "business_tools_backend", "go")
    rows, keys, attempts = {}, [], []

    def receive(request):
        payload = json.loads(request.content)
        key = payload["args"]["idempotency_key"]
        keys.append(key)
        rows.setdefault(key, {"ticket_id": "ticket-1"})
        attempts.append(request)
        if len(attempts) == 1:
            raise httpx.ReadTimeout("response lost after commit", request=request)
        return httpx.Response(200, json=rows[key])

    async def create(args, ctx):
        return await go_client.execute_business("create_ticket", args, ctx)

    async def log(*args):
        pass

    monkeypatch.setitem(registry.REGISTRY, "test_ticket", ToolSpec(
        "test_ticket", "test", {}, create,
    ))
    monkeypatch.setattr(registry, "_log_call", log)
    async with httpx.AsyncClient(transport=httpx.MockTransport(receive), base_url="http://go") as client:
        monkeypatch.setattr(go_client, "_client", client)
        result = await registry.execute("test_ticket", {"idempotency_key": "model-forged"}, ToolContext("t", "trusted"))
    assert result == {"ok": True, "data": {"ticket_id": "ticket-1"}}
    assert keys == ["trusted:create_ticket", "trusted:create_ticket"]
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_ticket_without_trusted_trace_is_rejected_before_http(monkeypatch):
    monkeypatch.setattr(settings, "internal_service_token", "test-service-token-01234567890123456789")
    monkeypatch.setattr(settings, "business_tools_backend", "go")
    with pytest.raises(ValueError, match="可信请求标识"):
        await go_client.execute_business("create_ticket", {"idempotency_key": "forged"}, ToolContext("t"))
