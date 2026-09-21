"""OpenDataLoader PDF as the local parser, with its OCR backend.

Two processes do the work and neither of them is ours: a Java engine that
reads the PDF's structure, and a Python backend that runs the layout and OCR
models. This module owns the second one for the length of one conversion --
it starts it on loopback, on a port nobody else was using, waits for it to
answer, tells it which device to run on, and stops it again whether the
conversion succeeded, failed or was interrupted. It never adopts a server
that was already running: that server's device and OCR settings are unknown,
and "the book was converted on something" is not an answer.

Triage is not left to chance either. The Java engine decides by itself
which pages are worth sending to the backend, and a page that is nothing but
a scan of a page does not always reach it -- measured here: an image-only
page produced a picture and no text, with the backend never asked. So the
selected pages are checked for an extractable text layer first, and a
document missing one converts with every page sent to the backend.

The device decision is docling's own (`decide_device`), not a hardware probe
of ours: `auto` resolves to whatever it finds and falls back to CPU, `cpu`
forces CPU, and a named accelerator that is not there fails by name. CPU is
the fallback for *acceleration*, never for OCR -- `hybrid_fallback` stays off
so a backend error cannot quietly demote the run to Java-only extraction.
"""

import contextlib
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

from .bundle import EXTRACTION_JOB, one_based_pages, parse_pages, sha256_file
from .errors import PipelineError
from .importer import import_markdown
from .pdf_sanitize import sanitize_pdf
from .messages import (
    BACKEND_FAILED,
    DEVICE_CPU_FALLBACK,
    DEVICE_SELECTED,
    DEVICE_UNAVAILABLE,
    ENGINE_JAVA,
    ENGINE_LAYOUT,
    ENGINE_OCR,
    EXTRACT_DONE,
    FIGURES_RASTERIZED,
    EXTRACT_PROGRESS_LABEL,
    HIDDEN_TEXT_RASTERIZED,
    JAVA_REQUIRED,
    OCR_EMPTY,
    OCR_EMPTY_PAGES,
    OCR_REQUIRED,
    PAGE_TOO_DENSE,
    PAGES_SCOPE,
    PAGE_SCOPE,
    SCANNED_PAGES,
)
from .progress import ProgressLine, ticking

STAGE = "extract"
PARSER = "opendataloader"

DEVICES = ("auto", "cpu", "cuda", "mps", "xpu")

HOST = "127.0.0.1"
# The models are downloaded on the first run and loaded on every one; a
# minute is not unusual and a slow first start is not a failure.
READINESS_TIMEOUT = 600.0
READINESS_INTERVAL = 0.5
# How long the backend gets to exit after being asked politely.
SHUTDOWN_GRACE = 10.0

# How much of the backend's log one failure message quotes. The log is a
# whole model-loading run and can be megabytes of progress chatter; the
# error that matters is at the end of it, and an operator reading a refusal
# must not be handed the whole thing to find it.
LOG_TAIL_BYTES = 4096
LOG_TAIL_CHARS = 600
# How many of the two engines' log lines are kept for a failure message.
LOG_TAIL_LINES = 20

# What a line of somebody else's log has to look like before it is shown as
# progress. The backend logs through Python's logging and through uvicorn,
# the Java engine through java.util.logging -- whose own format puts the
# class and method on a line of their own above the message, which is why an
# unprefixed line is dropped rather than shown.
LOG_LEVEL_PREFIX = re.compile(
    r"^(?:\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d[.,]\d+ - \w+ - |"
    r"(?:INFO|WARNING|ERROR|SEVERE|CRITICAL|DEBUG):\s+)"
)
# uvicorn's access log: one line per health probe, saying nothing about the
# conversion.
ACCESS_LOG = re.compile(r"^\w+:\s+\d{1,3}(?:\.\d{1,3}){3}:\d+\s+-\s+\"")

# `%page-number%` is substituted by the Java engine. An HTML comment is a
# block the Markdown loader passes through untouched and Pandoc drops from
# the rendered book, so the provenance marker survives translation without
# becoming prose. OpenDataLoader numbers pages from 1.
PAGE_SEPARATOR = "<!-- page %page-number% -->"

# Conversion request. Pictures are extracted as files and described by
# nobody: generated alt text has been measured inventing content, and a
# translation would then carry the invention.
HYBRID_MODE = "docling-fast"

# Which pages the Java engine hands to the backend. Its own triage ("auto")
# keeps an ordinary text document cheap; "full" sends every page, and is the
# only way a page with no text layer is read at all.
TRIAGE_AUTO = "auto"
TRIAGE_FULL = "full"

PAGE_MARKER = re.compile(r"<!--\s*page\s+(\d+)\s*-->")
# More prose than a printed page can show. A dense two-column page at nine
# points holds six or seven thousand characters; the measured failure
# (arXiv 2609.20519, page 1) returned a hundred thousand. Well above the
# first, well below the second; a warning, not a refusal.
PAGE_CHARS_LIMIT = 12000
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def resolve_device(requested):
    """`(resolved, message)` for the device the backend will actually use.

    Delegated to docling, which is the thing that will run the models:
    `auto` is its detection, an unavailable accelerator is its refusal, and
    `cpu` is the one answer that needs no hardware at all.
    """
    requested = (requested or "auto").lower()
    if requested not in DEVICES:
        raise PipelineError(
            f"{requested!r} is not a device; choose one of {', '.join(DEVICES)}",
            stage=STAGE,
        )
    try:
        from docling.utils.accelerator_utils import (
            AcceleratorDeviceNotAvailableError,
            decide_device,
        )
    except ImportError as err:
        raise PipelineError(
            f"the OpenDataLoader hybrid stack is not installed "
            f'(pip install "opendataloader-pdf[hybrid]"): {err}',
            stage=STAGE,
        )
    try:
        resolved = decide_device(requested)
    except AcceleratorDeviceNotAvailableError:
        raise PipelineError(DEVICE_UNAVAILABLE.format(device=requested), stage=STAGE)
    # docling answers `cuda:0`; the backend's own flag takes the family.
    resolved = resolved.split(":", 1)[0]
    if requested == "auto" and resolved == "cpu":
        return resolved, DEVICE_CPU_FALLBACK
    return resolved, DEVICE_SELECTED.format(device=resolved)


def free_port(host=HOST):
    """A port nothing is listening on, so no existing server is adopted."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return probe.getsockname()[1]


class HybridBackend:
    """The OCR backend, as a child process this harness starts and stops."""

    def __init__(
        self,
        device,
        *,
        ocr=True,
        host=HOST,
        port=None,
        readiness_timeout=READINESS_TIMEOUT,
        readiness_interval=READINESS_INTERVAL,
        popen=subprocess.Popen,
        sleep=time.sleep,
        monotonic=time.monotonic,
        probe=None,
    ):
        self.device = device
        self.ocr = ocr
        self.host = host
        self.port = port or free_port(host)
        self.readiness_timeout = readiness_timeout
        self.readiness_interval = readiness_interval
        self._popen = popen
        self._sleep = sleep
        self._monotonic = monotonic
        self._probe = probe or self._http_health
        self.process = None
        self._log = None
        self._read = 0

    @property
    def url(self):
        return f"http://{self.host}:{self.port}"

    def command(self):
        """The backend's command line, device included.

        `--host 127.0.0.1` on purpose: the server's own default binds every
        interface, and this one exists for the length of one conversion on
        this machine. `--no-enrich-picture-description` is the default and
        is stated anyway, because an invented caption is the one output
        this pipeline must never translate.

        `--no-ocr` when every selected page has a text layer: the models
        otherwise read the pictures too, and a chart's labels come back as
        paragraphs -- measured on a rasterized figure, whose panel titles
        even came back as headings. A page with no text layer is the one
        case the models must read, and then every picture is read with it.
        """
        executable = shutil.which("opendataloader-pdf-hybrid")
        launcher = (
            [executable]
            if executable
            else [sys.executable, "-m", "opendataloader_pdf.hybrid_server"]
        )
        command = launcher + [
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--device",
            self.device,
            "--no-enrich-picture-description",
        ]
        if not self.ocr:
            command.append("--no-ocr")
        return command

    def _http_health(self, url):
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=5) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def start(self):
        # A file avoids pipe backpressure while the converter is running.
        self._log = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
        self._read = 0
        try:
            self.process = self._popen(
                self.command(),
                stdout=self._log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            deadline = self._monotonic() + self.readiness_timeout
            while self._monotonic() < deadline:
                code = self.process.poll()
                if code is not None:
                    detail = self._drain().strip()
                    raise PipelineError(
                        BACKEND_FAILED.format(
                            detail=f"it exited with {code} before becoming ready"
                            + (f": {detail}" if detail else "")
                        ),
                        stage=STAGE,
                    )
                if self._probe(self.url):
                    return self
                self._sleep(self.readiness_interval)
            raise PipelineError(
                BACKEND_FAILED.format(
                    detail=f"it did not become ready within "
                    f"{self.readiness_timeout:g}s on {self.url}"
                ),
                stage=STAGE,
            )
        except (OSError, ValueError) as err:
            self.stop()
            raise PipelineError(
                BACKEND_FAILED.format(detail=f"could not start it: {err}"), stage=STAGE
            ) from err
        except BaseException:
            # __exit__ is not called if __enter__/start is interrupted.
            self.stop()
            raise

    def new_output(self):
        """Whole lines the backend has logged since this was last asked.

        The models take minutes and the operator is owed a sign of life,
        which is in this log and nowhere else. Complete lines only: the read
        stops at the last newline and leaves the rest for the next call, so
        a line caught half-written is not shown as a half sentence and a
        multi-byte character split across two reads is not mangled.
        """
        if self._log is None:
            return ""
        try:
            # Our own buffered writes (the start-up banner, a test's fake
            # process) must be on disk before the size is read.
            self._log.flush()
            chunk = _read_from(self._log, self._read)
        except (OSError, ValueError):
            # The log is closed once the backend has been stopped.
            return ""
        cut = chunk.rfind(b"\n")
        if cut < 0:
            return ""
        self._read += cut + 1
        return chunk[: cut + 1].decode("utf-8", "replace")

    def _drain(self):
        """The tail of the backend's log, bounded, for one failure message.

        Read through the underlying binary buffer rather than through the
        text wrapper. Once the log is longer than the bound the tail starts
        at an arbitrary byte, which lands in the middle of a multi-byte
        character often enough to matter -- a traceback with an em dash or a
        non-ASCII path in it is ordinary -- and decoding that strictly
        raises `UnicodeDecodeError` from inside the failure path, replacing
        the diagnostic this method exists to produce with a crash about
        reading it.
        """
        if self._log is None:
            return ""
        self._log.flush()
        raw = self._log.buffer
        raw.seek(0, 2)
        size = raw.tell()
        raw.seek(max(0, size - LOG_TAIL_BYTES))
        return raw.read().decode("utf-8", "replace")[-LOG_TAIL_CHARS:]

    def stop(self):
        """Terminate and reap only the child this instance started."""
        process, self.process = self.process, None
        try:
            if process is not None:
                if process.poll() is None:
                    try:
                        process.terminate()
                    except ProcessLookupError:
                        pass
                try:
                    process.wait(timeout=SHUTDOWN_GRACE)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        finally:
            if self._log is not None:
                self._log.close()
                self._log = None

    def __enter__(self):
        return self.start()

    def __exit__(self, *exception):
        self.stop()
        return False


# A page is a scan when a picture covers this much of it and the text
# layer holds fewer than this many characters: a stamped page number or a
# running header over a scanned page is not a text layer. Guards, not
# measurements; a blank page (no picture, no text) also counts as unread.
SCAN_IMAGE_AREA = 0.6
SCAN_MAX_CHARS = 200


def text_layer_report(pdf_path, page_range=None):
    """`(pages the text layer does not spell out, pages examined)`, from 1.

    pypdfium2 is the renderer the hybrid stack already carries, and reading
    what the page itself says is the only honest way to know whether the
    engine's own triage may skip it: a page with no characters, or a page
    that is one big picture with a few characters stamped on it, has
    nothing for the Java side to find, so it must be read by the models or
    not at all.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError as err:
        raise PipelineError(
            f"the OpenDataLoader hybrid stack is not installed "
            f'(pip install "opendataloader-pdf[hybrid]"): {err}',
            stage=STAGE,
        )
    if not hasattr(pdfium, "PdfDocument"):
        # An empty `pypdfium2` directory left behind by an uninstall imports
        # perfectly well and can do nothing; say that, rather than blaming
        # the PDF for it.
        raise PipelineError(
            "pypdfium2 is installed but unusable (no PdfDocument); reinstall "
            'it with pip install "opendataloader-pdf[hybrid]"',
            stage=STAGE,
        )
    ranges = parse_pages(page_range)
    missing = []
    examined = 0
    try:
        document = pdfium.PdfDocument(str(pdf_path))
    except Exception as err:
        raise PipelineError(
            f"{Path(pdf_path).name} could not be opened as a PDF: "
            f"{type(err).__name__}: {err}",
            stage=STAGE,
        )
    try:
        for number in range(1, len(document) + 1):
            if ranges and not any(start <= number <= end for start, end in ranges):
                continue
            examined += 1
            page = document[number - 1]
            textpage = page.get_textpage()
            try:
                text = textpage.get_text_bounded()
            finally:
                textpage.close()
            chars = len(text.strip())
            if not chars or (
                chars < SCAN_MAX_CHARS and _picture_share(page) >= SCAN_IMAGE_AREA
            ):
                missing.append(number)
            page.close()
    except Exception as err:
        raise PipelineError(
            f"{Path(pdf_path).name} could not be read page by page: "
            f"{type(err).__name__}: {err}",
            stage=STAGE,
        )
    finally:
        document.close()
    return missing, examined


def _picture_share(page):
    """How much of the page its page-level pictures cover, 0 to 1."""
    import pypdfium2.raw as raw

    width, height = page.get_size()
    if not width or not height:
        return 0.0
    covered = 0.0
    for obj in page.get_objects(max_depth=0):
        if obj.type != raw.FPDF_PAGEOBJ_IMAGE:
            continue
        left, bottom, right, top = obj.get_bounds()
        covered += max(0.0, min(right, width) - max(left, 0.0)) * max(
            0.0, min(top, height) - max(bottom, 0.0)
        )
    return min(1.0, covered / (width * height))


def _read_from(log, offset):
    """The bytes of `log` from `offset` to its end, without seeking.

    The child writes through a duplicate of this descriptor, and duplicates
    share one file offset, so a `seek` here would move where its next line
    lands. `pread` reads at an offset of its own; where it does not exist
    (Windows) the shared-offset read is what there is, and its window -- the
    child writing between the seek and the read -- costs a garbled
    diagnostic line at worst, never the conversion.
    """
    fd = log.fileno()
    size = os.fstat(fd).st_size
    if size <= offset:
        return b""
    pread = getattr(os, "pread", None)
    if pread is not None:
        return pread(fd, size - offset, offset)
    raw = log.buffer
    raw.seek(offset)
    return raw.read()


def backend_note(line):
    """The part of an engine's log line worth showing, or None.

    Measured on a real conversion (three pages, hybrid `full`): neither
    stream reports a page as it finishes -- the Java engine says how many
    pages go to the backend and then nothing for the whole conversion, and
    the backend says "Processing document" and, twenty-two seconds later,
    "Finished converting". So there is no page counter to show, and what is
    shown instead is the last of these lines, with its own logger's
    timestamp and level taken off.
    """
    line = (line or "").strip()
    if not line or ACCESS_LOG.match(line):
        return None
    match = LOG_LEVEL_PREFIX.match(line)
    if not match:
        return None
    return line[match.end() :].strip() or None


class _LineSink(io.TextIOBase):
    """Stands in for `sys.stdout` while the converter runs.

    `opendataloader_pdf` streams the Java engine's output to `sys.stdout`
    itself, writing to `sys.stdout.buffer` when there is one. This object
    deliberately has no `buffer`, so the runner takes its text branch and
    every line arrives here, as it is produced, instead of on the operator's
    terminal on top of the progress line.
    """

    def __init__(self, note):
        self._note = note
        self._partial = ""

    def writable(self):
        return True

    def write(self, text):
        self._partial += text
        while "\n" in self._partial:
            line, _, self._partial = self._partial.partition("\n")
            self._note(line)
        return len(text)

    def flush(self):
        pass


def _prose(chunk):
    """What is left of a chunk once markers and pictures are removed."""
    return IMAGE.sub(" ", COMMENT.sub(" ", chunk)).strip()


def blank_pages(markdown_text):
    """`(page numbers that carry no prose, whether any page does)`."""
    parts = PAGE_MARKER.split(markdown_text)
    any_prose = bool(_prose(parts[0]))
    blank = []
    for number, body in zip(parts[1::2], parts[2::2]):
        if _prose(body):
            any_prose = True
        else:
            blank.append(int(number))
    return blank, any_prose


def dense_pages(markdown_text, limit=PAGE_CHARS_LIMIT):
    """`[(page number, characters)]` for pages carrying more prose than fits."""
    parts = PAGE_MARKER.split(markdown_text)
    dense = []
    for number, body in zip(parts[1::2], parts[2::2]):
        chars = len(_prose(body))
        if chars > limit:
            dense.append((int(number), chars))
    return dense


def check_recognised_text(markdown_path, missing):
    """What the OCR pass actually returned for the pages that needed it.

    A conversion that sends every page to the models and still comes back
    with nothing but pictures has not read the book, and saying "completed"
    over that is the silent failure this pipeline refuses. A single page
    that came back empty is reported instead of refused: a plate with no
    words on it is a legitimate empty page.
    """
    if not missing:
        return []
    blank, any_prose = blank_pages(markdown_path.read_text(encoding="utf-8"))
    if not any_prose:
        raise PipelineError(OCR_EMPTY, stage=STAGE)
    silent = sorted(set(blank) & set(missing))
    if silent:
        print(OCR_EMPTY_PAGES.format(pages=", ".join(str(n) for n in silent)))
    return silent


def extract_pdf(
    bundle,
    pdf_path,
    *,
    pandoc,
    device="auto",
    page_range=None,
    ocr=False,
    backend_factory=HybridBackend,
    convert=None,
    progress=True,
):
    """Convert one PDF to Markdown in `bundle`, locally.

    The Java engine reads it; with `ocr` the model backend is started as
    well, and reads the pages that have no text layer (every picture on
    them included) or, when every page has one, only lays them out. Without
    `ocr` a page with no text layer is refused rather than skipped.
    """
    pdf = Path(pdf_path)
    if not pdf.is_file():
        raise PipelineError(f"no PDF at {pdf}", stage=STAGE)
    if not shutil.which("java"):
        raise PipelineError(JAVA_REQUIRED, stage=STAGE)
    converter = convert if convert is not None else _load_converter()
    pages = one_based_pages(page_range)

    bundle.create()
    _refuse_a_foreign_bundle(bundle)
    resolved = None
    if ocr:
        resolved, message = resolve_device(device)
        print(message)

    bundle.set_stage(STAGE, "running", parser=PARSER, device=resolved)
    staging = bundle.work_file("extraction")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    try:
        # Figures are taken out of the text layer before either engine
        # reads it: one that clips most of its own text away, or that
        # draws a chart, is put back as a picture, in a copy the engines
        # are handed instead of the original.
        sanitized, hidden = sanitize_pdf(pdf, staging, page_range)
        for line in _rasterized_lines(hidden):
            print(line)
        source = sanitized if sanitized is not None else pdf

        # Asked before the models are started, because it decides how they
        # are used: pages the engine cannot read itself have to be sent to
        # them -- and without them, refused rather than skipped.
        missing, examined = text_layer_report(source, page_range)
        if missing and not ocr:
            raise PipelineError(
                OCR_REQUIRED.format(
                    count=len(missing),
                    total=examined,
                    pages=", ".join(str(n) for n in missing),
                ),
                stage=STAGE,
            )
    except (PipelineError, KeyboardInterrupt):
        bundle.set_stage(STAGE, "failed", parser=PARSER, device=resolved)
        raise
    triage = TRIAGE_FULL if missing else TRIAGE_AUTO
    if missing:
        print(SCANNED_PAGES.format(count=len(missing), total=examined))

    scope = (PAGE_SCOPE if examined == 1 else PAGES_SCOPE).format(count=examined)
    if not ocr:
        engine = ENGINE_JAVA
    else:
        engine = (ENGINE_OCR if missing else ENGINE_LAYOUT).format(device=resolved)
    line = ProgressLine(
        EXTRACT_PROGRESS_LABEL.format(scope=scope, engine=engine),
        enabled=progress,
    )
    # Both engines' last words, for the failure message. `quiet=False` below
    # means their logs come here instead of being thrown away, so a
    # conversion that dies says what it said before it died.
    transcript = deque(maxlen=LOG_TAIL_LINES)

    def note(text):
        for raw in (text or "").splitlines():
            entry = backend_note(raw)
            if entry:
                transcript.append(entry)
                line.note(entry)

    # No backend at all without `ocr`: the Java engine needs nothing
    # started, downloaded or stopped.
    backend = backend_factory(resolved, ocr=bool(missing)) if ocr else _NoBackend()
    reader = getattr(backend, "new_output", None)
    finished = False
    # Started before the backend is: loading the models is part of the wait
    # -- and on a first run, downloading them is -- so the operator is owed
    # the same sign of life there as during the conversion itself.
    line.start()
    try:
        with ticking(line, poll=(lambda: note(reader())) if reader else None):
            with backend:
                try:
                    # The converter writes the Java engine's log to
                    # `sys.stdout` itself; this is where it is intercepted,
                    # so it feeds the progress line instead of scrolling
                    # past the operator.
                    with contextlib.redirect_stdout(_LineSink(note)):
                        converter(
                            str(source),
                            output_dir=str(staging),
                            format="markdown",
                            image_output="external",
                            image_dir=str(staging / "images"),
                            markdown_page_separator=PAGE_SEPARATOR,
                            pages=pages,
                            # Not quiet, and not printed either: quiet drops
                            # the engine's log stream, which is the only
                            # place this conversion says anything at all
                            # while it runs.
                            quiet=False,
                            **_backend_options(backend, triage),
                        )
                    finished = True
                except PipelineError:
                    raise
                except Exception as err:
                    raise PipelineError(
                        BACKEND_FAILED.format(detail=_failure_detail(err, transcript)),
                        stage=STAGE,
                    )
    except (PipelineError, KeyboardInterrupt):
        bundle.set_stage(STAGE, "failed", parser=PARSER, device=resolved)
        raise
    finally:
        line.finish(
            EXTRACT_DONE.format(
                scope=scope,
                engine=engine,
                elapsed=int(line.elapsed()),
            )
            if finished
            else None
        )

    try:
        markdown = _converted_markdown(staging, pdf)
        silent = check_recognised_text(markdown, missing)
        dense = dense_pages(markdown.read_text(encoding="utf-8"))
        for number, chars in dense:
            print(PAGE_TOO_DENSE.format(page=number, chars=chars))
        report = import_markdown(
            bundle, markdown, pandoc=pandoc, origin=pdf, stage=STAGE, kind="pdf"
        )
    except (PipelineError, KeyboardInterrupt):
        bundle.set_stage(STAGE, "failed", parser=PARSER, device=resolved)
        raise

    _write_provenance(
        bundle,
        pdf,
        resolved,
        device,
        pages,
        page_range,
        triage=triage,
        scanned=missing,
        examined=examined,
        hidden=hidden,
        ocr=ocr,
    )
    limitations = [
        "Extraction reading order, headings and diacritics are not verified "
        "by this pipeline; inspect source.md before translating."
    ]
    if silent:
        limitations.append(
            OCR_EMPTY_PAGES.format(pages=", ".join(str(n) for n in silent))
        )
    limitations.extend(_rasterized_lines(hidden))
    for number, chars in dense:
        limitations.append(PAGE_TOO_DENSE.format(page=number, chars=chars))
    bundle.add_limitations(limitations)
    return report


class _NoBackend:
    """Stands in for the model backend on a Java-only run."""

    url = None

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        return False


def _backend_options(backend, triage):
    """The converter's hybrid options: none at all on a Java-only run."""
    if backend.url is None:
        return {}
    return {
        "hybrid": HYBRID_MODE,
        "hybrid_mode": triage,
        "hybrid_url": backend.url,
        # Never on: the Java-only fallback drops the OCR this run exists
        # to get, and would do it silently.
        "hybrid_fallback": False,
    }


def _rasterized_lines(report):
    """What the operator is told about rasterized figures: one line per
    page hiding text, one line for every drawn figure together."""
    lines = []
    figures = {}
    for entry in report:
        hidden = sum(
            item["hidden"]
            for item in entry["objects"]
            if item["reason"] == "hidden-text"
        )
        if hidden:
            lines.append(
                HIDDEN_TEXT_RASTERIZED.format(page=entry["page"], hidden=hidden)
            )
        drawn = sum(1 for item in entry["objects"] if item["reason"] == "figure")
        if drawn:
            figures[entry["page"]] = drawn
    if figures:
        lines.append(
            FIGURES_RASTERIZED.format(
                pages=", ".join(str(page) for page in sorted(figures)),
                count=sum(figures.values()),
            )
        )
    return lines


def _failure_detail(err, transcript):
    """The exception, plus the last thing the engines said before it.

    `subprocess.CalledProcessError` says only that a command exited
    non-zero; the reason is in the output, which is now read for the
    progress line and would otherwise be dropped on the floor.
    """
    detail = f"{type(err).__name__}: {err}"
    if transcript:
        detail = f"{detail}; last output: {transcript[-1]}"
    return detail


def _load_converter():
    try:
        import opendataloader_pdf
    except ImportError as err:
        raise PipelineError(
            f"opendataloader-pdf is not installed "
            f'(pip install "opendataloader-pdf[hybrid]"): {err}',
            stage=STAGE,
        )
    return opendataloader_pdf.convert


def _converted_markdown(staging, pdf):
    candidate = staging / f"{pdf.stem}.md"
    if candidate.is_file():
        return candidate
    found = sorted(staging.rglob("*.md"))
    if not found:
        raise PipelineError(
            BACKEND_FAILED.format(detail="the conversion produced no Markdown"),
            stage=STAGE,
        )
    return found[0]


def _refuse_a_foreign_bundle(bundle):
    """A bundle already holding another parser's extraction is not reused."""
    path = bundle.work_file(EXTRACTION_JOB)
    if not path.is_file():
        return
    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if job.get("parser") and job["parser"] != PARSER:
        raise PipelineError(
            f"this bundle holds a {job['parser']} extraction; "
            f"use a new output directory",
            stage=STAGE,
        )


def _write_provenance(
    bundle,
    pdf,
    resolved,
    requested,
    pages,
    page_range,
    *,
    triage=TRIAGE_AUTO,
    scanned=(),
    examined=0,
    hidden=(),
    ocr=False,
):
    version = _installed_version()
    bundle.work.mkdir(parents=True, exist_ok=True)
    bundle.work_file(EXTRACTION_JOB).write_text(
        json.dumps(
            {
                "parser": PARSER,
                "pdf": str(pdf),
                "pdf_sha256": sha256_file(pdf),
                "device": resolved,
                "device_requested": requested,
                "pages": pages,
                "hybrid_mode": triage if ocr else None,
                "ocr": ocr,
                "version": version,
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    bundle.update_manifest(
        extraction={
            "provider": PARSER,
            "version": version,
            # The model backend runs only when asked (`--with-ocr`); the
            # Java engine alone reads a typed document.
            "hybrid": HYBRID_MODE if ocr else "off",
            "hybrid_mode": triage if ocr else None,
            "hybrid_fallback": False,
            "device": resolved,
            "device_requested": (requested or "auto") if ocr else None,
            "picture_description": False,
            # Pages the PDF itself could not spell out, and which therefore
            # had to be read by the OCR models.
            "pages_without_text_layer": list(scanned),
            "pages_examined": examined,
            # Whether the backend was started at all (`--with-ocr`); it
            # reads pictures only on pages with no text layer, so a typed
            # document's charts stay pictures.
            "ocr": ocr,
            "pages_read_by_ocr": list(scanned) if ocr else [],
            # Figures whose clipped-away text was taken out and which the
            # engines therefore saw as pictures: page, object, characters.
            "hidden_text_figures": list(hidden),
            "pdf": pdf.name,
            "pdf_sha256": sha256_file(pdf),
            "page_range": page_range,
            "page_range_sent": pages,
            "page_numbering": "1-based input, 1-based request",
            # Nothing was bought: the local parser has no provider charge.
            "cost_cents": None,
        }
    )
    bundle.set_stage(STAGE, "completed", parser=PARSER, device=resolved)


def _installed_version():
    try:
        from importlib.metadata import version

        return version("opendataloader-pdf")
    except Exception:
        return None
