"""`make_book.py --to-epub`: the route, the copy, and the progress line.

Everything here runs in process and none of it starts Java, a model or
Pandoc: what is under test is which code the CLI hands a PDF to, what it
leaves beside that PDF afterwards, and what an operator sees while the slow
stage is running. The stages themselves have their own tests
(`test_opendataloader_adapter.py`, `test_bundle_pipeline.py`).
"""

import io
import sys
from pathlib import Path

import pytest

from book_maker import cli
from book_maker.pipeline import messages, opendataloader, to_epub
from book_maker.pipeline.errors import PipelineError
from book_maker.pipeline.progress import ProgressLine

TRANSLATION = ["--api_format", "google", "--language", "zh-hans"]


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "book.pdf"
    path.write_bytes(b"%PDF-1.7\n%fake\n")
    return path


def stages(recorder, *, export=True, fail=None):
    """Fake extract/translate/export that only record being called."""

    def prepare_stage(bundle, source, *, pandoc, device=None, progress=True):
        recorder.append(("extract", device, progress))
        bundle.source.write_text("# Title\n\nProse.\n", encoding="utf-8")

    def translate_stage(bundle, options, *, pandoc):
        recorder.append(("translate", tuple(options)))
        bundle.bilingual_markdown.write_text("# Title\n", encoding="utf-8")

    def export_stage(bundle, *, pandoc):
        recorder.append(("export",))
        if fail:
            raise PipelineError(fail, stage="export")
        bundle.epub.write_bytes(b"PK\x03\x04 not really a zip")
        return bundle.epub

    return {
        "prepare_stage": prepare_stage,
        "translate_stage": translate_stage,
        "export_stage": export_stage,
    }


@pytest.fixture
def no_pandoc_lookup(monkeypatch):
    """Pandoc resolves to a name; nothing here ever runs it."""
    monkeypatch.setattr(to_epub, "find_pandoc", lambda explicit=None: "pandoc")


# --------------------------------------------------------------------------
# The route
# --------------------------------------------------------------------------
class TestRouting:
    def test_a_pdf_with_the_flag_runs_the_pipeline_instead_of_the_loader(
        self, pdf, monkeypatch
    ):
        seen = {}

        def fake(path, argv, *, no_gpu, quiet):
            seen.update(path=Path(path), argv=list(argv), no_gpu=no_gpu, quiet=quiet)

        monkeypatch.setattr(to_epub, "pdf_to_epub", fake)
        monkeypatch.setitem(
            cli.BOOK_LOADER_DICT,
            "pdf",
            lambda *a, **k: pytest.fail("the legacy PDF loader was built"),
        )

        cli.main(["--book_name", str(pdf), "--to-epub", *TRANSLATION])

        assert seen["path"] == pdf
        assert seen["no_gpu"] is False
        assert seen["quiet"] is False
        # every other option is the translation's, and is handed on as typed
        assert seen["argv"] == ["--book_name", str(pdf), "--to-epub", *TRANSLATION]

    def test_no_gpu_and_quiet_reach_the_pipeline(self, pdf, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            to_epub, "pdf_to_epub", lambda path, argv, **kwargs: seen.update(kwargs)
        )
        cli.main(
            ["--book_name", str(pdf), "--to-epub", "--no-gpu", "--quiet", *TRANSLATION]
        )
        assert seen == {"no_gpu": True, "quiet": True}

    def test_a_pdf_without_the_flag_still_takes_the_legacy_route(
        self, pdf, monkeypatch
    ):
        monkeypatch.setattr(
            to_epub,
            "pdf_to_epub",
            lambda *a, **k: pytest.fail("--to-epub was not asked for"),
        )

        class ReachedTheLegacyLoader(Exception):
            pass

        def loader(*args, **kwargs):
            raise ReachedTheLegacyLoader

        monkeypatch.setitem(cli.BOOK_LOADER_DICT, "pdf", loader)
        with pytest.raises(ReachedTheLegacyLoader):
            cli.main(["--book_name", str(pdf), *TRANSLATION])

    def test_a_pipeline_failure_is_one_line_and_exit_1(self, pdf, monkeypatch, capsys):
        def fail(*args, **kwargs):
            raise PipelineError("the backend said no", stage="extract")

        monkeypatch.setattr(to_epub, "pdf_to_epub", fail)
        with pytest.raises(SystemExit) as exited:
            cli.main(["--book_name", str(pdf), "--to-epub", *TRANSLATION])
        assert exited.value.code == 1
        out = " ".join(capsys.readouterr().out.split())
        assert "extract failed: the backend said no" in out
        assert "Traceback" not in out

    def test_a_missing_pandoc_names_the_path_not_a_flag_that_does_not_exist(
        self, pdf, monkeypatch, capsys
    ):
        # The main CLI has no --pandoc option, so the shared refusal that
        # offers one would send its operator looking for nothing.
        def missing(explicit=None):
            raise PipelineError(messages.PANDOC_REQUIRED)

        monkeypatch.setattr(to_epub, "find_pandoc", missing)
        with pytest.raises(SystemExit) as exited:
            cli.main(["--book_name", str(pdf), "--to-epub", *TRANSLATION])
        assert exited.value.code == 1
        out = capsys.readouterr().out
        assert messages.PANDOC_ON_PATH in " ".join(out.split())
        assert "--pandoc PATH" not in out


# --------------------------------------------------------------------------
# What is left on disk
# --------------------------------------------------------------------------
class TestTheStages:
    def test_they_run_in_order_and_the_epub_lands_beside_the_pdf(
        self, pdf, no_pandoc_lookup, capsys
    ):
        order = []
        result = to_epub.pdf_to_epub(pdf, TRANSLATION, **stages(order))

        assert [step[0] for step in order] == ["extract", "translate", "export"]
        assert result == pdf.parent / "book_bilingual.epub"
        assert result.is_file()
        # the bundle stays, for editing and for the next run to resume from
        bundle = pdf.parent / "book_book"
        assert (bundle / "source.md").is_file()
        assert (bundle / "book_bilingual.epub").is_file()
        out = capsys.readouterr().out
        assert str(bundle) in out
        assert str(result) in out

    def test_the_translation_stage_gets_the_options_the_route_does_not_own(
        self, pdf, no_pandoc_lookup
    ):
        order = []
        to_epub.pdf_to_epub(
            pdf,
            ["--book_name", str(pdf), "--to-epub", "--no-gpu", *TRANSLATION],
            **stages(order),
        )
        translated = dict(enumerate(order))[1]
        assert list(translated[1]) == TRANSLATION

    def test_no_gpu_forces_cpu_and_the_default_detects(self, pdf, no_pandoc_lookup):
        order = []
        to_epub.pdf_to_epub(pdf, TRANSLATION, **stages(order))
        assert order[0] == ("extract", "auto", True)

        order = []
        to_epub.pdf_to_epub(pdf, TRANSLATION, no_gpu=True, quiet=True, **stages(order))
        assert order[0] == ("extract", "cpu", False)

    def test_a_failed_export_copies_nothing(self, pdf, no_pandoc_lookup):
        order = []
        with pytest.raises(PipelineError) as refused:
            to_epub.pdf_to_epub(
                pdf, TRANSLATION, **stages(order, fail="navigation is invalid")
            )
        assert "navigation is invalid" in refused.value.detail
        # the name a reader opens must never hold a half-built book
        assert not (pdf.parent / "book_bilingual.epub").exists()

    def test_the_translation_options_are_parsed_before_anything_is_extracted(
        self, pdf, no_pandoc_lookup
    ):
        order = []
        with pytest.raises(PipelineError):
            to_epub.pdf_to_epub(pdf, ["--not-an-option"], **stages(order))
        assert order == []


@pytest.mark.parametrize(
    "argv,kept",
    [
        (["--to-epub", "--model", "m"], ["--model", "m"]),
        (["--no-gpu", "--key", "k"], ["--key", "k"]),
        (["--book_name", "b.pdf", "--test"], ["--test"]),
        (["--book_name=b.pdf", "--test"], ["--test"]),
        # a value that happens to look like a flag this route owns is still
        # the previous option's value
        (["--language", "zh-hans", "--to-epub"], ["--language", "zh-hans"]),
    ],
)
def test_only_the_route_s_own_options_are_stripped(argv, kept):
    assert to_epub.translation_argv(argv) == kept


# --------------------------------------------------------------------------
# The progress line
# --------------------------------------------------------------------------
class Stream(io.StringIO):
    def __init__(self, tty):
        super().__init__()
        self.tty = tty

    def isatty(self):
        return self.tty


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class TestProgressLine:
    def line(self, tty, **kwargs):
        stream, clock = Stream(tty), Clock()
        return (
            ProgressLine(
                "Extracting PDF: 3 pages, OCR on cpu",
                stream=stream,
                clock=clock,
                **kwargs,
            ),
            stream,
            clock,
        )

    def test_a_terminal_gets_one_line_rewritten_in_place(self):
        line, stream, clock = self.line(True)
        line.start()
        assert stream.getvalue() == "\rExtracting PDF: 3 pages, OCR on cpu, 0s"

        clock.now = 35.4
        line.note("Processing document book.pdf")
        line.tick()
        written = stream.getvalue()
        assert written.count("\n") == 0
        assert written.endswith(
            "\rExtracting PDF: 3 pages, OCR on cpu, 35s - Processing document book.pdf"
        )

    def test_a_shorter_line_covers_the_longer_one_it_replaces(self):
        line, stream, clock = self.line(True)
        line.start()
        detail = "a very long thing the backend said about loading its models"
        line.note(detail)
        clock.now = 5
        line.tick()
        long_line = stream.getvalue().rsplit("\r", 1)[-1]
        line.detail = None
        clock.now = 10
        line.tick()
        short = "Extracting PDF: 3 pages, OCR on cpu, 10s"
        # the padding is what stops half of the previous line surviving
        assert stream.getvalue().endswith(short + " " * (len(long_line) - len(short)))

    def test_a_log_gets_a_whole_line_every_ten_seconds(self):
        line, stream, clock = self.line(False)
        line.start()
        clock.now = 3
        line.tick()  # too soon: a log is kept forever
        clock.now = 10
        line.note("Finished converting document in 22.22 sec")
        line.tick()
        lines = stream.getvalue().splitlines()
        assert lines == [
            "Extracting PDF: 3 pages, OCR on cpu, 0s",
            "Extracting PDF: 3 pages, OCR on cpu, 10s - Finished converting "
            "document in 22.22 sec",
        ]
        assert "\r" not in stream.getvalue()

    def test_finishing_clears_the_terminal_line_and_leaves_the_record(self):
        line, stream, clock = self.line(True)
        line.start()
        clock.now = 29
        line.finish("PDF extracted: 3 pages, OCR on cpu, 29s.")
        written = stream.getvalue()
        assert written.endswith("PDF extracted: 3 pages, OCR on cpu, 29s.\n")
        assert "\r" + " " * len("Extracting PDF: 3 pages, OCR on cpu, 0s") in written

    def test_a_quiet_run_says_nothing_at_all(self):
        line, stream, clock = self.line(True, enabled=False)
        line.start()
        clock.now = 60
        line.note("Processing document book.pdf")
        line.tick()
        line.finish("PDF extracted: 3 pages, OCR on cpu, 60s.")
        assert stream.getvalue() == ""

    def test_the_detail_is_bounded(self):
        line, stream, clock = self.line(False)
        line.start()
        line.note("x" * 500)
        assert len(line.detail) == 80


class TestWhatCountsAsProgress:
    """Measured from a real conversion; see `backend_note`'s docstring."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            (
                "2026-09-20 16:18:33,287 - INFO - Processing document book.pdf",
                "Processing document book.pdf",
            ),
            ("INFO: Number of pages: 3", "Number of pages: 3"),
            (
                "INFO: Processing 3 pages via docling-fast backend",
                "Processing 3 pages via docling-fast backend",
            ),
            (
                "INFO:     Application startup complete.",
                "Application startup complete.",
            ),
            # java.util.logging's own header line, above the message
            ("9月 20, 2026 4:18:25 org.opendataloader.pdf.X processDocument", None),
            # uvicorn's access log: one line per health probe
            ('INFO:     127.0.0.1:57033 - "GET /health HTTP/1.1" 200 OK', None),
            ("", None),
            ("   ", None),
            ("Using a slow image processor as `use_fast` is unset", None),
        ],
    )
    def test_only_a_logged_message_is_shown(self, raw, expected):
        assert opendataloader.backend_note(raw) == expected

    def test_the_converter_s_own_output_reaches_the_line(self):
        said = []
        sink = opendataloader._LineSink(said.append)
        # the runner writes whole lines, one write each
        sink.write("INFO: Number of pages: 3\n")
        # ... but a partial write must not be shown as half a sentence
        sink.write("INFO: Processing 3 pages")
        assert said == ["INFO: Number of pages: 3"]
        sink.write(" via docling-fast backend\n")
        assert said[-1] == "INFO: Processing 3 pages via docling-fast backend"
