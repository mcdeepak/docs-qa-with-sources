"""Keyword retrieval with BM25. No model, no network."""
from ..index import CorpusIndex
from ..text import query_terms
from . import Hit

K1 = 1.2
B = 0.6
SYNONYM_WEIGHT = 0.7   # a synonym match scores a little less than the exact word


class BM25Retriever:
    name = "bm25"

    def search(self, index: CorpusIndex, question: str, k: int) -> list[Hit]:
        terms = query_terms(question)
        if not terms or not index.n:
            return []
        hits = []
        for pos, tf in enumerate(index.tf):
            norm = K1 * (1 - B + B * index.length[pos] / (index.avg_length or 1.0))
            score = 0.0
            for t in terms:
                best = 0.0
                for s in t.matches:
                    f = tf.get(s)
                    if not f:
                        continue
                    part = index.idf(s) * f * (K1 + 1) / (f + norm)
                    if s != t.stem:
                        part *= SYNONYM_WEIGHT
                    best = max(best, part)
                score += best
            if score > 0:
                hits.append(Hit(index.chunks[pos], pos, score))
        hits.sort(key=lambda h: (-h.score, h.chunk.id))
        return hits[:k]
