from app.services import pending_documents as pd


def test_register_then_pop_returns_and_clears_the_entry():
    pd.register_upload("doc-register-pop", "path/to/file.pdf", "file.pdf", content_type="document")

    result = pd.pop_pending_upload("doc-register-pop")
    assert result == {"file_path": "path/to/file.pdf", "filename": "file.pdf", "content_type": "document"}

    # Popped once — a second question about the same document shouldn't
    # re-trigger processing.
    assert pd.pop_pending_upload("doc-register-pop") is None


def test_pop_unknown_document_returns_none():
    assert pd.pop_pending_upload("doc-does-not-exist") is None


def test_content_type_defaults_to_document():
    pd.register_upload("doc-default-type", "p", "f.pdf")
    assert pd.pop_pending_upload("doc-default-type")["content_type"] == "document"


def test_discard_removes_a_pending_upload_without_processing_it():
    pd.register_upload("doc-discard", "p", "f.pdf")
    pd.discard_document("doc-discard")
    assert pd.pop_pending_upload("doc-discard") is None


def test_discard_unknown_document_is_a_safe_no_op():
    pd.discard_document("doc-was-never-registered")  # must not raise
