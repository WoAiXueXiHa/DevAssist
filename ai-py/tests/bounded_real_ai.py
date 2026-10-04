"""仅单批真实验收用：原 AI 入口/算法不替换，测试进程限制 SDK 输出与费用预留。"""
import json
import os
from pathlib import Path
import threading

import dashscope

from app.ai_main import app
from app.config import settings
from app.llm import client

TARGET = Path(os.environ["MIGRATION_REAL_LEDGER"])
LOCK = threading.Lock()
LEDGER = {"budget_cny": 1.0, "reserved_upper_cny": 0.0, "calls": [],
          "pricing_source": "https://help.aliyun.com/zh/model-studio/model-pricing",
          "scope": "China Beijing; non-thinking; input < 128K; one sample; no corpus ingest",
          "limits": {"max_network_attempts": 8, "max_output_tokens": 1024, "openai_sdk_retries": 0, "rerank_max_attempts_per_call": 2},
          "account_bill_verified": False}
PRICES = {"qwen-turbo": (.3,.6),"qwen-plus": (.8,2),"text-embedding-v3": (.5,0),"gte-rerank-v2": (.8,0)}
if settings.llm_base_url != "https://dashscope.aliyuncs.com/compatible-mode/v1":
    raise RuntimeError("真实验收仅核对了北京兼容端点价格")
if any(model not in PRICES for model in [settings.llm_model_small,settings.llm_model_large,settings.embedding_model,settings.rerank_model]):
    raise RuntimeError("当前模型价格未核对，停止真实调用")


def save():
    TARGET.write_text(json.dumps(LEDGER,ensure_ascii=False,indent=2)+"\n")


def reserve(model, payload, output_tokens=0, attempts=1):
    # 用 UTF-8 字节数和额外 4096 的协议余量上估输入；预留后不释放，即使调用失败也计入。
    input_upper = len(json.dumps(payload,ensure_ascii=False).encode())+4096
    if input_upper>100000:
        raise RuntimeError("验收输入超出已核对的价格档位")
    price_in,price_out=PRICES[model]
    upper=attempts*(input_upper*price_in+output_tokens*price_out)/1_000_000
    with LOCK:
        if sum(c["network_attempt_allowance"] for c in LEDGER["calls"])+attempts>8 or LEDGER["reserved_upper_cny"]+upper>.95:
            raise RuntimeError("真实验收剩余预算不足，停止新模型请求")
        LEDGER["reserved_upper_cny"]+=upper
        call={"model":model,"network_attempt_allowance":attempts,"input_upper_tokens":input_upper,"output_limit":output_tokens,"reserved_upper_cny":upper,"status":"started"}
        LEDGER["calls"].append(call);save()
        return call


def finish(call,usage=None,status="ok"):
    with LOCK:
        call["status"]=status
        if usage:
            if hasattr(usage,"model_dump"):usage=usage.model_dump()
            usage=dict(usage)
            p=int(usage.get("prompt_tokens",usage.get("input_tokens",usage.get("total_tokens",0))) or 0)
            q=int(usage.get("completion_tokens",usage.get("output_tokens",0)) or 0)
            call["usage"]={"prompt_tokens":p,"completion_tokens":q,"total_tokens":int(usage.get("total_tokens",p+q) or p+q)}
            a,b=PRICES[call["model"]];call["estimated_cny"]=(p*a+q*b)/1_000_000
        save()


sdk=client._client()
sdk.max_retries=0  # 避免 SDK 内部重试绕过逐次预留；原 tenacity 重试仍逐次进入 wrapper。
_chat=sdk.chat.completions.create
_embed=sdk.embeddings.create
_rerank=dashscope.TextReRank.call


async def bounded_chat(**kwargs):
    model=kwargs["model"]
    kwargs["max_tokens"]=1024
    kwargs["extra_body"]={**kwargs.get("extra_body",{}),"enable_thinking":False}
    call=reserve(model,{"messages":kwargs["messages"],"tools":kwargs.get("tools",[])},1024)
    try:
        result=await _chat(**kwargs)
        finish(call,result.usage);return result
    except Exception as error:
        finish(call,status=type(error).__name__);raise


async def bounded_embed(**kwargs):
    call=reserve(kwargs["model"],kwargs["input"])
    try:
        result=await _embed(**kwargs)
        finish(call,result.usage);return result
    except Exception as error:
        finish(call,status=type(error).__name__);raise


def bounded_rerank(**kwargs):
    # 每个候选重复计入 query；DashScope 可能重试掉线连接一次，预留两次费用与请求名额。
    call=reserve(kwargs["model"],{"pairs":[{"query":kwargs["query"],"document":doc} for doc in kwargs["documents"]]},attempts=2)
    try:
        result=_rerank(**kwargs)
        finish(call,result.usage,status=f"HTTP {result.status_code}");return result
    except Exception as error:
        finish(call,status=type(error).__name__);raise


sdk.chat.completions.create=bounded_chat
sdk.embeddings.create=bounded_embed
dashscope.TextReRank.call=bounded_rerank
save()
