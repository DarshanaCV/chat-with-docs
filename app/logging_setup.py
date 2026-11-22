"""Readable terminal progress; detailed, bounded logs per process on disk.

MCP uses stdout for its protocol. All human-readable output goes to stderr.
"""
import copy
import logging
import os
import re
import sys
import threading
import time
import textwrap
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from logging.handlers import RotatingFileHandler
from pathlib import Path

session_context = ContextVar("log_session", default="-")
request_context = ContextVar("log_request", default="-")

STAGES = {
    "search_logging": "SEARCH", "loader": "PDF",
    "document_preparation": "CACHE", "figure_answer": "FIGURE",
    "gemini_gateway": "GEMINI", "gemini_summary": "GEMINI", "summary_input": "PDF",
    "chat": "CHAT", "request_logging": "CHAT", "agent": "AGENT",
    "gemini_summary": "SUMMARY", "document_server": "TOOLS",
    "pipeline": "PDF", "image_extractor": "PDF", "embeddings": "EMBED",
    "vector_store": "SEARCH", "upload": "UPLOAD", "session_store": "STORE",
    "sessions": "CHAT", "gemini_gateway": "MODEL",
}


class ContextFilter(logging.Filter):
    def __init__(self, process_label):
        super().__init__()
        self.process_label = process_label

    def filter(self, record):
        record.process_label = self.process_label
        record.chat = getattr(record, "chat", session_context.get())
        record.request = getattr(record, "request", request_context.get())
        record.stage = getattr(record, "stage", STAGES.get(record.name.rsplit(".", 1)[-1], "APP"))
        return True


class ReadableFormatter(logging.Formatter):
    def __init__(self, detail_path):
        super().__init__("%(asctime)s  [%(chat_short)s] %(severity)s%(message)s", "%H:%M:%S")
        self.detail_path = detail_path

    def format(self, record):
        # Do not mutate the record used by the detailed file handler.
        item = copy.copy(record)
        chat = str(getattr(item, "chat", "-"))
        item.chat_short = "chat:" + chat[:8] if chat != "-" else "app"
        item.severity = (item.levelname + ": ") if item.levelno >= logging.WARNING else ""
        message = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", item.getMessage())
        message = re.sub(r"\b([a-fA-F0-9]{8})-[a-fA-F0-9-]{27,}\b", r"\1", message)
        message = " ".join(message.split())
        if len(message) > 350:
            message = message[:347] + "..."
        if item.exc_info:
            message += f" ({item.exc_info[0].__name__}; details: {self.detail_path})"
        item.msg, item.args = message, ()
        item.exc_info = item.exc_text = item.stack_info = None
        line = super().format(item)
        # Only explicit application-provided previews may create indented lines.
        preview = getattr(record, "console_preview", None)
        if preview:
            preview = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", preview)
            line += "\n" + textwrap.fill(" ".join(preview.split()), width=105,
                initial_indent="    Text: ", subsequent_indent="          ")
        if getattr(record, "question_start", False):
            line = "\n" + "-" * 78 + "\n" + line
        return line


class AccessErrorsOnly(logging.Filter):
    def filter(self, record):
        # Uvicorn's normal OPTIONS/GET polling hides useful progress.
        if isinstance(record.args, tuple) and len(record.args) >= 5:
            try:
                return int(record.args[4]) >= 400
            except (TypeError, ValueError):
                pass
        return record.levelno >= logging.WARNING


def configure_logging(process_label="API"):
    from dotenv import load_dotenv
    load_dotenv()
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TQDM_DISABLE", "1")
    os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")
    log_dir = Path(os.getenv("APP_LOG_DIR", str(Path(__file__).resolve().parents[1] / "logs")))
    log_dir.mkdir(parents=True, exist_ok=True)
    detail_path = log_dir / f"{process_label.lower()}-{os.getpid()}.log"
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(logging.DEBUG)
    context_filter = ContextFilter(process_label)
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(os.getenv("APP_LOG_LEVEL", "INFO").upper())
    console.addFilter(context_filter)
    console.setFormatter(ReadableFormatter(detail_path))
    details = RotatingFileHandler(detail_path, maxBytes=5_000_000, backupCount=2, encoding="utf-8")
    details.setLevel(logging.DEBUG)
    details.addFilter(context_filter)
    details.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(process_label)s | chat=%(chat)s request=%(request)s | %(name)s | %(message)s"))
    root.addHandler(console)
    root.addHandler(details)
    for name in ("openai", "groq", "httpx", "httpx2", "httpcore", "httpcore2", "urllib3",
                 "huggingface_hub", "transformers", "sentence_transformers", "chromadb",
                 "PIL", "asyncio", "pdfminer", "mcp", "torch", "multipart", "python_multipart"):
        logging.getLogger(name).setLevel(logging.WARNING)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(logging.INFO)
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, AccessErrorsOnly) for f in access.filters):
        access.addFilter(AccessErrorsOnly())
    logging.getLogger(__name__).info("Detailed logs: %s", detail_path)
    return detail_path


@contextmanager
def log_context(session_id, request_id="-"):
    session_token = session_context.set(session_id)
    request_token = request_context.set(request_id)
    try:
        yield
    finally:
        request_context.reset(request_token)
        session_context.reset(session_token)


@contextmanager
def progress(logger, label, interval=15):
    """Announce a blocking step and provide sparse updates during long work."""
    started = time.monotonic()
    finished = threading.Event()
    def heartbeat():
        while not finished.wait(interval):
            logger.info("Still working: %s | %.0fs elapsed", label, time.monotonic() - started)
    logger.info("%s...", label)
    thread = threading.Thread(target=copy_context().run, args=(heartbeat,), daemon=True)
    thread.start()
    try:
        yield
    except BaseException:
        logger.warning("%s stopped after %.1fs", label, time.monotonic() - started)
        raise
    else:
        logger.info("%s finished | %.1fs", label, time.monotonic() - started)
    finally:
        finished.set()
        thread.join(timeout=1)
