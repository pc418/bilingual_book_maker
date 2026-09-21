"""The local parser: which device it runs on, and whose process it owns.

Nothing here downloads a model or converts a real PDF -- those are live
checks recorded in the handback. What is asserted is everything the harness
decides: the device that reaches the backend's command line, the refusals
that must not be silently survivable, and the fact that the child process
this module starts is the only one it ever stops.
"""

import json
import subprocess
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

from book_maker.pipeline import opendataloader  # noqa: E402
from book_maker.pipeline.bundle import EXTRACTION_JOB, Bundle  # noqa: E402
from book_maker.pipeline.epub_export import export_epub  # noqa: E402
from book_maker.pipeline.errors import PipelineError  # noqa: E402
from book_maker.pipeline import pdf_sanitize  # noqa: E402
from book_maker.pipeline.messages import (  # noqa: E402
    BACKEND_FAILED,
    DEVICE_CPU_FALLBACK,
    DEVICE_SELECTED,
    DEVICE_UNAVAILABLE,
    FIGURES_RASTERIZED,
    HIDDEN_TEXT_RASTERIZED,
    JAVA_REQUIRED,
    OCR_EMPTY,
    OCR_EMPTY_PAGES,
    PAGE_TOO_DENSE,
    PDF_OPTIONS_INERT,
    SCANNED_PAGES,
)

# Captured before the autouse fixture below replaces the module attribute,
# so the tests of the real detector get the real detector.
from book_maker.pipeline.opendataloader import (  # noqa: E402
    text_layer_report as real_text_layer_report,
)
from book_maker.pipeline.translate import translate_bundle  # noqa: E402

HARNESS = Path(__file__).resolve().parent.parent / "tools" / "pdf_to_book.py"

CONVERTED = """<!-- page 1 -->

# Chapter One

The first paragraph is ordinary prose that the model will translate.

The second paragraph carries a [link](https://example.com) and `inline code`,
and its translation comes back as two paragraphs.

![](<images/imageFile1.png>)

<!-- page 2 -->

## Notes

A closing paragraph under the second heading.
"""


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
    further down, which skip where the hybrid stack is not installed. Every
    other test here is about what the harness *does* with the answer, so the
    answer is given: by default every page carries text.
    """
    state = {"missing": [], "examined": 2}

    def report(pdf_path, page_range=None):
        state["asked"] = (Path(pdf_path), page_range)
        return list(state["missing"]), state["examined"]

    monkeypatch.setattr(opendataloader, "text_layer_report", report)
    return state


@pytest.fixture(autouse=True)
def sanitizer(monkeypatch):
    """What the hidden-text pass found, under the test's control.

    The real pass needs pypdfium2 and a real PDF (its own tests are further
    down); by default nothing is hidden and the original is what the
    engines read.
    """
    state = {"report": [], "sanitized": None}

    def sanitize(pdf_path, output_dir, page_range=None):
        state["asked"] = (Path(pdf_path), Path(output_dir), page_range)
        if state["sanitized"] is not None:
            copy = Path(output_dir) / "sanitized" / Path(pdf_path).name
            copy.parent.mkdir(parents=True, exist_ok=True)
            copy.write_bytes(state["sanitized"])
            return copy, list(state["report"])
        return None, list(state["report"])

    monkeypatch.setattr(opendataloader, "sanitize_pdf", sanitize)
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


class FakeProcess:
    """Just enough subprocess.Popen for the lifecycle to be observable."""

    def __init__(self, exits_after=None, output="", ignores_terminate=False):
        self.returncode = None
        self._exits_after = exits_after
        self._polls = 0
        self.output = output
        self.waited = False
        self.terminated = False
        self.killed = False
        self._ignores_terminate = ignores_terminate

    def poll(self):
        self._polls += 1
        if self._exits_after is not None and self._polls > self._exits_after:
            self.returncode = 7
        return self.returncode

    def terminate(self):
        self.terminated = True
        if not self._ignores_terminate:
            self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self, timeout=None):
        self.waited = True
        if self.returncode is None:
            import subprocess

            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 0.01)


def backend(device="mps", *, process=None, ready_after=0, timeout=5.0, **kwargs):
    clock = Clock()
    started = []
    probes = []

    def popen(command, **popen_kwargs):
        started.append(command)
        result = process if process is not None else FakeProcess()
        popen_kwargs["stdout"].write(result.output)
        popen_kwargs["stdout"].flush()
        return result

    def probe(url):
        probes.append(url)
        return len(probes) > ready_after

    instance = opendataloader.HybridBackend(
        device,
        readiness_timeout=timeout,
        readiness_interval=0.5,
        popen=popen,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        probe=probe,
        **kwargs,
    )
    instance.started_commands = started
    instance.probed_urls = probes
    return instance


# --------------------------------------------------------------------------
# Device selection
# --------------------------------------------------------------------------
def test_auto_takes_the_accelerator_that_is_there(accelerators):
    accelerators["available"] = ["mps"]
    resolved, message = opendataloader.resolve_device("auto")
    assert resolved == "mps"
    assert message == DEVICE_SELECTED.format(device="mps")


def test_auto_falls_back_to_cpu_and_says_so(accelerators):
    accelerators["available"] = []
    resolved, message = opendataloader.resolve_device("auto")
    assert resolved == "cpu"
    assert message == DEVICE_CPU_FALLBACK


def test_cpu_is_forced_even_when_an_accelerator_exists(accelerators):
    accelerators["available"] = ["cuda"]
    resolved, message = opendataloader.resolve_device("cpu")
    assert resolved == "cpu"
    assert message == DEVICE_SELECTED.format(device="cpu")


def test_a_named_accelerator_that_is_present_is_honoured(accelerators):
    accelerators["available"] = ["cuda"]
    resolved, _ = opendataloader.resolve_device("cuda")
    # docling answers `cuda:0`; the backend's flag takes the family.
    assert resolved == "cuda"


@pytest.mark.parametrize("device", ["cuda", "mps", "xpu"])
def test_a_named_accelerator_that_is_absent_fails_by_name(accelerators, device):
    accelerators["available"] = []
    with pytest.raises(PipelineError) as refused:
        opendataloader.resolve_device(device)
    assert refused.value.detail == DEVICE_UNAVAILABLE.format(device=device)


def test_an_unknown_device_is_refused_before_docling_is_asked(accelerators):
    with pytest.raises(PipelineError) as refused:
        opendataloader.resolve_device("tpu")
    assert "is not a device" in refused.value.detail


def test_a_missing_hybrid_stack_is_an_error_not_a_cpu_fallback(monkeypatch):
    """No docling means no OCR; it must not read as 'no accelerator'."""
    for name in ("docling", "docling.utils", "docling.utils.accelerator_utils"):
        monkeypatch.setitem(sys.modules, name, None)
    with pytest.raises(PipelineError) as refused:
        opendataloader.resolve_device("auto")
    assert "opendataloader-pdf[hybrid]" in refused.value.detail


def test_the_real_docling_decision_is_reused():
    """The detection is upstream's, not a hardware probe of our own."""
    pytest.importorskip("docling.utils.accelerator_utils")
    resolved, message = opendataloader.resolve_device("auto")
    assert resolved in opendataloader.DEVICES
    assert resolved != "auto"
    assert message.startswith("OpenDataLoader device:")
    assert opendataloader.resolve_device("cpu")[0] == "cpu"


# --------------------------------------------------------------------------
# The backend process
# --------------------------------------------------------------------------
def test_the_backend_is_started_on_loopback_with_the_resolved_device():
    instance = backend("mps")
    with instance:
        command = instance.started_commands[0]
        assert "--device" in command
        assert command[command.index("--device") + 1] == "mps"
        assert command[command.index("--host") + 1] == "127.0.0.1"
        # Generated alt text is never turned on: it invents content, and a
        # translation would carry the invention.
        assert "--no-enrich-picture-description" in command
        # A port of our own, never the package's advertised default, so no
        # server anyone else is running can be mistaken for ours.
        port = int(command[command.index("--port") + 1])
        assert port != 5002
        assert instance.url == f"http://127.0.0.1:{port}"
        assert instance.probed_urls == [instance.url]


def test_ocr_is_off_when_every_page_spells_itself_out():
    with backend("mps", ocr=False) as instance:
        assert "--no-ocr" in instance.started_commands[0]
    with backend("mps") as instance:
        assert "--no-ocr" not in instance.started_commands[0]


def test_the_models_read_pictures_only_when_a_page_has_no_text_layer(
    bundle, pdf, pandoc, accelerators, text_layer
):
    factories = []

    def factory(device, **kw):
        factories.append(kw)
        return backend(device, **kw)

    opendataloader.extract_pdf(
        bundle, pdf, pandoc=pandoc, backend_factory=factory, convert=fake_convert()
    )
    text_layer["missing"] = [2]
    opendataloader.extract_pdf(
        bundle, pdf, pandoc=pandoc, backend_factory=factory, convert=fake_convert()
    )
    assert [kw["ocr"] for kw in factories] == [False, True]


def test_readiness_is_waited_for(monkeypatch):
    instance = backend("cpu", ready_after=3)
    with instance:
        assert len(instance.probed_urls) == 4
    assert instance.process is None


def test_a_backend_that_exits_before_answering_is_an_error():
    process = FakeProcess(exits_after=1, output="ModuleNotFoundError: docling")
    instance = backend("cpu", process=process, ready_after=99)
    with pytest.raises(PipelineError) as failed:
        instance.start()
    assert failed.value.detail.startswith("OpenDataLoader backend failed:")
    assert "exited with 7" in failed.value.detail
    assert "ModuleNotFoundError" in failed.value.detail


def test_a_backend_that_never_answers_times_out_and_is_stopped():
    process = FakeProcess()
    instance = backend("cpu", process=process, ready_after=99, timeout=2.0)
    with pytest.raises(PipelineError) as failed:
        instance.start()
    assert "did not become ready" in failed.value.detail
    assert process.terminated


def test_a_backend_that_ignores_terminate_is_killed():
    process = FakeProcess(ignores_terminate=True)
    instance = backend("cpu", process=process)
    instance.start()
    instance.stop()
    assert process.terminated and process.killed


def test_a_failure_quotes_the_end_of_the_log_and_not_the_whole_of_it():
    """The error is at the end of a model-loading run, and is bounded."""
    chatter = "loading shard 1/2\n" * 40000
    process = FakeProcess(
        exits_after=1, output=chatter + "ModuleNotFoundError: docling\n"
    )
    instance = backend("cpu", process=process, ready_after=99)
    with pytest.raises(PipelineError) as failed:
        instance.start()
    detail = failed.value.detail
    assert "ModuleNotFoundError: docling" in detail
    assert detail.count("loading shard") < 60
    assert len(detail) < 1000, len(detail)


@pytest.mark.parametrize("pad", [0, 1, 2])
def test_a_failure_after_non_ascii_output_still_names_the_error(pad):
    """The quoted tail starts at an arbitrary byte, mid-character or not.

    A log longer than the bound is cut wherever the bound falls. When that
    is inside a multi-byte character -- an em dash in a traceback, a
    non-ASCII path -- a strict decode raises from inside the failure path
    and the operator gets a UnicodeDecodeError instead of the reason the
    backend died.

    The padding lengthens the log after the error line, which moves the cut
    through a run of three-byte characters: over the three cases it lands on
    a character boundary once and inside a character twice, and none of them
    may behave differently.
    """
    process = FakeProcess(
        exits_after=1,
        output=(
            "\u2014" * 4000 + "\nOSError: \u00e9chec du mod\u00e8le" + "!" * pad + "\n"
        ),
    )
    instance = backend("cpu", process=process, ready_after=99)
    with pytest.raises(PipelineError) as failed:
        instance.start()
    assert failed.value.detail.startswith("OpenDataLoader backend failed:")
    assert "exited with 7" in failed.value.detail
    assert "OSError: \u00e9chec du mod\u00e8le" in failed.value.detail
    # The dashes before it survived as dashes, not as replacement noise.
    assert "\u2014\u2014\u2014" in failed.value.detail


def test_the_log_is_closed_and_forgotten_when_the_backend_stops():
    instance = backend("cpu")
    instance.start()
    log = instance._log
    assert log is not None and not log.closed
    instance.stop()
    assert instance._log is None
    assert log.closed
    # Nothing to drain once it is gone, and no error for asking.
    assert instance._drain() == ""


def test_stop_touches_nothing_it_did_not_start():
    foreign = FakeProcess()
    instance = backend("cpu")
    instance.start()
    instance.stop()
    instance.stop()  # idempotent
    assert not foreign.terminated and not foreign.killed


# --------------------------------------------------------------------------
# Conversion
# --------------------------------------------------------------------------
def fake_convert(staging_markdown=CONVERTED, image=PNG, fail=None):
    calls = []

    def convert(input_path, **kwargs):
        calls.append(dict(kwargs, input_path=input_path))
        if fail is not None:
            raise fail
        output = Path(kwargs["output_dir"])
        output.mkdir(parents=True, exist_ok=True)
        images = Path(kwargs["image_dir"])
        images.mkdir(parents=True, exist_ok=True)
        (images / "imageFile1.png").write_bytes(image)
        (output / f"{Path(input_path).stem}.md").write_text(
            staging_markdown, encoding="utf-8"
        )

    convert.calls = calls
    return convert


def test_a_conversion_produces_the_same_bundle_contract(
    bundle, pdf, pandoc, accelerators
):
    convert = fake_convert()
    opendataloader.extract_pdf(
        bundle,
        pdf,
        pandoc=pandoc,
        device="auto",
        page_range="1-2",
        backend_factory=lambda device, **kw: backend(device, **kw),
        convert=convert,
    )

    kwargs = convert.calls[0]
    assert kwargs["hybrid"] == "docling-fast"
    # Never on: the Java-only fallback would drop the OCR this run is for.
    assert kwargs["hybrid_fallback"] is False
    assert kwargs["hybrid_url"].startswith("http://127.0.0.1:")
    # OpenDataLoader counts pages from 1, like the harness.
    assert kwargs["pages"] == "1-2"
    assert kwargs["format"] == "markdown"
    assert kwargs["image_output"] == "external"

    assert bundle.source.is_file()
    # The extractor's own `images/` directory is kept inside `assets/`, so
    # two chapters' `plate.png` cannot collide.
    assert (bundle.assets / "images" / "imageFile1.png").read_bytes() == PNG
    assert "assets/images/imageFile1.png" in bundle.source.read_text(encoding="utf-8")

    manifest = bundle.read_manifest()
    assert manifest["stages"]["extract"]["status"] == "completed"
    assert manifest["source"]["kind"] == "pdf"
    extraction = manifest["extraction"]
    assert extraction["provider"] == "opendataloader"
    assert extraction["device"] == "mps"
    assert extraction["device_requested"] == "auto"
    assert extraction["hybrid_fallback"] is False
    assert extraction["picture_description"] is False
    assert extraction["ocr"] is False
    assert extraction["page_numbering"] == "1-based input, 1-based request"
    assert extraction["cost_cents"] is None


def test_the_conversion_says_it_is_running_and_what_it_said_last(
    bundle, pdf, pandoc, accelerators, capsys
):
    """Minutes of two other programs' work, with a sign of life.

    The converter writes the Java engine's log to `sys.stdout` itself; the
    adapter intercepts that, so nothing scrolls past the operator and the
    last line either engine said becomes the progress line's detail. Here
    the stream is not a terminal, so whole lines are printed instead of one
    being rewritten.
    """

    def convert(input_path, **kwargs):
        assert kwargs["quiet"] is False, "the engine's log is the only progress"
        print("INFO: Number of pages: 2")
        fake_convert()(input_path, **kwargs)

    opendataloader.extract_pdf(
        bundle,
        pdf,
        pandoc=pandoc,
        backend_factory=lambda device, **kw: backend(device, **kw),
        convert=convert,
    )
    out = capsys.readouterr().out
    assert "Extracting PDF: 2 pages, layout models on mps, 0s" in out
    assert "PDF extracted: 2 pages, layout models on mps," in out
    # the engine's own log line is not printed on its own account
    assert "INFO: Number of pages: 2" not in out


def test_reading_the_backend_log_leaves_the_shared_offset_alone(accelerators):
    # PIN (lead, 260920, Codex review): the child writes through a duplicate
    # of this descriptor, and duplicates share one offset; a read that
    # seeks would move where the child's next line lands. So the read is a
    # pread, and the offset a real child would write at is untouched.
    import os
    import subprocess
    import tempfile

    instance = backend()
    instance._log = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    fd = instance._log.fileno()
    subprocess.run(
        ["sh", "-c", "printf 'INFO: first\\n'"], stdout=instance._log, check=True
    )
    # Park the shared offset somewhere that is not the end: a seek-and-read
    # implementation leaves it at the end, and this one must not touch it.
    os.lseek(fd, 3, os.SEEK_SET)

    assert instance.new_output() == "INFO: first\n"
    assert os.lseek(fd, 0, os.SEEK_CUR) == 3

    os.lseek(fd, 0, os.SEEK_END)
    subprocess.run(
        ["sh", "-c", "printf 'INFO: second\\n'"], stdout=instance._log, check=True
    )
    assert instance.new_output() == "INFO: second\n"
    assert instance.new_output() == ""
    instance._log.close()
    instance._log = None


def test_the_backend_log_is_still_read_where_pread_does_not_exist(
    accelerators, monkeypatch
):
    # Windows has no os.pread; the read must still work there, on the shared
    # offset, rather than kill the progress thread with an AttributeError.
    import tempfile

    monkeypatch.delattr(opendataloader.os, "pread", raising=False)
    instance = backend()
    instance._log = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
    instance._log.write("INFO: one\nINFO: two\n")
    assert instance.new_output() == "INFO: one\nINFO: two\n"
    instance._log.write("INFO: three\n")
    assert instance.new_output() == "INFO: three\n"
    instance._log.close()
    instance._log = None


def test_a_quiet_extraction_prints_no_progress_at_all(
    bundle, pdf, pandoc, accelerators, capsys
):
    opendataloader.extract_pdf(
        bundle,
        pdf,
        pandoc=pandoc,
        backend_factory=lambda device, **kw: backend(device, **kw),
        convert=fake_convert(),
        progress=False,
    )
    out = capsys.readouterr().out
    assert "Extracting PDF" not in out
    assert "PDF extracted" not in out


def test_a_failed_conversion_quotes_what_the_engines_said_last(
    bundle, pdf, pandoc, accelerators
):
    """`quiet=False` is also what keeps the diagnostic.

    `CalledProcessError` says only that a command exited non-zero; the
    reason is in the output, which is read for the progress line and would
    otherwise be dropped.
    """

    def convert(input_path, **kwargs):
        print("SEVERE: cannot read the document catalog")
        raise subprocess.CalledProcessError(1, ["java"])

    with pytest.raises(PipelineError) as failed:
        opendataloader.extract_pdf(
            bundle,
            pdf,
            pandoc=pandoc,
            backend_factory=lambda device, **kw: backend(device, **kw),
            convert=convert,
        )
    assert "cannot read the document catalog" in failed.value.detail


def test_the_backend_log_is_read_forward_in_whole_lines():
    instance = backend("cpu")
    instance.start()
    instance._log.write("2026-01-01 00:00:00,000 - INFO - Processing document\n")
    instance._log.write("a half-written")
    assert instance.new_output().endswith("Processing document\n")
    # the rest of that line is not shown until it is a line
    assert instance.new_output() == ""
    instance._log.write(" line\n")
    assert instance.new_output() == "a half-written line\n"
    instance.stop()
    # closed, and asking is still not an error
    assert instance.new_output() == ""


def test_the_bundle_it_produces_translates_and_exports_like_any_other(
    bundle, pdf, pandoc, accelerators, monkeypatch
):
    register_fake_format(monkeypatch)
    opendataloader.extract_pdf(
        bundle,
        pdf,
        pandoc=pandoc,
        backend_factory=lambda device, **kw: backend(device, **kw),
        convert=fake_convert(),
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


def test_a_document_that_spells_itself_out_keeps_the_engines_own_triage(
    bundle, pdf, pandoc, accelerators, text_layer, capsys
):
    """Every page has a text layer, so nothing is forced through the models."""
    convert = fake_convert()
    opendataloader.extract_pdf(
        bundle,
        pdf,
        pandoc=pandoc,
        page_range="1-2",
        backend_factory=lambda device, **kw: backend(device, **kw),
        convert=convert,
    )
    assert convert.calls[0]["hybrid_mode"] == opendataloader.TRIAGE_AUTO
    # The selection is what gets examined, not the whole file.
    assert text_layer["asked"] == (pdf, "1-2")
    extraction = bundle.read_manifest()["extraction"]
    assert extraction["hybrid_mode"] == "auto"
    assert extraction["pages_without_text_layer"] == []
    assert extraction["pages_examined"] == 2
    assert "no text layer" not in capsys.readouterr().out


def test_a_page_with_no_text_layer_sends_every_page_to_the_backend(
    bundle, pdf, pandoc, accelerators, text_layer, capsys
):
    """The measured failure this guards: with the engine's own triage, an
    image-only page never reached the backend at all -- the conversion came
    back with a picture, no text, and a completed stage."""
    text_layer["missing"] = [2]
    convert = fake_convert()
    opendataloader.extract_pdf(
        bundle,
        pdf,
        pandoc=pandoc,
        backend_factory=lambda device, **kw: backend(device, **kw),
        convert=convert,
    )
    assert convert.calls[0]["hybrid_mode"] == opendataloader.TRIAGE_FULL
    assert convert.calls[0]["hybrid"] == "docling-fast"
    assert SCANNED_PAGES.format(count=1, total=2) in capsys.readouterr().out
    extraction = bundle.read_manifest()["extraction"]
    assert extraction["hybrid_mode"] == "full"
    assert extraction["pages_without_text_layer"] == [2]
    assert extraction["ocr"] is True
    job = json.loads(bundle.work_file(EXTRACTION_JOB).read_text(encoding="utf-8"))
    assert job["hybrid_mode"] == "full"


def test_ocr_that_returned_nothing_is_a_failure_not_an_empty_book(
    bundle, pdf, pandoc, accelerators, text_layer
):
    text_layer["missing"] = [1]
    text_layer["examined"] = 1
    with pytest.raises(PipelineError) as failed:
        opendataloader.extract_pdf(
            bundle,
            pdf,
            pandoc=pandoc,
            backend_factory=lambda device, **kw: backend(device, **kw),
            convert=fake_convert(
                staging_markdown="<!-- page 1 -->\n\n![](<images/imageFile1.png>)\n"
            ),
        )
    assert failed.value.detail == OCR_EMPTY
    assert bundle.stage_status("extract") == "failed"
    assert not bundle.source.exists()


def test_one_page_the_models_could_not_read_is_reported_not_hidden(
    bundle, pdf, pandoc, accelerators, text_layer, capsys
):
    """A plate with no words on it is a legitimate empty page, so the run
    stands -- but it is said out loud and recorded."""
    text_layer["missing"] = [1, 2]
    opendataloader.extract_pdf(
        bundle,
        pdf,
        pandoc=pandoc,
        backend_factory=lambda device, **kw: backend(device, **kw),
        convert=fake_convert(
            staging_markdown=(
                "<!-- page 1 -->\n\n# Scanned Chapter\n\nWords the models read.\n"
                "\n<!-- page 2 -->\n\n![](<images/imageFile1.png>)\n"
            )
        ),
    )
    assert bundle.stage_status("extract") == "completed"
    warning = OCR_EMPTY_PAGES.format(pages="2")
    assert warning in capsys.readouterr().out
    assert warning in bundle.read_manifest()["limitations"]


def test_a_conversion_failure_stops_the_backend_and_fails_the_stage(
    bundle, pdf, pandoc, accelerators
):
    processes = []

    def factory(device, **kw):
        instance = backend(device, process=FakeProcess())
        processes.append(instance)
        return instance

    with pytest.raises(PipelineError) as failed:
        opendataloader.extract_pdf(
            bundle,
            pdf,
            pandoc=pandoc,
            backend_factory=factory,
            convert=fake_convert(fail=RuntimeError("java exited 1")),
        )
    assert failed.value.detail == BACKEND_FAILED.format(
        detail="RuntimeError: java exited 1"
    )
    assert bundle.stage_status("extract") == "failed"
    assert processes[0].process is None
    assert not bundle.source.exists()


def test_an_interruption_stops_the_backend_it_owns(bundle, pdf, pandoc, accelerators):
    owned = []

    def factory(device, **kw):
        instance = backend(device, process=FakeProcess())
        owned.append(instance)
        return instance

    with pytest.raises(KeyboardInterrupt):
        opendataloader.extract_pdf(
            bundle,
            pdf,
            pandoc=pandoc,
            backend_factory=factory,
            convert=fake_convert(fail=KeyboardInterrupt()),
        )
    assert owned[0].process is None


def test_an_empty_conversion_is_an_error(bundle, pdf, pandoc, accelerators):
    with pytest.raises(PipelineError) as failed:
        opendataloader.extract_pdf(
            bundle,
            pdf,
            pandoc=pandoc,
            backend_factory=lambda device, **kw: backend(device, **kw),
            convert=fake_convert(staging_markdown="   \n"),
        )
    assert "no content" in failed.value.detail
    assert bundle.stage_status("extract") == "failed"


def test_a_missing_java_runtime_fails_before_the_backend_starts(
    bundle, pdf, pandoc, accelerators, monkeypatch
):
    monkeypatch.setattr(
        opendataloader.shutil, "which", lambda name: None if name == "java" else "/x"
    )
    started = []
    with pytest.raises(PipelineError) as refused:
        opendataloader.extract_pdf(
            bundle,
            pdf,
            pandoc=pandoc,
            backend_factory=lambda device: started.append(device),
            convert=fake_convert(),
        )
    assert refused.value.detail == JAVA_REQUIRED
    assert started == []


def test_a_bundle_holding_a_datalab_job_is_not_converted_locally(
    bundle, pdf, pandoc, accelerators
):
    bundle.work.mkdir(parents=True, exist_ok=True)
    bundle.work_file(EXTRACTION_JOB).write_text(
        json.dumps({"parser": "datalab", "check_url": "https://x/1"}), encoding="utf-8"
    )
    with pytest.raises(PipelineError) as refused:
        opendataloader.extract_pdf(
            bundle,
            pdf,
            pandoc=pandoc,
            backend_factory=lambda device, **kw: backend(device, **kw),
            convert=fake_convert(),
        )
    assert "holds a datalab extraction" in refused.value.detail


# --------------------------------------------------------------------------
# Parser dispatch
# --------------------------------------------------------------------------
def test_the_harness_and_the_adapter_name_the_same_parser():
    """The harness spells the parser name rather than importing it."""
    harness = load_harness()
    assert harness.PDF_PARSER == opendataloader.PARSER


def test_a_pdf_goes_to_opendataloader_with_no_flag_at_all(
    tmp_path, pandoc, pdf, accelerators, monkeypatch
):
    """The default and the only route: a PDF is read locally.

    No parser is chosen and none can be. `--no-gpu` left out means the
    adapter's own `auto`, and nothing else is consulted to decide.
    """
    seen = {}

    def record(bundle, path, *, pandoc, device="auto", page_range=None, **kwargs):
        seen.update(device=device, page_range=page_range, path=Path(path))
        raise PipelineError("stopped before the backend", stage="extract")

    monkeypatch.setattr(opendataloader, "extract_pdf", record)
    harness = load_harness()
    code = harness.main(
        ["--pandoc", pandoc, "extract", str(pdf), "--output", str(tmp_path / "b")]
    )
    assert code == 1
    assert seen == {"device": "auto", "page_range": None, "path": pdf}


def test_the_pdf_route_never_touches_the_cloud_adapter(
    tmp_path, pandoc, pdf, accelerators, monkeypatch
):
    """No key is read, no module is imported, no call is made.

    The Datalab adapter still exists for other work; what must be true here
    is that this command line cannot reach it, cannot be pushed towards it
    by an environment variable, and does not even load it.
    """
    import book_maker.pipeline.datalab as datalab

    def refuse(*args, **kwargs):
        raise AssertionError("the local parser used the Datalab adapter")

    monkeypatch.setattr(datalab, "extract_pdf", refuse)
    monkeypatch.setattr(datalab, "_requests_session", refuse)
    monkeypatch.setenv("DATALAB_API_KEY", "dl-from-env")
    monkeypatch.delitem(sys.modules, "book_maker.pipeline.datalab")

    seen = {}

    def record(bundle, path, *, pandoc, device="auto", page_range=None, **kwargs):
        seen.update(device=device, page_range=page_range, path=Path(path))
        raise PipelineError("stopped before the backend", stage="extract")

    monkeypatch.setattr(opendataloader, "extract_pdf", record)
    harness = load_harness()
    code = harness.main(
        [
            "--pandoc",
            pandoc,
            "extract",
            str(pdf),
            "--output",
            str(tmp_path / "b"),
            "--no-gpu",
            "--pages",
            "1-4",
        ]
    )
    assert code == 1
    assert seen["device"] == "cpu"
    assert seen["page_range"] == "1-4"
    assert seen["path"] == pdf
    assert "book_maker.pipeline.datalab" not in sys.modules


@pytest.mark.parametrize("command", ["extract", "run"])
def test_the_parser_selector_is_gone_and_is_refused(
    tmp_path, pandoc, pdf, command, capsys
):
    """An unshipped flag gets no compatibility alias, only a refusal."""
    harness = load_harness()
    with pytest.raises(SystemExit) as exited:
        harness.main(
            [
                "--pandoc",
                pandoc,
                command,
                str(pdf),
                "--output",
                str(tmp_path / "b"),
                "--pdf-parser",
                "opendataloader",
            ]
        )
    assert exited.value.code == 2
    assert "--pdf-parser" in capsys.readouterr().err


@pytest.mark.parametrize(
    "given", [["--no-gpu"], ["--pages", "1-2"]], ids=["--no-gpu", "--pages"]
)
def test_a_pdf_only_option_on_markdown_is_refused_not_ignored(
    tmp_path, pandoc, given, capsys, monkeypatch
):
    """Markdown reads no PDF, so neither option could have been honoured.

    The translation options are real ones, so the refusal that arrives is
    the one under test and not `run` rejecting the command line first.
    """
    register_fake_format(monkeypatch)
    book = tmp_path / "book.md"
    book.write_text("# Title\n\nProse.\n", encoding="utf-8")

    def refuse(*args, **kwargs):
        raise AssertionError("a Markdown input reached the PDF adapter")

    monkeypatch.setattr(opendataloader, "extract_pdf", refuse)
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


def test_importing_markdown_loads_no_pdf_stack_and_starts_nothing(
    tmp_path, pandoc, monkeypatch
):
    """An import must not pay for the parser it is not going to use."""
    register_fake_format(monkeypatch)
    book = tmp_path / "book.md"
    book.write_text("# Title\n\nProse to translate.\n", encoding="utf-8")

    def refuse(*args, **kwargs):
        raise AssertionError("a Markdown import started the OCR backend")

    monkeypatch.setattr(opendataloader.HybridBackend, "start", refuse)
    heavy = ("docling", "pypdfium2", "opendataloader_pdf")
    for name in list(sys.modules):
        if name in heavy or any(name.startswith(f"{h}.") for h in heavy):
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
    assert not set(heavy) & set(sys.modules)


def test_a_finished_run_does_not_extract_again_or_clobber_an_edited_source(
    tmp_path, pandoc, pdf, accelerators, monkeypatch
):
    register_fake_format(monkeypatch)
    bundle = Bundle(tmp_path / "bundle").create()
    opendataloader.extract_pdf(
        bundle,
        pdf,
        pandoc=pandoc,
        backend_factory=lambda device, **kw: backend(device, **kw),
        convert=fake_convert(),
    )
    edited = bundle.source.read_text(encoding="utf-8").replace(
        "Chapter One", "Chapter One, corrected"
    )
    bundle.source.write_text(edited, encoding="utf-8")

    def refuse(*args, **kwargs):
        raise AssertionError("a finished extraction was bought again")

    monkeypatch.setattr(opendataloader, "extract_pdf", refuse)
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
    tmp_path, pandoc, pdf, accelerators, monkeypatch
):
    bundle = Bundle(tmp_path / "bundle").create()
    opendataloader.extract_pdf(
        bundle,
        pdf,
        pandoc=pandoc,
        page_range="1-2",
        backend_factory=lambda device, **kw: backend(device, **kw),
        convert=fake_convert(),
    )
    harness = load_harness()
    assert harness.already_prepared(bundle, pdf, "opendataloader", "1-2")
    assert not harness.already_prepared(bundle, pdf, "opendataloader", "3-4")
    assert not harness.already_prepared(bundle, pdf, "datalab", "1-2")


# --------------------------------------------------------------------------
# Hidden text: what the engines are handed, and what the operator is told
# --------------------------------------------------------------------------
FIGURE_REPORT = [
    {
        "page": 1,
        "objects": [
            {
                "index": 3,
                "reason": "hidden-text",
                "hidden": 85525,
                "visible": 896,
                "bounds": [0, 0, 1, 1],
            }
        ],
    },
    {
        "page": 3,
        "objects": [
            {
                "index": 2,
                "reason": "figure",
                "hidden": 0,
                "visible": 209,
                "bounds": [0, 0, 1, 1],
            },
            {
                "index": 5,
                "reason": "figure",
                "hidden": 0,
                "visible": 40,
                "bounds": [0, 0, 1, 1],
            },
        ],
    },
]


def test_a_figure_hiding_text_is_rasterized_before_the_engines_read_it(
    bundle, pdf, pandoc, accelerators, sanitizer, text_layer, capsys
):
    sanitizer["report"] = FIGURE_REPORT
    sanitizer["sanitized"] = b"%PDF-1.7\n%sanitized\n"
    convert = fake_convert()
    opendataloader.extract_pdf(
        bundle,
        pdf,
        pandoc=pandoc,
        page_range="1-2",
        backend_factory=lambda device, **kw: backend(device, **kw),
        convert=convert,
    )
    handed = Path(convert.calls[0]["input_path"])
    # The engines read the copy, named like the original so the Markdown
    # keeps its stem; the original is what the pass was asked about.
    assert handed != pdf and handed.name == pdf.name
    assert handed.read_bytes() == b"%PDF-1.7\n%sanitized\n"
    assert sanitizer["asked"] == (pdf, bundle.work_file("extraction"), "1-2")
    # The text-layer triage looks at what the engines will read.
    assert text_layer["asked"][0] == handed
    line = HIDDEN_TEXT_RASTERIZED.format(page=1, hidden=85525)
    figures = FIGURES_RASTERIZED.format(pages="3", count=2)
    out = capsys.readouterr().out
    assert line in out and figures in out
    manifest = bundle.read_manifest()
    assert manifest["extraction"]["hidden_text_figures"] == FIGURE_REPORT
    assert line in manifest["limitations"] and figures in manifest["limitations"]
    # Provenance still names the operator's file, not the working copy.
    assert manifest["extraction"]["pdf"] == pdf.name


def test_a_document_hiding_nothing_is_read_as_it_is(
    bundle, pdf, pandoc, accelerators, sanitizer
):
    convert = fake_convert()
    opendataloader.extract_pdf(
        bundle,
        pdf,
        pandoc=pandoc,
        backend_factory=lambda device, **kw: backend(device, **kw),
        convert=convert,
    )
    assert Path(convert.calls[0]["input_path"]) == pdf
    manifest = bundle.read_manifest()
    assert manifest["extraction"]["hidden_text_figures"] == []
    assert not any("rasterized" in line for line in manifest["limitations"])


def test_a_page_with_more_prose_than_a_page_holds_is_called_out(
    bundle, pdf, pandoc, accelerators, capsys
):
    flood = "<!-- page 1 -->\n\n# Title\n\n" + ("(a) Previous methods\n\n" * 700)
    flood += "<!-- page 2 -->\n\nAn ordinary page.\n"
    convert = fake_convert(staging_markdown=flood)
    opendataloader.extract_pdf(
        bundle,
        pdf,
        pandoc=pandoc,
        backend_factory=lambda device, **kw: backend(device, **kw),
        convert=convert,
    )
    out = capsys.readouterr().out
    assert "Warning: page 1 extracted" in out
    assert "page 2 extracted" not in out
    limitations = bundle.read_manifest()["limitations"]
    assert any(line.startswith("Warning: page 1 extracted") for line in limitations)


def test_dense_pages_counts_prose_not_markers_or_pictures():
    text = (
        "<!-- page 1 -->\n\n" + "x" * 30 + "\n\n"
        "<!-- page 2 -->\n\n![](<images/imageFile1.png>)\n\n" + "y" * 10 + "\n"
    )
    assert opendataloader.dense_pages(text, limit=20) == [(1, 30)]
    assert opendataloader.dense_pages(text, limit=40) == []


# --------------------------------------------------------------------------
# Hidden text, found in real PDFs
# --------------------------------------------------------------------------
FIGURE_LINES = [f"Clipped figure line {n} that nobody sees" for n in range(30)]


def test_the_clipped_away_text_of_a_figure_is_found_and_the_rest_is_not(tmp_path):
    pdfium_or_skip()
    import pypdfium2 as pdfium

    path = write_pdf(
        tmp_path / "figure.pdf",
        ["Body text on page one.", "Body text on page two."],
        figure=(1, FIGURE_LINES),
    )
    document = pdfium.PdfDocument(str(path))
    report = pdf_sanitize.figure_report(document, [1, 2])
    document.close()
    assert [entry["page"] for entry in report] == [1]
    (figure,) = report[0]["objects"]
    assert figure["reason"] == "hidden-text"
    # Everything but the first line is under the clip.
    assert figure["hidden"] == sum(len(line) for line in FIGURE_LINES[1:])
    assert figure["visible"] == len(FIGURE_LINES[0])


def report_of(path, pages=(1,)):
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(path))
    try:
        return pdf_sanitize.figure_report(document, list(pages))
    finally:
        document.close()


def test_a_drawn_figure_is_a_picture_even_when_it_hides_nothing(tmp_path):
    pdfium_or_skip()
    path = write_pdf(
        tmp_path / "chart.pdf",
        ["Body."],
        figure=(1, FIGURE_LINES[:3]),
        figure_clip=False,
    )
    ((figure,),) = [entry["objects"] for entry in report_of(path)]
    assert figure["reason"] == "figure"
    assert figure["hidden"] == 0
    assert figure["visible"] == sum(len(line) for line in FIGURE_LINES[:3])


def test_a_form_that_only_writes_is_left_as_text(tmp_path):
    pdfium_or_skip()
    path = write_pdf(
        tmp_path / "words.pdf",
        ["Body."],
        figure=(1, FIGURE_LINES[:3]),
        figure_clip=False,
        figure_draws=False,
    )
    assert report_of(path) == []


def test_a_whole_page_wrapper_is_left_as_text(tmp_path):
    pdfium_or_skip()
    path = write_pdf(
        tmp_path / "wrapped.pdf",
        ["Body."],
        figure=(1, FIGURE_LINES[:3]),
        figure_clip=False,
        figure_scale=2.5,
    )
    assert report_of(path) == []


def test_a_figure_hiding_text_becomes_a_picture_in_a_copy(tmp_path):
    pdfium_or_skip()
    import pypdfium2 as pdfium

    path = write_pdf(
        tmp_path / "figure.pdf",
        ["Body text on page one.", "Body text on page two."],
        figure=(1, FIGURE_LINES),
    )
    original = path.read_bytes()
    copy, report = pdf_sanitize.sanitize_pdf(path, tmp_path / "work")
    assert path.read_bytes() == original
    assert copy == tmp_path / "work" / "sanitized" / "figure.pdf"
    assert report[0]["objects"][0]["rasterized"]

    document = pdfium.PdfDocument(str(copy))
    first = document[0].get_textpage().get_text_range()
    second = document[1].get_textpage().get_text_range()
    kinds = [obj.type for obj in document[0].get_objects(max_depth=0)]
    document.close()
    assert "Body text on page one." in first
    assert "Clipped figure line" not in first
    assert second.strip() == "Body text on page two."
    assert pdfium.raw.FPDF_PAGEOBJ_IMAGE in kinds
    assert pdfium.raw.FPDF_PAGEOBJ_FORM not in kinds


def test_a_document_hiding_nothing_gets_no_copy(tmp_path):
    pdfium_or_skip()
    path = write_pdf(tmp_path / "plain.pdf", ["Page one.", "Page two."])
    assert pdf_sanitize.sanitize_pdf(path, tmp_path / "work") == (None, [])
    assert not (tmp_path / "work").exists()


def test_only_the_selected_pages_are_sanitized(tmp_path):
    pdfium_or_skip()
    path = write_pdf(
        tmp_path / "figure.pdf", ["One.", "Two."], figure=(1, FIGURE_LINES)
    )
    assert pdf_sanitize.sanitize_pdf(path, tmp_path / "work", "2") == (None, [])


# --------------------------------------------------------------------------
# The text layer, read from real PDFs
# --------------------------------------------------------------------------
MISSING_PDFIUM = 'pypdfium2 is not installed (pip install "opendataloader-pdf[hybrid]")'


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


def test_a_document_with_no_text_layer_at_all_is_seen_as_scanned(tmp_path):
    pdfium_or_skip()
    path = write_pdf(tmp_path / "scan.pdf", [None, None])
    assert real_text_layer_report(path) == ([1, 2], 2)


def test_only_the_selected_pages_are_examined(tmp_path):
    pdfium_or_skip()
    path = write_pdf(tmp_path / "mixed.pdf", ["Typed.", None, "Typed.", None])
    assert real_text_layer_report(path, "3-4") == ([4], 2)
    assert real_text_layer_report(path, "1") == ([], 1)


def test_a_file_that_is_not_a_pdf_is_refused_before_the_models_start(tmp_path):
    pdfium_or_skip()
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.7\n%not really\n")
    with pytest.raises(PipelineError) as refused:
        real_text_layer_report(path)
    assert "broken.pdf" in refused.value.detail


def test_the_pages_a_conversion_left_blank_are_named():
    blank, any_prose = opendataloader.blank_pages(
        "<!-- page 1 -->\n\n# Heading\n\nProse.\n\n"
        "<!-- page 2 -->\n\n![](<images/imageFile1.png>)\n\n"
        "<!-- page 3 -->\n\nMore prose.\n"
    )
    assert (blank, any_prose) == ([2], True)


def test_a_conversion_of_nothing_but_pictures_has_no_prose_anywhere():
    blank, any_prose = opendataloader.blank_pages(
        "<!-- page 1 -->\n\n![](<images/imageFile1.png>)\n\n"
        "<!-- page 2 -->\n\n![](<images/imageFile2.png>)\n"
    )
    assert (blank, any_prose) == ([1, 2], False)


def test_interrupt_during_backend_start_cleans_up_child():
    process = FakeProcess()
    instance = backend(process=process)

    def interrupt(url):
        raise KeyboardInterrupt

    instance._probe = interrupt
    with pytest.raises(KeyboardInterrupt):
        instance.start()
    assert process.terminated and process.waited
    assert instance.process is None
    assert instance._log is None


def test_verbose_backend_does_not_block_on_unread_output(tmp_path):
    import subprocess
    import time

    marker = tmp_path / "ready"
    instance = opendataloader.HybridBackend(
        "cpu",
        readiness_timeout=10,
        probe=lambda url: marker.exists(),
    )
    instance.command = lambda: [
        sys.executable,
        "-c",
        "import sys,time; from pathlib import Path; "
        "sys.stdout.write('x' * 2000000); sys.stdout.flush(); "
        "Path(sys.argv[1]).write_text('ready'); time.sleep(30)",
        str(marker),
    ]
    with instance:
        process = instance.process
        assert marker.read_text() == "ready"
    assert process.poll() is not None
