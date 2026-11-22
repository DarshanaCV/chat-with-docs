# Chat With Docs

A chat application where a conversation can have zero or more attached files searched via RAG, blended with the model's own general knowledge.

## Language

**Session**:
A conversation. Exists as a `chat_sessions` row once either a question is asked or a Document is attached — never on merely opening the app.
_Avoid_: Conversation, chat (fine as UI copy, not as the domain term).

**Document**:
A file (PDF or image) attached to a Session and searchable once processed. Many Documents can be attached to one Session (`session_documents`); removing one deletes its vectors from the store, not just unlinks it.
_Avoid_: File, attachment.

**Staged Document**:
A Document that has been uploaded and attached to a Session but not yet processed — extraction/captioning, embedding, and indexing are deferred until the first question that could need it, so attaching stays instant.
_Avoid_: Uploaded document (ambiguous about whether processing already happened).

**Relevance Gate**:
The check, made from the reranker's top score before generation, deciding whether a question's answer is allowed to cite attached Documents at all. Below the threshold, the question is treated as unrelated to every attached Document even though one may be present.
_Avoid_: "Used the document" (the old, unreliable post-hoc phrasing check this replaced).
