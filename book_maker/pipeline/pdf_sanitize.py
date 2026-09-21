"""Figures taken out of a PDF's text layer before the extractor sees it.

Measured on arXiv 2609.20519, page 1: 3,325 characters are visible and
85,525 are not. The teaser figure is a vector drawing whose whole content
is instantiated some 140 times, each copy under a small clip window that
shows one tile of it, and every extractor tried -- OpenDataLoader's Java
engine, its docling backend, poppler -- reads all 140 copies, because none
of them honours clipping. The reading edition then held 9,386 translation
units for a fifteen-page paper, most of them "(a) Previous methods". The
paper's other figures are plainer: a chart's axis labels came back as
paragraphs, its panel titles as headings, and its lines not at all, since
the extractor exports no vector drawing as a picture.

pdfium exposes the clips, so the text's visibility is computed here by
composing each object's own clip and its ancestors' matrices and clips
outward to page space. A page-level form that hides text under its clips,
or that draws and covers part of the page, is a figure, and a figure is
what the reader wants to see, not read: it is rendered on its own, removed
from the page and put back at the same place in the stacking order as that
picture, in a sanitized copy of the PDF that the extractor reads instead of
the original. A page-level text object hidden by its own clip is simply
taken out. Everything else on the page stays as it was, text included.
"""

import ctypes
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
# A page-level form that draws is a figure when it covers this much of the
# page or more -- below it, a bullet or a logo glyph -- and less than the
# whole-page share, above which it is a wrapper some producers put every
# page in (pdfpages, print-to-PDF), whose text must stay text. Neither
# reason rasterizes a wrapper: a page body and its illustration sharing
# one form keep their text, and the extractor's density warning is what
# says so.
FIGURE_MIN_AREA = 0.02
WHOLE_PAGE_AREA = 0.7
# How many paths or pictures a form must draw before it is a figure by
# drawing alone. A callout box is one rectangle around prose; the paper's
# plainest chart drew seventeen. A guard, not a measurement.
FIGURE_MIN_DRAWINGS = 8
# The most visible text a form may carry and still be a figure by drawing
# alone. A chart's labels, legend and axis titles run to a few hundred
# characters; a page body some producers wrap in a form with a few rules
# runs to a thousand and more, and it must stay text. Six hundred is a
# guess between the two, not a measurement; a chart with more labels than
# that keeps its labels as prose, which is a blemish, where a page body
# turned into a picture is a loss.
FIGURE_MAX_LABEL_CHARS = 600
# Resolution of the picture that replaces a figure. Body text in a figure
# is small; 200 DPI keeps it legible in a reader without the file bloating.
RASTER_DPI = 200

SANITIZED_DIR = "sanitized"

REASON_HIDDEN = "hidden-text"
REASON_FIGURE = "figure"
REASON_CLIPPED = "clipped-text"


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


def _text_length(raw, pageobj, textpage):
    count = raw.FPDFTextObj_GetText(pageobj, textpage, None, 0)
    if count <= 0:
        return 0
    buffer = (ctypes.c_ushort * count)()
    raw.FPDFTextObj_GetText(pageobj, textpage, buffer, count)
    return len(bytes(buffer).decode("utf-16-le", errors="replace").rstrip("\x00"))


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


def _visible_fraction(raw, obj, page_box):
    box, clip = _page_space(raw, obj)
    shown = _intersect(box, page_box)
    if clip is not None:
        shown = _intersect(shown, clip)
    return _area(shown) / _area(box) if _area(box) > 0 else 1.0


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


def figure_report(document, page_numbers):
    """Page-level objects to take out, per page, numbered from 1.

    `[{"page": n, "objects": [{"index": i, "reason": r, "hidden": chars,
    "visible": chars, "bounds": [l, b, r, t]}]}]`, listing only pages with
    at least one. Three reasons: `hidden-text`, a form hiding at least
    `HIDDEN_TEXT_THRESHOLD` characters under its clips; `figure`, a form
    that draws at least `FIGURE_MIN_DRAWINGS` paths or pictures; both only
    when the form covers between `FIGURE_MIN_AREA` and `WHOLE_PAGE_AREA` of
    the page, and both replaced by a picture. `clipped-text`, a page-level
    text object its own clip hides entirely, removed as it is; one that
    shows any part stays, its hidden part reaching the extraction rather
    than its shown part leaving the page.
    """
    pdfium, raw = _pdfium()
    report = []
    for number in page_numbers:
        try:
            page = document[number - 1]
            page_box = _page_box(page)
            textpage = page.get_textpage()
        except Exception as err:
            raise PipelineError(
                f"page {number} could not be read: {type(err).__name__}: {err}",
                stage=STAGE,
            )
        flagged = []
        try:
            for index, top in enumerate(page.get_objects(max_depth=0)):
                entry = _examine(raw, page, textpage, page_box, index, top)
                if entry is not None:
                    flagged.append(entry)
        except PipelineError:
            raise
        except Exception as err:
            raise PipelineError(
                f"page {number} could not be examined: {type(err).__name__}: {err}",
                stage=STAGE,
            )
        finally:
            textpage.close()
        if flagged:
            report.append({"page": number, "objects": flagged})
    return report


def _examine(raw, page, textpage, page_box, index, top):
    bounds = top.get_bounds()
    if top.type == raw.FPDF_PAGEOBJ_TEXT:
        length = _text_length(raw, top.raw, textpage.raw)
        if not length or _visible_fraction(raw, top, page_box) > 0:
            return None
        return _entry(index, REASON_CLIPPED, length, 0, bounds)
    if top.type != raw.FPDF_PAGEOBJ_FORM:
        return None
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
        if _visible_fraction(raw, obj, page_box) < VISIBLE_FRACTION:
            hidden += length
        else:
            visible += length
    area = _area(_intersect(bounds, page_box)) / _area(page_box)
    if not FIGURE_MIN_AREA <= area < WHOLE_PAGE_AREA:
        return None
    # A form that shows more text than it hides is a page body with an
    # overflow, not a figure, however much it draws; its hidden part is a
    # bounded leak where its shown part would be a loss.
    if hidden >= HIDDEN_TEXT_THRESHOLD and visible > hidden:
        return None
    if hidden >= HIDDEN_TEXT_THRESHOLD:
        return _entry(index, REASON_HIDDEN, hidden, visible, bounds)
    if drawings >= FIGURE_MIN_DRAWINGS and visible < FIGURE_MAX_LABEL_CHARS:
        return _entry(index, REASON_FIGURE, hidden, visible, bounds)
    return None


def _entry(index, reason, hidden, visible, bounds):
    return {
        "index": index,
        "reason": reason,
        "hidden": hidden,
        "visible": visible,
        "bounds": [round(v, 2) for v in bounds],
    }


def _render_alone(pdfium, document, page_number, index, bounds, dpi):
    """`(picture, box)`: the object at `index` on that page drawn on its own.

    Drawn over nothing, so the box is tightened to the pixels the object
    actually put down: a figure's bounds run to wherever its clipped-away
    content reached, which is not where the reader sees it. The scratch
    page is rendered unrotated, so the crop and the box share the page's
    own coordinates whatever `/Rotate` says; the picture put back rotates
    with the page as the drawing did. `(None, None)` when nothing was
    drawn; the caller then leaves the object alone.
    """
    scratch = pdfium.PdfDocument.new()
    try:
        scratch.import_pages(document, pages=[page_number - 1])
        page = scratch[0]
        page.set_rotation(0)
        for position, obj in reversed(list(enumerate(page.get_objects(max_depth=0)))):
            if position != index:
                page.remove_obj(obj)
                obj.close()
        page.gen_content()
        crop_left, crop_bottom, crop_right, crop_top = _page_box(page)
        left, bottom, right, top = _intersect(
            bounds, (crop_left, crop_bottom, crop_right, crop_top)
        )
        if _area((left, bottom, right, top)) <= 0:
            return None, None
        scale = dpi / 72.0
        bitmap = page.render(
            scale=scale,
            crop=(
                left - crop_left,
                bottom - crop_bottom,
                crop_right - right,
                crop_top - top,
            ),
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

        # Flattened on white on purpose: a transparent chart vanishes in a
        # reader's dark mode, and a figure sits in space of its own.
        picture = Image.new("RGB", drawn.size, (255, 255, 255))
        picture.paste(drawn, mask=drawn.getchannel("A"))
        return picture, placed
    finally:
        scratch.close()


def _insert_at(pdfium, raw, page, obj, index):
    """`page.insert_obj`, at the position the removed object held."""
    if hasattr(raw, "FPDFPage_InsertObjectAtIndex"):
        ok = raw.FPDFPage_InsertObjectAtIndex(page, obj, index)
        if not ok:
            raise pdfium.PdfiumError("Failed to insert object at index.")
        obj._detach_finalizer()
        obj.page = page
    else:  # pragma: no cover - older pdfium: appended, on top of the page
        page.insert_obj(obj)


def sanitize_pdf(pdf_path, output_dir, page_range=None, *, dpi=RASTER_DPI):
    """A copy of the PDF with its figures taken out of the text, or None.

    Returns `(path or None, report)`: the path of the sanitized copy under
    `output_dir` when any page changed, and the `figure_report` of what
    was found, each replaced object carrying `rasterized` (its picture's
    box) or `removed`, and an object whose render came back empty carrying
    `kept`. The original is never written to.
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
        changed = False
        for entry in report:
            try:
                changed |= _apply(pdfium, raw, document, entry, dpi)
            except PipelineError:
                raise
            except Exception as err:
                raise PipelineError(
                    f"page {entry['page']} could not be sanitized: "
                    f"{type(err).__name__}: {err}",
                    stage=STAGE,
                )
        if not changed:
            return None, report
        output = Path(output_dir) / SANITIZED_DIR
        output.mkdir(parents=True, exist_ok=True)
        target = output / pdf.name
        try:
            with open(target, "wb") as handle:
                document.save(handle)
        except Exception as err:
            raise PipelineError(
                f"the sanitized copy could not be written: {type(err).__name__}: {err}",
                stage=STAGE,
            )
        return target, report
    finally:
        document.close()


def _apply(pdfium, raw, document, entry, dpi):
    page = document[entry["page"] - 1]
    changed = False
    # Highest index first, so replacing one object does not shift the
    # index of the next; each picture goes back where its object was.
    for flagged in sorted(entry["objects"], key=lambda f: -f["index"]):
        index = flagged["index"]
        if flagged["reason"] == REASON_CLIPPED:
            target = list(page.get_objects(max_depth=0))[index]
            page.remove_obj(target)
            target.close()
            flagged["removed"] = True
            changed = True
            continue
        image, placed = _render_alone(
            pdfium, document, entry["page"], index, flagged["bounds"], dpi
        )
        if image is None:
            flagged["kept"] = True
            continue
        target = list(page.get_objects(max_depth=0))[index]
        page.remove_obj(target)
        target.close()
        left, bottom, right, top = placed
        picture = pdfium.PdfImage.new(document)
        picture.set_bitmap(pdfium.PdfBitmap.from_pil(image))
        picture.set_matrix(
            pdfium.PdfMatrix().scale(right - left, top - bottom).translate(left, bottom)
        )
        _insert_at(pdfium, raw, page, picture, index)
        flagged["rasterized"] = [round(v, 2) for v in placed]
        changed = True
    if changed:
        page.gen_content()
    return changed
