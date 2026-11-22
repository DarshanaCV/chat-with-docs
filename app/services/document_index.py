"""Versioned document inventory backed by saved text; never creates embeddings."""
import logging
import json
from app.services.ingestion.figure_captions import CAPTION_INDEX_VERSION
logger = logging.getLogger(__name__)

def indexed_state(document_id, cache, read_pages):
    state = cache.get("document-preparation-v1", document_id)
    if not state:
        raise ValueError("Document must be prepared before reading its index.")
    pages = state.get("pages")
    if pages is None or any(p.get("caption_index_version") != CAPTION_INDEX_VERSION for p in pages):
        if not state.get("file_path"):
            raise ValueError("Original file unavailable to refresh the document index; please re-upload it.")
        logger.info("Refreshing document index | text only, no new embeddings")
        pages = read_pages(state["file_path"])
        state["pages"] = pages
        cache.put("document-preparation-v1", document_id, state)
    return state

def inventory(state, category="overview", offset=0, limit=30, byte_budget=8000):
    """Bounded model-facing inventory; retain coordinates only in saved state."""
    fields = {"figures": ("figure_captions", "figure_number", "caption"),
              "tables": ("table_captions", "table_number", "caption"),
              "headings": ("heading_candidates", None, "title")}
    if category not in ("overview", *fields):
        raise ValueError("Choose overview, figures, tables, or headings.")
    if offset < 0 or not 1 <= limit <= 50:
        raise ValueError("offset must be nonnegative; limit must be between 1 and 50.")
    counts = {name: sum(len(p.get(field[0], [])) for p in state["pages"])
              for name, field in fields.items()}
    result = {"filename": state["filename"], "page_count": len(state["pages"]),
              "category": category, "counts": counts,
              "notice": "Recognized text entries only; scanned/unusual captions may be missing. Headings are unverified candidates."}
    if category == "overview":
        result["instruction"] = "Request a category to read entries. Follow next_offset until null for a complete list."
        return result
    field, label, text_field = fields[category]
    entries = []
    for page in state["pages"]:
        for item in page.get(field, []):
            entry = {"page_number": page["page_number"], text_field: item[text_field][:300]}
            if len(item[text_field]) > 300:
                entry["text_truncated"] = True
            if label:
                entry[label] = item[label]
            entries.append(entry)
    result.update(entries=[], offset=offset, total=len(entries), next_offset=None)
    if offset >= len(entries):
        result["notice"] += " No more entries at this offset; this does not establish coverage of earlier entries."
        return result
    for entry in entries[offset:offset+limit]:
        candidate = dict(result, entries=[*result["entries"], entry])
        if len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) > byte_budget - 100:
            if not result["entries"]:
                raise ValueError("An inventory entry exceeds the response budget.")
            break
        result["entries"].append(entry)
    end = offset + len(result["entries"])
    result["next_offset"] = end if end < len(entries) else None
    return result


def page_sources(document_id, state, start, end, character_budget=12000):
    pages = state["pages"]
    if not 1 <= start <= end <= len(pages):
        raise ValueError(f"Page range must be between 1 and {len(pages)}.")
    sources, used = [], 0
    for page in pages[start-1:end]:
        text = page["text"]
        if used + len(text) > character_budget:
            if not sources:
                raise ValueError("This page exceeds the raw-text budget; use a targeted search.")
            break
        sources.append(dict(chunk_id=f"{document_id}:page:{page['page_number']}",
            filename=state["filename"], page_number=page["page_number"], content_type="text", content=text, score=1.0))
        used += len(text)
    next_page = start + len(sources) if len(sources) < end-start+1 else None
    return sources, next_page


def section_sources(document_id, state, section, offset=0, limit=8000):
    """Locate a literal heading, then return a bounded slice with explicit coverage."""
    import re
    if offset < 0 or not section.strip():
        raise ValueError("Provide a section title and a nonnegative offset")
    title = section.strip().casefold()
    rows = [(page["page_number"], line) for page in state["pages"] for line in page["text"].splitlines()]
    def heading(line):
        return re.sub(r"^\s*(?:\d+(?:\.\d+)*[.)]?\s+)", "", line).strip().casefold()
    matches = [i for i, (_, line) in enumerate(rows) if heading(line) == title or heading(line) == title + "s"]
    if len(matches) != 1:
        return [], {"found": False, "reason": "Section heading missing or ambiguous; use index or targeted search."}
    start = matches[0]
    candidates = {h["title"].strip().casefold() for p in state["pages"] for h in p.get("heading_candidates", [])}
    end = len(rows)
    for i in range(start + 1, len(rows)):
        line = rows[i][1].strip()
        numbered = re.match(r"^\d+(?:\.\d+)*[.)]?\s+[A-Za-z]", line)
        if line and len(line) < 120 and (numbered or line.casefold() in candidates):
            end = i
            break
    parts = []
    position = 0
    for page, line in rows[start:end]:
        line += "\n"
        left, right = max(0, offset-position), min(len(line), offset+limit-position)
        if right > left:
            if parts and parts[-1][0] == page:
                parts[-1][1] += line[left:right]
            else:
                parts.append([page, line[left:right]])
        position += len(line)
    sources = [dict(chunk_id=f"{document_id}:section:{section}:{offset}:{page}", filename=state["filename"],
                    page_number=page, content_type="text", content=text, score=1.0) for page, text in parts]
    return sources, {"found": True, "next_offset": offset+limit if offset+limit < position else None,
                     "notice": "Heading boundaries are inferred from extracted text; verify before claiming complete coverage."}
