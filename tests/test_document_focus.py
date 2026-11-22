import asyncio
import json
import pytest
from langchain_core.tools import tool
from app.services.document_focus import resolve_focus, ClarifyDocument, scoped_tools, request_scope

DOCS = [{"document_id": c, "filename": f"{c}.pdf"} for c in "ABC"]


@pytest.mark.parametrize("question,focus,expected", [
    ("Explain figure 1 from this paper", ["C"], ["C"]),
    ("summarize the first PDF", ["C"], ["A"]),
    ("previous PDF", ["C"], ["B"]),
    ("compare A.pdf and C.pdf", ["B"], ["A", "C"]),
    ("summarize both papers", ["A", "C"], ["A", "C"]),
    ("compare all uploaded PDFs", ["A"], ["A", "B", "C"]),
    ("compare the first and second papers", ["C"], ["A", "B"]),
    ("summarize document 2", ["C"], ["B"]),
    ("compare them", ["A", "B"], ["A", "B"]),
])
def test_focus_rules(question, focus, expected):
    assert resolve_focus(question, DOCS, focus) == expected


@pytest.mark.parametrize("question,focus", [
    ("Explain figure 1 from this paper", ["A", "B"]),
    ("previous PDF", ["A"]), ("previous PDF", ["A", "B"]),
    ("compare documents", ["C"]), ("this PDF", []),
])
def test_ambiguity_stops_before_tools(question, focus):
    with pytest.raises(ClarifyDocument):
        resolve_focus(question, DOCS, focus)


@pytest.mark.parametrize("tool_name", ["search_documents", "explain_document_figures"])
def test_backend_overrides_wrong_model_selection_and_scopes_comparison(tool_name):
    calls = []
    @tool(tool_name)
    async def search_documents(session_id: str, query: str, document_id: str | None = None,
                               filename: str | None = None, all_documents: bool = False) -> str:
        """Search a scoped document."""
        calls.append((session_id, document_id, all_documents))
        return json.dumps({"context": f"{document_id} fact [S1]", "sources": [{"source_id": "S1"}]})
    wrapped = scoped_tools([search_documents])[0]
    async def run():
        token = request_scope.set(("my-chat", ["B", "C"]))
        try:
            return await wrapped.ainvoke({"session_id": "wrong-chat", "query": "facts",
                                          "document_id": "A", "all_documents": True})
        finally:
            request_scope.reset(token)
    result = json.loads(asyncio.run(run()))
    assert calls == [("my-chat", "B", False), ("my-chat", "C", False)]
    assert "B fact [S1]" in result["context"] and "C fact [S2]" in result["context"]


@pytest.mark.parametrize("question", [
    "Explain Figure 2, including both parts",
    "Compare Figures 6 and 7",
    "Compare the first and second figures",
    "Explain both panels",
    "Compare these tables",
    "Explain the previous figure",
])
@pytest.mark.parametrize("attached", [DOCS, DOCS[:2], DOCS[:1]])
def test_content_references_keep_current_document(question, attached):
    current = attached[-1]["document_id"]
    assert resolve_focus(question, attached, [current]) == [current]


def test_content_comparison_with_multiple_focused_documents_needs_selection():
    with pytest.raises(ClarifyDocument):
        resolve_focus("Compare Figures 6 and 7", DOCS, ["A", "B"])


def test_explicit_multiple_papers_still_selects_both():
    assert resolve_focus("Compare Figure 2 in both papers", DOCS[:2], ["B"]) == ["A", "B"]


def test_page_read_selects_only_requested_pdf_inside_scope():
    calls = []
    @tool("read_document_pages")
    async def read_pages(session_id: str, document_id: str | None = None,
                         filename: str | None = None, all_documents: bool = False) -> str:
        """Read pages for a PDF."""
        calls.append(document_id)
        return "{}"
    wrapped = scoped_tools([read_pages])[0]
    async def run():
        token = request_scope.set(("chat", ["A", "B"]))
        try:
            await wrapped.ainvoke({"session_id": "chat", "document_id": "B"})
            with pytest.raises(ValueError):
                await wrapped.ainvoke({"session_id": "chat", "document_id": "C"})
            with pytest.raises(ValueError):
                await wrapped.ainvoke({"session_id": "chat"})
        finally:
            request_scope.reset(token)
    asyncio.run(run())
    assert calls == ["B"]


@pytest.mark.parametrize("tool_name", ["get_document_index", "read_document_section"])
def test_pagination_is_independent_for_different_length_documents(tool_name):
    from app.services.document_index import inventory
    calls = []
    documents = {key: {"filename": key + ".pdf", "pages": [{"page_number": 1,
        "heading_candidates": [{"title": f"Section {i}"} for i in range(count)]}]}
        for key, count in [("A", 65), ("B", 2)]}
    @tool(tool_name)
    async def lookup(session_id: str, document_id: str | None = None, offset: int = 0,
                     filename: str | None = None, all_documents: bool = False) -> str:
        """Look up paginated document entries."""
        calls.append((document_id, offset))
        return json.dumps({"context": json.dumps(inventory(documents[document_id], "headings", offset)), "sources": []})
    wrapped = scoped_tools([lookup])[0]
    async def run():
        token = request_scope.set(("chat", ["A", "B"]))
        try:
            await wrapped.ainvoke({"session_id": "chat"})
            assert calls == [("A", 0), ("B", 0)]
            warning = json.loads(await wrapped.ainvoke({"session_id": "chat", "offset": 30}))
            assert "document_id" in warning["context"]
            assert len(calls) == 2
            result = json.loads(await wrapped.ainvoke({"session_id": "chat", "document_id": "A", "offset": 30}))
            assert json.loads(result["context"])["next_offset"] == 60
            assert calls[-1] == ("A", 30)
            exhausted = json.loads(await wrapped.ainvoke({"session_id": "chat", "document_id": "B", "offset": 30}))
            assert json.loads(exhausted["context"])["next_offset"] is None
            with pytest.raises(ValueError):
                await wrapped.ainvoke({"session_id": "chat", "document_id": "C", "offset": 30})
        finally:
            request_scope.reset(token)
    asyncio.run(run())
