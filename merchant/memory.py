"""只保留本浏览器会话最近成功的查询，不落盘。"""
import json


def history_from_messages(messages, max_turns=6):
    history, user_question = [], ""
    for message in messages:
        if message.get("role") == "user":
            user_question = message.get("content", "")
        elif message.get("response"):
            response = message["response"]
            if response.get("kind") == "clarify":
                continue
            result = response.get("result") or {}
            history.append({
                "user_question": user_question[:2000],
                "resolved_question": response.get("resolved_question", "")[:2000],
                "kind": response.get("kind", "data"),
                "executed_sql": result.get("executed_sql", "")[:4000],
                "columns": result.get("columns", [])[:12],
                "rows_preview": [[value[:300] if isinstance(value, str) else value for value in row[:12]]
                                 for row in result.get("rows", [])[:5]],
                "context": response.get("context", {}),
            })
    # 错误和澄清不会覆盖上次有效的实体、指标或日期。
    return history[-max_turns:]


def serialize_history(history):
    selected = []
    for turn in reversed((history or [])[-6:]):
        candidate = [turn] + selected
        if len(json.dumps(candidate, ensure_ascii=False)) > 24000:
            break
        selected = candidate
    return json.dumps(selected, ensure_ascii=False)
