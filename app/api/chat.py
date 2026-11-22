import json
import logging

from fastapi import APIRouter, HTTPException, Request
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage, ToolMessage

from app.models.schemas import AskRequest,AskResponse, Source
from app.services.agent import SYSTEM_PROMPT
from app.services.gemini_gateway import RequestTooLarge
from app.services.request_logging import ChatProgress, logged_chat
from app.services.document_focus import request_scope
from app.services.document_clarification import resolve_turn
from app.services.visitor_scope import scoped_session_id, visitor_prefix
from app.services.session_store import (
    add_message,
    get_document_focus,
    get_pending_document_request,
    list_session_documents,
    set_document_focus,
    set_pending_document_request,
    upsert_session,
)

logger=logging.getLogger(__name__)
router=APIRouter()

@router.post("/ask",response_model=AskResponse)
@logged_chat
async def ask_question(payload:AskRequest,request:Request):
    visitor_session_id = payload.session_id
    payload = payload.model_copy(update={"session_id": scoped_session_id(visitor_prefix(request), visitor_session_id)})
    config = {"configurable": {"thread_id": payload.session_id},
              "callbacks": [ChatProgress(payload.session_id)]}
    try:
        upsert_session(payload.session_id,title=payload.question[:60])
    except Exception:
        logger.exception("session=%s failed to upsert session", payload.session_id)
        raise HTTPException(status_code=500,detail="Could not load this conversation's state")

    agent=request.app.state.agent
    attached = list_session_documents(payload.session_id)
    decision = resolve_turn(payload.question, attached, get_document_focus(payload.session_id),
                            get_pending_document_request(payload.session_id))
    set_pending_document_request(payload.session_id, decision["pending"])
    if "reply" in decision:
        answer = decision["reply"]
        logger.info("Document clarification: %s", answer)
        add_message(payload.session_id, "user", payload.question)
        add_message(payload.session_id, "assistant", answer)
        return AskResponse(session_id=visitor_session_id, question=payload.question, response=answer, sources=[])
    selected = decision["selected"]
    effective_question = decision["question"]
    set_document_focus(payload.session_id, selected)
    logger.info("Document focus for this question: %s",
                [d["filename"] for d in attached if d["document_id"] in selected])
    inventory = json.dumps([dict(d, upload_number=i) for i, d in enumerate(attached, 1)], ensure_ascii=False)
    prompt = SYSTEM_PROMPT.format(session_id=payload.session_id) + "\nAttached documents in upload order (metadata only):\n" + inventory
    prompt += "\nBackend-selected document IDs for this question: " + json.dumps(selected)
    messages=[SystemMessage(content=prompt,id="session-context"),
              HumanMessage(content=effective_question)]
    scope_token = request_scope.set((payload.session_id, selected))
    try:
        result=await agent.ainvoke({"messages":messages},config=config)
    except RequestTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=503, detail="The model is busy. Please try again shortly.") from exc
    except Exception:
        logger.exception("session=%s agent.aiinvoke failed",payload.session_id)
        raise HTTPException(status_code=500,detail="Something went wrong answering that question")
    finally:
        request_scope.reset(scope_token)

    sources=[]
    latest_question = max(
        (i for i, message in enumerate(result["messages"]) if isinstance(message, HumanMessage)),
        default=-1,
    )
    for message in result["messages"][latest_question + 1:]:
        if isinstance(message,AIMessage) and message.tool_calls:
            logger.debug("Completed turn used tools: %s", [tc["name"] for tc in message.tool_calls])
        elif isinstance(message, ToolMessage):
            logger.debug("Tool result received: %s", message.name)
            try:
                raw = message.content
                if isinstance(raw, list):
                    text = next((b["text"] for b in raw if b.get("type") == "text"), None)
                else:
                    text = raw
                if text:
                    sources.extend(json.loads(text).get("sources", []))
            except (json.JSONDecodeError, TypeError, KeyError):
                pass


    answer=result['messages'][-1].content
    sources=[Source(**s) for s in sources]
    logger.debug("Answer generated: %d characters, %d sources", len(answer), len(sources))

    try:
        add_message(payload.session_id,"user",payload.question)
        add_message(payload.session_id,"assistant",answer,sources=[s.model_dump() for s in sources])
    except Exception:
        logger.exception("session=%s failed to persist messages",payload.session_id)

    return AskResponse(session_id=visitor_session_id, question=payload.question, response=answer, sources=sources)
