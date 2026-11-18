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
anybody everyone exactly
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


_CURRENCY = {"$": "dollars", "\u00a3": "pounds", "\u20ac": "euros"}
_MONEY = re.compile(r"([$\u00a3\u20ac])\s?(\d[\d,]*(?:\.\d+)?)")


def _spell_out_money(text: str) -> str:
    """"$5,000" is read as "5,000 dollars", so it matches a policy written either way."""
    return _MONEY.sub(lambda m: f"{m.group(2)} {_CURRENCY[m.group(1)]}", text)


def words(text: str) -> list[str]:
    """Lowercased word tokens. Hyphens and apostrophes split words."""
    text = _spell_out_money(_keep_acronyms(text)).lower().replace("’", "'")
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


# Comparatives that mean the opposite of each other. A rule about contracts "longer than
# 12 months" says nothing about "shorter" ones, so the opposite word in a passage is a
# contradiction, not a near miss.
OPPOSITES = {"shorter": "longer", "longer": "shorter", "fewer": "more", "less": "more", "more": "less",
             "below": "above", "above": "below", "lower": "higher", "higher": "lower",
             "minimum": "maximum", "maximum": "minimum", "smaller": "larger", "larger": "smaller",
             "earlier": "later", "later": "earlier"}

# A question that opens with one of these asks whether something is so ("Are backups kept
# for 90 days?"). A number in it is a claim to check, and the passage may well state a
# different one. In any other question a number picks out what is being asked about.
YES_NO_OPENERS = frozenset(
    "is are was were am do does did can could should would will may must has have had".split())

# ---------------------------------------------------------------- numeric ranges
# A span is (low, high, low included, high included). "Up to 2,000 dollars" is
# (-inf, 2000, True, True) in dollars; "above 10,000 dollars" is (10000, inf, False, True).
Span = tuple[float, float, bool, bool]
_INF = float("inf")
_NUM = r"(?<![\w.])(\d+(?:\.\d+)?)"
_UNIT = r"(?:\s+([a-z]+))?"
_RANGE_PATTERNS = [
    (re.compile(rf"between\s+{_NUM}\s+and\s+{_NUM}{_UNIT}"), "between"),
    (re.compile(rf"{_NUM}\s*(?:to|-|\u2013)\s*{_NUM}{_UNIT}"), "between"),
    (re.compile(rf"(?:up to|at most|no more than|not more than|maximum of)\s+{_NUM}{_UNIT}"), "at most"),
    (re.compile(rf"(?:under|below|less than|fewer than|shorter than)\s+{_NUM}{_UNIT}"), "below"),
    (re.compile(rf"(?:above|over|more than|greater than|longer than|exceeding)\s+{_NUM}{_UNIT}"), "above"),
    (re.compile(rf"(?:at least|minimum of|no less than|not less than)\s+{_NUM}{_UNIT}"), "at least"),
]


def number_ranges(text: str) -> list[tuple[Span, str]]:
    """The numeric ranges a text states, each with the stem of its unit word. A range
    with no unit word after it is ignored: without a unit it cannot be compared."""
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", _spell_out_money(text).lower())
    found = []
    for pattern, kind in _RANGE_PATTERNS:
        for m in pattern.finditer(text):
            unit = m.groups()[-1]
            if not unit or is_stop(unit):
                continue
            a = float(m.group(1))
            span = {"between": lambda: (a, float(m.group(2)), True, True),
                    "at most": lambda: (-_INF, a, True, True),
                    "below": lambda: (-_INF, a, True, False),
                    "above": lambda: (a, _INF, False, True),
                    "at least": lambda: (a, _INF, True, True)}[kind]()
            found.append((span, stem(unit)))
    return found


def _holds(span: Span, x: float) -> bool:
    return (span[0] < x or (span[0] == x and span[2])) and (x < span[1] or (x == span[1] and span[3]))


def span_within(asked: Span, stated: Span) -> bool:
    """Is everything the question asks about covered by the range the passage states?
    "Longer than 24 months" is within "longer than 12 months". "Longer than 6 months"
    is not: a 7-month contract is asked about and the rule says nothing on it."""
    low_ok = stated[0] < asked[0] or (stated[0] == asked[0] and (stated[2] or not asked[2]))
    high_ok = asked[1] < stated[1] or (asked[1] == stated[1] and (stated[3] or not asked[3]))
    return low_ok and high_ok


# Units of time say how much, not what about: "how many days" has no topic.
UNIT_STEMS = frozenset(stem(w) for w in "second minute hour day week month year".split())


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
    # For a number that picks out what is asked about ("Tier 4", "5,000 dollars"): the
    # stems of the content words just before and just after it in the question.
    before: str = ""
    after: str = ""
    # For such a number, the values the question means: 5000 exactly for "5,000 dollars",
    # everything above 10000 for "above 10,000 dollars".
    span: Span | None = None

    @property
    def topic(self) -> bool:
        """Does this term name what the question is about? Quantity words, the "not" of
        a negative question, bare numbers and units of time do not."""
        return not (self.frame or self.negation or self.stem.isdigit() or self.stem in UNIT_STEMS)

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
    selects_by_number = bool(tokens) and tokens[0] not in YES_NO_OPENERS
    asked_ranges = number_ranges(question) if selects_by_number else []
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
        if w in OPPOSITES:
            opposite = stem(OPPOSITES[w])
        before = after = ""
        span = None
        if s.isdigit() and selects_by_number:
            before = next((stem(t) for t in reversed(tokens[:i]) if not is_stop(t)), "")
            after = next((stem(t) for t in tokens[i + 1:] if not is_stop(t)), "")
            value = float(s)
            span = next((r for r, _ in asked_ranges if value in (r[0], r[1])), (value, value, True, True))
        # The word a "who approves..." question turns on must be in the passage itself,
        # or a form of the same word ("owns" / "owner"). A looser synonym is not enough
        # there: "approve" shares a group with "allowed" and "prohibited", and a ban
        # does not say who approves.
        is_required = i in required_at and not frame
        if settings.use_synonyms:
            group = _synonym_groups().get(s, frozenset())
            matches |= {m for m in group if m[:3] == s[:3]} if is_required else group
        found.append((i, QueryTerm(stem=s, word=display(w), matches=frozenset(matches), frame=frame,
                                   required=is_required, opposite=opposite,
                                   before=before, after=after, span=span)))
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
