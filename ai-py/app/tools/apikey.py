"""API Key 状态查询工具（通过 Go 查询 MySQL）。"""



from app.tools.go_client import execute_business
from app.tools.registry import ToolContext, ToolSpec, register


async def query_apikey_status(args: dict, ctx: ToolContext) -> dict:
    """SQL 主体已迁到 Go；保留原签名和 ToolSpec。"""
    return await execute_business("query_apikey_status", args, ctx)


register(ToolSpec(
    name="query_apikey_status",
    description="查询应用 API Key 的状态（有效/过期/禁用）与过期时间。用于诊断 401 鉴权失败。",
    parameters={
        "type": "object",
        "properties": {
            "app_id": {"type": "string", "description": "应用 ID，如 app_acme"},
            "api_key_id": {"type": "string", "description": "API Key ID（可选）"},
        },
    },
    func=query_apikey_status,
    category="apikey",
))
