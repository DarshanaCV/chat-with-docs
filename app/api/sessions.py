import logging

from app.models.schemas import SessionSummary, SessionDetail, MessageOut, AttachDocumentRequest, DocumentInfo
from app.services.session_store import (
    list_sessions as _list_sessions,
    get_session as _get_session,
    delete_session as _delete_session,
    upsert_session,
    attach_document as _attach_document,
    remove_document as _remove_document,
    list_session_documents,
)
from app.services.vector_store import delete_document
from app.services.pending_documents import discard_document, peek_pending_upload
from app.services.visitor_scope import scoped_session_id, visitor_prefix
from fastapi import APIRouter, Depends, HTTPException, Request

logger = logging.getLogger(__name__)
router=APIRouter(prefix="/sessions",tags=["sessions"])

@router.get("",response_model=list[SessionSummary])
def list_sessions(prefix: str = Depends(visitor_prefix)):
    rows = _list_sessions(prefix)
    for row in rows:
        row["session_id"] = row["session_id"][len(prefix):]
    logger.debug("GET /sessions -> %d session(s)", len(rows))
    return [SessionSummary(**row) for row in rows]

@router.get("/{session_id}", response_model=SessionDetail)
def get_session(session_id: str, prefix: str = Depends(visitor_prefix)):
    session = _get_session(scoped_session_id(prefix, session_id))
    if session is None:
        logger.warning("GET /sessions/%s -> 404 not found", session_id)
        raise HTTPException(status_code=404, detail="Session not found")
    session["messages"] = [MessageOut(**m) for m in session["messages"]]
    session["documents"] = [DocumentInfo(**d) for d in session["documents"]]
    session["session_id"] = session_id
    return SessionDetail(**session)


@router.post("/{session_id}/documents", response_model=list[DocumentInfo])
def attach_document(session_id: str, payload: AttachDocumentRequest, prefix: str = Depends(visitor_prefix)):
    session_id = scoped_session_id(prefix, session_id)
    if prefix:
        document = peek_pending_upload(payload.document_id) if payload.document_id.startswith(prefix) else None
        if document is None:
            raise HTTPException(status_code=404, detail="Upload not found")
        payload = AttachDocumentRequest(document_id=payload.document_id,
                                        filename=document["filename"], content_type=document["content_type"])
    logger.debug("POST /sessions/%s/documents: document_id=%s filename=%r content_type=%s", session_id, payload.document_id, payload.filename, payload.content_type)
    upsert_session(session_id, title=payload.filename[:60])  # no-op on title if the session already exists
    _attach_document(session_id, payload.document_id, payload.filename, payload.content_type)
    return [DocumentInfo(**d) for d in list_session_documents(session_id)]


@router.delete("/{session_id}/documents/{document_id}", response_model=list[DocumentInfo])
def remove_document(session_id: str, document_id: str, prefix: str = Depends(visitor_prefix)):
    session_id = scoped_session_id(prefix, session_id)
    logger.info("DELETE /sessions/%s/documents/%s", session_id, document_id)
    documents = list_session_documents(session_id)
    match = next((d for d in documents if d["document_id"] == document_id), None)
    if match is None:
        logger.warning("session=%s document_id=%s not attached -> 404", session_id, document_id)
        raise HTTPException(status_code=404, detail="Document not attached to this session")
    discard_document(document_id)  # in case it was never asked about (still staged, not in Chroma)
    delete_document(document_id)   # in case it was already committed
    _remove_document(session_id, document_id)
    return [DocumentInfo(**d) for d in list_session_documents(session_id)]


@router.delete("/{session_id}", status_code=204)
async def delete_session(session_id: str, request: Request, prefix: str = Depends(visitor_prefix)):
    session_id = scoped_session_id(prefix, session_id)
    logger.info("DELETE /sessions/%s", session_id)
    _delete_session(session_id)
    try:
        await request.app.state.checkpointer.adelete_thread(session_id)
    except Exception:
        logger.exception("session=%s failed to delete LangGraph checkpoint thread (Postgres row for the conversation state may be orphaned)", session_id)
