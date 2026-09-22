"""Display formulas, kept as pictures.

docling's layout model finds a display equation but does not read it: the
Markdown export writes `<!-- formula-not-decoded -->` where the equation
was, and the mathematics is simply gone from the book. Its own formula
model (`do_formula_enrichment`) was measured and rejected -- it
hallucinated on hard equations, swallowed body prose into `$$` blocks and
cost 117 s a page, 29x the default (docs/260921-eval-DOCLING_VS_OPENDATALOADER.md).

So the equation is preserved the way a reader actually wants it: the
region is cropped out of the PDF and written beside the text as an image.
It is pixel-exact because it *is* the page, it costs no model and no
network, and the translator never sees it -- an image is not prose, so
nothing can be mistranslated into a wrong formula.

What it does not do: inline mathematics inside a paragraph is not a
formula region at all, and on a scan it arrives as whatever OCR made of
it. That text is untouched here.
"""

import re

from .messages import (
    FORMULA_COUNT_MISMATCH,
    FORMULA_REGION_OVERSIZE,
    FORMULA_UNPLACEABLE,
)

# The serializer's placeholder, verbatim. PIN: docling-core's Markdown
# serializer writes `$$text$$` when a FormulaItem has text, this comment
# when it has none but has `orig`, and NOTHING AT ALL when it has
# neither -- which is why `mark` fills `orig` in before the export.
PLACEHOLDER = "<!-- formula-not-decoded -->"
_PLACEHOLDER_RE = re.compile(re.escape(PLACEHOLDER))

IMAGE_DIR = "images"
# 3x the PDF's own 72 dpi. Enough that a subscript stays legible on a
# high-density screen without making the bundle heavy.
SCALE = 3.0
# Padding is deliberately asymmetric, measured on the fixture. The layout
# box sits tight against the glyphs and clips a tall bracket or an
# integral sign at the sides, so the horizontal pad is generous; but a
# display equation is separated from the prose above and below it by
# ordinary line leading, so the same generosity vertically drags the
# neighbouring sentence into the picture (seen at 12pt: "Thus (Equation
# 2.36)" appeared under a derivation).
PAD_X = 12.0
PAD_Y = 2.0
# A "formula" covering most of the page is a layout mistake, not an
# equation; cropping it would silently replace the page's prose with a
# picture of itself.
MAX_PAGE_SHARE = 0.8


class Region:
    """One formula item: where it is, and whether it can be cropped."""

    def __init__(self, page, box):
        self.page = page
        self.box = box  # (left, bottom, right, top), PDF points, bottom-left

    def __repr__(self):  # pragma: no cover - debugging only
        return f"Region(page={self.page}, box={self.box})"


def mark(document):
    """Formula regions in reading order, one per undecoded formula.

    Also fills in `orig` on any formula that has neither text nor `orig`,
    because such an item exports to an empty string: the equation would
    vanish without even a placeholder to replace. After this every
    undecoded formula is guaranteed to leave exactly one placeholder, so
    the nth placeholder is the nth region.
    """
    regions = []
    for item, _level in document.iterate_items():
        if "formula" not in str(getattr(item, "label", "")).lower():
            continue
        if getattr(item, "text", ""):
            # docling read this one; leave its $$...$$ alone. Truthiness,
            # not content: the serializer tests `if text:` and would write
            # `$$ $$` for a blank, and counting that as a region here would
            # trip the count guard and drop every image in the document.
            continue
        if not getattr(item, "orig", None):
            item.orig = " "
        prov = list(getattr(item, "prov", None) or [])
        if not prov:
            regions.append(Region(None, None))
            continue
        first = prov[0]
        box = first.bbox
        regions.append(
            Region(
                first.page_no, (float(box.l), float(box.b), float(box.r), float(box.t))
            )
        )
    return regions


def _overlap(first, second):
    l1, b1, r1, t1 = first
    l2, b2, r2, t2 = second
    return l1 < r2 and l2 < r1 and b1 < t2 and b2 < t1


def _union(first, second):
    return (
        min(first[0], second[0]),
        min(first[1], second[1]),
        max(first[2], second[2]),
        max(first[3], second[3]),
    )


def merge(regions):
    """Overlapping regions on one page become a single crop.

    A scan makes the layout model draw two boxes over one equation --
    seen on the fixture, where neither box contains the other. Two crops
    would show the reader the same equation twice, so they are unioned
    and the extra placeholders are dropped rather than filled.
    """
    groups = []  # each: [page, box, [region index, ...]]
    for index, region in enumerate(regions):
        if region.page is None:
            continue
        for group in groups:
            if group[0] == region.page and _overlap(group[1], region.box):
                group[1] = _union(group[1], region.box)
                group[2].append(index)
                break
        else:
            groups.append([region.page, region.box, [index]])
    # A union can bring two previously separate groups into contact.
    changed = True
    while changed:
        changed = False
        for i in range(len(groups)):
            for j in range(len(groups) - 1, i, -1):
                if groups[i][0] == groups[j][0] and _overlap(
                    groups[i][1], groups[j][1]
                ):
                    groups[i][1] = _union(groups[i][1], groups[j][1])
                    groups[i][2].extend(groups[j][2])
                    del groups[j]
                    changed = True
    for group in groups:
        group[2].sort()
    groups.sort(key=lambda group: group[2][0])
    return groups


def _crop(page, box, pad_x, pad_y):
    """The render `crop` margins for `box`, clamped to the page."""
    from .pdf_common import _page_box

    left, bottom, right, top = _page_box(page)
    l = max(left, box[0] - pad_x)
    b = max(bottom, box[1] - pad_y)
    r = min(right, box[2] + pad_x)
    t = min(top, box[3] + pad_y)
    if r <= l or t <= b:
        return None, 0.0
    share = ((r - l) * (t - b)) / max((right - left) * (top - bottom), 1e-9)
    return (l - left, b - bottom, right - r, top - t), share


def rasterize(pdf_path, groups, out_dir, *, scale=SCALE, pad_x=PAD_X, pad_y=PAD_Y):
    """Write one PNG per group; return {group index: file name} and warnings.

    The page is rendered unrotated so the crop and the box share one
    coordinate space -- docling reports the same page box pdfium does,
    checked on the fixture.
    """
    from pathlib import Path

    from .pdf_common import _pdfium

    pdfium, _raw = _pdfium()
    images, warnings = {}, []
    directory = Path(out_dir) / IMAGE_DIR
    document = pdfium.PdfDocument(str(pdf_path))
    try:
        for index, (page_number, box, _members) in enumerate(groups):
            page = document[page_number - 1]
            margins, share = _crop(page, box, pad_x, pad_y)
            if margins is None:
                warnings.append(FORMULA_UNPLACEABLE.format(page=page_number))
                continue
            if share > MAX_PAGE_SHARE:
                warnings.append(
                    FORMULA_REGION_OVERSIZE.format(
                        page=page_number, share=round(share * 100)
                    )
                )
                continue
            bitmap = page.render(scale=scale, crop=margins, rotation=0)
            directory.mkdir(parents=True, exist_ok=True)
            name = f"formula_p{page_number:04d}_{index:03d}.png"
            bitmap.to_pil().convert("RGB").save(directory / name)
            images[index] = name
    finally:
        document.close()
    return images, warnings


def replace(markdown, regions, groups, images):
    """Each placeholder becomes its group's image, or is left alone.

    A placeholder whose group produced no image keeps the comment, so a
    region that could not be cropped still tells the reader that an
    equation was there.
    """
    target = {}
    for index, (_page, _box, members) in enumerate(groups):
        name = images.get(index)
        if name is None:
            continue
        target[members[0]] = f"![]({IMAGE_DIR}/{name})"
        for extra in members[1:]:
            target[extra] = ""

    counter = {"n": 0}

    def swap(_match):
        position = counter["n"]
        counter["n"] += 1
        return target.get(position, PLACEHOLDER)

    return _PLACEHOLDER_RE.sub(swap, markdown), counter["n"]


def apply(
    markdown, regions, pdf_path, out_dir, *, scale=SCALE, pad_x=PAD_X, pad_y=PAD_Y
):
    """Rasterize every marked formula. Returns (markdown, count, warnings).

    The placeholder count is checked against the regions `mark` found. A
    mismatch means the export did not line up with the document -- a
    docling change, most likely -- and the Markdown is returned untouched
    rather than having equations placed where they do not belong.
    """
    found = len(_PLACEHOLDER_RE.findall(markdown))
    if not regions and not found:
        return markdown, 0, []
    if found != len(regions):
        mismatch = FORMULA_COUNT_MISMATCH.format(regions=len(regions), found=found)
        return markdown, 0, [mismatch]
    groups = merge(regions)
    images, warnings = rasterize(
        pdf_path, groups, out_dir, scale=scale, pad_x=pad_x, pad_y=pad_y
    )
    markdown, _seen = replace(markdown, regions, groups, images)
    return markdown, len(images), warnings
