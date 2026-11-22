from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel


class Source(BaseModel):
    source_id: Optional[str] = None
    chunk_id: str
    content_type: Literal["text", "table", "figure"]
    filename: str
    page_number: int
    score: float
    content: str
    table_number: Optional[int] = None
    figure_number: Optional[int | str] = None
    caption: Optional[str] = None
    image_path: Optional[str] = None
    image_url: Optional[str] = None
    rows: Optional[list[list[str]]] = None


class MessageOut(BaseModel):
    role: Literal["user", "assistant", "attachment", "attachment_removed"]
    content: str
    sources: Optional[list[Source]] = None
    created_at: datetime


class DocumentInfo(BaseModel):
    document_id: str
    filename: str
    content_type: Literal["document", "image"]


class AttachDocumentRequest(BaseModel):
    document_id: str
    filename: str
    content_type: Literal["document", "image"] = "document"


class AskRequest(BaseModel):
    session_id: str
    question: str


class AskResponse(BaseModel):
    session_id: str
    question: str
    response: str
    sources: list[Source]


class SessionSummary(BaseModel):
    session_id: str
    title: str
    message_count: int
    document_count: int
    created_at: datetime
    updated_at: datetime


class SessionDetail(SessionSummary):
    messages: list[MessageOut]
    documents: list[DocumentInfo]
