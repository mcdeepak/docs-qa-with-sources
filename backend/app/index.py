"""The searchable set: every passage of every active document version, in memory.

At this scale (tens of documents, hundreds of passages) an in-memory index is fast
and simple. The API reloads it only when index_state.generation changes, which the
worker bumps each time a document becomes ready, is replaced or is deleted.
"""
import math
import threading
from collections import Counter
from dataclasses import dataclass

from . import db
from .config import settings
from .text import (FRAME_WEIGHT, MEASURE_FRAME, QueryTerm, analyse, has_measure, has_quantity,
                   states_negative, words)

HEADING_WEIGHT = 0.5   # a term in the section heading counts as half a body term
TITLE_WEIGHT = 0.25    # a term in the document title counts for a quarter
# When a question says "probation period" and a passage has "probation" but not
# "period", the passage is about something else. The half that matched counts this much.
BROKEN_PHRASE_CREDIT = 0.5


@dataclass
class Coverage:
    value: float                 # 0 to 1: weighted share of the question found in the passage
    matched: list[str]
    missing: list[str]
    required_missing: list[str]  # words the question turns on that the passage lacks


@dataclass(frozen=True)
class Chunk:
    id: int
    document_id: int
    filename: str
    title: str
    doc_version: str | None
    doc_owner: str | None
    version_no: int
    uploaded_at: str
    ordinal: int
    kind: str
    text: str
    heading: str
    page: int | None
    clause: str | None
    line: int | None


class CorpusIndex:
    def __init__(self, chunks: list[Chunk], generation: int = 0, absent_term_weight: float | None = None):
        self.generation = generation
        self.absent_term_weight = (settings.absent_term_weight if absent_term_weight is None
                                   else absent_term_weight)
        self.chunks = chunks
        # Weighted term frequencies per chunk: body + heading + title.
        self.tf: list[dict[str, float]] = []
        # Where each term occurs, so a match in the passage itself can count for more
        # than a match in its section heading or document title.
        self.body: list[frozenset[str]] = []
        self.heading: list[frozenset[str]] = []
        self.title: list[frozenset[str]] = []
        # Exact words of the title, used only for a document's owner/version line.
        self.title_words: list[frozenset[str]] = []
        self.quantity: list[bool] = [has_quantity(c.text) for c in chunks]
        self.measure: list[bool] = [has_measure(c.text) for c in chunks]
        self.negative: list[bool] = [states_negative(c.text) for c in chunks]
        self.length: list[float] = []
        df: Counter = Counter()
        for c in chunks:
            tf: dict[str, float] = {}
            for term in analyse(c.text):
                tf[term] = tf.get(term, 0.0) + 1.0
            for term in analyse(c.heading):
                tf[term] = tf.get(term, 0.0) + HEADING_WEIGHT
            for term in analyse(c.title):
                tf[term] = tf.get(term, 0.0) + TITLE_WEIGHT
            self.tf.append(tf)
            self.body.append(frozenset(analyse(c.text)))
            self.heading.append(frozenset(analyse(c.heading)))
            self.title.append(frozenset(analyse(c.title)))
            self.title_words.append(frozenset(words(c.title)) if c.kind == "metadata" else frozenset())
            self.length.append(sum(tf.values()))
            df.update(tf.keys())
        self.df = df
        self.n = len(chunks)
        self.avg_length = (sum(self.length) / self.n) if self.n else 0.0
        self.max_idf = self._idf_for(0)

    def _idf_for(self, df: int) -> float:
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def idf(self, stem: str) -> float:
        return self._idf_for(self.df.get(stem, 0))

    def term_weight(self, term: QueryTerm) -> float:
        """How much a question term matters. Rare terms matter more; a term that
        appears nowhere in the documents matters most. A term reached through a
        synonym is weighed by the commonest wording that is actually present."""
        if term.negation:
            return 0.0      # the "not" of a question is a condition, it earns no coverage
        if term.frame:
            return self.max_idf * FRAME_WEIGHT
        stems = term.matches | {term.opposite} if term.opposite else term.matches
        present = [self.idf(s) for s in stems if self.df.get(s, 0) > 0]
        return min(present) if present else self.max_idf * self.absent_term_weight

    def covers(self, term: QueryTerm, chunk_pos: int) -> bool:
        return self.credit(term, chunk_pos) > 0

    def credit(self, term: QueryTerm, chunk_pos: int) -> float:
        """How well a passage matches one question term: 1 in the passage text, 0.5 in
        its section heading only, 0.25 in the document title only, else 0."""
        if term.negation:
            return 1.0 if self.negative[chunk_pos] else 0.0
        if term.frame and self.frame_answered(term, chunk_pos):
            return 1.0
        if term.matches & self.body[chunk_pos]:
            return 1.0
        if self.opposite_matches(term, chunk_pos) and self.negative[chunk_pos]:
            return 1.0
        if term.matches & self.heading[chunk_pos]:
            return HEADING_WEIGHT
        if term.matches & self.title[chunk_pos]:
            return TITLE_WEIGHT
        return 0.0

    def frame_answered(self, term: QueryTerm, chunk_pos: int) -> bool:
        """Does the passage contain the kind of quantity a "how long/many" term asks for?"""
        return (self.measure if term.word.lower() in MEASURE_FRAME else self.quantity)[chunk_pos]

    def opposite_matches(self, term: QueryTerm, chunk_pos: int) -> bool:
        return bool(term.opposite) and term.opposite in self.body[chunk_pos]

    def coverage(self, terms: list[QueryTerm], chunk_pos: int) -> Coverage:
        """Share of the question, weighted by term importance, found in a passage."""
        credits = [self.credit(t, chunk_pos) for t in terms]
        # A document's owner/version line is about the document named in its title. When
        # the question asks for something that line states ("who owns the device
        # policy?"), the title's exact words count in full.
        title_words = self.title_words[chunk_pos]
        if title_words and any(not t.frame and t.matches & self.body[chunk_pos] for t in terms):
            credits = [1.0 if t.word.lower() in title_words else c for t, c in zip(terms, credits)]
        for i, t in enumerate(terms[:-1]):
            # The partner counts as missing when it is only in the document title.
            if t.joined_to_next and credits[i] > 0 and credits[i + 1] < HEADING_WEIGHT:
                credits[i] *= BROKEN_PHRASE_CREDIT
        total = got = 0.0
        matched, missing, required_missing = [], [], []
        for t, c in zip(terms, credits):
            w = self.term_weight(t)
            total += w
            got += w * c
            (matched if c > 0 else missing).append(t.word or t.stem)
            # A required word must be in the passage or its heading; the title is not enough.
            if t.required and c < HEADING_WEIGHT:
                required_missing.append(t.word or t.stem)
            # "How long ...?" cannot be answered by a passage that states no quantity.
            elif t.frame and c == 0:
                required_missing.append(t.word or t.stem)
            # "unapproved" asked, "approved" stated: the passage says the opposite.
            elif c == 0 and self.opposite_matches(t, chunk_pos):
                required_missing.append(t.word or t.stem)
        return Coverage(got / total if total else 0.0, matched, missing, required_missing)


_lock = threading.Lock()
_cached: CorpusIndex | None = None

_LOAD_SQL = """
SELECT c.id, d.id AS document_id, d.filename, v.title, v.doc_version, v.doc_owner,
       v.version_no, v.uploaded_at, c.ordinal, c.kind, c.text, c.heading, c.page,
       c.clause, c.line
FROM documents d
JOIN document_versions v ON v.id = d.active_version_id
JOIN chunks c ON c.version_id = v.id
ORDER BY d.id, c.ordinal
"""


def current() -> CorpusIndex:
    """The index for the current generation, rebuilt only when it has changed."""
    global _cached
    with db.pool().connection() as conn:
        generation = conn.execute("SELECT generation FROM index_state WHERE id = 1").fetchone()["generation"]
        if _cached is not None and _cached.generation == generation:
            return _cached
        with _lock:
            if _cached is not None and _cached.generation == generation:
                return _cached
            rows = conn.execute(_LOAD_SQL).fetchall()
            chunks = [Chunk(**{**r, "uploaded_at": r["uploaded_at"].isoformat(),
                               "title": r["title"] or r["filename"]}) for r in rows]
            _cached = CorpusIndex(chunks, generation)
            return _cached
