"""Exact numbered-figure lookup; cached crops and question-specific answers."""
import logging
import hashlib
import json
from pathlib import Path
from app.services.gemini_gateway import model_id
import os

logger = logging.getLogger(__name__)

def answer_figures(document_id, numbers, question, cache, read_pages, crop, describe):
    from app.services.document_index import indexed_state
    state = indexed_state(document_id, cache, read_pages)
    pages = state["pages"]
    selected = []
    for number in numbers:
        matches = [(p, c) for p in pages for c in p["figure_captions"] if c["figure_number"] == number]
        if len(matches) != 1:
            raise ValueError(f"Figure {number}: found {len(matches)} matching captions. Cannot safely select its image; please specify the page or upload the figure.")
        selected.append(matches[0])
    sources = []
    for page, caption in selected:
        key = [document_id, page["page_number"], caption]
        saved = cache.get("figure-crop-v1", key)
        if not saved or not Path(saved["image_path"]).is_file():
            saved = crop(state["file_path"], document_id, page["page_number"], caption, page["figure_captions"])
            cache.put("figure-crop-v1", key, saved)
        else:
            logger.info("Reusing crop | Figure %s | page %d", caption["figure_number"], page["page_number"])
        answer_key = [key, os.getenv("GEMINI_VISION_MODEL") or model_id(), question]
        answer = cache.get("figure-answer-v1", answer_key)
        if answer is None:
            answer = describe(saved["image_path"], caption=caption["caption"], question=question)
            if not answer or not answer.strip():
                raise ValueError("Figure analysis returned no answer; please retry.")
            cache.put("figure-answer-v1", answer_key, answer)
        else:
            logger.info("Reusing saved figure answer | Figure %s", caption["figure_number"])
        # Build on every response, including old cached crops without an ID.
        identity = json.dumps([document_id, page["page_number"], caption["figure_number"]],
                              ensure_ascii=False, separators=(",", ":"))
        chunk_id = "figure-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
        sources.append(dict(saved, chunk_id=chunk_id, document_id=document_id, filename=state["filename"],
            page_number=page["page_number"], figure_number=caption["figure_number"],
            caption=caption["caption"], content_type="figure", content=answer))
    return sources


def explain_indexed_figures(document_id, numbers, question, cache, read_pages, crop, describe):
    """An empty selection means every recognized figure, with per-item outcomes."""
    from app.services.document_index import indexed_state
    state = indexed_state(document_id, cache, read_pages)
    selected = numbers or list(dict.fromkeys(c["figure_number"] for p in state["pages"]
                                            for c in p["figure_captions"]))
    sources, outcomes = [], []
    for index, number in enumerate(selected, 1):
        logger.info("Explaining figure %s | %d/%d", number, index, len(selected))
        try:
            result = answer_figures(document_id, [number], question, cache, read_pages, crop, describe)
        except Exception as error:
            logger.warning("Figure %s unavailable | %s", number, type(error).__name__)
            # Provider exceptions can contain transport details; don't expose them.
            reason = str(error) if isinstance(error, (ValueError, FileNotFoundError)) else "Image analysis failed; retry this figure later."
            outcomes.append({"figure_number": number, "status": "unavailable", "reason": reason})
        else:
            sources.extend(result)
            outcomes.append({"figure_number": number, "status": "explained"})
    return sources, {"requested": len(selected), "explained": len(sources), "items": outcomes,
                     "notice": "Coverage is of recognized indexed captions, not a guarantee that every image in the PDF was detected. Do not guess additional figure numbers."}
