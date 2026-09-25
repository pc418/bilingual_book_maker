"""Figures, drawn from the PDF by our own renderer at a chosen resolution.

docling finds the figures and says where they stand; its own pictures of
them are 1 px per PDF point (72 DPI, `images_scale` 1.0), which is what a
reader zooming into a plot sees blur. So docling keeps the layout, the
text and the placement, and the pixels are drawn here with pypdfium2, one
page at a time, at a resolution a `FigurePolicy` chooses (owner 260925:
the policy is general -- "A4 was an example, not that every page is
treated as A4").

The seam is a stable name per picture. At extraction every docling
PictureItem that reaches the Markdown is given `figures/p{page}-{n}.png`
as its reference, by identity (the item's own `image.uri`, set before the
serializer runs -- the way `pdf_formula.mark` gives a formula its marker),
so `source.md` names a figure and never a resolution: it is byte-identical
whatever the policy, and so is the translation built from it. The record
of where each figure stands is `.work/extraction/figures.json`; the render
step reads it and writes the PNGs under `assets/figures/`. Changing the
policy redraws those files and nothing else -- no extraction, no
translation -- and the EPUB, which is always rebuilt, picks them up.

docling's own pictures stay under `.work/extraction/images/` as the
fallback for a figure that cannot be drawn.

The policy is not an extraction setting (`ExtractionSettings`): it does
not change a word of the text, and a change must never re-extract.
"""

import argparse
import json
import math
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from .bundle import ASSETS_DIR
from .messages import (
    FIGURE_RENDER_FAILED,
    FIGURES_DRAWN,
    FIGURES_LEGACY,
    FIGURES_REDRAWN,
)

# Beside `source.md` in the extraction's staging directory, and under
# `assets/` once imported.
FIGURE_DIR = "figures"
# Inside the extraction's staging directory, `.work/extraction/`.
FIGURES_FILE = "figures.json"
# docling's own pictures, in the same staging directory (the adapter's
# `IMAGE_DIR`, spelled here so this module never imports the adapter).
DOCLING_IMAGE_DIR = "images"
# Bumped when the drawing itself changes (crop, encoding), so every bundle
# drawn by the older code is drawn again on its next run.
FIGURE_REVISION = 1

POLICY_KINDS = ("dpi", "page-width", "figure-px")
# `figure-px`: the height is held to this many times the asked width, so a
# tall narrow figure does not become a column of pixels, and the scale is
# never above what `dpi` 300 would draw -- a thumbnail is not blown up to
# thousands of pixels from a few points of vector art.
FIGURE_PX_HEIGHT_CAP = 1.5
FIGURE_PX_MAX_DPI = 300

# A name `name_pictures` gives: `p0003-01.png`. Wider numbers are allowed,
# a page past 9999 or a hundredth picture on one page is still ours.
STABLE_NAME = re.compile(r"^p\d{4,}-\d{2,}\.png$")


@dataclass(frozen=True)
class FigurePolicy:
    """How many pixels a figure is drawn with.

    - `dpi`: the PDF's own physical resolution; scale = value / 72.
    - `page-width`: a pixel budget for the page's displayed width; scale =
      value / page width in points (1654 is "200 DPI of an A4-wide page",
      and A4 is only one way to pick the number).
    - `figure-px`: the figure's own width in pixels; scale = value / box
      width, the height held to 1.5 x value, never above `dpi` 300.
    """

    kind: str
    value: float

    def __post_init__(self):
        if self.kind not in POLICY_KINDS:
            raise ValueError(
                f"figure policy kind {self.kind!r} is not one of "
                f"{', '.join(POLICY_KINDS)}"
            )
        try:
            value = float(self.value)
        except (TypeError, ValueError):
            raise ValueError(f"figure policy value {self.value!r} is not a number")
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"figure policy value {self.value!r} is not positive")
        # 200 and 200.0 are one policy, and the manifest says 200.
        object.__setattr__(self, "value", int(value) if value.is_integer() else value)

    def describe(self):
        """`200 DPI`, `1654 px per page width`, `1600 px per figure`."""
        value = f"{self.value:g}" if isinstance(self.value, float) else self.value
        return {
            "dpi": f"{value} DPI",
            "page-width": f"{value} px per page width",
            "figure-px": f"{value} px per figure",
        }[self.kind]

    def to_manifest(self):
        return {"kind": self.kind, "value": self.value}

    @classmethod
    def from_manifest(cls, data):
        return cls(data["kind"], data["value"])


# Provisional (packet R measures the candidates and the lead sets this).
FIGURE_POLICY_DEFAULT = FigurePolicy("dpi", 200)


def parse_figure_policy(text):
    """`KIND:VALUE` as a `FigurePolicy`; an argparse `type=`.

    Refused at parse time (argparse exits 2): an unknown kind, a value
    that is not a positive number, a missing colon.
    """
    kind, sep, value = str(text).partition(":")
    try:
        if not sep:
            raise ValueError("no colon")
        return FigurePolicy(kind.strip(), float(value))
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not KIND:VALUE with KIND one of "
            f"{', '.join(POLICY_KINDS)} and VALUE a positive number "
            f"(e.g. dpi:200, page-width:1654, figure-px:1600)"
        )


def figure_scale(box, page_size, policy):
    """Pixels per PDF point for `box` on a page of `page_size`, by `policy`.

    `box` is `(left, top, right, bottom)` and `page_size` `(width,
    height)`, both in points in the page's displayed frame (after
    /Rotate), which is the frame docling reports and pypdfium2 renders.
    A box with no area cannot be drawn and is refused.
    """
    left, top, right, bottom = (float(v) for v in box)
    width, height = right - left, bottom - top
    if not (width > 0 and height > 0) or not all(
        math.isfinite(v) for v in (width, height)
    ):
        raise ValueError(f"the figure box {list(box)} has no area")
    if policy.kind == "dpi":
        return policy.value / 72.0
    if policy.kind == "page-width":
        page_width = float(page_size[0])
        if not page_width > 0:
            raise ValueError(f"the page width {page_width} is not positive")
        return policy.value / page_width
    scale = policy.value / width
    scale = min(scale, FIGURE_PX_HEIGHT_CAP * policy.value / height)
    return min(scale, FIGURE_PX_MAX_DPI / 72.0)


def figure_id(page, number):
    return f"p{page:04d}-{number:02d}"


def is_drawn_figure(path):
    """Whether a bundle-relative asset path is one of the drawn figures."""
    parts = Path(path).parts
    return (
        len(parts) == 3
        and parts[0] == ASSETS_DIR
        and parts[1] == FIGURE_DIR
        and bool(STABLE_NAME.match(parts[2]))
    )


def display_width(box, page_width):
    """The figure's share of the page's width, in whole percent, 1..100."""
    share = 100.0 * (float(box[2]) - float(box[0])) / float(page_width)
    return max(1, min(100, int(round(share))))


# --------------------------------------------------------------------------
# Extraction: names, records, widths
# --------------------------------------------------------------------------
def name_pictures(document, out_dir):
    """Give every written picture of `document` its stable name; return records.

    `document` is the copy docling-core's `_with_pictures_refs` returned:
    each picture it wrote carries its file's path as `image.uri`. That
    file is copied to `out_dir/figures/<name>` (a stand-in until the render
    step draws it, and the same pixels if the drawing fails) and the item's
    `uri` becomes `figures/<name>`, so the serializer writes the name where
    the item stands, whatever order it walks the document in.

    The pictures are numbered per page in the order docling-core writes
    them (`iterate_items`, reading order). A picture without a page or a
    written file keeps what docling gave it; there is nothing to draw.
    Each record: `id`, `page`, `bbox` (`[left, top, right, bottom]`, points,
    displayed frame, top-left origin), `file` (bundle-relative), `width`
    (percent of the page width), `fallback` (docling's file, relative to
    `out_dir`; the extraction writes it bundle-relative into figures.json).
    """
    from docling_core.types.doc import PictureItem
    from pydantic import AnyUrl

    target = Path(out_dir) / FIGURE_DIR
    counts, records = {}, []
    for item, _level in document.iterate_items(with_groups=False):
        if not isinstance(item, PictureItem) or not item.prov:
            continue
        image = item.image
        if image is None or image.uri is None or isinstance(image.uri, AnyUrl):
            continue
        source = Path(str(image.uri))
        if not source.is_file():
            continue
        prov = item.prov[0]
        page = document.pages.get(prov.page_no)
        if page is None:
            continue
        size = page.size
        box = prov.bbox.to_top_left_origin(page_height=size.height)
        counts[prov.page_no] = counts.get(prov.page_no, 0) + 1
        ident = figure_id(prov.page_no, counts[prov.page_no])
        name = f"{ident}.png"
        target.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target / name)
        item.image.uri = Path(FIGURE_DIR) / name
        bbox = [round(float(v), 3) for v in (box.l, box.t, box.r, box.b)]
        records.append(
            {
                "id": ident,
                "page": prov.page_no,
                "bbox": bbox,
                "file": f"{ASSETS_DIR}/{FIGURE_DIR}/{name}",
                "width": display_width(bbox, size.width),
                "fallback": f"{DOCLING_IMAGE_DIR}/{source.name}",
            }
        )
    return records


def _reference(record):
    """The Markdown target a record's picture is written with, in staging."""
    return f"{FIGURE_DIR}/{Path(record['file']).name}"


def add_widths(markdown, records):
    """Each named figure's reference gets its Pandoc width attribute.

    By name, which is the item's identity: `![Image](figures/p0003-01.png)`
    becomes `...{width=76%}`. So more pixels never change how large the
    figure is shown, only how sharp.
    """
    for record in records:
        pattern = re.compile(
            r"(!\[[^\]\n]*\]\(" + re.escape(_reference(record)) + r"\))(?!\{)"
        )
        markdown = pattern.sub(
            lambda match: f"{match.group(1)}{{width={record['width']}%}}", markdown
        )
    return markdown


def referenced(records, markdown):
    """The records whose picture the final Markdown still names."""
    return [r for r in records if f"]({_reference(r)})" in markdown]


def write_records(path, records):
    Path(path).write_text(
        json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def records_path(bundle):
    return bundle.work_file("extraction") / FIGURES_FILE


def forget(bundle):
    """A new extraction: the drawn figures' record and its limitations go.

    The files under `assets/figures/` are replaced by the extraction's own
    stand-ins, so a `figures` block left behind would claim a drawing that
    is no longer there, and the next run would draw nothing.
    """
    data = bundle.read_manifest()
    old = data.pop("figures", None)
    if old is None:
        return
    bundle.write_manifest(data)
    bundle.drop_limitations(old.get("limitations") or [])


def clear_assets(bundle):
    """Remove the previous extraction's figure files before the import.

    The importer gives a file whose name is taken by other bytes a
    `-2` suffix; the stable names must land on their own names.
    """
    shutil.rmtree(Path(bundle.assets) / FIGURE_DIR, ignore_errors=True)


# --------------------------------------------------------------------------
# The render step
# --------------------------------------------------------------------------
LEGACY_PICTURE = re.compile(
    r"!\[[^\]\n]*\]\(<?" + ASSETS_DIR + r"/images/(?!formula_)[^)\n]+\)"
)


def render_figures(bundle, pdf_path, policy=FIGURE_POLICY_DEFAULT):
    """Draw the bundle's figures at `policy`, unless they already are.

    Called after the extraction (whether it ran or was reused) and before
    the EPUB is built. Returns the manifest's `figures` block, or None when
    the bundle has no extraction to draw from.

    - No completed PDF extraction (a Markdown import, a stub stage): nothing.
    - No `figures.json`: a bundle made before this step. It is left exactly
      as it is, and `FIGURES_LEGACY` is said once if it has pictures.
    - The manifest's block has this policy and revision: nothing is drawn.
    - Otherwise every figure is drawn again. One that cannot be drawn gets
      docling's picture under its name, a line and a limitation.
    """
    manifest = bundle.read_manifest()
    extract = ((manifest.get("stages") or {}).get("extract") or {}).get("status")
    if extract != "completed":
        return None
    path = records_path(bundle)
    if not path.is_file():
        if bundle.source.is_file() and LEGACY_PICTURE.search(
            bundle.source.read_text(encoding="utf-8")
        ):
            print(FIGURES_LEGACY)
        return None
    records = json.loads(path.read_text(encoding="utf-8"))
    old = manifest.get("figures")
    wanted = policy.to_manifest()
    if old and old.get("policy") == wanted and old.get("revision") == FIGURE_REVISION:
        return old

    # Forgotten before the first file is touched: a drawing interrupted
    # half-way is then drawn again on the next run, whatever it asks for.
    forget(bundle)
    failed, lines, total = [], [], 0
    for record, error in _draw_all(pdf_path, records, bundle.root, policy):
        destination = bundle.root / record["file"]
        if error is not None:
            _fallback(bundle.root / record["fallback"], destination)
            line = FIGURE_RENDER_FAILED.format(
                id=record["id"],
                page=record["page"],
                policy=policy.describe(),
                err=f"{type(error).__name__}: {error}",
            )
            print(line)
            failed.append(record["id"])
            lines.append(line)
        if destination.is_file():
            total += destination.stat().st_size
    block = {
        "policy": wanted,
        "revision": FIGURE_REVISION,
        "count": len(records),
        "bytes": total,
        "failed": failed,
        # This drawing's own lines, so the next drawing takes them back.
        "limitations": lines,
    }
    bundle.update_manifest(figures=block)
    bundle.add_limitations(lines)
    if records:
        if old and old.get("policy"):
            print(
                FIGURES_REDRAWN.format(
                    policy=policy.describe(),
                    old=_describe_recorded(old),
                )
            )
        print(
            FIGURES_DRAWN.format(
                count=len(records) - len(failed),
                policy=policy.describe(),
                size=human_size(total),
            )
        )
    return block


def _describe_recorded(block):
    try:
        text = FigurePolicy.from_manifest(block["policy"]).describe()
    except (KeyError, TypeError, ValueError):
        return "an unrecorded policy"
    if block.get("revision") != FIGURE_REVISION:
        return f"{text}, drawing revision {block.get('revision')}"
    return text


def _fallback(source, destination):
    """docling's 72 DPI picture under the figure's own name."""
    if Path(source).is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


def _draw_all(pdf_path, records, root, policy):
    """`(record, error or None)` for each record, drawn page by page.

    One page is open at a time, and each bitmap is released before the
    next is drawn: a long illustrated book must not hold its pages.
    """
    from .pdf_common import _pdfium

    try:
        pdfium, _raw = _pdfium()
        document = pdfium.PdfDocument(str(pdf_path))
    except Exception as err:
        for record in records:
            yield record, err
        return
    try:
        pages = {}
        for record in records:
            pages.setdefault(record["page"], []).append(record)
        for number in sorted(pages):
            try:
                page = document[number - 1]
            except Exception as err:
                for record in pages[number]:
                    yield record, err
                continue
            try:
                for record in pages[number]:
                    try:
                        _draw(page, record, policy, Path(root) / record["file"])
                    except Exception as err:
                        yield record, err
                    else:
                        yield record, None
            finally:
                page.close()
    finally:
        document.close()


def _draw(page, record, policy, destination):
    """Render one record's box from `page` at `policy` into `destination`.

    The crop is the formula crop's own (`pdf_formula._crop`: the page as
    rendered, CropBox after /Rotate, margins clamped to it), with no
    padding: a figure box is drawn as docling found it.
    """
    from .pdf_formula import _crop

    width, height = (float(v) for v in page.get_size())
    left, top, right, bottom = (float(v) for v in record["bbox"])
    scale = figure_scale(record["bbox"], (width, height), policy)
    margins, _share = _crop(
        page, (left, height - bottom, right, height - top), 0.0, (0.0, 0.0)
    )
    if margins is None:
        raise ValueError(f"the figure box {record['bbox']} is outside the page")
    bitmap = page.render(
        scale=scale, crop=_snapped(margins, (width, height), scale), rotation=0
    )
    try:
        image = bitmap.to_pil()
        try:
            if image.mode != "RGB":
                converted = image.convert("RGB")
                image.close()
                image = converted
            destination.parent.mkdir(parents=True, exist_ok=True)
            partial = destination.with_name(destination.name + ".part")
            try:
                image.save(partial, format="PNG", optimize=True)
                os.replace(partial, destination)
            except BaseException:
                # Never left under assets/, where every file is hashed.
                partial.unlink(missing_ok=True)
                raise
        finally:
            image.close()
    finally:
        bitmap.close()


def _snapped(margins, page_size, scale):
    """`margins` (points) that pypdfium2 turns into whole pixels exactly.

    pypdfium2 rounds the page and each margin up separately
    (`ceil(c * scale)`), so a box could lose up to two pixels of the width
    the policy asked for. Here the box's pixel width is decided once --
    `round(box width x scale)` -- and the margins are given as the whole
    pixel counts that leave exactly that, a hair under each so the
    library's ceiling lands on the count and not one past it.
    """
    left, bottom, right, top = margins
    width, height = page_size
    page_w = math.ceil(width * scale)
    page_h = math.ceil(height * scale)
    box_w = round((width - left - right) * scale)
    box_h = round((height - bottom - top) * scale)
    left_px = min(round(left * scale), page_w - 1)
    top_px = min(round(top * scale), page_h - 1)
    box_w = max(1, min(box_w, page_w - left_px))
    box_h = max(1, min(box_h, page_h - top_px))
    pixels = (left_px, page_h - top_px - box_h, page_w - left_px - box_w, top_px)
    return tuple(max(0.0, (count - 1e-6) / scale) if count else 0.0 for count in pixels)


def human_size(count):
    """`812 B`, `640 KB`, `1.2 MB` (decimal units)."""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f} MB"
    if count >= 1_000:
        return f"{count / 1_000:.0f} KB"
    return f"{count} B"
