"""Which stage a source file needs, and whether it has already had it.

One copy of this decision, because there are now two front doors to the same
pipeline: the staged harness in `tools/pdf_to_book.py`, and `--to-epub` on
the main CLI. A second copy would be a second set of resume rules, and the
one that drifted would silently re-extract a book somebody had edited.
"""

from pathlib import Path

from .bundle import sha256_file
from .errors import PipelineError
from .importer import import_markdown
from .messages import PDF_OPTIONS_INERT, STAGE_COMPLETE

MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdown"}
PDF_SUFFIXES = {".pdf"}

# The one parser a PDF is read with. Spelled here rather than imported so
# that a Markdown import never touches the adapter; `book_maker.pipeline.
# opendataloader.PARSER` is the same string, and a test holds them equal.
PDF_PARSER = "opendataloader"


def source_kind(path):
    suffix = Path(path).suffix.lower()
    if suffix in PDF_SUFFIXES:
        return "pdf"
    if suffix in MARKDOWN_SUFFIXES:
        return "markdown"
    raise PipelineError(
        f"{path} is neither a PDF nor a Markdown file; this harness reads "
        f"{', '.join(sorted(PDF_SUFFIXES | MARKDOWN_SUFFIXES))}"
    )


def check_pdf_options(kind, options):
    """The device for a PDF run, and a refusal when it cannot apply.

    A Markdown import reads no PDF, so `--no-gpu` and `--pages` have nothing
    to act on there. Accepting them silently would let an operator believe a
    page selection or a device was honoured when the file they handed in
    never went near the parser, so typing either with Markdown is an error
    rather than a no-op.
    """
    no_gpu = getattr(options, "no_gpu", False)
    pages = getattr(options, "pages", None)
    if kind != "pdf" and (no_gpu or pages):
        raise PipelineError(PDF_OPTIONS_INERT)
    return device_for(no_gpu)


def device_for(no_gpu):
    """`cpu` when the operator said so, otherwise docling's own detection."""
    return "cpu" if no_gpu else "auto"


def already_prepared(bundle, input_path, parser, pages):
    """Whether this bundle already holds this input, prepared this way.

    A second run over a finished bundle must not buy the extraction again,
    and must not overwrite a `source.md` somebody edited between the two
    runs -- editing it is the whole reason the stage is separate.
    """
    if not bundle.manifest_path.is_file():
        return False
    manifest = bundle.read_manifest()
    stages = manifest.get("stages") or {}
    done = [
        name
        for name in ("import", "extract")
        if (stages.get(name) or {}).get("status") == "completed"
    ]
    if not done:
        return False
    source = manifest.get("source") or {}
    if source.get("origin_sha256") != sha256_file(input_path):
        return False
    extraction = manifest.get("extraction") or {}
    if done == ["extract"]:
        if extraction.get("provider") != parser:
            return False
        if (extraction.get("page_range") or None) != (pages or None):
            return False
    return done[0]


def prepare(bundle, input_path, *, pandoc, device=None, pages=None, progress=True):
    """Import or extract, chosen by the input's suffix alone.

    The adapter is imported here rather than at the top of the file so a
    Markdown import never pulls in the PDF parser, its models or its
    process management for a file it is not going to read.
    """
    kind = source_kind(input_path)
    finished = already_prepared(
        bundle, input_path, PDF_PARSER if kind == "pdf" else None, pages
    )
    if finished:
        print(STAGE_COMPLETE.format(stage=finished))
        return None
    if kind == "markdown":
        return import_markdown(bundle, input_path, pandoc=pandoc)
    from .opendataloader import extract_pdf

    return extract_pdf(
        bundle,
        input_path,
        pandoc=pandoc,
        device=device or "auto",
        page_range=pages,
        progress=progress,
    )
