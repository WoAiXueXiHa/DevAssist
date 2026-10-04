"""套餐 / 用量 / 账单查询工具（通过 Go 查询 MySQL）。"""


from app.tools.go_client import execute_business
from app.tools.registry import ToolContext, ToolSpec, register


async def query_plan(args: dict, ctx: ToolContext) -> dict:
    """SQL 主体已迁到 Go；保留原签名和 ToolSpec。"""
    return await execute_business("query_plan", args, ctx)


async def query_usage(args: dict, ctx: ToolContext) -> dict:
    """SQL 主体已迁到 Go；保留原签名和 ToolSpec。"""
    return await execute_business("query_usage", args, ctx)


async def query_bill(args: dict, ctx: ToolContext) -> dict:
    """SQL 主体已迁到 Go；保留原签名和 ToolSpec。"""
    return await execute_business("query_bill", args, ctx)


register(ToolSpec(
    name="query_plan",
    description="查询当前租户的套餐信息：套餐名、QPS 上限、月调用量配额、单价与超额单价。",
    parameters={"type": "object", "properties": {}},
    func=query_plan,
    category="billing",
))
register(ToolSpec(
    name="query_usage",
    description="查询租户某月（YYYY-MM）的调用量与超额量；不传 month 则返回全部月份。",
    parameters={
        "type": "object",
        "properties": {"month": {"type": "string", "description": "月份，如 2026-06"}},
    },
    func=query_usage,
    category="billing",
))
register(ToolSpec(
    name="query_bill",
    description="查询租户某月账单金额与费用构成（基础费用、超额费用）。",
    parameters={
        "type": "object",
        "properties": {"month": {"type": "string", "description": "月份，如 2026-06"}},
    },
    func=query_bill,
    category="billing",
))
