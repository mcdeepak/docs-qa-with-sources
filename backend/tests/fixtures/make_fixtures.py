"""Generates the PDF fixtures used by test_extract.py. Needs reportlab.

    pip install reportlab && python backend/tests/fixtures/make_fixtures.py

The generated PDFs are committed, so this only needs running to change them.
"""
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate

HERE = Path(__file__).parent
TITLE = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=20, leading=24, spaceAfter=10)
H = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=13, leading=16, spaceBefore=12, spaceAfter=5)
BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=11, leading=14, spaceAfter=6)


def footer(canvas, doc):
    canvas.setFont("Helvetica", 8)
    canvas.drawString(60, 30, f"Northwind Analytics | Page {doc.page} of 3")


def main():
    # Three pages. Body lines that differ only by digits must survive; the footer must not.
    story = [Paragraph("Support Response Standard", TITLE), Paragraph("1. Targets", H)]
    for level, minutes in (("P1", 15), ("P2", 30), ("P3", 60)):
        story.append(Paragraph(f"{level} {minutes} minutes", BODY))
    story += [
        Paragraph("Overtime is paid at", BODY),
        Paragraph("1.5 times the hourly rate after 38 hours in a week.", BODY),
        PageBreak(), Paragraph("2. Escalation", H),
        Paragraph("2.1 Escalate to the duty manager if a target is missed.", BODY),
        PageBreak(), Paragraph("3. Review", H),
        Paragraph("3.1 Targets are reviewed every year.", BODY),
    ]
    doc = SimpleDocTemplate(str(HERE / "repeating-lines.pdf"), pagesize=A4, leftMargin=60,
                            rightMargin=60, topMargin=50, bottomMargin=50)
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


if __name__ == "__main__":
    main()
