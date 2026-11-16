# Notes on the submission

## Approach

The brief is about trust: people are unsure whether the answer they found is right and
current. So I built the system around one rule. An answer is shown only when code can
point to the exact passage it came from, and otherwise the system says the documents
do not answer the question.

I wrote the specification first (`SPEC.md`, the first commit), built the pipeline
without a database to get retrieval and refusals right, then added the worker, API and
UI, and finally measured it with a golden question set and tuned from those results.

The default system uses no model at all: keyword search (BM25) and answers copied from
the best passage. A model can be switched on to phrase the answer, but it cannot change
whether an answer is considered supported.

## Main decisions

**Split documents along their structure, not into fixed-size windows.** Policies are
made of clauses, bullets and table rows. Each of those becomes one passage with its
page, section heading and clause number or line. This makes the citation exact, and it
makes extractive answers good, because one passage is usually one rule. Table rows are
stored with their column headers so "P1 | 15 minutes" is still meaningful on its own.
For PDFs I read font size and weight to find headings rather than relying on numbering.

**Decide "supported" with two checks in code.** First, coverage: each question word is
weighed by how rare it is in the indexed documents, and the best passage must cover
55% of the total. A word that appears in no document counts double. Second, quotes:
the answerer must return the text it used, and the code confirms that text is in the
stored passage word for word. I chose coverage over a raw search score because scores
are not comparable between questions, while "how much of the question did we find" is,
and it can be explained to a user. The UI shows it for every answer.

**Postgres for everything: documents, passages, file bytes and the job queue.** Saving
an upload and queuing its job happen in one transaction. Jobs are claimed with
`FOR UPDATE SKIP LOCKED` and a lease, so a worker that dies simply stops renewing and
the job is claimed again. All passages of a new version are written, and the document
switched to it, in one transaction, so a crash can never leave a half-indexed document
and running a job twice is harmless. One store also means one thing to run and back up.

**Search in memory, reloaded only when documents change.** At 20 documents there are
about 170 passages. Scoring them in Python takes about a millisecond, so a search
engine would add operations without adding speed. A counter in the database tells the
API when to rebuild.

**A changed file replaces the old one only when the new one is ready.** The old version
keeps answering during indexing, and stays if the new upload turns out to be
unreadable. Identity is the filename; content is compared by SHA-256. The same content
under a different filename is treated as a duplicate rather than indexed twice, because
two copies of a policy would show up as two sources.

**Show whether an answer is current.** Each source shows the document's own version and
owner (read from its header), plus when it was uploaded and which upload it is.

**Server-sent events for live status.** One-way updates over plain HTTP, with automatic
reconnection in the browser. Simpler than WebSockets for this job.

**Interfaces for retrieval and answering.** Each is a small class chosen by an
environment variable. The support checks sit outside both, so swapping either one
cannot weaken the guarantee.

## What the evaluation showed

72 questions over 20 documents, split into a dev half that I tuned on and a test half.

| Split | Correct | Wrong passage | Declined wrongly | Unanswerable declined | Answered wrongly |
|---|---|---|---|---|---|
| dev | 26 of 26 | 0 | 0 | 6 of 6 | 0 |
| test | 23 of 30 | 0 | 7 | 8 of 10 | 2 |

Latency through the API at 20 documents: median 1.0 ms, 95th percentile 1.5 ms.

What I take from this:

- The first version answered 73% correctly with 8 confident mistakes. The fixes that
  mattered were ranking by coverage, treating "how long / how often / how many" as a
  request for a quantity, keeping "10,000" as one number, and weighing words that
  appear in no document more heavily.
- When it is wrong, it is mostly wrong in the safe direction. All 7 wrong refusals on
  the test split are wording gaps ("replaced" vs "refreshed", "fly" vs "flights",
  "crypto" vs "cryptocurrency"), and in all 7 the right passage was still shown under
  "closest passages".
- The 2 confident mistakes are the real limit of keyword matching. "How long is the
  probation period?" was answered from "sets probation goals within 30 days": every
  important word is there, but the passage is about something else. Only meaning-aware
  retrieval or a model reading the passage would catch that.
- The test figure is somewhat optimistic. I could see the test failures while tuning,
  and two general changes (irregular verb forms such as lost/lose, and treating
  allowed/prohibited as one group) were prompted by them. I did not add synonyms to fix
  individual test questions. All questions and 15 of the documents were written for
  this assignment, so they share my blind spots.

## What I left out, and why

- **Embeddings and hybrid search.** The brief requires the system to work with no
  model. The interface is there, but I spent the time on the refusal rule instead,
  which matters with or without embeddings.
- **OCR.** A scanned PDF with no text layer is rejected with a clear message.
- **Tables in PDFs.** Markdown tables are handled row by row. PDF tables are read as
  lines of text, which works for simple ones only.
- **A real model run.** The model answerer is tested with a simulated endpoint,
  including a model that invents a quote. I have not run it against a live model.
- **Private networking, single sign-on and remote Terraform state** in the Azure
  definition. Listed in `infra/README.md`.
- **Scaling beyond a small team.** The in-memory index and Postgres queue are right
  for tens to hundreds of documents, not tens of thousands.
- Everything the brief excluded: per-document permissions, multi-tenancy, accounts
  beyond a basic login, conversation memory, searching the document list.

## With another week

1. Add an embedding retriever and combine it with BM25, then use the existing harness
   to measure whether it fixes the wording-gap refusals without adding confident
   mistakes. Run it as a shadow comparison against the current retriever first.
2. Get a second person to write questions, and grow the unanswerable set, since my
   own questions are the weakest part of the evaluation.
3. Run the model answerer against Azure AI Foundry and measure how often its quotes
   are rejected, its latency and its cost per question.
4. Record every declined question so the team can see what their documents fail to
   answer. That is useful product feedback and a source of new test questions.
5. Flag conflicts: when two documents give different answers to the same question,
   show both rather than picking one.
6. Move file bytes to Blob Storage and add private networking in the Azure definition.

## How I used AI tools

I built this with Claude Code. I worked specification-first: the spec was written and
agreed before any code, and the build followed it. The AI wrote most of the code, the
test corpus and the first drafts of these documents, and ran the tests and evaluation.

My part was the decisions: the stack, the rule that support is decided in code, how
duplicates and replacements should behave, what to leave out, and when to stop tuning.
I treated its output the way I would treat a colleague's pull request, and relied on
checks I could see rather than its word: 37 tests, the golden-set evaluation, a live
kill of the worker, and screenshots of the UI.

The evaluation is where this mattered most. The first version looked right on the
eight sample questions and scored 73% on the wider set, with 8 confident mistakes.
Without the harness I would have submitted that.
