from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from ..deps import require_roles
from ..schemas import QuestionRequest
from ..security import current_user
from ..services.qa_service import (
    ask_question,
    evaluate_rag as evaluate_rag_cases,
    get_qa_conversation as get_qa_conversation_record,
    list_qa_conversations as list_qa_conversation_rows,
    suggest_knowledge,
)

router = APIRouter(prefix="/api", tags=["qa"])


@router.post("/qa/ask")
def ask(data: QuestionRequest, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return ask_question(data, user)


@router.get("/qa/suggest")
def suggest(q: str = "", limit: int = Query(8, ge=1, le=20), user: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    return suggest_knowledge(q, limit)


@router.get("/rag/evaluate")
def evaluate_rag(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    require_roles(user, {"admin", "ops", "auditor"})
    return evaluate_rag_cases()


@router.get("/qa/conversations")
def list_qa_conversations(
    limit: int = Query(20, ge=1, le=100),
    user: dict[str, Any] = Depends(current_user),
) -> list[dict[str, Any]]:
    return list_qa_conversation_rows(limit, user)


@router.get("/qa/conversations/{conversation_id}")
def get_qa_conversation(conversation_id: int, user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    return get_qa_conversation_record(conversation_id, user)
