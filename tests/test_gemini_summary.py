import re
from types import SimpleNamespace

import pymupdf
import pytest

from app.services.gemini_summary import GeminiSummary, summarize_with_gemini
from app.services.summary_input import read_summary_input


class Cache:
    def __init__(self):
        self.values = {}

    def get(self, kind, key):
        return self.values.get(repr((kind, key)))

    def put(self, kind, key, value):
        self.values[repr((kind, key))] = value


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    calls = []

    def request(self, method, body):
        calls.append(method)
        text = body["contents"][0]["parts"][0]["text"]
        if method == "countTokens":
            return {"totalTokens": len(text)}
        # The actual source text follows the instruction, which includes an example ID.
        source_text = text.split("\n\n", 1)[1]
        ids = sorted(set(re.findall(r"\[S\d+\]", source_text)))
        return {"candidates": [{"finishReason": "STOP", "content": {
            "parts": [{"text": "Summary " + " ".join(ids)}]}}]}

    monkeypatch.setattr(GeminiSummary, "request", request)
    return calls


def test_direct_summary_and_cache(provider):
    cache = Cache()
    assert summarize_with_gemini(["facts"], [{}], cache) == "Summary [S1]"
    assert provider == ["countTokens", "generateContent"]
    summarize_with_gemini(["facts"], [{}], cache)
    assert len(provider) == 2


def test_large_document_splits_and_combines(provider, monkeypatch):
    monkeypatch.setenv("GEMINI_SUMMARY_INPUT_BUDGET", "1000")
    answer = summarize_with_gemini(["a" * 1200, "b" * 1200], [{}, {}])
    assert "[S1]" in answer and "[S2]" in answer
    assert provider.count("generateContent") > 1


@pytest.mark.parametrize("finish,text", [("MAX_TOKENS", "fact [S1]"), ("STOP", "fact [S99]"),
                                        ("STOP", "fact [S1, S99]"), ("STOP", "")])
def test_invalid_answers_not_cached(provider, monkeypatch, finish, text):
    def request(self, method, body):
        if method == "countTokens":
            return {"totalTokens": 100}
        return {"candidates": [{"finishReason": finish, "content": {"parts": [{"text": text}]}}]}
    monkeypatch.setattr(GeminiSummary, "request", request)
    cache = Cache()
    with pytest.raises(ValueError):
        summarize_with_gemini(["facts"], [{}], cache)
    assert not cache.values


def test_pdf_read_does_not_consume_pending_or_index(tmp_path):
    path = tmp_path / "paper.pdf"
    with pymupdf.open() as pdf:
        for content in ("First page", "Second page"):
            page = pdf.new_page()
            page.insert_text((50, 50), content)
        pdf.save(path)
    pending = {"file_path": str(path)}
    docs, metadata = read_summary_input(
        [{"document_id": "a", "filename": "paper.pdf"}], lambda _: pending,
        SimpleNamespace(get=lambda **kw: pytest.fail("Must not index PDF")), 2, 2)
    assert docs == ["Second page"]
    assert metadata[0]["page_number"] == 2
    assert pending["file_path"] == str(path)
