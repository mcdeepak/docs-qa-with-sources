"""Turn an uploaded file into passages, each with a location a person can find.

Policies are structured: headings, numbered clauses, bullets, tables. We split along
that structure instead of fixed-size windows, so a passage is one rule and its
location is exact (page, section, clause or line).

To support a new file type, write a function that returns an Extracted and add it to
EXTRACTORS at the bottom of this file.
"""
import io
import re
from collections import Counter
from dataclasses import dataclass, field

from pypdf import PdfReader

MAX_PASSAGE_CHARS = 700


class UnsupportedDocument(Exception):
    """The file can never be indexed, so retrying is pointless."""


@dataclass
class Passage:
    kind: str                 # paragraph | bullet | clause | table_row | metadata
    text: str
    heading: str = ""         # section path, e.g. "3. Lost or stolen devices"
    page: int | None = None   # PDF only
    clause: str | None = None # e.g. "3.1"
    line: int | None = None   # Markdown only: first line of the passage


@dataclass
class Extracted:
    title: str
    doc_version: str | None
    doc_owner: str | None
    passages: list[Passage] = field(default_factory=list)


# A clause number is "2.1" followed by a capitalised word. Requiring the capital keeps
# "1.5 times the hourly rate" and "10.0.0.1 is the gateway" as ordinary text.
_CLAUSE = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){1,3})[.)]?\s+(?=[A-Z\"'(\[])")
_BULLET = re.compile(r"^\s*(?:[-*+\u2022\u25aa\u2013]|\d{1,2}[.)])\s+")
_VERSION = re.compile(r"\bversion\b[\s:*_]*v?(\d+(?:\.\d+)*)", re.I)
_OWNER = re.compile(r"\bowner\b[\s*_]*:[\s*_]*([^|\n*]+)", re.I)
# A metadata line is short and either uses "|" separators or starts with the label.
_META_LINE = re.compile(r"^[\s*_>#-]*(version|owner)\b", re.I)


def _metadata(header_text: str) -> tuple[str | None, str | None]:
    """Document version and owner, read only from lines that look like a header
    ("Owner: IT | Version: 2.0"), never from ordinary sentences."""
    version = owner = None
    for line in header_text.splitlines():
        if len(line) > 160 or not ("|" in line or _META_LINE.match(line)):
            continue
        if version is None and (m := _VERSION.search(line)):
            version = m.group(1)
        if owner is None and (m := _OWNER.search(line)):
            owner = m.group(1).strip() or None
    return version, owner


def _split_long(passage: Passage) -> list[Passage]:
    """Break an over-long passage at sentence boundaries."""
    if len(passage.text) <= MAX_PASSAGE_CHARS:
        return [passage]
    from .text import sentences
    parts, current = [], ""
    for start, end in sentences(passage.text):
        sentence = passage.text[start:end]
        if current and len(current) + len(sentence) + 1 > MAX_PASSAGE_CHARS:
            parts.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        parts.append(current)
    return [Passage(passage.kind, p, passage.heading, passage.page, passage.clause, passage.line)
            for p in parts]


def _finish(title: str, header_text: str, passages: list[Passage]) -> Extracted:
    for p in passages:
        p.text = p.text.replace("\x00", "")       # Postgres text cannot hold NUL
        p.heading = p.heading.replace("\x00", "")
    title = title.replace("\x00", "")
    # The header line that carries owner and version describes the whole document.
    for p in passages[:3]:
        if len(p.text) <= 160 and ("|" in p.text or _META_LINE.match(p.text)) \
                and (_VERSION.search(p.text) or _OWNER.search(p.text)):
            p.kind = "metadata"
    passages = [q for p in passages if p.text.strip() for q in _split_long(p)]
    if not passages:
        raise UnsupportedDocument("No readable text was found in this file.")
    version, owner = _metadata(header_text)
    return Extracted(title=title, doc_version=version, doc_owner=owner, passages=passages)


# ---------------------------------------------------------------- Markdown

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_MD_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _clean_inline(text: str) -> str:
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(\*\*|__|`)", "", text)
    text = re.sub(r"(?<!\w)[*_](?=\S)|(?<=\S)[*_](?!\w)", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _cells(row: str) -> list[str]:
    return [_clean_inline(c) for c in row.strip().strip("|").split("|")]


_MD_RULE = re.compile(r"^\s*([-*_])\s*(?:\1\s*){2,}$")          # --- *** ___
_MD_SETEXT = re.compile(r"^\s*(=+|-+)\s*$")


def _decode(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):          # UTF-16, e.g. Notepad "Unicode"
        return data.decode("utf-16")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def extract_markdown(data: bytes, filename: str) -> Extracted:
    lines = _decode(data).splitlines()
    # YAML front matter between two "---" lines at the very top is not content.
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is not None:
            lines[: end + 1] = [""] * (end + 1)
    # Block quotes are read as ordinary text.
    lines = [re.sub(r"^\s*>+\s?", "", line) for line in lines]

    title = ""
    stack: list[tuple[int, str]] = []   # (level, heading text) below the title
    passages: list[Passage] = []

    def heading() -> str:
        return " > ".join(h for _, h in stack)

    def add(kind: str, text: str, line_no: int) -> None:
        text = _clean_inline(text)
        if not text:
            return
        clause = None
        m = _CLAUSE.match(text)
        if m:
            clause, text, kind = m.group(1), text[m.end():], "clause"
        passages.append(Passage(kind, text, heading(), None, clause, line_no))

    i, in_code = 0, False
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            in_code = not in_code
            i += 1
            continue
        if in_code or not line.strip():
            if in_code and line.strip():
                add("paragraph", line, i + 1)
            i += 1
            continue

        if _MD_RULE.match(line):
            i += 1
            continue

        m = _MD_HEADING.match(line)
        # Setext heading: a text line underlined with === or ---.
        setext = bool(not m and i + 1 < len(lines) and _MD_SETEXT.match(lines[i + 1])
                      and not _BULLET.match(line) and "|" not in line)
        if m or setext:
            if setext:
                level = 1 if lines[i + 1].strip().startswith("=") else 2
                text = _clean_inline(line)
                i += 1
            else:
                level, text = len(m.group(1)), _clean_inline(m.group(2))
            if level == 1 and not title:
                title = text
            else:
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, text))
            i += 1
            continue

        # Table: keep each row with its column headers so it stands on its own.
        if "|" in line and i + 1 < len(lines) and _MD_TABLE_SEP.match(lines[i + 1]):
            headers = _cells(line)
            i += 2
            while i < len(lines) and "|" in lines[i] and lines[i].strip():
                cells = _cells(lines[i])
                pairs = [f"{h}: {c}" if h else c for h, c in zip(headers, cells) if c]
                add("table_row", " | ".join(pairs), i + 1)
                i += 1
            continue

        if _BULLET.match(line):
            start, text = i, _BULLET.sub("", line, count=1)
            i += 1
            # Continuation lines are indented and are not a new bullet.
            while (i < len(lines) and lines[i].startswith((" ", "\t")) and lines[i].strip()
                   and not _BULLET.match(lines[i])):
                text += " " + lines[i].strip()
                i += 1
            add("bullet", text, start + 1)
            continue

        start, buf = i, []
        while (i < len(lines) and lines[i].strip() and not _MD_HEADING.match(lines[i])
               and not _BULLET.match(lines[i]) and not lines[i].strip().startswith("```")
               and not _MD_RULE.match(lines[i])
               and not (buf and i + 1 < len(lines) and _MD_SETEXT.match(lines[i + 1]))
               and not (buf and "|" in lines[i] and i + 1 < len(lines)
                        and _MD_TABLE_SEP.match(lines[i + 1]))):
            buf.append(lines[i].strip())
            i += 1
        add("paragraph", " ".join(buf), start + 1)

    header_text = "\n".join(lines[:12])
    return _finish(title or _title_from_filename(filename), header_text, passages)


# ---------------------------------------------------------------- PDF

@dataclass
class _Line:
    page: int
    y: float
    size: float
    bold: bool
    text: str


def _pdf_lines(reader: PdfReader) -> list[_Line]:
    """Visual lines with font size and weight, read from the PDF text operators."""
    out: list[_Line] = []
    for page_no, page in enumerate(reader.pages, start=1):
        fragments: list[tuple[float, float, float, bool, str]] = []

        def visit(text, cm, tm, font_dict, font_size, fragments=fragments):
            if not text or not text.strip():
                return
            scale = abs(tm[3] * cm[3]) or 1.0
            font = str((font_dict or {}).get("/BaseFont", ""))
            bold = "bold" in font.lower() or "black" in font.lower()
            y = tm[5] * cm[3] + cm[5]
            x = tm[4] * cm[0] + cm[4]
            fragments.append((y, x, round(font_size * scale, 1), bold, text))

        try:
            page.extract_text(visitor_text=visit)
        except Exception:
            fragments = []
        # Group fragments on the same baseline into one line, left to right.
        fragments.sort(key=lambda f: (-f[0], f[1]))
        current: list[tuple] = []
        for frag in fragments:
            if current and abs(current[0][0] - frag[0]) > 2.0:
                out.append(_merge(page_no, current))
                current = []
            current.append(frag)
        if current:
            out.append(_merge(page_no, current))
    return out


def _merge(page_no: int, fragments: list[tuple]) -> _Line:
    fragments = sorted(fragments, key=lambda f: f[1])
    text = "".join(f[4].replace("\n", "") for f in fragments)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)   # stray space left by PDF kerning
    size = max(f[2] for f in fragments)
    bold = all(f[3] for f in fragments)
    return _Line(page_no, fragments[0][0], size, bold, text)


def _join(a: str, b: str) -> str:
    # A word broken across lines with a hyphen stays hyphenated without a space.
    if a.endswith("-") and b[:1].islower():
        return a + b
    return f"{a} {b}"


def extract_pdf(data: bytes, filename: str) -> Extracted:
    try:
        reader = PdfReader(io.BytesIO(data))
        # Many PDFs are encrypted only to restrict printing and open with an empty password.
        if reader.is_encrypted and not reader.decrypt(""):
            raise UnsupportedDocument("This PDF is password protected.")
        lines = [l for l in _pdf_lines(reader) if l.text]
    except UnsupportedDocument:
        raise
    except Exception as exc:
        raise UnsupportedDocument(f"This PDF could not be read ({type(exc).__name__}).")
    if not lines:
        raise UnsupportedDocument(
            "No text layer was found in this PDF. Scanned documents are not supported.")

    sizes = Counter()
    for l in lines:
        sizes[l.size] += len(l.text)
    body_size = sizes.most_common(1)[0][0]
    gaps = [a.y - b.y for a, b in zip(lines, lines[1:]) if a.page == b.page and 0 < a.y - b.y < 40]
    # Normal line spacing: the tightest spacing that occurs regularly, capped at 1.3 times
    # the font size so a document of one-line paragraphs is not read as a single block.
    real_gaps = sorted(g for g in gaps if g >= body_size * 0.8)
    tight = real_gaps[len(real_gaps) // 10] if real_gaps else body_size * 1.2
    line_gap = min(tight, body_size * 1.3)

    # A running header or footer is a line at the top or bottom edge of a page that
    # repeats (ignoring digits such as the page number) on most pages. Body lines are
    # never dropped, however often their shape repeats.
    pages = len(reader.pages)
    by_page: dict[int, list[_Line]] = {}
    for l in lines:
        by_page.setdefault(l.page, []).append(l)
    edge_ids = {id(l) for page_lines in by_page.values() for l in page_lines[:2] + page_lines[-2:]}

    def shape(l: _Line) -> str:
        return re.sub(r"\d+", "#", l.text)

    on_pages: dict[str, set[int]] = {}
    for l in lines:
        if id(l) in edge_ids:
            on_pages.setdefault(shape(l), set()).add(l.page)
    boiler = {t for t, ps in on_pages.items() if pages >= 3 and len(ps) >= max(3, int(pages * 0.6))}
    page_number = re.compile(r"(page\s*)?\d+(\s*(of|/)\s*\d+)?", re.I)

    def is_boilerplate(l: _Line) -> bool:
        if id(l) not in edge_ids:
            return False
        return shape(l) in boiler or (pages >= 2 and bool(page_number.fullmatch(l.text)))

    def is_heading(l: _Line) -> bool:
        if len(l.text) > 120:
            return False
        if l.size >= body_size * 1.12:
            return True
        return l.bold and not l.text.endswith((".", ",", ";")) and len(l.text) < 90

    first_page = [l for l in lines if l.page == 1]
    title_line = max(first_page, key=lambda l: l.size) if first_page else None
    title = title_line.text if title_line and title_line.size > body_size else ""

    passages: list[Passage] = []
    section = ""
    current: Passage | None = None
    prev: _Line | None = None

    def flush():
        nonlocal current
        if current and current.text.strip():
            passages.append(current)
        current = None

    for l in lines:
        if is_boilerplate(l):
            continue
        if l is title_line and title:
            prev = l
            continue
        if is_heading(l):
            flush()
            # A heading wrapped over two lines continues the same heading.
            if prev is not None and prev.page == l.page and section and is_heading(prev) \
                    and prev is not title_line and abs(prev.size - l.size) < 0.1 \
                    and prev.y - l.y < line_gap * 1.3:
                section = _join(section, l.text)
            else:
                section = l.text
            prev = l
            continue

        clause = _CLAUSE.match(l.text)
        bullet = _BULLET.match(l.text)
        new_block = (
            current is None or clause or bullet
            or prev is None or prev.page != l.page and current.text.rstrip().endswith((".", ":", ";", "?", "!"))
            or (prev.page == l.page and prev.y - l.y > line_gap * 1.35)
        )
        if new_block:
            flush()
            if clause:
                current = Passage("clause", l.text[clause.end():], section, l.page, clause.group(1))
            elif bullet:
                current = Passage("bullet", _BULLET.sub("", l.text, count=1), section, l.page)
            else:
                current = Passage("paragraph", l.text, section, l.page)
        else:
            current.text = _join(current.text, l.text)
        prev = l
    flush()

    header_text = "\n".join(l.text for l in first_page[:8])
    return _finish(title or _title_from_filename(filename), header_text, passages)


def _title_from_filename(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0]
    return re.sub(r"[-_]+", " ", stem).strip().capitalize()


EXTRACTORS = {
    ".pdf": extract_pdf,
    ".md": extract_markdown,
    ".markdown": extract_markdown,
}


def extension(filename: str) -> str:
    return "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def extract(data: bytes, filename: str) -> Extracted:
    extractor = EXTRACTORS.get(extension(filename))
    if extractor is None:
        raise UnsupportedDocument(
            f"Unsupported file type. Accepted: {', '.join(sorted(EXTRACTORS))}.")
    return extractor(data, filename)
