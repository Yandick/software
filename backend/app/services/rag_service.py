from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from dataclasses import field
from typing import Any

from ..database import connect, rows_to_dicts

HIGH_RISK_PATTERNS = [
    "删除", "清空", "生产", "提权", "权限提升", "数据库重启", "重启数据库", "泄露", "攻击", "勒索", "批量", "root", "sudo",
]

DOMAIN_ALIASES = {
    "vpn": ["vpn", "VPN", "远程办公", "远程接入", "证书过期", "客户端证书"],
    "证书": ["证书", "证书过期", "客户端证书", "证书链"],
    "账号": ["账号", "账户", "用户名", "登录名", "工号"],
    "冻结": ["冻结", "锁定", "解冻", "账号锁定", "临时锁定"],
    "密码": ["密码", "口令", "重置密码", "密码过期", "登录失败"],
    "权限": ["权限", "授权", "角色", "权限申请", "权限变更"],
    "审批": ["审批", "审核", "申请", "受控流程"],
    "邮箱": ["邮箱", "邮件", "收发", "客户端授权码"],
    "网络": ["网络", "链路", "网关", "DNS", "代理"],
    "数据库": ["数据库", "DB", "SQL", "连接串", "连接池"],
    "连接失败": ["连接失败", "无法连接", "连不上", "连接异常"],
    "磁盘": ["磁盘", "空间", "挂载点", "日志清理"],
    "性能": ["性能", "系统慢", "访问慢", "超时", "响应时间"],
    "转人工": ["转人工", "在线记录", "工单", "人工处理", "申告"],
    "知识库": ["知识库", "FAQ", "处理案例", "知识候选", "沉淀"],
}

STOP_TERMS = {
    "以及", "一个", "可以", "如果", "应该", "怎么", "如何", "处理", "问题", "用户", "系统",
    "确认", "需要", "信息", "记录", "进行", "当前", "相关", "时候", "什么",
}

CHUNK_MAX_CHARS = 260
SNIPPET_MAX_CHARS = 180
MIN_REFERENCE_SCORE = 0.05


@dataclass
class RetrievalResult:
    references: list[dict[str, Any]]
    confidence: float
    high_risk: bool
    query_terms: list[str] = field(default_factory=list)
    strategy: str = "hybrid_keyword_chunk"


class RagService:
    """Deterministic RAG retriever for the operations digital employee.

    The project avoids a heavyweight vector dependency for the course demo, but
    the retriever still needs RAG-grade behavior: chunked evidence, source
    attribution, explainable matches, and conservative confidence. This hybrid
    lexical implementation can later be replaced by a vector store without
    changing the API contract used by the digital employee.
    """

    def _load_knowledge(self) -> list[dict[str, Any]]:
        with connect() as conn:
            rows = conn.execute(
                """
                select id,title,content,tags,source_type,status,version,reviewed_by,
                       reviewed_at,review_note,created_at,updated_at
                from knowledge
                where status='published'
                order by updated_at desc,id desc
                """
            ).fetchall()
        return rows_to_dicts(rows)

    def search(self, question: str, limit: int = 4) -> RetrievalResult:
        items = self._load_knowledge()
        high_risk = self.is_high_risk(question)
        if not items:
            return RetrievalResult([], 0.0, high_risk)

        chunks = self._build_chunks(items)
        query_terms = self._tokenize(question)
        display_query_terms = self._display_terms(question, query_terms)
        if not query_terms:
            return RetrievalResult([], 0.0, high_risk, display_query_terms)

        idf = self._build_idf(chunks)
        scored: list[dict[str, Any]] = []
        for chunk in chunks:
            score, detail = self._score_chunk(question, query_terms, chunk, idf)
            if score >= MIN_REFERENCE_SCORE:
                scored.append({**chunk, "score": score, "score_detail": detail})

        scored.sort(
            key=lambda item: (
                item["score"],
                item["knowledge"].get("updated_at", ""),
                item["knowledge"].get("id", 0),
            ),
            reverse=True,
        )

        refs: list[dict[str, Any]] = []
        seen: set[int] = set()
        for chunk in scored:
            item = chunk["knowledge"]
            item_id = int(item["id"])
            if item_id in seen:
                continue
            seen.add(item_id)
            matched_terms = self._matched_display_terms(question, chunk)
            snippet = self._best_snippet(item["content"], matched_terms, chunk["text"])
            refs.append(
                {
                    **item,
                    "score": round(float(chunk["score"]), 4),
                    "chunk_index": chunk["chunk_index"],
                    "snippet": snippet,
                    "matched_terms": matched_terms[:8],
                    "match_reason": self._match_reason(item, matched_terms, chunk["score_detail"]),
                    "score_detail": chunk["score_detail"],
                }
            )
            if len(refs) >= limit:
                break

        confidence = float(refs[0]["score"]) if refs else 0.0
        return RetrievalResult(refs, confidence, high_risk, display_query_terms)

    def suggest(self, keyword: str = "", limit: int = 8) -> list[dict[str, Any]]:
        items = self._load_knowledge()
        if not items:
            return []
        matched = self.search(keyword, limit=limit).references if keyword.strip() else items[:limit]

        suggestions: list[dict[str, Any]] = []
        for item in matched[:limit]:
            tags = [tag.strip() for tag in str(item.get("tags", "")).split(",") if tag.strip()]
            suggestions.append(
                {
                    "id": item["id"],
                    "title": item["title"],
                    "query": self._build_suggest_query(item),
                    "tags": tags[:3],
                    "source_type": item.get("source_type", "faq"),
                    "score": round(float(item.get("score", 0)), 4),
                    "snippet": item.get("snippet", ""),
                    "matched_terms": item.get("matched_terms", []),
                }
            )
        return suggestions

    def _build_suggest_query(self, item: dict[str, Any]) -> str:
        title = str(item.get("title", "")).strip()
        if title.endswith(("？", "?", "怎么处理", "如何处理")):
            return title
        return f"{title}怎么处理？"

    def _build_chunks(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        for item in items:
            for index, text in enumerate(self._split_content(str(item.get("content", "")))):
                searchable = f"{item.get('title', '')} {item.get('tags', '')} {text}"
                chunks.append(
                    {
                        "knowledge": item,
                        "chunk_index": index,
                        "text": text,
                        "terms": self._tokenize(searchable),
                        "title_terms": self._tokenize(str(item.get("title", ""))),
                        "tag_terms": self._tokenize(str(item.get("tags", ""))),
                    }
                )
        return chunks

    def _split_content(self, content: str) -> list[str]:
        sentences = [item.strip() for item in re.split(r"(?<=[。！？；;])\s*|\n+", content) if item.strip()]
        if not sentences:
            return [content[:CHUNK_MAX_CHARS]] if content.strip() else [""]
        chunks: list[str] = []
        current = ""
        for sentence in sentences:
            if not current:
                current = sentence
                continue
            if len(current) + len(sentence) <= CHUNK_MAX_CHARS:
                current = f"{current}{sentence}"
            else:
                chunks.append(current)
                current = sentence
        if current:
            chunks.append(current)
        return chunks or [content[:CHUNK_MAX_CHARS]]

    def _build_idf(self, chunks: list[dict[str, Any]]) -> dict[str, float]:
        doc_freq: Counter[str] = Counter()
        for chunk in chunks:
            doc_freq.update(chunk["terms"])
        total = max(len(chunks), 1)
        return {term: math.log((total + 1) / (count + 0.5)) + 1 for term, count in doc_freq.items()}

    def _score_chunk(
        self,
        question: str,
        query_terms: set[str],
        chunk: dict[str, Any],
        idf: dict[str, float],
    ) -> tuple[float, dict[str, Any]]:
        total_weight = sum(idf.get(term, 1.0) for term in query_terms) or 1.0
        overlap = query_terms & chunk["terms"]
        title_overlap = query_terms & chunk["title_terms"]
        tag_overlap = query_terms & chunk["tag_terms"]
        overlap_score = sum(idf.get(term, 1.0) for term in overlap) / total_weight
        title_score = sum(idf.get(term, 1.0) for term in title_overlap) / total_weight
        tag_score = sum(idf.get(term, 1.0) for term in tag_overlap) / total_weight
        phrase_score = self._phrase_score(question, chunk)

        score = min(1.0, 0.56 * overlap_score + 0.22 * title_score + 0.16 * tag_score + 0.06 * phrase_score)
        keyword_hits = len(self._matched_display_terms(question, chunk))
        if keyword_hits >= 3:
            score = min(1.0, score + 0.08)
        elif keyword_hits == 2:
            score = min(1.0, score + 0.04)
        if not overlap:
            score = 0.0
        if overlap and keyword_hits == 0 and score < 0.12:
            score *= 0.45

        return round(score, 4), {
            "overlap": round(overlap_score, 4),
            "title": round(title_score, 4),
            "tags": round(tag_score, 4),
            "phrase": round(phrase_score, 4),
            "matched_token_count": len(overlap),
            "keyword_hits": keyword_hits,
        }

    def _phrase_score(self, question: str, chunk: dict[str, Any]) -> float:
        normalized_question = self._normalize(question)
        haystack = self._normalize(f"{chunk['knowledge'].get('title', '')} {chunk['knowledge'].get('tags', '')} {chunk['text']}")
        hits = 0
        for term in self._display_terms(question, self._tokenize(question)):
            if len(term) >= 2 and self._normalize(term) in haystack:
                hits += 1
        if len(normalized_question) >= 4 and normalized_question in haystack:
            hits += 2
        return min(1.0, hits / 4)

    def _tokenize(self, text: str) -> set[str]:
        normalized = self._normalize(text)
        terms: set[str] = set()
        for token in re.findall(r"[a-z0-9_.+-]+", normalized):
            if token not in STOP_TERMS:
                terms.add(token)
        chinese = "".join(re.findall(r"[\u4e00-\u9fff]", normalized))
        for size in (2, 3, 4):
            for index in range(0, max(len(chinese) - size + 1, 0)):
                gram = chinese[index : index + size]
                if gram and gram not in STOP_TERMS:
                    terms.add(gram)
        for canonical, aliases in DOMAIN_ALIASES.items():
            if any(self._normalize(alias) in normalized for alias in aliases):
                terms.add(self._normalize(canonical))
                for alias in aliases:
                    alias_norm = self._normalize(alias)
                    if len(alias_norm) >= 2:
                        terms.add(alias_norm)
        return {term for term in terms if term and term not in STOP_TERMS}

    def _display_terms(self, text: str, terms: set[str]) -> list[str]:
        normalized = self._normalize(text)
        display: list[str] = []
        for canonical, aliases in DOMAIN_ALIASES.items():
            if any(self._normalize(alias) in normalized for alias in aliases):
                display.append(canonical)
        for token in re.findall(r"[A-Za-z0-9_.+-]+", text):
            if self._normalize(token) in terms and token not in display:
                display.append(token)
        return display[:8]

    def _matched_display_terms(self, question: str, chunk: dict[str, Any]) -> list[str]:
        haystack = self._normalize(f"{chunk['knowledge'].get('title', '')} {chunk['knowledge'].get('tags', '')} {chunk['text']}")
        matched = []
        for term in self._display_terms(question, self._tokenize(question)):
            if self._normalize(term) in haystack and term not in matched:
                matched.append(term)
        return matched

    def _best_snippet(self, content: str, matched_terms: list[str], fallback: str) -> str:
        sentences = [item.strip() for item in re.split(r"(?<=[。！？；;])\s*|\n+", content) if item.strip()]
        if not sentences:
            return self._compact_text(fallback or content, SNIPPET_MAX_CHARS)
        normalized_terms = [self._normalize(term) for term in matched_terms if term]

        def score(sentence: str) -> int:
            sentence_norm = self._normalize(sentence)
            return sum(1 for term in normalized_terms if term in sentence_norm)

        best = max(sentences, key=score)
        if score(best) == 0:
            best = fallback or sentences[0]
        return self._compact_text(best, SNIPPET_MAX_CHARS)

    def _match_reason(self, item: dict[str, Any], matched_terms: list[str], detail: dict[str, Any]) -> str:
        source_label = {
            "case": "历史处理案例",
            "faq": "FAQ",
            "manual": "操作手册",
            "policy": "制度流程",
            "runbook": "Runbook",
        }.get(str(item.get("source_type", "")), "知识条目")
        terms = "、".join(matched_terms[:4]) if matched_terms else "关键词"
        if detail.get("title", 0) > 0 or detail.get("tags", 0) > 0:
            return f"{source_label}的标题/标签命中：{terms}"
        return f"{source_label}正文片段命中：{terms}"

    def _normalize(self, text: str) -> str:
        return unicodedata.normalize("NFKC", str(text or "")).lower().strip()

    def _compact_text(self, text: str, limit: int) -> str:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 1].rstrip() + "..."

    def build_context(self, references: list[dict[str, Any]]) -> str:
        blocks = []
        for idx, item in enumerate(references, start=1):
            matched = "、".join(item.get("matched_terms") or [])
            snippet = item.get("snippet") or self._compact_text(str(item.get("content", "")), SNIPPET_MAX_CHARS)
            content = self._compact_text(str(item.get("content", "")), 700)
            blocks.append(
                "\n".join(
                    [
                        f"[{idx}] knowledge_id={item['id']} source_type={item.get('source_type', '')} version={item.get('version', 1)} score={item.get('score', 0)}",
                        f"标题：{item['title']}",
                        f"标签：{item.get('tags','')}",
                        f"命中词：{matched or '无'}",
                        f"命中依据：{item.get('match_reason', '')}",
                        f"命中片段：{snippet}",
                        f"知识正文：{content}",
                    ]
                )
            )
        return "\n\n".join(blocks)

    def is_high_risk(self, question: str) -> bool:
        normalized = self._normalize(question)
        return any(self._normalize(pattern) in normalized for pattern in HIGH_RISK_PATTERNS)
