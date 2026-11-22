"""Run the real graph with a fake model; no provider or database required."""
import ast
import os
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage, BaseMessage
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.utils.function_calling import convert_to_openai_tool
from langchain_openai.chat_models.base import _convert_message_to_dict
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import create_react_agent

from app.services.model_context import compact_tool_message, bound_tool_evidence
from app.services.gemini_gateway import estimate_tokens, RequestTooLarge


def test_existing_failed_chat_can_accept_another_question():
    # Load actual builder/cleanup without module-level provider initialization.
    tree = ast.parse(Path("app/services/agent.py").read_text())
    functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name in ("_trim_messages", "build_agent")]
    namespace = dict(globals(), _MAX_HISTORY_TURNS=3,
        model=FakeMessagesListChatModel(responses=[AIMessage(content="Recovered answer")]))
    exec(compile(ast.Module(body=functions, type_ignores=[]), "agent_functions", "exec"), namespace)
    graph = namespace["build_agent"]([], InMemorySaver())
    config = {"configurable": {"thread_id": "existing-chat"}}
    failed = AIMessage(content="", tool_calls=[{"name": "get_document_index",
        "args": {"offset": 240}, "id": "unfinished", "type": "tool_call"}])
    graph.update_state(config, {"messages": [HumanMessage(content="Old question"), failed]})
    result = graph.invoke({"messages": [HumanMessage(content="Try again")]}, config)
    assert result["messages"][-1].content == "Recovered answer"
    # Recovery changes model input, not the original audit history.
    assert any(isinstance(m, AIMessage) and m.tool_calls for m in result["messages"])


def test_cleanup_keeps_current_turn_tool_results():
    tree = ast.parse(Path("app/services/agent.py").read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_trim_messages")
    namespace = dict(globals(), _MAX_HISTORY_TURNS=3)
    exec(compile(ast.Module(body=[function], type_ignores=[]), "cleanup", "exec"), namespace)
    call = AIMessage(content="", tool_calls=[{"name": "lookup", "args": {}, "id": "current", "type": "tool_call"}])
    result = namespace["_trim_messages"]({"messages": [HumanMessage(content="Question"), call,
        ToolMessage(content="Evidence", tool_call_id="current")]})
    assert result[1].tool_calls[0]["id"] == result[2].tool_call_id
