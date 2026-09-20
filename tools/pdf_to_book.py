#!/usr/bin/env python3
"""Staged harness for the PDF/Markdown -> bilingual Markdown + EPUB bundle.

Development tooling, not a stable interface: the main CLI gains no flags
from this file. Each stage writes its artifacts into one bundle directory
and records what it did in the manifest, so a failure downstream never
costs the work upstream.

    tools/pdf_to_book.py import  INPUT.md  --output BUNDLE
    tools/pdf_to_book.py extract INPUT.pdf --output BUNDLE
    tools/pdf_to_book.py translate BUNDLE -- --model gpt-5-mini --language zh-hans
    tools/pdf_to_book.py export  BUNDLE
    tools/pdf_to_book.py run INPUT --output BUNDLE -- <translation options>

Everything after a bare `--` is handed to the existing BBM translation
command line untouched, except for the few options this harness owns.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from book_maker.pipeline import messages  # noqa: E402
from book_maker.pipeline.bundle import Bundle, sha256_file  # noqa: E402
from book_maker.pipeline.epub_export import export_epub  # noqa: E402
from book_maker.pipeline.errors import PipelineError  # noqa: E402
from book_maker.pipeline.importer import import_markdown  # noqa: E402
from book_maker.pipeline.preflight import find_pandoc  # noqa: E402
from book_maker.pipeline.translate import check_options, translate_bundle  # noqa: E402

MARKDOWN_SUFFIXES = {".md", ".markdown", ".mdown"}
PDF_SUFFIXES = {".pdf"}

# The one parser a PDF is read with. Spelled here rather than imported so
# that a Markdown import never touches the adapter; `book_maker.pipeline.
# opendataloader.PARSER` is the same string, and a test holds them equal.
PDF_PARSER = "opendataloader"
PDF_DEVICES = ("auto", "cpu", "cuda", "mps", "xpu")


def split_trailing(argv):
    """`(harness arguments, BBM options)` around the first bare `--`.

    argparse cannot be trusted with this: REMAINDER swallows the wrong
    tokens as soon as an option before it takes a value.
    """
    argv = list(argv)
    if "--" in argv:
        index = argv.index("--")
        return argv[:index], argv[index + 1 :]
    return argv, []


def build_parser():
    parser = argparse.ArgumentParser(
        prog="pdf_to_book.py",
        description=messages.DESCRIPTION,
        allow_abbrev=False,
    )
    parser.add_argument("--pandoc", default=None, help=messages.HELP_PANDOC)
    sub = parser.add_subparsers(dest="command", required=True)

    importer = sub.add_parser("import", help=messages.HELP_IMPORT, allow_abbrev=False)
    importer.add_argument("input", help=messages.HELP_INPUT)
    importer.add_argument("--output", required=True, help=messages.HELP_OUTPUT)

    extract = sub.add_parser("extract", help=messages.HELP_EXTRACT, allow_abbrev=False)
    extract.add_argument("input", help=messages.HELP_INPUT)
    extract.add_argument("--output", required=True, help=messages.HELP_OUTPUT)
    _add_pdf_options(extract)

    translate = sub.add_parser(
        "translate", help=messages.HELP_TRANSLATE, allow_abbrev=False
    )
    translate.add_argument("bundle", help=messages.HELP_BUNDLE)

    export = sub.add_parser("export", help=messages.HELP_EXPORT, allow_abbrev=False)
    export.add_argument("bundle", help=messages.HELP_BUNDLE)
    export.add_argument("--title", default=None, help=messages.HELP_TITLE)
    export.add_argument("--language", default=None, help=messages.HELP_LANGUAGE)

    run = sub.add_parser("run", help=messages.HELP_RUN, allow_abbrev=False)
    run.add_argument("input", help=messages.HELP_INPUT)
    run.add_argument("--output", required=True, help=messages.HELP_OUTPUT)
    _add_pdf_options(run)
    run.add_argument("--title", default=None, help=messages.HELP_TITLE)
    run.add_argument("--language", default=None, help=messages.HELP_LANGUAGE)
    return parser


def _add_pdf_options(parser):
    """The options that only mean something when the input is a PDF.

    Both default to None rather than to a value, so that typing one is
    distinguishable from leaving it out: on a Markdown input neither names
    anything that exists, and being ignored is not an answer.
    """
    parser.add_argument(
        "--pdf-device", choices=PDF_DEVICES, default=None, help=messages.HELP_PDF_DEVICE
    )
    parser.add_argument("--pages", default=None, help=messages.HELP_PAGES)


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

    A Markdown import reads no PDF, so `--pdf-device` and `--pages` have
    nothing to act on there. Accepting them silently would let an operator
    believe a page selection or a device was honoured when the file they
    handed in never went near the parser, so typing either with Markdown is
    an error rather than a no-op.
    """
    device = getattr(options, "pdf_device", None)
    pages = getattr(options, "pages", None)
    if kind != "pdf" and (device or pages):
        raise PipelineError(messages.PDF_OPTIONS_INERT)
    return device


def already_prepared(bundle, input_path, parser, pages):
    """Whether this bundle already holds this input, prepared this way.

    A second `run` over a finished bundle must not buy the extraction again,
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


def prepare(bundle, input_path, *, pandoc, device=None, pages=None):
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
        print(messages.STAGE_COMPLETE.format(stage=finished))
        return None
    if kind == "markdown":
        return import_markdown(bundle, input_path, pandoc=pandoc)
    from book_maker.pipeline.opendataloader import extract_pdf

    return extract_pdf(
        bundle,
        input_path,
        pandoc=pandoc,
        device=device or "auto",
        page_range=pages,
    )


def main(argv=None):
    own, trailing = split_trailing(sys.argv[1:] if argv is None else list(argv))
    options = build_parser().parse_args(own)
    command = options.command
    if trailing and command not in ("translate", "run"):
        print(
            messages.STAGE_FAILED.format(
                stage=command,
                detail=f"{messages.HELP_TRAILING} They belong to translate or run.",
            )
        )
        return 2

    try:
        # Resolved first, every time: the EPUB dependency must fail before
        # a PDF is submitted or a model is called, not after.
        pandoc = find_pandoc(options.pandoc)

        if command == "import":
            if source_kind(options.input) != "markdown":
                raise PipelineError(
                    f"{options.input} is not a Markdown file; use the extract "
                    f"command for a PDF",
                    stage="import",
                )
            bundle = Bundle(options.output).create()
            import_markdown(bundle, options.input, pandoc=pandoc)
        elif command == "extract":
            if source_kind(options.input) != "pdf":
                raise PipelineError(
                    f"{options.input} is not a PDF; use the import command",
                    stage="extract",
                )
            device = check_pdf_options("pdf", options)
            bundle = Bundle(options.output).create()
            prepare(
                bundle,
                options.input,
                pandoc=pandoc,
                device=device,
                pages=options.pages,
            )
        elif command == "translate":
            bundle = Bundle(options.bundle)
            translate_bundle(bundle, check_options(trailing), pandoc=pandoc)
        elif command == "export":
            bundle = Bundle(options.bundle)
            export_epub(
                bundle,
                pandoc=pandoc,
                title=options.title,
                language=options.language,
            )
            print(messages.STAGE_COMPLETE.format(stage="export"))
        elif command == "run":
            # The translation command line is validated first: `run` must
            # not pay for an extraction and then refuse the options that
            # were going to translate it.
            bbm_options = check_options(trailing)
            device = check_pdf_options(source_kind(options.input), options)
            bundle = Bundle(options.output).create()
            prepare(
                bundle,
                options.input,
                pandoc=pandoc,
                device=device,
                pages=options.pages,
            )
            translate_bundle(bundle, bbm_options, pandoc=pandoc)
            export_epub(
                bundle,
                pandoc=pandoc,
                title=options.title,
                language=options.language,
            )
            print(messages.STAGE_COMPLETE.format(stage="export"))
    except PipelineError as err:
        print(
            messages.STAGE_FAILED.format(stage=err.stage or command, detail=err.detail)
        )
        return 1
    except KeyboardInterrupt:
        print(messages.STAGE_FAILED.format(stage=command, detail="interrupted"))
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
