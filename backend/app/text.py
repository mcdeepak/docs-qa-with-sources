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
used how must shall may might give take need make put go let someone somebody anyone
anybody everyone
""".split())

# Words that follow "how" and ask for a quantity rather than name a topic ("how long",
# "how often", "how many"). A passage satisfies one by containing a quantity: a number
# or a frequency word. See has_quantity below.
HOW_FRAME = frozenset(
    "long often quickly soon fast far big old late early many much frequently".split())
FRAME_WEIGHT = 0.3

_QUANTITY = re.compile(
    r"\d|\b(?:every|each|once|twice|daily|nightly|weekly|fortnightly|monthly|quarterly|"
    r"annually|yearly|immediately|always|never|one|two|three|four|five|six|seven|eight|"
    r"nine|ten)\b", re.I)

# Irregular forms the stemmer does not connect.
IRREGULAR = {
    "kept": "keep", "lost": "lose", "stolen": "steal", "stole": "steal", "bought": "buy",
    "sent": "send", "paid": "pay", "held": "hold", "brought": "bring", "lent": "lend",
    "broken": "break", "broke": "break", "gave": "give", "given": "give", "took": "take",
    "taken": "take", "written": "write", "wrote": "write", "chosen": "choose",
    "chose": "choose", "sold": "sell", "told": "tell", "met": "meet", "made": "make",
    "done": "do", "went": "go", "gone": "go", "began": "begin", "begun": "begin",
    "known": "know", "knew": "know", "seen": "see", "saw": "see", "shown": "show",
    "spent": "spend", "built": "build", "found": "find", "taught": "teach",
    "withdrawn": "withdraw", "worn": "wear", "forgot": "forget", "forgotten": "forget",
}

# Spellings that should be one word.
ALIASES = {"wi-fi": "wifi", "wi fi": "wifi", "e-mail": "email", "sign-in": "signin",
           "sign in": "signin", "log-in": "login", "log in": "login"}


def has_quantity(text: str) -> bool:
    return bool(_QUANTITY.search(text))


@lru_cache(maxsize=50_000)
def stem(word: str) -> str:
    return _stemmer.stemWord(word)


def words(text: str) -> list[str]:
    """Lowercased word tokens. Hyphens and apostrophes split words."""
    text = text.lower().replace("’", "'")
    text = re.sub(r"'s\b", "", text)
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)   # 10,000 -> 10000
    for spelling, word in ALIASES.items():
        text = text.replace(spelling, word)
    return [IRREGULAR.get(w, w) for w in _WORD.findall(text)]


STOP_STEMS = frozenset(stem(w) for w in STOP_WORDS)


def is_stop(word: str) -> bool:
    return word in STOP_WORDS or stem(word) in STOP_STEMS


def analyse(text: str) -> list[str]:
    """Stemmed content words of a passage."""
    return [stem(w) for w in words(text) if not is_stop(w)]


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
    word: str = ""   # the word as the person typed it, for display
    # Stems that count as a match for this term, including itself.
    matches: frozenset = field(default_factory=frozenset)
    # A frame term asks for a quantity ("how long"). A passage with a number or a
    # frequency word satisfies it.
    frame: bool = False


def query_terms(question: str) -> list[QueryTerm]:
    tokens = words(question)
    terms: list[QueryTerm] = []
    seen: set[str] = set()
    for i, w in enumerate(tokens):
        frame = i > 0 and tokens[i - 1] == "how" and w in HOW_FRAME
        if is_stop(w) and not frame:
            continue
        s = stem(w)
        if s in seen:
            continue
        seen.add(s)
        matches = {s}
        if len(s) >= 6 and s.startswith("un"):
            matches.add(s[2:])                       # "uncollected" also matches "collected"
        if settings.use_synonyms:
            matches |= _synonym_groups().get(s, frozenset())
        terms.append(QueryTerm(stem=s, word=w, matches=frozenset(matches), frame=frame))
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
