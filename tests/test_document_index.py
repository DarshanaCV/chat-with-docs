import json
from app.services.document_index import inventory


def state(count=100):
    return {"filename": "paper.pdf", "pages": [{"page_number": 1,
        "figure_captions": [{"figure_number": str(n), "caption": "Caption " + "x" * 500,
                            "caption_bbox": [1, 2, 3, 4]} for n in range(count)],
        "heading_candidates": [{"title": "Noise", "bbox": [1,2,3,4]}] * 300}]}


def test_default_only_returns_counts():
    result = inventory(state())
    assert result["counts"]["figures"] == 100
    assert "entries" not in result
    assert len(json.dumps(result)) < 1000


def test_pages_cover_every_entry_without_coordinates_or_other_categories():
    document = state()
    offset, labels = 0, []
    while offset is not None:
        result = inventory(document, "figures", offset)
        encoded = json.dumps(result, ensure_ascii=False)
        assert len(encoded.encode()) <= 8000
        assert "bbox" not in encoded and "Noise" not in encoded
        labels.extend(e["figure_number"] for e in result["entries"])
        offset = result["next_offset"]
    assert labels == [str(n) for n in range(100)]


def test_unicode_respects_byte_budget():
    document = state(50)
    for entry in document["pages"][0]["figure_captions"]:
        entry["caption"] = "\U0001f4c4" * 400
    result = inventory(document, "figures")
    assert len(json.dumps(result, ensure_ascii=False).encode()) <= 8000
    assert result["next_offset"] is not None


def test_section_lookup_uses_each_documents_actual_pages():
    from app.services.document_index import section_sources
    for page_number in (2, 9):
        document = {"filename": "paper.pdf", "pages": [{"page_number": page_number,
            "text": "Unrelated introduction\n7 Conclusion\nOur findings are useful.\n8 References\nNot evidence",
            "heading_candidates": []}]}
        sources, report = section_sources("doc", document, "conclusion")
        assert sources[0]["page_number"] == page_number
        assert "Our findings" in sources[0]["content"]
        assert "Not evidence" not in sources[0]["content"]
        assert report["next_offset"] is None


def test_section_missing_and_pagination_are_explicit():
    from app.services.document_index import section_sources
    document = {"filename": "paper.pdf", "pages": [{"page_number": 1,
        "text": "Conclusion\n" + "Evidence " * 100, "heading_candidates": []}]}
    assert section_sources("doc", document, "methods")[1]["found"] is False
    sources, report = section_sources("doc", document, "conclusion", limit=40)
    assert len(sources[0]["content"]) == 40
    assert report["next_offset"] == 40


def test_context_bound_preserves_originals_and_tool_pairing():
    from app.services.model_context import bound_tool_evidence
    from app.services.gemini_gateway import estimate_tokens
    from langchain_core.messages import ToolMessage
    messages = [ToolMessage(content="Evidence " * 5000, tool_call_id=str(i)) for i in range(4)]
    measure = lambda items: estimate_tokens([m.model_dump() for m in items])
    result = bound_tool_evidence(messages, 4000, measure)
    assert measure(result) <= 4000
    assert all(len(m.content) == 45000 for m in messages)
    assert [m.tool_call_id for m in result] == [m.tool_call_id for m in messages]
    assert all("omitted" in m.content for m in result)


def test_exhausted_index_returns_empty_without_claiming_full_coverage():
    for count, offset in [(0, 0), (2, 2), (2, 30)]:
        result = inventory(state(count), "figures", offset)
        assert result["entries"] == []
        assert result["next_offset"] is None
        assert result["total"] == count
        assert "does not establish coverage" in result["notice"]
