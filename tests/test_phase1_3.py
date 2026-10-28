import sqlite3
import json

import pytest
from langchain_core.runnables import RunnableLambda
from streamlit.testing.v1 import AppTest

from merchant.agent import AgentError, ask, plan_query
from merchant.config import ROOT, Settings
from merchant.database import QueryRejected, run_readonly
from merchant.seed import seed_database


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "demo.sqlite3"
    seed_database(path)
    return path


def test_schema_seed_idempotent(db):
    assert seed_database(db) is False
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall() == [
            ("inventory",), ("orders",), ("products",)]
        assert con.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 96
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []


def test_correct_sales_profit_inventory(db):
    result = run_readonly("""SELECT SUM(quantity), SUM(quantity*unit_price_cents)/100.0,
    SUM(quantity*(unit_price_cents-unit_cost_cents))/100.0 FROM orders
    WHERE status IN ('paid','completed') AND product_id='P001' AND order_date>='2026-09-01' AND order_date<'2026-10-01'""", db)
    assert result.rows == [[24, 1848.0, 984.0]]
    assert run_readonly("SELECT SUM(on_hand-reserved) FROM inventory", db).rows == [[350]]


@pytest.mark.parametrize("sql", [
    "DROP TABLE orders", "DELETE FROM orders", "UPDATE inventory SET on_hand=0", "INSERT INTO products VALUES(1)",
    "SELECT 1; DELETE FROM orders", "PRAGMA table_info(orders)", "ATTACH DATABASE '/tmp/evil' AS evil",
    "SELECT * FROM sqlite_master", "SELECT * FROM main.orders", "SELECT * FROM secret",
    "SELECT load_extension('/tmp/evil')", "SELECT readfile('/etc/passwd')", "SELECT randomblob(1000000000)",
    "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n) SELECT * FROM n",
    "SELECT * FROM orders LIMIT -1", "SELECT * FROM orders LIMIT (SELECT 100)",
    "SELECT * INTO backup FROM orders", "SELECT rowid FROM orders",
])
def test_sql_safety(db, sql):
    with pytest.raises(QueryRejected):
        run_readonly(sql, db)


def test_row_cap_and_actual_sql(db):
    result = run_readonly("SELECT item_id FROM orders ORDER BY item_id LIMIT 999", db, max_rows=4)
    assert len(result.rows) == 4 and result.truncated
    assert result.executed_sql.endswith("LIMIT 5")


def test_execution_timeout(db):
    with pytest.raises(QueryRejected, match="超时"):
        run_readonly("SELECT SUM(a.quantity*b.quantity*c.quantity*d.quantity) FROM orders a CROSS JOIN orders b CROSS JOIN orders c CROSS JOIN orders d", db, timeout=0.02)
    assert run_readonly("SELECT COUNT(*) FROM orders", db).rows == [[96]]


def test_missing_key_does_not_invoke_model():
    with pytest.raises(AgentError, match="未配置"):
        ask("销量是多少", Settings())


def test_plan_contract_fixture_not_live_model():
    chain = RunnableLambda(lambda data: {"kind": "data", "resolved_question": "总销量", "sql": "SELECT SUM(quantity) FROM orders WHERE status IN ('paid','completed')", "message": ""})
    plan = plan_query("总销量", Settings(api_key="test-only-not-real"), chain=chain)
    assert plan.kind == "data"
    invalid = RunnableLambda(lambda data: {"kind": "data", "resolved_question": "总销量", "sql": None})
    with pytest.raises(AgentError, match="格式"):
        plan_query("总销量", Settings(api_key="test-only-not-real"), chain=invalid)


def test_langchain_http_contract_with_explicit_fixture(monkeypatch):
    """测试用的 HTTP 响应，不是真实 DeepSeek 输出，不进入运行页面。"""
    import httpx
    import merchant.agent as agent
    original = agent.ChatOpenAI
    seen = []

    def handle(request):
        body = json.loads(request.content)
        seen.append(body)
        assert str(request.url) == "https://api.deepseek.com/chat/completions"
        assert body["model"] == "local-contract-test"
        assert body["response_format"] == {"type": "json_object"}
        plan = {"kind": "data", "resolved_question": "总销量", "sql": "SELECT SUM(quantity) FROM orders WHERE status IN ('paid','completed')", "message": ""}
        return httpx.Response(200, json={"id": "fixture-only", "object": "chat.completion", "created": 0,
            "model": "local-contract-test", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": json.dumps(plan, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(agent, "ChatOpenAI", lambda **kwargs: original(**kwargs, http_client=client))
        plan = agent.plan_query("总销量", Settings(api_key="fixture-only", model="local-contract-test"))
    assert plan.kind == "data" and len(seen) == 1


def test_streamlit_load_and_preset(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20).run()
    assert not app.exception
    assert any("未配置" in item.value for item in app.warning)
    app.button[2].click().run()
    assert not app.exception
    assert len(app.dataframe) == 1 and app.code
    app.chat_input[0].set_value("本月销量是多少").run()
    assert not app.exception
    assert any("未配置" in item.value for item in app.error)
