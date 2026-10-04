"""只暴露 AI 能力；对外业务入口由 Go 承担，不注册旧 api 路由。"""

import hmac
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict

from app.config import settings
from app.guardrail.desensitize import desensitize_text


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 空凭证不能退化为匿名内部接口。运行 AI 入口也禁止静默切回 Python SQL。
    if len(settings.internal_service_token) < 32:
        raise RuntimeError("请配置至少 32 字符的 INTERNAL_SERVICE_TOKEN")
    if settings.business_tools_backend != "go":
        raise RuntimeError("AI 拆分入口要求 BUSINESS_TOOLS_BACKEND=go")
    yield
    from app.tools.go_client import close_client
    from app.db import async_engine, sync_engine

    await close_client()
    await async_engine.dispose()
    sync_engine.dispose()


app = FastAPI(title="DevAssist 内部 AI", version="0.1.0", lifespan=lifespan)


def require_service(x_internal_token: str | None = Header(default=None)) -> None:
    expected = settings.internal_service_token
    if len(expected) < 32 or not hmac.compare_digest(
        (x_internal_token or "").encode(), expected.encode()
    ):
        raise HTTPException(401, "内部凭证无效")


class InternalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunInput(InternalInput):
    query: str
    tenant_id: str
    user_id: str
    conversation_id: str
    is_internal: bool = False


class SuggestInput(InternalInput):
    context: str


class CleanInput(InternalInput):
    texts: list[str]


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "env": settings.app_env, "version": app.version}


@app.post("/internal/ai/run", dependencies=[Depends(require_service)])
async def run(body: RunInput) -> dict:
    # 身份只能来自已认证 Go 服务；不接受模型参数覆盖用户/租户/内部角色。
    from app.agents.supervisor import run as run_supervisor

    return await run_supervisor(**body.model_dump())


async def generate_suggestion(context: str) -> dict:
    """复用原推荐话术 prompt、模型、温度与脱敏，不负责查业务表。"""
    from app.llm import client
    from app.llm.router import model_for

    result = await client.chat(
        [
            {"role": "system", "content": "你是资深技术支持。基于会话上下文，为客服起草一段专业、礼貌、可直接发送给客户的回复话术，给出明确结论与下一步。只输出话术正文。"},
            {"role": "user", "content": context or "（暂无对话内容）"},
        ],
        model=model_for("summarize"), temperature=0.3,
    )
    return {"suggestion": desensitize_text(result.content.strip())}


@app.post("/internal/ai/suggest-reply", dependencies=[Depends(require_service)])
async def suggest(body: SuggestInput) -> dict:
    return await generate_suggestion(body.context)


@app.post("/internal/ai/desensitize", dependencies=[Depends(require_service)])
async def clean(body: CleanInput) -> dict:
    # 原正则含前后向断言，继续用 Python 纯函数；该接口零模型调用。
    return {"texts": [desensitize_text(text) for text in body.texts]}


@app.post("/internal/ai/eval", dependencies=[Depends(require_service)])
async def evaluate() -> dict:
    from eval.run_eval import evaluate as run_evaluate

    return await run_evaluate()
