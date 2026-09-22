"""Operator-facing text for the Markdown/EPUB bundle pipeline.

Every string a person reads on this path lives here, verbatim, so the
wording is owned in one place rather than scattered through the stages.
"""

STAGE_COMPLETE = "Stage complete: {stage}"
BILINGUAL_MARKDOWN_SAVED = "Bilingual Markdown saved: {path}"
BILINGUAL_EPUB_SAVED = "Bilingual EPUB saved: {path}"
STAGE_FAILED = "{stage} failed: {detail}"
PRESERVED_WITHOUT_TRANSLATION = "Preserved without translation: {kind} ({count})."
UNSUPPORTED_STRUCTURE = (
    "Unsupported Markdown structure: {kind} at {location}. "
    "Normalize the source before translation."
)
SETTINGS_CHANGED = (
    "Source or translation settings changed; start a new translation bundle."
)
BILINGUAL_EDITED = (
    "Bilingual Markdown was edited; export it or use a new output directory."
)
SUBMISSION_UNKNOWN = (
    "Extraction submission outcome is unknown; do not resubmit until the "
    "provider job is checked."
)
PANDOC_REQUIRED = (
    "Pandoc is required for EPUB export. Install it or provide --pandoc PATH."
)
# Pandoc 3.1.12 (February 2024) is the first release whose EPUB contents
# point at the headings (text/ch001.xhtml#chapter-one); earlier ones point
# at the files, which the navigation check refuses after the translation
# was paid for. Ubuntu 24.04 and Debian 13 apt ship older releases, so the
# message names the download page rather than the package manager.
PANDOC_MIN_VERSION = (3, 1, 12)
PANDOC_TOO_OLD = (
    "{found} is too old for EPUB export; Pandoc 3.1.12 or newer is required "
    "(its table of contents points at headings, older releases point at "
    "files). Install a current release from https://pandoc.org/installing.html "
    "or provide --pandoc PATH."
)
NAV_INVALID = "EPUB navigation is invalid: "
PDF_OPTIONS_INERT = (
    "--with-ocr, --no-gpu, --ocr-lang and --pages apply only to PDF input."
)
DEVICE_SELECTED = "OpenDataLoader device: {device}."
DEVICE_CPU_FALLBACK = "OpenDataLoader device: cpu (no supported accelerator detected)."
DEVICE_UNAVAILABLE = "Requested OpenDataLoader device is unavailable: {device}."
JAVA_REQUIRED = (
    "OpenDataLoader requires Java 11 or newer on PATH (a JRE is enough); "
    "install one, for example Temurin from https://adoptium.net/, and retry."
)
PDF_ROUTE_NOT_INSTALLED = (
    "the PDF route's packages are not installed; they are base dependencies "
    "of this package (pip install opendataloader-pdf pypdfium2 pillow, or "
    "reinstall it). Detail: {err}"
)
PDFIUM_UNUSABLE = (
    "pypdfium2 is installed but unusable (no PdfDocument); reinstall it with "
    "pip install --force-reinstall pypdfium2"
)
OCR_NOT_INSTALLED = (
    "the OCR runtime is not installed; --with-ocr needs the ocr extra: "
    'pip install "bbook_maker[ocr]" (from a checkout: pip install -r '
    "requirements-ocr.txt). Detail: {err}"
)
BACKEND_FAILED = "OpenDataLoader backend failed: {detail}"
SCANNED_PAGES = (
    "{count} of {total} selected pages have no text layer; every page is sent "
    "to the OCR backend."
)
OCR_REQUIRED = (
    "{count} of {total} selected pages have no text layer (page(s) {pages}); "
    "rerun with --with-ocr to read them with the OCR models."
)
OCR_EMPTY = (
    "OpenDataLoader produced no text for a document whose pages have no text "
    "layer; the OCR backend returned pictures only."
)
OCR_LANG_DEFAULT = (
    "The OCR models read English, Spanish, French and German unless --ocr-lang "
    "names the pages' languages (EasyOCR codes, comma-separated: ch_sim, ja, "
    "ko, ...); check source.md before translating."
)
OCR_LANG_EMPTY = "--ocr-lang needs at least one language code, for example ch_sim,en"
OCR_EMPTY_PAGES = (
    "Warning: no text was recognised on page(s) {pages}; check source.md "
    "before translating."
)
HIDDEN_TEXT_RASTERIZED = (
    "Page {page}: a figure carrying {hidden} characters of clipped-away text "
    "was rasterized; the reading edition shows it as a picture."
)
FIGURE_KEPT = (
    "Page {page}: a figure hiding {hidden} characters of clipped-away text "
    "drew nothing on its own and was left in place; that text may reach the "
    "extraction, so read source.md for that page."
)
FIGURES_RASTERIZED = (
    "Page(s) {pages}: {count} vector figure(s) rasterized; the reading edition "
    "shows them as pictures instead of their labels."
)
SELECTION_HEADING_ADDED = (
    "Page {page}: the selection starts inside a section, so a heading "
    '"Page {page}" was added above its prose; the table of contents needs '
    "one there. Rename it in source.md before translating if you like."
)
PAGE_TOO_DENSE = (
    "Warning: page {page} extracted {chars} characters, several times what a "
    "printed page holds; inspect source.md before translating."
)

# Progress. The line is rewritten in place on a terminal and printed every
# ten seconds into a log, so it says the same thing either way: what is
# running, how long it has been running, and the last thing the engine or
# the OCR backend said for itself.
PROGRESS_LINE = "{label}, {elapsed}s"
PROGRESS_LINE_DETAIL = "{label}, {elapsed}s - {detail}"
EXTRACT_PROGRESS_LABEL = "Extracting PDF: {scope}, {engine}"
EXTRACT_DONE = "PDF extracted: {scope}, {engine}, {elapsed}s."
# Which engines are reading: the Java engine alone, or the model backend
# with it -- reading pages nobody typed, or only laying out pages that
# spell themselves out.
ENGINE_JAVA = "Java engine"
ENGINE_OCR = "OCR on {device}"
ENGINE_LAYOUT = "layout models on {device}"
PAGE_SCOPE = "{count} page"
PAGES_SCOPE = "{count} pages"

# The main CLI's --to-epub route. It has no --pandoc flag, so the refusal
# that names one would send its operator looking for an option that is not
# there.
PANDOC_ON_PATH = (
    "Pandoc is required for --to-epub. Install it and make sure pandoc is on PATH."
)
TO_EPUB_BUNDLE = "Working bundle: {path}"
TO_EPUB_COPY = "Bilingual EPUB saved beside the PDF: {path}"
TRANSLATION_REUSED = (
    "Translation reused: {path} (same source and settings; delete it to "
    "translate again)."
)

# Argument and subcommand help, as authored.
DESCRIPTION = "Create bilingual Markdown and a reflowable EPUB from PDF or Markdown."
HELP_IMPORT = "Import Markdown and its local images."
HELP_EXTRACT = "Extract Markdown and images from a PDF with OpenDataLoader."
HELP_WITH_OCR = (
    "Start the OCR backend (docling models): required for pages with no text "
    "layer, which are refused without it; on typed pages it adds table and "
    "layout detection, and reads no pictures. The default is the Java engine "
    "alone: no models, no download."
)
HELP_NO_GPU = (
    "With --with-ocr: run the models on the CPU even when an accelerator is "
    "available; the default detects one and falls back to CPU."
)
HELP_OCR_LANG = (
    "With --with-ocr: the languages the OCR models read on pages with no text "
    "layer, as EasyOCR codes, comma-separated (ch_sim,en; ja; ko); the default "
    "is en,es,fr,de."
)
HELP_PAGES = "PDF pages, numbered from 1; for example 1-20."
HELP_TRANSLATE = "Translate a prepared bundle with BBM."
HELP_EXPORT = "Build an EPUB from the bundle's bilingual Markdown without translation."
HELP_RUN = "Import or extract, translate once, and export both reading formats."
HELP_INPUT = "Source PDF or Markdown file."
HELP_BUNDLE = "Prepared book directory."
HELP_OUTPUT = "Output bundle directory."
HELP_PANDOC = "Pandoc executable; defaults to PATH lookup."
HELP_TITLE = "Book title; otherwise use source metadata or the filename."
HELP_LANGUAGE = "Target language tag for EPUB metadata."
HELP_TRAILING = "BBM translation options after --."
