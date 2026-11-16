# Evaluation

- `golden.yaml`: 72 questions. Answerable ones name the file and a phrase the cited
  passage must contain. Unanswerable ones must be declined. Each has a `split`.
- `corpus/`: 15 fictional policy documents written for this assignment (12 Markdown,
  3 PDF). With the 5 supplied samples that makes the 20 documents the brief asks for.
  No personal or company data.
- `tools/make_pdfs.py`: regenerates the corpus PDFs.
- `run_eval.py`: the harness.

```bash
make eval        # score, print failures, apply release gates
make sweep       # threshold comparison on the dev split
make eval-live   # same questions through the running API, with latency
```

## Outcomes

| Question type | Outcome | Meaning |
|---|---|---|
| Answerable | correct | Right file and the passage contains the expected phrase. |
| Answerable | wrong passage | Answered from the wrong place. Harmful. |
| Answerable | false refusal | Declined although the answer exists. Annoying, not harmful. |
| Unanswerable | declined | Correct. |
| Unanswerable | false answer | Answered something the documents do not say. Harmful. |

## Release gates

`run_eval.py` exits with an error, and CI fails, if any of these is missed:

1. No harmful outcome on the dev split.
2. At least 80% of answerable questions correct across both splits.
3. At most 2 harmful outcomes across all 72 questions.

## How the threshold was chosen

`make sweep` on the dev split, after the matching rules were settled:

| Absent-word weight | Threshold | Correct (of 26) | False refusals | Harmful |
|---|---|---|---|---|
| 1.0 | 0.5 | 26 | 0 | 2 |
| 1.0 | 0.6 | 26 | 0 | 1 |
| 1.0 | 0.7 | 25 | 1 | 0 |
| 2.0 | 0.4 | 26 | 0 | 1 |
| 2.0 | 0.5 | 26 | 0 | 0 |
| 2.0 | 0.6 | 26 | 0 | 0 |
| 2.0 | 0.7 | 25 | 1 | 0 |

Doubling the weight of a question word found in no document removed the last harmful
dev outcome without losing a correct answer. With that weight, thresholds from 0.5 to
0.6 are clean on dev, and 0.55 is the middle of that range.
