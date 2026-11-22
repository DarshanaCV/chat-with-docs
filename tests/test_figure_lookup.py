import pytest
import pymupdf
from app.services.ingestion.figure_captions import captions_from_blocks, match_figure, requested_figures
from app.services.ingestion.loader import read_pdf
from app.services.figure_answer import answer_figures

class Cache:
    def __init__(self):
        self.data = {}
    def get(self, kind, key):
        import json
        return self.data.get((kind, json.dumps(key, sort_keys=True)))
    def put(self, kind, key, value):
        import json
        self.data[kind, json.dumps(key, sort_keys=True)] = value

def test_text_pass_saves_caption_number_and_position(tmp_path):
    path = tmp_path / "paper.pdf"
    with pymupdf.open() as doc:
        page = doc.new_page()
        page.insert_text((40, 50), "See Figure 4 for results.")
        page.insert_text((40, 250), "Figure 1: Architecture")
        page.insert_text((40, 500), "Fig. 4. Results")
        doc.save(path)
    pages = read_pdf(path)
    assert "See Figure 4" in pages[0]["text"]
    captions = pages[0]["figure_captions"]
    assert [c["figure_number"] for c in captions] == ["1", "4"]
    assert captions[0]["caption_bbox"][1] < captions[1]["caption_bbox"][1]


def test_requested_number_does_not_match_four_or_fourteen():
    assert requested_figures("Explain Figure 1") == ["1"]
    assert requested_figures("Compare Figures 1 and 4") == ["1", "4"]
    assert requested_figures("Fig. S2 and Figure 14") == ["S2", "14"]
    with pytest.raises(ValueError):
        requested_figures("Figures 1-4")


def test_two_figures_on_same_page_and_ambiguous_match():
    left = {"caption_bbox": [0, 200, 100, 220]}
    right = {"caption_bbox": [200, 200, 300, 220]}
    boxes = [[200, 50, 300, 190], [0, 50, 100, 190]]
    assert match_figure(left, [left, right], boxes) == boxes[1]
    with pytest.raises(ValueError):
        match_figure(left, [left], [boxes[1], [0, 60, 100, 185]])


def test_crop_and_answers_reused_but_new_question_calls_vision(tmp_path):
    cache = Cache()
    cap = {"figure_number": "1", "caption": "Figure 1: Test", "caption_bbox": [0, 200, 100, 220]}
    for doc_id in ["a", "b"]:
        cache.put("document-preparation-v1", doc_id, {"file_path": doc_id + ".pdf", "filename": doc_id + ".pdf",
            "pages": [{"page_number": 2, "caption_index_version": 2, "figure_captions": [cap]}]})
    crops, descriptions = [], []
    def crop(path, doc_id, page, caption, captions):
        crops.append((doc_id, page))
        image = tmp_path / (doc_id + ".png")
        image.write_bytes(b"fake")
        return {"image_path": str(image), "bbox": [0, 0, 100, 190]}
    def describe(path, **kwargs):
        descriptions.append(kwargs)
        return "Visible evidence"
    def run(doc_id="a", question="Explain Figure 1", numbers=None):
        return answer_figures(doc_id, numbers or ["1"], question, cache,
            lambda _: pytest.fail("Must not reread an indexed PDF"), crop, describe)
    from app.models.schemas import Source
    first = run()[0]
    assert first["filename"] == "a.pdf"
    assert Source(**first, score=1.0).chunk_id
    assert run()[0]["chunk_id"] == first["chunk_id"]
    assert len(crops) == len(descriptions) == 1
    assert run(question="What do the arrows in Figure 1 mean?")[0]["chunk_id"] == first["chunk_id"]
    assert len(crops) == 1 and len(descriptions) == 2
    other = run("b")[0]
    assert other["filename"] == "b.pdf"
    assert other["chunk_id"] != first["chunk_id"]
    assert crops == [("a", 2), ("b", 2)]
    with pytest.raises(ValueError, match="0 matching captions"):
        run(numbers=["4"])
    assert len(crops) == 2


def test_old_pdf_caption_backfill_happens_once(tmp_path):
    cache = Cache()
    cache.put("document-preparation-v1", "old", {"file_path": "old.pdf", "filename": "old.pdf", "pages": [{"text": "old text"}]})
    reads = []
    def read(path):
        reads.append(path)
        return [{"page_number": 1, "caption_index_version": 2, "text": "old text", "figure_captions": []}]
    for _ in range(2):
        with pytest.raises(ValueError, match="0 matching captions"):
            answer_figures("old", ["1"], "Explain", cache, read, None, None)
    assert reads == ["old.pdf"]


@pytest.mark.parametrize("reverse", [False, True])
def test_phyai_figure_two_prefers_complete_detection(reverse):
    # Actual page-seven detections: lower panel, upper panel, whole figure.
    caption = {"caption_bbox": [70.866, 416.687, 524.415, 436.612]}
    boxes = [[121.269, 275.676, 471.538, 395.120],
             [112.376, 62.800, 478.093, 249.038],
             [113.204, 61.670, 478.613, 397.779]]
    complete = boxes[-1]
    assert match_figure(caption, [caption], boxes[::-1] if reverse else boxes) == complete


def test_enclosing_box_cannot_swallow_another_caption():
    target = {"caption_bbox": [0, 400, 200, 420]}
    other = {"caption_bbox": [0, 180, 200, 200]}
    complete = [0, 20, 200, 390]
    target_image = [0, 250, 200, 390]
    assert match_figure(target, [target, other], [complete, target_image]) == target_image
    with pytest.raises(ValueError):
        match_figure(target, [target, other], [complete])


def test_separate_side_by_side_panels_remain_ambiguous():
    caption = {"caption_bbox": [0, 200, 220, 220]}
    with pytest.raises(ValueError):
        match_figure(caption, [caption], [[0, 20, 100, 190], [120, 20, 220, 190]])


def test_caption_paragraph_is_not_a_second_caption():
    blocks = [(0, 0, 100, 10, "Figure 9. Timeline of a step.", 0, 0),
              (0, 20, 100, 40, "Figure 9 shows a representative step.", 1, 0)]
    assert len(captions_from_blocks(blocks)) == 1


def test_all_figures_uses_inventory_and_keeps_partial_results(monkeypatch):
    from app.services import figure_answer
    cache = Cache()
    cache.put("document-preparation-v1", "doc", {"filename": "paper", "pages": [
        {"page_number": 3, "caption_index_version": 2, "figure_captions": [
            {"figure_number": "2"}, {"figure_number": "S5"}]}]})
    calls = []
    def answer(document_id, numbers, *args):
        calls.append(numbers)
        if numbers == ["2"]:
            raise ValueError("Ambiguous crop")
        return [{"figure_number": "S5", "content": "evidence"}]
    monkeypatch.setattr(figure_answer, "answer_figures", answer)
    sources, report = figure_answer.explain_indexed_figures("doc", None, "Explain all", cache, None, None, None)
    assert calls == [["2"], ["S5"]]
    assert len(sources) == 1 and report["requested"] == 2 and report["explained"] == 1
    assert report["items"][0]["reason"] == "Ambiguous crop"


def test_index_version_refreshes_old_caption_list_once():
    from app.services.document_index import indexed_state
    cache = Cache()
    cache.put("document-preparation-v1", "doc", {"filename": "paper", "file_path": "paper.pdf",
        "text_ready": True, "pages": [{"figure_captions": [{"figure_number": "9"}]}]})
    calls = []
    def read(path):
        calls.append(path)
        return [{"caption_index_version": 2, "figure_captions": []}]
    assert indexed_state("doc", cache, read)["text_ready"]
    indexed_state("doc", cache, read)
    assert calls == ["paper.pdf"]


def test_original_pages_preserve_text_and_report_remaining_pages():
    from app.services.document_index import page_sources
    state = {"filename": "paper", "pages": [{"page_number": n, "text": "Reference " + str(n)} for n in range(1, 4)]}
    sources, next_page = page_sources("doc", state, 1, 3, character_budget=15)
    assert sources[0]["content"] == "Reference 1" and next_page == 2
    sources, next_page = page_sources("doc", state, 2, 3)
    assert len(sources) == 2 and next_page is None
    with pytest.raises(ValueError):
        page_sources("doc", state, 0, 3)
