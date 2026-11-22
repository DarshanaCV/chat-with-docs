"""Exercise the actual tool body without loading GPU models or connecting to DB."""
import ast
import json
import uuid
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("query", [
    "Figure 6 Figure 7 batch profile GR00T Cosmos3",
    "What does the text say about Figure 2?",
    "Explain Figures 1-4",
])
def test_numbered_figure_text_search_does_not_run_vision(query):
    tree = ast.parse(Path("app/mcp_servers/document_server.py").read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "search_documents")
    function.decorator_list = []
    search = Mock(return_value={"documents": [["Supporting text"]], "metadatas": [[{}]], "distances": [[0.1]]})
    vision = Mock(side_effect=AssertionError("Text search must not call vision"))
    embed = Mock(return_value=[[0.1]])
    namespace = dict(
        json=json, uuid=uuid, logger=None, progress=lambda *a: nullcontext(),
        select_tool_documents=lambda *a: [{"document_id": "doc"}],
        list_session_documents=lambda *a: [], log_search_query=Mock(),
        log_search_results=Mock(), _ensure_processed=Mock(),
        create_embeddings=embed, search_chunks=search,
        rerank_chunks=Mock(return_value=[{"chunk": "Supporting text", "metadata": {}, "score": 1}]),
        _build_source=lambda *a: {"content": a[1]},
        _figure_payload=vision, _generate_description=vision,
    )
    exec(compile(ast.Module(body=[function], type_ignores=[]), "search_tool", "exec"), namespace)
    result = json.loads(namespace["search_documents"]("chat", query))
    assert result["context"] == "Supporting text"
    embed.assert_called_once_with([query])
    search.assert_called_once()
    vision.assert_not_called()
