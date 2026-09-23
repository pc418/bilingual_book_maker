"""Heading levels read from the page.

PIN (lead 260922, docs/260922-feat-PDF_HEADING_LEVELS.md): docling writes
every heading at `##`, so the EPUB contents came out flat -- 42.6% of
levels exact on twenty arXiv papers. Numbering first, then the largest
typographic style as the title and a numbered heading's level for any
other heading in the same style, measured 95.9% exact, 19 of 20 papers
entirely right. The style rank is opendataloader-pdf's (Apache-2.0),
written afresh; its heading detector is not used.
"""

import pytest

from book_maker.pipeline import pdf_headings
from pipeline_helpers import write_pdf


@pytest.mark.parametrize(
    "text,level",
    [
        ("1 Introduction", 2),
        ("1. Introduction", 2),
        ("2.3 Method", 3),
        ("2.3.1 Detail", 4),
        ("1.2.3.4.5.6.7 deep", 6),
        ("II. RELATED WORK", 2),
        ("A. Proofs", 3),
        ("3) Third", 4),
        ("Abstract", None),
        ("2024 was a year", 2),  # a year reads as a number; numbering wins anyway
        ("1", None),  # a number alone is not a heading with a number
    ],
)
def test_numbering_states_the_level(text, level):
    assert pdf_headings.numbering_level(text) == level


def test_numbering_first_then_the_largest_style_then_a_numbered_sibling_s_level():
    big, section, sub = (17.0, False), (12.0, True), (10.0, True)
    headings = [
        ("Attention Is All You Need", big),
        ("Abstract", section),
        ("1 Introduction", section),
        ("2 Background", section),
        ("3.1 Encoder and Decoder Stacks", sub),
        ("3.2.1 Scaled Dot-Product Attention", sub),  # same font as 3.1
        ("Acknowledgements", sub),
        ("References", section),
        ("Appendix", None),  # no glyphs under it: a scanned page
    ]
    assert pdf_headings.levels(headings) == [1, 2, 2, 2, 3, 4, 3, 2, 2]


def test_a_document_with_no_style_at_all_still_levels_its_numbering():
    headings = [("Title", None), ("1 Intro", None), ("1.1 Sub", None), ("Refs", None)]
    assert pdf_headings.levels(headings) == [2, 2, 3, 2]


def test_the_style_is_read_under_the_box_in_docling_s_cropbox_frame(tmp_path):
    pytest.importorskip("pypdfium2")
    # The helper writes 18pt Helvetica at MediaBox (72, 700); with this
    # CropBox docling would report the line at (32, 640).
    pdf = write_pdf(
        tmp_path / "book.pdf", ["A heading line"], cropbox=(40, 60, 452, 750)
    )
    found = pdf_headings.styles(
        pdf, {0: (1, (20.0, 630.0, 300.0, 660.0)), 1: (1, (20.0, 100.0, 300.0, 130.0))}
    )
    assert found == {0: (18.0, False), 1: None}


class FakeBox:
    def __init__(self, l, b, r, t):
        self.l, self.b, self.r, self.t = l, b, r, t


class FakeProv:
    def __init__(self, page_no, box):
        self.page_no, self.bbox = page_no, FakeBox(*box)


class FakeItem:
    def __init__(self, label, text, box=None):
        self.label, self.text, self.level = label, text, 1
        self.prov = [FakeProv(1, box)] if box else []


class FakeDoc:
    def __init__(self, *items):
        self.items = items

    def iterate_items(self):
        return ((item, 0) for item in self.items)


def test_assign_sets_the_level_on_every_section_header(tmp_path):
    pytest.importorskip("pypdfium2")
    pdf = write_pdf(tmp_path / "book.pdf", ["A heading line"])
    title = FakeItem("section_header", "The Title", (60.0, 690.0, 320.0, 720.0))
    numbered = FakeItem("section_header", "2.1 Sub", (60.0, 100.0, 320.0, 120.0))
    plain = FakeItem("section_header", "Notes")  # no position at all
    body = FakeItem("text", "prose", (60.0, 690.0, 320.0, 720.0))
    assert pdf_headings.assign(FakeDoc(title, numbered, plain, body), pdf) == (3, 1)
    assert (title.level, numbered.level, plain.level, body.level) == (1, 3, 2, 1)


def test_promote_lifts_every_heading_one_level_and_leaves_a_title_item():
    markdown = (
        "# Title item\n\n## Top\n\nprose\n\n### 1 Intro\n\n#### 1.1 Sub\n| ## cell |\n"
    )
    assert pdf_headings.promote(markdown) == (
        "# Title item\n\n# Top\n\nprose\n\n## 1 Intro\n\n### 1.1 Sub\n| ## cell |\n"
    )
