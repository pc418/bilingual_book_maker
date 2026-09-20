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

import json
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from .bundle import EXTRACTION_JOB, one_based_pages, parse_pages, sha256_file
from .errors import PipelineError
from .importer import import_markdown
from .messages import (
    BACKEND_FAILED,
    DEVICE_CPU_FALLBACK,
    DEVICE_SELECTED,
    DEVICE_UNAVAILABLE,
    JAVA_REQUIRED,
    OCR_EMPTY,
    OCR_EMPTY_PAGES,
    SCANNED_PAGES,
)

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
        """
        executable = shutil.which("opendataloader-pdf-hybrid")
        launcher = (
            [executable]
            if executable
            else [sys.executable, "-m", "opendataloader_pdf.hybrid_server"]
        )
        return launcher + [
            "--host",
            self.host,
            "--port",
            str(self.port),
            "--device",
            self.device,
            "--no-enrich-picture-description",
        ]

    def _http_health(self, url):
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=5) as response:
                return 200 <= response.status < 300
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def start(self):
        # A file avoids pipe backpressure while the converter is running.
        self._log = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
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


def text_layer_report(pdf_path, page_range=None):
    """`(pages with no extractable text, pages examined)`, numbered from 1.

    pypdfium2 is the renderer the hybrid stack already carries, and reading
    what the page itself says is the only honest way to know whether the
    engine's own triage may skip it: a page with no characters has nothing
    for the Java side to find, so it must be read by the models or not at
    all.
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
                page.close()
            if not text.strip():
                missing.append(number)
    except Exception as err:
        raise PipelineError(
            f"{Path(pdf_path).name} could not be read page by page: "
            f"{type(err).__name__}: {err}",
            stage=STAGE,
        )
    finally:
        document.close()
    return missing, examined


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
    backend_factory=HybridBackend,
    convert=None,
):
    """Convert one PDF to Markdown in `bundle`, locally, with OCR."""
    pdf = Path(pdf_path)
    if not pdf.is_file():
        raise PipelineError(f"no PDF at {pdf}", stage=STAGE)
    if not shutil.which("java"):
        raise PipelineError(JAVA_REQUIRED, stage=STAGE)
    converter = convert if convert is not None else _load_converter()
    pages = one_based_pages(page_range)

    bundle.create()
    _refuse_a_foreign_bundle(bundle)
    resolved, message = resolve_device(device)
    print(message)

    # Asked before the models are started, because it decides how they are
    # used: pages the engine cannot read itself have to be sent to them.
    missing, examined = text_layer_report(pdf, page_range)
    triage = TRIAGE_FULL if missing else TRIAGE_AUTO
    if missing:
        print(SCANNED_PAGES.format(count=len(missing), total=examined))

    bundle.set_stage(STAGE, "running", parser=PARSER, device=resolved)
    staging = bundle.work_file("extraction")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    backend = backend_factory(resolved)
    try:
        with backend:
            try:
                converter(
                    str(pdf),
                    output_dir=str(staging),
                    format="markdown",
                    image_output="external",
                    image_dir=str(staging / "images"),
                    markdown_page_separator=PAGE_SEPARATOR,
                    pages=pages,
                    hybrid=HYBRID_MODE,
                    hybrid_mode=triage,
                    hybrid_url=backend.url,
                    # Never on: the Java-only fallback drops the OCR this
                    # run exists to get, and would do it silently.
                    hybrid_fallback=False,
                    quiet=True,
                )
            except PipelineError:
                raise
            except Exception as err:
                raise PipelineError(
                    BACKEND_FAILED.format(detail=f"{type(err).__name__}: {err}"),
                    stage=STAGE,
                )
    except (PipelineError, KeyboardInterrupt):
        bundle.set_stage(STAGE, "failed", parser=PARSER, device=resolved)
        raise

    try:
        markdown = _converted_markdown(staging, pdf)
        silent = check_recognised_text(markdown, missing)
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
    )
    limitations = [
        "Extraction reading order, headings and diacritics are not verified "
        "by this pipeline; inspect source.md before translating."
    ]
    if silent:
        limitations.append(
            OCR_EMPTY_PAGES.format(pages=", ".join(str(n) for n in silent))
        )
    bundle.add_limitations(limitations)
    return report


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
                "hybrid_mode": triage,
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
            "hybrid": HYBRID_MODE,
            "hybrid_mode": triage,
            "hybrid_fallback": False,
            "device": resolved,
            "device_requested": requested or "auto",
            "picture_description": False,
            # Pages the PDF itself could not spell out, and which therefore
            # had to be read by the OCR models.
            "pages_without_text_layer": list(scanned),
            "pages_examined": examined,
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
