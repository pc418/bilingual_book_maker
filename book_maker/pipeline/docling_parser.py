"""docling as the PDF parser.

One process, in this interpreter: docling's layout and table models read
the page and return a document, which is exported as Markdown with the
pictures written beside it. There is no second engine, no sidecar and no
port -- the 260921 evaluation retired the Java route and its hybrid
wrapper, which was docling behind a server that degraded its output.

The device decision is docling's own (`decide_device`), not a hardware
probe of ours: `auto` resolves to whatever it finds and falls back to CPU,
`cpu` forces CPU, and a named accelerator that is not there fails by name.
We split its one refusal into two, because a CPU-only PyTorch build and a
machine with no accelerator need opposite answers from the operator.

OCR is off unless asked for. The models lay out and read tables either
way; OCR is for pages that carry no text layer at all, and those pages are
refused rather than silently skipped when it is off.
"""

import contextlib
import json
import re
import shutil
import time
from collections import deque
from pathlib import Path

from .bundle import (
    EXTRACTION_JOB,
    one_based_pages,
    parse_ocr_lang,
    parse_pages,
    sha256_file,
)
from .errors import PipelineError
from .importer import import_markdown
from .messages import (
    BACKEND_FAILED,
    DEVICE_CPU_FALLBACK,
    DEVICE_NO_CUDA_BUILD,
    DEVICE_SELECTED,
    DEVICE_UNAVAILABLE,
    ENGINE_LAYOUT,
    ENGINE_OCR,
    EXTRACT_DONE,
    EXTRACT_PROGRESS_LABEL,
    OCR_LANG_DEFAULT,
    OCR_REQUIRED,
    PAGE_TOO_DENSE,
    PAGES_SCOPE,
    PAGES_SPAN_CONVERTED,
    PAGE_SCOPE,
    PDF_ROUTE_NOT_INSTALLED,
    SCANNED_PAGES,
    SELECTION_HEADING_ADDED,
)
from .pdf_common import (
    PAGE_MARKER,
    check_recognised_text,
    dense_pages,
    first_selected_page,
    heading_for_mid_section,
    text_layer_report,
)
from .progress import ProgressLine, ticking

STAGE = "extract"
PARSER = "docling"

DEVICES = ("auto", "cpu", "cuda", "mps", "xpu")

# What the export writes between two pages. docling emits this placeholder
# between pages and never before the first, so the numbered markers the
# rest of the pipeline reads are put in afterwards, by `_number_pages`.
PAGE_BREAK = "\x00bbm-page-break\x00"

# How many of the parser's log lines are kept for a failure message.
LOG_TAIL_LINES = 20

IMAGE_DIR = "images"
IMAGE_REF = re.compile(r"(!\[[^\]]*\]\()([^)]*)(\))")


def resolve_device(requested):
    """`(resolved, message)` for the device the models will actually use.

    Delegated to docling, which is the thing that will run them: `auto` is
    its detection, an unavailable accelerator is its refusal, and `cpu` is
    the one answer that needs no hardware at all.
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
        raise PipelineError(PDF_ROUTE_NOT_INSTALLED.format(err=err), stage=STAGE)
    try:
        resolved = decide_device(requested)
    except AcceleratorDeviceNotAvailableError:
        raise PipelineError(_unavailable(requested), stage=STAGE)
    # docling answers `cuda:0`; everything downstream takes the family.
    resolved = resolved.split(":", 1)[0]
    if requested == "auto" and resolved == "cpu":
        return resolved, DEVICE_CPU_FALLBACK
    return resolved, DEVICE_SELECTED.format(device=resolved)


def _unavailable(requested):
    """Which of the two CUDA refusals this is.

    docling raises the same error for a CPU-only PyTorch build and for a
    machine with no NVIDIA card, but the first is fixed by reinstalling
    and the second never is, so the operator is told which one happened.
    """
    if requested == "cuda":
        try:
            import torch

            if torch.version.cuda is None:
                return DEVICE_NO_CUDA_BUILD
        except Exception:
            pass
    return DEVICE_UNAVAILABLE.format(device=requested)


def _converter(device, ocr, languages):
    """docling's `DocumentConverter`, configured for one conversion."""
    try:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption
    except ImportError as err:
        raise PipelineError(PDF_ROUTE_NOT_INSTALLED.format(err=err), stage=STAGE)

    options = PdfPipelineOptions()
    # Layout and table structure run on every page: they are what this
    # parser is for, and `TableFormerMode.ACCURATE` is already the default.
    options.do_table_structure = True
    # Pictures are written as files and described by nobody: generated alt
    # text has been measured inventing content, and a translation would
    # then carry the invention.
    options.generate_picture_images = True
    options.do_ocr = bool(ocr)
    if ocr and languages:
        options.ocr_options.lang = list(languages)
    options.accelerator_options.device = device
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )


def _convert(pdf, *, out_dir, span, device, ocr, languages):
    """Markdown for `span` of `pdf`, with its pictures written to `out_dir`.

    The default seam: `extract_pdf(convert=...)` replaces this whole call,
    so a test never loads a model.
    """
    from docling_core.types.doc.base import ImageRefMode

    converter = _converter(device, ocr, languages)
    result = converter.convert(str(pdf), page_range=span)
    images = Path(out_dir) / IMAGE_DIR
    markdown = result.document.export_to_markdown(
        page_break_placeholder=PAGE_BREAK,
        image_mode=ImageRefMode.REFERENCED,
        image_dir=images,
    )
    return _relative_images(markdown, images)


def _relative_images(markdown, image_dir):
    """Picture references as paths beside `source.md`, not absolute ones.

    docling writes the directory it was given into every reference. A
    bundle has to survive being moved, and Pandoc resolves the reference
    against the Markdown file, so the directory is taken back off.
    """
    prefix = str(Path(image_dir)) + "/"

    def rewrite(match):
        target = match.group(2)
        if target.startswith(prefix):
            target = f"{IMAGE_DIR}/{target[len(prefix):]}"
        return f"{match.group(1)}{target}{match.group(3)}"

    return IMAGE_REF.sub(rewrite, markdown)


def _span(ranges):
    """The one run of pages a converter can be asked for, `(first, last)`."""
    if not ranges:
        return None
    return min(start for start, _ in ranges), max(end for _, end in ranges)


def _number_pages(markdown, first_page):
    """The export's page breaks as the numbered markers the pipeline reads.

    docling puts a break between two pages and never before the first, so
    the first page's marker is prepended and each break becomes the marker
    of the page that follows it.
    """
    number = first_page or 1
    out = [f"<!-- page {number} -->", ""]
    for segment in markdown.split(PAGE_BREAK):
        if out[-1] != "":
            out.append("")
        out.append(segment.strip("\n"))
        number += 1
        out.extend(["", f"<!-- page {number} -->", ""])
    # The loop leaves a marker for a page that does not exist.
    del out[-3:]
    return "\n".join(out).rstrip("\n") + "\n"


def _selected_only(markdown, ranges):
    """The Markdown with the pages outside a gapped selection removed."""
    parts = PAGE_MARKER.split(markdown)
    kept = [parts[0].strip("\n")] if parts[0].strip() else []
    for number, body in zip(parts[1::2], parts[2::2]):
        page = int(number)
        if any(start <= page <= end for start, end in ranges):
            kept.append(f"<!-- page {page} -->\n{body.strip(chr(10))}")
    return "\n\n".join(kept).rstrip("\n") + "\n"


def extract_pdf(
    bundle,
    pdf_path,
    *,
    pandoc,
    device="auto",
    page_range=None,
    ocr=False,
    ocr_lang=None,
    convert=None,
    progress=True,
):
    """Convert one PDF to Markdown in `bundle`, locally.

    docling's models read it. With `ocr` the pages that have no text layer
    are read by the OCR models as well; without it such a page is refused
    rather than skipped, because a page nobody can read is not a page that
    was translated.
    """
    pdf = Path(pdf_path)
    if not pdf.is_file():
        raise PipelineError(f"no PDF at {pdf}", stage=STAGE)
    converter = convert if convert is not None else _convert
    ranges = parse_pages(page_range)
    pages = one_based_pages(page_range)
    languages = parse_ocr_lang(ocr_lang)

    bundle.create()
    resolved, message = resolve_device(device)
    print(message)

    bundle.set_stage(STAGE, "running", parser=PARSER, device=resolved)
    staging = bundle.work_file("extraction")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    try:
        # Asked before the models are started, because it decides how they
        # are used: a page the PDF cannot spell out has to be read by the
        # OCR models -- and without them, refused rather than skipped.
        missing, examined = text_layer_report(pdf, page_range)
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

    if missing:
        print(SCANNED_PAGES.format(count=len(missing), total=examined))
        # The models are about to read these pages in whatever languages
        # they were given; an operator who gave none is told which.
        if not languages:
            print(OCR_LANG_DEFAULT)

    # A converter reads one run of pages. A selection with a gap in it is
    # read as the run that covers it and trimmed afterwards, so the flag
    # keeps working -- at the cost of reading the pages in the gap, which
    # the operator is told about rather than charged for silently.
    span = _span(ranges)
    gapped = bool(ranges) and len(ranges) > 1
    if gapped:
        print(PAGES_SPAN_CONVERTED.format(span=f"{span[0]}-{span[1]}"))

    scope = (PAGE_SCOPE if examined == 1 else PAGES_SCOPE).format(count=examined)
    engine = (ENGINE_OCR if missing else ENGINE_LAYOUT).format(device=resolved)
    line = ProgressLine(
        EXTRACT_PROGRESS_LABEL.format(scope=scope, engine=engine),
        enabled=progress,
    )
    # The parser's last words, for the failure message: a conversion that
    # dies says what it said before it died.
    transcript = deque(maxlen=LOG_TAIL_LINES)

    def note(text):
        for raw in (text or "").splitlines():
            entry = raw.strip()
            if entry:
                transcript.append(entry)
                line.note(entry)

    finished = False
    # Started before the models are loaded: on a first run they are
    # downloaded too, and the operator is owed the same sign of life
    # there as during the conversion itself.
    line.start()
    try:
        with ticking(line):
            try:
                with contextlib.redirect_stdout(_Sink(note)):
                    markdown = converter(
                        pdf,
                        out_dir=staging,
                        span=span,
                        device=resolved,
                        ocr=bool(ocr),
                        languages=languages,
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
            EXTRACT_DONE.format(scope=scope, engine=engine, elapsed=int(line.elapsed()))
            if finished
            else None
        )

    try:
        text = _number_pages(markdown, first_selected_page(page_range))
        if gapped:
            text = _selected_only(text, ranges)
        source = staging / "source.md"
        source.write_text(text, encoding="utf-8")
        silent = check_recognised_text(source, missing)
        headed = heading_for_mid_section(text, first_selected_page(page_range))
        if headed is not None:
            text = headed
            source.write_text(text, encoding="utf-8")
            print(SELECTION_HEADING_ADDED.format(page=first_selected_page(page_range)))
        dense = dense_pages(text)
        for number, chars in dense:
            print(PAGE_TOO_DENSE.format(page=number, chars=chars))
        report = import_markdown(
            bundle, source, pandoc=pandoc, origin=pdf, stage=STAGE, kind="pdf"
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
        scanned=missing,
        examined=examined,
        ocr=ocr,
        ocr_lang=languages,
    )
    limitations = [
        "Extraction reading order, headings and diacritics are not verified "
        "by this pipeline; inspect source.md before translating."
    ]
    if silent:
        from .messages import OCR_EMPTY_PAGES

        limitations.append(
            OCR_EMPTY_PAGES.format(pages=", ".join(str(n) for n in silent))
        )
    if missing and not languages:
        limitations.append(OCR_LANG_DEFAULT)
    if headed is not None:
        limitations.append(
            SELECTION_HEADING_ADDED.format(page=first_selected_page(page_range))
        )
    for number, chars in dense:
        limitations.append(PAGE_TOO_DENSE.format(page=number, chars=chars))
    bundle.add_limitations(limitations)
    return report


class _Sink:
    """stdout during a conversion, fed to the progress line one line at a time."""

    def __init__(self, note):
        self._note = note
        self._buffer = ""

    def write(self, text):
        self._buffer += text
        while "\n" in self._buffer:
            head, _, self._buffer = self._buffer.partition("\n")
            self._note(head)
        return len(text)

    def flush(self):
        if self._buffer:
            self._note(self._buffer)
            self._buffer = ""

    def isatty(self):
        return False


def _failure_detail(err, transcript):
    """Why the conversion died: the exception, and what was logged before it."""
    detail = f"{type(err).__name__}: {err}".strip()
    tail = [line for line in transcript if line]
    if tail:
        detail = f"{detail} (last log line: {tail[-1]})"
    return detail


def _write_provenance(
    bundle,
    pdf,
    resolved,
    requested,
    pages,
    page_range,
    *,
    scanned=(),
    examined=0,
    ocr=False,
    ocr_lang=None,
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
                "ocr": ocr,
                "ocr_lang": ocr_lang,
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
            "device": resolved,
            "device_requested": requested or "auto",
            "picture_description": False,
            # Pages the PDF itself could not spell out, and which therefore
            # had to be read by the OCR models.
            "pages_without_text_layer": list(scanned),
            "pages_examined": examined,
            "ocr": ocr,
            "pages_read_by_ocr": list(scanned) if ocr else [],
            # The languages the models were told to read, as given
            # (`--ocr-lang`); None means the engine's own default.
            "ocr_lang": ocr_lang,
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

        return version("docling")
    except Exception:
        return None
