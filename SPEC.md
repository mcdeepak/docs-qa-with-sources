# Specification: Document Q&A with Sources

Status: written before implementation and kept up to date. Changes to behaviour are made here first.

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
- `document_versions(id, document_id, version_no, sha256, content, status, error, title, doc_version, doc_owner, chunk_count, uploaded_at, indexed_at)`
- `chunks(id, version_id, ordinal, kind, text, heading, page, clause, line)`
- `jobs(id, version_id, state, attempts, run_after, leased_until, worker, last_error)`
- `index_state(generation)`: bumped whenever the searchable set changes.

File bytes are stored in `document_versions.content` so that saving a file and queuing
its job commit together.

## Behaviour

### Upload and versions
- A document's identity is its filename. Its content identity is the SHA-256 of its bytes.
- Same filename, same hash: no new version, no new job. The existing document is returned.
- Same hash under a different filename: nothing is created. The response names the
  document that already holds the content.
- Same filename, new hash: a new version is created and queued. The old version keeps
  serving answers until the new one is ready, then the active pointer moves and the old
  chunks are deleted in one transaction.
- Only `.pdf` and `.md` are accepted. A PDF with no extractable text fails with a clear error.

### Indexing worker
- The worker claims one job with `FOR UPDATE SKIP LOCKED` and sets a lease it renews
  while working.
- A job whose lease has expired is claimed again. Indexing a version is idempotent:
  its chunks are deleted and rewritten in one transaction. A job that runs again after
  it already finished does nothing, and only versions older than the one being indexed
  are ever retired.
- After 3 failed attempts the version is marked `failed` with the error shown in the UI.
- Stages reported: `queued`, `extracting`, `chunking`, `indexing`, `ready`, `failed`.

### Chunking and location
- Markdown: split by heading, then by bullet, paragraph or table row. A table row is
  stored with its column headers.
- PDF: split by page, then by heading and numbered clause where present, else by paragraph.
- Each chunk records page (PDF), heading path and clause number. This is the "location".

### Retrieval (swappable)
- Interface: `Retriever.search(index, question, k) -> list[Hit]`.
- Default `bm25`: BM25 over active chunks, with lowercasing, stop-word removal, stemming
  and a small configurable synonym table. Heading text is indexed at lower weight.
- Selected with `RETRIEVER`.

### Answering (swappable)
- Interface: `Answerer.answer(question, hits) -> Draft(text, quotes)`.
- Default `extractive`: the top passage verbatim, or its best-matching sentence when the
  passage is longer than 300 characters.
- Optional `llm`: any OpenAI-compatible endpoint. Must return quotes. Off by default.
- Selected with `ANSWERER`.

### Support decision (always in code, after any answerer)
1. Coverage gate: the rarity-weighted share of the question's words found in a passage
   must reach `SUPPORT_THRESHOLD` (0.55).
   - A word found in no document counts `ABSENT_TERM_WEIGHT` (2.0) times the rarest word.
   - A word in the passage text counts in full, in its section heading only half, in
     the document title only a quarter. The exception is a document's owner/version
     line, where the title's words count in full when the question asks for something
     that line states.
   - A quantity word after "how" (long, often, many) is satisfied by a passage that
     contains a number or a frequency word.
   - In "who <verb>..." and "how do I <verb>..." questions, the first content word after
     the opener is required: a passage without it (in text or heading) is rejected.
   - For two content words that are adjacent in the question, a passage that has the
     first but not the second gets half credit for the first.
   - A "how long/often/quickly" question is rejected unless the passage has a number
     with a unit or a frequency word; a bare number is enough only for "how many/much".
   - A negative question (not, no, never, without, n't) requires a passage that states a
     negative or an exception and contains the first content word after the "not". An "un..." word matches its positive form only in such
     a passage; against a positive passage it is a contradiction and is rejected.
   - A question with exactly one topic word and no quantity word is declined as too
     general, unless that word is typed as an acronym or contains a digit.
   - When the question has two or more topic words, a passage with fewer than two of
     them (in its text or heading) is rejected. Numbers and units of time are not topic
     words; a number weighs the same as a quantity word.
   - The required word of a "who <verb>" question matches only itself or a form of the
     same word, not looser synonyms.
   - Candidate passages are ordered by coverage, then by retrieval score.
2. Quote gate: every quote must occur verbatim (whitespace-normalised) in the stored
   passage it cites, be at least 20 characters or the whole passage, and that passage
   must have passed the coverage gate. Quotes that fail are dropped. No quotes left
   means not supported.
3. Wording gate: if the answer text contains a number that is not in its quotes, or
   fewer than 60% of its content words come from the quotes or the question, the first
   quote is shown as the answer instead.

When the answer is not supported, the closest passages are returned separately and
labelled as insufficient.

### API
- `POST /api/login`
- `POST /api/documents` (multipart) -> document, version, status
- `GET /api/documents` -> list with status
- `GET /api/documents/events` -> server-sent events of status changes
- `DELETE /api/documents/{id}`
- `POST /api/ask {question}` -> `{supported, answer, reason, sources[{document, document_version, location, passage, quote, highlight, coverage}], related, closest, took_ms}`
- Login by session cookie (browser) or HTTP Basic (scripts).

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
- Each question is in a `dev` split (used for tuning) or a `test` split (kept apart).
- Outcomes: correct, wrong passage, false refusal, declined, false answer. Latency is
  measured through the API.
- The support threshold is set from the dev results. Release gates in the harness fail
  CI if confident mistakes appear on dev or accuracy drops below 80%.

## Azure (defined, not deployed)
Terraform: Container Apps (api, worker), PostgreSQL Flexible Server, Container
Registry, Key Vault, managed identity, Log Analytics. Azure OpenAI behind
`enable_ai = false`. Reasoning in `infra/README.md`.

## Out of scope
Per-document permissions, multi-tenancy, accounts beyond a basic login, conversation
memory, document list search, OCR for scanned PDFs, embedding and hybrid retrieval
(interface only).
