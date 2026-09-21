"""Text a PDF carries but never shows, taken out before the extractor sees it.

Measured on arXiv 2609.20519, page 1: 3,325 characters are visible and
85,525 are not. The teaser figure is a vector drawing whose whole content
is instantiated some 140 times, each copy under a small clip window that
shows one tile of it, and every extractor tried -- OpenDataLoader's Java
engine, its docling backend, poppler -- reads all 140 copies, because none
of them honours clipping. The reading edition then held 9,386 translation
units for a fifteen-page paper, most of them "(a) Previous methods".

pdfium exposes the clips, on the form objects rather than on the text
inside them, so the text's visibility is computed here by composing each
ancestor's matrix and clip outward to page space. A page-level object with
more than a little clipped-away text under it is a figure, and a figure is
what the reader wants to see, not read: it is rendered on its own, removed
from the page and put back as that picture, in a sanitized copy of the
PDF that the extractor reads instead of the original. Everything else on
the page stays as it was, text included.
"""

import ctypes
from collections import defaultdict
from pathlib import Path

from .bundle import parse_pages
from .errors import PipelineError

STAGE = "extract"

# How much clipped-away text makes a page-level object a figure to
# rasterize. A legitimate chart clips the odd axis label; the measured
# failure hid tens of thousands of characters. Two hundred is far above
# the first and far below the second; it is a guard, not a measurement.
HIDDEN_TEXT_THRESHOLD = 200
# Below this share of its box inside the effective clip, a text object is
# counted as hidden. Half: a label cut in two is still shown.
VISIBLE_FRACTION = 0.5
# Resolution of the picture that replaces a figure. Body text in a figure
# is small; 200 DPI keeps it legible in a reader without the file bloating.
RASTER_DPI = 200

# A page-level form that draws is a figure when it covers this much of the
# page or more -- below it, a bullet or a logo glyph -- and less than the
# whole-page share, above which it is a wrapper some producers put every
# page in (pdfpages, print-to-PDF), whose text must stay text.
FIGURE_MIN_AREA = 0.02
WHOLE_PAGE_AREA = 0.7

SANITIZED_DIR = "sanitized"


def _pdfium():
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as raw
    except ImportError as err:
        raise PipelineError(
            f"the OpenDataLoader hybrid stack is not installed "
            f'(pip install "opendataloader-pdf[hybrid]"): {err}',
            stage=STAGE,
        )
    if not hasattr(pdfium, "PdfDocument"):
        raise PipelineError(
            "pypdfium2 is installed but unusable (no PdfDocument); reinstall "
            'it with pip install "opendataloader-pdf[hybrid]"',
            stage=STAGE,
        )
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
    """The bounding box of an object's clip path, in its parent's space."""
    clip = raw.FPDFPageObj_GetClipPath(pageobj)
    if not clip:
        return None
    xs, ys = [], []
    for index in range(raw.FPDFClipPath_CountPaths(clip)):
        for segment in range(raw.FPDFClipPath_CountPathSegments(clip, index)):
            point = raw.FPDFClipPath_GetPathSegment(clip, index, segment)
            x, y = ctypes.c_float(), ctypes.c_float()
            if raw.FPDFPathSegment_GetPoint(point, x, y):
                xs.append(x.value)
                ys.append(y.value)
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _text_length(raw, pageobj, textpage):
    count = raw.FPDFTextObj_GetText(pageobj, textpage, None, 0)
    if count <= 0:
        return 0
    buffer = (ctypes.c_ushort * count)()
    raw.FPDFTextObj_GetText(pageobj, textpage, buffer, count)
    return len(bytes(buffer).decode("utf-16-le", errors="replace").rstrip("\x00"))


def _page_space(raw, obj, top):
    """`(box, clip)` of an object nested under `top`, in page space.

    pdfium keeps each object's bounds and clip in its parent's coordinate
    space; walking outward, each ancestor's matrix carries both into the
    next space up and its own clip narrows what is left.
    """
    box = obj.get_bounds()
    clip = None
    ancestor = obj.container if obj is not top else None
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


def figure_report(document, page_numbers):
    """Page-level objects to rasterize, per page, numbered from 1.

    `[{"page": n, "objects": [{"index": i, "reason": r, "hidden": chars,
    "visible": chars, "bounds": [l, b, r, t]}]}]`, listing only pages with
    at least one. Two reasons: `hidden-text`, a form hiding at least
    `HIDDEN_TEXT_THRESHOLD` characters under its clips; `figure`, a form
    that draws (paths or pictures) and covers between `FIGURE_MIN_AREA`
    and `WHOLE_PAGE_AREA` of the page -- an included drawing, whose axis
    labels the extractor would otherwise return as paragraphs and whose
    lines it would not return at all.
    """
    pdfium, raw = _pdfium()
    report = []
    for number in page_numbers:
        page = document[number - 1]
        width, height = page.get_size()
        page_box = (0.0, 0.0, float(width), float(height))
        textpage = page.get_textpage()
        flagged = []
        try:
            for index, top in enumerate(page.get_objects(max_depth=0)):
                if top.type != raw.FPDF_PAGEOBJ_FORM:
                    continue
                hidden = visible = drawings = 0
                for obj in page.get_objects(max_depth=16, form=top, level=1):
                    if obj.type in (raw.FPDF_PAGEOBJ_PATH, raw.FPDF_PAGEOBJ_IMAGE):
                        drawings += 1
                        continue
                    if obj.type != raw.FPDF_PAGEOBJ_TEXT:
                        continue
                    length = _text_length(raw, obj.raw, textpage.raw)
                    if not length:
                        continue
                    box, clip = _page_space(raw, obj, top)
                    shown = _intersect(box, page_box)
                    if clip is not None:
                        shown = _intersect(shown, clip)
                    fraction = _area(shown) / _area(box) if _area(box) > 0 else 1.0
                    if fraction < VISIBLE_FRACTION:
                        hidden += length
                    else:
                        visible += length
                bounds = top.get_bounds()
                area = _area(_intersect(bounds, page_box)) / _area(page_box)
                if hidden >= HIDDEN_TEXT_THRESHOLD:
                    reason = "hidden-text"
                elif drawings and FIGURE_MIN_AREA <= area < WHOLE_PAGE_AREA:
                    reason = "figure"
                else:
                    continue
                flagged.append(
                    {
                        "index": index,
                        "reason": reason,
                        "hidden": hidden,
                        "visible": visible,
                        "bounds": [round(v, 2) for v in bounds],
                    }
                )
        finally:
            textpage.close()
        if flagged:
            report.append({"page": number, "objects": flagged})
    return report


def _address(obj):
    return ctypes.cast(obj.raw, ctypes.c_void_p).value


def _index_of(page, target):
    wanted = _address(target)
    for index, obj in enumerate(page.get_objects(max_depth=0)):
        if _address(obj) == wanted:
            return index
    raise PipelineError("a page object vanished while it was being examined", STAGE)


def _render_alone(pdfium, document, page_number, index, bounds, dpi):
    """`(picture, box)`: the object at `index` on that page drawn on its own.

    Drawn over nothing, so the box is tightened to the pixels the object
    actually put down: a figure's bounds run to wherever its clipped-away
    content reached, which is not where the reader sees it.
    """
    scratch = pdfium.PdfDocument.new()
    try:
        scratch.import_pages(document, pages=[page_number - 1])
        page = scratch[0]
        width, height = page.get_size()
        for position, obj in reversed(list(enumerate(page.get_objects(max_depth=0)))):
            if position != index:
                page.remove_obj(obj)
                obj.close()
        page.gen_content()
        left, bottom, right, top = _intersect(bounds, (0, 0, width, height))
        if _area((left, bottom, right, top)) <= 0:
            return None, None
        scale = dpi / 72.0
        bitmap = page.render(
            scale=scale,
            crop=(left, bottom, width - right, height - top),
            fill_color=(255, 255, 255, 0),
        )
        drawn = bitmap.to_pil().convert("RGBA")
        inked = drawn.getbbox()
        if inked is None:
            return None, None
        drawn = drawn.crop(inked)
        placed = (
            left + inked[0] / scale,
            top - inked[3] / scale,
            left + inked[2] / scale,
            top - inked[1] / scale,
        )
        from PIL import Image

        picture = Image.new("RGB", drawn.size, (255, 255, 255))
        picture.paste(drawn, mask=drawn.getchannel("A"))
        return picture, placed
    finally:
        scratch.close()


def sanitize_pdf(pdf_path, output_dir, page_range=None, *, dpi=RASTER_DPI):
    """A copy of the PDF with its figures rasterized, or None.

    Returns `(path or None, report)`: the path of the sanitized copy under
    `output_dir` when any page changed, and the `figure_report` of what
    was found. The original is never written to.
    """
    pdfium, raw = _pdfium()
    pdf = Path(pdf_path)
    try:
        document = pdfium.PdfDocument(str(pdf))
    except Exception as err:
        raise PipelineError(
            f"{pdf.name} could not be opened as a PDF: {type(err).__name__}: {err}",
            stage=STAGE,
        )
    try:
        ranges = parse_pages(page_range)
        numbers = [
            number
            for number in range(1, len(document) + 1)
            if not ranges or any(start <= number <= end for start, end in ranges)
        ]
        report = figure_report(document, numbers)
        if not report:
            return None, report
        for entry in report:
            page = document[entry["page"] - 1]
            # Highest index first, so removing one object does not shift
            # the index of the next.
            for flagged in sorted(entry["objects"], key=lambda f: -f["index"]):
                image, placed = _render_alone(
                    pdfium,
                    document,
                    entry["page"],
                    flagged["index"],
                    flagged["bounds"],
                    dpi,
                )
                target = list(page.get_objects(max_depth=0))[flagged["index"]]
                page.remove_obj(target)
                target.close()
                if image is None:
                    continue
                left, bottom, right, top = placed
                picture = pdfium.PdfImage.new(document)
                picture.set_bitmap(pdfium.PdfBitmap.from_pil(image))
                picture.set_matrix(
                    pdfium.PdfMatrix()
                    .scale(right - left, top - bottom)
                    .translate(left, bottom)
                )
                page.insert_obj(picture)
                flagged["rasterized"] = [round(v, 2) for v in placed]
            page.gen_content()
        output = Path(output_dir) / SANITIZED_DIR
        output.mkdir(parents=True, exist_ok=True)
        target = output / pdf.name
        with open(target, "wb") as handle:
            document.save(handle)
        return target, report
    finally:
        document.close()
