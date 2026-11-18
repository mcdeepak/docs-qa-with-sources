"""Extractive answers: the answer is text copied from the best passage. No model."""
from ..retrieval import Hit
from ..text import analyse, query_terms, sentences
from . import Draft, Quote

SHORT_PASSAGE = 300        # passages this short are quoted whole


def best_sentence(question: str, passage: str) -> str:
    """The sentence of the passage that shares the most question terms. When the
    question asks for a quantity ("how long"), only sentences that contain one are
    considered, so the sentence shown is the one that states the answer."""
    spans = sentences(passage)
    if len(passage) <= SHORT_PASSAGE or len(spans) <= 1:
        return passage
    terms = query_terms(question)
    for frame in (t for t in terms if t.frame):
        stating = [s for s in spans if frame.answered_by(passage[s[0]:s[1]])]
        if stating:
            spans = stating

    def overlap(span: tuple[int, int]) -> int:
        stems = set(analyse(passage[span[0]:span[1]]))
        return sum(1 for t in terms if t.matches & stems)

    start, end = max(spans, key=overlap)
    return passage[start:end]


class ExtractiveAnswerer:
    name = "extractive"

    def answer(self, question: str, hits: list[Hit]) -> Draft:
        if not hits:
            return Draft(text="", answerer=self.name)
        # The answer comes from the single best passage, so that is the only source.
        top = hits[0]
        quote = best_sentence(question, top.chunk.text)
        return Draft(text=quote, quotes=[Quote(top.chunk.id, quote)], answerer=self.name)
