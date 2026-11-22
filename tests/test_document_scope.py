"""Document access and MCP connection tests."""
import asyncio
import io
import sys
import pytest
from mcp import StdioServerParameters
from app.services.document_connection import document_session
from app.services.document_scope import select_documents, select_pages


def test_document_selection_and_access_boundary():
    attached = [{"document_id": "old", "filename": "large.pdf"},
                {"document_id": "new", "filename": "small.pdf"}]
    assert select_documents(attached) == attached[1:]
    assert select_documents(attached, filename="large.pdf") == attached[:1]
    assert select_documents(attached, all_documents=True) == attached
    with pytest.raises(ValueError):
        select_documents(attached, document_id="another-chats-document")
    with pytest.raises(ValueError):
        select_documents(attached, document_id="new", all_documents=True)


def test_page_selection_and_stable_source_order():
    docs = ["four", "one", "three", "unknown"]
    metas = [{"page_number": page} for page in (4, 1, 3, None)]
    selected, _ = select_pages(docs, metas, 1, 3)
    assert selected == ["one", "three"]
    assert select_pages(docs, metas, 5, 6) == ([], [])
    with pytest.raises(ValueError):
        select_pages(docs, metas, 0, 3)
    with pytest.raises(ValueError):
        select_pages(docs, metas, 3, 1)


def test_persistent_mcp_connection_relays_progress_and_exits_once(tmp_path):
    server = tmp_path / "server.py"
    server.write_text('''
import sys
from mcp.server.fastmcp import FastMCP
mcp = FastMCP("test")
@mcp.tool()
def ping() -> str:
    print("Summarizing section 2/5", file=sys.stderr, flush=True)
    return "ok"
mcp.run(transport="stdio")
''', encoding="utf-8")
    output = io.StringIO()
    async def run():
        async with document_session(StdioServerParameters(command=sys.executable, args=[str(server)]), output) as session:
            assert (await session.call_tool("ping")).content[0].text == "ok"
            assert (await session.call_tool("ping")).content[0].text == "ok"
    asyncio.run(run())
    assert output.getvalue().count("Summarizing section 2/5") == 2
