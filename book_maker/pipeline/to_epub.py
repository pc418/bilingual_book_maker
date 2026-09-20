"""`make_book.py --to-epub`: a PDF in, a bilingual EPUB beside it.

The same three stages the staged harness runs, in one command, over a bundle
that stays on disk. Nothing is reimplemented here: `prepare` extracts,
`translate_bundle` runs the real translation CLI over the bundle's Markdown,
`export_epub` packages it, and the resume rules are the bundle's own -- a
second run over a finished bundle re-uses the extraction and the translated
batches rather than paying for them again.

What this module owns is the two decisions the harness leaves to the
operator: where the bundle goes (`<name>_book/` beside the PDF, kept, so the
Markdown can be edited and the run resumed) and where the book goes
(`<name>_bilingual.epub` beside the PDF, the name every other BBM route
writes). The copy is made after the export has validated its navigation, so
the file next to the PDF is never a half-built one.
"""

import shutil
from pathlib import Path

from .bundle import Bundle
from .epub_export import export_epub
from .errors import PipelineError
from .messages import PANDOC_ON_PATH, TO_EPUB_BUNDLE, TO_EPUB_COPY
from .preflight import find_pandoc
from .stages import device_for, prepare
from .translate import check_options, translate_bundle

BUNDLE_SUFFIX = "_book"
EPUB_SUFFIX = "_bilingual.epub"

# Options the pipeline answers for itself. Everything else the operator
# typed is a translation option and is handed to the translate stage
# untouched -- the model, the key, the language, --test, --use_context, the
# prompt, all of it.
OWNED_OPTIONS = ("--to-epub", "--no-gpu")
OWNED_VALUE_OPTIONS = ("--book_name",)


def translation_argv(argv):
    """`argv` without the options this route owns.

    `--book_name` goes because the bundle names its own source file (the
    extracted Markdown, not the PDF); the other two because they are this
    route's own switches and the translation CLI has never heard of them.
    """
    kept = []
    skip = False
    for token in argv:
        if skip:
            skip = False
            continue
        if token in OWNED_OPTIONS:
            continue
        if token in OWNED_VALUE_OPTIONS:
            skip = True
            continue
        if any(token.startswith(f"{name}=") for name in OWNED_VALUE_OPTIONS):
            continue
        kept.append(token)
    return kept


def bundle_path(pdf_path):
    pdf = Path(pdf_path)
    return pdf.parent / f"{pdf.stem}{BUNDLE_SUFFIX}"


def epub_path(pdf_path):
    pdf = Path(pdf_path)
    return pdf.parent / f"{pdf.stem}{EPUB_SUFFIX}"


def pdf_to_epub(
    pdf_path,
    argv,
    *,
    no_gpu=False,
    quiet=False,
    pandoc=None,
    prepare_stage=prepare,
    translate_stage=translate_bundle,
    export_stage=export_epub,
):
    """Extract, translate and export one PDF. Returns the EPUB beside it.

    The stage functions are parameters so the routing can be tested without
    a PDF parser, a model or Pandoc; the defaults are the real stages and
    nothing else is injectable.
    """
    pdf = Path(pdf_path)
    # Both resolved before the PDF is opened: an unusable Pandoc or a
    # translation option that does not parse must not cost an extraction.
    try:
        executable = find_pandoc(pandoc)
    except PipelineError:
        raise PipelineError(PANDOC_ON_PATH)
    options = check_options(translation_argv(argv))

    bundle = Bundle(bundle_path(pdf)).create()
    print(TO_EPUB_BUNDLE.format(path=bundle.root))
    prepare_stage(
        bundle,
        pdf,
        pandoc=executable,
        device=device_for(no_gpu),
        progress=not quiet,
    )
    translate_stage(bundle, options, pandoc=executable)
    built = export_stage(bundle, pandoc=executable)

    # Only now, with a validated book in the bundle: a copy made from a
    # failed export would put a broken EPUB under the name a reader opens.
    destination = epub_path(pdf)
    shutil.copyfile(built, destination)
    print(TO_EPUB_COPY.format(path=destination))
    return destination
