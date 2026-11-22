# PostgreSQL storage (current, October 15)

All application cache/checkpoint data now lives in PostgreSQL document_cache as
JSONB, using the existing POSTGRES_CONN_STRING and unchanged cache keys. This
includes page text, preparation state, caption indexes, summaries, crop metadata,
page layout detections and question-specific figure explanations. No re-embedding
or regeneration is needed because keys and values are preserved.

Gemini admission uses gemini_rate_scopes and gemini_usage in PostgreSQL. Per-model
row locks serialize admission across API/MCP processes. Transactions commit before
sleeping or making a provider call; timestamps come from PostgreSQL.

Startup initializes these tables and imports legacy SQLite files transactionally.
app_state_migrations records a completed import. Repeated startup does not reimport
old data or overwrite newer PostgreSQL values. Migration failures roll back and
can be retried; they do not silently switch back to SQLite. The standalone command
is `.venv\Scripts\python.exe scripts/migrate_sqlite_state.py`.

Stop old backend processes before switching versions; restart the backend after
migration so no old workers continue writing SQLite. Original SQLite files are
retained untouched as backups, not live stores. Back up PostgreSQL plus the PDF
and figure directories going forward. Chroma (including its own internal storage)
is unchanged; pgvector is not part of this migration. Files remain on disk.

Validation: isolated-schema tests cover cache persistence, one-time import,
rollback/retry, active rate-limit migration and concurrent admission. The initial
local copy verified 69 identical cache records, zero missing or different values.
Historical sections below may describe the former SQLite implementation.

# Bounded inventory responses

get_document_index defaults to counts only. Choose category figures, tables, or
headings for entries. Responses omit coordinates, cap caption/title previews at
300 characters (marked text_truncated), and paginate by both entry count and an
8 KB JSON budget. next_offset is null only when the requested category is complete.
Saved metadata remains unchanged. read_document_pages now defaults to a 12,000
character batch to leave room for tool schemas, instructions, and history.

Verified the Physical AI figure listing with real PDF extraction and a live Gemini
agent round trip using an in-memory inventory tool: all nine figure/page pairs were
correct, with a roughly 2.5 KB inventory. This check does not exercise HTTP or MCP
transport; scripts/check_figure_index.py reproduces it without re-embedding.

# Indexed retrieval (current)

Document tools now separate semantic search, inventory, original page reading,
and figure explanation. The agent selects the operation; no paper names, fixed
page numbers, or figure counts are embedded in routing.

- get_document_index returns recognized figure/table captions and heading candidates.
- read_document_pages returns original text with source IDs and explicit next_page
  when the response budget stops before the requested end. Section boundaries need
  verification; heading candidates are deliberately not treated as confirmed sections.
- explain_document_figures enumerates the saved inventory when numbers are omitted,
  processes every selected label and reports per-item failures alongside successes.
- search_documents remains for topical retrieval. Numbered figure queries use the
  indexed figure path. Broad include_visuals searches redirect to indexed tools,
  never scan the whole PDF and silently return only a few figures.

All new tools are restricted to the backend-selected attached documents. Figure
labels support supplemental labels such as S1. Caption index version 2 requires
label punctuation, excluding prose like "Figure 9 shows...". Older preparation
checkpoints refresh from original PDF text on access without re-embedding. This
conservative rule can miss unusual or scanned captions; coverage reports say so.
It is not a universal PDF-layout parser or OCR system.

Layout detection is cached per document/page/file revision. Crops and answers
retain their existing caches. Multiple figures on one page share detected boxes.
A single crop/vision failure is reported without discarding other figure results.
This does not promise a one-minute deadline for arbitrarily large documents.

# Numbered figures (October 12)

Initial PDF text extraction now also saves caption text, printed figure number,
page number and caption coordinates in the persistent preparation checkpoint.
Both text and blocks come from the same PyMuPDF TextPage. There is no page rendering,
layout detection or Gemini vision call at this stage. The layout model is loaded
lazily when visual analysis is requested.

A search query naming Figure 1 (or Fig. 1) bypasses semantic retrieval. It selects
that exact caption within the backend-selected document, renders only its page,
and conservatively matches a detected image region to the caption. Ambiguous,
missing or duplicate matches fail visibly instead of substituting another figure.
Crops persist by document/page/caption; answers persist by that identity, vision
model and exact question. A different question reuses the crop but calls Gemini.
Old PDFs get caption metadata backfilled once from the original file without
re-embedding. If the original is missing, re-upload is required.

Limitations: scanned captions need OCR (not added here); unusual caption styles,
split captions and multi-panel detections can require an uploaded figure image.
General visual questions without a figure number still use the existing visual
search path. Exact matching uses layout heuristics, not a guarantee for every PDF.
No section-title inference or new figure embeddings are introduced.

# Gemini migration (current behavior)

All chat, tool selection, final answers, summaries and figure descriptions now use Gemini.
`GEMINI_MODEL` defaults to `gemini-3.5-flash-lite`; `GEMINI_SUMMARY_MODEL` and
`GEMINI_VISION_MODEL` optionally override it. Only `GEMINI_API_KEY` is needed
(`GOOGLE_API_KEY` is also accepted). Restart the app after changing providers.

API and MCP share a SQLite rolling minute budget: 15 requests and 240,000 input
units by default (`GEMINI_RPM_LIMIT`, `GEMINI_TPM_LIMIT`). Summary inputs use Google's
exact count; chat uses a deliberately conservative byte estimate and a separate
60,000-unit input budget (`GEMINI_CHAT_INPUT_BUDGET`). This avoids loading the old
GPT-OSS tokenizer. Figure inputs include an estimate, so provider quota errors can
still occur. This limiter coordinates local processes, not multiple machines or
other apps using the same project; daily quota is enforced by Google. Requests
fail visibly instead of falling back to Groq. No automatic provider retries.

Existing saved document preparation and summary cache keys are preserved, so the
provider switch does not force document re-embedding. Historical Groq queue code
is retained for old tests/imports, but its completion entry point routes to Gemini.
The sections below describe earlier changes and may mention the previous provider.

# Clarification conversations

A pending document request is stored in PostgreSQL as the original question plus
an optional single suggested document ID. A short title such as "Quantization"
can produce a named confirmation. "Yes" confirms only that single suggestion;
"no" clears it. A question offering A or B has no single suggestion, so yes does
not authorize either file or both. Full-title/ordinal selection can also resolve
an unfinished request. The agent receives the saved task when selection completes,
not just the user's confirmation word. UI history keeps the actual user reply.

"Cancel"/"forget that" drops the saved request. An explicit new action replaces
the old task. Suggestions are checked against currently attached documents before
execution. Matching remains rule-based English with title words; it is not a
complete natural-language resolver. Startup adds pending_document_request to
chat_sessions. No figure extraction or exact figure-number lookup was changed.

# Saved document focus

Each chat now stores `document_focus` as a list of document IDs in PostgreSQL.
Startup adds the column if needed. Attaching a PDF selects it; removing one
removes it from focus. Reattaching does not reorder uploads. Older chats with no
saved focus and multiple PDFs ask for selection rather than guessing.

Before model execution, the backend resolves full filenames, upload ordinals,
numbered document references, latest, previous, all PDFs, and plural follow-ups.
Previous means the upload immediately before the single focused PDF. Singular
paper/figure questions after a comparison ask which file. Resolution is currently
rule-based English, not a general natural-language intent classifier; unfamiliar
reference wording may need an explicit filename or upload number.

A request-local tool wrapper enforces the resolved session and document IDs,
overriding model-supplied document selectors. Comparisons execute the same tool
for each selected document and remap source labels to avoid citation collisions.
Uploads and explicit selections persist focus across restarts. Concurrent turns
in one chat use separate tool scopes but the last focus update wins; the UI
should submit turns sequentially. Clarifications are saved in chat history and
skip model/tool calls. This change does not implement exact figure-number lookup
or figure extraction validation; those remain separate work.

# October 8: first-request preparation and reuse

On the first summary or search request, only the selected document is prepared:
read and save canonical page text, embed text passages, then upsert them into
Chroma. Figure/table layout analysis is skipped unless search explicitly requests
`include_visuals=True`. That enrichment writes only figure/table records and has
its own saved completion flag, including when no figures or tables are found.
Normal questions still embed the *query*; that is not document re-indexing.

Preparation state and canonical summary inputs live in the local summary SQLite
cache. The original file location is retained; the pending-upload row is read
without consuming it. Readiness is marked only after successful vector storage.
Failed indexing retries reuse saved page text and deterministic chunk IDs/upserts.
An in-process lock prevents duplicate preparation inside the persistent MCP server;
multiple server processes would require a distributed lock. Existing legacy Chroma
documents are reused without re-embedding. If their original file location has
already been lost, new visual extraction requires re-upload. The saved canonical
summary input does not change after figure enrichment, so summaries stay reusable.
These caches contain document-derived text and currently have no automatic expiry.

Each question includes an upload-order document inventory. Search and overview
require explicit selection when multiple files are attached and validate that the
selected ID belongs to this chat. Cross-document search must be explicit. The
first request now includes text-indexing latency; subsequent requests reuse it.
Previous summaries may miss the cache once as canonical page text is established.
No changes were made to the three-turn conversation window or Groq answer routing.

The sections below describe previous iterations; this section takes precedence.

# How document summaries work

The app keeps Map-Reduce and the existing MCP server.

1. `document_summary.py` orders extracted text by document, page, and chunk.
   It packs labelled excerpts into sections of at most 2,600 tokens, preferring
   paragraph boundaries when a chunk needs splitting.
2. Map calls extract JSON with `main_points`, `facts_and_numbers`, and
   `limitations`. Each point has supporting source IDs such as `S15`.
3. If the combined notes exceed 1,800 tokens, group reduction merges token-sized
   batches. It repeats **while the notes are too large**. The configurable round
   limit is a safeguard, not the decision to reduce. Lack of progress also stops
   the process. No raw truncation or partial JSON is accepted.
4. Final synthesis turns those notes into a short readable overview with source
   references. Its output is also checked against the 1,800-token budget.
5. Full source records remain in the stored tool message and the API response.
   A model-only copy contains just the overview and short source references.
   The chat API collects sources only from the current user turn.

These sizes are starting settings for this application's 8,000 TPM allowance.
Summaries necessarily omit detail; specific follow-up questions should retrieve
original passages rather than treating an overview as the complete document.

# One queue for both GPT-OSS callers

`llm.py` and the LangChain adapter in `queued_model.py` both call `groq_queue.complete`.
The API process and MCP process must use the **same PostgreSQL database and scope**.
The queue creates `groq_budget` and `groq_requests` lazily on its first request.

Before sending, it estimates the complete prompt including chat formatting,
tools and schemas, adds a 256-token safety margin, and reserves the maximum
completion allowance (1,536 by default, including model reasoning).
A request larger than the configured allowance is rejected immediately.
Older whole conversation turns may be left out of model input so the current
turn fits. The original conversation stays in storage.

Queue admission uses a PostgreSQL row lock and FIFO tickets. One request per
scope is in flight at a time. Usage stays charged for 60 seconds after completion.
This conservative policy can be slower than the provider's own rolling allowance,
but prevents the two local processes from each spending the whole budget.
Actual usage replaces reservations after a successful call. Failed or crashed
requests retain reservations conservatively. Expired queued tickets are removed.

Groq's remaining-token and reset headers also restrict admission. A 429 establishes
a shared cooldown from `Retry-After`, then the request rejoins the queue (at most
two retries). SDK retries are disabled, so they cannot bypass the queue.
Other apps using the same provider allowance can still cause a 429. Local token
counts are estimates; provider accounting is authoritative.

Optional environment settings (defaults shown):

```text
GROQ_TPM_LIMIT=8000
GROQ_RATE_SCOPE=openai/gpt-oss-120b
GROQ_QUEUE_TIMEOUT=900
SUMMARY_MAX_REDUCTION_ROUNDS=12
```

The queue waits at most 900 seconds for each admission. Network calls have a
90-second timeout; abandoned in-flight reservations expire after 180 seconds.
The agent uses a worker thread so waiting does not block the API event loop.
If an HTTP caller disconnects, an already-started worker may finish and account
for its request. This is an admission queue, not a persistent background job system.
Streaming is explicitly unsupported by this adapter until it can account for a
stream safely. The separate image-caption model is not part of this GPT-OSS scope.

The queue does not make PDF extraction or summarization itself faster. Moving
ingestion to upload time and caching document summaries remain separate work.

# Checks

Offline logic tests (no Groq calls or model downloads):

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_context_pipeline.py -q -p no:cacheprovider
```

SQL integration tests use independent connection pools and a disposable schema;
they do not change chat tables or contact Groq:

```powershell
$env:RUN_QUEUE_DB_TESTS='1'
.\.venv\Scripts\python.exe -m pytest tests/test_groq_queue_postgres.py -q -p no:cacheprovider
```

Restart the backend after updating. Watch for `Summarizing section`, `Combining summaries`,
`Document summary ready`, and request `waiting/ready/complete` in the logs. Live model
quality and provider behavior still need an end-to-end document trial.

# Reading the terminal

The terminal shows time, severity, API or MCP, a short chat ID, the step, and a
short message. The agent logs its actual tool choice as it happens:

- `Searching the document for this question` means `search_documents` is running.
- `Summarizing the document` means `get_document_overview` is running.
- A normal question does not automatically run Map-Reduce. The agent decides which
  tool to use, and these messages make that decision visible.
- Completed sections, reduction groups and final summaries are saved locally in
  `data/summary_cache.sqlite3`. Repeating the same request reuses them. Failed or
  truncated outputs are never saved. Old tool calls are not replayed as live progress.

PDF page counts, section counts, model-loading steps and elapsed times are shown.
Long blocking steps and queue waits give updates about every 15 seconds. A queue
message distinguishes another running request, FIFO order, token shortage, and
a provider cooldown. It does not promise an exact completion time.

Successful HTTP access/polling messages and SDK connection/header dumps are hidden.
Warnings and errors remain visible. The terminal keeps errors short; full stack
traces and application debug logs go to `logs/api-<pid>.log` and
`logs/mcp-<pid>.log`. Each process's file rolls over at 5 MB, retaining two backups.
New processes create new files; older process files can be removed when no longer
needed. Existing `mcp_server.log` is left untouched and is no longer the active log.

Optional settings: `APP_LOG_LEVEL=DEBUG` for extra application detail in the terminal,
and `APP_LOG_DIR=logs` to choose a log directory. Provider debug dumps remain disabled.
All readable MCP logging goes to stderr so it cannot corrupt the stdout protocol.


## Recovery and document selection

An overview now defaults to the most recently attached document. Use `filename`
or `document_id` to choose an older one, or `all_documents=true` for an explicit
all-document overview. `page_start` and `page_end` select inclusive, one-based pages.
The tool checks that selected documents belong to the chat before reading the cache.
Page filtering happens after document preparation; the first request can still pay
for full PDF extraction and indexing.

Each summary step retries unusable output at most twice, asking for a shorter
answer and reserving more completion space (1536, 2048, then 2560 tokens). Each
attempt goes through the same shared rate limiter. Unknown citations and malformed
or truncated JSON are rejected. Reduction still stops based on token count, with
no-progress and maximum-round safeguards. If retries fail, completed steps remain
saved so another request can resume. No partial overview is presented as complete.

Cache keys include exact input/source labels, model and summary-format version;
final keys also include metadata and page selection through the filtered input.
The cache contains document-derived text: keep it private with the application data.
It currently has no automatic expiry; removing the cache file when the app is stopped
clears all saved summaries. This is a single-machine cache, not a distributed cache.
Changes to prompts/schema require bumping the version in `summary_cache.py`.

The persistent MCP connection now explicitly relays child stderr to the API
terminal; stdout remains reserved for MCP messages. Shutdown exits the lifespan
once. A full uncached large PDF cannot be guaranteed to finish in one minute with
the configured 8,000 TPM allowance. Cache reuse avoids repeated model calls, but
queue waits and first-time PDF preparation still take time.
# Gemini document summaries (current path)

`get_document_overview` now uses `gemini-3.5-flash-lite`. Groq still handles
chat/tool selection and normal questions. The agent can still make a Groq call
after the summary tool, so the complete chat response can wait on Groq's queue.

Set `GEMINI_API_KEY` in your local `.env`. Optional settings:
`GEMINI_SUMMARY_MODEL` (default `gemini-3.5-flash-lite`) and
`GEMINI_SUMMARY_INPUT_BUDGET` (default 60,000 input tokens, maximum 240,000).
This conservative request budget leaves room below the account's displayed
250,000 input TPM allowance. Changing models requires checking that model's quotas.

New text PDFs are read directly without consuming the pending search-indexing
job. Search still prepares embeddings/figures when needed. Already indexed
documents use their stored text. Empty selected pages report that OCR or vision
is needed; this route does not claim to analyze image contents. New uploads use
unique document-ID filenames so equal filenames cannot overwrite each other.

Gemini counts the whole prompt. If it fits, the service makes one generation
request. Otherwise it splits pages/chunks, summarizes groups and combines their
notes recursively. Exact counts decide whether splitting is necessary; depth
and no-progress guards prevent endless work. It never truncates the document
to fit. Each completed step is cached separately by model, prompt version and
source-labelled text. Gemini cache entries are separate from old Groq entries.

The persistent MCP process shares a conservative limiter (15 API calls and
240,000 input tokens per rolling minute, including count calls in call admission).
This is a single-process limiter, not a distributed quota system. Other apps or
multiple backend workers can consume the same project allowance. Provider quota
errors, including daily exhaustion, are reported without automatic retries or
silent fallback to Groq. No daily quota is inferred from the API key.

Restart the backend to activate changes. Logs show `Reading document text`,
`Summarizing with Gemini`, and `Reusing saved Gemini summary`. Cached requests
avoid Gemini calls. Citation IDs are validated, but factual accuracy still needs
review. Historical Groq implementation details follow below.

