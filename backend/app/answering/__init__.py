"""Answering is swappable. An answerer turns ranked passages into a draft answer
with quotes. It never decides whether the answer is supported: app/support.py checks
every quote against the stored passage afterwards.

To add one: write a class with `name` and `answer`, and register it in get_answerer.
Choose it at runtime with the ANSWERER environment variable.
"""
from dataclasses import dataclass, field
from typing import Protocol

from ..retrieval import Hit


@dataclass
class Quote:
    chunk_id: int
    text: str          # must appear word for word in the cited passage


@dataclass
class Draft:
    text: str
    quotes: list[Quote] = field(default_factory=list)
    answerer: str = ""


class Answerer(Protocol):
    name: str

    def answer(self, question: str, hits: list[Hit]) -> Draft: ...


def get_answerer(name: str) -> Answerer:
    from .extractive import ExtractiveAnswerer
    from .llm import LLMAnswerer

    answerers = {"extractive": ExtractiveAnswerer, "llm": LLMAnswerer}
    if name not in answerers:
        raise ValueError(f"Unknown ANSWERER '{name}'. Available: {', '.join(answerers)}")
    return answerers[name]()
