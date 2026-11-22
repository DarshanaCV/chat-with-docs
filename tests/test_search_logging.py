import logging
from app.services.search_logging import log_search_query, log_search_results


def test_search_log_preserves_full_query_and_evidence(caplog):
    text = "opening " + "long passage " * 100 + "END_OF_EVIDENCE"
    with caplog.at_level(logging.DEBUG):
        log_search_query("search1", "find conclusion", [{"document_id": "a", "filename": "paper.pdf"}], False)
        log_search_results("search1", "returned", [{"chunk": text, "metadata": {
            "filename": "paper.pdf", "page_number": 7, "content_type": "text"}, "score": 0.5}])
    assert "SEARCH_QUERY search1" in caplog.text
    assert "find conclusion" in caplog.text
    assert "END_OF_EVIDENCE" in caplog.text
    previews = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert any("page 7" in line for line in previews)
    assert not any("END_OF_EVIDENCE" in line for line in previews)
