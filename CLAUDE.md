# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A RAG chat app: a conversation ("Session") can have zero or more Documents (PDF or image) attached, searched via retrieval and blended with the model's own general knowledge. See [CONTEXT.md](CONTEXT.md) for the full domain vocabulary (Session, Document, Staged Document, Relevance Gate) — read it before naming things or reasoning about attach/processing state.

Backend: FastAPI + LangGraph + ChromaDB + Postgres. Frontend: Next.js (App Router) + Tailwind.

## Running locally

Backend (from repo root):
```powershell
uv run uvicorn app.main:app --reload --port 8000
```
Docs at `http://127.0.0.1:8000/docs`.

Frontend:
```powershell
cd frontend
npm install
npm run dev
```
Opens on `http://localhost:3000`; talks to the backend via `NEXT_PUBLIC_API_URL` (defaults to `http://127.0.0.1:8000`, see [frontend/lib/api.ts](frontend/lib/api.ts)).

Required env vars in the root `.env`: `GROQ_API_KEY` (answer generation + figure captioning) and `POSTGRES_CONN_STRING` (sessions/messages/checkpoints). `app/services/db.py` reads `POSTGRES_CONN_STRING` at import time, so `load_dotenv()` must run before any `app.*` module is imported — this happens implicitly in the app via early-imported modules, and explicitly in [tests/conftest.py](tests/conftest.py).

## Tests

```powershell
uv run pytest
uv run pytest tests/test_graph.py
uv run pytest tests/test_graph.py::test_relevant_chunks_are_attached_as_sources
```

`tests/test_graph.py` imports `app.services.graph`, which at import time loads the DocLayout-YOLO model, the CrossEncoder reranker, and sets up a Postgres-backed LangGraph checkpointer — this needs a reachable `POSTGRES_CONN_STRING` and a slow first run per session, but no test makes a real Groq or Chroma call (everything external is mocked via `mocker.patch.object` on the imported module, e.g. `graph_module.search_chunks`).

Frontend lint: `cd frontend && npm run lint`. No frontend test suite exists yet.

## Architecture

### Upload is decoupled from processing

`POST /upload` ([app/api/upload.py](app/api/upload.py)) only saves the file and registers it as "pending" in an in-memory dict ([app/services/pending_documents.py](app/services/pending_documents.py)) — it does **not** extract, embed, or index anything, so attaching a file to a session is instant. The actual extraction pipeline only runs the first time a question that could need the document is asked: `retreive_and_answer` in [app/services/graph.py](app/services/graph.py) pops each attached `document_id` out of the pending queue and, if it was still pending, runs `process_and_store_document` or `process_and_store_image` ([app/services/document_processor.py](app/services/document_processor.py)) synchronously before answering. Because the pending dict is in-process memory, restarting the backend loses pending (not-yet-asked-about) documents.

### RAG pipeline (single LangGraph node)

`process_and_store_document` runs: `document_loader.read_pdf` → `chunker.chunk_text` (per page) → `image_extractor.process_images` (DocLayout-YOLO detects figures/tables per rendered page; figures get a VLM-generated caption via Groq, tables get extracted via pdfplumber with a grid-then-text-strategy fallback for booktabs-style tables with no vertical rules) → `embeddings.create_embeddings` (local `sentence-transformers/all-MiniLM-L6-v2`, mean-pooled + normalized) → `vector_store.store_chunks` (writes to a single Chroma collection called `documents`, one shared collection for every session/document, filtered by `document_id` metadata at query time).

The whole retrieve-and-answer flow is one LangGraph node (`answer`) in a single-node graph — it isn't actually a multi-step graph yet, just wrapped in LangGraph for the Postgres checkpointer (conversation memory keyed by `thread_id` = `session_id`). When editing `graph.py`, prefer keeping mocizable module-level function references (`search_chunks`, `generate_response`, etc. imported by name) since tests patch them via `mocker.patch.object(graph_module, "name")`.

### Retrieval is pooled across documents, then reranked

`search_chunks` runs one Chroma query across *every* document_id attached to the session (`where={"document_id": {"$in": document_ids}}`) rather than querying per-document — so `rerank_chunks` (a CrossEncoder, `cross-encoder/ms-marco-MiniLM-L-6-v2`) decides which document's chunks actually surface, not a per-document quota. The reranker's top score is then checked against `RELEVANCE_THRESHOLD` in `graph.py` — the "Relevance Gate" (see [CONTEXT.md](CONTEXT.md)). Below threshold, the LLM still receives the context (so "summarize this" style requests keep working) but `sources` returned to the frontend is emptied, hiding citations for answers that don't confidently trace to specific chunks.

### Data model

Chroma (`data/chroma/`, single `documents` collection) holds chunk text + embeddings + metadata (`document_id`, `content_type`: text/table/figure, `page_number`, plus table `rows_json` / figure `image_path` for richer UI rendering). Postgres holds `chat_sessions`, `chat_messages` (with a `sources` JSONB column), and `session_documents` (session ↔ document attachment, many-to-many) — schema created idempotently by `init_tables()` in [app/services/chat_history.py](app/services/chat_history.py), called once at app startup. LangGraph's own checkpoint tables live in the same Postgres via `PostgresSaver`.

Removing a document from a session (`DELETE /sessions/{id}/documents/{doc_id}`) both discards it from the pending queue (if never processed) *and* deletes its vectors from Chroma (if already processed) — see [CONTEXT.md](CONTEXT.md)'s definition of Document for why both paths matter.

### Logging

[app/main.py](app/main.py) sets root logging to DEBUG but explicitly quiets noisy third-party loggers (httpx, chromadb, sentence_transformers, PIL, etc.) back to WARNING, so `app.*` module logs stay readable. Service functions generally log an `INFO` line with `document_id=%s`/`session_id=%s` context at entry/exit of each pipeline stage, and `logger.exception` on every caught failure — follow this pattern when adding new pipeline steps rather than introducing a different logging style.

## Frontend

`frontend/AGENTS.md` is auto-generated/re-added by `next dev` on every run (documents that this is a bleeding-edge Next.js version with docs in `node_modules/next/dist/docs/`) — commit it as-is rather than deleting it. `frontend/CLAUDE.md` just `@`-imports that file. Frontend API calls all go through [frontend/lib/api.ts](frontend/lib/api.ts); add new endpoints there rather than calling `fetch` directly from components.
