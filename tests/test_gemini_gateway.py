import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from openai.types.chat import ChatCompletion
from app.services import queued_model, gemini_gateway


def test_tool_signature_survives_followup(monkeypatch):
    calls = []
    def complete(payload):
        calls.append(payload)
        message = {"role": "assistant", "content": "Done"}
        if len(calls) == 1:
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": "{}"},
                "extra_content": {"google": {"thought_signature": "signed-value"}},
            }]}
        return ChatCompletion.model_validate({"id": "test", "object": "chat.completion", "created": 0,
            "model": "gemini-test", "choices": [{"index": 0, "finish_reason": "stop", "message": message}]})
    monkeypatch.setattr(queued_model, "complete", complete)
    model = queued_model.QueuedChatOpenAI(model="gemini-test", api_key="test", base_url=gemini_gateway.BASE_URL)
    question = HumanMessage(content="Look it up")
    answer = model.invoke([question])
    model.invoke([question, answer, ToolMessage(content="found", tool_call_id="call_1")])
    assert calls[1]["messages"][1]["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "signed-value"


def test_oversized_request_fails_before_waiting(monkeypatch):
    monkeypatch.setenv("GEMINI_TPM_LIMIT", "100")
    with pytest.raises(gemini_gateway.RequestTooLarge):
        gemini_gateway.reserve(101, "test")


def test_image_data_is_not_counted_as_text():
    def payload(size):
        return {"messages": [{"content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64," + "a" * size}}]}]}
    assert gemini_gateway.estimate_tokens(payload(100)) == gemini_gateway.estimate_tokens(payload(100000))


def test_shared_budget_waits_until_window_expires(monkeypatch):
    from app.services import postgres_state
    now = [1000.0]
    waits = []
    monkeypatch.setattr(postgres_state, "state_pool", lambda: object())
    monkeypatch.setattr(postgres_state, "admit", lambda *a: max(0, 1060-now[0]) or None)
    monkeypatch.setattr(gemini_gateway.time, "monotonic", lambda: now[0])
    def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds
    monkeypatch.setattr(gemini_gateway.time, "sleep", sleep)
    gemini_gateway.reserve(10, "test")
    assert sum(waits) == 60


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_temporary_failure_retries_then_succeeds(monkeypatch, status):
    import httpx
    from unittest.mock import Mock
    response = httpx.Response(status, request=httpx.Request("POST", "https://example.test"))
    error = httpx.HTTPStatusError("busy", request=response.request, response=response)
    operation = Mock(side_effect=[error, error, "answer"])
    sleep = Mock()
    monkeypatch.setattr(gemini_gateway.time, "sleep", sleep)
    assert gemini_gateway.retry_request(operation) == "answer"
    assert operation.call_count == 3
    assert sleep.call_count == 2


def test_retries_stop_with_safe_error(monkeypatch):
    import httpx
    from unittest.mock import Mock
    operation = Mock(side_effect=httpx.ConnectError("private transport detail"))
    monkeypatch.setattr(gemini_gateway.time, "sleep", Mock())
    with pytest.raises(gemini_gateway.GeminiUnavailable, match="temporarily unavailable"):
        gemini_gateway.retry_request(operation)
    assert operation.call_count == 3


@pytest.mark.parametrize("status", [400, 401, 403, 429])
def test_permanent_or_quota_error_is_not_retried(monkeypatch, status):
    import httpx
    from unittest.mock import Mock
    response = httpx.Response(status, request=httpx.Request("POST", "https://example.test"))
    error = httpx.HTTPStatusError("failed", request=response.request, response=response)
    operation = Mock(side_effect=error)
    sleep = Mock()
    monkeypatch.setattr(gemini_gateway.time, "sleep", sleep)
    with pytest.raises(httpx.HTTPStatusError):
        gemini_gateway.retry_request(operation)
    assert operation.call_count == 1
    sleep.assert_not_called()


def test_summary_retry_reserves_each_attempt(monkeypatch):
    import httpx
    from unittest.mock import Mock
    from app.services.gemini_summary import GeminiSummary
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    request = httpx.Request("POST", "https://example.test")
    post = Mock(side_effect=[httpx.Response(503, request=request),
                            httpx.Response(200, request=request, json={"ok": True})])
    reserve = Mock()
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(gemini_gateway, "reserve", reserve)
    monkeypatch.setattr(gemini_gateway.time, "sleep", Mock())
    body = {"contents": [], "_reserved_tokens": 100}
    assert GeminiSummary().request("generateContent", body) == {"ok": True}
    assert reserve.call_count == 2
    assert body["_reserved_tokens"] == 100
