"""What reading a PDF needs regardless of which parser reads it.

Page geometry, the scan test, and the Markdown post-processing that runs on
whatever the parser produced: page markers, the heading a page selection
needs, the density warning and the check that an OCR pass actually returned
words. None of it knows which engine wrote the Markdown, which is the point
-- these rules are about the book and the page, not about the parser, and a
second copy of them inside an adapter would be a second set of answers.
"""

import ctypes
import re
from pathlib import Path

from .bundle import parse_pages
from .errors import PipelineError
from .messages import (
    OCR_EMPTY,
    OCR_EMPTY_PAGES,
    PDF_ROUTE_NOT_INSTALLED,
    PDFIUM_UNUSABLE,
)

STAGE = "extract"

# An HTML comment is a block the Markdown loader passes through untouched
# and Pandoc drops from the rendered book, so the provenance marker
# survives translation without becoming prose. Pages are numbered from 1.
PAGE_MARKER = re.compile(r"<!--\s*page\s+(\d+)\s*-->")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")

# More prose than a printed page can show. A dense two-column page at nine
# points holds six or seven thousand characters; the measured failure
# (arXiv 2609.20519, page 1) returned a hundred thousand. Well above the
# first, well below the second; a warning, not a refusal.
PAGE_CHARS_LIMIT = 12000

# A page is a scan when a picture covers this much of it and the text
# layer holds fewer than this many characters: a stamped page number or a
# running header over a scanned page is not a text layer. Guards, not
# measurements; a blank page (no picture, no text) also counts as unread.
SCAN_IMAGE_AREA = 0.6
SCAN_MAX_CHARS = 200


def _pdfium():
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as raw
    except ImportError as err:
        raise PipelineError(PDF_ROUTE_NOT_INSTALLED.format(err=err), stage=STAGE)
    if not hasattr(pdfium, "PdfDocument"):
        raise PipelineError(PDFIUM_UNUSABLE, stage=STAGE)
    return pdfium, raw


def _transform(matrix, box):
    a, b, c, d, e, f = matrix
    left, bottom, right, top = box
    corners = [
        (a * x + c * y + e, b * x + d * y + f)
        for x, y in ((left, bottom), (right, bottom), (left, top), (right, top))
    ]
    return (
        min(x for x, _ in corners),
        min(y for _, y in corners),
        max(x for x, _ in corners),
        max(y for _, y in corners),
    )


def _intersect(first, second):
    left = max(first[0], second[0])
    bottom = max(first[1], second[1])
    right = min(first[2], second[2])
    top = min(first[3], second[3])
    if right <= left or top <= bottom:
        return (0.0, 0.0, 0.0, 0.0)
    return (left, bottom, right, top)


def _area(box):
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _clip_box(raw, pageobj):
    """The box an object's clip confines it to, in its parent's space.

    A clip is the intersection of its paths, so each path's bounding box
    narrows the result; a path that is not a rectangle is over-approximated
    by its box, which can only count text as visible, never as hidden.
    """
    clip = raw.FPDFPageObj_GetClipPath(pageobj)
    if not clip:
        return None
    box = None
    for index in range(raw.FPDFClipPath_CountPaths(clip)):
        xs, ys = [], []
        for segment in range(raw.FPDFClipPath_CountPathSegments(clip, index)):
            point = raw.FPDFClipPath_GetPathSegment(clip, index, segment)
            x, y = ctypes.c_float(), ctypes.c_float()
            if raw.FPDFPathSegment_GetPoint(point, x, y):
                xs.append(x.value)
                ys.append(y.value)
        if not xs:
            continue
        own = (min(xs), min(ys), max(xs), max(ys))
        box = own if box is None else _intersect(box, own)
    return box



def _page_space(raw, obj):
    """`(box, clip)` of an object in page space.

    pdfium keeps each object's bounds and clip in its parent's coordinate
    space. The object's own clip comes first; walking outward, each
    ancestor's matrix carries both into the next space up and the
    ancestor's clip narrows what is left.
    """
    box = obj.get_bounds()
    clip = _clip_box(raw, obj.raw)
    ancestor = obj.container
    while ancestor is not None:
        matrix = ancestor.get_matrix().get()
        box = _transform(matrix, box)
        if clip is not None:
            clip = _transform(matrix, clip)
        own = _clip_box(raw, ancestor.raw)
        if own is not None:
            clip = own if clip is None else _intersect(clip, own)
        ancestor = ancestor.container
    return box, clip



def _page_box(page):
    # The effective page: media box cut to the crop box, inherited or not.
    left, bottom, right, top = page.get_bbox()
    return (float(left), float(bottom), float(right), float(top))


def picture_share(page):
    """How much of the effective page its pictures cover, 0 to 1.

    Pictures at any depth, in page space, cut to their clips: a scanner's
    page image is as often wrapped in a form as drawn directly. Overlaps
    are summed, not unioned; two pictures each covering half a page make
    a scan by this measure too, which is the right answer for a page that
    is pictures and nothing else.
    """
    pdfium, raw = _pdfium()
    page_box = _page_box(page)
    total = _area(page_box)
    if total <= 0:
        return 0.0
    covered = 0.0
    for obj in page.get_objects(max_depth=16):
        if obj.type != raw.FPDF_PAGEOBJ_IMAGE:
            continue
        box, clip = _page_space(raw, obj)
        shown = _intersect(box, page_box)
        if clip is not None:
            shown = _intersect(shown, clip)
        covered += _area(shown)
    return min(1.0, covered / total)

def text_layer_report(pdf_path, page_range=None):
    """`(pages the text layer does not spell out, pages examined)`, from 1.

    pypdfium2 comes with the pdf extra for exactly this, and reading what
    the page itself says is the only honest way to know whether the models
    will find anything: a page with no characters, or a page that is one
    big picture with a few characters stamped on it, has to be read by OCR
    or not at all.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError as err:
        raise PipelineError(PDF_ROUTE_NOT_INSTALLED.format(err=err), stage=STAGE)
    if not hasattr(pdfium, "PdfDocument"):
        # An empty `pypdfium2` directory left behind by an uninstall imports
        # perfectly well and can do nothing; say that, rather than blaming
        # the PDF for it.
        raise PipelineError(PDFIUM_UNUSABLE, stage=STAGE)
    ranges = parse_pages(page_range)
    missing = []
    examined = 0
    try:
        document = pdfium.PdfDocument(str(pdf_path))
    except Exception as err:
        raise PipelineError(
            f"{Path(pdf_path).name} could not be opened as a PDF: "
            f"{type(err).__name__}: {err}",
            stage=STAGE,
        )
    try:
        for number in range(1, len(document) + 1):
            if ranges and not any(start <= number <= end for start, end in ranges):
                continue
            examined += 1
            page = document[number - 1]
            textpage = page.get_textpage()
            try:
                text = textpage.get_text_bounded()
            finally:
                textpage.close()
            chars = len(text.strip())
            if not chars or (
                chars < SCAN_MAX_CHARS and picture_share(page) >= SCAN_IMAGE_AREA
            ):
                missing.append(number)
            page.close()
    except Exception as err:
        raise PipelineError(
            f"{Path(pdf_path).name} could not be read page by page: "
            f"{type(err).__name__}: {err}",
            stage=STAGE,
        )
    finally:
        document.close()

def _prose(chunk):
    """What is left of a chunk once markers and pictures are removed."""
    return IMAGE.sub(" ", COMMENT.sub(" ", chunk)).strip()


FIRST_CONTENT_HEADING = re.compile(r"^#{1,6}\s+\S")
LEADING_MARKER = re.compile(r"^<!--.*-->$")


def first_selected_page(page_range):
    """The first page of a selection, from 1; None for the whole PDF."""
    ranges = parse_pages(page_range)
    return ranges[0][0] if ranges else None


def heading_for_mid_section(markdown_text, first_page):
    """The Markdown with a heading above a selection that starts mid-section.

    A page selection normally begins inside a section, so the extraction
    opens with prose the previous pages' heading would have owned. The
    EPUB's table of contents follows the headings and the export refuses
    prose above the first one, so the extraction gets a heading naming the
    page it starts on -- written into source.md, where the operator sees
    it before anything is paid for and can rename it. Only a selection
    that starts after page 1 gets one; the document's own first page owns
    its front matter, and a selection whose first content is a heading
    needs nothing. Returns None when nothing was added.
    """
    if not first_page or first_page < 2:
        return None
    lines = markdown_text.splitlines()
    insert_at = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or LEADING_MARKER.match(stripped):
            insert_at = index + 1
            continue
        if FIRST_CONTENT_HEADING.match(stripped):
            return None
        break
    else:
        return None  # nothing but markers: no prose to head
    heading = [f"# Page {first_page}", ""]
    if insert_at and lines[insert_at - 1].strip():
        heading.insert(0, "")
    return "\n".join(lines[:insert_at] + heading + lines[insert_at:]) + "\n"


def blank_pages(markdown_text):
    """`(page numbers that carry no prose, whether any page does)`."""
    parts = PAGE_MARKER.split(markdown_text)
    any_prose = bool(_prose(parts[0]))
    blank = []
    for number, body in zip(parts[1::2], parts[2::2]):
        if _prose(body):
            any_prose = True
        else:
            blank.append(int(number))
    return blank, any_prose


def dense_pages(markdown_text, limit=PAGE_CHARS_LIMIT):
    """`[(page number, characters)]` for pages carrying more prose than fits."""
    parts = PAGE_MARKER.split(markdown_text)
    dense = []
    for number, body in zip(parts[1::2], parts[2::2]):
        chars = len(_prose(body))
        if chars > limit:
            dense.append((int(number), chars))
    return dense


def check_recognised_text(markdown_path, missing):
    """What the OCR pass actually returned for the pages that needed it.

    A conversion that sends every page to the models and still comes back
    with nothing but pictures has not read the book, and saying "completed"
    over that is the silent failure this pipeline refuses. A single page
    that came back empty is reported instead of refused: a plate with no
    words on it is a legitimate empty page.
    """
    if not missing:
        return []
    blank, any_prose = blank_pages(markdown_path.read_text(encoding="utf-8"))
    if not any_prose:
        raise PipelineError(OCR_EMPTY, stage=STAGE)
    silent = sorted(set(blank) & set(missing))
    if silent:
        print(OCR_EMPTY_PAGES.format(pages=", ".join(str(n) for n in silent)))
    return silent


