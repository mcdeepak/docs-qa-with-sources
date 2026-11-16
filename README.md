# Document Q&A with Sources

Ask questions about a team's policy documents (PDF and Markdown) and get an answer
together with the exact passage it came from, the document, its version and its
location. If the documents do not support an answer, the system says so.

It runs with no AI model, no API key and no account. A model can be switched on by
configuration, and its answers go through the same checks.

## Start it

You need Docker. Then, from this folder:

```bash
docker compose up --build
```

Open **http://localhost:8000** and sign in with `admin` / `admin`.

The first start downloads base images and packages, so it needs internet once. After
that the system runs offline. If port 8000 is taken: `APP_PORT=8080 docker compose up --build`.

To try it: open **Documents**, drop in the files from `samples/`, watch them go from
*queued* to *ready*, then open **Ask** and try the questions in `samples/sample-questions.md`.

## What was built

| # | Requirement | How it is met | Where |
|---|---|---|---|
| 01 | Upload page with live status | Drag-and-drop upload. Status is pushed to the browser over server-sent events. | `frontend/src/DocumentsPage.tsx`, `GET /api/documents/events` |
| 02 | Answer with exact passages and location | The answer is quoted from one passage, highlighted in place, with document, document version, page, section, clause or line. | `backend/app/qa.py`, `frontend/src/AskPage.tsx` |
| 03 | Says so when unsupported, decided in code | Two checks in code: the passage must cover enough of the question, and every quote must exist word for word in the stored passage. | `backend/app/support.py` |
| 04 | Background worker, no duplicates, clean replacement, crash-safe | Separate worker process. Jobs in Postgres with leases. Content hashing. One-transaction version swap. | `backend/app/worker.py`, `backend/app/documents.py` |
| 05 | Swappable retrieval and answering, runs with no model | `Retriever` and `Answerer` interfaces chosen by environment variable. Default is BM25 keyword search with extractive answers. | `backend/app/retrieval/`, `backend/app/answering/` |
| 06 | Under 2 seconds at 20 documents, page never freezes | About 1 ms per question at 20 documents (measured below). Uploads return before indexing starts. | `eval/run_eval.py --api` |
| 07 | Simple API | Five endpoints, usable with curl. | `backend/app/main.py` |
| 08 | One command | `docker compose up --build` | `docker-compose.yml` |
| 09 | Azure infrastructure as code | Terraform, validated but not deployed, with the reasoning written down. | `infra/` |

## How "supported" is decided

No model takes part in this decision.

1. **Coverage check.** Each word of the question is weighed by how rare it is across
   the indexed documents. A word that appears in no document weighs the most. The best
   passage must cover at least 55% of that total weight. "What is the policy on
   parental leave?" fails here: plenty of passages mention "policy", none mention
   "parental".
2. **Quote check.** Whatever wrote the answer must return the exact text it relied on.
   The code looks that text up in the stored passage. A quote that is not there word
   for word is discarded, and an answer with no surviving quote is replaced by "the
   documents do not contain an answer". This is what makes a model safe to plug in.

When the system declines, it still shows the closest passages it found, labelled as
not enough to answer, so a person can judge for themselves.

Every answer has a "How this was decided" panel showing the coverage figure and which
question words were and were not found.

## API

All endpoints except `/api/health` need a login. Scripts can use HTTP Basic.

```bash
# Upload one or more files (returns immediately; indexing runs in the worker)
curl -u admin:admin -F files=@samples/data-retention-policy.pdf \
     -F files=@samples/incident-response-procedure.md http://localhost:8000/api/documents

# List documents and their status
curl -u admin:admin http://localhost:8000/api/documents

# Ask a question
curl -u admin:admin -H 'Content-Type: application/json' \
     -d '{"question": "How long are backups kept?"}' http://localhost:8000/api/ask

# Live status stream (server-sent events)
curl -u admin:admin -N http://localhost:8000/api/documents/events

# Remove a document
curl -u admin:admin -X DELETE http://localhost:8000/api/documents/1
```

Interactive API documentation is at http://localhost:8000/docs.

An answer looks like this (shortened):

```json
{
  "supported": true,
  "answer": "Backups are retained for 35 days.",
  "sources": [{
    "document": "Data Retention Policy", "document_version": "1.4",
    "filename": "data-retention-policy.pdf", "upload_no": 1,
    "location": {"page": 1, "section": "2. Retention periods", "clause": "2.3",
                 "label": "page 1, section “2. Retention periods”, clause 2.3"},
    "passage": "Backups are retained for 35 days.",
    "quote": "Backups are retained for 35 days.", "highlight": [0, 33],
    "coverage": 1.0
  }],
  "took_ms": 1.1
}
```

## Upload rules

- **Same file again:** nothing is created. The response says it is a duplicate.
- **Same content under a different filename:** nothing is created. The response names
  the document that already holds it.
- **Same filename, changed content:** a new version is queued. The old version keeps
  answering until the new one is ready, then it is swapped out in one transaction and
  its passages are deleted.
- **Unreadable file** (corrupt, password protected, scanned with no text): marked
  *failed* with the reason. If it was a replacement, the previous version stays live.

## Seeing crash recovery

```bash
INDEX_DELAY_SECONDS=4 WORKER_LEASE_SECONDS=8 docker compose up -d worker   # slow the worker down
# upload a file in the UI, then while it shows "extracting":
docker compose kill -s KILL worker
docker compose up -d worker
```

The document stays at *extracting* until the dead worker's lease runs out (8 seconds
here), then the job is picked up again and finishes. The Documents page shows
"Attempt 2 (recovered after an interruption)". The same scenario is covered by
`backend/tests/test_indexing.py`.

## Configuration

Copy `.env.example` to `.env` to change anything. Every setting has a working default.

| Setting | Default | Meaning |
|---|---|---|
| `RETRIEVER` | `bm25` | Which retriever to use. |
| `ANSWERER` | `extractive` | `extractive` (no model) or `llm`. |
| `SUPPORT_THRESHOLD` | `0.55` | Share of the question a passage must cover. Higher means more refusals. |
| `ABSENT_TERM_WEIGHT` | `2.0` | How heavily a question word found in no document counts against a passage. |
| `USE_SYNONYMS` | `true` | Use the small synonym table in `backend/app/synonyms.txt`. |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | empty | Any OpenAI-compatible endpoint, used only when `ANSWERER=llm`. |
| `APP_USERNAME`, `APP_PASSWORD` | `admin` | The shared login. |

### Using a model (optional)

```bash
ANSWERER=llm LLM_BASE_URL=http://host.docker.internal:11434/v1 LLM_MODEL=llama3.2 docker compose up --build
```

The model only phrases the answer. Its quotes are checked in code, and if the call
fails or times out the extractive answer is used instead. This path is covered by
tests with a simulated model; it has not been run against a real model endpoint.

## Tests and evaluation

```bash
make test        # 37 unit and integration tests
make eval        # golden-set evaluation with release gates
make sweep       # compare support thresholds on the dev split
make eval-live   # the same questions through the running API, with latency
```

The golden set is 72 questions over 20 documents (the 5 samples plus 15 written for
this assignment, in `eval/corpus/`). It is split into a dev half used for tuning and a
test half kept apart. Results with the default settings:

| Split | Answerable: correct | Wrong passage | Declined wrongly | Unanswerable: declined | Answered wrongly |
|---|---|---|---|---|---|
| dev | 26 of 26 | 0 | 0 | 6 of 6 | 0 |
| test | 23 of 30 | 0 | 7 | 8 of 10 | 2 |

All 7 wrong refusals still showed the right passage under "closest passages".
Latency through the API with 20 documents: median 1.0 ms, 95th percentile 1.5 ms,
slowest 36 ms, over 216 requests on a laptop.

`NOTES.md` discusses what these numbers do and do not show.

## Where to change things

| To do this | Edit |
|---|---|
| Make refusals stricter or looser | `SUPPORT_THRESHOLD` in `.env` |
| Teach it that two words mean the same | `backend/app/synonyms.txt` |
| Accept a new file type | add an extractor to `EXTRACTORS` in `backend/app/extract.py` |
| Add a retriever or answerer | add a class and register it in `backend/app/retrieval/__init__.py` or `backend/app/answering/__init__.py` |
| Change how passages are split | `backend/app/extract.py` |

## Layout

```
backend/app/        API, worker, extraction, retrieval, answering, support checks
backend/tests/      unit and integration tests
frontend/src/       React + TypeScript UI
eval/               golden questions, 15-document corpus, evaluation harness
infra/              Azure definition in Terraform, with explanation
samples/            the documents and questions supplied with the assignment
SPEC.md             the specification, written before the code
NOTES.md            approach, decisions, what was left out, use of AI tools
```
