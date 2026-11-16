# Specification: Document Q&A with Sources

Status: draft, written before implementation. Changes to behaviour are made here first.

## Problem

A small team keeps policies and procedures as PDF and Markdown files. People waste time
searching them and are unsure whether what they found is current. They need to ask a
question and get an answer they can verify against the source.

## Principles

1. An answer is only shown if code can prove it is supported. Otherwise the system says
   "the documents do not answer this".
2. The system runs with no AI model, API key or internet access. Any model is optional
   and switched on by configuration.
3. Every answer shows the exact passage, the document, its version and its location.

## Stack

| Part | Choice | Reason |
|---|---|---|
| API | Python, FastAPI | Small, typed, easy to change live |
| Worker | Same codebase, separate process | Indexing never blocks requests |
| Store and queue | Postgres | Transactional version swap, `SKIP LOCKED` queue, maps to Azure |
| UI | React + TypeScript, served by the API | Live status and highlighted passages |
| Start | `docker compose up` | One command: db, api, worker |

## Data model

- `documents(id, filename unique, active_version_id, created_at)`
- `document_versions(id, document_id, sha256, version_no, status, error, stage, meta, uploaded_at)`
- `chunks(id, version_id, ordinal, text, heading_path, page, clause, kind)`
- `jobs(id, version_id, state, attempts, leased_until, last_error, created_at)`

## Behaviour

### Upload and versions
- A document's identity is its filename. Its content identity is the SHA-256 of its bytes.
- Same filename, same hash: no new version, no new job. The existing document is returned.
- Same filename, new hash: a new version is created and queued. The old version keeps
  serving answers until the new one is ready, then the active pointer moves and the old
  chunks are deleted in one transaction.
- Only `.pdf` and `.md` are accepted. A PDF with no extractable text fails with a clear error.

### Indexing worker
- The worker claims one job with `FOR UPDATE SKIP LOCKED` and sets a lease it renews
  while working.
- A job whose lease has expired is claimed again. Indexing a version is idempotent:
  its chunks are deleted and rewritten in one transaction.
- After 3 failed attempts the version is marked `failed` with the error shown in the UI.
- Stages reported: `queued`, `extracting`, `chunking`, `indexing`, `ready`, `failed`.

### Chunking and location
- Markdown: split by heading, then by bullet, paragraph or table row. A table row is
  stored with its column headers.
- PDF: split by page, then by heading and numbered clause where present, else by paragraph.
- Each chunk records page (PDF), heading path and clause number. This is the "location".

### Retrieval (swappable)
- Interface: `Retriever.search(question, k) -> list[ScoredChunk]`.
- Default `bm25`: BM25 over active chunks, with lowercasing, stop-word removal, stemming
  and a small configurable synonym table. Heading text is indexed at lower weight.
- Selected with `RETRIEVER`.

### Answering (swappable)
- Interface: `Answerer.answer(question, chunks) -> Draft(text, quotes)`.
- Default `extractive`: the best-matching sentence or table row from the top chunk, verbatim.
- Optional `llm`: any OpenAI-compatible endpoint. Must return quotes. Off by default.
- Selected with `ANSWERER`.

### Support decision (always in code, after any answerer)
1. Coverage gate: the rarity-weighted share of the question's informative terms found in
   the top passage must reach `SUPPORT_THRESHOLD`. Terms absent from the whole corpus
   count at maximum weight.
2. Quote gate: every quote must occur verbatim (whitespace-normalised) in the stored
   chunk it cites. Quotes that fail are dropped. No quotes left means not supported.

### API
- `POST /api/login`
- `POST /api/documents` (multipart) -> document, version, status
- `GET /api/documents` -> list with status
- `GET /api/documents/events` -> server-sent events of status changes
- `POST /api/ask {question}` -> `{supported, answer, sources[{document, version, location, passage, highlight}], timings}`

### UI
- Documents page: upload, list, live status per document.
- Ask page: question box, answer, source passages with the answering text highlighted,
  document name, version and location. A clear "not supported" state.

## Non-functional
- `POST /api/ask` p95 under 2 seconds with 20 documents indexed. Measured by the eval script.
- Uploads return before indexing starts. The UI stays responsive during indexing.
- Basic login from environment variables.

## Evaluation
- `eval/golden.yaml`: questions with expected document and location, or expected refusal.
- Metrics: hit@1, hit@3, refusal precision and recall, quote validity, p95 latency.
- The support threshold is set from these results and the results are reported in the note.

## Azure (defined, not deployed)
Terraform: Container Apps (api, worker), PostgreSQL Flexible Server, Blob Storage,
Key Vault, managed identity, Log Analytics. Azure OpenAI behind `enable_ai = false`.

## Out of scope
Per-document permissions, multi-tenancy, accounts beyond a basic login, conversation
memory, document list search, OCR for scanned PDFs, embedding and hybrid retrieval
(interface only).
