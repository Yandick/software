from typing import Any
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class KnowledgeCreate(BaseModel):
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    tags: str = ""
    source_type: str = "faq"
    status: str = "pending_review"


class KnowledgeStatusUpdate(BaseModel):
    status: str = Field(min_length=1)
    review_note: str = ""


class KnowledgeSensitiveCheckRequest(BaseModel):
    title: str = ""
    content: str = ""
    tags: str = ""


class KnowledgeDuplicateCheckRequest(BaseModel):
    title: str = ""
    content: str = ""
    tags: str = ""
    exclude_id: int | None = None


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1)
    enable_thinking: bool | None = None
    conversation_id: int | None = None


class IssueDraftRequest(BaseModel):
    description: str = Field(min_length=1)


class IssueCreate(BaseModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    contact_phone: str = ""
    priority: str = "medium"
    category: str = "general"
    impact_scope: str = ""
    attachment_url: str = ""
    log_excerpt: str = ""


class IssueHandle(BaseModel):
    solution: str = Field(min_length=1)


class IssueStatusUpdate(BaseModel):
    status: str = Field(min_length=1)
    note: str = ""


class IssueVisit(BaseModel):
    resolved: bool
    satisfaction_score: int | None = None
    visit_result: str = ""


class IssueFeedback(BaseModel):
    satisfaction_score: int = Field(ge=1, le=5)
    feedback: str = ""


class AccountCreate(BaseModel):
    account_name: str = Field(min_length=1)
    owner_name: str = ""
    department: str = ""
    contact_phone: str = ""
    permission_scope: str = "basic_ops"
    risk_level: str = "medium"
    expires_at: str = ""
    remark: str = ""


class AccountUpdate(BaseModel):
    owner_name: str | None = None
    department: str | None = None
    contact_phone: str | None = None
    permission_scope: str | None = None
    risk_level: str | None = None
    expires_at: str | None = None
    remark: str | None = None
    status: str | None = None


class AccountApprovalCreate(BaseModel):
    account_id: int
    action: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class AccountApprovalDecision(BaseModel):
    decision: str = Field(pattern="^(approved|rejected)$")
    reason: str = ""


class StaffUserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=6, max_length=256)
    real_name: str = Field(min_length=1, max_length=128)
    role: str = "ops"
    department: str = "运维中心"
    status: str = "active"


class StaffUserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=128)
    real_name: str | None = Field(default=None, min_length=1, max_length=128)
    role: str | None = None
    department: str | None = None
    status: str | None = None


class StaffUserPasswordReset(BaseModel):
    password: str = Field(min_length=6, max_length=256)


class MenuMeta(BaseModel):
    title: str
    icon: str | None = None
    order: int | None = None


class ApiResponse(BaseModel):
    data: Any
