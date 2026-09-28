"""Render a tailored selection as two Word files, a CV and a cover letter.

The layout copies the candidate's own CV rather than inventing one:
Calibri throughout, A4, half-inch side margins, a sixteen point name, ten point
body, grey for secondary text, and a rule under each section heading. Matching the
document already in use means a tailored copy looks like that CV and not like
something a script produced.

Nothing here calls a model. Every choice was made upstream, so this is rendering
only and costs a few milliseconds per file.
"""

from __future__ import annotations

import pathlib

import cv_layout
import settings
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

# Taken from the existing CV, in the units python-docx expects.
FONT = "Calibri"
NAME_PT, HEAD_PT, BODY_PT, SMALL_PT = 16, 10.5, 10, 9.5
INK = RGBColor(0x22, 0x22, 0x22)
GREY = RGBColor(0x55, 0x55, 0x55)
RULE = "999999"

# Read from .env, so the repository carries no personal detail.
CONTACT = "  |  ".join(settings.contact_parts())
NAME = settings.candidate_name()


def new_document() -> Document:
    """Start a document with the page setup and base font of the original."""
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21.0), Cm(29.7)
    section.left_margin = section.right_margin = Cm(1.27)
    section.top_margin, section.bottom_margin = Cm(1.0), Cm(0.9)

    normal = document.styles["Normal"]
    normal.font.name = FONT
    normal.font.size = Pt(BODY_PT)
    normal.font.color.rgb = INK
    # python-docx sets only the Latin font, so east-asian has to be set in the XML.
    normal.element.rPr.rFonts.set(qn("w:eastAsia"), FONT)
    normal.paragraph_format.space_after = Pt(2)
    normal.paragraph_format.line_spacing = 1.08
    return document


def bottom_rule(paragraph) -> None:
    """Draw a hairline under a paragraph, which is how the original marks a section."""
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "4")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), RULE)
    borders.append(bottom)
    paragraph._p.get_or_add_pPr().append(borders)


def line(document, text: str, size: float = BODY_PT, bold: bool = False,
         colour: RGBColor = INK, before: float = 0, after: float = 2,
         align=None):
    """Add one paragraph, because every visual choice here is per paragraph."""
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(before)
    paragraph.paragraph_format.space_after = Pt(after)
    if align is not None:
        paragraph.alignment = align
    run = paragraph.add_run(text)
    run.font.name = FONT
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.color.rgb = colour
    return paragraph


def heading(document, text: str):
    """A small capitalised heading with a rule under it."""
    paragraph = line(document, text.upper(), size=SMALL_PT, bold=True,
                     colour=GREY, before=9, after=3)
    paragraph.runs[0].font.name = FONT
    bottom_rule(paragraph)
    return paragraph


def bullet(document, text: str):
    """A bullet at a fixed indent, using the built-in list style."""
    paragraph = document.add_paragraph(style="List Bullet")
    paragraph.paragraph_format.space_after = Pt(2)
    paragraph.paragraph_format.left_indent = Cm(0.5)
    run = paragraph.add_run(text)
    run.font.name = FONT
    run.font.size = Pt(BODY_PT)
    run.font.color.rgb = INK
    return paragraph


def labelled(document, label: str, text: str):
    """A skills line with its category in bold, the way the original CV sets it."""
    paragraph = line(document, f"{label}: ", bold=True, after=1)
    run = paragraph.add_run(text)
    run.font.name, run.font.size, run.font.color.rgb = FONT, Pt(BODY_PT), INK
    return paragraph


def render_cv(chosen: dict, facts: dict[str, str], path: pathlib.Path) -> None:
    """Write the CV in the fixed layout of the original, filled with the selected content."""
    layout = cv_layout.build(chosen, facts)
    document = new_document()

    line(document, NAME, size=NAME_PT, bold=True, after=0)
    line(document, layout.headline, size=HEAD_PT, colour=GREY, after=1)
    line(document, CONTACT, size=SMALL_PT, colour=GREY, after=4)

    heading(document, "Professional Summary")
    line(document, layout.summary)

    for name in layout.order:
        heading(document, cv_layout.TITLES[name])
        if name == "skills":
            for label, text in layout.skills:
                labelled(document, label, text)
        elif name == "education":
            for item in layout.education:
                line(document, item, after=1)
        else:
            for block in getattr(layout, name):
                line(document, block.header, bold=True, before=4, after=1)
                for item in block.bullets:
                    bullet(document, item)

    document.save(path)


def to_pdf(docx: pathlib.Path) -> pathlib.Path | None:
    """Export through Word itself, so the PDF looks exactly like the Word file. None when Word is unavailable."""
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        return None
    # Word is a COM server, and every thread that talks to it must initialise COM first;
    # the page's buttons run this on a worker thread.
    pythoncom.CoInitialize()
    pdf = docx.with_suffix(".pdf")
    word = win32com.client.DispatchEx("Word.Application")
    word.Visible = False
    try:
        document = word.Documents.Open(str(docx.resolve()), ReadOnly=True)
        document.SaveAs2(str(pdf.resolve()), FileFormat=17)  # 17 is PDF
        document.Close(False)
    finally:
        word.Quit()
    return pdf


def render_cover(chosen: dict, job: dict, path: pathlib.Path) -> None:
    """Write the cover letter as its own file, because many forms ask for one."""
    document = new_document()

    line(document, NAME, size=NAME_PT, bold=True, after=0)
    line(document, CONTACT, size=SMALL_PT, colour=GREY, after=10)

    line(document, job.get("company", ""), bold=True, after=0)
    line(document, f"Re: {job.get('title', '')}", colour=GREY, after=10)

    line(document, "Hello,", after=8)
    for part in [p.strip() for p in chosen["cover_note"].split("\n") if p.strip()]:
        line(document, part, after=8)

    line(document, "Thank you for your time,", before=8, after=0)
    line(document, NAME, after=0)

    document.save(path)


def render_both(chosen: dict, facts: dict[str, str], job: dict, folder: pathlib.Path, stem: str) -> list[pathlib.Path]:
    """Write both files and return what was written, so the caller can report it."""
    folder.mkdir(parents=True, exist_ok=True)
    cv = folder / f"{stem}-cv.docx"
    cover = folder / f"{stem}-cover-letter.docx"
    render_cv(chosen, facts, cv)
    render_cover(chosen, job, cover)
    written = [cv, cover]
    # A PDF of each is what gets sent, because it looks the same in every viewer and every ATS.
    for document in (cv, cover):
        pdf = to_pdf(document)
        if pdf:
            written.append(pdf)
    return written
