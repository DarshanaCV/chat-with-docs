"""Select only documents attached to this chat, and explicit one-based pages."""


def select_tool_documents(attached, document_id=None, filename=None, all_documents=False):
    if len(attached) > 1 and not (document_id or filename or all_documents):
        raise ValueError("Multiple documents are attached. Pass the requested document_id from the upload-order inventory; do not guess the newest file.")
    return select_documents(attached, document_id, filename, all_documents)


def select_documents(attached, document_id=None, filename=None, all_documents=False):
    if all_documents and (document_id or filename):
        raise ValueError("Choose either all documents or one document.")
    if document_id or filename:
        matches = [d for d in attached
                   if (not document_id or d["document_id"] == document_id)
                   and (not filename or d["filename"].casefold() == filename.casefold())]
        if len(matches) != 1:
            raise ValueError("The requested document is missing or ambiguous in this chat. Specify its document ID.")
        return matches
    return attached if all_documents else attached[-1:]


def select_pages(docs, metadata, page_start=None, page_end=None):
    if any(p is not None and p < 1 for p in (page_start, page_end)):
        raise ValueError("Page numbers start at 1.")
    if page_start is not None and page_end is not None and page_end < page_start:
        raise ValueError("The last page must not precede the first page.")
    if len(docs) != len(metadata):
        raise ValueError("Document text and metadata lengths differ.")
    pairs = [(d, m) for d, m in zip(docs, metadata)
             if (page_start is None and page_end is None) or
             (m.get("page_number") is not None
              and (page_start is None or m["page_number"] >= page_start)
              and (page_end is None or m["page_number"] <= page_end))]
    # Stable source numbering allows cache reuse even if Chroma returns a different order.
    pairs.sort(key=lambda pair: (str(pair[1].get("document_id", "")),
                                pair[1].get("page_number") or 0,
                                str(pair[1].get("chunk_id", "")), pair[0]))
    return [p[0] for p in pairs], [p[1] for p in pairs]
