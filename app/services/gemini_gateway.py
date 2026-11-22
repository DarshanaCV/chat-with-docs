"""Gemini transport and a shared PostgreSQL minute budget for API and MCP."""
import json
import logging
import os
import time
import random
import httpx
from openai import OpenAI, APIConnectionError
from app.logging_setup import progress

logger = logging.getLogger(__name__)
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

class RequestTooLarge(ValueError):
    pass

class GeminiUnavailable(TimeoutError):
    """Temporary provider failure after bounded retries."""


def retry_request(operation):
    """Retry only the transport call, never the surrounding agent/tool workflow."""
    for attempt in range(3):
        try:
            return operation()
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code
            temporary = status in (500, 502, 503, 504) or isinstance(
                exc, (APIConnectionError, httpx.TransportError))
            if not temporary:
                raise
            if attempt == 2:
                logger.warning("Gemini remains unavailable after 3 attempts; saved document data is retained")
                raise GeminiUnavailable("Gemini is temporarily unavailable. Please try again shortly; your document is saved.") from None
            delay = 2 ** (attempt + 1) + random.uniform(0, 1)
            logger.warning("Gemini is busy or unreachable; retrying in %.1fs | attempt %d/3", delay, attempt + 2)
            time.sleep(delay)


def model_id():
    return os.getenv("GEMINI_MODEL") or os.getenv("GEMINI_SUMMARY_MODEL", "gemini-3.5-flash-lite")

def api_key():
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        raise ValueError("Set GEMINI_API_KEY in .env")
    return key

def estimate_tokens(payload):
    # Conservative text estimate, NOT Gemini's exact tokenizer. Strip image data:
    # reserve separately for images instead of counting base64 as text.
    def clean(value):
        if isinstance(value, dict):
            if value.get("type") == "image_url":
                return {"image_token_reserve": "x" * 8192}
            return {k: clean(v) for k, v in value.items()}
        if isinstance(value, list):
            return [clean(v) for v in value]
        return value
    return len(json.dumps(clean(payload), ensure_ascii=False).encode("utf-8")) + 256

def reserve(tokens, model):
    limit = int(os.getenv("GEMINI_TPM_LIMIT", "240000"))
    rpm = int(os.getenv("GEMINI_RPM_LIMIT", "15"))
    timeout = float(os.getenv("GEMINI_QUEUE_TIMEOUT", "120"))
    if limit <= 0 or rpm <= 0 or timeout <= 0:
        raise ValueError("Gemini rate limits and timeout must be positive")
    if tokens > limit:
        raise RequestTooLarge("This request exceeds the Gemini input budget; use smaller document sections.")
    from app.services.postgres_state import state_pool, admit
    pool = state_pool()
    deadline = time.monotonic() + timeout
    while True:
        wait_seconds = admit(pool, model, tokens, limit, rpm)
        if wait_seconds is None:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("Timed out waiting for Gemini allowance. Please retry shortly.")
        wait = min(10, wait_seconds, max(.1, deadline - time.monotonic()))
        logger.info("Waiting for Gemini allowance | next check in %.0fs", wait)
        time.sleep(wait)

def complete(payload):
    payload = dict(payload)
    payload["model"] = payload.get("model") or model_id()
    payload.pop("stream", None)
    logger.debug("Gemini request | model: %s", payload["model"])
    with progress(logger, "Waiting for Gemini reply"):
        with OpenAI(api_key=api_key(), base_url=BASE_URL, max_retries=0, timeout=120) as client:
            def send():
                reserve(estimate_tokens(payload), payload["model"])
                return client.chat.completions.create(**payload)
            return retry_request(send)
