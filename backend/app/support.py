"""Decides in code whether an answer is supported. No model takes part.

Two checks, both must pass:

1. Coverage. The passage must contain enough of what the question asks about.
   Each question term is weighed by how rare it is in the documents; a term found
   in no document weighs the most. A match in the passage counts in full, a match
   only in its heading or document title counts for less. This is what stops
   "parental leave" being answered from a passage that merely mentions "policy".
   For "who approves..." and "how do I book..." questions the passage must also
   contain the action word itself.

2. Quote. Every quote the answerer returns must appear word for word in the stored
   passage it cites, be more than a fragment, and that passage must have passed
   check 1. An answerer that invents or paraphrases a quote loses it. With no quotes
   left, there is no answer.

3. Wording. An answer written by a model is compared with its quotes. If it contains
   a number the quotes do not, or mostly words the quotes do not, the quote itself is
   shown as the answer instead.
"""
import re
from dataclasses import dataclass, field

from .answering import Draft
from .config import settings
from .index import CorpusIndex
from .retrieval import Hit
from .text import QueryTerm, analyse, normalise_ws

MIN_QUOTE_CHARS = 20        # a shorter quote must be the whole passage
MIN_ANSWER_OVERLAP = 0.6    # share of the answer's content words that must be in the quotes


@dataclass
class ScoredHit:
    hit: Hit
    coverage: float
    matched: list[str]
    missing: list[str]
    required_missing: list[str] = field(default_factory=list)


@dataclass
class Source:
    scored: ScoredHit
    quote: str
    start: int   # position of the quote inside the passage text
    end: int


@dataclass
class Decision:
    supported: bool
    reason: str
    sources: list[Source] = field(default_factory=list)


def score_hits(index: CorpusIndex, terms: list[QueryTerm], hits: list[Hit]) -> list[ScoredHit]:
    scored = []
    for h in hits:
        c = index.coverage(terms, h.position)
        scored.append(ScoredHit(h, c.value, c.matched, c.missing, c.required_missing))
    # Prefer the passage that covers more of the question; BM25 score breaks ties.
    # Coverage is compared in steps of 0.05 so a hair's difference does not outrank
    # a clearly better BM25 match.
    scored.sort(key=lambda s: (-round(s.coverage * 20), -s.hit.score))
    return scored


def passing(scored: list[ScoredHit], threshold: float | None = None) -> list[ScoredHit]:
    threshold = settings.support_threshold if threshold is None else threshold
    return [s for s in scored if s.coverage >= threshold and not s.required_missing]


def locate_quote(quote: str, passage: str) -> tuple[int, int] | None:
    """Find a quote in a passage, ignoring differences in whitespace only."""
    quote = normalise_ws(quote)
    if not quote:
        return None
    pos = passage.find(quote)
    if pos >= 0:
        return pos, pos + len(quote)
    # Map positions in the whitespace-normalised passage back to the original.
    mapping, normalised, last_space = [], [], True
    for i, ch in enumerate(passage):
        if ch.isspace():
            if not last_space:
                normalised.append(" ")
                mapping.append(i)
            last_space = True
        else:
            normalised.append(ch)
            mapping.append(i)
            last_space = False
    pos = "".join(normalised).find(quote)
    if pos < 0:
        return None
    return mapping[pos], mapping[pos + len(quote) - 1] + 1


def check_quotes(draft: Draft, allowed: list[ScoredHit]) -> Decision:
    by_id = {s.hit.chunk.id: s for s in allowed}
    sources, seen = [], set()
    for q in draft.quotes:
        scored = by_id.get(q.chunk_id)
        if scored is None or q.chunk_id in seen:
            continue
        passage = scored.hit.chunk.text
        span = locate_quote(q.text, passage)
        if span is None:
            continue
        # A few characters would "match" almost any passage, so a fragment is not a quote.
        if span[1] - span[0] < min(MIN_QUOTE_CHARS, len(passage.strip())):
            continue
        seen.add(q.chunk_id)
        sources.append(Source(scored, scored.hit.chunk.text[span[0]:span[1]], span[0], span[1]))
    if not sources or not draft.text.strip():
        return Decision(False, "The answer could not be traced to an exact passage.")
    return Decision(True, "ok", sources)


def grounded(answer: str, quotes: list[str], question: str) -> bool:
    """Is the wording of an answer backed by its quotes? Every number in the answer
    must appear in a quote, and most of its content words must come from the quotes
    or the question."""
    quoted = " ".join(quotes)
    quote_numbers = set(re.findall(r"\d+(?:\.\d+)?", quoted.replace(",", "")))
    for number in re.findall(r"\d+(?:\.\d+)?", answer.replace(",", "")):
        if number not in quote_numbers:
            return False
    answer_terms = analyse(answer)
    if not answer_terms:
        return False
    known = set(analyse(quoted)) | set(analyse(question))
    return sum(t in known for t in answer_terms) / len(answer_terms) >= MIN_ANSWER_OVERLAP
