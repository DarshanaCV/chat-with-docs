# Chat With Docs

Chat With Docs is a document Q&A application for reading research papers and other PDFs. Upload a PDF, ask a question in plain language, and follow the page references and source cards to check the answer against the document.

## What you can do

- Ask focused questions about a PDF, read a named section, or request a whole-document summary.
- Look up tables and figures, inspect extracted table rows, and view figure crops when available.
- Attach multiple documents to a conversation and compare their findings.
- Reopen saved conversations with their messages and attached documents.

Try questions such as “What evaluation metrics did the authors use?”, “What does Figure 2 show?”, or “Compare the methods used in these two papers.”

## How it works

The Next.js frontend sends questions to FastAPI. The backend resolves which attached documents the question refers to and uses a LangGraph agent with Gemini to choose document tools. Those tools run in a separate MCP process. Depending on the question, they can retrieve and rerank relevant passages, read specific pages or sections, summarize a document, or look up tables and figures. The API returns the answer with the sources used to generate it.

| Component | Responsibility |
| --- | --- |
| Next.js and React | Uploads, conversations, and source cards |
| FastAPI, LangGraph, Gemini, and MCP | Request handling, document selection, tool calls, and answer generation |
| PyMuPDF, pdfplumber, DocLayout-YOLO, and Sentence Transformers | PDF text and layout extraction, embeddings, and passage reranking |
| PostgreSQL | Conversation history, agent checkpoints, and document caches |
| Chroma and local `data/` storage | Vector index, uploaded files, and figure crops |

## Run locally

You’ll need **Python 3.14+** with **uv**, **Node.js 20.9+** with npm, a running **PostgreSQL** server, and a **Gemini API key**. The first startup needs internet access to download model weights.

### 1. Create a database and configure the backend

Create an empty PostgreSQL database named `chat_with_docs`. From the repository root, copy the environment template:

```powershell
Copy-Item .env.example .env
```

Set these values in `.env` using your own credentials. The database role needs permission to create tables.

| Variable | Value |
| --- | --- |
| `POSTGRES_CONN_STRING` | Connection URL for your database, for example `postgresql://postgres:replace-me@127.0.0.1:5432/chat_with_docs` |
| `GEMINI_API_KEY` | Your Gemini API key |

Keep `.env` private. The repository includes `.env.example` for sharing configuration names.

### 2. Start FastAPI

```powershell
uv sync --locked
uv run uvicorn app.main:app --reload --port 8000
```

Wait for **Application startup complete**. FastAPI creates the database tables and loads the document tools during startup. The API reference is available at `http://127.0.0.1:8000/docs`.

### 3. Start Next.js

From the repository root in a second terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Open **http://localhost:3000**, select **New conversation**, and attach a PDF. The conversation appears in the sidebar after you attach a file or send a message. The first question about a newly attached document can take longer while the backend prepares its text and index.

Next.js forwards `/api` requests to `http://127.0.0.1:8000` by default. If FastAPI runs on a different address, set `API_PROXY_TARGET` in `frontend/.env.local` and restart Next.js.

## Optional demo mode

Demo mode gives each browser an anonymous workspace without requiring signup. Set `APP_MODE=demo` and `DEMO_POSTGRES_CONN_STRING` in the backend environment, using a separate, empty PostgreSQL database. Start the demo from a separate checkout or deployment volume so it has its own `data/` directory. The frontend uses its same-origin `/api` proxy to keep visitor cookies available for both API calls and citation images.

Use HTTPS when serving the demo publicly. To test isolation locally, open the app at `localhost` in a regular window and an incognito window. Create a conversation in each and verify that each window shows only its own history. The visitor cookie expires after 30 days. Clearing it starts a new workspace.

## Repository guide

| Path | Contents |
| --- | --- |
| `frontend/` | Next.js interface and API proxy |
| `app/api/` | Upload, chat, conversation, and visitor endpoints |
| `app/services/ingestion/` | PDF extraction, chunking, embeddings, and layout analysis |
| `app/mcp_servers/` | Document tools exposed to the agent |
| `tests/` | Unit and integration tests |

Run backend tests from the repository root with `uv run pytest`. Session API tests use the configured PostgreSQL database and may load local models. Run frontend lint checks from `frontend/` with `npm run lint`.

See the [code map](docs/code-map.md) for module responsibilities and the [logging guide](docs/readable-logs.md) for tracing a request.
