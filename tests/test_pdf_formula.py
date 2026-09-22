"""Display formulas kept as pictures.

PIN (owner 260922, docs/260922-feat-PDF_FORMULA_IMAGES.md): docling finds a
display equation and does not read it, so the Markdown export carries
`<!-- formula-not-decoded -->` and the mathematics is gone from the book.
The equation is preserved by cropping its region out of the PDF, not by
docling's own formula model: `do_formula_enrichment` was measured in
docs/260921-eval-DOCLING_VS_OPENDATALOADER.md and hallucinated on hard
equations, swallowed prose into `$$` blocks and cost 29x the default. Do
not "improve" this into a LaTeX decoder without re-running that eval.
"""

from pathlib import Path

import pytest

from book_maker.pipeline import pdf_formula
from book_maker.pipeline.messages import (
    FORMULA_COUNT_MISMATCH,
    FORMULA_REGION_OVERSIZE,
)
from pipeline_helpers import write_pdf

PLACEHOLDER = pdf_formula.PLACEHOLDER


class FakeBox:
    def __init__(self, l, b, r, t):
        self.l, self.b, self.r, self.t = l, b, r, t


class FakeProv:
    def __init__(self, page_no, box):
        self.page_no = page_no
        self.bbox = FakeBox(*box)


class FakeItem:
    def __init__(
        self, label="formula", text="", orig=None, page=1, box=(10, 10, 90, 30)
    ):
        self.label = label
        self.text = text
        self.orig = orig
        self.prov = [FakeProv(page, box)] if box is not None else []


class FakeDoc:
    def __init__(self, *items):
        self.items = items

    def iterate_items(self):
        return ((item, 0) for item in self.items)


# --------------------------------------------------------------------------
# mark
# --------------------------------------------------------------------------
def test_every_undecoded_formula_is_marked_in_reading_order():
    doc = FakeDoc(
        FakeItem(page=1, box=(0, 0, 10, 10)),
        FakeItem(label="text", box=(0, 0, 10, 10)),
        FakeItem(page=2, box=(5, 5, 20, 20)),
    )
    regions = pdf_formula.mark(doc)
    assert [r.page for r in regions] == [1, 2]
    assert regions[1].box == (5.0, 5.0, 20.0, 20.0)


def test_a_formula_without_orig_is_given_one_so_it_leaves_a_placeholder():
    """PIN: docling-core exports a FormulaItem with neither text nor `orig`
    as the empty string -- the equation disappears with no placeholder at
    all, and the nth placeholder would then be the wrong region. Checked
    against the real serializer in the last test of this module."""
    empty = FakeItem(orig=None)
    kept = FakeItem(orig="E=mc^2")
    pdf_formula.mark(FakeDoc(empty, kept))
    assert empty.orig, "an undecoded formula must be forced to a placeholder"
    assert kept.orig == "E=mc^2", "an existing orig is left alone"


def test_a_formula_docling_did_read_is_not_touched():
    decoded = FakeItem(text="E = mc^2")
    assert pdf_formula.mark(FakeDoc(decoded)) == []


def test_a_formula_with_no_position_is_marked_but_has_no_box():
    regions = pdf_formula.mark(FakeDoc(FakeItem(box=None)))
    assert len(regions) == 1 and regions[0].page is None


# --------------------------------------------------------------------------
# merge
# --------------------------------------------------------------------------
def test_overlapping_regions_on_one_page_become_a_single_crop():
    """Seen on the fixture: a scan makes the layout model draw two boxes
    over one equation, neither containing the other. Two crops would show
    the reader the same equation twice."""
    regions = pdf_formula.mark(
        FakeDoc(
            FakeItem(page=51, box=(121.3, 149.1, 374.0, 171.7)),
            FakeItem(page=51, box=(174.3, 143.4, 298.7, 176.1)),
        )
    )
    groups = pdf_formula.merge(regions)
    assert len(groups) == 1
    assert groups[0][1] == (121.3, 143.4, 374.0, 176.1)
    assert groups[0][2] == [0, 1]


def test_regions_on_different_pages_never_merge():
    regions = pdf_formula.mark(
        FakeDoc(
            FakeItem(page=1, box=(10, 10, 90, 30)),
            FakeItem(page=2, box=(10, 10, 90, 30)),
        )
    )
    assert len(pdf_formula.merge(regions)) == 2


def test_two_groups_brought_into_contact_by_a_union_are_merged():
    regions = pdf_formula.mark(
        FakeDoc(
            FakeItem(page=1, box=(0, 0, 10, 10)),
            FakeItem(page=1, box=(30, 0, 40, 10)),
            FakeItem(page=1, box=(5, 0, 35, 10)),
        )
    )
    groups = pdf_formula.merge(regions)
    assert len(groups) == 1 and groups[0][2] == [0, 1, 2]


def test_groups_keep_the_reading_order_of_their_first_member():
    regions = pdf_formula.mark(
        FakeDoc(
            FakeItem(page=1, box=(200, 200, 260, 220)),
            FakeItem(page=1, box=(0, 0, 60, 20)),
        )
    )
    assert [g[2][0] for g in pdf_formula.merge(regions)] == [0, 1]


# --------------------------------------------------------------------------
# replace
# --------------------------------------------------------------------------
def test_each_placeholder_becomes_its_group_s_image():
    regions = pdf_formula.mark(
        FakeDoc(
            FakeItem(page=1, box=(0, 0, 10, 10)), FakeItem(page=2, box=(0, 0, 10, 10))
        )
    )
    groups = pdf_formula.merge(regions)
    markdown = f"a\n\n{PLACEHOLDER}\n\nb\n\n{PLACEHOLDER}\n"
    out, seen = pdf_formula.replace(
        markdown, regions, groups, {0: "one.png", 1: "two.png"}
    )
    assert seen == 2
    assert "![](images/one.png)" in out and "![](images/two.png)" in out
    assert PLACEHOLDER not in out


def test_a_merged_group_fills_the_first_placeholder_and_drops_the_rest():
    regions = pdf_formula.mark(
        FakeDoc(
            FakeItem(page=1, box=(0, 0, 20, 10)),
            FakeItem(page=1, box=(10, 0, 30, 10)),
        )
    )
    groups = pdf_formula.merge(regions)
    out, _ = pdf_formula.replace(
        f"{PLACEHOLDER}\n\n{PLACEHOLDER}\n", regions, groups, {0: "one.png"}
    )
    assert out.count("![](images/one.png)") == 1
    assert PLACEHOLDER not in out


def test_a_group_with_no_image_keeps_its_placeholder():
    """A region that could not be cropped still tells the reader that an
    equation was there; it is never silently deleted."""
    regions = pdf_formula.mark(FakeDoc(FakeItem(page=1, box=(0, 0, 10, 10))))
    groups = pdf_formula.merge(regions)
    out, _ = pdf_formula.replace(f"{PLACEHOLDER}\n", regions, groups, {})
    assert out.strip() == PLACEHOLDER


# --------------------------------------------------------------------------
# apply: the guards
# --------------------------------------------------------------------------
def test_a_count_mismatch_leaves_every_equation_alone(tmp_path):
    """Belt and braces for a docling change: if the export stops lining up
    with the document, placing images by position would put an equation in
    the wrong paragraph. Doing nothing is the safe failure."""
    regions = pdf_formula.mark(
        FakeDoc(
            FakeItem(page=1, box=(0, 0, 10, 10)), FakeItem(page=1, box=(50, 50, 60, 60))
        )
    )
    markdown = f"only one\n\n{PLACEHOLDER}\n"
    out, count, warnings = pdf_formula.apply(
        markdown, regions, tmp_path / "x.pdf", tmp_path
    )
    assert out == markdown and count == 0
    assert warnings == [FORMULA_COUNT_MISMATCH.format(regions=2, found=1)]


def test_a_document_with_no_formulas_does_nothing(tmp_path):
    out, count, warnings = pdf_formula.apply(
        "plain\n", [], tmp_path / "x.pdf", tmp_path
    )
    assert (out, count, warnings) == ("plain\n", 0, [])


# --------------------------------------------------------------------------
# The real thing: pdfium, a real PDF, real pixels
# --------------------------------------------------------------------------
def pdfium_or_skip():
    try:
        import pypdfium2
    except ImportError:
        pytest.skip("pypdfium2 is not installed")
    if not hasattr(pypdfium2, "PdfDocument"):
        pytest.skip("pypdfium2 is not installed")


def test_a_region_is_cropped_out_of_a_real_pdf(tmp_path):
    pdfium_or_skip()
    pytest.importorskip("PIL")
    from PIL import Image

    pdf = write_pdf(tmp_path / "book.pdf", ["A typed line of prose on the page."])
    regions = pdf_formula.mark(FakeDoc(FakeItem(page=1, box=(50, 700, 300, 730))))
    groups = pdf_formula.merge(regions)
    images, warnings = pdf_formula.rasterize(pdf, groups, tmp_path)
    assert warnings == []
    written = tmp_path / pdf_formula.IMAGE_DIR / images[0]
    assert written.is_file()
    with Image.open(written) as picture:
        # (250 + 2*PAD_X) x (30 + 2*PAD_Y) points at SCALE, within rounding.
        assert (
            abs(picture.width - (250 + 2 * pdf_formula.PAD_X) * pdf_formula.SCALE) <= 2
        )
        assert (
            abs(picture.height - (30 + 2 * pdf_formula.PAD_Y) * pdf_formula.SCALE) <= 2
        )


def test_a_region_covering_the_page_is_refused_rather_than_cropped(tmp_path):
    """A box over most of the page is a layout mistake. Cropping it would
    replace the page's prose with a picture of itself."""
    pdfium_or_skip()
    pdf = write_pdf(tmp_path / "book.pdf", ["Prose."])
    regions = pdf_formula.mark(FakeDoc(FakeItem(page=1, box=(0, 0, 612, 792))))
    groups = pdf_formula.merge(regions)
    images, warnings = pdf_formula.rasterize(pdf, groups, tmp_path)
    assert images == {}
    assert warnings == [FORMULA_REGION_OVERSIZE.format(page=1, share=100)]


def test_the_serializer_still_behaves_the_way_mark_depends_on():
    """PIN: the three branches of docling-core's FormulaItem serializer.

    `mark` exists because the third one exports nothing at all. If a
    docling upgrade changes this, the placeholders stop lining up with the
    regions and equations would land in the wrong place -- so it fails
    here, loudly, rather than in somebody's book.
    """
    serializer = pytest.importorskip("docling_core.transforms.serializer.markdown")
    import inspect

    source = inspect.getsource(serializer)
    assert 'text_part = f"$${text}$$"' in source or "$${text}$$" in source
    assert PLACEHOLDER in source
