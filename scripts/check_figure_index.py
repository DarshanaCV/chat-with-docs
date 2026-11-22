"""Live Gemini regression: real PDF index, no upload or embedding/database writes."""
import asyncio
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
from typing import Literal
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage
from app.services.ingestion.loader import read_pdf
from app.services.document_index import inventory
from app.services.document_focus import request_scope
from app.services.agent import build_agent, SYSTEM_PROMPT

async def main():
    path = Path(__file__).resolve().parents[1] / "Research Papers" / "Real-Time Physical AI at the Edge, Scalable Rollouts in the Cloud.pdf"
    state = {"filename": path.name, "pages": read_pdf(path)}
    calls = []
    @tool
    def get_document_index(session_id: str, document_id: str | None = None,
                           filename: str | None = None, all_documents: bool = False,
                           category: Literal["overview", "figures", "tables", "headings"] = "overview",
                           offset: int = 0, limit: int = 30) -> str:
        """Read a compact index. Choose figures to list figure labels/pages, headings for sections,
        or tables for table labels/pages. Overview returns counts only. Follow next_offset until null."""
        result = inventory(state, category, offset, limit)
        calls.append((category, len(json.dumps(result).encode())))
        return json.dumps({"context": json.dumps(result), "sources": []})
    agent = build_agent([get_document_index], None)
    token = request_scope.set(("index-check", ["local-paper"]))
    try:
        result = await agent.ainvoke({"messages": [
            SystemMessage(content=SYSTEM_PROMPT.format(session_id="index-check") + "\nSelected document: local-paper"),
            HumanMessage(content="List all figures and their page numbers.")]}, {"recursion_limit": 10})
    finally:
        request_scope.reset(token)
    assert any(c[0] == "figures" for c in calls), "Did not retrieve figures"
    answer = result["messages"][-1].content
    target = Path(__file__).resolve().parents[1] / "logs" / "figure-index-check.md"
    target.write_text(answer, encoding="utf-8")
    print("PASS: live Gemini inventory lookup and final answer; calls:", calls)
    print("Answer saved to", target)

if __name__ == "__main__":
    asyncio.run(main())
