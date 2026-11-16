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
from .text import QueryTerm, analyse, SOFT_WEIGHT

HEADING_WEIGHT = 0.5   # a term in the section heading counts as half a body term
TITLE_WEIGHT = 0.25    # a term in the document title counts for a quarter


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
    def __init__(self, chunks: list[Chunk], generation: int = 0):
        self.generation = generation
        self.chunks = chunks
        # Weighted term frequencies per chunk: body + heading + title.
        self.tf: list[dict[str, float]] = []
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
        present = [self.idf(s) for s in term.matches if self.df.get(s, 0) > 0]
        weight = min(present) if present else self.max_idf
        return weight * (SOFT_WEIGHT if term.soft else 1.0)

    def coverage(self, terms: list[QueryTerm], chunk_pos: int) -> tuple[float, list[str], list[str]]:
        """Share of the question, weighted by term importance, found in a passage.
        Returns (coverage, matched terms, missing terms)."""
        tf = self.tf[chunk_pos]
        total = got = 0.0
        matched, missing = [], []
        for t in terms:
            w = self.term_weight(t)
            total += w
            if any(s in tf for s in t.matches):
                got += w
                matched.append(t.stem)
            else:
                missing.append(t.stem)
        return (got / total if total else 0.0), matched, missing


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
