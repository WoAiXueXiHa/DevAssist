"""调用日志查询工具（通过 Go 查询 MySQL）。"""



from app.tools.go_client import execute_business
from app.tools.registry import ToolContext, ToolSpec, register


async def query_call_log(args: dict, ctx: ToolContext) -> dict:
    """SQL 主体已迁到 Go；保留原签名和 ToolSpec。"""
    return await execute_business("query_call_log", args, ctx)


async def query_recent_call_stats(args: dict, ctx: ToolContext) -> dict:
    """SQL 主体已迁到 Go；保留原签名和 ToolSpec。"""
    return await execute_business("query_recent_call_stats", args, ctx)


register(ToolSpec(
    name="query_call_log",
    description="根据 request_id 查询某次 API 调用的状态码、错误码、耗时、调用时间等日志详情。",
    parameters={
        "type": "object",
        "properties": {"request_id": {"type": "string", "description": "调用请求 ID，如 req_20260615_8842"}},
        "required": ["request_id"],
    },
    func=query_call_log,
    category="logs",
))

register(ToolSpec(
    name="query_recent_call_stats",
    description="统计某接口近 N 分钟的调用量与各状态码分布，用于判断是否触发限流(429)。",
    parameters={
        "type": "object",
        "properties": {
            "endpoint": {"type": "string", "description": "接口路径，如 /v1/risk/score"},
            "minutes": {"type": "integer", "description": "时间窗口（分钟），默认 60"},
        },
    },
    func=query_recent_call_stats,
    category="logs",
))
