"""Read text for summaries without running layout models or embedding models."""
from pathlib import Path

import pymupdf
from app.services.summary_cache import SummaryCache


def read_summary_input(attached, lookup_pending, collection, page_start=None, page_end=None):
    docs, metadata = [], []
    for document in attached:
        document_id = document["document_id"]
        cache = SummaryCache()
        saved = cache.get("canonical-summary-input-v1", document_id)
        if saved is not None:
            docs.extend(saved[0])
            metadata.extend(saved[1])
            continue
        state = cache.get("document-preparation-v1", document_id)
        if state and state.get("pages") is not None:
            page_docs = [p["text"] for p in state["pages"]]
            if any(not text.strip() for text in page_docs):
                raise ValueError("Some PDF pages have no readable text; OCR is needed for a full summary")
            page_meta = [{"document_id": document_id, "filename": document["filename"],
                          "page_number": p["page_number"], "content_type": "text",
                          "chunk_id": f"{document_id}:page:{p['page_number']}"} for p in state["pages"]]
            cache.put("canonical-summary-input-v1", document_id, [page_docs, page_meta])
            docs.extend(page_docs)
            metadata.extend(page_meta)
            continue
        pending = lookup_pending(document_id)
        if pending and Path(pending["file_path"]).suffix.lower() == ".pdf":
            with pymupdf.open(pending["file_path"]) as pdf:
                for index, page in enumerate(pdf, 1):
                    if (page_start and index < page_start) or (page_end and index > page_end):
                        continue
                    text = page.get_text(sort=True).strip()
                    if not text:
                        raise ValueError(f"Page {index} has no readable text. OCR or PDF vision is needed before summarizing it.")
                    docs.append(text)
                    metadata.append({"document_id": document_id, "filename": document["filename"],
                                     "page_number": index, "content_type": "text",
                                     "chunk_id": f"{document_id}:page:{index}"})
        else:
            result = collection.get(where={"document_id": document_id}, include=["documents", "metadatas"])
            if not result["documents"]:
                raise ValueError("No readable document text is available. This summary path requires a text PDF or an indexed document.")
            docs.extend(result["documents"])
            metadata.extend(result["metadatas"])
            cache.put("canonical-summary-input-v1", document_id, [result["documents"], result["metadatas"]])
    from app.services.document_scope import select_pages
    return select_pages(docs, metadata, page_start, page_end)
