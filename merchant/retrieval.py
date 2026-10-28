"""离线中文关键词检索，不需要向量库或第二个 API 密钥。"""
from collections import Counter
from dataclasses import asdict, dataclass
import math
import re

from merchant.config import DOC_PATH

TOPICS = {
    "D01": ("演示数据", "数据范围", "参照日期", "演示日期", "今天", "本月", "上个月"),
    "D02": ("有效成交", "退款", "取消", "订单数", "订单状态", "paid", "completed", "refunded", "cancelled"),
    "D03": ("销量", "销售件数", "件数"),
    "D04": ("销售额", "成交价", "售价", "标价", "折后", "金额单位", "税额"),
    "D05": ("毛利", "历史成本", "历史单位成本", "净利润", "成交成本", "广告费", "平台佣金"),
    "D06": ("毛利率", "利润率"),
    "D07": ("库存", "占用", "可售", "账面库存", "历史库存", "周转天数", "on_hand", "reserved"),
    "D08": ("低库存", "安全库存", "库存预警", "库存金额", "库存估值"),
    "D09": ("商品目录", "商品编号", "类目"),
    "D10": ("业务边界", "缺失依据"),
}
STOP_WORDS = {"什么", "怎么", "如何", "多少", "计算", "我们", "本店", "请问", "这个", "那个", "一下", "说明", "规则", "的是", "是否", "以及", "为何", "可以", "告诉", "目标"}


@dataclass(frozen=True)
class Chunk:
    id: str
    title: str
    text: str
    line_start: int
    line_end: int
    source: str

    def to_dict(self):
        return asdict(self)


def split_document(path=DOC_PATH, chunk_size=450, overlap=60):
    if chunk_size < 80 or not 0 <= overlap < chunk_size:
        raise ValueError("分段长度或重叠参数无效。")
    text = path.read_text(encoding="utf-8")
    headings = list(re.finditer(r"^##\s+(D\d+)\s+(.+)$", text, flags=re.M))
    chunks = []
    for index, match in enumerate(headings):
        body_start = match.end()
        body_end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        raw = text[body_start:body_end]
        begin = body_start + len(raw) - len(raw.lstrip())
        body = raw.strip()
        offset, part = 0, 0
        while offset < len(body):
            end = min(offset + chunk_size, len(body))
            if end < len(body):
                boundary = body.rfind("。", offset + chunk_size // 2, end)
                if boundary >= 0:
                    end = boundary + 1
            part += 1
            chunk_id = match[1] if part == 1 else f"{match[1]}.{part}"
            chunks.append(Chunk(chunk_id, match[2], body[offset:end],
                                text.count("\n", 0, begin + offset) + 1,
                                text.count("\n", 0, begin + end) + 1, str(path)))
            if end == len(body):
                break
            offset = end - overlap
    return chunks


def terms(text):
    result = set()
    for token in re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z][a-zA-Z0-9_]+", text.lower()):
        if re.search(r"[\u4e00-\u9fff]", token):
            result.update(token[i:i+2] for i in range(len(token) - 1) if token[i:i+2] not in STOP_WORDS)
        else:
            result.add(token)
    return result


def retrieve(question, path=DOC_PATH, top_k=3):
    chunks = split_document(path)
    query_terms = terms(question)
    doc_terms = [terms(chunk.title + " " + chunk.text) for chunk in chunks]
    frequencies = Counter(term for term_set in doc_terms for term in term_set)
    scored = []
    for chunk, tokens in zip(chunks, doc_terms):
        topic = chunk.id.split(".")[0]
        keywords = [word for word in TOPICS.get(topic, ()) if word.lower() in question.lower()]
        overlap = query_terms & tokens
        if not keywords and len(overlap) < 2:
            continue
        lexical = sum(math.log((len(chunks) + 1) / (frequencies[t] + 1)) + 1 for t in overlap)
        # 短关键词按长度加权，特定口径（毛利率、低库存）优先于泛词（毛利、库存）。
        score = lexical + sum(3 * len(keyword) for keyword in keywords)
        scored.append((score, chunk))
    scored.sort(key=lambda item: (-item[0], item[1].id))
    return [dict(chunk.to_dict(), score=round(score, 2)) for score, chunk in scored[:top_k]]


def format_context(chunks):
    return "\n\n".join(f"[{c['id']}] {c['title']}\n{c['text']}" for c in chunks) or "未检索到相关文档依据。"
