"""Offline orchestration regressions: no Redis, model or business service calls."""
from unittest.mock import AsyncMock

import pytest

from app.agents import supervisor, ticket, doc_rag


@pytest.fixture
def isolated(monkeypatch):
    monkeypatch.setattr(supervisor.client, "chat", AsyncMock(side_effect=AssertionError("External model call forbidden")))
    monkeypatch.setattr(supervisor.client, "embed_one", AsyncMock(side_effect=AssertionError("External embedding call forbidden")))
    for name, value in [('get_history', []), ('get_entities', {}), ('update_entities', {})]:
        monkeypatch.setattr(supervisor.session, name, AsyncMock(return_value=value))
    monkeypatch.setattr(supervisor.session, 'append_turn', AsyncMock())
    monkeypatch.setattr(supervisor.route_cache, 'get', AsyncMock(return_value=None))
    monkeypatch.setattr(supervisor.route_cache, 'put', AsyncMock())
    monkeypatch.setattr(supervisor.semantic_cache, 'get', AsyncMock(return_value=(None, [1.0])))
    monkeypatch.setattr(supervisor.semantic_cache, 'put', AsyncMock())
    monkeypatch.setattr(supervisor.TraceCollector, 'persist', AsyncMock())
    monkeypatch.setattr(supervisor.cost, 'record', AsyncMock())
    monkeypatch.setattr(supervisor.intent_router, 'classify', AsyncMock(return_value={
        'intent': 'doc_qa', 'confidence': 1.0, 'entities': {}, 'route': ['doc_rag'], 'tokens': 0}))
    monkeypatch.setattr(supervisor.ticket, 'create_from_context', AsyncMock(return_value=ticket.TicketResult('tk_one', '已创建工单 tk_one。')))


async def run():
    return await supervisor.run(query='文档问题', tenant_id='t', user_id='u', conversation_id='c')


@pytest.mark.asyncio
async def test_no_hit_preserves_answer_and_never_caches(isolated, monkeypatch):
    monkeypatch.setattr(supervisor.doc_rag, 'answer', AsyncMock(return_value=doc_rag.RagResult(answer=doc_rag.NO_HIT_MESSAGE, hit=False)))
    result = await run()
    assert result['answer'] == doc_rag.NO_HIT_MESSAGE
    assert result['handoff_status'] == 'not_required'
    supervisor.semantic_cache.put.assert_not_awaited()
    supervisor.ticket.create_from_context.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('accepted', [True, False])
async def test_all_specialists_fail_handoff_is_truthful(isolated, monkeypatch, accepted):
    monkeypatch.setattr(supervisor.doc_rag, 'answer', AsyncMock(side_effect=RuntimeError('offline')))
    monkeypatch.setattr(supervisor.ticket, 'create_from_context', AsyncMock(return_value=ticket.TicketResult('tk_one' if accepted else None, '已创建工单 tk_one。' if accepted else 'failed')))
    result = await run()
    assert result['need_human'] is True
    assert result['handoff_status'] == ('accepted' if accepted else 'failed')
    assert 'None' not in result['answer']
    assert ('已创建' in result['answer']) is accepted
    supervisor.semantic_cache.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_completed_answer_survives_all_auxiliary_write_failures(isolated, monkeypatch):
    monkeypatch.setattr(supervisor.doc_rag, 'answer', AsyncMock(return_value=doc_rag.RagResult(answer='有效依据', hit=True)))
    for obj, name in [(supervisor.session, 'append_turn'), (supervisor.TraceCollector, 'persist'), (supervisor.cost, 'record'), (supervisor.semantic_cache, 'put')]:
        monkeypatch.setattr(obj, name, AsyncMock(side_effect=RuntimeError('write unavailable')))
    result = await run()
    assert result['answer'] == '有效依据'
    assert result['trace_persisted'] is False
    assert result['handoff_status'] == 'not_required'
    supervisor.semantic_cache.put.assert_awaited_once()


@pytest.mark.asyncio
async def test_pipeline_and_ticket_failure_returns_retry_message(isolated, monkeypatch):
    monkeypatch.setattr(supervisor.intent_router, 'classify', AsyncMock(side_effect=RuntimeError('model down')))
    monkeypatch.setattr(supervisor.ticket, 'create_from_context', AsyncMock(side_effect=RuntimeError('business down')))
    result = await run()
    assert result['handoff_status'] == 'failed'
    assert result['ticket_id'] is None
    assert '暂时无法登记工单' in result['answer']
    assert '已登记' not in result['answer']


@pytest.mark.asyncio
async def test_partial_failure_preserves_success_and_marks_incomplete(isolated, monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(supervisor.intent_router, 'classify', AsyncMock(return_value={
        'intent': 'billing', 'confidence': 1.0, 'entities': {},
        'route': ['api_diagnostic', 'billing'], 'tokens': 0}))
    monkeypatch.setattr(supervisor.api_diagnostic, 'diagnose', AsyncMock(side_effect=RuntimeError('logs down')))
    monkeypatch.setattr(supervisor.billing, 'handle', AsyncMock(return_value=SimpleNamespace(
        answer='账单已核对', card={'conclusion': '账单已核对', 'evidence': [], 'steps': []}, citations=[], tokens=0, need_human=False, need_ticket=False)))
    result = await run()
    assert '账单已核对' in result['answer']
    assert '部分信息暂时无法取得' in result['answer']
    assert result['card'] is None
    assert result['handoff_status'] == 'not_required'
    supervisor.ticket.create_from_context.assert_not_awaited()
    supervisor.semantic_cache.put.assert_not_awaited()


@pytest.mark.asyncio
async def test_pipeline_failure_successful_ticket_reports_accepted(isolated, monkeypatch):
    monkeypatch.setattr(supervisor.intent_router, 'classify', AsyncMock(side_effect=RuntimeError('model down')))
    result = await run()
    assert result['handoff_status'] == 'accepted'
    assert result['ticket_id'] == 'tk_one'
    assert '已创建工单 tk_one' in result['answer']
    supervisor.ticket.create_from_context.assert_awaited_once()
