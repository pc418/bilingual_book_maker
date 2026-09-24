# PDF to bilingual EPUB

This route is experimental. It has been checked on arXiv papers and a set of books, scans and browser-saved pages, not on every PDF shape. Issues and pull requests are welcome; attach the PDF if it can be shared, or the page of `source.md` that came out wrong.

## What it does

`--to-epub` reads the PDF with [docling](https://github.com/docling-project/docling)'s layout and table models into Markdown, translates that Markdown with the Markdown loader, and has Pandoc build a reflowable bilingual EPUB. Every paragraph is followed by its translation, and the table of contents follows the headings. Heading levels are read from the page itself: numbering first (`1.`, `1.1`, `I.`, `A.`), then font size and weight. Figures stay pictures, and each display formula is cropped from the page as a picture and placed where it stood. Pages with no text layer, a scan, are read by the OCR models only when you pass `--pdf-ocr`.

Everything lives in a working folder beside the PDF, `<name>_book/`: `source.md` (the extraction), `images/`, `book_bilingual.md` (the translation) and a manifest. The finished book is copied out as `<name>_bilingual.epub`. Rerunning the same command reuses the extraction and a finished translation, so you can stop, read `source.md`, fix a heading, and go on without paying twice. A PDF is a page description, not a document: it stores glyphs at positions and knows nothing of paragraphs or headings, so every extractor guesses the structure back. A heading one level off or a table that arrives as prose is the format showing through, and a minute's edit in `source.md`.

![A translated arXiv paper open in Apple Books: the contents list on the left, Chinese text beside the original figure and caption](../img/pdf_reading_edition.jpg)

*An arXiv paper after the route, open in Apple Books. The contents come from the paper's headings.*

## Setup

1. Install the route from a checkout: [PDF extra](../installation-pdf.md). It needs the PDF packages (docling, PyTorch) and Pandoc **3.1.12 or newer** on PATH. No Java.
2. The models (about 500 MB) download on the first run.
3. Read the first two pages before anything longer, and read `source.md`, the headings at least, before you pay for the full translation. They become the table of contents.

The route's flags:

| flag | what it does |
|---|---|
| `--to-epub` | Take this route. |
| `--pdf-ocr` | Read pages that have no text layer. Off by default: a born-digital PDF is already readable, and OCR costs several times the time without changing what is read. Layout, heading and table detection run either way. |
| `--ocr-lang LANGS` | With `--pdf-ocr`: the languages the OCR engine reads, in its own codes. With the shipped requirements the engine is rapidocr (`ch`, `en`, `latin`). easyocr (`ch_sim`, `ja`, `ko`) and ocrmac (`zh-Hans`, `ja-JP`) are used when installed. A BCP-47 tag behind `iso:` (`iso:ja`, `iso:zh-Hant`) works on every engine, so it is the safe choice when you do not know which one will run. rapidocr reads one language per run and uses the first. The run prints the engine and languages it used. |
| `--device auto\|cpu\|cuda\|mps\|xpu` | Where the models run. `auto` detects CUDA or MPS and falls back to the CPU. The CPU gives the same text, only slower. |
| `--pages 12-30` | Only these pages, numbered from 1 (`1,3,5-7` works too). The book gets its own names: `<name>_pages-12-30_book/`, `<name>_pages-12-30_bilingual.epub`. |
| `--no-formula-images` | Leave display formulas as `<!-- formula-not-decoded -->` placeholders. Almost never what you want: the parser never reads equations, so without the pictures the mathematics is missing. |

Every Markdown-loader flag works unchanged: `--use_context session` (recommended), `--glossary`, `--parallel-workers` (not with a session), `--test`. The full list is on [PDF](../formats/pdf.md).

## Recommended commands

Always start with two pages and a few translated blocks. It extracts only those pages and costs almost nothing:

```bash
python make_book.py \
  --book_name book.pdf \
  --to-epub \
  --pages 1-2 \
  --test
```

Then open `book_pages-1-2_book/source.md` and read it. When it looks right, run the command for your document below.

### By document type

=== "Novel"

    A typed novel has few headings and no tables. Leave out front matter you do not want with `--pages`.

    ```bash
    python make_book.py \
      --book_name novel.pdf \
      --to-epub \
      --language zh-hans \
      --use_context session \
      --quiet
    ```

    Check the chapter headings in `source.md` first: a novel often sets chapters without numbering, and the route then relies on font size alone.

=== "Textbook with tables and formulas"

    Tables are detected without OCR. Display formulas become pictures by default; the prose around them is translated, the equations are not.

    ```bash
    python make_book.py \
      --book_name textbook.pdf \
      --to-epub \
      --pages 12-30 \
      --language zh-hans \
      --use_context session \
      --glossary terms.txt
    ```

    Translate a chapter at a time with `--pages`; each range gets its own book and never overwrites another. Inline mathematics inside a sentence is not a formula region and is not covered: it arrives as whatever the text layer or OCR made of it.

=== "Paper"

    A paper extracts into many short blocks; a session gives each block the text before it.

    ```bash
    python make_book.py \
      --book_name paper.pdf \
      --to-epub \
      --language zh-hans \
      --use_context session
    ```

    Heading levels were exact on 187 of 195 headings across 20 arXiv papers. Leave out the bibliography with `--pages` if you do not want to pay for it.

=== "Scanned book"

    The run refuses a page with no text layer until you pass `--pdf-ocr`, so you never need to guess.

    ```bash
    python make_book.py \
      --book_name scan.pdf \
      --to-epub \
      --pdf-ocr \
      --language zh-hans \
      --use_context session
    ```

    rapidocr's default reads Chinese and English. A scan in another script needs `--ocr-lang`; the first use of a language downloads its model.

=== "Chinese scan"

    Name the language the OCR engine should read. An `iso:` tag works whichever engine docling picks; use `iso:zh-Hant` for traditional characters.

    ```bash
    python make_book.py \
      --book_name scan.pdf \
      --to-epub \
      --pdf-ocr \
      --ocr-lang iso:zh-Hans \
      --language en \
      --use_context session
    ```

    Horizontal text reads well. **Vertical text** (traditional books set top to bottom, right to left) comes out with its columns in the wrong order: measured on one page, the local engine's character error rate was 0.905. Check `source.md` before you translate a vertical scan. See [Why a vision model reads scans better](../evaluation/pdf-ocr-llm-vs-local.md).

### By system

=== "macOS (Apple silicon)"

    Install natively; `--device auto` finds MPS.

    ```bash
    python make_book.py \
      --book_name paper.pdf \
      --to-epub \
      --use_context session
    ```

    The run prints `PDF extraction device: mps.` Docker cannot reach MPS on a Mac.

=== "Linux with NVIDIA"

    Install `requirements-pdf-gpu.txt`; `--device auto` finds CUDA.

    ```bash
    python make_book.py \
      --book_name paper.pdf \
      --to-epub \
      --use_context session
    ```

    The run prints `PDF extraction device: cuda.` Name it to be sure; the run then refuses, with the reason, if it cannot use it:

    ```bash
    python make_book.py \
      --book_name paper.pdf \
      --to-epub \
      --device cuda \
      --use_context session
    ```

=== "CPU only"

    Install `requirements-pdf-cpu.txt` on Linux; on Windows `requirements-pdf-gpu.txt` already gives the CPU build. The text is the same as on a GPU; extraction is slower. Measured on one two-page typewriter scan with OCR, on a 16 GB Apple silicon laptop: identical text, 26.3 s on the CPU against 10.6 s on MPS.

    ```bash
    python make_book.py \
      --book_name paper.pdf \
      --to-epub \
      --device cpu \
      --use_context session
    ```

=== "Docker"

    The `pdf` image carries Pandoc and the PDF packages. Keep the models in a volume.

    ```bash
    docker run --rm \
      -v "$PWD":/book \
      -v bbm-models:/root/.cache \
      -e OPENAI_API_KEY \
      ghcr.io/yihong0618/bilingual_book_maker:pdf \
      --book_name /book/paper.pdf \
      --to-epub \
      --use_context session
    ```

    Add `--gpus all` on Linux or Windows (WSL2) with an NVIDIA card; the GPU works only on the amd64 image. See [Docker](../docker.md).

## What can go wrong

Every failure on this route prints one line starting with `Error:`, before anything is paid for where it can. These are the lines, and what to do.

### Before extraction

- **`Pandoc is required for --to-epub. Install it and make sure pandoc is on PATH.`** Install Pandoc 3.1.12 or newer from [pandoc.org](https://pandoc.org/installing.html).
- **`pandoc 3.1.3 is too old for EPUB export; Pandoc 3.1.12 or newer is required …`** Your Pandoc came from apt (Ubuntu 24.04 ships 3.1.3, Debian 13 ships 3.1.11). Install the release. The line also mentions `--pandoc PATH`; that option belongs to `tools/pdf_to_book.py`, not to `make_book.py`.
- **`reading a PDF needs the pdf extra, which is not installed.`** Do step 3 of [PDF extra](../installation-pdf.md). Not `pip install "bbook_maker[pdf]"`.
- **`--device cuda was asked for, but the installed PyTorch is a CPU-only build.`** Reinstall through the CUDA route. **`… but this machine has no cuda accelerator available.`** Use `--device cpu` or `--device auto`.
- **`--parallel-workers is not supported with --use_context session …`** Choose one.

### During extraction

- **`N of M selected pages have no text layer (page(s) …); rerun with --pdf-ocr on to read them with the OCR models.`** The PDF (or part of it) is a scan. Add `--pdf-ocr`.
- **`No --ocr-lang given: the OCR engine reads its own default languages, which may not be the pages'; …`** Then **`OCR engine: rapidocr (docling's choice on this install), languages: the engine's defaults.`** Check `source.md`. If the scan is not in Chinese or English, rerun with `--ocr-lang`; the bundle is read again.
- **`The parser produced no text for a document whose pages have no text layer; the OCR pass returned pictures only.`** or **`Warning: no text was recognised on page(s) …`** The engine could not read the script. Rerun with `--ocr-lang` for the page's language.
- **`The parser returned no text for this PDF; there is nothing to translate. If its pages are scans, rerun with --pdf-ocr.`** As it says.
- **`Warning: page N extracted C characters, several times what a printed page holds; inspect source.md before translating.`** Something on that page, usually a figure, carries far more text than it shows. A page whose Markdown is thousands of lines is junk, not a long page. Remove it from `source.md` or leave the page out with `--pages`.
- **A scanned page reads as garbage although the scan is clean.** Scans from the Internet Archive and ABBYY FineReader often use JBIG2 image masks, which docling-parse draws as a smear (docling issue #4329). The build this site was written against does not work around it. Newer builds render such pages with pypdfium2 and print `The PDF carries JBIG2 image masks, which docling-parse renders wrongly …`. See [Why docling-parse stays](../evaluation/pdf-page-render-backend.md).

### After extraction, before translation

- **`The document does not open with a top-level heading, so a heading "<name>" was added above its text; …`** The EPUB's contents need a level-1 heading at the start. Rename it in `source.md` before translating if you like.
- **`Page 12: the selection starts inside a section, so a heading "Page 12" was added above its prose; …`** The same, for a `--pages` range that starts mid-section.
- **`The page selection is not one run of pages, so pages 1-7 were read and the ones outside the selection dropped afterwards; …`** A range with a gap reads everything it spans. A single range reads fewer pages.
- **`Display formulas kept as images: N. …`** Information: the equations are pictures and are not translated.
- **`Warning: a formula region on page N covers S% of the page, which is a layout mistake rather than an equation; …`** That region stays a placeholder instead of a picture of the whole page. The other `Warning: … formula …` lines mean one formula could not be placed; its placeholder stays.
- **`Unsupported Markdown structure: raw tex '\s' at block 29 … Normalize the source before translation.`** The extractor does not escape Markdown in prose, so a sentence containing `\s`, `[u](y)` or `<k>` reads as raw TeX, a link or HTML. Escape it in `source.md` and rerun; the extraction is not repeated.
- **`Reusing the extraction made with docling … on …; this run would use docling … on …. Delete the bundle directory to extract again.`** Information. The extraction came from another device or docling version.

### Translation and export

- **`Translation reused: … (same source and settings; delete it to translate again).`** Information. Delete `book_bilingual.md` to translate again.
- **`Error: translate failed: Source or translation settings changed; start a new translation bundle.`** You changed the model, the language or `source.md` after a partial translation. Rerun with the original settings, or move the bundle aside and start over.
- **`Error: … Bilingual Markdown was edited; export it or use a new output directory.`** You edited `book_bilingual.md` by hand. That is allowed, but the run will not overwrite it.
- **`EPUB navigation is invalid: …`** The headings do not form a usable table of contents. Fix the heading levels in `source.md` (one `#` title, then `##`, `###`) and rerun.
- **`Interrupted. Rerun the same command to resume.`** Ctrl+C. Rerun; the stages that finished are not repeated.

### Limits to know

- Figures stay pictures and their labels are not translated.
- The EPUB carries no `bbm_translation_metadata.json`, and `--no_disclosure` is not honored on this route yet: the credit line is always added.
- `--glossary-auto` learns only when a compaction happens, so a short paper at the default budget learns nothing.
