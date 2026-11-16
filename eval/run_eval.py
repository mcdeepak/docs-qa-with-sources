"""Evaluation harness.

Offline (no database, used in CI):
    python eval/run_eval.py                 # score the configured threshold
    python eval/run_eval.py --sweep         # compare thresholds on the dev split
    python eval/run_eval.py --verbose       # list every failure

Against the running system (measures real latency through the API):
    python eval/run_eval.py --api http://localhost:8000

Outcomes per question:
    answerable    correct | wrong passage | declined
    unanswerable  declined | answered
"Wrong passage" and "answered" are the harmful ones: the system looked sure and was not.

The run fails (exit code 1) if a release gate below is missed, so it can gate CI.
"""
import argparse
import statistics
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, "/srv")

CORPUS_DIRS = [ROOT / "samples", ROOT / "eval" / "corpus"]

# Release gates. A change that breaks one of these should not ship.
GATE_DEV_HARMFUL = 0          # no confident mistakes on the tuning split
GATE_MIN_CORRECT = 0.80       # share of answerable questions answered correctly, all splits
GATE_MAX_HARMFUL = 2          # confident mistakes allowed across all 72 questions


def corpus_files() -> list[Path]:
    files = []
    for d in CORPUS_DIRS:
        files += [f for f in sorted(d.iterdir())
                  if f.suffix in (".pdf", ".md") and f.name != "sample-questions.md"]
    return files


def build_index(absent_term_weight=None):
    from app.extract import extract
    from app.index import Chunk, CorpusIndex

    chunks, cid = [], 0
    for doc_id, f in enumerate(corpus_files()):
        ex = extract(f.read_bytes(), f.name)
        for i, p in enumerate(ex.passages):
            cid += 1
            chunks.append(Chunk(cid, doc_id, f.name, ex.title, ex.doc_version, ex.doc_owner, 1,
                                "", i, p.kind, p.text, p.heading, p.page, p.clause, p.line))
    return CorpusIndex(chunks, absent_term_weight=absent_term_weight)


def judge(case: dict, result: dict) -> str:
    answerable = case.get("answerable", True)
    if not result["supported"]:
        return "declined" if not answerable else "false refusal"
    if not answerable:
        return "false answer"
    src = result["sources"][0]
    wanted = case["contains"] if isinstance(case["contains"], list) else [case["contains"]]
    ok = src["filename"] == case["file"] and any(w.lower() in src["passage"].lower() for w in wanted)
    return "correct" if ok else "wrong passage"


def summarise(rows: list[tuple[dict, dict, str]]) -> dict:
    answerable = [r for r in rows if r[0].get("answerable", True)]
    unanswerable = [r for r in rows if not r[0].get("answerable", True)]
    count = lambda rs, v: sum(1 for r in rs if r[2] == v)
    return {
        "answerable": len(answerable),
        "correct": count(answerable, "correct"),
        "wrong_passage": count(answerable, "wrong passage"),
        "false_refusal": count(answerable, "false refusal"),
        "unanswerable": len(unanswerable),
        "declined": count(unanswerable, "declined"),
        "false_answer": count(unanswerable, "false answer"),
    }


def line(name: str, s: dict) -> str:
    pct = lambda a, b: f"{a}/{b} ({100 * a / b:.0f}%)" if b else "-"
    return (f"{name:<6} correct {pct(s['correct'], s['answerable']):<13} "
            f"wrong passage {s['wrong_passage']:<3} false refusal {s['false_refusal']:<3} | "
            f"declined {pct(s['declined'], s['unanswerable']):<13} false answer {s['false_answer']}")


def run_offline(cases, threshold, absent_weight=None, index=None):
    from app import qa
    from app.answering import get_answerer
    from app.retrieval import get_retriever

    index = index or build_index(absent_weight)
    retriever, answerer = get_retriever("bm25"), get_answerer("extractive")
    rows = []
    for case in cases:
        result = qa.ask(case["q"], index=index, retriever=retriever, answerer=answerer, threshold=threshold)
        rows.append((case, result, judge(case, result)))
    return rows


def run_api(cases, base: str, username: str, password: str):
    import httpx

    with httpx.Client(base_url=base, auth=(username, password), timeout=30) as client:
        files = [("files", (f.name, f.read_bytes())) for f in corpus_files()]
        client.post("/api/documents", files=files).raise_for_status()
        deadline = time.time() + 120
        while time.time() < deadline:
            docs = client.get("/api/documents").json()["documents"]
            if docs and all(d["status"] in ("ready", "failed") for d in docs):
                break
            time.sleep(0.5)
        print(f"{sum(d['status'] == 'ready' for d in docs)} documents ready")
        rows, timings = [], []
        for _ in range(3):   # three passes for a steadier latency figure
            rows = []
            for case in cases:
                start = time.perf_counter()
                result = client.post("/api/ask", json={"question": case["q"]}).json()
                timings.append((time.perf_counter() - start) * 1000)
                rows.append((case, result, judge(case, result)))
    timings.sort()
    print(f"latency over {len(timings)} requests: median {statistics.median(timings):.1f} ms, "
          f"p95 {timings[int(len(timings) * 0.95) - 1]:.1f} ms, max {timings[-1]:.1f} ms")
    return rows


def right_passage_shown(case: dict, result: dict) -> bool:
    """For a declined answerable question: was the right passage still shown as closest?"""
    wanted = case["contains"] if isinstance(case["contains"], list) else [case["contains"]]
    return any(p["filename"] == case["file"] and any(w.lower() in p["passage"].lower() for w in wanted)
               for p in result["closest"])


def report(rows, verbose: bool) -> bool:
    for split in ("dev", "test"):
        print(line(split, summarise([r for r in rows if r[0]["split"] == split])))
    print(line("all", summarise(rows)))
    refusals = [r for r in rows if r[2] == "false refusal"]
    if refusals:
        shown = sum(right_passage_shown(c, r) for c, r, _ in refusals)
        print(f"\nOf {len(refusals)} false refusals, {shown} still showed the right passage under "
              f"\"closest passages\".")
    failures = [r for r in rows if r[2] not in ("correct", "declined")]
    if verbose and failures:
        print("\nFailures:")
        for case, result, verdict in failures:
            best = (result["sources"] or result["closest"] or [{}])[0]
            print(f"  [{case['split']}] {verdict}: {case['q']}")
            print(f"      coverage {best.get('coverage')} missing {best.get('missing_terms')} "
                  f"-> {best.get('filename')}: {str(best.get('passage'))[:90]}")

    harmful = lambda rs: sum(1 for r in rs if r[2] in ("wrong passage", "false answer"))
    total = summarise(rows)
    gates = [
        ("no confident mistakes on dev", harmful([r for r in rows if r[0]["split"] == "dev"]) <= GATE_DEV_HARMFUL),
        (f"at least {GATE_MIN_CORRECT:.0%} of answerable questions correct",
         total["correct"] / max(total["answerable"], 1) >= GATE_MIN_CORRECT),
        (f"at most {GATE_MAX_HARMFUL} confident mistakes overall", harmful(rows) <= GATE_MAX_HARMFUL),
    ]
    print("\nRelease gates:")
    for name, ok in gates:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    return all(ok for _, ok in gates)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", help="base URL of a running system, e.g. http://localhost:8000")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="admin")
    parser.add_argument("--sweep", action="store_true", help="compare thresholds on the dev split")
    parser.add_argument("--threshold", type=float)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    cases = yaml.safe_load((ROOT / "eval" / "golden.yaml").read_text())

    if args.api:
        return 0 if report(run_api(cases, args.api, args.username, args.password), args.verbose) else 1

    from app.config import settings

    if args.sweep:
        dev = [c for c in cases if c["split"] == "dev"]
        print("Dev split only. Harmful = wrong passage + false answer.\n")
        print(f"{'absent wt':<10}{'threshold':<10}{'correct':<9}{'false refusal':<15}{'harmful':<8}")
        for absent in (1.0, 2.0, 3.0):
            index = build_index(absent)
            for threshold in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9):
                s = summarise(run_offline(dev, threshold, index=index))
                print(f"{absent:<10}{threshold:<10}{s['correct']:<9}{s['false_refusal']:<15}"
                      f"{s['wrong_passage'] + s['false_answer']:<8}")
        return 0

    threshold = args.threshold if args.threshold is not None else settings.support_threshold
    print(f"threshold {threshold}, absent term weight {settings.absent_term_weight}, "
          f"{len(corpus_files())} documents, {len(cases)} questions\n")
    return 0 if report(run_offline(cases, threshold), args.verbose) else 1


if __name__ == "__main__":
    sys.exit(main())
