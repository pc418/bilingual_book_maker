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

import os
import shutil
from pathlib import Path

from .bundle import Bundle, parse_ocr_lang, parse_pages
from .epub_export import export_epub
from .errors import PipelineError
from .messages import PANDOC_ON_PATH, PANDOC_REQUIRED, TO_EPUB_BUNDLE, TO_EPUB_COPY
from .preflight import find_pandoc
from .stages import device_for, prepare
from .translate import check_options, translate_bundle

BUNDLE_SUFFIX = "_book"
EPUB_SUFFIX = "_bilingual.epub"

# Options the pipeline answers for itself. Everything else the operator
# typed is a translation option and is handed to the translate stage
# untouched -- the model, the key, the language, --test, --use_context, the
# prompt, all of it.
OWNED_OPTIONS = ("--to-epub", "--pdf-ocr", "--with-ocr", "--no-gpu")
OWNED_VALUE_OPTIONS = ("--book_name", "--ocr-lang", "--pages", "--device")


def translation_argv(argv):
    """`argv` without the options this route owns.

    `--book_name` goes because the bundle names its own source file (the
    extracted Markdown, not the PDF); the others because they are this
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


def selection_stem(pdf_path, pages=None):
    """`<stem>` for the whole PDF, `<stem>_pages-6-7` for a page selection.

    A selection gets its own bundle and its own book: a chapter run must
    not overwrite the whole-book run beside it, and a rerun with the same
    selection must find its own extraction and translation to resume.
    """
    stem = Path(pdf_path).stem
    if pages:
        return f"{stem}_pages-{''.join(str(pages).split())}"
    return stem


def bundle_path(pdf_path, pages=None):
    pdf = Path(pdf_path)
    return pdf.parent / f"{selection_stem(pdf, pages)}{BUNDLE_SUFFIX}"


def epub_path(pdf_path, pages=None):
    pdf = Path(pdf_path)
    return pdf.parent / f"{selection_stem(pdf, pages)}{EPUB_SUFFIX}"


def pdf_to_epub(
    pdf_path,
    argv,
    *,
    device=None,
    pdf_ocr=False,
    ocr_lang=None,
    pages=None,
    formula_images=True,
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
    # All resolved before the PDF is opened: an unusable Pandoc, a
    # translation option or a page selection that does not parse must not
    # cost an extraction.
    try:
        executable = find_pandoc(pandoc)
    except PipelineError as err:
        if err.detail != PANDOC_REQUIRED:
            raise  # too old: the message already names the fix
        raise PipelineError(PANDOC_ON_PATH)
    options = check_options(translation_argv(argv))
    parse_pages(pages)  # a selection that does not parse is refused here too
    parse_ocr_lang(ocr_lang)  # and an empty language list

    bundle = Bundle(bundle_path(pdf, pages)).create()
    print(TO_EPUB_BUNDLE.format(path=bundle.root))
    prepare_stage(
        bundle,
        pdf,
        pandoc=executable,
        device=device_for(device),
        pages=pages,
        ocr=pdf_ocr,
        ocr_lang=ocr_lang,
        formula_images=formula_images,
        progress=not quiet,
    )
    translate_stage(bundle, options, pandoc=executable)
    built = export_stage(bundle, pandoc=executable)

    # Only now, with a validated book in the bundle: a copy made from a
    # failed export would put a broken EPUB under the name a reader opens.
    destination = epub_path(pdf, pages)
    # Through a sibling and a rename: a copy that dies halfway must not
    # leave a truncated file under the name a reader opens, and a previous
    # good book under that name survives until the new one is complete.
    partial = destination.with_name(destination.name + ".part")
    try:
        shutil.copyfile(built, partial)
        os.replace(partial, destination)
    except OSError as err:
        partial.unlink(missing_ok=True)
        raise PipelineError(f"could not save {destination}: {err}", stage="export")
    print(TO_EPUB_COPY.format(path=destination))
    return destination
