"""Model context and Gemini tool-call tests."""
import json
from types import SimpleNamespace
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from app.services.model_context import compact_tool_message


@pytest.mark.parametrize("blocks", [False, True])
def test_full_sources_survive_but_do_not_reach_model(blocks):
    payload = json.dumps({"context": "Overview [S15]", "sources": [
        {"source_id": "S15", "filename": "paper.pdf", "page_number": 4,
         "content": "huge excerpt " * 1000, "rows": [["table data"]]}]})
    content = [{"type": "text", "text": payload}] if blocks else payload
    original = ToolMessage(content=content, tool_call_id="call1")
    compact = compact_tool_message(original)
    text = compact.content[0]["text"] if blocks else compact.content
    assert "huge excerpt" not in text and "table data" not in text
    assert json.loads(text)["sources"][0]["source_id"] == "S15"
    assert original.content == content
    assert compact.tool_call_id == "call1"


def test_trim_keeps_current_tool_pair_and_removes_old_evidence(monkeypatch):
    from app.services.agent import _trim_messages
    old = ToolMessage(content="old PDF", tool_call_id="old")
    current = ToolMessage(content="new PDF", tool_call_id="new")
    messages = [HumanMessage(content="old question"),
                AIMessage(content="", tool_calls=[{"id": "old", "name": "search", "args": {}}]),
                old, AIMessage(content="old answer"), HumanMessage(content="new question"),
                AIMessage(content="", tool_calls=[{"id": "new", "name": "search", "args": {}}]), current]
    filtered = _trim_messages({"messages": messages})
    assert old not in filtered
    assert filtered[-1] == current
    assert filtered[-2].tool_calls[0]["id"] == "new"


@pytest.mark.parametrize("status", [413, 429])
def test_gemini_provider_errors_are_not_retried(monkeypatch, status):
    from app.services import gemini_gateway as gemini
    attempts = []
    reservations = []
    monkeypatch.setattr(gemini, "reserve", lambda *a: reservations.append(a))
    monkeypatch.setattr(gemini, "api_key", lambda: "test")
    class ProviderError(Exception):
        status_code = status
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["base_url"] == gemini.BASE_URL
            assert kwargs["max_retries"] == 0
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def create(self, **kwargs):
            attempts.append(1)
            raise ProviderError()
    monkeypatch.setattr(gemini, "OpenAI", Client)
    with pytest.raises(ProviderError):
        gemini.complete({"messages": []})
    assert len(attempts) == len(reservations) == 1


def test_langchain_tool_call_response_and_payload(monkeypatch):
    from app.services import queued_model
    from app.services.agent import model
    captured = []
    response = {"id": "test", "model": "openai/gpt-oss-120b", "object": "chat.completion", "created": 1,
                "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
                    "role": "assistant", "content": None, "tool_calls": [{"id": "call1", "type": "function",
                    "function": {"name": "search_documents", "arguments": '{"query":"test"}'}}]}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
    monkeypatch.setattr(queued_model, "complete", lambda payload: captured.append(payload) or
                        SimpleNamespace(model_dump=lambda: response))
    result = model._generate([HumanMessage(content="Find a fact")], tools=[{
        "type": "function", "function": {"name": "search_documents", "parameters": {"type": "object"}}}])
    assert captured[0]["tools"][0]["function"]["name"] == "search_documents"
    assert result.generations[0].message.tool_calls[0]["id"] == "call1"


def test_graph_round_trip_keeps_ui_sources_out_of_model(monkeypatch):
    import asyncio
    from langchain_core.tools import tool
    from app.services import agent, queued_model
    @tool
    def overview() -> str:
        """Return a document overview."""
        return json.dumps({"context": "A finding [S1]", "sources": [{
            "source_id": "S1", "filename": "paper.pdf", "page_number": 1,
            "content": "PRIVATE_FULL_SOURCE_TEXT"}]})
    calls = []
    def complete(payload):
        calls.append(payload)
        if len(calls) == 1:
            message = {"role": "assistant", "content": None, "tool_calls": [{
                "id": "call1", "type": "function", "function": {"name": "overview", "arguments": "{}"}}]}
            finish = "tool_calls"
        else:
            assert "PRIVATE_FULL_SOURCE_TEXT" not in json.dumps(payload)
            message = {"role": "assistant", "content": "A finding [S1]"}
            finish = "stop"
        response = {"id": "test", "object": "chat.completion", "created": 1,
                    "model": "openai/gpt-oss-120b", "choices": [{"index": 0,
                    "message": message, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}
        return SimpleNamespace(model_dump=lambda: response)
    monkeypatch.setattr(queued_model, "complete", complete)
    monkeypatch.setattr(agent, "estimate_tokens", lambda payload: 100)
    graph = agent.build_agent([overview], None)
    result = asyncio.run(graph.ainvoke({"messages": [HumanMessage(content="Summarize")]}))
    assert len(calls) == 2
    assert result["messages"][-1].content == "A finding [S1]"
    tool_message = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert "PRIVATE_FULL_SOURCE_TEXT" in tool_message.content

