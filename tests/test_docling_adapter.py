"""The PDF parser: which device it runs on, and what it leaves in the bundle.

Nothing here downloads a model or converts a real PDF -- those are live
checks recorded in the handback. What is asserted is everything the route
decides around the parser: the device it resolves, the refusals that must
not be silently survivable, the Markdown post-processing that runs on
whatever the parser produced, and the bundle contract the rest of the
pipeline reads.

docling is never imported. `extract_pdf(convert=...)` replaces the one call
that would load it, and `resolve_device` -- the only other place in the
route that touches it -- is either fed a fake `docling.utils` module or
replaced outright. So this file runs on an install with no PDF extra, which
is the install most of this tool's users have.
"""

import json
import sys
import types
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from pipeline_helpers import (  # noqa: E402
    PNG,
    FakeTranslator,
    pandoc_or_skip,
    register_fake_format,
    write_pdf,
)

from book_maker.pipeline import docling_parser, pdf_common, stages  # noqa: E402
from book_maker.pipeline.bundle import EXTRACTION_JOB, Bundle  # noqa: E402
from book_maker.pipeline.epub_export import export_epub  # noqa: E402
from book_maker.pipeline.errors import PipelineError  # noqa: E402
from book_maker.pipeline.messages import (  # noqa: E402
    BACKEND_FAILED,
    DEVICE_CPU_FALLBACK,
    DEVICE_NO_CUDA_BUILD,
    DEVICE_SELECTED,
    DEVICE_UNAVAILABLE,
    EXTRACTION_EMPTY,
    OCR_EMPTY,
    OCR_EMPTY_PAGES,
    OCR_LANG_DEFAULT,
    OCR_REQUIRED,
    PAGE_TOO_DENSE,
    PAGES_SPAN_CONVERTED,
    PDF_OPTIONS_INERT,
    SCANNED_PAGES,
    SELECTION_HEADING_ADDED,
    TITLE_HEADING_ADDED,
)

# The real page-geometry reader, captured before the autouse fixture below
# replaces the adapter's reference to it, so the tests of the real detector
# get the real detector.
from book_maker.pipeline.pdf_common import (  # noqa: E402
    text_layer_report as real_text_layer_report,
)
from book_maker.pipeline.translate import translate_bundle  # noqa: E402

HARNESS = Path(__file__).resolve().parent.parent / "tools" / "pdf_to_book.py"

BREAK = docling_parser.PAGE_BREAK

# What the parser returns: Markdown with its own page-break placeholder
# between two pages and none before the first. The numbered `<!-- page N -->`
# markers the rest of the pipeline reads are put in afterwards, by the
# adapter, which is why they are absent here.
PAGE_ONE = """# Chapter One

The first paragraph is ordinary prose that the model will translate.

The second paragraph carries a [link](https://example.com) and `inline code`,
and its translation comes back as two paragraphs.

![](images/imageFile1.png)"""

PAGE_TWO = """## Notes

A closing paragraph under the second heading."""

CONVERTED = f"{PAGE_ONE}\n{BREAK}\n{PAGE_TWO}\n"


def load_harness():
    import importlib.util

    spec = importlib.util.spec_from_file_location("pdf_to_book", HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def pandoc():
    return pandoc_or_skip()


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "book.pdf"
    path.write_bytes(b"%PDF-1.7\n%fake\n")
    return path


@pytest.fixture(autouse=True)
def text_layer(monkeypatch):
    """What the PDF's own text layer says, under the test's control.

    The real reader needs pypdfium2 and a real PDF; it has its own tests
    further down, which skip where pdfium is not installed. Every other
    test here is about what the route *does* with the answer, so the
    answer is given: by default every page carries text.
    """
    state = {"missing": [], "examined": 2}

    def report(pdf_path, page_range=None):
        state["asked"] = (Path(pdf_path), page_range)
        return list(state["missing"]), state["examined"]

    monkeypatch.setattr(docling_parser, "text_layer_report", report)
    return state


@pytest.fixture
def device(monkeypatch):
    """The resolved device, without docling.

    `resolve_device` is the one call in an extraction that imports docling,
    and `extract_pdf` makes it before anything else -- even when the
    conversion itself is injected. Replacing it is what lets the whole
    route be exercised on an install with no PDF extra.
    """
    state = {"resolved": "mps", "requested": []}

    def resolve(requested):
        state["requested"].append(requested)
        return state["resolved"], DEVICE_SELECTED.format(device=state["resolved"])

    monkeypatch.setattr(docling_parser, "resolve_device", resolve)
    return state


@pytest.fixture
def bundle(tmp_path):
    return Bundle(tmp_path / "bundle").create()


@pytest.fixture
def accelerators(monkeypatch):
    """docling's device decision, with the hardware under the test's control.

    The real function is used in `test_the_real_docling_decision_is_reused`;
    this mirrors its contract (auto resolves, cpu is always cpu, a named
    device that is not there raises) so every other test can choose what
    the machine has.
    """
    state = {"available": ["mps"]}

    class AcceleratorDeviceNotAvailableError(Exception):
        pass

    def decide_device(requested, supported_devices=None):
        if requested == "cpu":
            return "cpu"
        if requested == "auto":
            return state["available"][0] if state["available"] else "cpu"
        if requested in state["available"]:
            return "cuda:0" if requested == "cuda" else requested
        raise AcceleratorDeviceNotAvailableError(requested)

    module = types.ModuleType("docling.utils.accelerator_utils")
    module.decide_device = decide_device
    module.AcceleratorDeviceNotAvailableError = AcceleratorDeviceNotAvailableError
    monkeypatch.setitem(sys.modules, "docling", types.ModuleType("docling"))
    monkeypatch.setitem(sys.modules, "docling.utils", types.ModuleType("docling.utils"))
    monkeypatch.setitem(sys.modules, "docling.utils.accelerator_utils", module)
    return state


# --------------------------------------------------------------------------
# Device selection
# --------------------------------------------------------------------------
def test_auto_takes_the_accelerator_that_is_there(accelerators):
    accelerators["available"] = ["mps"]
    resolved, message = docling_parser.resolve_device("auto")
    assert resolved == "mps"
    assert message == DEVICE_SELECTED.format(device="mps")


def test_auto_falls_back_to_cpu_and_says_so(accelerators):
    accelerators["available"] = []
    resolved, message = docling_parser.resolve_device("auto")
    assert resolved == "cpu"
    assert message == DEVICE_CPU_FALLBACK


def test_cpu_is_forced_even_when_an_accelerator_exists(accelerators):
    # PIN (owner 260921, docs/260921-plan-PDF_DOCLING_ONLY_AND_INSTALL_ROUTES.md):
    # `--device cpu` is a supported configuration, not a degraded one --
    # measured, CPU output is identical to MPS output and only slower.
    accelerators["available"] = ["cuda"]
    resolved, message = docling_parser.resolve_device("cpu")
    assert resolved == "cpu"
    assert message == DEVICE_SELECTED.format(device="cpu")


def test_a_named_accelerator_that_is_present_is_honoured(accelerators):
    accelerators["available"] = ["cuda"]
    resolved, _ = docling_parser.resolve_device("cuda")
    # docling answers `cuda:0`; everything downstream takes the family.
    assert resolved == "cuda"


# `cuda` is left out on purpose: it is the one device whose refusal also
# consults the installed PyTorch, so it is covered by the test below, which
# controls what that answers. Asserting it here would pass or fail with the
# torch build of whoever runs the suite.
@pytest.mark.parametrize("device", ["mps", "xpu"])
def test_a_named_accelerator_that_is_absent_fails_by_name(accelerators, device):
    accelerators["available"] = []
    with pytest.raises(PipelineError) as refused:
        docling_parser.resolve_device(device)
    assert refused.value.detail == DEVICE_UNAVAILABLE.format(device=device)


def test_a_cpu_only_torch_build_is_not_a_machine_without_a_card(
    accelerators, monkeypatch
):
    """The two CUDA refusals docling reports as one.

    One is fixed by reinstalling through the CUDA route and the other never
    is, so an operator who asked for `--device cuda` is told which happened.
    """
    accelerators["available"] = []
    torch = types.ModuleType("torch")
    torch.version = types.SimpleNamespace(cuda=None)
    monkeypatch.setitem(sys.modules, "torch", torch)
    with pytest.raises(PipelineError) as refused:
        docling_parser.resolve_device("cuda")
    assert refused.value.detail == DEVICE_NO_CUDA_BUILD

    # The same machine with a CUDA-built torch: the card is what is missing.
    torch.version = types.SimpleNamespace(cuda="12.4")
    with pytest.raises(PipelineError) as refused:
        docling_parser.resolve_device("cuda")
    assert refused.value.detail == DEVICE_UNAVAILABLE.format(device="cuda")


def test_an_unknown_device_is_refused_before_docling_is_asked(accelerators):
    with pytest.raises(PipelineError) as refused:
        docling_parser.resolve_device("tpu")
    assert "is not a device" in refused.value.detail


def test_a_missing_pdf_install_is_an_error_not_a_cpu_fallback(monkeypatch):
    """No docling means no PDF route; it must not read as 'no accelerator'."""
    for name in ("docling", "docling.utils", "docling.utils.accelerator_utils"):
        monkeypatch.setitem(sys.modules, name, None)
    with pytest.raises(PipelineError) as refused:
        docling_parser.resolve_device("auto")
    # PIN (owner 260921, docs/260921-plan-PDF_DOCLING_ONLY_AND_INSTALL_ROUTES.md):
    # PDF input without the PDF install refuses with the install line and a
    # pointer to the guide. It never degrades silently.
    detail = refused.value.detail
    assert "requirements-pdf-cpu.txt" in detail
    assert "requirements-pdf-gpu.txt" in detail
    assert "docs/installation-pdf.md" in detail
    # PIN (260921): the published package has no `pdf` extra, and pip meets a
    # missing extra with a warning and a successful install of the release
    # without it -- an operator who followed that line would arrive back here
    # unchanged. The message may name the command only to say it does not
    # work, and the checkout route has to come first.
    assert 'pip install "bbook_maker[pdf]"' in detail
    assert detail.index("requirements-pdf-gpu.txt") < detail.index("bbook_maker[pdf]")
    assert "does not carry this route yet" in detail


def test_the_real_docling_decision_is_reused():
    """The detection is upstream's, not a hardware probe of our own."""
    pytest.importorskip("docling.utils.accelerator_utils")
    resolved, message = docling_parser.resolve_device("auto")
    assert resolved in docling_parser.DEVICES
    assert resolved != "auto"
    assert message.startswith("PDF extraction device:")
    assert docling_parser.resolve_device("cpu")[0] == "cpu"


# --------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------
def fake_convert(markdown=CONVERTED, image=PNG, fail=None, says=()):
    """The `convert=` seam: the one call that would load a model.

    The real one returns the export's Markdown and writes the pictures
    beside it; `says` is what it prints while it runs, which the adapter
    captures for the progress line and for a failure message.
    """
    calls = []

    def convert(pdf_path, **kwargs):
        calls.append(dict(kwargs, pdf=Path(pdf_path)))
        for line in says:
            print(line)
        if fail is not None:
            raise fail
        images = Path(kwargs["out_dir"]) / docling_parser.IMAGE_DIR
        images.mkdir(parents=True, exist_ok=True)
        (images / "imageFile1.png").write_bytes(image)
        return markdown

    convert.calls = calls
    return convert


def test_a_conversion_produces_the_same_bundle_contract(bundle, pdf, pandoc, device):
    convert = fake_convert()
    docling_parser.extract_pdf(
        bundle,
        pdf,
        pandoc=pandoc,
        ocr=True,
        device="auto",
        page_range="1-2",
        convert=convert,
    )

    kwargs = convert.calls[0]
    assert kwargs["pdf"] == pdf
    # One run of pages, numbered from 1 like the flag.
    assert kwargs["span"] == (1, 2)
    assert kwargs["device"] == "mps"
    assert kwargs["ocr"] is True
    assert kwargs["languages"] is None
    assert device["requested"] == ["auto"]

    assert bundle.source.is_file()
    # The parser's own `images/` directory is kept inside `assets/`, so two
    # chapters' `plate.png` cannot collide.
    assert (bundle.assets / "images" / "imageFile1.png").read_bytes() == PNG
    assert "assets/images/imageFile1.png" in bundle.source.read_text(encoding="utf-8")

    manifest = bundle.read_manifest()
    assert manifest["stages"]["extract"]["status"] == "completed"
    assert manifest["source"]["kind"] == "pdf"
    extraction = manifest["extraction"]
    assert extraction["provider"] == "docling"
    assert extraction["device"] == "mps"
    assert extraction["device_requested"] == "auto"
    assert extraction["picture_description"] is False
    assert extraction["ocr"] is True
    assert extraction["page_numbering"] == "1-based input, 1-based request"
    assert extraction["pdf"] == pdf.name
    assert extraction["page_range"] == "1-2"
    # Nothing was bought: the local parser has no provider charge.
    assert extraction["cost_cents"] is None
    job = json.loads(bundle.work_file(EXTRACTION_JOB).read_text(encoding="utf-8"))
    assert job["parser"] == "docling"
    assert job["device"] == "mps"


def test_the_export_s_page_breaks_become_the_markers_the_pipeline_reads():
    """docling breaks between two pages and never before the first."""
    numbered = docling_parser._number_pages(f"one{BREAK}two", None)
    assert numbered == "<!-- page 1 -->\n\none\n\n<!-- page 2 -->\n\ntwo\n"
    # A selection is numbered from the page it starts on, not from 1.
    assert docling_parser._number_pages(f"a{BREAK}b", 6).startswith("<!-- page 6 -->")
    assert "<!-- page 7 -->" in docling_parser._number_pages(f"a{BREAK}b", 6)
    # One page, one marker, and no marker for a page that does not exist.
    assert docling_parser._number_pages("only", 3) == "<!-- page 3 -->\n\nonly\n"


def test_a_picture_reference_is_a_path_beside_the_markdown():
    """A bundle has to survive being moved, so the absolute directory goes.

    docling writes the directory it was handed into every reference, and
    Pandoc resolves the reference against the Markdown file.
    """
    rewritten = docling_parser._relative_images(
        "![](/work/x/images/p1.png) and ![alt](/elsewhere/q.png)", "/work/x/images"
    )
    assert "![](images/p1.png)" in rewritten
    # Not ours to rewrite: only what the parser was told to write goes.
    assert "![alt](/elsewhere/q.png)" in rewritten


def test_a_selection_with_a_gap_reads_the_run_and_drops_the_rest(
    bundle, pdf, pandoc, device, capsys
):
    """One converter call reads one run of pages, so a gap costs the middle.

    The pages in the gap are read and then dropped, which is what the
    operator is told rather than charged for silently.
    """
    convert = fake_convert(markdown=f"page one{BREAK}page two{BREAK}page three\n")
    docling_parser.extract_pdf(
        bundle, pdf, pandoc=pandoc, page_range="1,3", convert=convert
    )
    assert convert.calls[0]["span"] == (1, 3)
    source = bundle.source.read_text(encoding="utf-8")
    assert "page one" in source and "page three" in source
    assert "page two" not in source
    assert "<!-- page 2 -->" not in source
    assert PAGES_SPAN_CONVERTED.format(span="1-3") in capsys.readouterr().out


def test_the_conversion_says_it_is_running_and_what_it_said_last(
    bundle, pdf, pandoc, device, capsys
):
    """Minutes of somebody else's work, with a sign of life.

    The parser writes its log to `sys.stdout` itself; the adapter
    intercepts that, so nothing scrolls past the operator and the last
    line it said becomes the progress line's detail. Here the stream is
    not a terminal, so whole lines are printed instead of one being
    rewritten.
    """
    docling_parser.extract_pdf(
        bundle,
        pdf,
        pandoc=pandoc,
        ocr=True,
        convert=fake_convert(says=["INFO - Processing document book.pdf"]),
    )
    out = capsys.readouterr().out
    assert "Extracting PDF: 2 pages, layout and table models on mps, 0s" in out
    assert "PDF extracted: 2 pages, layout and table models on mps," in out
    # the parser's own log line is not printed on its own account
    assert "INFO - Processing document book.pdf" not in out


def test_the_ocr_models_are_named_in_the_progress_line_when_they_run(
    bundle, pdf, pandoc, device, text_layer, capsys
):
    text_layer["missing"] = [2]
    docling_parser.extract_pdf(
        bundle, pdf, pandoc=pandoc, ocr=True, convert=fake_convert()
    )
    out = capsys.readouterr().out
    assert "Extracting PDF: 2 pages, OCR and layout models on mps" in out


def test_a_quiet_extraction_prints_no_progress_at_all(
    bundle, pdf, pandoc, device, capsys
):
    docling_parser.extract_pdf(
        bundle,
        pdf,
        pandoc=pandoc,
        ocr=True,
        convert=fake_convert(),
        progress=False,
    )
    out = capsys.readouterr().out
    assert "Extracting PDF" not in out
    assert "PDF extracted" not in out


def test_a_failed_conversion_quotes_what_the_parser_said_last(
    bundle, pdf, pandoc, device
):
    """The exception alone is not the reason; the log line before it is.

    An exception type says only that the conversion died. What it was
    doing is in the output, which is read for the progress line and would
    otherwise be dropped.
    """
    with pytest.raises(PipelineError) as failed:
        docling_parser.extract_pdf(
            bundle,
            pdf,
            pandoc=pandoc,
            ocr=True,
            convert=fake_convert(
                says=["SEVERE: cannot read the document catalog"],
                fail=RuntimeError("conversion aborted"),
            ),
        )
    assert failed.value.detail == BACKEND_FAILED.format(
        detail=(
            "RuntimeError: conversion aborted "
            "(last log line: SEVERE: cannot read the document catalog)"
        )
    )
    assert bundle.stage_status("extract") == "failed"
    assert not bundle.source.exists()


def test_a_failure_quotes_the_end_of_the_log_and_not_the_whole_of_it(
    bundle, pdf, pandoc, device
):
    """The error is at the end of a model-loading run, and is bounded."""
    chatter = [f"loading shard {n}/500" for n in range(500)]
    with pytest.raises(PipelineError) as failed:
        docling_parser.extract_pdf(
            bundle,
            pdf,
            pandoc=pandoc,
            convert=fake_convert(
                says=[*chatter, "ModuleNotFoundError: no module named 'x'"],
                fail=RuntimeError("exit 1"),
            ),
        )
    detail = failed.value.detail
    assert "ModuleNotFoundError: no module named 'x'" in detail
    assert "loading shard" not in detail
    assert len(detail) < 300, len(detail)


def test_a_refusal_the_parser_logged_is_named_in_the_failure(
    bundle, pdf, pandoc, device
):
    # PIN (lead, 260921, docs/260921-feat-PDF_OCR_LANG_FLAG.md): an unknown
    # OCR language is refused inside the parser, the wrapper then says only
    # that processing failed, and the reason never reached the operator.
    raised = (
        "docling.exceptions.OcrLanguageNotSupportedError: EasyOcr has no model "
        "for the OCR language 'xx'. Supported: iso:zh, iso:ja"
    )
    with pytest.raises(PipelineError) as failed:
        docling_parser.extract_pdf(
            bundle,
            pdf,
            pandoc=pandoc,
            ocr=True,
            convert=fake_convert(
                says=["Traceback (most recent call last):", raised],
                fail=RuntimeError("conversion failed"),
            ),
        )
    assert raised in failed.value.detail


def test_an_interruption_fails_the_stage_and_is_not_swallowed(
    bundle, pdf, pandoc, device
):
    with pytest.raises(KeyboardInterrupt):
        docling_parser.extract_pdf(
            bundle,
            pdf,
            pandoc=pandoc,
            ocr=True,
            convert=fake_convert(fail=KeyboardInterrupt()),
        )
    assert bundle.stage_status("extract") == "failed"


# PRODUCTION BUG (found 260921 while migrating this file, reported to the
# lead, NOT fixed here -- this file may not touch book_maker/): the adapter
# writes the `<!-- page N -->` markers itself, after the conversion, instead
# of taking them from what the parser wrote. So a conversion that returned
# nothing would reach `import_markdown` as "<!-- page 1 -->\n" -- not blank,
# so the importer's own "has no content" refusal never fires, and the stage
# would report completed over an empty book. `check_recognised_text` only
# looks at pages the text layer could not spell out, so nothing else catches
# it. The adapter therefore asks what the PARSER returned, before the markers
# go in. Found 260921 by the test migration; do not relax this to a warning.
def test_an_empty_conversion_is_an_error(bundle, pdf, pandoc, device):
    with pytest.raises(PipelineError) as failed:
        docling_parser.extract_pdf(
            bundle, pdf, pandoc=pandoc, ocr=True, convert=fake_convert(markdown="   \n")
        )
    assert failed.value.detail == EXTRACTION_EMPTY
    assert bundle.stage_status("extract") == "failed"


def test_a_conversion_of_pictures_alone_is_an_error(bundle, pdf, pandoc, device):
    """Markers and an image are not content either.

    The refusal has to look past what the export always writes, or a PDF
    whose pages the parser saw only as pictures would be reported as a
    finished book with nothing in it to read.
    """
    with pytest.raises(PipelineError) as failed:
        docling_parser.extract_pdf(
            bundle,
            pdf,
            pandoc=pandoc,
            ocr=True,
            convert=fake_convert(markdown="<!-- image -->\n\n![](images/a.png)\n"),
        )
    assert failed.value.detail == EXTRACTION_EMPTY


def test_a_pdf_that_is_not_there_is_refused_before_anything_starts(
    bundle, tmp_path, pandoc, device
):
    convert = fake_convert()
    with pytest.raises(PipelineError) as refused:
        docling_parser.extract_pdf(
            bundle, tmp_path / "absent.pdf", pandoc=pandoc, convert=convert
        )
    assert "no PDF at" in refused.value.detail
    assert convert.calls == []


def test_the_bundle_it_produces_translates_and_exports_like_any_other(
    bundle, pdf, pandoc, device, monkeypatch
):
    register_fake_format(monkeypatch)
    docling_parser.extract_pdf(
        bundle, pdf, pandoc=pandoc, ocr=True, convert=fake_convert()
    )
    translate_bundle(
        bundle, ["--api_format", "faketest", "--language", "zh-hans"], pandoc=pandoc
    )
    export_epub(bundle, pandoc=pandoc)

    text = bundle.bilingual_markdown.read_text(encoding="utf-8")
    assert text.count("![](") == 1
    assert "{#chapter-one}" in text and "{#notes}" in text
    with zipfile.ZipFile(bundle.epub) as archive:
        names = archive.namelist()
        body = "".join(
            archive.read(n).decode("utf-8") for n in names if n.endswith(".xhtml")
        )
        assert body.count("<img") == 1
        assert body.count('<div class="bbm-translation"') == 5
        assert len([n for n in names if n.startswith("EPUB/media/")]) == 1
        nav = archive.read("EPUB/nav.xhtml").decode("utf-8")
        assert nav.count('<a href="text/ch') == 2
    # The page markers are provenance, not prose: carried through as HTML
    # comments, so a reader never sees them and the model never gets them.
    assert "<!-- page 1 -->" in text
    assert "<!-- page 1 -->" in body
    assert "<p>page 1" not in body and "译:<!--" not in body
    # The marker above the first heading must not become a chapter of its
    # own: that is an empty page with a table-of-contents entry.
    assert "page 1" not in nav


MID_SECTION = f"""agent.

Before held-out evaluation, we freeze the mechanism source, configuration,
metrics, capability tolerances, and acceptance thresholds, so that nothing
learned on the held-out tasks can leak back into the loop being measured.

# 3. Experimental Evaluation

The first paragraph of the section.
{BREAK}
## 3.1. Overall Comparison

More prose.
"""

MID_SECTION_NUMBERED = """<!-- page 6 -->

agent.

Before held-out evaluation, we freeze the mechanism source.

# 3. Experimental Evaluation

The first paragraph of the section.

<!-- page 7 -->

## 3.1. Overall Comparison

More prose.
"""


class TestASelectionThatStartsMidSection:
    # PIN (owner ask 260921, docs/260921-feat-PDF_PAGES_FLAG.md): the
    # first live run of --pages 6-7 translated two pages and then failed the
    # export, because the selection opened with prose the previous page's
    # heading owned and the table of contents had nothing to point at. The
    # extraction now heads such prose with the page it starts on, in
    # source.md, before anything is paid for.

    # PIN (lead 260922, docs/260922-feat-PDF_FORMULA_IMAGES.md, from the Opus
    # corpus run): Pandoc gives any document that does not OPEN with a
    # level-1 heading a book-title contents entry, at every split level, and
    # docling writes every heading -- the paper's title too -- as `##`. So
    # the rule is "opens with a level-1 heading", not "opens with a
    # heading"; a page-1 document gets the PDF's stem, a later start its
    # page. This reverses the 260921 choice that page 1 owns its front
    # matter: with docling, that choice failed 9 of 12 corpus bundles at
    # export, after paying for the translation.
    @pytest.mark.parametrize(
        "text,first_page,expected,heading",
        [
            (
                MID_SECTION_NUMBERED,
                6,
                "<!-- page 6 -->\n\n# Page 6\n\nagent.\n",
                "Page 6",
            ),
            ("prose first\n\n# H\n", 4, "# Page 4\n\nprose first\n", "Page 4"),
            # a selection opening with a section heading, not a chapter one
            (
                "<!-- page 4 -->\n\n## 3.1 Encoder\n\nprose\n",
                4,
                "<!-- page 4 -->\n\n# Page 4\n\n## 3.1 Encoder\n",
                "Page 4",
            ),
            # page 1: docling's `##` title, and a banner above it
            (
                "<!-- page 1 -->\n\n## Attention\n\n## Abstract\n",
                1,
                "<!-- page 1 -->\n\n# 1706.03762\n\n## Attention\n",
                "1706.03762",
            ),
            (
                "<!-- page 1 -->\n\nPermission banner.\n\n## Attention\n",
                1,
                "<!-- page 1 -->\n\n# 1706.03762\n\nPermission banner.\n",
                "1706.03762",
            ),
            (
                MID_SECTION_NUMBERED,
                None,  # no selection at all
                "<!-- page 6 -->\n\n# 1706.03762\n\nagent.\n",
                "1706.03762",
            ),
        ],
    )
    def test_a_document_that_does_not_open_with_a_chapter_heading_gets_one(
        self, text, first_page, expected, heading
    ):
        headed = pdf_common.heading_for_top(text, first_page, "1706.03762")
        assert headed[0].startswith(expected) and headed[1] == heading

    @pytest.mark.parametrize(
        "text,first_page",
        [
            ("<!-- page 6 -->\n\n# 3. Eval\n\nprose\n", 6),  # already headed
            ("# Title\n\n## Abstract\n", 1),
            ("<!-- page 6 -->\n", 6),  # nothing to head
        ],
    )
    def test_nothing_is_added_when_nothing_needs_it(self, text, first_page):
        assert pdf_common.heading_for_top(text, first_page, "book") is None

    def test_the_extraction_writes_the_heading_and_says_so(
        self, bundle, pdf, pandoc, device, capsys
    ):
        docling_parser.extract_pdf(
            bundle,
            pdf,
            pandoc=pandoc,
            page_range="6-7",
            convert=fake_convert(markdown=MID_SECTION),
        )
        source = bundle.source.read_text(encoding="utf-8")
        assert source.startswith("<!-- page 6 -->\n\n# Page 6\n\nagent.\n")
        assert source.count("# Page 6") == 1
        expected = SELECTION_HEADING_ADDED.format(page=6)
        assert expected in capsys.readouterr().out
        assert expected in bundle.read_manifest()["limitations"]

    def test_a_document_from_page_one_is_headed_with_the_pdf_s_name(
        self, bundle, pdf, pandoc, device, capsys
    ):
        docling_parser.extract_pdf(
            bundle,
            pdf,
            pandoc=pandoc,
            page_range="1-2",
            convert=fake_convert(markdown=MID_SECTION),
        )
        source = bundle.source.read_text(encoding="utf-8")
        assert "# Page" not in source
        assert source.count(f"# {pdf.stem}\n") == 1
        expected = TITLE_HEADING_ADDED.format(title=pdf.stem)
        assert expected in capsys.readouterr().out
        assert expected in bundle.read_manifest()["limitations"]

    def test_the_headed_selection_translates_and_exports(
        self, bundle, pdf, pandoc, device, monkeypatch
    ):
        register_fake_format(monkeypatch)
        docling_parser.extract_pdf(
            bundle,
            pdf,
            pandoc=pandoc,
            page_range="6-7",
            convert=fake_convert(markdown=MID_SECTION),
        )
        translate_bundle(
            bundle, ["--api_format", "faketest", "--language", "zh-hans"], pandoc=pandoc
        )
        export_epub(bundle, pandoc=pandoc)
        with zipfile.ZipFile(bundle.epub) as archive:
            nav = archive.read("EPUB/nav.xhtml").decode("utf-8")
        assert nav.count('<a href="text/ch') == 3
        assert "Page 6" in nav and "#page-6" in nav

    def test_a_paper_whose_headings_are_all_second_level_exports(
        self, bundle, pdf, pandoc, device, monkeypatch
    ):
        # PIN (lead 260922): the shape docling gives every arXiv paper -- a
        # banner, then the title and every section as `##` -- through the
        # real navigation check. Before `heading_for_top` this failed with
        # "5 heading(s) ... 6 entry/entries" on 9 of 12 corpus bundles.
        register_fake_format(monkeypatch)
        paper = (
            "Permission is granted to reproduce the tables and figures "
            "in this paper solely for use in journalistic or scholarly works.\n\n"
            "## Attention Is All You Need\n\nAuthors.\n\n"
            "## Abstract\n\nThe abstract.\n\n## 1 Introduction\n\nProse.\n"
        )
        docling_parser.extract_pdf(
            bundle, pdf, pandoc=pandoc, page_range="1-2", convert=fake_convert(paper)
        )
        assert bundle.source.read_text(encoding="utf-8").startswith(
            f"<!-- page 1 -->\n\n# {pdf.stem}\n\nPermission"
        )
        translate_bundle(
            bundle, ["--api_format", "faketest", "--language", "zh-hans"], pandoc=pandoc
        )
        export_epub(bundle, pandoc=pandoc)
        with zipfile.ZipFile(bundle.epub) as archive:
            nav = archive.read("EPUB/nav.xhtml").decode("utf-8")
        assert nav.count('<a href="text/ch') == 4  # the book heading + 3 sections
        assert "Attention Is All You Need" in nav and "1 Introduction" in nav


# --------------------------------------------------------------------------
# What is read, and what is refused
# --------------------------------------------------------------------------
def test_a_document_that_spells_itself_out_is_not_sent_through_ocr(
    bundle, pdf, pandoc, device, text_layer, capsys
):
    """Every page has a text layer, so nothing is reported as scanned."""
    docling_parser.extract_pdf(
        bundle, pdf, pandoc=pandoc, ocr=True, page_range="1-2", convert=fake_convert()
    )
    # The selection is what gets examined, not the whole file.
    assert text_layer["asked"] == (pdf, "1-2")
    extraction = bundle.read_manifest()["extraction"]
    assert extraction["pages_without_text_layer"] == []
    assert extraction["pages_examined"] == 2
    assert extraction["pages_read_by_ocr"] == []
    assert "no text layer" not in capsys.readouterr().out


def test_a_page_with_no_text_layer_is_read_by_the_models_and_recorded(
    bundle, pdf, pandoc, device, text_layer, capsys
):
    """The measured failure this guards: an image-only page came back as a
    picture, no text, and a completed stage."""
    text_layer["missing"] = [2]
    convert = fake_convert()
    docling_parser.extract_pdf(bundle, pdf, pandoc=pandoc, ocr=True, convert=convert)
    assert convert.calls[0]["ocr"] is True
    assert SCANNED_PAGES.format(count=1, total=2) in capsys.readouterr().out
    extraction = bundle.read_manifest()["extraction"]
    assert extraction["pages_without_text_layer"] == [2]
    assert extraction["pages_read_by_ocr"] == [2]
    assert extraction["ocr"] is True
    job = json.loads(bundle.work_file(EXTRACTION_JOB).read_text(encoding="utf-8"))
    assert job["ocr"] is True


def test_a_page_with_no_text_layer_is_refused_without_the_models(
    bundle, pdf, pandoc, device, text_layer
):
    text_layer["missing"] = [2]
    convert = fake_convert()
    with pytest.raises(PipelineError) as refused:
        docling_parser.extract_pdf(bundle, pdf, pandoc=pandoc, convert=convert)
    assert refused.value.detail == OCR_REQUIRED.format(count=1, total=2, pages="2")
    # Refused before anything was paid for.
    assert convert.calls == []
    assert bundle.stage_status("extract") == "failed"


def test_a_plain_run_reads_without_ocr_and_says_so(bundle, pdf, pandoc, device, capsys):
    # PIN (owner 260921, docs/260921-plan-PDF_DOCLING_ONLY_AND_INSTALL_ROUTES.md):
    # OCR is not the quality boundary. On born-digital PDFs it changed
    # nothing measurable and cost 4.6x the time, so it is off by default and
    # layout and table detection run either way.
    convert = fake_convert()
    docling_parser.extract_pdf(bundle, pdf, pandoc=pandoc, convert=convert)
    assert convert.calls[0]["ocr"] is False
    out = capsys.readouterr().out
    assert "Extracting PDF: 2 pages, layout and table models on mps, 0s" in out
    extraction = bundle.read_manifest()["extraction"]
    assert extraction["ocr"] is False
    assert extraction["pages_read_by_ocr"] == []
    assert extraction["device"] == "mps"


def test_scanned_pages_without_named_languages_are_read_with_the_default_and_say_so(
    bundle, pdf, pandoc, device, text_layer, capsys
):
    # PIN (owner ask 260921, docs/260921-feat-PDF_OCR_LANG_FLAG.md): the
    # models' default reads Latin scripts only, so a scan in another script
    # came back empty without a word about why (measured: a Chinese
    # two-page scan, OCR_EMPTY). The default stands; the operator is told
    # it is in force and where the switch is.
    text_layer["missing"] = [2]
    docling_parser.extract_pdf(
        bundle, pdf, pandoc=pandoc, ocr=True, convert=fake_convert()
    )
    assert OCR_LANG_DEFAULT in capsys.readouterr().out
    manifest = bundle.read_manifest()
    assert manifest["extraction"]["ocr_lang"] is None
    assert OCR_LANG_DEFAULT in manifest["limitations"]


def test_named_languages_reach_the_parser_and_the_manifest(
    bundle, pdf, pandoc, device, text_layer, capsys
):
    text_layer["missing"] = [2]
    convert = fake_convert()
    docling_parser.extract_pdf(
        bundle, pdf, pandoc=pandoc, ocr=True, ocr_lang=" zh , en", convert=convert
    )
    assert convert.calls[0]["languages"] == ["zh", "en"]
    assert OCR_LANG_DEFAULT not in capsys.readouterr().out
    manifest = bundle.read_manifest()
    assert manifest["extraction"]["ocr_lang"] == ["zh", "en"]
    assert OCR_LANG_DEFAULT not in manifest["limitations"]
    job = json.loads(bundle.work_file(EXTRACTION_JOB).read_text(encoding="utf-8"))
    assert job["ocr_lang"] == ["zh", "en"]


def test_a_typed_document_says_nothing_about_ocr_languages(
    bundle, pdf, pandoc, device, text_layer, capsys
):
    # every page has a text layer: the models lay out, read nothing, and
    # the language line would be noise
    docling_parser.extract_pdf(
        bundle, pdf, pandoc=pandoc, ocr=True, convert=fake_convert()
    )
    assert OCR_LANG_DEFAULT not in capsys.readouterr().out
    assert OCR_LANG_DEFAULT not in bundle.read_manifest()["limitations"]


def test_an_empty_language_list_is_refused_before_the_models_start(
    bundle, pdf, pandoc, device, text_layer
):
    convert = fake_convert()
    with pytest.raises(PipelineError) as refused:
        docling_parser.extract_pdf(
            bundle, pdf, pandoc=pandoc, ocr=True, ocr_lang=",", convert=convert
        )
    assert "--ocr-lang needs at least one language code" in refused.value.detail
    assert convert.calls == []


def test_ocr_that_returned_nothing_is_a_failure_not_an_empty_book(
    bundle, pdf, pandoc, device, text_layer
):
    text_layer["missing"] = [1]
    text_layer["examined"] = 1
    with pytest.raises(PipelineError) as failed:
        docling_parser.extract_pdf(
            bundle,
            pdf,
            pandoc=pandoc,
            ocr=True,
            convert=fake_convert(markdown="![](images/imageFile1.png)\n"),
        )
    assert failed.value.detail == OCR_EMPTY
    assert bundle.stage_status("extract") == "failed"
    assert not bundle.source.exists()


def test_one_page_the_models_could_not_read_is_reported_not_hidden(
    bundle, pdf, pandoc, device, text_layer, capsys
):
    """A plate with no words on it is a legitimate empty page, so the run
    stands -- but it is said out loud and recorded."""
    text_layer["missing"] = [1, 2]
    docling_parser.extract_pdf(
        bundle,
        pdf,
        pandoc=pandoc,
        ocr=True,
        convert=fake_convert(
            markdown=(
                f"# Scanned Chapter\n\nWords the models read.\n"
                f"{BREAK}\n![](images/imageFile1.png)\n"
            )
        ),
    )
    assert bundle.stage_status("extract") == "completed"
    warning = OCR_EMPTY_PAGES.format(pages="2")
    assert warning in capsys.readouterr().out
    assert warning in bundle.read_manifest()["limitations"]


def test_a_page_with_more_prose_than_a_page_holds_is_called_out(
    bundle, pdf, pandoc, device, capsys
):
    flood = "# Title\n\n" + ("(a) Previous methods\n\n" * 700)
    flood += f"{BREAK}\nAn ordinary page.\n"
    docling_parser.extract_pdf(
        bundle, pdf, pandoc=pandoc, ocr=True, convert=fake_convert(markdown=flood)
    )
    out = capsys.readouterr().out
    assert "Warning: page 1 extracted" in out
    assert "page 2 extracted" not in out
    limitations = bundle.read_manifest()["limitations"]
    assert any(line.startswith("Warning: page 1 extracted") for line in limitations)


def test_dense_pages_counts_prose_not_markers_or_pictures():
    text = (
        "<!-- page 1 -->\n\n" + "x" * 30 + "\n\n"
        "<!-- page 2 -->\n\n![](images/imageFile1.png)\n\n" + "y" * 10 + "\n"
    )
    assert pdf_common.dense_pages(text, limit=20) == [(1, 30)]
    assert pdf_common.dense_pages(text, limit=40) == []
    assert PAGE_TOO_DENSE.format(page=1, chars=30).startswith("Warning: page 1")


def test_the_pages_a_conversion_left_blank_are_named():
    blank, any_prose = pdf_common.blank_pages(
        "<!-- page 1 -->\n\n# Heading\n\nProse.\n\n"
        "<!-- page 2 -->\n\n![](images/imageFile1.png)\n\n"
        "<!-- page 3 -->\n\nMore prose.\n"
    )
    assert (blank, any_prose) == ([2], True)


def test_a_conversion_of_nothing_but_pictures_has_no_prose_anywhere():
    blank, any_prose = pdf_common.blank_pages(
        "<!-- page 1 -->\n\n![](images/imageFile1.png)\n\n"
        "<!-- page 2 -->\n\n![](images/imageFile2.png)\n"
    )
    assert (blank, any_prose) == ([1, 2], False)


# --------------------------------------------------------------------------
# Parser dispatch
# --------------------------------------------------------------------------
def test_the_harness_and_the_adapter_name_the_same_parser():
    """The harness and the stage spell the name rather than importing it.

    `stages.py` must not import the adapter for a Markdown run, so the
    string is written twice; this is what holds the two copies equal.
    """
    harness = load_harness()
    assert harness.PDF_PARSER == docling_parser.PARSER == "docling"
    assert stages.PDF_PARSER == docling_parser.PARSER


def test_a_pdf_goes_to_docling_with_no_flag_at_all(tmp_path, pandoc, pdf, monkeypatch):
    """The default and the only route: a PDF is read locally, by docling.

    No parser is chosen and none can be. `--device` left out means the
    adapter's own `auto`, and nothing else is consulted to decide.
    """
    seen = {}

    def record(bundle, path, *, pandoc, device="auto", page_range=None, **kwargs):
        seen.update(device=device, page_range=page_range, path=Path(path))
        raise PipelineError("stopped before the models", stage="extract")

    monkeypatch.setattr(docling_parser, "extract_pdf", record)
    harness = load_harness()
    code = harness.main(
        ["--pandoc", pandoc, "extract", str(pdf), "--output", str(tmp_path / "b")]
    )
    assert code == 1
    assert seen == {"device": "auto", "page_range": None, "path": pdf}


def test_the_device_and_the_page_selection_reach_the_parser(
    tmp_path, pandoc, pdf, monkeypatch
):
    seen = {}

    def record(bundle, path, *, pandoc, device="auto", page_range=None, **kwargs):
        seen.update(device=device, page_range=page_range)
        raise PipelineError("stopped before the models", stage="extract")

    monkeypatch.setattr(docling_parser, "extract_pdf", record)
    harness = load_harness()
    code = harness.main(
        [
            "--pandoc",
            pandoc,
            "extract",
            str(pdf),
            "--output",
            str(tmp_path / "b"),
            "--device",
            "cpu",
            "--pages",
            "1-4",
        ]
    )
    assert code == 1
    assert seen == {"device": "cpu", "page_range": "1-4"}


HEAVY = ("docling", "docling_core", "torch", "pypdfium2")


def _purge(names):
    """Drop `names` and everything under them from `sys.modules`, reversibly."""
    saved = {
        name: module
        for name, module in sys.modules.items()
        if name in names or any(name.startswith(f"{n}.") for n in names)
    }
    for name in saved:
        del sys.modules[name]
    return saved


def test_loading_the_front_door_imports_neither_docling_nor_torch():
    """The base install stays light, enforced instead of intended.

    Nothing on the way in may import the PDF parser at module level:
    docling and torch are 200 MB to 1.8 GB of wheels plus ~500 MB of
    models on first run, and the EPUB-only majority never opens a PDF.
    Both `stages.py` and `cli.py` import the adapter inside the function
    that needs it, for exactly this reason, and this is the only cheap
    guard on that promise.

    It is meant to fail: move `from .docling_parser import extract_pdf` to
    the top of `stages.py`, or import torch anywhere either of these
    modules reaches, and this goes red. That is its whole job -- do not
    relax it into a warning or scope it to one module name.

    This half needs no Pandoc, so it runs on a base install too, where the
    end-to-end half below skips. Purging `book_maker` as well is what
    makes the import real rather than a lookup of what a previous test
    already loaded.
    """
    import importlib

    saved = _purge((*HEAVY, "book_maker"))
    try:
        for name in (
            "book_maker.cli",
            "book_maker.pipeline.stages",
            "book_maker.pipeline.to_epub",
            "book_maker.pipeline.translate",
            "book_maker.pipeline.epub_export",
            "book_maker.pipeline.importer",
        ):
            importlib.import_module(name)
        assert "torch" not in sys.modules
        assert "docling" not in sys.modules
        assert not set(HEAVY) & set(sys.modules)
    finally:
        _purge((*HEAVY, "book_maker"))
        sys.modules.update(saved)


def test_a_non_pdf_run_imports_neither_docling_nor_torch(tmp_path, pandoc, monkeypatch):
    """The same promise, over a whole run through the front door.

    The structural half above cannot see an import that happens while the
    run is going; this one does, because the run finishes and the modules
    are still absent afterwards.

    A Markdown book goes in through the same front door as a PDF and must
    not pay for the PDF parser: docling and torch are 200 MB to 1.8 GB of
    wheels plus ~500 MB of models, and the EPUB-only majority never opens
    a PDF. This assertion is the only cheap guard on that promise, and it
    is meant to fail: add a module-level `import docling` (or anything
    that pulls torch in) to `stages.py`, `cli.py`, `bundle.py` or anything
    either of them imports, and this test goes red. That is its whole job
    -- do not relax it into a warning or scope it to one module name.

    Generalised 260921 from the pin that a local run loaded no cloud
    adapter and read no API key; the cloud adapter is gone, the promise it
    was guarding is not.
    """
    register_fake_format(monkeypatch)
    book = tmp_path / "book.md"
    book.write_text("# Title\n\nProse to translate.\n", encoding="utf-8")

    def refuse(*args, **kwargs):
        raise AssertionError("a Markdown import reached the PDF parser")

    monkeypatch.setattr(docling_parser, "extract_pdf", refuse)
    for name in list(sys.modules):
        if name in HEAVY or any(name.startswith(f"{h}.") for h in HEAVY):
            monkeypatch.delitem(sys.modules, name)

    harness = load_harness()
    code = harness.main(
        [
            "--pandoc",
            pandoc,
            "run",
            str(book),
            "--output",
            str(tmp_path / "bundle"),
            "--",
            "--api_format",
            "faketest",
            "--language",
            "zh-hans",
        ]
    )
    assert code == 0
    assert "torch" not in sys.modules
    assert "docling" not in sys.modules
    assert not set(HEAVY) & set(sys.modules)


@pytest.mark.parametrize("command", ["extract", "run"])
def test_the_parser_selector_is_gone_and_is_refused(tmp_path, pdf, command, capsys):
    """An unshipped flag gets no compatibility alias, only a refusal.

    PIN (owner 260921,
    docs/260921-plan-PDF_DOCLING_ONLY_AND_INSTALL_ROUTES.md): a two-tier
    design that would have kept a second, zero-model parser behind
    `--pdf-parser` was considered and rejected the same day -- the JRE it
    needed is a system install rather than a pip extra, the speed saving
    was under three seconds on a five-page cut, and the quality gap was
    not trivial (cell recall 0.519 vs 0.995, 60 of 134 headings, 51.4
    debris translation units per paper vs 4.8). There is one parser, so
    the flag has nothing to select. Do not invert or delete this pin.

    Deliberately not taking the `pandoc` fixture: argparse refuses the
    unknown flag before anything looks for Pandoc, so this pin runs on a
    base install too rather than skipping where it is least watched.
    """
    harness = load_harness()
    with pytest.raises(SystemExit) as exited:
        harness.main(
            [
                "--pandoc",
                "pandoc",
                command,
                str(pdf),
                "--output",
                str(tmp_path / "b"),
                "--pdf-parser",
                "docling",
            ]
        )
    assert exited.value.code == 2
    assert "--pdf-parser" in capsys.readouterr().err


@pytest.mark.parametrize(
    "given",
    [["--device", "cpu"], ["--pdf-ocr"], ["--pages", "1-2"], ["--ocr-lang", "zh"]],
    ids=["--device", "--pdf-ocr", "--pages", "--ocr-lang"],
)
def test_a_pdf_only_option_on_markdown_is_refused_not_ignored(
    tmp_path, pandoc, given, capsys, monkeypatch
):
    """Markdown reads no PDF, so none of these could have been honoured.

    The translation options are real ones, so the refusal that arrives is
    the one under test and not `run` rejecting the command line first.
    """
    register_fake_format(monkeypatch)
    book = tmp_path / "book.md"
    book.write_text("# Title\n\nProse.\n", encoding="utf-8")

    def refuse(*args, **kwargs):
        raise AssertionError("a Markdown input reached the PDF parser")

    monkeypatch.setattr(docling_parser, "extract_pdf", refuse)
    harness = load_harness()
    code = harness.main(
        [
            "--pandoc",
            pandoc,
            "run",
            str(book),
            "--output",
            str(tmp_path / "b"),
            *given,
            "--",
            "--api_format",
            "faketest",
            "--language",
            "zh-hans",
        ]
    )
    assert code == 1
    assert PDF_OPTIONS_INERT in capsys.readouterr().out


# --------------------------------------------------------------------------
# Resume
# --------------------------------------------------------------------------
def test_a_finished_run_does_not_extract_again_or_clobber_an_edited_source(
    tmp_path, pandoc, pdf, device, monkeypatch
):
    register_fake_format(monkeypatch)
    bundle = Bundle(tmp_path / "bundle").create()
    docling_parser.extract_pdf(
        bundle, pdf, pandoc=pandoc, ocr=True, convert=fake_convert()
    )
    edited = bundle.source.read_text(encoding="utf-8").replace(
        "Chapter One", "Chapter One, corrected"
    )
    bundle.source.write_text(edited, encoding="utf-8")

    def refuse(*args, **kwargs):
        raise AssertionError("a finished extraction was bought again")

    monkeypatch.setattr(docling_parser, "extract_pdf", refuse)
    harness = load_harness()
    code = harness.main(
        [
            "--pandoc",
            pandoc,
            "run",
            str(pdf),
            "--output",
            str(bundle.root),
            "--",
            "--api_format",
            "faketest",
            "--language",
            "zh-hans",
        ]
    )
    assert code == 0
    assert "Chapter One, corrected" in bundle.source.read_text(encoding="utf-8")
    assert "Chapter One, corrected" in bundle.bilingual_markdown.read_text(
        encoding="utf-8"
    )
    assert FakeTranslator.instances, "the edited source was never translated"


def test_a_rerun_with_a_different_page_selection_extracts_again(
    tmp_path, pandoc, pdf, device
):
    bundle = Bundle(tmp_path / "bundle").create()
    docling_parser.extract_pdf(
        bundle, pdf, pandoc=pandoc, ocr=True, page_range="1-2", convert=fake_convert()
    )
    harness = load_harness()
    assert harness.already_prepared(bundle, pdf, "docling", "1-2")
    assert not harness.already_prepared(bundle, pdf, "docling", "3-4")
    # A bundle another parser wrote is not this parser's to resume; the
    # retired Java route left bundles that say so in the manifest.
    assert not harness.already_prepared(bundle, pdf, "opendataloader", "1-2")


def test_a_rerun_with_other_ocr_languages_reads_a_scan_again_but_not_a_typed_document(
    tmp_path, pandoc, pdf, device, text_layer
):
    # a scan: the languages decided what the models read
    text_layer["missing"] = [1, 2]
    scan = Bundle(tmp_path / "scan").create()
    docling_parser.extract_pdf(
        scan, pdf, pandoc=pandoc, ocr=True, ocr_lang="zh,en", convert=fake_convert()
    )
    assert stages.already_prepared(scan, pdf, "docling", None, ["zh", "en"])
    assert not stages.already_prepared(scan, pdf, "docling", None, ["ja"])
    assert not stages.already_prepared(scan, pdf, "docling", None, None)

    # a typed document: the models read no page, so the languages changed
    # nothing and the extraction stands whatever is typed next time
    text_layer["missing"] = []
    typed = Bundle(tmp_path / "typed").create()
    docling_parser.extract_pdf(
        typed, pdf, pandoc=pandoc, ocr=True, ocr_lang="zh,en", convert=fake_convert()
    )
    assert stages.already_prepared(typed, pdf, "docling", None, ["ja"])
    assert stages.already_prepared(typed, pdf, "docling", None, None)


def test_a_rerun_that_changes_the_formula_picture_setting_extracts_again(
    tmp_path, pandoc, pdf, device
):
    # PIN (lead 260922, docs/260922-feat-PDF_FORMULA_IMAGES.md; Codex found
    # the gap): a bundle made with --no-formula-images has no pictures to
    # reuse, and one made with them is not what a rerun asking for none
    # wants. The setting is recorded with the extraction and a rerun that
    # changes it extracts again. A manifest from before the setting existed
    # counts as the default, so an older bundle is not thrown away.
    import json

    bundle = Bundle(tmp_path / "bundle").create()
    docling_parser.extract_pdf(
        bundle,
        pdf,
        pandoc=pandoc,
        ocr=True,
        formula_images=False,
        convert=fake_convert(),
    )
    assert bundle.read_manifest()["extraction"]["formula_images"] is False
    assert stages.already_prepared(bundle, pdf, "docling", None, formula_images=False)
    assert not stages.already_prepared(bundle, pdf, "docling", None)

    manifest = bundle.read_manifest()
    del manifest["extraction"]["formula_images"]
    bundle.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert stages.already_prepared(bundle, pdf, "docling", None)
    assert not stages.already_prepared(
        bundle, pdf, "docling", None, formula_images=False
    )


def test_the_stage_hands_the_extraction_the_formula_setting(
    tmp_path, pdf, pandoc, device, monkeypatch
):
    convert = fake_convert()
    real = docling_parser.extract_pdf
    monkeypatch.setattr(
        docling_parser,
        "extract_pdf",
        lambda *a, **kw: real(*a, convert=convert, **kw),
    )
    bundle = Bundle(tmp_path / "b").create()
    stages.prepare(
        bundle, pdf, pandoc=pandoc, ocr=True, formula_images=False, progress=False
    )
    assert convert.calls[0]["formulas"] is False
    assert bundle.read_manifest()["extraction"]["formula_images"] is False
    # And the rerun that asks for pictures is not answered from the bundle.
    stages.prepare(bundle, pdf, pandoc=pandoc, ocr=True, progress=False)
    assert len(convert.calls) == 2 and convert.calls[1]["formulas"] is True


def test_what_the_converter_says_about_formulas_reaches_the_terminal_and_the_manifest(
    tmp_path, pdf, pandoc, device, capsys
):
    from book_maker.pipeline.messages import FORMULA_IMAGES, FORMULA_REGION_OVERSIZE

    warning = FORMULA_REGION_OVERSIZE.format(page=2, share=91)
    inner = fake_convert()  # writes the picture CONVERTED refers to

    def convert(pdf_path, **kwargs):
        return inner(pdf_path, **kwargs), 3, [warning]

    bundle = Bundle(tmp_path / "b").create()
    docling_parser.extract_pdf(bundle, pdf, pandoc=pandoc, ocr=True, convert=convert)
    out = capsys.readouterr().out
    assert warning in out
    assert FORMULA_IMAGES.format(count=3) in out
    manifest = bundle.read_manifest()
    assert warning in manifest["limitations"]
    assert manifest["extraction"]["formula_images"] is True
    assert manifest["extraction"]["formula_image_count"] == 3


def test_the_stage_hands_the_extraction_the_languages_it_parsed_once(
    tmp_path, pdf, pandoc, device, text_layer, monkeypatch
):
    # PIN (lead, 260921, docs/260921-feat-PDF_OCR_LANG_FLAG.md): the stage
    # splits the flag and the extraction must not split the list again --
    # the models were once asked for the code `['ch_sim'` and refused
    # every page.
    text_layer["missing"] = [1, 2]
    convert = fake_convert()
    real = docling_parser.extract_pdf
    monkeypatch.setattr(
        docling_parser,
        "extract_pdf",
        lambda *a, **kw: real(*a, convert=convert, **kw),
    )
    stages.prepare(
        Bundle(tmp_path / "b").create(),
        pdf,
        pandoc=pandoc,
        ocr=True,
        ocr_lang="ch_sim, en",
        progress=False,
    )
    assert convert.calls[0]["languages"] == ["ch_sim", "en"]


# --------------------------------------------------------------------------
# The text layer, read from real PDFs
# --------------------------------------------------------------------------
MISSING_PDFIUM = "pypdfium2 is not installed (pip install pypdfium2)"


def pdfium_or_skip():
    try:
        import pypdfium2
    except ImportError:
        pytest.skip(MISSING_PDFIUM)
    # An uninstall can leave an importable but empty package behind.
    if not hasattr(pypdfium2, "PdfDocument"):
        pytest.skip(MISSING_PDFIUM)


def test_the_real_text_layer_is_read_page_by_page(tmp_path):
    pdfium_or_skip()
    path = write_pdf(
        tmp_path / "mixed.pdf", ["Page one is typed.", None, "Page three is typed."]
    )
    assert real_text_layer_report(path) == ([2], 3)


def test_a_scan_with_a_stamped_page_number_is_seen_as_scanned(tmp_path):
    pdfium_or_skip()
    path = write_pdf(
        tmp_path / "stamped.pdf", ["17", "Page two is typed and says so."], scan=(1,)
    )
    assert real_text_layer_report(path) == ([1], 2)


def test_a_typed_page_with_a_picture_on_it_is_not_a_scan(tmp_path):
    pdfium_or_skip()
    typed = "\n".join(["A typed page that also carries a full-page picture."] * 5)
    path = write_pdf(tmp_path / "typed.pdf", [typed], scan=(1,))
    assert real_text_layer_report(path) == ([], 1)


def test_a_document_with_no_text_layer_at_all_is_seen_as_scanned(tmp_path):
    pdfium_or_skip()
    path = write_pdf(tmp_path / "scan.pdf", [None, None])
    assert real_text_layer_report(path) == ([1, 2], 2)


def test_only_the_selected_pages_are_examined(tmp_path):
    pdfium_or_skip()
    path = write_pdf(tmp_path / "mixed.pdf", ["Typed.", None, "Typed.", None])
    assert real_text_layer_report(path, "3-4") == ([4], 2)
    assert real_text_layer_report(path, "1") == ([], 1)


def test_a_scan_drawn_from_inside_a_form_is_still_a_scan(tmp_path):
    # Codex re-verify 260920: the page image wrapped in a Form XObject
    # counted as no picture at all, and the stamped number made the page
    # "typed".
    pdfium_or_skip()
    path = write_pdf(
        tmp_path / "nested.pdf",
        ["17", "Page two is typed and says so."],
        scan=(1,),
        scan_nested=True,
    )
    assert real_text_layer_report(path) == ([1], 2)


def test_a_file_that_is_not_a_pdf_is_refused_before_the_models_start(tmp_path):
    pdfium_or_skip()
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.7\n%not really\n")
    with pytest.raises(PipelineError) as refused:
        real_text_layer_report(path)
    assert "broken.pdf" in refused.value.detail
