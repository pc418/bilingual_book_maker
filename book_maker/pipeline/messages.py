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
NAV_INVALID = "EPUB navigation is invalid: "
PDF_OPTIONS_INERT = "--no-gpu and --pages apply only to PDF input."
DEVICE_SELECTED = "OpenDataLoader device: {device}."
DEVICE_CPU_FALLBACK = "OpenDataLoader device: cpu (no supported accelerator detected)."
DEVICE_UNAVAILABLE = "Requested OpenDataLoader device is unavailable: {device}."
JAVA_REQUIRED = (
    "OpenDataLoader requires Java; install a supported Java runtime and retry."
)
BACKEND_FAILED = "OpenDataLoader backend failed: {detail}"
SCANNED_PAGES = (
    "{count} of {total} selected pages have no text layer; every page is sent "
    "to the OCR backend."
)
OCR_EMPTY = (
    "OpenDataLoader produced no text for a document whose pages have no text "
    "layer; the OCR backend returned pictures only."
)
OCR_EMPTY_PAGES = (
    "Warning: no text was recognised on page(s) {pages}; check source.md "
    "before translating."
)
HIDDEN_TEXT_RASTERIZED = (
    "Page {page}: a figure carrying {hidden} characters of clipped-away text "
    "was rasterized; the reading edition shows it as a picture."
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
EXTRACT_PROGRESS_LABEL = "Extracting PDF: {scope}, {engine} on {device}"
EXTRACT_DONE = "PDF extracted: {scope}, {engine} on {device}, {elapsed}s."
# What the backend is doing on that device: reading pages nobody typed, or
# only laying out pages that spell themselves out.
ENGINE_OCR = "OCR"
ENGINE_LAYOUT = "layout models"
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
HELP_NO_GPU = (
    "Run OCR on the CPU even when an accelerator is available; the default "
    "detects one and falls back to CPU."
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
