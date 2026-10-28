"""离线验证基准 SQL；显式 --live 才调用真实 DeepSeek。"""
import argparse
from collections import Counter
from decimal import Decimal
import json
import sys

from merchant.acceptance_cases import CASES
from merchant.agent import AgentError, answer_from_documents, ask
from merchant.config import ROOT, Settings, load_settings
from merchant.database import QueryRejected, run_readonly
from merchant.memory import history_from_messages
from merchant.retrieval import retrieve


def normalize(value):
    if value is None:
        return ("null", "")
    if isinstance(value, (int, float)):
        return ("number", str(Decimal(str(value)).quantize(Decimal("0.01"))))
    return ("text", str(value))


def same_rows(actual, expected, ordered=False):
    # 列顺序和中文别名可变，值及行数必须匹配；排名题还要求行顺序正确。
    actual_rows = [tuple(sorted(normalize(v) for v in row)) for row in actual]
    expected_rows = [tuple(sorted(normalize(v) for v in row)) for row in expected]
    return actual_rows == expected_rows if ordered else Counter(actual_rows) == Counter(expected_rows)


def check_offline(case):
    if case["kind"] == "data":
        result = run_readonly(case["sql"])
        return same_rows(result.rows, case["expected_rows"], case.get("ordered", False)), "只验证标准 SQL 与人工基准值，未验证中文模型", result.to_dict()
    if case["kind"] == "knowledge":
        chunks = retrieve(case["question"])
        return case["citation"] in [chunk["id"] for chunk in chunks], "只验证本地检索能命中依据段落，未调用模型摘录", chunks
    if case["kind"] == "unknown":
        answer, chunks = answer_from_documents(case["question"], retrieve(case["question"]), Settings())
        return "未检索到" in answer, "验证缺失依据时明确说明", {"answer": answer, "citations": chunks}
    before = run_readonly("SELECT COUNT(*) FROM orders").rows
    rejected = False
    try:
        run_readonly("DELETE FROM orders")
    except QueryRejected:
        rejected = True
    return rejected and run_readonly("SELECT COUNT(*) FROM orders").rows == before, "验证数据库安全层拒绝 DELETE；未验证模型意图分类", None


def write_documents():
    (ROOT / "docs" / "验收问题.json").write_text(json.dumps(CASES, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# 20 道验收问题与标准答案", "", "全部基于虚构演示数据；参照今天为 2026-10-20。本月为 10 月 1—20 日，上个月为 9 月。",
             "", "第 5、6、7 题必须在同一轮对话内依次提问；其余题分别点击侧栏‘清空对话’后提问，以免继承条件。",
             "", "离线检查验证标准 SQL、数据库防护与关键词检索，不代表真实模型验收。填好密钥后可运行 `.venv/bin/python -m merchant.acceptance --live`，按实际模型结果逐题比对。",
             "", "| 编号 | 问题 | 标准答案 |", "|---|---|---|"]
    for case in CASES:
        lines.append(f"| {case['id']} | {case['question']} | {case['answer']} |")
    lines.extend(["", "## 数值题的标准 SQL", "", "金额按元、毛利率按百分数校验；忽略列顺序和中文别名差异，排名题保留行顺序。所有 SQL 还会经过相同安全层并加外层行数限制。"])
    for case in CASES:
        if "sql" in case:
            lines.extend(["", f"### 第 {case['id']} 题", "", "```sql", case["sql"], "```"])
    (ROOT / "docs" / "20道验收问题与标准答案.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_suite(live=False):
    settings = load_settings()
    mode = "live" if live else "offline"
    path = ROOT / "reports" / f"{mode}-acceptance.json"
    path.parent.mkdir(exist_ok=True)
    if live and not settings.ready:
        report = {"mode": mode, "status": "not_run", "reason": "未配置真实 API 密钥，未调用模型。", "model_calls": 0}
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(report["reason"])
        return 2
    outcomes, conversations = [], {}
    for case in CASES:
        try:
            if live:
                group = case.get("conversation")
                messages = conversations.setdefault(group, []) if group else []
                response = ask(case["question"], settings, history=history_from_messages(messages))
                if case["kind"] == "data":
                    passed = bool(response["result"]) and same_rows(response["result"]["rows"], case["expected_rows"], case.get("ordered", False))
                elif case["kind"] == "knowledge":
                    passed = case["citation"] in [c["id"] for c in response["citations"]]
                elif case["kind"] == "unknown":
                    passed = "未检索到" in response["answer"] and response["result"] is None
                else:
                    passed = response["kind"] == "clarify" and response["result"] is None
                messages.extend([{"role": "user", "content": case["question"]}, {"role": "assistant", "response": response}])
                detail, artifact = "真实 API 输出与基准比对", response
            else:
                passed, detail, artifact = check_offline(case)
            outcomes.append({"id": case["id"], "passed": passed, "detail": detail, "artifact": artifact})
        except (AgentError, QueryRejected) as exc:
            # 删除请求被数据库层拦截也满足安全验收。
            passed = case["kind"] == "refusal" and isinstance(exc, QueryRejected)
            outcomes.append({"id": case["id"], "passed": passed, "detail": str(exc)})
        print(f"[{mode}] 第 {case['id']:02d} 题：{'通过' if outcomes[-1]['passed'] else '失败'}", flush=True)
    report = {"mode": mode, "demo_date": "2026-10-20", "total": len(CASES),
              "passed": sum(item["passed"] for item in outcomes), "real_model_tested": live,
              "note": "真实模型逐题验收" if live else "仅本地验证，中文语义与真实 DeepSeek 调用未验证", "outcomes": outcomes}
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"结果：{report['passed']}/{report['total']}；报告：{path}")
    return 0 if report["passed"] == report["total"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="使用本地密钥，真实调用 DeepSeek API（产生实际用量）")
    parser.add_argument("--write-docs", action="store_true", help="生成 20 道验收题 Markdown 和 JSON 文件")
    args = parser.parse_args()
    if args.write_docs:
        write_documents()
    sys.exit(run_suite(live=args.live))
