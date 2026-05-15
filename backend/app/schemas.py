from typing import Any
from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class KnowledgeCreate(BaseModel):
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    tags: str = ""
    source_type: str = "faq"
    status: str = "published"


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1)
    enable_thinking: bool | None = None


class IssueCreate(BaseModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    contact_phone: str = ""
    priority: str = "medium"


class IssueHandle(BaseModel):
    solution: str = Field(min_length=1)


class IssueVisit(BaseModel):
    resolved: bool
    satisfaction_score: int | None = None
    visit_result: str = ""


class AccountCreate(BaseModel):
    account_name: str = Field(min_length=1)
    permission_scope: str = "basic_ops"
    remark: str = ""


class AccountUpdate(BaseModel):
    permission_scope: str | None = None
    remark: str | None = None
    status: str | None = None


class MenuMeta(BaseModel):
    title: str
    icon: str | None = None
    order: int | None = None


class ApiResponse(BaseModel):
    data: Any
