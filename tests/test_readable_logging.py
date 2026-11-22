import asyncio
import inspect
import logging
import os
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

from app.logging_setup import ContextFilter, ReadableFormatter, log_context, progress, session_context
from app.services.request_logging import ChatProgress, logged_chat, logged_document_tool


def test_console_error_is_short_and_does_not_destroy_traceback():
    try:
        raise ValueError("diagnostic detail")
    except ValueError:
        record = logging.LogRecord("app.api.chat", logging.ERROR, __file__, 1,
                                   "Failed\n\x1b[31mnew line", (), sys.exc_info())
    ContextFilter("API").filter(record)
    console = ReadableFormatter("logs/api-test.log").format(record)
    assert "\n" not in console and "\x1b" not in console
    assert "ValueError" in console and "logs/api-test.log" in console
    assert "Traceback" not in console
    assert "Traceback" in logging.Formatter().format(record)


def test_sessions_remain_separate_between_async_tasks():
    async def emit(session):
        with log_context(session):
            await asyncio.sleep(0)
            record = logging.LogRecord("test", logging.INFO, __file__, 1, "status", (), None)
            ContextFilter("API").filter(record)
            return record.chat
    async def run():
        return await asyncio.gather(emit("session-a"), emit("session-b"))
    assert asyncio.run(run()) == ["session-a", "session-b"]
    assert session_context.get() == "-"


def test_progress_heartbeat_stops_and_preserves_chat(caplog):
    logger = logging.getLogger("test.progress")
    signalled = threading.Event()
    class Capture(logging.Handler):
        def emit(self, record):
            if "Still working" in record.getMessage():
                assert session_context.get() == "session-a"
                signalled.set()
    handler = Capture()
    logger.addHandler(handler)
    try:
        with caplog.at_level(logging.INFO), log_context("session-a"):
            with progress(logger, "Reading PDF", interval=.01):
                assert signalled.wait(2)
        assert "Reading PDF finished" in caplog.text
    finally:
        logger.removeHandler(handler)


def test_progress_failure_does_not_claim_finished(caplog):
    with caplog.at_level(logging.INFO), pytest.raises(ValueError):
        with progress(logging.getLogger("test"), "Reading PDF"):
            raise ValueError("failed")
    assert "Reading PDF stopped" in caplog.text
    assert "Reading PDF finished" not in caplog.text


def test_tool_choice_logs_search_and_summary_without_payloads(caplog):
    callback = ChatProgress("session-a")
    with caplog.at_level(logging.DEBUG):
        callback.on_tool_start({"name": "search_documents"}, "PRIVATE_QUERY")
        callback.on_tool_start({"name": "get_document_overview"}, "PRIVATE_QUERY")
        callback.on_tool_end(SimpleNamespace(status="error"))
    assert "Searching the document" in caplog.text
    assert "Summarizing the document" in caplog.text
    assert "PRIVATE_QUERY" not in caplog.text
    assert "reported a failure" in caplog.text


def test_decorators_preserve_fastapi_and_mcp_signatures(caplog):
    @logged_chat
    async def ask(payload: str, request: str):
        return SimpleNamespace(sources=[])
    @logged_document_tool
    def search_documents(session_id: str, query: str) -> str:
        """Search test documents."""
        assert session_context.get() == session_id
        return query
    assert list(inspect.signature(ask).parameters) == ["payload", "request"]
    assert inspect.signature(search_documents).parameters["query"].annotation is str
    with caplog.at_level(logging.INFO):
        assert search_documents("session-a", "query") == "query"
        asyncio.run(ask(SimpleNamespace(session_id="session-a", question="question"), None))
    assert "Answer ready" in caplog.text
    assert session_context.get() == "-"


def test_logging_bootstrap_keeps_mcp_stdout_clean(tmp_path):
    script = '''
import logging
from app.logging_setup import configure_logging, log_context
configure_logging("MCP")
from mcp.server.fastmcp import FastMCP
server = FastMCP("logging-test")
with log_context("session-test"):
    logging.getLogger("app.services.gemini_summary").info("Summarizing section 2/5")
    logging.getLogger("httpcore2.connection").debug("NOISY_TLS_DETAILS")
    logging.getLogger("openai._base_client").debug("NOISY_PROVIDER_DETAILS")
    logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', 'client', 'GET', '/sessions', '1.1', 200)
    logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', 'client', 'POST', '/ask', '1.1', 500)
    try:
        raise RuntimeError("full diagnostic")
    except RuntimeError:
        logging.getLogger("app").exception("Operation failed")
print("protocol-only")
'''
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                            env={**os.environ, "APP_LOG_DIR": str(tmp_path), "APP_LOG_LEVEL": "INFO"}, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "protocol-only"
    assert "Summarizing section 2/5" in result.stderr
    assert "chat:session-" in result.stderr
    assert "NOISY_" not in result.stderr and "Traceback" not in result.stderr
    assert 'GET /sessions' not in result.stderr
    assert 'POST /ask' in result.stderr
    detail = next(tmp_path.glob("mcp-*.log")).read_text(encoding="utf-8")
    assert "Traceback" in detail and "full diagnostic" in detail


def test_nested_tool_callbacks_do_not_duplicate_console_progress(caplog):
    callback = ChatProgress("session-a")
    with caplog.at_level(logging.INFO):
        for _ in range(2):
            callback.on_tool_start({"name": "search_documents"}, "query")
            callback.on_tool_end(SimpleNamespace(status="success"))
    assert not caplog.records


def test_search_preview_is_indented_and_console_keeps_chat_identity():
    record = logging.LogRecord("app.services.search_logging", logging.INFO, __file__, 1,
        "Result 1 | paper.pdf | page 1", (), None)
    record.console_preview = "Some readable source text. " * 20
    record.chat = "abcdefgh-session"
    ContextFilter("MCP").filter(record)
    output = ReadableFormatter("details.log").format(record)
    assert "[chat:abcdefgh]" in output
    assert "\n    Text: " in output
    assert "MCP" not in output and "INFO" not in output
    assert all(len(line) <= 105 for line in output.splitlines()[1:])


@pytest.mark.parametrize("name", ["read_document_section", "future_document_tool"])
def test_tool_executes_even_without_registered_log_label(name, caplog):
    calls = []
    def implementation(session_id, section):
        calls.append((session_id, section))
        return "saved section text"
    implementation.__name__ = name
    decorated = logged_document_tool(implementation)
    with caplog.at_level(logging.INFO):
        assert decorated("chat", "Conclusion") == "saved section text"
    assert calls == [("chat", "Conclusion")]
    assert "completed" in caplog.text
