"""Resolve document references before running tools; never silently choose after a comparison."""
import re
from contextvars import ContextVar

from langchain_core.tools import StructuredTool

request_scope = ContextVar("document_request_scope", default=None)


class ClarifyDocument(ValueError):
    pass


def resolve_focus(question, attached, focus):
    ids = [d["document_id"] for d in attached]
    focus = [i for i in focus if i in ids]
    q = question.casefold()
    # Comparisons of content do not imply a comparison of uploaded documents.
    content_reference = bool(re.search(
        r"\b(?:figures?|fig\.?|tables?|parts?|panels?|sections?|paragraphs?|charts?|equations?)\b", q))
    document_comparison = bool(re.search(r"\b(?:pdfs|papers|documents|files)\b", q))
    local_content = content_reference and not document_comparison
    selected = []
    for d in attached:
        name = d["filename"].casefold()
        stem = re.sub(r"\.pdf$", "", name)
        if name in q or (len(stem) > 2 and re.search(r"(?<!\w)" + re.escape(stem) + r"(?!\w)", q)):
            selected.append(d["document_id"])
    ordinals = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2,
                "fourth": 3, "4th": 3, "fifth": 4, "5th": 4}
    for word, index in ordinals.items():
        if (re.search(rf"\b{word}\s+(?:uploaded\s+)?(?:pdf|paper|document|file|one)\b", q)
                or ("compare" in q and document_comparison and re.search(rf"\b{word}\b", q))):
            if index >= len(ids):
                raise ClarifyDocument("That upload number does not exist. Which PDF do you mean?")
            selected.append(ids[index])
    for match in re.finditer(r"\b(?:pdf|paper|document|file)\s*#?\s*(\d+)\b", q):
        index = int(match[1]) - 1
        if not 0 <= index < len(ids):
            raise ClarifyDocument("That upload number does not exist. Which PDF do you mean?")
        selected.append(ids[index])
    if selected:
        return list(dict.fromkeys(selected))
    if re.search(r"\ball\s+(?:(?:uploaded|attached|the)\s+)*(?:pdfs|papers|documents|files)\b", q):
        return ids
    if re.search(r"\b(?:latest|newest|last uploaded)\b", q):
        return ids[-1:]
    if re.search(r"\bprevious\b", q) and (not local_content or re.search(r"\bprevious\s+(?:pdf|paper|document|file)\b", q)):
        if len(focus) != 1:
            raise ClarifyDocument("Which PDF do you mean by previous? Please give its filename or upload number.")
        index = ids.index(focus[0])
        if index == 0:
            raise ClarifyDocument("The focused PDF is the first upload; there is no previous PDF.")
        return [ids[index - 1]]
    document_question = bool(re.search(r"\b(pdf\w*|paper\w*|document\w*|figure\w*|table\w*|summari\w*|conclusion\w*|compare|both|these|those)\b", q))
    if re.search(r"\b(?:both|these|those|them)\b", q) and not local_content:
        if len(focus) > 1:
            return focus
        if len(ids) == 2:
            return ids
        raise ClarifyDocument("Which PDFs should I use? Please give their filenames or upload numbers.")
    if re.search(r"\bcompare\b", q) and not local_content and len(focus) < 2:
        raise ClarifyDocument("Which PDFs should I compare? Please give their filenames or upload numbers.")
    if re.search(r"\bcompare\b", q) and not local_content:
        return focus
    if not focus and document_question and ids:
        if len(ids) == 1:
            return ids
        raise ClarifyDocument("Which PDF do you mean? Please give its filename or upload number.")
    if len(focus) > 1 and document_question:
        names = [d["filename"] for d in attached if d["document_id"] in focus]
        raise ClarifyDocument("Which paper do you mean: " + " or ".join(names) + "?")
    return focus


def scoped_tools(tools):
    """Tools cannot override the backend-selected session or expand the document set."""
    result = []
    for original in tools:
        if original.name not in ("search_documents", "get_document_overview", "get_document_index", "read_document_pages", "read_document_section", "explain_document_figures"):
            result.append(original)
            continue

        async def run(_tool=original, **kwargs):
            scope = request_scope.get()
            if scope is None:
                raise ValueError("Document tools require a backend request scope")
            session_id, selected = scope
            if not selected:
                raise ValueError("Select one or more attached PDFs before using document tools")
            if _tool.name == "read_document_pages" and len(selected) > 1 and not kwargs.get("document_id"):
                raise ValueError("Select one document_id for page reading; PDFs have different page ranges")
            if (_tool.name in ("get_document_index", "read_document_section")
                    and len(selected) > 1 and kwargs.get("offset", 0) != 0
                    and not kwargs.get("document_id")):
                import json
                return json.dumps({"context": "Pagination needs one document_id from the selected documents. "
                    "Use that document's own next_offset; do not share offsets between PDFs.",
                    "selected_document_ids": selected, "sources": []})
            targets = selected
            if _tool.name in ("read_document_pages", "read_document_section", "get_document_index") and kwargs.get("document_id"):
                if kwargs["document_id"] not in selected:
                    raise ValueError("Requested document is outside the selected PDFs")
                targets = [kwargs["document_id"]]
            outputs = []
            for document_id in targets:
                args = dict(kwargs, session_id=session_id, document_id=document_id,
                            filename=None, all_documents=False)
                output = await _tool.ainvoke(args)
                outputs.append({"document_id": document_id, "result": output})
            if len(outputs) == 1:
                return outputs[0]["result"]
            # Preserve each document's own evidence and source IDs separately.
            import json
            sources, contexts = [], []
            for item in outputs:
                raw = item["result"]
                if isinstance(raw, list):
                    raw = next((b["text"] for b in raw if b.get("type") == "text"), "")
                try:
                    data = json.loads(raw)
                except (TypeError, ValueError):
                    raise ValueError("One selected document returned no usable evidence; comparison stopped") from None
                mapping = {}
                for source in data.get("sources", []):
                    old = source.get("source_id")
                    new = f"S{len(sources) + 1}"
                    if old:
                        mapping[old] = new
                    sources.append(dict(source, source_id=new))
                context = re.sub(r"\[(S\d+)\]", lambda m: f"[{mapping.get(m[1], m[1])}]", data.get("context", ""))
                contexts.append(f"Document {item['document_id']}:\n{context}")
            return json.dumps({"context": "\n\n".join(contexts), "sources": sources})

        result.append(StructuredTool.from_function(coroutine=run, name=original.name,
                      description=original.description, args_schema=original.args_schema))
    return result
