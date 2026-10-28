import json

import pytest
from langchain_core.runnables import RunnableLambda

import merchant.agent as agent
from merchant.agent import AgentError, answer_from_documents, ask, plan_query
from merchant.config import Settings
from merchant.config import ROOT
from streamlit.testing.v1 import AppTest
from merchant.memory import history_from_messages, serialize_history
from merchant.retrieval import retrieve, split_document

TEST_SETTINGS = Settings(api_key="fixture-only-never-real")


def test_document_chunks_and_source_locations():
    chunks = split_document()
    assert len(chunks) == 10
    assert len({chunk.id for chunk in chunks}) == 10
    assert all(len(chunk.text) <= 450 and chunk.line_start <= chunk.line_end for chunk in chunks)


@pytest.mark.parametrize("question,expected", [
    ("毛利应该用历史成本还是当前成本？", "D05"),
    ("毛利率怎么算？", "D06"), ("可售库存怎么算？", "D07"),
    ("退款和取消的订单算销量吗？", "D02"), ("销售额使用标价吗？", "D04"),
    ("低库存怎么判断？", "D08"),
])
def test_keyword_retrieval(question, expected):
    assert expected in [chunk["id"] for chunk in retrieve(question)]


def test_no_basis_explicit_and_never_invents():
    chunks = retrieve("本店广告投放ROI目标是多少？")
    answer, citations = answer_from_documents("本店广告投放ROI目标是多少？", chunks, Settings())
    assert "未检索到" in answer
    assert retrieve("仓库建筑面积多少平方米？") == []
    answer, citations = answer_from_documents("未知问题", [], Settings())
    assert "未检索到" in answer and citations == []


def test_extractive_rag_checks_every_quote():
    chunks = retrieve("毛利计算用什么成本？")
    chunk = next(chunk for chunk in chunks if chunk["id"] == "D05")
    fixture = RunnableLambda(lambda data: {"supported": True, "passages": [
        {"chunk_id": "D05", "quote": chunk["text"].split("；")[0]}]})
    answer, citations = answer_from_documents("毛利计算用什么成本？", chunks, TEST_SETTINGS, chain=fixture)
    assert "[D05]" in answer and citations[0]["id"] == "D05"
    for evidence in [
        {"chunk_id": "D99", "quote": "伪造的不存在的文档引用句子"},
        {"chunk_id": "D05", "quote": "此店毛利等于所有销售额再加100万元"},
    ]:
        fixture = RunnableLambda(lambda data: {"supported": True, "passages": [evidence]})
        with pytest.raises(AgentError, match="引用无法"):
            answer_from_documents("毛利", chunks, TEST_SETTINGS, chain=fixture)


def test_multi_turn_pipeline_with_explicit_fixtures(monkeypatch):
    """仅测上下文传递、继承条件与真实 SQL 执行；不声称测试了真实中文模型。"""
    fixtures = [
        {"kind": "data", "resolved_question": "2026年10月1—20日云朵纯棉T恤销量和销售额", "sql": "SELECT SUM(quantity),SUM(quantity*unit_price_cents)/100.0 FROM orders WHERE status IN ('paid','completed') AND product_id='P001' AND order_date>='2026-10-01' AND order_date<'2026-10-21'", "message": "", "context": {"product_ids": ["P001"], "start_date": "2026-10-01", "end_date_exclusive": "2026-10-21", "metric": "mixed"}},
        {"kind": "data", "resolved_question": "2026年10月1—20日云朵纯棉T恤毛利", "sql": "SELECT SUM(quantity*(unit_price_cents-unit_cost_cents))/100.0 FROM orders WHERE status IN ('paid','completed') AND product_id='P001' AND order_date>='2026-10-01' AND order_date<'2026-10-21'", "message": "", "context": {"product_ids": ["P001"], "start_date": "2026-10-01", "end_date_exclusive": "2026-10-21", "metric": "profit"}},
        {"kind": "data", "resolved_question": "2026年9月云朵纯棉T恤毛利", "sql": "SELECT SUM(quantity*(unit_price_cents-unit_cost_cents))/100.0 FROM orders WHERE status IN ('paid','completed') AND product_id='P001' AND order_date>='2026-09-01' AND order_date<'2026-10-01'", "message": "", "context": {"product_ids": ["P001"], "start_date": "2026-09-01", "end_date_exclusive": "2026-10-01", "metric": "profit"}},
    ]
    seen = []
    def fixture_chain(data):
        seen.append(json.loads(data["history"]))
        return fixtures[len(seen) - 1]
    monkeypatch.setattr(agent, "make_chain", lambda settings: RunnableLambda(fixture_chain))
    messages, results = [], []
    for question in ("本月云朵纯棉T恤销量和销售额", "它的毛利呢", "那上个月呢"):
        response = ask(question, TEST_SETTINGS, history=history_from_messages(messages))
        results.append(response["result"]["rows"])
        messages.extend([{"role": "user", "content": question}, {"role": "assistant", "response": response}])
    assert results == [[[30, 2280.0]], [[1185.0]], [[984.0]]]
    assert len(seen[0]) == 0 and seen[1][-1]["context"]["product_ids"] == ["P001"]
    assert seen[2][-1]["context"]["metric"] == "profit"
    assert history_from_messages([]) == []


def test_errors_do_not_replace_memory_and_history_is_valid_json():
    messages = [
        {"role": "user", "content": "查云朵T恤"},
        {"role": "assistant", "response": {"kind": "data", "resolved_question": "云朵T恤销售额", "context": {"product_ids": ["P001"]}}},
        {"role": "user", "content": "错误问题"}, {"role": "assistant", "content": "失败"},
        {"role": "assistant", "response": {"kind": "clarify", "answer": "请明确商品"}},
    ]
    history = history_from_messages(messages)
    assert len(history) == 1 and history[0]["context"]["product_ids"] == ["P001"]
    assert json.loads(serialize_history(history)) == history


def test_plan_rejects_invalid_context():
    fixture = RunnableLambda(lambda data: {"kind": "data", "resolved_question": "销量", "sql": "SELECT 1", "context": {"product_ids": ["P999"]}})
    with pytest.raises(AgentError):
        plan_query("销量", TEST_SETTINGS, chain=fixture)


def test_document_search_page_and_missing_basis(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
    app.text_input[0].set_value("毛利和历史成本")
    next(button for button in app.button if button.label == "检索文档").click().run()
    assert not app.exception
    assert any("[D05]" in item.value for item in app.markdown)
    app.text_input[0].set_value("仓库建筑面积多少平方米？")
    next(button for button in app.button if button.label == "检索文档").click().run()
    assert any("未检索到" in item.value for item in app.warning)
    assert not app.exception


def test_authentication_failure_is_clear_and_does_not_leak_secret():
    class RejectedCredential(Exception):
        status_code = 401
    def fail(_):
        raise RejectedCredential("fixture-secret-do-not-expose")
    with pytest.raises(AgentError) as exc:
        plan_query("本月销量", TEST_SETTINGS, chain=RunnableLambda(fail))
    assert "401" in str(exc.value)
    assert "fixture-secret" not in str(exc.value)


@pytest.mark.parametrize("status,expected,unexpected", [
    (402, "账户余额不足（402）", "并发达到上限"),
    (429, "并发达到上限（429）", "账户余额不足"),
])
def test_balance_and_rate_limits_are_distinct(status, expected, unexpected):
    class ServiceFailure(Exception):
        status_code = status
    def fail(_):
        raise ServiceFailure("fixture-secret-do-not-expose")
    with pytest.raises(AgentError) as exc:
        plan_query("本月销量", TEST_SETTINGS, chain=RunnableLambda(fail))
    assert expected in str(exc.value)
    assert unexpected not in str(exc.value)
    assert "fixture-secret" not in str(exc.value)
