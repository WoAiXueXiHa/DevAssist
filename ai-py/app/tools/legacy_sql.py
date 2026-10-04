"""迁移前 SQL 对照实现；只能通过显式 legacy_sql 回退配置执行。"""






import uuid
from datetime import datetime, timedelta
from sqlalchemy import func, select
from app.db import AsyncSessionLocal
from app.models import ApiCallLog, ApiKey, Invoice, Plan, Tenant, Ticket, UsageRecord
from app.tools.registry import ToolContext


async def query_call_log(args: dict, ctx: ToolContext) -> dict:
    """按 request_id 查询单次调用日志。"""
    request_id = args.get("request_id")
    if not request_id:
        return {"found": False, "reason": "缺少 request_id"}
    async with AsyncSessionLocal() as s:
        log = (
            await s.execute(select(ApiCallLog).where(ApiCallLog.request_id == request_id))
        ).scalar_one_or_none()
        if log is None:
            return {"found": False, "reason": "未找到该 request_id 的日志"}
        # 租户隔离：客户侧只能查本租户
        if not ctx.is_internal and log.tenant_id != ctx.tenant_id:
            return {"found": False, "reason": "无权访问其它租户的调用日志"}
        key_masked = None
        if log.api_key_id:
            key = (
                await s.execute(select(ApiKey).where(ApiKey.id == log.api_key_id))
            ).scalar_one_or_none()
            key_masked = key.key_masked if key else None
        return {
            "found": True,
            "request_id": log.request_id,
            "app_id": log.app_id,
            "endpoint": log.endpoint,
            "http_status": log.http_status,
            "error_code": log.error_code,
            "latency_ms": log.latency_ms,
            "api_key_masked": key_masked,
            "called_at": log.created_at.isoformat(),
        }


async def query_recent_call_stats(args: dict, ctx: ToolContext) -> dict:
    """统计近 N 分钟某接口的调用情况（用于 429 限流诊断）。"""
    endpoint = args.get("endpoint")
    minutes = int(args.get("minutes", 240))
    async with AsyncSessionLocal() as s:
        # 锚点：优先定位该租户(+接口)最近一次 429 限流事件的时间；无 429 则用最近一条日志。
        # 这样能围绕"限流事件"统计窗口，不依赖系统当前时间，也不被随机背景日志漂移。
        base_conds = [ApiCallLog.tenant_id == ctx.tenant_id]
        if endpoint:
            base_conds.append(ApiCallLog.endpoint == endpoint)
        anchor = (
            await s.execute(
                select(func.max(ApiCallLog.created_at)).where(
                    *base_conds, ApiCallLog.http_status == 429
                )
            )
        ).scalar()
        if anchor is None:
            anchor = (
                await s.execute(select(func.max(ApiCallLog.created_at)).where(*base_conds))
            ).scalar()
        if anchor is None:
            return {"total": 0, "by_status": {}}
        since = anchor - timedelta(minutes=minutes)
        conds = [ApiCallLog.tenant_id == ctx.tenant_id, ApiCallLog.created_at >= since]
        if endpoint:
            conds.append(ApiCallLog.endpoint == endpoint)
        rows = (
            await s.execute(
                select(ApiCallLog.http_status, func.count())
                .where(*conds)
                .group_by(ApiCallLog.http_status)
            )
        ).all()
        by_status = {int(st): int(cnt) for st, cnt in rows}
        total = sum(by_status.values())
        n429 = by_status.get(429, 0)
        return {
            "endpoint": endpoint,
            "window_minutes": minutes,
            "total": total,
            "by_status": by_status,
            "rate_limited_count": n429,
            "rate_limited_ratio": round(n429 / total, 3) if total else 0.0,
        }







async def query_apikey_status(args: dict, ctx: ToolContext) -> dict:
    """查询 API Key 状态。支持按 api_key_id 或 app_id 查询。"""
    api_key_id = args.get("api_key_id")
    app_id = args.get("app_id")
    async with AsyncSessionLocal() as s:
        stmt = select(ApiKey)
        if api_key_id:
            stmt = stmt.where(ApiKey.id == api_key_id)
        elif app_id:
            stmt = stmt.where(ApiKey.app_id == app_id)
        else:
            return {"found": False, "reason": "需提供 api_key_id 或 app_id"}
        keys = (await s.execute(stmt)).scalars().all()
        # 租户隔离：客户侧只能看到本租户的 Key
        keys = [k for k in keys if ctx.is_internal or k.tenant_id == ctx.tenant_id]
        if not keys:
            return {"found": False, "reason": "未找到对应 API Key 或无权访问"}
        now = datetime(2026, 6, 15)  # 固定为演示数据基准时间，保证过期判定可复现
        return {
            "found": True,
            "keys": [
                {
                    "api_key_masked": k.key_masked,
                    "status": k.status,
                    "expire_at": k.expire_at.isoformat() if k.expire_at else None,
                    "expired": bool(k.expire_at and k.expire_at < now),
                }
                for k in keys
            ],
        }






async def query_plan(args: dict, ctx: ToolContext) -> dict:
    """查询租户当前套餐（QPS、月配额、单价）。"""
    async with AsyncSessionLocal() as s:
        tenant = (
            await s.execute(select(Tenant).where(Tenant.id == ctx.tenant_id))
        ).scalar_one_or_none()
        if tenant is None or tenant.plan_id is None:
            return {"found": False, "reason": "未找到套餐信息"}
        plan = (await s.execute(select(Plan).where(Plan.id == tenant.plan_id))).scalar_one()
        return {
            "found": True,
            "plan_name": plan.name,
            "qps_limit": plan.qps_limit,
            "monthly_quota": plan.monthly_quota,
            "price_per_call": plan.price_per_call,
            "overage_price_per_call": plan.overage_price_per_call,
        }


async def query_usage(args: dict, ctx: ToolContext) -> dict:
    """查询某月调用量与超额量。"""
    month = args.get("month")
    async with AsyncSessionLocal() as s:
        stmt = select(UsageRecord).where(UsageRecord.tenant_id == ctx.tenant_id)
        if month:
            stmt = stmt.where(UsageRecord.month == month)
        stmt = stmt.order_by(UsageRecord.month)
        rows = (await s.execute(stmt)).scalars().all()
        if not rows:
            return {"found": False, "reason": "未找到用量记录"}
        return {
            "found": True,
            "usage": [
                {"month": r.month, "call_count": r.call_count, "overage_count": r.overage_count}
                for r in rows
            ],
        }


async def query_bill(args: dict, ctx: ToolContext) -> dict:
    """查询某月账单及费用构成。"""
    month = args.get("month")
    async with AsyncSessionLocal() as s:
        stmt = select(Invoice).where(Invoice.tenant_id == ctx.tenant_id)
        if month:
            stmt = stmt.where(Invoice.month == month)
        stmt = stmt.order_by(Invoice.month)
        rows = (await s.execute(stmt)).scalars().all()
        if not rows:
            return {"found": False, "reason": "未找到账单"}
        return {
            "found": True,
            "bills": [
                {"month": r.month, "amount": r.amount, "status": r.status, "items": r.items}
                for r in rows
            ],
        }








async def create_ticket(args: dict, ctx: ToolContext) -> dict:
    """创建技术支持工单（含 AI 诊断摘要与证据）。"""
    ticket_id = "tk_" + datetime(2026, 6, 15).strftime("%Y%m%d") + "_" + uuid.uuid4().hex[:6]
    async with AsyncSessionLocal() as s:
        s.add(
            Ticket(
                ticket_id=ticket_id,
                tenant_id=ctx.tenant_id,
                user_id=args.get("user_id", ""),
                category=args.get("category", "其它"),
                priority=args.get("priority", "P2"),
                status="new",
                title=args.get("title", "技术支持工单"),
                summary=args.get("summary", ""),
                related_request_ids=args.get("related_request_ids", []),
                related_endpoint=args.get("related_endpoint"),
                error_code=args.get("error_code"),
                evidence=args.get("evidence", ""),
                ai_diagnosis=args.get("ai_diagnosis", ""),
                conversation_id=args.get("conversation_id"),
            )
        )
        await s.commit()
    return {"ticket_id": ticket_id, "status": "new", "priority": args.get("priority", "P2")}


async def query_ticket(args: dict, ctx: ToolContext) -> dict:
    """按 ticket_id 查询工单状态。"""
    ticket_id = args.get("ticket_id")
    async with AsyncSessionLocal() as s:
        t = (
            await s.execute(select(Ticket).where(Ticket.ticket_id == ticket_id))
        ).scalar_one_or_none()
        if t is None:
            return {"found": False}
        if not ctx.is_internal and t.tenant_id != ctx.tenant_id:
            return {"found": False, "reason": "无权访问"}
        return {"found": True, "ticket_id": t.ticket_id, "status": t.status,
                "priority": t.priority, "title": t.title}
