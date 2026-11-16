"""Generates the PDF documents of the evaluation corpus. Needs reportlab.

    pip install reportlab && python eval/tools/make_pdfs.py eval/corpus

The generated PDFs are committed, so this only needs running to change them.
"""
import sys
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

TITLE = ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=20, leading=24, spaceAfter=6)
META = ParagraphStyle("meta", fontName="Helvetica", fontSize=10, leading=13, textColor="#555555", spaceAfter=12)
H = ParagraphStyle("h", fontName="Helvetica-Bold", fontSize=13, leading=16, spaceBefore=12, spaceAfter=5)
BODY = ParagraphStyle("body", fontName="Helvetica", fontSize=11, leading=14, spaceAfter=6)


def footer(canvas, doc):
    canvas.setFont("Helvetica", 8)
    canvas.drawString(60, 30, f"Northwind Analytics | Internal | Page {doc.page}")


def build(path: Path, title: str, meta: str, blocks: list, with_footer: bool = False):
    story = [Paragraph(title, TITLE), Paragraph(meta, META)]
    for kind, text in blocks:
        if kind == "h":
            story.append(Paragraph(text, H))
        elif kind == "break":
            story.append(PageBreak())
        else:
            story.append(Paragraph(text, BODY))
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=60, rightMargin=60, topMargin=50, bottomMargin=50)
    if with_footer:
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
    else:
        doc.build(story)


def main(out: Path):
    # Numbered clauses over three pages, with a running footer.
    build(out / "business-continuity-plan.pdf", "Business Continuity Plan",
          "Northwind Analytics Pty Ltd | Version 2.0 | Owner: Risk and Compliance", [
        ("h", "1. Purpose"),
        ("p", "This plan describes how Northwind Analytics continues to operate during a major disruption to its offices, people or systems."),
        ("h", "2. Activation"),
        ("p", "2.1 The plan is activated by the Chief Operating Officer or, in their absence, the Head of Risk."),
        ("p", "2.2 The crisis management team must convene within 60 minutes of activation."),
        ("p", "2.3 Staff are notified of an activation through the emergency text message service."),
        ("break", ""),
        ("h", "3. Alternate working arrangements"),
        ("p", "3.1 If the main office is unavailable, staff work remotely until the office is declared safe."),
        ("p", "3.2 The alternate site at the Parramatta office has 40 desks reserved for critical teams."),
        ("p", "3.3 Critical teams are customer support, platform operations and payroll."),
        ("h", "4. Communication"),
        ("p", "4.1 Only the Communications Lead may speak to the media during a disruption."),
        ("p", "4.2 Customers are updated through the status page at least every 2 hours while the disruption continues."),
        ("break", ""),
        ("h", "5. Testing and review"),
        ("p", "5.1 The plan is tested with a desktop exercise twice a year."),
        ("p", "5.2 The plan is reviewed after every activation and at least once every 12 months."),
    ], with_footer=True)

    # Paragraph style, no clause numbers.
    build(out / "ai-tools-usage-guideline.pdf", "AI Tools Usage Guideline",
          "Northwind Analytics Pty Ltd | Version 1.0 | Owner: Technology Office", [
        ("h", "Approved tools"),
        ("p", "Staff may use the company-licensed AI coding assistant and the internal chat assistant for work. Other AI tools must be approved by the Technology Office before they are used with company information."),
        ("h", "Information you must not share"),
        ("p", "Confidential or Restricted information must never be entered into a public AI tool. Customer data must not be used to train any external model."),
        ("h", "Reviewing output"),
        ("p", "Code produced with an AI assistant must be reviewed by a person before it is merged. The author remains responsible for the accuracy of anything they submit, whether or not an AI tool helped to write it."),
        ("h", "Disclosure"),
        ("p", "Client deliverables that contain AI-generated content must say so in the document history."),
    ])

    build(out / "clean-desk-and-printing-policy.pdf", "Clean Desk and Printing Policy",
          "Northwind Analytics Pty Ltd | Version 1.5 | Owner: Facilities", [
        ("h", "1. Clean desk"),
        ("p", "1.1 Desks must be cleared of papers and removable media at the end of each working day."),
        ("p", "1.2 Screens must be locked whenever a workstation is left unattended."),
        ("p", "1.3 Whiteboards in meeting rooms must be wiped after use."),
        ("h", "2. Printing"),
        ("p", "2.1 Print jobs are released at the printer with an access card."),
        ("p", "2.2 Print jobs that are not collected are deleted after 12 hours."),
        ("p", "2.3 Colour printing is limited to client-facing material."),
    ])


if __name__ == "__main__":
    main(Path(sys.argv[1]))
