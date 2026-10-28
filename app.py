import pandas as pd
import streamlit as st

from merchant.agent import AgentError, ask, format_result, missing_key_message
from merchant.config import ConfigurationError, DB_PATH, DEMO_TODAY, DOC_PATH, load_settings
from merchant.database import QueryRejected, SCHEMA_DESCRIPTION, run_readonly
from merchant.presets import PRESETS
from merchant.memory import history_from_messages
from merchant.retrieval import retrieve, split_document

st.set_page_config(page_title="商家经营问答 · 演示", page_icon="◈", layout="wide")
st.markdown("""<style>
.block-container{max-width:1180px;padding-top:2.3rem;padding-bottom:4rem}
h1{font-size:2.2rem!important;letter-spacing:-.05rem}h2{font-size:1.3rem!important}
[data-testid="stMetric"]{background:white;border:1px solid #E1E9EC;border-radius:12px;padding:16px 20px}
[data-testid="stMetricLabel"]{color:#607687}[data-testid="stChatMessage"]{border:1px solid #E7EEF1;border-radius:12px}
.eyebrow{font-size:12px;letter-spacing:2px;color:#087F70;font-weight:700;margin-bottom:10px}
</style>""", unsafe_allow_html=True)


def render_response(response):
    if response.get("source") == "preset":
        st.caption("预置 SQL 演示 · 未调用模型 · 以下回答由程序根据数据库结果整理")
    else:
        st.caption(f"模型规划：{response.get('model', '')} · " +
                   ("数值由程序根据实际 SQL 结果整理" if response.get("result") else "说明回答只展示核验过的文档原文；缺失信息会明确提示"))
    if response.get("resolved_question"):
        st.caption("本次查询理解：" + response["resolved_question"])
    st.write(response["answer"])
    result = response.get("result")
    if result:
        st.dataframe(pd.DataFrame(result["rows"], columns=result["columns"]), hide_index=True, width="stretch")
        if result["truncated"]:
            st.warning(f"结果超过 {result['max_rows']} 行，只展示前 {result['max_rows']} 行。请缩小范围，不能把截断表视为完整结果。")
        with st.expander("查看实际执行的 SQL", expanded=True):
            st.code(result["executed_sql"], language="sql")
            st.caption(f"只读查询 · 耗时 {result['elapsed_ms']} ms · 最多展示 {result['max_rows']} 行（额外读取 1 行检测截断）")
    for citation in response.get("citations", []):
        with st.expander(f"引用 [{citation['id']}] {citation['title']}", expanded=response.get("kind") == "knowledge"):
            st.write(citation["text"])
            st.caption(f"来源：docs/业务指标说明.md · 第 {citation['line_start']}—{citation['line_end']} 行 · 关键词检索分数 {citation['score']}")


try:
    settings = load_settings()
except ConfigurationError as exc:
    st.error(str(exc))
    st.stop()

if not DB_PATH.exists():
    st.error("演示数据库尚未生成。请在终端运行：.venv/bin/python -m merchant.seed")
    st.stop()

with st.sidebar:
    st.markdown("### ◈ 商家经营问答")
    st.caption("本地数据工作台 / DEMO")
    st.divider()
    st.markdown("**连接状态**")
    if settings.ready:
        st.success("已读取密钥 · 尚不代表连接成功")
    else:
        st.warning("未配置 API 密钥")
    st.caption(f"模型：{settings.model}")
    st.caption(f"每次 SQL 最多 {settings.max_rows} 行 / {settings.sql_timeout:g} 秒")
    if st.button("重新读取配置", width="stretch"):
        st.rerun()
    if st.button("清空对话", width="stretch"):
        st.session_state.messages = []
        st.rerun()
    st.divider()
    st.markdown("**演示数据范围**")
    st.caption("订单：2026-07-01 至 2026-10-20\n\n6 个商品 · 96 条订单明细\n\n全部为虚构数据，不代表真实经营")
    st.caption(f"相对日期参照：{DEMO_TODAY}\n\n库存快照：{DEMO_TODAY}")
    st.caption("最近 6 轮成功查询用于追问；仅保留在当前浏览器会话。清空对话后不再继承。")
    with st.expander("表结构与查询口径"):
        st.text(SCHEMA_DESCRIPTION)
    with st.expander("业务指标说明文档"):
        st.markdown(DOC_PATH.read_text(encoding="utf-8"))

st.markdown('<div class="eyebrow">MERCHANT INSIGHT / 本机数据助手</div>', unsafe_allow_html=True)
st.title("把经营数据，变成清楚的答案")
st.caption("查询销量、销售额、库存与毛利。每次结果都能查看实际 SQL。")
st.warning(f"演示数据 · 全部为虚构订单、商品与库存。‘今天’固定为 {DEMO_TODAY}，本月为 10 月 1—20 日。")
summary = run_readonly("""SELECT SUM(quantity) AS qty, SUM(quantity*unit_price_cents)/100.0 AS revenue,
SUM(quantity*(unit_price_cents-unit_cost_cents))/100.0 AS profit FROM orders
WHERE status IN ('paid','completed') AND order_date >= '2026-10-01' AND order_date < '2026-10-21'""").rows[0]
stock = run_readonly("SELECT SUM(on_hand-reserved) FROM inventory").rows[0][0]
for col, label, value in zip(st.columns(4), ["本月销量 · 件", "本月销售额 · 元", "本月毛利 · 元", "可售库存 · 件"],
                              [f"{summary[0]:,}", f"{summary[1]:,.2f}", f"{summary[2]:,.2f}", f"{stock:,}"]):
    col.metric(label, value)
st.caption("以上概览为程序执行固定 SQL 的结果，未调用模型。")

st.markdown("### 先试一试")
st.caption("无需密钥也能运行下列预置 SQL 演示；中文自由提问需要你配置真实 API。")
if "messages" not in st.session_state:
    st.session_state.messages = []
for col, (label, sql) in zip(st.columns(3), PRESETS.items()):
    if col.button("▶ " + label, width="stretch"):
        result = run_readonly(sql, max_rows=settings.max_rows, timeout=settings.sql_timeout)
        st.session_state.messages.extend([
            {"role": "user", "content": "[预置 SQL 演示] " + label},
            {"role": "assistant", "response": {"source": "preset", "answer": format_result(result),
                                                 "result": result.to_dict(), "resolved_question": label}},
        ])

with st.expander("无需密钥：检索业务说明文档"):
    st.caption(f"本地文档按标题及段落分为 {len(split_document())} 段；使用中文关键词检索，不调用模型。")
    with st.form("document_search"):
        doc_question = st.text_input("输入指标或业务关键词", value="毛利和历史成本")
        search_docs = st.form_submit_button("检索文档")
    if search_docs:
        chunks = retrieve(doc_question)
        if not chunks:
            st.warning("未检索到相关文档依据。请换用具体业务指标关键词；不要将常识当作本店规定。")
        elif all(chunk["id"].split(".")[0] == "D10" for chunk in chunks):
            st.warning("未检索到能回答问题的具体文档依据；以下段落仅说明数据缺失范围。")
        for chunk in chunks:
            st.markdown(f"**[{chunk['id']}] {chunk['title']}**")
            st.write(chunk["text"])
            st.caption(f"来源：业务指标说明.md · 第 {chunk['line_start']}—{chunk['line_end']} 行 · 关键词检索分数 {chunk['score']}")

st.markdown("### 经营问答")
if not settings.ready:
    st.info(missing_key_message())
if not st.session_state.messages:
    st.markdown("试着输入：**本月云朵纯棉T恤的销量和销售额是多少？**")
st.caption("可以接着问：‘它的毛利呢’‘那上个月呢’。理解有歧义时会先澄清；每轮都显示完整查询理解。")
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        if "response" in message:
            render_response(message["response"])
        else:
            st.write(message["content"])

if question := st.chat_input("输入中文经营问题，例如：本月哪个商品的毛利最高？", max_chars=2000):
    history = history_from_messages(st.session_state.messages)
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.write(question)
    with st.chat_message("assistant"):
        try:
            with st.spinner("正在理解问题并执行只读查询…"):
                response = ask(question, settings, history=history)
            render_response(response)
            st.session_state.messages.append({"role": "assistant", "response": response})
        except (AgentError, QueryRejected) as exc:
            st.error(str(exc))
            st.session_state.messages.append({"role": "assistant", "content": str(exc)})
