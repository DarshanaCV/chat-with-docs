"""Readable search previews in the console, complete evidence in the DEBUG file."""
import json
import logging

logger = logging.getLogger(__name__)


def log_search_query(search_id, query, documents, include_visuals):
    logger.info('Search %s: "%s" | %s', search_id, query,
                ", ".join(d["filename"] for d in documents))
    logger.debug("SEARCH_QUERY %s %s", search_id, json.dumps({
        "query": query, "documents": documents, "include_visuals": include_visuals}, ensure_ascii=False))


def log_search_results(search_id, stage, passages):
    if stage == "returned":
        logger.info("Search %s: returning %d passages (previews below; full text in detailed log)", search_id, len(passages))
    else:
        logger.debug("Search %s | %s | %d passages", search_id, stage, len(passages))
    for index, passage in enumerate(passages, 1):
        meta = passage["metadata"]
        # One complete JSON record per passage; newline escapes keep log boundaries clear.
        logger.debug("SEARCH_TEXT %s %s %d %s", search_id, stage, index,
                     json.dumps(passage, ensure_ascii=False, default=str))
        if stage == "returned":
            preview = " ".join(passage["chunk"].split())
            if len(preview) > 300:
                preview = preview[:297] + "..."
            logger.info("Result %d | %s | page %s | %s", index, meta.get("filename"),
                        meta.get("page_number"), meta.get("content_type"),
                        extra={"console_preview": preview})
