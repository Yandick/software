from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..database import connect, rows_to_dicts

HIGH_RISK_PATTERNS = [
    "删除", "清空", "生产", "提权", "权限提升", "数据库重启", "重启数据库", "泄露", "攻击", "勒索", "批量", "root", "sudo",
]


@dataclass
class RetrievalResult:
    references: list[dict[str, Any]]
    confidence: float
    high_risk: bool


class RagService:
    def _load_knowledge(self) -> list[dict[str, Any]]:
        with connect() as conn:
            rows = conn.execute(
                "select id,title,content,tags,source_type,status,created_at,updated_at from knowledge where status='published' order by id desc"
            ).fetchall()
        return rows_to_dicts(rows)

    def search(self, question: str, limit: int = 4) -> RetrievalResult:
        items = self._load_knowledge()
        if not items:
            return RetrievalResult([], 0.0, self.is_high_risk(question))
        scores = self._score(question, items)
        ranked = sorted(zip(items, scores), key=lambda pair: pair[1], reverse=True)
        refs = [{**item, "score": round(float(score), 4)} for item, score in ranked[:limit] if score > 0]
        confidence = float(refs[0]["score"]) if refs else 0.0
        return RetrievalResult(refs, confidence, self.is_high_risk(question))

    def suggest(self, keyword: str = "", limit: int = 8) -> list[dict[str, Any]]:
        items = self._load_knowledge()
        if not items:
            return []
        if keyword.strip():
            ranked = sorted(
                zip(items, self._score(keyword, items)),
                key=lambda pair: pair[1],
                reverse=True,
            )
            matched = [(item, float(score)) for item, score in ranked if score > 0]
        else:
            matched = [(item, 0.0) for item in items]

        suggestions: list[dict[str, Any]] = []
        for item, score in matched[:limit]:
            tags = [tag.strip() for tag in str(item.get("tags", "")).split(",") if tag.strip()]
            suggestions.append(
                {
                    "id": item["id"],
                    "title": item["title"],
                    "query": self._build_suggest_query(item),
                    "tags": tags[:3],
                    "source_type": item.get("source_type", "faq"),
                    "score": round(score, 4),
                }
            )
        return suggestions

    def _build_suggest_query(self, item: dict[str, Any]) -> str:
        title = str(item.get("title", "")).strip()
        if title.endswith(("？", "?", "怎么处理", "如何处理")):
            return title
        return f"{title}怎么处理？"

    def _score(self, question: str, items: list[dict[str, Any]]) -> list[float]:
        corpus = [f"{item['title']} {item['tags']} {item['content']}" for item in items]
        q_terms = self._tokenize(question)
        scores: list[float] = []
        for doc in corpus:
            d_terms = self._tokenize(doc)
            overlap = q_terms & d_terms
            scores.append(len(overlap) / max(len(q_terms), 1))
        return scores

    def _tokenize(self, text: str) -> set[str]:
        chars = [char.lower() for char in text if char.strip()]
        grams = set(chars)
        grams.update("".join(chars[index : index + 2]) for index in range(max(len(chars) - 1, 0)))
        return grams

    def build_context(self, references: list[dict[str, Any]]) -> str:
        blocks = []
        for idx, item in enumerate(references, start=1):
            blocks.append(f"[{idx}] 标题：{item['title']}\n标签：{item.get('tags','')}\n内容：{item['content']}")
        return "\n\n".join(blocks)

    def is_high_risk(self, question: str) -> bool:
        return any(pattern in question for pattern in HIGH_RISK_PATTERNS)
