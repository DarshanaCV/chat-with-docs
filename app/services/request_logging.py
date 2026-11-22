"""Live tool choices, not a replay of past tool calls after an answer finishes."""
import functools
import logging
import time
import uuid

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import ToolMessage

from app.logging_setup import log_context

logger = logging.getLogger(__name__)
TOOL_LABELS = {
    "get_document_index": "Reading saved document index",
    "read_document_section": "Reading requested document section",
    "read_document_pages": "Reading original page text",
    "explain_document_figures": "Explaining indexed figures",
    "search_documents": "Searching the document for this question",
    "get_document_overview": "Summarizing the document",
}


def logged_chat(function):
    @functools.wraps(function)
    async def wrapped(payload, request):
        started = time.monotonic()
        with log_context(payload.session_id, uuid.uuid4().hex[:8]):
            logger.info("QUESTION: %.180s", payload.question, extra={"question_start": True})
            try:
                result = await function(payload, request)
            except BaseException:
                logger.warning("Request did not finish successfully | %.1fs", time.monotonic() - started)
                raise
            logger.info("Answer ready | %.0f sources attached | total %.1fs", len(result.sources), time.monotonic() - started)
            return result
    return wrapped


class ChatProgress(BaseCallbackHandler):
    def __init__(self, session_id):
        self.extra = {"chat": session_id, "stage": "AGENT"}

    def on_chat_model_start(self, serialized, messages, **kwargs):
        after_tool = any(batch and isinstance(batch[-1], ToolMessage) for batch in messages)
        logger.info("%s", "Gemini is reviewing the results (it may answer or request another lookup)" if after_tool else
                    "Gemini is deciding how to answer", extra=self.extra)

    def on_tool_start(self, serialized, input_str, **kwargs):
        name = (serialized or {}).get("name", "unknown tool")
        logger.debug("%s | tool=%s", TOOL_LABELS.get(name, "Running tool"), name, extra=self.extra)

    def on_llm_end(self, response, **kwargs):
        names = [call["name"] for batch in response.generations for generation in batch
                 for call in getattr(getattr(generation, "message", None), "tool_calls", [])]
        if names:
            logger.debug("Agent chose: %s", ", ".join(names), extra=self.extra)
        else:
            logger.debug("Answer text generated", extra=self.extra)

    def on_tool_end(self, output, **kwargs):
        if getattr(output, "status", None) == "error":
            logger.warning("Tool reported a failure; the agent will receive the error", extra=self.extra)
        else:
            logger.debug("Tool finished; returning its result to the agent", extra=self.extra)

    def on_tool_error(self, error, **kwargs):
        logger.error("Tool failed: %s", type(error).__name__, extra=self.extra)


def logged_document_tool(function):
    @functools.wraps(function)
    def wrapped(session_id, *args, **kwargs):
        with log_context(session_id):
            started = time.monotonic()
            logger.info("%s", TOOL_LABELS.get(function.__name__, "Running document tool: " + function.__name__), extra={"stage": "TOOLS"})
            try:
                result = function(session_id, *args, **kwargs)
            except BaseException:
                logger.exception("Document tool failed after %.1fs", time.monotonic() - started)
                raise
            logger.info("%s completed | %.1fs", TOOL_LABELS.get(function.__name__, "Document tool"), time.monotonic() - started, extra={"stage": "TOOLS"})
            return result
    return wrapped
