"""Persistent preparation checkpoints. Commit readiness only after vector writes succeed."""
import logging
import threading

from app.services.summary_cache import SummaryCache

logger = logging.getLogger(__name__)
_lock = threading.RLock()


def prepare_document(document_id, lookup, collection, read_pages, index_pdf, index_image,
                     cache=None, include_visuals=False):
    cache = cache if cache is not None else SummaryCache()
    with _lock:
        state = cache.get("document-preparation-v1", document_id)
        pending = lookup(document_id)
        if state is None:
            existing = collection.get(where={"document_id": document_id}, include=["metadatas"])
            # Older documents already in Chroma must not be embedded again.
            state = {"text_ready": bool(existing["ids"]), "visuals_ready": False, "pages": None}
            if pending:
                state.update(pending)
            if state["text_ready"]:
                state["visuals_ready"] = any(m.get("content_type") in ("figure", "table")
                                             for m in existing["metadatas"])
        if not state.get("file_path") and pending:
            state.update(pending)
        if not state["text_ready"]:
            if not state.get("file_path"):
                raise ValueError("Document file is unavailable; please upload it again.")
            if state.get("content_type") == "image":
                index_image(document_id, state["file_path"], state["filename"])
                state["visuals_ready"] = True
            else:
                if state["pages"] is None:
                    state["pages"] = read_pages(state["file_path"])
                    cache.put("document-preparation-v1", document_id, state)
                index_pdf(document_id, state["file_path"], state["filename"], pages=state["pages"])
            state["text_ready"] = True
            cache.put("document-preparation-v1", document_id, state)
        else:
            logger.info("Reusing saved document embeddings | %s", document_id)
        if include_visuals and not state["visuals_ready"]:
            if not state.get("file_path"):
                raise ValueError("Original PDF needed for figure analysis; please upload this older document again.")
            index_pdf(document_id, state["file_path"], state["filename"],
                      pages=[], include_visuals=True, visuals_only=True)
            state["visuals_ready"] = True
        cache.put("document-preparation-v1", document_id, state)
        return state
