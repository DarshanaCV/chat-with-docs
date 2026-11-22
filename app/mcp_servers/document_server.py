import logging
from typing import Literal

from app.logging_setup import configure_logging, progress
configure_logging("MCP")
logging.getLogger(__name__).info("Starting document tools; loading local models")

import json
import uuid
from app.services.search_logging import log_search_query, log_search_results

from mcp.server.fastmcp import FastMCP

from app.services.session_store import list_session_documents
from app.services.pending_documents import peek_pending_upload
from app.services.ingestion.pipeline import process_and_store_document, process_and_store_image
from app.services.ingestion.embeddings import create_embeddings
from app.services.ingestion.image_extractor import _generate_description
from app.services.vector_store import collection, search_chunks, rerank_chunks
from app.services.gemini_summary import summarize_with_gemini
from app.services.summary_input import read_summary_input
from app.services.summary_cache import SummaryCache
from app.services.document_scope import select_tool_documents, select_pages
from app.services.request_logging import logged_document_tool

logger=logging.getLogger("app.mcp_servers.document_server")
mcp=FastMCP("documents")

def _ensure_processed(document_ids, include_visuals=False):
    from app.services.document_preparation import prepare_document
    from app.services.ingestion.loader import read_pdf
    for document_id in document_ids:
        prepare_document(document_id, peek_pending_upload, collection, read_pdf,
                         process_and_store_document, process_and_store_image,
                         include_visuals=include_visuals)

def _build_source(metadata:dict,chunk:str,score:float)->dict:
    image_path=metadata.get("image_path") or None
    rows_json=metadata.get("rows_json")
    return{
        "chunk_id":metadata.get("chunk_id"),
        "content_type":metadata.get("content_type"),
        "filename":metadata.get("filename"),
        "page_number":metadata.get("page_number"),
        "score":float(score),
        "content":chunk,
        "table_number":metadata.get("table_number"),
        "figure_number":metadata.get("figure_number"),
        "caption":metadata.get("caption") or None,
        "image_path":image_path,
        "image_url": f"/{image_path.replace(chr(92), '/')}" if image_path else None,
        "rows":json.loads(rows_json) if rows_json else None
    }

def _figure_payload(document_ids, numbers, question):
    from app.services.figure_answer import explain_indexed_figures
    from app.services.ingestion.loader import read_pdf
    from app.services.ingestion.image_extractor import crop_numbered_figure
    _ensure_processed(document_ids)
    sources, reports = [], []
    for document_id in document_ids:
        figures, report = explain_indexed_figures(document_id, numbers, question,
            SummaryCache(), read_pdf, crop_numbered_figure, _generate_description)
        reports.append(dict(report, document_id=document_id))
        for figure in figures:
            sources.append(dict(_build_source(figure, figure["content"], 1.0), source_id=f"S{len(sources)+1}"))
    context = "Coverage report (report unavailable figures; do not invent missing explanations):\n" + json.dumps(reports)
    context += "\n\n" + "\n\n".join(f"[{s['source_id']}] Figure {s['figure_number']} ({s['filename']}, page {s['page_number']}): {s['content']}" for s in sources)
    return json.dumps({"context": context, "sources": sources, "coverage": reports}, ensure_ascii=False)


@mcp.tool()
@logged_document_tool
def get_document_index(session_id: str, document_id: str | None = None,
                       filename: str | None = None, all_documents: bool = False,
                       category: Literal["overview", "figures", "tables", "headings"] = "overview",
                       offset: int = 0, limit: int = 30) -> str:
    """Read a compact, paginated index. Choose figures for figure lists, tables for table lists,
    headings to locate sections; overview returns counts only. Follow next_offset until null.
    Each next_offset belongs to its document_id; specify that ID on pagination calls.
    Returns recognized captions/page numbers, without image coordinates.
    Use to locate a section such as references, or discover figures without guessing numbers.
    Does not analyze images. Heading candidates need verification using read_document_pages.
    """
    from app.services.document_index import indexed_state, inventory
    from app.services.ingestion.loader import read_pdf
    attached = select_tool_documents(list_session_documents(session_id), document_id, filename, all_documents)
    _ensure_processed([d["document_id"] for d in attached])
    indexes = [dict(inventory(indexed_state(d["document_id"], SummaryCache(), read_pdf), category, offset, limit), document_id=d["document_id"]) for d in attached]
    return json.dumps({"context": json.dumps(indexes, ensure_ascii=False), "sources": []}, ensure_ascii=False)


@mcp.tool()
@logged_document_tool
def read_document_section(session_id: str, section: str, document_id: str | None = None,
                          filename: str | None = None, all_documents: bool = False, offset: int = 0) -> str:
    """Find a named section independently in each PDF, e.g. Conclusion or Methods.
    Prefer this over semantic search for named sections. Follow next_offset for more text.
    If missing/ambiguous, use the heading index or targeted search; never guess page numbers.
    """
    from app.services.document_index import indexed_state, section_sources
    from app.services.ingestion.loader import read_pdf
    attached = select_tool_documents(list_session_documents(session_id), document_id, filename, all_documents)
    _ensure_processed([d["document_id"] for d in attached])
    sources, reports = [], []
    for d in attached:
        state = indexed_state(d["document_id"], SummaryCache(), read_pdf)
        found, report = section_sources(d["document_id"], state, section, offset)
        reports.append(dict(report, document_id=d["document_id"]))
        base = len(sources)
        sources.extend(dict(item, source_id=f"S{base+i+1}") for i, item in enumerate(found))
    return json.dumps({"context": json.dumps(reports) + "\n" + "\n\n".join(
        f"[{s['source_id']}] {s['filename']} page {s['page_number']}:\n{s['content']}" for s in sources),
        "sources": sources}, ensure_ascii=False)


@mcp.tool()
@logged_document_tool
def read_document_pages(session_id: str, page_start: int, page_end: int,
                        document_id: str | None = None, filename: str | None = None,
                        all_documents: bool = False) -> str:
    """Read original saved page text, without summarizing or omitting reference entries.
    Use for complete sections, authors, tables in text, or verifying heading candidates.
    Follow next_page when returned. Do not claim full coverage if pages remain unread.
    """
    from app.services.document_index import indexed_state, page_sources
    from app.services.ingestion.loader import read_pdf
    attached = select_tool_documents(list_session_documents(session_id), document_id, filename, all_documents)
    _ensure_processed([d["document_id"] for d in attached])
    sources, reports = [], []
    for d in attached:
        state = indexed_state(d["document_id"], SummaryCache(), read_pdf)
        pages, next_page = page_sources(d["document_id"], state, page_start, page_end)
        reports.append({"document_id": d["document_id"], "next_page": next_page, "requested_end": page_end})
        for page in pages:
            sources.append(dict(page, source_id=f"S{len(sources)+1}"))
    return json.dumps({"context": "Page coverage: " + json.dumps(reports) + "\n\n" + "\n\n".join(
        f"[{s['source_id']}] {s['filename']} page {s['page_number']}:\n{s['content']}" for s in sources), "sources": sources}, ensure_ascii=False)


@mcp.tool()
@logged_document_tool
def explain_document_figures(session_id: str, question: str, figure_numbers: list[str] | None = None,
                             document_id: str | None = None, filename: str | None = None,
                             all_documents: bool = False) -> str:
    """Explain figures from the saved inventory. Omit figure_numbers to explain ALL recognized figures.
    For selected figures pass printed labels, e.g. ["2", "S1"]. Pass the user's full question.
    Returns per-figure coverage; report failures rather than retrying other guessed numbers.
    """
    attached = select_tool_documents(list_session_documents(session_id), document_id, filename, all_documents)
    return _figure_payload([d["document_id"] for d in attached], figure_numbers, question)


@mcp.tool()
@logged_document_tool
def search_documents(session_id: str, query: str, document_id: str | None = None,
                     filename: str | None = None, all_documents: bool = False,
                     include_visuals: bool = False) -> str:
    """Search the documents attached to this chat session for content relevant to a specific
    question, fact, or topic, including text discussing numbered figures.
    This tool searches text only. Use explain_document_figures to analyze images.
    Select document_id for the requested file; required when multiple files are attached. Use all_documents
    only for explicit cross-document questions. Set include_visuals=True only when
    answering requires figures/charts/table layout rather than ordinary page text.
    Do NOT use this for summarize/overview requests — use get_document_overview instead.
    """
    attached = select_tool_documents(list_session_documents(session_id), document_id, filename, all_documents)
    document_ids = [d["document_id"] for d in attached]
    if not document_ids:
        return "No documents are attached to this session"

    search_id = uuid.uuid4().hex[:8]
    log_search_query(search_id, query, attached, include_visuals)

    if include_visuals:
        # Broad visual searches cannot silently scan everything and return top-three.
        return json.dumps({"context": "For visual questions use get_document_index to select figures or tables. Use explain_document_figures for all figures or a selected list. Use read_document_pages for table text. No visual scan was run.", "sources": []})

    _ensure_processed(document_ids, include_visuals=include_visuals)

    with progress(logger, "Searching for relevant passages"):
        embedding=create_embeddings([query])
        chunks=search_chunks(embedding,document_ids,n_results=20)
    log_search_results(search_id, "candidates", [
        {"chunk": text, "metadata": meta, "distance": distance}
        for text, meta, distance in zip(chunks["documents"][0], chunks["metadatas"][0], chunks["distances"][0])])
    if not chunks['documents'][0]:
        return "No relevant content found in the attached documents."

    with progress(logger, "Choosing the best matching passages"):
        reranked=rerank_chunks(query,chunks['documents'][0],chunks['metadatas'][0],top_k=3)
    log_search_results(search_id, "reranked", reranked)
    if not reranked:
        return "No relevant content found in the attached documents."

    log_search_results(search_id, "returned", reranked)
    payload={
        "context":"\n\n".join(r['chunk'] for r in reranked),
        "sources":[_build_source(r['metadata'],r['chunk'],r['score']) for r in reranked]
    }
    return json.dumps(payload)


@mcp.tool()
@logged_document_tool
def get_document_overview(session_id: str, document_id: str | None = None,
                          filename: str | None = None, page_start: int | None = None,
                          page_end: int | None = None, all_documents: bool = False) -> str:
    """Get comprehensive content covering an ENTIRE attached document.
    Use this for 'summarize', 'overview', or 'what is this document about' requests.
    For multiple attached files, explicitly select the requested document. Pass an exact filename or
    document_id for an older document. Set all_documents only when explicitly asked.
    page_start/page_end are inclusive, starting at 1; omit for the whole document.
    """
    attached = select_tool_documents(list_session_documents(session_id), document_id, filename, all_documents)
    document_ids = [d["document_id"] for d in attached]
    if not document_ids:
        return "No documents are attached to this session"

    select_pages([], [], page_start, page_end)  # Validate before expensive processing.
    logger.info("Summary requested | %s | pages %s to %s",
                ", ".join(d["filename"] for d in attached), page_start or 1, page_end or "end")

    _ensure_processed(document_ids)
    with progress(logger, "Reading document text for summary"):
        docs, metadatas = read_summary_input(attached, peek_pending_upload, collection, page_start, page_end)
        docs, metadatas = select_pages(docs, metadatas, page_start, page_end)
    if not docs:
        return "No relevant content found in the attached documents."

    sources=[_build_source(m,d,1.0) for d,m in zip(docs,metadatas)]
    overview = summarize_with_gemini(docs, metadatas, cache=SummaryCache())
    overview += "\n\n*Summary based on extracted text; image contents were not analyzed.*"
    # Only carry evidence cited by the synthesized overview. Stable labels survive
    # filtering; API clients also receive the source_id in each source record.
    import re
    cited = set(re.findall(r"\[(S\d+)\]", overview))
    selected = [dict(source, source_id=f"S{i}")
                for i, source in enumerate(sources, start=1) if f"S{i}" in cited]
    return json.dumps({"context": overview, "sources": selected}, ensure_ascii=False)

if __name__=="__main__":
    from app.services.db import pool
    try:
        mcp.run(transport="stdio")
    finally:
        pool.close()
