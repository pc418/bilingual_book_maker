"""Heading levels read from the page, for a parser that gives none.

docling finds a paper's headings almost without fail (195 of 196 on the
260922 corpus of twenty arXiv papers) and then writes every one of them,
the title included, at the same level: `##`. The EPUB's table of contents
is that outline, so it came out flat. Measured on that corpus, 42.6% of
heading levels were exact as shipped.

The level is decided here, before the export, from two things the page
can tell us:

1. **Section numbering**, when a heading carries it: `1 Introduction` is
   level 2, `1.1` level 3, `1.1.1` level 4; `I.` is 2, `A.` is 3, `1)` is
   4. Numbering is the author's own statement of depth and beats any
   typographic guess -- `3.1` and `3.2.1` can be set in the same font.
2. **Typographic style** otherwise: the rendered font size and whether the
   glyphs under the heading's box are bold, read from the PDF's text layer
   with pypdfium2. The heading in the largest style is level 1 (the
   title); any other unnumbered heading takes the level of a numbered
   heading set in the same style, else level 2.

Measured 260922 (headings-eval, same corpus): 95.9% of levels exact, 19 of
20 papers entirely right. The one miss is a paper with no numbering whose
two heading levels share one font. A scanned page has no glyphs, so only
rule 1 applies there and everything unnumbered is level 2.

The style rank -- one level per distinct (size, weight) pair, largest
first -- is opendataloader-pdf's `HeadingProcessor.detectHeadingsLevels`
(Apache License 2.0, Copyright Hancom Inc.; `HeadingProcessor.java`,
`TextNodeStatistics.java`, `ModeWeightStatistics.java`), written afresh in
Python. Its rendered-size measure (the `Tf` size times the text matrix
scale) and font-weight fallback follow the same idea. Nothing of
veraPDF's heading *detector* is used: docling's is far better, and that
code is under a different licence.
"""

import collections
import ctypes
import re

# `1 Intro`, `2.3 Method`, `2.3.1 Detail`; a trailing dot after the number
# is allowed (`1. Introduction`).
NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+\S")
ROMAN = re.compile(r"^[IVXL]+\.\s+\S")
LETTER = re.compile(r"^[A-H]\.\s+\S")
PAREN = re.compile(r"^\d+\)\s+\S")
MAX_LEVEL = 6
# A font descriptor weight from here up is bold. 600 is semibold; on a
# heading that reads as bold.
BOLD = 600
# When the PDF gives no /FontWeight (the base-14 Times-Bold, for one), the
# face name says it; order matters, `Bold` after `SemiBold`.
NAME_WEIGHTS = (
    ("Thin", 100),
    ("ExtraLight", 200),
    ("UltraLight", 200),
    ("Light", 300),
    ("SemiBold", 600),
    ("Semibold", 600),
    ("DemiBold", 600),
    ("ExtraBold", 800),
    ("UltraBold", 800),
    ("Bold", 700),
    ("Black", 900),
    ("Heavy", 900),
    ("Medium", 500),
    ("Regular", 400),
    ("Book", 400),
)
# Sizes within half a point are one style: TeX sets 9.96 and 10.0.
SIZE_STEP = 0.5


def numbering_level(text):
    """The level a heading's own numbering states, or None."""
    text = text.strip()
    numbered = NUMBERED.match(text)
    if numbered:
        return min(MAX_LEVEL, numbered.group(1).count(".") + 2)
    if ROMAN.match(text):
        return 2
    if LETTER.match(text):
        return 3
    if PAREN.match(text):
        return 4
    return None


def levels(headings):
    """Levels for `[(text, style or None), ...]`, in that order.

    A style is `(size, bold)`. The rule is the module docstring's.
    """
    styled = [style for _text, style in headings if style is not None]
    top = max(styled) if styled else None
    by_style = {}
    for text, style in headings:
        number = numbering_level(text)
        if number and style is not None:
            by_style.setdefault(style, number)
    out = []
    for text, style in headings:
        number = numbering_level(text)
        if number:
            out.append(number)
        elif style is not None and style == top:
            out.append(1)
        else:
            out.append(by_style.get(style, 2))
    return out


def _weight(raw, textpage, index, buffer, flags):
    weight = raw.FPDFText_GetFontWeight(textpage, index)
    if weight > 0:
        return weight
    raw.FPDFText_GetFontInfo(textpage, index, buffer, len(buffer), ctypes.byref(flags))
    name = buffer.value.decode("latin1", "replace")
    return next((value for key, value in NAME_WEIGHTS if key in name), 400)


def _glyphs(raw, textpage):
    """`(x, y, rendered size, weight)` per visible character."""
    buffer = ctypes.create_string_buffer(256)
    flags = ctypes.c_int()
    matrix = raw.FS_MATRIX()
    for index in range(textpage.count_chars()):
        char = chr(raw.FPDFText_GetUnicode(textpage, index))
        if char.isspace() or char == "\x00":
            continue
        left, bottom, right, top = textpage.get_charbox(index)
        raw.FPDFText_GetMatrix(textpage, index, ctypes.byref(matrix))
        scale = (matrix.c * matrix.c + matrix.d * matrix.d) ** 0.5
        size = raw.FPDFText_GetFontSize(textpage, index) * scale
        yield (
            (left + right) / 2,
            (bottom + top) / 2,
            size,
            _weight(raw, textpage, index, buffer, flags),
        )


def styles(pdf_path, boxes):
    """`{index: (size, bold) or None}` for `boxes = {index: (page, box)}`.

    The box is docling's: PDF points, bottom-left origin, relative to the
    CropBox; pdfium's character boxes are in user space, so the CropBox
    origin is subtracted. A box with no glyphs under it (a scan, a rotated
    page) gets None.
    """
    from .pdf_common import _pdfium

    pdfium, raw = _pdfium()
    by_page = collections.defaultdict(list)
    for index, (page_number, box) in boxes.items():
        by_page[page_number].append((index, box))
    found = {index: None for index in boxes}
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        for page_number, wanted in by_page.items():
            page = document[page_number - 1]
            left, bottom = page.get_bbox()[:2]
            textpage = page.get_textpage()
            glyphs = [
                (x - left, y - bottom, size, weight)
                for x, y, size, weight in _glyphs(raw, textpage)
            ]
            for index, (l, b, r, t) in wanted:
                inside = [
                    (size, weight)
                    for x, y, size, weight in glyphs
                    if l - 1 <= x <= r + 1 and b - 1 <= y <= t + 1
                ]
                if not inside:
                    continue
                size = max(size for size, _weight in inside)
                weight = collections.Counter(w for _s, w in inside).most_common(1)[0][0]
                found[index] = (round(size / SIZE_STEP) * SIZE_STEP, weight >= BOLD)
    finally:
        document.close()
    return found


def assign(document, pdf_path):
    """Set every section header's level. Returns `(headings, with a style)`."""
    items = []
    for item, _level in document.iterate_items():
        if str(getattr(item, "label", "")).lower() != "section_header":
            continue
        items.append(item)
    boxes = {}
    for index, item in enumerate(items):
        prov = list(getattr(item, "prov", None) or [])
        if prov:
            box = prov[0].bbox
            boxes[index] = (
                prov[0].page_no,
                (float(box.l), float(box.b), float(box.r), float(box.t)),
            )
    found = styles(pdf_path, boxes) if boxes else {}
    headings = [(item.text or "", found.get(index)) for index, item in enumerate(items)]
    for item, level in zip(items, levels(headings)):
        item.level = level
    return len(items), sum(style is not None for _text, style in headings)


_ATX = re.compile(r"^#(#+\s)", re.M)


def promote(markdown):
    """Every heading one level up.

    The serializer writes a section header with one hash more than its
    level, keeping `#` for a title item, so with levels assigned the top
    heading arrives as `##`. Lifting every `##`-or-deeper line by one puts
    it at `#`, and a title item already there stays.
    """
    return _ATX.sub(r"\1", markdown)
