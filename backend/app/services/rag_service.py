from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from dataclasses import field
from threading import RLock
from typing import Any

from ..database import connect, rows_to_dicts
from .embedding_service import embedding_service
from .vector_index_service import vector_index_service

HIGH_RISK_PATTERNS = [
    "删除", "清空", "生产", "提权", "权限提升", "数据库重启", "重启数据库", "泄露", "攻击", "勒索", "批量", "root", "sudo",
    "绕过审批", "跳过审批", "免审批", "破解", "撞库", "盗号", "越权", "清除日志", "删除日志",
]

CONTROLLED_OPERATION_PATTERNS = [
    re.compile(pattern)
    for pattern in [
        r"(冻结|解冻|停用|启用|注销|清理|删除).{0,12}(运维)?账号",
        r"(运维)?账号.{0,12}(冻结|解冻|停用|启用|注销|清理|删除)",
        r"(开通|申请|修改|变更|扩大|提升|授予|增加).{0,12}(管理员|root|sudo|高权限|权限|角色)",
        r"(管理员|root|sudo|高权限).{0,8}(权限|账号|角色)",
        r"权限.{0,8}(变更|提升|扩大|开通|修改|授权)",
        r"(绕过|跳过|免).{0,8}审批",
        r"(破解|盗用|撞库).{0,8}(密码|账号|账户)",
        r"(清除|删除).{0,8}(审计|操作)?日志",
    ]
]

DOMAIN_ALIASES = {
    "vpn": ["vpn", "VPN", "远程办公", "远程接入", "证书过期", "客户端证书"],
    "mfa": ["mfa", "MFA", "2fa", "2FA", "多因素认证", "双因素认证", "二次验证", "认证器", "Authenticator"],
    "验证码": ["验证码", "短信验证码", "邮箱验证码", "手机验证", "验证失败", "收不到验证码"],
    "证书": ["证书", "证书过期", "客户端证书", "证书链"],
    "账号": ["账号", "账户", "用户名", "登录名", "工号"],
    "冻结": ["冻结", "锁定", "解冻", "账号锁定", "临时锁定"],
    "密码": ["密码", "口令", "重置密码", "密码过期", "登录失败"],
    "权限": ["权限", "授权", "角色", "权限申请", "权限变更"],
    "审批": ["审批", "审核", "申请", "受控流程"],
    "邮箱": ["邮箱", "邮件", "收发", "客户端授权码"],
    "outlook": ["outlook", "Outlook", "邮件客户端", "客户端离线", "脱机工作", "收不到邮件", "发不出邮件"],
    "网络": ["网络", "链路", "网关", "DNS", "代理", "无线", "有线", "联网"],
    "wifi": ["wifi", "wi-fi", "Wi-Fi", "WIFI", "无线网", "无线网络", "公司 Wi-Fi", "公司wifi"],
    "数据库": ["数据库", "DB", "SQL", "连接串", "连接池"],
    "连接失败": ["连接失败", "无法连接", "连不上", "连接异常"],
    "磁盘": ["磁盘", "空间", "挂载点", "日志清理"],
    "性能": ["性能", "系统慢", "访问慢", "超时", "响应时间"],
    "业务系统": ["业务系统", "应用系统", "页面白屏", "白屏", "403", "500", "502", "504", "网关错误"],
    "打印机": ["打印机", "打印", "打印失败", "打印队列", "卡在队列", "驱动", "耗材"],
    "共享盘": ["共享盘", "共享文件夹", "部门文件夹", "网盘", "文件夹", "访问被拒绝"],
    "浏览器": ["浏览器", "缓存", "cookie", "Cookie", "无痕窗口", "证书警告", "登录循环"],
    "软件安装": ["软件安装", "软件中心", "客户端更新", "安装失败", "升级失败", "插件"],
    "会议": ["会议", "音视频", "麦克风", "摄像头", "投屏", "会议室"],
    "文件恢复": ["误删", "文件恢复", "数据恢复", "回收站", "历史版本", "快照"],
    "转人工": ["转人工", "在线记录", "工单", "人工处理", "申告"],
    "知识库": ["知识库", "FAQ", "处理案例", "知识候选", "沉淀"],
}

DAILY_SUGGESTION_QUERIES = [
    "MFA 验证码收不到怎么办？",
    "VPN 连不上或证书过期怎么处理？",
    "Outlook 一直离线收不到邮件怎么排查？",
    "打印机任务卡在队列里怎么办？",
    "公司 Wi-Fi 或有线网络无法联网怎么办？",
    "业务系统白屏或 500 超时怎么处理？",
    "共享盘提示访问被拒绝怎么处理？",
    "软件中心安装失败怎么处理？",
]

STOP_TERMS = {
    "以及", "一个", "可以", "如果", "应该", "怎么", "如何", "处理", "问题", "用户", "系统",
    "确认", "需要", "信息", "记录", "进行", "当前", "相关", "时候", "什么",
    "答辩", "类似", "项目", "资料",
}

CHUNK_MAX_CHARS = 260
SNIPPET_MAX_CHARS = 180
PRIMARY_CONTEXT_MAX_CHARS = 2600
SECONDARY_CONTEXT_MAX_CHARS = 700
MIN_REFERENCE_SCORE = 0.05
MIN_EMBEDDING_REFERENCE_SCORE = 0.28
MIN_EMBEDDING_ONLY_SCORE = 0.55


@dataclass
class RetrievalResult:
    references: list[dict[str, Any]]
    confidence: float
    high_risk: bool
    query_terms: list[str] = field(default_factory=list)
    strategy: str = "qwen3_embedding_hybrid_rerank"


@dataclass
class RetrievalScore:
    final: float
    detail: dict[str, Any]


class RagService:
    """Embedding-first RAG retriever for the operations digital employee.

    The primary retriever uses the local Qwen3-Embedding model for dense
    similarity, then mixes in lexical/title/tag evidence as a conservative
    reranking signal. If embedding is explicitly disabled or unavailable, the
    service falls back to keyword reranking and marks the strategy accordingly.
    """

    def __init__(self) -> None:
        self._cache_lock = RLock()
        self._cache_key: tuple[Any, ...] | None = None
        self._cache_items: list[dict[str, Any]] = []
        self._cache_chunks: list[dict[str, Any]] = []
        self._cache_idf: dict[str, float] = {}
        self._cache_embedding_status: dict[str, Any] = {}

    def clear_cache(self) -> None:
        with self._cache_lock:
            self._cache_key = None
            self._cache_items = []
            self._cache_chunks = []
            self._cache_idf = {}
            self._cache_embedding_status = {}

    def _knowledge_signature(self, conn: Any) -> tuple[Any, ...]:
        db_row = conn.execute("pragma database_list").fetchone()
        db_file = db_row["file"] if db_row and "file" in db_row.keys() else ""
        row = conn.execute(
            """
            select
              count(*) as row_count,
              coalesce(max(id), 0) as max_id,
              coalesce(max(updated_at), '') as max_updated_at,
              coalesce(sum(length(title) + length(content) + length(tags) + length(status)), 0) as content_size
            from knowledge
            where status='published'
            """
        ).fetchone()
        return (
            db_file,
            int(row["row_count"] or 0),
            int(row["max_id"] or 0),
            str(row["max_updated_at"] or ""),
            int(row["content_size"] or 0),
        )

    def _query_published_knowledge(self, conn: Any) -> list[dict[str, Any]]:
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

    def _get_index(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, float], dict[str, Any]]:
        with connect() as conn:
            cache_key = self._knowledge_signature(conn)
            with self._cache_lock:
                if self._cache_key == cache_key:
                    return self._cache_items, self._cache_chunks, self._cache_idf, self._cache_embedding_status
            items = self._query_published_knowledge(conn)

        chunks = self._build_chunks(items)
        idf = self._build_idf(chunks)
        embedding_status = self._attach_embeddings(chunks, cache_key)
        with self._cache_lock:
            self._cache_key = cache_key
            self._cache_items = items
            self._cache_chunks = chunks
            self._cache_idf = idf
            self._cache_embedding_status = embedding_status
        return items, chunks, idf, embedding_status

    def _load_knowledge(self) -> list[dict[str, Any]]:
        items, _, _, _ = self._get_index()
        return items

    def search(self, question: str, limit: int = 4) -> RetrievalResult:
        items, chunks, idf, embedding_status = self._get_index()
        high_risk = self.is_high_risk(question)
        if not items:
            return RetrievalResult([], 0.0, high_risk)

        query_terms = self._tokenize(question)
        display_query_terms = self._display_terms(question, query_terms)
        query_embedding = self._query_embedding(question, embedding_status)
        strategy = self._retrieval_strategy(query_embedding, embedding_status)
        if not query_terms and query_embedding is None:
            return RetrievalResult([], 0.0, high_risk, display_query_terms)

        scored: list[dict[str, Any]] = []
        for chunk_index in self._candidate_chunk_indexes(question, query_terms, query_embedding, chunks, limit):
            chunk = chunks[chunk_index]
            score = self._score_chunk(question, query_terms, query_embedding, chunk, idf, strategy)
            threshold = MIN_EMBEDDING_REFERENCE_SCORE if query_embedding is not None else MIN_REFERENCE_SCORE
            if score.final >= threshold:
                scored.append({**chunk, "score": score.final, "score_detail": score.detail})

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
                    "retrieval_stage": self._retrieval_stage(query_embedding, embedding_status),
                    "score_detail": chunk["score_detail"],
                }
            )
            if len(refs) >= limit:
                break

        confidence = float(refs[0]["score"]) if refs else 0.0
        return RetrievalResult(refs, confidence, high_risk, display_query_terms, strategy)

    def suggest(self, keyword: str = "", limit: int = 8) -> list[dict[str, Any]]:
        items = self._load_knowledge()
        if not items:
            return []
        if keyword.strip():
            matched = self.search(keyword, limit=limit).references
        else:
            matched = self._daily_suggestion_items(limit)

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
        if title.startswith("用户自助："):
            title = title.removeprefix("用户自助：").strip()
            return f"{title}时我该先检查什么？"
        if title.endswith(("？", "?", "怎么处理", "如何处理")):
            return title
        return f"{title}怎么处理？"

    def _daily_suggestion_items(self, limit: int) -> list[dict[str, Any]]:
        suggestions: list[dict[str, Any]] = []
        seen: set[int] = set()
        for query in DAILY_SUGGESTION_QUERIES:
            for item in self.search(query, limit=2).references:
                item_id = int(item["id"])
                if item_id in seen:
                    continue
                seen.add(item_id)
                suggestions.append(item)
                break
            if len(suggestions) >= limit:
                return suggestions

        for item in self._load_knowledge():
            item_id = int(item["id"])
            if item_id in seen:
                continue
            tags = str(item.get("tags", ""))
            if "用户自助" not in tags and item.get("source_type") != "faq":
                continue
            seen.add(item_id)
            suggestions.append(item)
            if len(suggestions) >= limit:
                break
        return suggestions

    def _build_chunks(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        chunks: list[dict[str, Any]] = []
        for item in items:
            for index, text in enumerate(self._split_content(str(item.get("content", "")))):
                searchable = f"{item.get('title', '')} {item.get('tags', '')} {text}"
                chunks.append(
                    {
                        "knowledge": item,
                        "chunk_index": index,
                        "searchable": searchable,
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
        query_embedding: list[float] | None,
        chunk: dict[str, Any],
        idf: dict[str, float],
        strategy: str,
    ) -> RetrievalScore:
        total_weight = sum(idf.get(term, 1.0) for term in query_terms) or 1.0
        overlap = query_terms & chunk["terms"]
        title_overlap = query_terms & chunk["title_terms"]
        tag_overlap = query_terms & chunk["tag_terms"]
        overlap_score = sum(idf.get(term, 1.0) for term in overlap) / total_weight
        title_score = sum(idf.get(term, 1.0) for term in title_overlap) / total_weight
        tag_score = sum(idf.get(term, 1.0) for term in tag_overlap) / total_weight
        phrase_score = self._phrase_score(question, chunk)
        embedding_score = self._embedding_score(query_embedding, chunk.get("embedding"))

        lexical_score = min(1.0, 0.58 * overlap_score + 0.22 * title_score + 0.14 * tag_score + 0.06 * phrase_score)
        keyword_hits = len(self._matched_display_terms(question, chunk))
        if keyword_hits >= 3:
            lexical_score = min(1.0, lexical_score + 0.08)
        elif keyword_hits == 2:
            lexical_score = min(1.0, lexical_score + 0.04)
        if not overlap:
            lexical_score = 0.0
        if overlap and keyword_hits == 0 and lexical_score < 0.12:
            lexical_score *= 0.45

        has_direct_signal = bool(overlap) or keyword_hits > 0 or phrase_score > 0
        if query_embedding is not None:
            if not has_direct_signal and embedding_score < MIN_EMBEDDING_ONLY_SCORE:
                final_score = 0.0
            else:
                rerank_boost = 0.04 if embedding_score >= 0.62 and (title_overlap or tag_overlap) else 0.0
                final_score = min(1.0, 0.7 * embedding_score + 0.22 * lexical_score + 0.05 * phrase_score + 0.03 * tag_score + rerank_boost)
        else:
            rerank_boost = 0.04 if lexical_score >= 0.55 and (title_overlap or tag_overlap) else 0.0
            final_score = min(1.0, lexical_score + 0.04 * phrase_score + rerank_boost)

        return RetrievalScore(round(final_score, 4), {
            "embedding": round(embedding_score, 4),
            "final": round(final_score, 4),
            "lexical": round(lexical_score, 4),
            "overlap": round(overlap_score, 4),
            "rerank_boost": round(rerank_boost if query_embedding is None or has_direct_signal or embedding_score >= MIN_EMBEDDING_ONLY_SCORE else 0.0, 4),
            "retriever": strategy,
            "title": round(title_score, 4),
            "tags": round(tag_score, 4),
            "phrase": round(phrase_score, 4),
            "matched_token_count": len(overlap),
            "keyword_hits": keyword_hits,
        })

    def _attach_embeddings(self, chunks: list[dict[str, Any]], knowledge_signature: tuple[Any, ...]) -> dict[str, Any]:
        status = embedding_service.status()
        if not status["enabled"]:
            return {**status, "ok": False, "reason": "disabled"}
        if not chunks:
            return {**status, "ok": True, "chunk_count": 0}
        persisted_embeddings, persisted_status = vector_index_service.load_embeddings(
            chunks=chunks,
            embedding_status=status,
            knowledge_signature=knowledge_signature,
        )
        if persisted_embeddings is not None:
            for chunk, embedding in zip(chunks, persisted_embeddings, strict=False):
                chunk["embedding"] = embedding
            return {**embedding_service.status(), **persisted_status, "ok": True, "chunk_count": len(chunks)}
        try:
            embeddings = embedding_service.embed_documents([str(chunk.get("searchable", "")) for chunk in chunks])
        except Exception as exc:
            return {**status, **persisted_status, "ok": False, "error": exc.__class__.__name__}
        for chunk, embedding in zip(chunks, embeddings, strict=False):
            chunk["embedding"] = embedding
        index_status = vector_index_service.save_embeddings(
            chunks=chunks,
            embeddings=embeddings,
            embedding_status=embedding_service.status(),
            knowledge_signature=knowledge_signature,
        )
        return {**embedding_service.status(), **index_status, "ok": True, "chunk_count": len(chunks)}

    def _retrieval_strategy(self, query_embedding: list[float] | None, embedding_status: dict[str, Any]) -> str:
        if query_embedding is None:
            return "keyword_rerank_fallback"
        if str(embedding_status.get("index_backend", "")).startswith("faiss"):
            return "faiss_qwen3_embedding_hybrid_rerank"
        return "qwen3_embedding_hybrid_rerank"

    def _retrieval_stage(self, query_embedding: list[float] | None, embedding_status: dict[str, Any]) -> str:
        if query_embedding is None:
            return "keyword_rerank_fallback"
        if str(embedding_status.get("index_backend", "")).startswith("faiss"):
            return "faiss_embedding_hybrid_rerank"
        return "embedding_hybrid_rerank"

    def _candidate_chunk_indexes(
        self,
        question: str,
        query_terms: set[str],
        query_embedding: list[float] | None,
        chunks: list[dict[str, Any]],
        limit: int,
    ) -> list[int]:
        if query_embedding is None:
            return list(range(len(chunks)))

        candidate_indexes: set[int] = set()
        dense_top_k = min(len(chunks), max(40, int(limit) * 12))
        for hit in vector_index_service.search(query_embedding, top_k=dense_top_k):
            chunk_index = int(hit.get("chunk_index", -1))
            if 0 <= chunk_index < len(chunks):
                candidate_indexes.add(chunk_index)

        if query_terms:
            for index, chunk in enumerate(chunks):
                if query_terms & chunk["terms"] or query_terms & chunk["title_terms"] or query_terms & chunk["tag_terms"]:
                    candidate_indexes.add(index)
                elif self._phrase_score(question, chunk) > 0:
                    candidate_indexes.add(index)

        if not candidate_indexes:
            return list(range(len(chunks)))
        return sorted(candidate_indexes)

    def _query_embedding(self, question: str, embedding_status: dict[str, Any]) -> list[float] | None:
        if not embedding_status.get("ok"):
            return None
        try:
            return embedding_service.embed_query(question)
        except Exception:
            return None

    def _embedding_score(self, left: list[float] | None, right: list[float] | None) -> float:
        if not left or not right:
            return 0.0
        limit = min(len(left), len(right))
        if limit <= 0:
            return 0.0
        score = sum(float(left[index]) * float(right[index]) for index in range(limit))
        return max(0.0, min(1.0, score))

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
            "document": "上传文档",
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
            is_primary = idx == 1
            content_limit = PRIMARY_CONTEXT_MAX_CHARS if is_primary else SECONDARY_CONTEXT_MAX_CHARS
            content = self._compact_text(str(item.get("content", "")), content_limit)
            priority_note = (
                "PRIMARY_MATCH: highest-ranked evidence. Use this reference first; preserve its concrete cause, steps, recovery method, and cautions when answering."
                if is_primary
                else "SECONDARY_MATCH: use only to supplement or cross-check the primary reference."
            )
            blocks.append(
                "\n".join(
                    [
                        f"[{idx}] {priority_note}",
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
        compact = re.sub(r"\s+", "", normalized)
        return any(self._normalize(pattern) in normalized for pattern in HIGH_RISK_PATTERNS) or any(
            pattern.search(compact) for pattern in CONTROLLED_OPERATION_PATTERNS
        )


rag_service = RagService()
