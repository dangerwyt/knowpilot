"""Pydantic 请求/响应模型。"""
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, ConfigDict


# ---------- Auth ----------
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6)
    name: str | None = None


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: str
    email: str
    name: str | None
    role: str


class TokenOut(BaseModel):
    token: str
    user: UserOut


# ---------- Projects ----------
class ProjectIn(BaseModel):
    name: str
    description: str | None = None


class ProjectOut(BaseModel):
    id: str
    name: str
    description: str | None
    created_at: datetime


# ---------- Tasks ----------
class TaskCreateIn(BaseModel):
    project_id: str
    objective: str = Field(min_length=5, description="调研目标")
    title: str | None = None
    sources: Literal["all", "web", "kb"] = "all"
    kb_ids: list[str] | None = None


class TaskOut(BaseModel):
    id: str
    project_id: str
    kb_ids: list[str] | None
    title: str | None
    objective: str
    status: str
    plan: Any | None
    report_id: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    score: int | None = None
    elapsed_seconds: float | None = None
    passed: bool | None = None

    model_config = ConfigDict(from_attributes=True)


class ResumeIn(BaseModel):
    feedback: str | None = None


# ---------- Knowledge Base ----------
class KBCreateIn(BaseModel):
    name: str
    chunk_strategy: dict | None = None


class KBOut(BaseModel):
    id: str
    name: str
    embedding_model: str
    chunk_strategy: dict
    doc_count: int
    created_at: datetime


class DocumentOut(BaseModel):
    id: str
    kb_id: str
    file_name: str
    file_type: str
    status: str
    chunk_count: int
    error: str | None
    created_at: datetime | None = None
    size_bytes: int | None = None

    model_config = ConfigDict(from_attributes=True)


class ChunkOut(BaseModel):
    seq: int
    content: str


class DocumentDetailOut(DocumentOut):
    content: str | None = None
    object_key: str | None = None
    chunks: list[ChunkOut] = []


# ---------- Reports ----------
class ReportOut(BaseModel):
    id: str
    task_id: str
    title: str
    content: dict
    version: int
    created_at: datetime


class CitationOut(BaseModel):
    id: str
    section_id: str
    source_type: str
    source_url: str | None
    source_title: str
    snippet: str | None
    document_id: str | None = None
    kb_id: str | None = None


class SectionRevisitIn(BaseModel):
    instruction: str


class PrecheckIn(BaseModel):
    objective: str = Field(min_length=1)
    kb_ids: list[str] | None = None


class PrecheckOut(BaseModel):
    has_material: bool | None = None
    material_count: int = 0