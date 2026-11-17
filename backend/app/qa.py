"""The question pipeline: retrieve, check coverage, answer, check quotes."""
import time
from functools import lru_cache

from . import index as corpus
from .answering import get_answerer
from .config import settings
from .retrieval import get_retriever
from .support import ScoredHit, check_quotes, grounded, passing, score_hits
from .text import query_terms

NOT_SUPPORTED = "The indexed documents do not contain an answer to this question."


@lru_cache(maxsize=1)
def _retriever():
    return get_retriever(settings.retriever)


@lru_cache(maxsize=1)
def _answerer():
    return get_answerer(settings.answerer)


def _location(chunk) -> dict:
    parts = []
    if chunk.page:
        parts.append(f"page {chunk.page}")
    if chunk.heading:
        parts.append(f"section “{chunk.heading}”")
    if chunk.clause:
        parts.append(f"clause {chunk.clause}")
    if chunk.kind == "table_row":
        parts.append("table row")
    if chunk.line:
        parts.append(f"line {chunk.line}")
    return {"page": chunk.page, "section": chunk.heading or None, "clause": chunk.clause,
            "line": chunk.line, "label": ", ".join(parts)}


def _passage(scored: ScoredHit) -> dict:
    c = scored.hit.chunk
    return {
        "document": c.title,
        "filename": c.filename,
        "document_version": c.doc_version,
        "document_owner": c.doc_owner,
        "upload_no": c.version_no,
        "uploaded_at": c.uploaded_at,
        "location": _location(c),
        "passage": c.text,
        "score": round(scored.hit.score, 3),
        "coverage": round(scored.coverage, 3),
        "matched_terms": scored.matched,
        "missing_terms": scored.missing,
        "required_missing": scored.required_missing,
    }


def ask(question: str, index=None, retriever=None, answerer=None, threshold=None) -> dict:
    started = time.perf_counter()
    index = index or corpus.current()
    retriever = retriever or _retriever()
    answerer = answerer or _answerer()
    threshold = settings.support_threshold if threshold is None else threshold

    result = {
        "question": question, "supported": False, "answer": NOT_SUPPORTED, "reason": "",
        "sources": [], "related": [], "closest": [], "retriever": retriever.name, "answerer": answerer.name,
        "threshold": threshold, "documents_indexed": len({c.document_id for c in index.chunks}),
    }

    def done(reason: str) -> dict:
        result["reason"] = reason
        result["took_ms"] = round((time.perf_counter() - started) * 1000, 1)
        return result

    terms = query_terms(question)
    if not terms:
        return done("The question has no searchable words.")
    if not index.n:
        return done("No documents are indexed yet.")

    hits = retriever.search(index, question, settings.top_k)
    scored = score_hits(index, terms, hits)
    allowed = passing(scored, threshold)
    if not allowed:
        # Shown as "closest passages" so a person can see why we declined.
        result["closest"] = [_passage(s) for s in scored[:2]]
        best = scored[0] if scored else None
        if best and best.coverage >= threshold and best.required_missing:
            word = best.required_missing[0]
            return done(f"No passage says anything about \u201c{word}\u201d, which the question asks about.")
        return done("No passage covers enough of the question.")

    draft = answerer.answer(question, [s.hit for s in allowed])
    result["answerer"] = draft.answerer or answerer.name
    decision = check_quotes(draft, allowed)
    if not decision.supported:
        result["closest"] = [_passage(s) for s in allowed[:2]]
        return done(decision.reason)

    result["supported"] = True
    result["answer"] = draft.text
    # Wording check: an answer the quotes do not back up is replaced by the quote.
    if not grounded(draft.text, [src.quote for src in decision.sources], question):
        result["answer"] = decision.sources[0].quote
        result["answerer"] += " (wording replaced by the quote)"
    result["sources"] = [
        {**_passage(src.scored), "quote": src.quote, "highlight": [src.start, src.end]}
        for src in decision.sources
    ]
    # Other passages that also passed the coverage check, shown as further reading.
    cited = {src.scored.hit.chunk.id for src in decision.sources}
    result["related"] = [_passage(s) for s in allowed if s.hit.chunk.id not in cited][:2]
    return done("ok")
