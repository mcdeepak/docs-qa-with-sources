"""Retrieval is swappable. A retriever takes a question and returns ranked passages.

To add one: write a class with `name` and `search`, and register it in RETRIEVERS.
Choose it at runtime with the RETRIEVER environment variable.

The support decision (app/support.py) does not depend on which retriever ran, so a
new retriever cannot weaken the "supported or not" guarantee.
"""
from dataclasses import dataclass
from typing import Protocol

from ..index import Chunk, CorpusIndex


@dataclass
class Hit:
    chunk: Chunk
    position: int      # position of the chunk in the index
    score: float


class Retriever(Protocol):
    name: str

    def search(self, index: CorpusIndex, question: str, k: int) -> list[Hit]: ...


def get_retriever(name: str) -> Retriever:
    from .bm25 import BM25Retriever

    retrievers = {"bm25": BM25Retriever}
    if name not in retrievers:
        raise ValueError(f"Unknown RETRIEVER '{name}'. Available: {', '.join(retrievers)}")
    return retrievers[name]()
