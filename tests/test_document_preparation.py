from types import SimpleNamespace
import pytest
from app.services.document_preparation import prepare_document
from app.services import summary_input
from app.services.document_scope import select_tool_documents


def test_multiple_files_require_explicit_selection():
    attached = [{"document_id": "first", "filename": "paper.pdf"},
                {"document_id": "second", "filename": "job.pdf"}]
    with pytest.raises(ValueError, match="Multiple documents"):
        select_tool_documents(attached)
    assert select_tool_documents(attached, document_id="first") == attached[:1]
    with pytest.raises(ValueError):
        select_tool_documents(attached, document_id="another-chat")


class Cache:
    def __init__(self):
        self.data = {}
    def get(self, kind, key):
        import copy
        return copy.deepcopy(self.data.get((kind, key)))
    def put(self, kind, key, value):
        import copy
        self.data[kind, key] = copy.deepcopy(value)


def test_summary_then_search_reuses_embeddings_and_visuals_are_lazy(monkeypatch):
    cache = Cache()
    calls = []
    collection = SimpleNamespace(get=lambda **kw: {"ids": [], "metadatas": []})
    pending = lambda _: {"file_path": "a.pdf", "filename": "a.pdf", "content_type": "document"}
    read = lambda _: [{"page_number": 1, "text": "Conclusion: findings"}]
    index = lambda *args, **kw: calls.append(kw)
    def run(visuals=False):
        return prepare_document("a", pending, collection, read, index, None, cache, visuals)
    run()
    monkeypatch.setattr(summary_input, "SummaryCache", lambda: cache)
    attached = [{"document_id": "a", "filename": "a.pdf"}]
    first = summary_input.read_summary_input(attached, pending, collection)
    run()
    assert len(calls) == 1 and "include_visuals" not in calls[0]
    run(True)
    run(True)
    assert len(calls) == 2 and calls[1]["visuals_only"]
    assert first == summary_input.read_summary_input(attached, pending, collection)


def test_failed_index_can_retry_without_reextracting():
    cache = Cache()
    calls = []
    def index(*args, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("write failed")
    read_calls = []
    def read(_):
        read_calls.append(1)
        return [{"page_number": 1, "text": "facts"}]
    def run():
        return prepare_document("a", lambda _: {"file_path": "a.pdf", "filename": "a.pdf"},
            SimpleNamespace(get=lambda **kw: {"ids": [], "metadatas": []}), read, index, None, cache)
    with pytest.raises(RuntimeError):
        run()
    assert not cache.get("document-preparation-v1", "a")["text_ready"]
    assert run()["text_ready"]
    assert len(read_calls) == 1


def test_legacy_index_not_reembedded():
    state = prepare_document("old", lambda _: None,
        SimpleNamespace(get=lambda **kw: {"ids": ["old_chunk_0"], "metadatas": [{"content_type": "text"}]}),
        lambda _: pytest.fail("No original read"), lambda *a: pytest.fail("No reindex"), None, Cache())
    assert state["text_ready"]
