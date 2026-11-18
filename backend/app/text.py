"""Text analysis shared by indexing, retrieval and the support decision.

Everything here is deterministic and dependency-light: lowercase, split into words,
drop stop words, stem. A small synonym table bridges common wording differences
between how people ask and how policies are written ("kept" vs "retained").
"""
import re
from dataclasses import dataclass, field, replace
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
# These ask for a duration, a length or a frequency. A bare number such as the 7 in
# "vault 7" does not answer them: the number must come with a unit ("35 days").
MEASURE_FRAME = frozenset("long often quickly soon fast late early frequently".split())
FRAME_WEIGHT = 0.3

_FREQUENCY = (r"every|each|once|twice|daily|nightly|weekly|fortnightly|monthly|quarterly|"
              r"annually|yearly|immediately|always|never")
_NUMBER_WORD = r"one|two|three|four|five|six|seven|eight|nine|ten"
_QUANTITY = re.compile(rf"\d|\b(?:{_FREQUENCY}|{_NUMBER_WORD})\b", re.I)
# A number followed by a unit word, a percentage, an amount of money, or a frequency word.
_MEASURE = re.compile(
    rf"(?<![A-Za-z\d.])\d+(?:[.,]\d+)*\s*(?:%|-?\s*[A-Za-z]{{2,}})|[$\u00a3\u20ac]\s*\d|"
    rf"\b(?:{_FREQUENCY})\b|\b(?:{_NUMBER_WORD})\s+[A-Za-z]{{2,}}", re.I)

# Words that make a question negative ("when is MFA not required?") and words that make
# a passage state a negative, an exception or a ban.
_ASKS_NEGATIVE = re.compile(r"\b(?:not|no|never|without|cannot)\b|n['\u2019]t\b", re.I)
_STATES_NEGATIVE = re.compile(
    r"\b(?:not|no|never|without|cannot|neither|nor|except\w*|unless|exempt\w*|prohibit\w*|"
    r"forbidden)\b|n['\u2019]t\b", re.I)

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


_ALIAS = re.compile(r"\b(?:" + "|".join(re.escape(a) for a in ALIASES) + r")\b")


def has_quantity(text: str) -> bool:
    return bool(_QUANTITY.search(text))


def has_measure(text: str) -> bool:
    return bool(_MEASURE.search(text))


def asks_negative(question: str) -> bool:
    return bool(_ASKS_NEGATIVE.search(question))


def states_negative(text: str) -> bool:
    return bool(_STATES_NEGATIVE.search(text))


@lru_cache(maxsize=50_000)
def stem(word: str) -> str:
    return _stemmer.stemWord(word)


# Upper-case words such as "IT" and "US" are names, not the stop words "it" and "us".
_ACRONYM = re.compile(r"\b[A-Z]{2,5}\b")
ACRONYM_MARK = "xx"


def _keep_acronyms(text: str) -> str:
    if text.isupper():            # a question typed in capitals is not full of acronyms
        return text

    def mark(m: re.Match) -> str:
        word = m.group(0).lower()
        return word + ACRONYM_MARK if word in STOP_WORDS else m.group(0)

    return _ACRONYM.sub(mark, text)


def display(word: str) -> str:
    """The word as a person would write it (undoes the acronym marker)."""
    if word.endswith(ACRONYM_MARK) and word[:-len(ACRONYM_MARK)] in STOP_WORDS:
        return word[:-len(ACRONYM_MARK)].upper()
    return word


def words(text: str) -> list[str]:
    """Lowercased word tokens. Hyphens and apostrophes split words."""
    text = _keep_acronyms(text).lower().replace("’", "'")
    # "can't" and "isn't" are read as "can not" and "is not".
    text = re.sub(r"\bcan't\b", "can not", text)
    text = re.sub(r"\bwon't\b", "will not", text)
    text = re.sub(r"n't\b", " not", text)
    text = re.sub(r"'(?:s|m|re|ve|ll|d)\b", "", text)    # what's, I'm, we're, I've, I'll, I'd
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)   # 10,000 -> 10000
    text = _ALIAS.sub(lambda m: ALIASES[m.group(0)], text)
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
    # The action or subject the question turns on ("who approves...", "how do I book...").
    # A passage that does not contain it cannot support an answer.
    required: bool = False
    # True when the next term follows this one directly in the question, as in
    # "probation period". Used to check that a passage has the phrase, not just a part.
    joined_to_next: bool = False
    # For an "un..." word, the stem without the prefix ("unapproved" -> "approv"). A
    # passage matches through it only if the passage is itself negative ("not approved").
    opposite: str = ""
    # Stands for the "not" in a negative question. Only a passage that states a
    # negative or an exception satisfies it.
    negation: bool = False

    def answered_by(self, text: str) -> bool:
        """For a frame term: does this text contain the kind of quantity asked for?"""
        return has_measure(text) if self.word.lower() in MEASURE_FRAME else has_quantity(text)


_HOW_AUX = frozenset("do does did can could should would must may".split())


def _required_position(tokens: list[str]) -> int | None:
    """Position of the word a "who ..." or "how do I ..." question turns on: the first
    content word after the question opener. None for other kinds of question."""
    for i, w in enumerate(tokens):
        opener = w == "who" or (w == "how" and i + 1 < len(tokens) and tokens[i + 1] in _HOW_AUX)
        if not opener:
            continue
        for j in range(i + 1, len(tokens)):
            if not is_stop(tokens[j]):
                return j
        return None
    return None


_NEGATORS = frozenset("not no never without cannot".split())


def _negated_position(tokens: list[str]) -> int | None:
    """Position of the word a negative question negates: the first content word after
    "not" ("when is MFA not required?" turns on "required")."""
    for i, w in enumerate(tokens):
        if w in _NEGATORS:
            for j in range(i + 1, len(tokens)):
                if not is_stop(tokens[j]):
                    return j
            return None
    return None


def query_terms(question: str) -> list[QueryTerm]:
    tokens = words(question)
    required_at = {_required_position(tokens), _negated_position(tokens)}
    found: list[tuple[int, QueryTerm]] = []
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
        # "uncollected" can be answered by "not collected", never by "collected" alone.
        opposite = s[2:] if len(s) >= 6 and s.startswith("un") else ""
        if settings.use_synonyms:
            matches |= _synonym_groups().get(s, frozenset())
        found.append((i, QueryTerm(stem=s, word=display(w), matches=frozenset(matches), frame=frame,
                                   required=(i in required_at and not frame), opposite=opposite)))
    terms = []
    for n, (i, term) in enumerate(found):
        nxt = found[n + 1] if n + 1 < len(found) else None
        joined = bool(nxt and nxt[0] == i + 1 and not term.frame and not nxt[1].frame)
        terms.append(replace(term, joined_to_next=joined))
    if terms and asks_negative(question):
        terms.append(QueryTerm(stem="not", word="not", negation=True, required=True))
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
