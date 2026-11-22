# Code map

## Request flow

1. `app/api/chat.py` receives a question.
2. `document_clarification.py` and `document_focus.py` resolve which documents it concerns.
3. `agent.py` prepares conversation context and lets Gemini choose tools.
4. `app/mcp_servers/document_server.py` exposes the document tools.
5. Gemini uses the tool evidence to answer; `session_store.py` saves the conversation.

## Where to make changes

| Concern | Service |
| --- | --- |
| Gemini requests, retries, rate limits | `gemini_gateway.py` |
| Whole-document summaries | `gemini_summary.py` |
| Model input size and evidence copies | `model_context.py` |
| First-time document preparation | `document_preparation.py` |
| PDF extraction, chunking, embeddings | `ingestion/` |
| Vector search and ranking | `vector_store.py` |
| Figure/page/section inventory | `document_index.py` |
| Figure answers and crop reuse | `figure_answer.py` |
| Persistent document caches | `summary_cache.py`, `postgres_state.py` |
| Chat records and document attachments | `session_store.py` |
| Persistent MCP process | `document_connection.py` |
| Logs | `app/logging_setup.py`, `request_logging.py`, `search_logging.py` |

PostgreSQL stores chats, checkpoints, and document caches. Chroma stores embeddings.
Uploaded files and figure crops live under `data/`. Model weights are reused separately.

The active summary path is Gemini. The retired Groq queue and older structured-summary
implementation were removed. The legacy cache namespace remains deliberately unchanged
so existing saved records can still be found. SQLite import support remains for migration.
