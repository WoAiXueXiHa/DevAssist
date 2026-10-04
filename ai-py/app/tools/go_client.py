"""八个业务工具的内部 HTTP 适配；返回原 data，由 registry 统一包装、重试与日志。"""

import httpx

from app.config import settings

_client: httpx.AsyncClient | None = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        # 私网请求不经过环境中的公网代理。期限沿用原工具配置，没有额外 HTTP 重试。
        _client = httpx.AsyncClient(
            base_url=settings.go_tools_url.rstrip("/"),
            timeout=httpx.Timeout(settings.tool_timeout_seconds),
            trust_env=False,
            follow_redirects=False,
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def execute_business(name: str, args: dict, ctx) -> dict:
    if settings.business_tools_backend == "legacy_sql":
        # 仅用于明确回退：停 Go/ai_main，启动旧 app.main；正常模式绝不自动回退。
        from app.tools import legacy_sql

        return await getattr(legacy_sql, name)(args, ctx)
    if settings.business_tools_backend != "go":
        raise RuntimeError("BUSINESS_TOOLS_BACKEND 只能为 go 或 legacy_sql")
    if len(settings.internal_service_token) < 32:
        raise RuntimeError("内部服务凭证未配置")
    # 模型传来的同名参数不具有身份效力，可信上下文单独传输。
    business_args = {
        k: v for k, v in args.items()
        if k not in {"tenant_id", "user_id", "conversation_id", "is_internal", "trace_id"}
    }
    response = await get_client().post(
        "/internal/tools/execute",
        headers={"X-Internal-Token": settings.internal_service_token},
        json={
            "name": name,
            "args": business_args,
            "context": {
                "tenant_id": ctx.tenant_id,
                "trace_id": ctx.trace_id,
                "is_internal": ctx.is_internal,
                "user_id": ctx.user_id,
                "conversation_id": ctx.conversation_id,
            },
        },
    )
    # 不将响应体/内部凭证带入异常与工具日志。
    if response.status_code != 200:
        raise RuntimeError(f"Go 业务工具返回状态 {response.status_code}")
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Go 业务工具返回数据无效")
    return data
