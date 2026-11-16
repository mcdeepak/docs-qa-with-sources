import pytest

from app.extract import UnsupportedDocument, extract
from tests.conftest import CORPUS, SAMPLES


def load(path):
    return extract(path.read_bytes(), path.name)


def test_pdf_clauses_keep_page_section_and_clause_number():
    doc = load(SAMPLES / "password-and-mfa-policy.pdf")
    assert doc.title == "Password and Multi-Factor Authentication Policy"
    assert doc.doc_version == "2.1"
    assert doc.doc_owner == "IT Governance"
    clause = next(p for p in doc.passages if p.clause == "2.1")
    assert clause.text == "Passwords must be at least 14 characters long."
    assert clause.heading == "2. Passwords"
    assert clause.page == 1


def test_pdf_lines_wrapped_across_the_page_are_joined():
    doc = load(SAMPLES / "password-and-mfa-policy.pdf")
    clause = next(p for p in doc.passages if p.clause == "3.1")
    assert clause.text.endswith("access to the finance system.")


def test_pdf_page_numbers_and_running_footer():
    doc = load(CORPUS / "business-continuity-plan.pdf")
    pages = {p.clause: p.page for p in doc.passages if p.clause}
    assert pages["2.2"] == 1 and pages["3.2"] == 2 and pages["5.1"] == 3
    assert not any("Internal | Page" in p.text for p in doc.passages)


def test_pdf_without_clause_numbers_uses_headings():
    doc = load(CORPUS / "ai-tools-usage-guideline.pdf")
    passage = next(p for p in doc.passages if "public AI tool" in p.text)
    assert passage.heading == "Information you must not share"


def test_markdown_table_rows_carry_their_headers():
    doc = load(SAMPLES / "incident-response-procedure.md")
    row = next(p for p in doc.passages if p.kind == "table_row" and "P1" in p.text)
    assert row.text == "Level: P1 | Description: Critical outage or confirmed data breach | First response: 15 minutes"
    assert row.heading == "1. Severity levels"
    assert row.line == 8


def test_markdown_bullets_are_separate_passages_with_line_numbers():
    doc = load(SAMPLES / "device-and-laptop-policy.md")
    usb = next(p for p in doc.passages if p.text.startswith("USB storage"))
    assert usb.heading == "3. Removable media" and usb.line == 14
    assert doc.doc_version == "2.0" and doc.doc_owner == "IT Operations"


def test_unsupported_and_empty_files_are_rejected_permanently():
    with pytest.raises(UnsupportedDocument):
        extract(b"hello", "notes.docx")
    with pytest.raises(UnsupportedDocument):
        extract(b"   \n", "empty.md")
    with pytest.raises(UnsupportedDocument):
        extract(b"%PDF-1.4 not really a pdf", "broken.pdf")
