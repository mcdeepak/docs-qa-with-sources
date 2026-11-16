"""Text analysis shared by indexing, retrieval and the support decision.

Everything here is deterministic and dependency-light: lowercase, split into words,
drop stop words, stem. A small synonym table bridges common wording differences
between how people ask and how policies are written ("kept" vs "retained").
"""
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import snowballstemmer

from .config import settings

_stemmer = snowballstemmer.stemmer("english")
_WORD = re.compile(r"[a-z0-9]+")

STOP_WORDS = frozenset("""
a an and are as at be been being but by can could did do does doing for from had has
have having he her here hers him his i if in into is it its me my of on or our ours
she should so than that the their theirs them then there these they this those to
too us was we were what when where which who whom whose why will with would you your
yours am any about also not no yes get got please tell know want there's using use
used many much how must shall may might
""".split())

# Words that follow "how" and shape the question rather than name its topic
# ("how long", "how quickly"). They still help ranking but count for less when we
# decide whether a passage covers the question.
HOW_FRAME = frozenset("long often quickly soon fast far big old late early".split())
SOFT_WEIGHT = 0.3


@lru_cache(maxsize=50_000)
def stem(word: str) -> str:
    return _stemmer.stemWord(word)


def words(text: str) -> list[str]:
    """Lowercased word tokens. Hyphens and apostrophes split words."""
    text = text.lower().replace("’", "'")
    text = re.sub(r"'s\b", "", text)
    return _WORD.findall(text)


def analyse(text: str) -> list[str]:
    """Stemmed content words of a passage."""
    return [stem(w) for w in words(text) if w not in STOP_WORDS]


@lru_cache(maxsize=1)
def _synonym_groups() -> dict[str, frozenset[str]]:
    groups: dict[str, set[str]] = {}
    path = Path(__file__).parent / "synonyms.txt"
    for line in path.read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        stems = {stem(w) for entry in line.split(",") for w in words(entry)}
        for s in stems:
            groups.setdefault(s, set()).update(stems)
    return {k: frozenset(v) for k, v in groups.items()}


@dataclass(frozen=True)
class QueryTerm:
    stem: str
    # Stems that count as a match for this term, including itself.
    matches: frozenset = field(default_factory=frozenset)
    # Soft terms shape the question ("how long") and weigh less in coverage.
    soft: bool = False


def query_terms(question: str) -> list[QueryTerm]:
    tokens = words(question)
    terms: list[QueryTerm] = []
    seen: set[str] = set()
    for i, w in enumerate(tokens):
        if w in STOP_WORDS:
            continue
        s = stem(w)
        if s in seen:
            continue
        seen.add(s)
        soft = i > 0 and tokens[i - 1] == "how" and w in HOW_FRAME
        matches = frozenset({s})
        if settings.use_synonyms:
            matches = matches | _synonym_groups().get(s, frozenset())
        terms.append(QueryTerm(stem=s, matches=matches, soft=soft))
    return terms


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def sentences(text: str) -> list[tuple[int, int]]:
    """Character spans of the sentences in a passage."""
    spans, start = [], 0
    for m in _SENTENCE_END.finditer(text):
        # Do not split after a clause number or an abbreviation like "e.g."
        before = text[start:m.start()]
        if re.search(r"(?:^|\s)(?:\d+|e\.g|i\.e|etc|no|vs)\.$", before, re.I):
            continue
        spans.append((start, m.start()))
        start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def normalise_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
