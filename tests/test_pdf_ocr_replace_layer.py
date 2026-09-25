"""`--ocr-replace-layer`: fresh OCR replaces an embedded text layer, as an
explicit setting.

PIN (owner ruling 260923, docs/260923-docs-OWNER_RULINGS_OCR_PROMPT_LAYER_WIKI.md
section 6, packet G): replacement is explicit and off by default; the choice
is extraction identity, so toggling it re-extracts; neither OCR nor an image
model implies it; and when replacement is asked for and the engine reads
nothing on a page that carried a layer, that page is empty and said to be,
never silently filled from the layer. Measured reason it is off by default:
docs/260923-eval-PDF_OCR_LUNA_VS_LOCAL_BASELINE.md findings 1 and 4 (a
full-page rerun was worse than the layer on all three scored pages and came
back empty on innerspace with a success status).

No model is loaded here. The conversion is the `convert=` seam; the text
layer is the real reader over PDFs written by `pipeline_helpers.write_pdf`
(pypdfium2), with visible text and with the invisible OCR-layer shape.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from pipeline_helpers import PNG, pandoc_or_skip, write_pdf  # noqa: E402

from book_maker.pipeline import docling_parser, stages  # noqa: E402
from book_maker.pipeline.bundle import Bundle  # noqa: E402
from book_maker.pipeline.errors import PipelineError  # noqa: E402
from book_maker.pipeline.messages import (  # noqa: E402
    DEVICE_SELECTED,
    EXTRACTION_EMPTY,
    INVISIBLE_TEXT_LAYER,
    OCR_LANG_DEFAULT,
    OCR_REPLACE_ALL_EMPTY,
    OCR_REPLACE_EMPTY,
    OCR_REPLACE_EMPTY_MORE,
    OCR_REPLACE_NEEDS_OCR,
    OCR_REPLACING_LAYER,
    PDF_OPTIONS_INERT,
)
from book_maker.pipeline.pdf_settings import ExtractionSettings  # noqa: E402

BREAK = docling_parser.PAGE_BREAK
REPLACE = ExtractionSettings(ocr=True, ocr_mode="full_page")
KEEP = ExtractionSettings(ocr=True)


def _pdfium_or_skip():
    pypdfium2 = pytest.importorskip("pypdfium2")
    if not hasattr(pypdfium2, "PdfDocument"):
        pytest.skip("pypdfium2 is installed but unusable")


@pytest.fixture
def pandoc():
    return pandoc_or_skip()


@pytest.fixture(autouse=True)
def device(monkeypatch):
    """The resolved device, without docling (the one call that imports it)."""
    monkeypatch.setattr(
        docling_parser,
        "resolve_device",
        lambda requested: ("cpu", DEVICE_SELECTED.format(device="cpu")),
    )


@pytest.fixture
def bundle(tmp_path):
    return Bundle(tmp_path / "bundle").create()


@pytest.fixture(params=["visible", "invisible"])
def layered(request, tmp_path):
    """A PDF whose pages all carry a text layer, `count` pages long.

    `visible` is a typed page; `invisible` is the scanned-book shape, the
    recognised text in render mode 3 (tests/test_docling_adapter.py's
    invisible-layer fixture).
    """
    _pdfium_or_skip()

    def make(count=2):
        pages = [f"Layer text on page {n}." for n in range(1, count + 1)]
        modes = {n: 3 for n in range(1, count + 1)}
        return write_pdf(
            tmp_path / f"{request.param}.pdf",
            pages,
            render_mode=modes if request.param == "invisible" else None,
        )

    make.kind = request.param
    return make


def converting(pages, *, report_pages=True):
    """The `convert=` seam, returning `pages` (text or None) as the export.

    `report_pages` also answers `text_pages`, as the real converter does
    from the document; without it the adapter reads the page markers.
    """
    calls = []

    def convert(pdf_path, **kwargs):
        calls.append(kwargs)
        images = Path(kwargs["out_dir"]) / docling_parser.IMAGE_DIR
        images.mkdir(parents=True, exist_ok=True)
        (images / "imageFile1.png").write_bytes(PNG)
        if report_pages:
            kwargs["report"]["text_pages"] = [
                number for number, text in enumerate(pages, start=1) if text
            ]
        # docling writes no break for a page with no item; the seam says
        # what the document says, and the markers are not relied on.
        return BREAK.join(text or "" for text in pages)

    convert.calls = calls
    return convert


def _extract(bundle, pdf, pandoc, convert, settings=REPLACE, **kwargs):
    return docling_parser.extract_pdf(
        bundle, pdf, pandoc=pandoc, settings=settings, convert=convert, **kwargs
    )


# --------------------------------------------------------------- settings
def test_the_flag_is_the_full_page_ocr_mode_and_nothing_else():
    assert REPLACE.ocr_replace_layer is True
    assert KEEP.ocr_replace_layer is False
    # without OCR there is nothing to replace the layer with
    assert ExtractionSettings(ocr_mode="full_page").ocr_replace_layer is False


def test_toggling_the_flag_changes_the_identity():
    assert REPLACE.identity() != KEEP.identity()
    assert REPLACE.identity()["ocr_mode"] == "full_page"
    # no new identity field: `ocr_mode` already is one
    assert set(REPLACE.identity()) == set(KEEP.identity())


def test_the_stage_builds_the_setting_from_the_flag(tmp_path, monkeypatch):
    seen = []

    def record(bundle, path, *, settings, **kwargs):
        seen.append(settings)

    monkeypatch.setattr(docling_parser, "extract_pdf", record)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.7\n%fake\n")
    for flag in (True, False):
        stages.prepare(
            Bundle(tmp_path / f"b{flag}").create(),
            pdf,
            pandoc="pandoc",
            ocr=True,
            ocr_replace_layer=flag,
        )
    assert [s.ocr_mode for s in seen] == ["full_page", "default"]


def test_the_converter_asks_docling_for_full_page_ocr_only_with_the_flag(
    monkeypatch,
):
    pytest.importorskip("docling.document_converter")
    import docling.document_converter as module
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import OcrMode

    built = []

    class Recorder:
        def __init__(self, format_options):
            built.append(format_options[InputFormat.PDF].pipeline_options)

    monkeypatch.setattr(module, "DocumentConverter", Recorder)
    docling_parser._converter("cpu", REPLACE)
    docling_parser._converter("cpu", KEEP)
    assert [options.ocr_options.mode for options in built] == [
        OcrMode.FULL_PAGE,
        OcrMode.DEFAULT,
    ]
    assert all(options.do_ocr for options in built)


def test_the_document_s_own_pages_decide_what_was_read():
    """docling writes no page break for a page with no item, so the page
    markers after an empty page count short; the pages that were read are
    taken from the document instead."""
    pytest.importorskip("docling_core")
    from docling_core.types.doc import (
        BoundingBox,
        DocItemLabel,
        DoclingDocument,
        ProvenanceItem,
        Size,
    )

    document = DoclingDocument(name="t")
    for number in (1, 2, 3):
        document.add_page(page_no=number, size=Size(width=600, height=800))
    box = BoundingBox(l=10, t=10, r=100, b=40)
    for page, text in ((2, "read on two"), (3, "  ")):
        document.add_text(
            label=DocItemLabel.TEXT,
            text=text,
            prov=ProvenanceItem(page_no=page, bbox=box, charspan=(0, len(text))),
        )
    assert docling_parser._text_pages(document) == [2]


# ------------------------------------------------------ failure semantics
def test_a_page_read_empty_is_named_and_recorded_and_the_run_goes_on(
    bundle, layered, pandoc, capsys
):
    pdf = layered()
    _extract(bundle, pdf, pandoc, converting(["Read by OCR on one.", None]))
    out = capsys.readouterr().out
    line = OCR_REPLACE_EMPTY.format(page=2)
    assert line in out
    assert OCR_REPLACE_EMPTY.format(page=1) not in out
    assert bundle.stage_status("extract") == "completed"
    manifest = bundle.read_manifest()
    assert line in manifest["limitations"]
    assert line in manifest["extraction"]["limitations"]
    # the empty page is empty: nothing of the layer was put in its place
    assert "Layer text" not in bundle.source.read_text(encoding="utf-8")


def test_every_page_read_empty_stops_before_translation(
    bundle, layered, pandoc, capsys
):
    pdf = layered()
    with pytest.raises(PipelineError) as stopped:
        _extract(bundle, pdf, pandoc, converting([None, None]))
    # the replacement's own stop, not the generic "rerun with --pdf-ocr"
    assert stopped.value.detail == OCR_REPLACE_ALL_EMPTY
    assert stopped.value.detail != EXTRACTION_EMPTY
    out = capsys.readouterr().out
    assert OCR_REPLACE_EMPTY.format(page=1) in out
    assert OCR_REPLACE_EMPTY.format(page=2) in out
    assert bundle.stage_status("extract") == "failed"
    assert not bundle.source.exists()


def test_an_empty_first_page_is_named_by_the_document_not_the_markers(
    bundle, layered, pandoc, capsys
):
    # docling's export writes no break for a page with no item, so an
    # empty page 1 leaves page 2's text under the first marker; the
    # document's own page numbers name the right page.
    pdf = layered()

    def convert(pdf_path, **kwargs):
        kwargs["report"]["text_pages"] = [2]
        return "Read by OCR on two.\n"

    _extract(bundle, pdf, pandoc, convert)
    out = capsys.readouterr().out
    assert OCR_REPLACE_EMPTY.format(page=1) in out
    assert OCR_REPLACE_EMPTY.format(page=2) not in out


def test_the_page_markers_answer_when_the_converter_does_not(
    bundle, layered, pandoc, capsys
):
    pdf = layered()
    _extract(
        bundle,
        pdf,
        pandoc,
        converting(["Read by OCR on one.", None], report_pages=False),
    )
    assert OCR_REPLACE_EMPTY.format(page=2) in capsys.readouterr().out


def test_without_the_flag_an_empty_page_is_not_called_a_replaced_layer(
    bundle, layered, pandoc, capsys
):
    pdf = layered()
    _extract(bundle, pdf, pandoc, converting(["Text.", None]), settings=KEEP)
    out = capsys.readouterr().out
    assert "--ocr-replace-layer the layer is not used" not in out
    assert OCR_REPLACING_LAYER not in out


def test_a_long_list_of_empty_pages_is_capped_on_the_terminal_only(
    bundle, tmp_path, pandoc, capsys
):
    _pdfium_or_skip()
    pdf = write_pdf(tmp_path / "long.pdf", [f"Layer {n}." for n in range(1, 13)])
    _extract(bundle, pdf, pandoc, converting(["Only page one read."] + [None] * 11))
    out = capsys.readouterr().out
    assert out.count("the OCR engine read nothing where the PDF carried") == 10
    assert OCR_REPLACE_EMPTY.format(page=11) in out
    assert OCR_REPLACE_EMPTY.format(page=12) not in out
    assert OCR_REPLACE_EMPTY_MORE.format(count=1) in out
    limitations = bundle.read_manifest()["extraction"]["limitations"]
    assert [n for n in limitations if "read nothing where" in n] == [
        OCR_REPLACE_EMPTY.format(page=n) for n in range(2, 13)
    ]


def test_a_page_without_a_layer_is_not_a_replaced_layer(
    bundle, tmp_path, pandoc, capsys
):
    # page 2 has no text layer at all: OCR_EMPTY_PAGES is its warning,
    # not the replacement's
    _pdfium_or_skip()
    pdf = write_pdf(tmp_path / "mixed.pdf", ["Layer on one.", None])
    _extract(bundle, pdf, pandoc, converting(["Read on one.", None]))
    out = capsys.readouterr().out
    assert OCR_REPLACE_EMPTY.format(page=2) not in out
    assert "no text was recognised on page(s) 2" in out


def test_a_selection_is_checked_page_by_page_with_its_own_numbers(
    bundle, tmp_path, pandoc, capsys
):
    _pdfium_or_skip()
    pdf = write_pdf(tmp_path / "four.pdf", [f"Layer {n}." for n in range(1, 5)])
    convert = converting(["Read on three.", None])

    def shifted(pdf_path, **kwargs):
        # the document numbers its pages as the PDF does
        result = convert(pdf_path, **kwargs)
        kwargs["report"]["text_pages"] = [3]
        return result

    _extract(bundle, pdf, pandoc, shifted, page_range="3-4")
    out = capsys.readouterr().out
    assert OCR_REPLACE_EMPTY.format(page=4) in out
    assert OCR_REPLACE_EMPTY.format(page=3) not in out


# ------------------------------------------------------ what is said/kept
def test_the_replacement_is_said_and_asks_for_languages(
    bundle, layered, pandoc, capsys
):
    pdf = layered()
    _extract(bundle, pdf, pandoc, converting(["One.", "Two."]))
    out = capsys.readouterr().out
    assert OCR_REPLACING_LAYER in out
    # the case where the language matters most: every page is read again
    assert out.count(OCR_LANG_DEFAULT) == 1
    if layered.kind == "invisible":
        # the invisible layer line keeps printing, and names the flag
        assert INVISIBLE_TEXT_LAYER.format(count=2, total=2) in out
        assert "--ocr-replace-layer" in INVISIBLE_TEXT_LAYER


def test_languages_given_silence_the_hint(bundle, layered, pandoc, capsys):
    pdf = layered()
    settings = ExtractionSettings(ocr=True, ocr_mode="full_page", ocr_lang=("ch",))
    _extract(bundle, pdf, pandoc, converting(["One.", "Two."]), settings=settings)
    assert OCR_LANG_DEFAULT not in capsys.readouterr().out


def test_the_manifest_says_the_layer_was_replaced(bundle, layered, pandoc):
    pdf = layered()
    _extract(bundle, pdf, pandoc, converting(["One.", "Two."]))
    extraction = bundle.read_manifest()["extraction"]
    assert extraction["ocr_mode"] == "full_page"
    assert extraction["ocr_replace_layer"] is True
    assert extraction["pages_read_by_ocr"] == [1, 2]
    # read back as the same setting; the derived field is not identity
    assert ExtractionSettings.from_manifest(extraction) == REPLACE


def test_the_manifest_says_a_kept_layer_was_kept(bundle, layered, pandoc):
    pdf = layered()
    _extract(bundle, pdf, pandoc, converting(["One.", "Two."]), settings=KEEP)
    extraction = bundle.read_manifest()["extraction"]
    assert extraction["ocr_replace_layer"] is False
    assert extraction["pages_read_by_ocr"] == []


def test_a_rerun_that_toggles_the_flag_extracts_again(bundle, layered, pandoc):
    pdf = layered()
    _extract(bundle, pdf, pandoc, converting(["One.", "Two."]))
    assert stages.already_prepared(bundle, pdf, "docling", None, REPLACE)
    assert not stages.already_prepared(bundle, pdf, "docling", None, KEEP)


# ---------------------------------------------------------------- harness
def _harness():
    import importlib.util

    path = Path(__file__).resolve().parent.parent / "tools" / "pdf_to_book.py"
    spec = importlib.util.spec_from_file_location("pdf_to_book", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_harness_takes_the_flag_to_the_extraction(tmp_path, pandoc, monkeypatch):
    seen = []

    def record(bundle, path, *, settings, **kwargs):
        seen.append(settings)
        raise PipelineError("stopped before the models", stage="extract")

    monkeypatch.setattr(docling_parser, "extract_pdf", record)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.7\n%fake\n")
    code = _harness().main(
        [
            "--pandoc",
            pandoc,
            "extract",
            str(pdf),
            "--output",
            str(tmp_path / "b"),
            "--pdf-ocr",
            "--ocr-replace-layer",
        ]
    )
    assert code == 1
    assert [s.ocr_mode for s in seen] == ["full_page"]


def test_the_harness_refuses_the_flag_without_ocr(
    tmp_path, pandoc, monkeypatch, capsys
):
    def refuse(*args, **kwargs):
        raise AssertionError("extracted without OCR to replace the layer with")

    monkeypatch.setattr(docling_parser, "extract_pdf", refuse)
    pdf = tmp_path / "book.pdf"
    pdf.write_bytes(b"%PDF-1.7\n%fake\n")
    code = _harness().main(
        [
            "--pandoc",
            pandoc,
            "extract",
            str(pdf),
            "--output",
            str(tmp_path / "b"),
            "--ocr-replace-layer",
        ]
    )
    assert code == 1
    assert OCR_REPLACE_NEEDS_OCR in " ".join(capsys.readouterr().out.split())


def test_the_flag_on_markdown_is_refused_as_a_pdf_option():
    options = type("Options", (), {"ocr_replace_layer": True, "pdf_ocr": True})()
    with pytest.raises(PipelineError) as refused:
        stages.check_pdf_options("markdown", options)
    assert refused.value.detail == PDF_OPTIONS_INERT
    assert "--ocr-replace-layer" in PDF_OPTIONS_INERT
