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

**Decide "supported" with checks in code.** First, coverage: each question word is
weighed by how rare it is in the indexed documents, and the best passage must cover
55% of the total. A word that appears in no document counts double, and a word found
only in a heading or title counts for less than one found in the passage. For "who
approves..." and "how do I book..." questions the passage must contain the action word
itself. A negative question ("when is MFA not required?") needs a passage that states
a negative or an exception, and "how long" needs a number with a unit. Second, quotes: the answerer must return the text it used, and the code
confirms that text is in the stored passage word for word. Third, wording: a
model-written answer that contains a number its quotes do not is replaced by the quote.
I chose coverage over a raw search score because scores are not comparable between
questions, while "how much of the question did we find" is, and it can be explained to
a user. The UI shows it for every answer.

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

98 questions over 20 documents, in three parts: a dev split I tuned on, a test split,
and a fresh split written after an independent review and before the fixes it led to.

| Split | Correct | Wrong passage | Declined wrongly | Unanswerable declined | Answered wrongly |
|---|---|---|---|---|---|
| dev (tuned on) | 26 of 26 | 0 | 0 | 6 of 6 | 0 |
| test | 27 of 30 | 0 | 3 | 10 of 10 | 0 |
| fresh | 11 of 14 | 0 | 3 | 12 of 12 | 0 |

Latency through the API at 20 documents: median 0.9 ms, 95th percentile 1.1 ms.

What I take from this:

- The first version answered 73% correctly with 8 confident mistakes on the original
  72 questions. The fixes that mattered were ranking by coverage, treating "how long /
  how often / how many" as a request for a quantity, keeping "10,000" as one number,
  and weighing words that appear in no document more heavily.
- A second round came from an independent review of the finished code. It found that
  naming a document in the question was enough to get an answer from it ("Who approved
  the data retention policy?" was answered with an unrelated clause), because title
  words counted in full. It also found extraction bugs that the sample files did not
  trigger: table-like lines dropped as page footers, and "1.5 times the hourly rate"
  read as clause 1.5. I wrote the fresh questions before fixing anything, so I could
  measure the change. Wrong answers on the fresh split went from 3 to 0 and on the test
  split from 2 to 0.
- A third round was adversarial. I had a different AI model look for inputs that
  would break it, then tried about 120 odd questions against the 20 documents. Every
  case was reproduced before anything was changed. About ten were wrong answers shown
  as supported: "When is MFA not required?" answered with the rule that it is
  required, "How was your day?" answered with "Backups are retained for 35 days", and
  "Do passwords expire?" answered from a passage about leave days. Each became a
  general rule with a test: negative questions, numbers without a unit, one-word
  questions, and passages that match only one of the things asked about. It also
  showed my evaluation graded the whole passage rather than the sentence displayed.
  It now grades the displayed quote, and the figures did not change.
- The honest figure for wording the system has not seen is the fresh split: 11 of 14
  answered, and that did not move with the fixes. The 3 it declined are wording gaps
  ("How much can I spend on a taxi?" against a table row that says "Limit"). In every
  wrong refusal across all splits, the right passage was still shown under "closest
  passages".
- No wrong answers on 98 questions does not mean none are possible, and that round
  proved it. The rules are about words, not meaning. Three cases I know of and left:
  "How often are laptops backed up?" is answered with "File shares are backed up
  nightly"; "Does Payroll approve badge requests?" is answered from "Payroll approves
  invoices. Facilities handles badge requests."; and when two documents disagree, one
  is shown as the answer and the other only as a related passage. All questions and
  15 of the documents were written for this assignment, so they share my blind spots.
- Synonyms are the riskiest knob. Adding "responsible" to the owner group made
  "response" match "owner" through word stems and broke the P1 question. The release
  gate in the evaluation caught it before it was committed.

## What I left out, and why

- **Embeddings and hybrid search.** The brief requires the system to work with no
  model. The interface is there, but I spent the time on the refusal rule instead,
  which matters with or without embeddings.
- **OCR.** A scanned PDF with no text layer is rejected with a clear message.
- **Tables and columns in PDFs.** Markdown tables are handled row by row. PDF tables
  are read as lines of text, which works for simple ones only, and a two-column page
  layout is read across both columns and comes out scrambled.
- **Code blocks.** Text inside a fenced code block in Markdown is not indexed, so a
  configuration example cannot be cited as policy. A procedure whose only statement
  of a step is inside a code block will not be found.
- **Numbers in words.** "14 days" in a question does not match "fourteen days" in a
  document.
- **Re-indexing after a rule change.** Documents keep the passages they were split into
  when uploaded. If the extraction rules change, a document has to be removed and
  uploaded again to pick them up.
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
checks I could see rather than its word: 70 tests, the golden-set evaluation, a live
kill of the worker, and screenshots of the UI.

Before submitting I had the finished code reviewed twice by AI with no knowledge of
how it was built: a second Claude session, then ChatGPT, which I asked for inputs
that would break it. I had every finding reproduced before accepting it. Some were
real and were fixed with regression tests, some were already handled, and one
contradicted the brief.

The evaluation is where this mattered most. The first version looked right on the
eight sample questions and scored 73% on the wider set, with 8 confident mistakes.
Without the harness I would have submitted that.
