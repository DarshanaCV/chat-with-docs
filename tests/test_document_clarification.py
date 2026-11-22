from app.services.document_clarification import resolve_turn

DOCS = [{"document_id": "q", "filename": "Quantization Effects on Biomedical LLM Reliability.pdf"},
        {"document_id": "p", "filename": "Real-Time Physical AI at the Edge.pdf"}]


def test_short_title_then_yes_resumes_original_task():
    first = resolve_turn("explain figures from previous paper", DOCS, ["q", "p"])
    second = resolve_turn("from the Quantization paper", DOCS, ["q", "p"], first["pending"])
    assert second["pending"]["suggestion"] == "q"
    third = resolve_turn("Yes", DOCS, ["q", "p"], second["pending"])
    assert third["selected"] == ["q"]
    assert third["question"] == "explain figures from previous paper"
    assert third["pending"] is None


def test_yes_does_not_choose_between_two_pdfs():
    decision = resolve_turn("Quantization or Physical AI?", DOCS, ["q", "p"])
    assert "reply" in decision
    assert "reply" in resolve_turn("yes", DOCS, ["q", "p"], decision["pending"])


def test_cancel_and_new_request_replaces_pending():
    pending = {"question": "explain figures", "suggestion": "q"}
    decision = resolve_turn("Forget that, summarize the latest PDF", DOCS, ["q"], pending)
    assert decision["selected"] == ["p"]
    assert decision["question"] == "summarize the latest PDF"
    assert resolve_turn("cancel", DOCS, ["q"], pending)["pending"] is None


def test_no_clears_suggestion_and_deleted_suggestion_cannot_run():
    pending = {"question": "explain figures", "suggestion": "q"}
    assert resolve_turn("no", DOCS, ["q"], pending)["pending"]["suggestion"] is None
    assert "reply" in resolve_turn("yes", DOCS[1:], ["p"], pending)


def test_explicit_new_named_task_does_not_resume_old_one():
    result = resolve_turn("summarize Physical AI", DOCS, ["q"],
                          {"question": "explain figures", "suggestion": "q"})
    assert result["selected"] == ["p"]
    assert result["question"] == "summarize Physical AI"
