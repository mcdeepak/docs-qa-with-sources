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


# ---------------------------------------------------------------- regressions from review

FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures"


def md(text: str, name: str = "doc.md"):
    return extract(text.encode(), name)


def test_pdf_body_lines_that_differ_only_by_digits_are_kept_but_the_footer_is_not():
    doc = load(FIXTURES / "repeating-lines.pdf")
    texts = [p.text for p in doc.passages]
    assert "P1 15 minutes" in texts and "P2 30 minutes" in texts and "P3 60 minutes" in texts
    assert not any("Page" in t and "of 3" in t for t in texts)
    assert next(p for p in doc.passages if p.clause == "3.1").page == 3


def test_pdf_decimal_at_the_start_of_a_line_is_not_a_clause_number():
    doc = load(FIXTURES / "repeating-lines.pdf")
    assert any("1.5 times the hourly rate" in p.text for p in doc.passages)
    assert not any(p.clause == "1.5" for p in doc.passages)


def test_markdown_decimals_and_addresses_are_not_clause_numbers():
    doc = md("# Pay\n\n- 1.5 times the hourly rate is paid for overtime.\n"
             "- 2.5 days of leave accrue each month.\n\n10.0.0.1 is the gateway address.\n\n"
             "2.1 Staff must record overtime weekly.\n")
    by_text = {p.text: p for p in doc.passages}
    assert by_text["1.5 times the hourly rate is paid for overtime."].clause is None
    assert by_text["2.5 days of leave accrue each month."].clause is None
    assert by_text["10.0.0.1 is the gateway address."].clause is None
    assert by_text["Staff must record overtime weekly."].clause == "2.1"


def test_a_year_at_the_start_of_a_wrapped_line_is_not_a_list_item():
    doc = md("# Policy\n\nThis policy applies from 1 July\n2025. Staff must comply.\n")
    assert [p.text for p in doc.passages] == ["This policy applies from 1 July 2025. Staff must comply."]


def test_owner_and_version_are_read_from_header_lines_only():
    prose = md("# Access\n\nEach system owner must approve access requests. This is version 3 of the document.\n")
    assert prose.doc_owner is None and prose.doc_version is None
    header = md("# Access\n\n**Owner:** Security  |  **Version:** 1.6\n\nText.\n")
    assert header.doc_owner == "Security" and header.doc_version == "1.6"


def test_markdown_rules_front_matter_setext_headings_and_quotes():
    doc = md("---\ntitle: Ignored\n---\n\nKettle Policy\n=============\n\nMaintenance\n-----------\n\n"
             "> Kettles must be descaled every 6 weeks.\n\n---\n\nSpare kettles are kept in the store room.\n")
    assert doc.title == "Kettle Policy"
    assert [(p.heading, p.text) for p in doc.passages] == [
        ("Maintenance", "Kettles must be descaled every 6 weeks."),
        ("Maintenance", "Spare kettles are kept in the store room."),
    ]


def test_utf16_markdown_is_decoded_and_never_contains_nul():
    data = "# Kettle Policy\n\n- Kettles must be descaled every 6 weeks.\n".encode("utf-16")
    doc = extract(data, "kettle.md")
    assert doc.passages[0].text == "Kettles must be descaled every 6 weeks."
    assert all("\x00" not in p.text for p in doc.passages)
