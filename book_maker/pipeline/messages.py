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
    "--pdf-ocr, --device, --ocr-lang and --pages apply only to PDF input."
)
DEVICE_SELECTED = "PDF extraction device: {device}."
DEVICE_CPU_FALLBACK = "PDF extraction device: cpu (no supported accelerator detected)."
# Two refusals, not one. docling reports both of these as the same
# unavailable-device error, but they need opposite answers from the
# operator: one is a reinstall, the other is a machine that has no such
# accelerator and never will.
DEVICE_NO_CUDA_BUILD = (
    "--device cuda was asked for, but the installed PyTorch is a CPU-only "
    "build. Reinstall through the CUDA route; see docs/installation-pdf.md."
)
DEVICE_UNAVAILABLE = (
    "--device {device} was asked for, but this machine has no {device} "
    "accelerator available. Use --device cpu to run on the processor, or "
    "--device auto to take whatever is here."
)
# The install line names the checkout route FIRST and on purpose. The
# published bbook_maker carries no `pdf` extra yet, and pip answers a
# missing extra with a warning and a successful install of the release
# without it -- so an operator told to run that command would land back
# on this very message, having changed nothing. Naming it as the thing
# that does not work is what breaks the loop.
PDF_ROUTE_NOT_INSTALLED = (
    "reading a PDF needs the pdf extra, which is not installed. From a "
    "checkout: pip install -r requirements-pdf-gpu.txt, or "
    "requirements-pdf-cpu.txt on Linux without an NVIDIA GPU (the other "
    "file downloads about 3 GB of CUDA there). The published package does "
    'not carry this route yet, so pip install "bbook_maker[pdf]" will only '
    "warn about the unknown extra and install the release without it. Every "
    "case, per platform, in docs/installation-pdf.md. Detail: {err}"
)
PDFIUM_UNUSABLE = (
    "pypdfium2 is installed but unusable (no PdfDocument); reinstall it with "
    "pip install --force-reinstall pypdfium2"
)
BACKEND_FAILED = "PDF extraction failed: {detail}"
SCANNED_PAGES = (
    "{count} of {total} selected pages have no text layer; they are read by "
    "the OCR models."
)
OCR_REQUIRED = (
    "{count} of {total} selected pages have no text layer (page(s) {pages}); "
    "rerun with --pdf-ocr on to read them with the OCR models."
)
EXTRACTION_EMPTY = (
    "The parser returned no text for this PDF; there is nothing to translate. "
    "If its pages are scans, rerun with --pdf-ocr."
)
OCR_EMPTY = (
    "The parser produced no text for a document whose pages have no text "
    "layer; the OCR pass returned pictures only."
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
SELECTION_HEADING_ADDED = (
    "Page {page}: the selection starts inside a section, so a heading "
    '"Page {page}" was added above its prose; the table of contents needs '
    "one there. Rename it in source.md before translating if you like."
)
PAGES_SPAN_CONVERTED = (
    "The page selection is not one run of pages, so pages {span} were read "
    "and the ones outside the selection dropped afterwards; a single range "
    "reads fewer pages."
)
# Display formulas. docling finds the equation and does not read it, so
# the region is cropped from the page and kept as a picture; the wording
# says plainly that the equations are not translated, because a reader of
# a bilingual maths book will notice and should not have to guess why.
FORMULA_IMAGES = (
    "Display formulas kept as images: {count}. The parser does not read "
    "equations, so each one is cropped from the page; the prose around them "
    "is translated, the equations are not."
)
FORMULA_REGION_OVERSIZE = (
    "Warning: a formula region on page {page} covers {share}% of the page, "
    "which is a layout mistake rather than an equation; it was left as a "
    "placeholder instead of replacing the page with a picture of itself."
)
FORMULA_UNPLACEABLE = (
    "Warning: a formula on page {page} has no usable position on the page, "
    "so its placeholder is left where it is."
)
FORMULA_COUNT_MISMATCH = (
    "Warning: the parser found {regions} undecoded formula(s) but the "
    "Markdown carries {found} placeholder(s). Every equation was left as a "
    "placeholder rather than risk putting one in the wrong place; please "
    "report this together with the PDF."
)
HELP_NO_FORMULA_IMAGES = (
    "Leave display formulas as <!-- formula-not-decoded --> placeholders "
    "instead of cropping each one from the page as an image. The parser "
    "never reads equations, so without the images the mathematics is "
    "missing from the book entirely."
)
PAGE_TOO_DENSE = (
    "Warning: page {page} extracted {chars} characters, several times what a "
    "printed page holds; inspect source.md before translating."
)

# Progress. The line is rewritten in place on a terminal and printed every
# ten seconds into a log, so it says the same thing either way: what is
# running, how long it has been running, and the last thing the parser
# said for itself.
PROGRESS_LINE = "{label}, {elapsed}s"
PROGRESS_LINE_DETAIL = "{label}, {elapsed}s - {detail}"
EXTRACT_PROGRESS_LABEL = "Extracting PDF: {scope}, {engine}"
EXTRACT_DONE = "PDF extracted: {scope}, {engine}, {elapsed}s."
# What the models are doing: laying out and reading tables on pages that
# spell themselves out, or additionally reading pages nobody typed.
ENGINE_LAYOUT = "layout and table models on {device}"
ENGINE_OCR = "OCR and layout models on {device}"
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
HELP_EXTRACT = "Extract Markdown and images from a PDF with docling."
HELP_PDF_OCR = (
    "Read pages that carry no text layer with the OCR models; such pages are "
    "refused without it. Off by default: a born-digital PDF is already "
    "readable, and OCR costs several times the time without changing what is "
    "read. Layout and table detection run either way."
)
HELP_DEVICE = (
    "Which processor the extraction models run on: auto (detect, falling back "
    "to the CPU), cpu, cuda, mps or xpu. CPU is fully supported and produces "
    "the same output; it is slower."
)
HELP_OCR_LANG = (
    "With --pdf-ocr on: the languages the OCR models read on pages with no "
    "text layer, as EasyOCR codes, comma-separated (ch_sim,en; ja; ko); the "
    "default is en,es,fr,de."
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
