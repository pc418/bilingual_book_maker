<div align="left">

# Bilingual Book Maker

**[中文](./README-CN.md) | English**


The bilingual_book_maker is an AI translation tool that uses ChatGPT to assist users in creating multi-language versions of epub/txt/md/srt/pdf files and books. Use it only with material you have the right to translate — works for which you hold the necessary rights, suitably licensed or permitted works, public-domain books, or uses otherwise allowed by applicable law. Before using this tool, please review the project's **[disclaimer](./disclaimer.md)**.

[![Stars](https://img.shields.io/github/stars/yihong0618/bilingual_book_maker)](https://github.com/yihong0618/bilingual_book_maker/stargazers)
[![CI](https://github.com/yihong0618/bilingual_book_maker/actions/workflows/make_test_ebook.yaml/badge.svg)](https://github.com/yihong0618/bilingual_book_maker/actions/workflows/make_test_ebook.yaml)
[![PyPI](https://img.shields.io/pypi/v/bbook-maker.svg)](https://pypi.org/project/bbook-maker/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)
[![Code style](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![litellm](https://img.shields.io/badge/%20%F0%9F%9A%85%20liteLLM-OpenAI%7CAzure%7CAnthropic%7CPalm%7CCohere%7CReplicate%7CHugging%20Face-blue?color=green)](https://github.com/BerriAI/litellm)

</div>


![image](https://user-images.githubusercontent.com/15976103/222317531-a05317c5-4eee-49de-95cd-04063d9539d9.png)

**Documentation:** <https://yihong0618.github.io/bilingual_book_maker/>. The same pages are in this repository under [`docs/`](docs/index.md): start with the [Quick start](docs/quickstart.md), then the page for your file — [EPUB](docs/formats/epub.md), [PDF](docs/features/pdf-to-epub.md), [TXT](docs/formats/txt.md), [SRT](docs/formats/srt.md) or [Markdown](docs/formats/md.md).

## Install

You need Python 3.10 or newer and an API key for a model (OpenAI or Anthropic [^token], any OpenAI-compatible endpoint, or a local model), or a free machine-translation route.

```shell
pip install -U bbook_maker          # gives the bbook_maker command
# or, from a clone (newest code, and the only way to get the PDF route):
pip install -r requirements.txt     # then run python3 make_book.py
```

More: [Installation](docs/installation.md), [Docker](docs/docker.md).

## Quick start

A sample book, `test_books/animal_farm.epub`, is in the repository. `--test` translates only the first few paragraphs, so a mistake costs almost nothing.

```shell
export OPENAI_API_KEY=sk-...
```

**An EPUB** — writes `animal_farm_bilingual.epub` beside the input:

```shell
python3 make_book.py --book_name test_books/animal_farm.epub --use_context session --test
```

Then read [EPUB](docs/formats/epub.md) and [Recommended settings for EPUB](docs/features/recommended-epub.md).

**A TXT file** — writes `the_little_prince_bilingual.txt`:

```shell
python3 make_book.py --book_name test_books/the_little_prince.txt --batch_size 20 --test
```

SRT and Markdown work the same way: [TXT](docs/formats/txt.md), [SRT](docs/formats/srt.md), [Markdown](docs/formats/md.md).

**A PDF** — see [PDF to bilingual EPUB](#pdf-to-bilingual-epub-experimental) below.

**Or hand it to a coding agent** (the repository ships a skill for it):

```shell
git clone https://github.com/yihong0618/bilingual_book_maker.git
cd bilingual_book_maker
codex "Hi, please use bbm-plan to translate this book: test_books/animal_farm.epub into a bilingual Chinese-English edition, thanks."
```

## Supported endpoints

A route is an endpoint, not a model name: `--api_base` (the address), `--key`, and `--model` (the id exactly as the endpoint spells it). Leave out `--api_base` for OpenAI's own API and `--model` for `gpt-5.6-luna`. `--api_format` names the wire format when it cannot be inferred. Or write the endpoint down once in `bbm_providers.json` and pass `--provider NAME`: [Provider file](docs/providers.md).

| service | how to reach it |
|---|---|
| OpenAI | the default; `OPENAI_API_KEY` |
| Anthropic Claude | `--model claude-sonnet-4-6`; `ANTHROPIC_API_KEY` |
| any OpenAI-compatible endpoint (OpenRouter, SiliconFlow, Azure, a gateway) | `--api_base <url>/v1 --model <id>` |
| Gemini | `--api_format gemini` |
| Qwen-MT | `--api_format qwen` |
| Groq, xAI, a LiteLLM proxy | `--api_format groq`, `xai`, `litellm`, with `--model` |
| OrcaRouter | `--model orcarouter` |
| Ollama, llama.cpp, vLLM, LM Studio | `--api_base http://localhost:11434/v1 --model <id>`; no key |
| your ChatGPT plan, through the Codex CLI | `--api_format codex` |
| Google Translate, DeepL, DeepL free, Caiyun, Tencent, a custom API | `--api_format google`, `deepl`, `deeplfree`, `caiyun`, `tencent`, `customapi` |

One command per vendor: [Translating with an LLM](docs/llm-args.md#one-command-per-vendor) and [Machine translation](docs/machine-args.md). Keys and variables: [Environment variables](docs/env_settings.md). Older flags (`--model gpt4o`, `--openai_key`, …) still work: [Migrating from the old flags](docs/migration.md).

## Features

### Plan mode

An EPUB keeps text in paragraphs, headings, list items, table cells, verse lines and captions. By default the tool finds all of them, groups them by kind, asks the model which kinds are worth translating, and saves the answer in `<book>_plan.json`; consecutive blocks then share one request. It needs no flag. Preview what will be translated with `--plan-dry-run` (no key needed), translate everything with `--plan-classify all`, or decide the plan yourself with `--plan-classify agent`. Read more: [Plan mode](docs/features/plan-mode.md).

### Session mode

`--use_context session` keeps one conversation for the whole book, so names, register and terms stay the same from chapter to chapter; on an endpoint with prompt caching the history costs little. When it grows past `--context-compact-at` (8192 by default) the model writes a short handoff that opens the next window. Pin names that must hold with `--glossary`. Read more: [Session mode](docs/features/session-mode.md).

### PDF to bilingual EPUB (experimental)

`--to-epub` turns a PDF into a reflowable bilingual EPUB with a table of contents: every paragraph followed by its translation, figures and display formulas kept as pictures. It needs the PDF extra from a clone (`pip install ".[pdf]"`, which reuses a PyTorch you already have; Linux without an NVIDIA card and Windows with one add PyTorch's own index, see [Installing the PDF extra](docs/installation-pdf.md)) and Pandoc 3.1.12 or newer. Read two pages first, then open `paper_pages-1-2_book/source.md` and check the headings before you pay for the whole file:

```shell
python3 make_book.py --book_name paper.pdf --to-epub --pages 1-2 --test
python3 make_book.py --book_name paper.pdf --to-epub --use_context session
```

A scan needs `--pdf-ocr` (and `--ocr-lang iso:ja` or the like outside Chinese and English); a paper gains from `--img-model gpt-5.6-luna`. Without `--to-epub` a PDF becomes a bilingual `.txt`, and that text route also takes `--use_context session` and `--glossary`. Read more: [PDF to bilingual EPUB](docs/features/pdf-to-epub.md) and [Recommended settings for PDF](docs/features/recommended-pdf.md).

![An arXiv paper as a reading edition: the table of contents built from the headings, the bilingual text, and a figure kept as a picture](./docs/img/pdf_reading_edition.webp)

## Params

Every flag in one line. The full text is in [Command line options](docs/cmd.md), and `python3 make_book.py --help` is the authority.

**Every format**

| flag | what it does |
|---|---|
| `--book_name` | the file to translate; the extension picks the format |
| `--language` | target language: a tag, a name, or `TAG:NAME` ([tags](docs/languages.md)); default `zh-hans` |
| `--source_lang` | state the source language instead of detecting it |
| `--test`, `--test_num` | translate only the first few paragraphs (default 10) |
| `--resume` | continue an interrupted run |
| `--single_translate` | write the translation only, without the original |
| `--prompt` | your own prompt: a template, JSON, or a `.json`/`.txt`/`.md` file ([Prompt files](docs/prompt.md)) |
| `--batch_size` | lines per request for TXT, Markdown and the PDF text route |
| `--accumulated_num` | EPUB plan mode: tokens per request; SRT: characters per request |
| `--parallel-workers` | several EPUB chapters or Markdown batches at once |
| `--quiet` | no progress bars or paragraph echoes |

**Endpoint and model** ([Translating with an LLM](docs/llm-args.md))

| flag | what it does |
|---|---|
| `--model` | the model id, exactly as the endpoint names it; default `gpt-5.6-luna` |
| `--key`, `--api_key` | API key (two names for one flag); falls back to `BBM_API_KEY`, then the format's own variable |
| `--api_base` | the endpoint URL |
| `--api_format` | the wire format or machine-translation engine, when it cannot be inferred |
| `--provider` | a named endpoint from `bbm_providers.json` |
| `--model_list` | several models to rotate across |
| `--proxy` | an HTTP proxy |
| `--temperature` | sampling temperature |
| `--no-thinking` | ask a reasoning model not to think first |
| `--extra_body`, `--extra_headers` | extra request fields or HTTP headers |
| `--interval` | pause between requests (the gemini route only) |
| `--batch`, `--batch-use` | OpenAI's Batch API (not on EPUB) |

**Context** ([Session mode](docs/features/session-mode.md))

| flag | what it does |
|---|---|
| `--use_context` | bare or `window`: re-send the last few pairs; `session`: one history for the book |
| `--context_paragraph_limit` | window mode: how many pairs to re-send |
| `--context-compact-at` | session mode: compact at this many estimated tokens (default 8192) |
| `--no-context-compact` | session mode: roll over without a handoff report |
| `--glossary`, `--terminology` | a `term -> translation` file the run must follow (two names for one flag) |
| `--glossary-auto` | `on` keeps the renderings a session's handoffs establish (off by default) |

**EPUB** ([EPUB](docs/formats/epub.md), [Plan mode](docs/features/plan-mode.md))

| flag | what it does |
|---|---|
| `--plan-classify` | how the plan is decided: `auto` (default), `none`, `all`, `model`, `agent` |
| `--plan-dry-run` | print and save the plan without translating |
| `--plan-min-coverage` | stop when the plan covers less than this share of the text (default 0.5) |
| `--max-batch-units` | most units per request (default 16) |
| `--classify-model` | classify with another model, or with Jev (`jev`) |
| `--classify-base-url`, `--classify-key` | where that model is served, and its key |
| `--classify-min-confidence` | Jev's gate: a less certain skip is translated (default 0.95) |
| `--translate-tags` | tags to translate when there is no plan (default `p`) |
| `--exclude-translate-tags` | tags never translated (default `sup,code`; `""` translates everything) |
| `--allow_navigable_strings` | also translate text outside any tag |
| `--only_filelist`, `--exclude_filelist` | translate only these files inside the EPUB, or skip them |
| `--block_size` | without a plan: merge paragraphs into blocks |
| `--sentence_mode` | without a plan: translate sentence by sentence |
| `--translation_style`, `--translation_color` | CSS, or just a colour, for the translated text |
| `--no_disclosure` | leave out the one-line AI-translation credit |
| `--translation-metadata` | add the metadata file to a plain tag-mode run |
| `--retranslate` | retranslate a range of a finished bilingual EPUB ([how](docs/cmd.md#retranslate-epub-only)) |

**PDF** ([PDF to bilingual EPUB](docs/features/pdf-to-epub.md))

| flag | what it does |
|---|---|
| `--to-epub` | turn the PDF into a bilingual EPUB |
| `--pdf-ocr` | read pages with no text layer (a scan) |
| `--ocr-lang` | the languages the OCR engine reads (`iso:zh`, `iso:ja`, …) |
| `--ocr-engine` | which OCR engine reads the scan: `auto`, `rapidocr`, `ocrmac`, `easyocr`, `tesseract` |
| `--ocr-replace-layer` | with `--pdf-ocr`: OCR every page and drop a garbage text layer |
| `--pages` | only these pages (`12-30`, `1,3,5-7`) |
| `--device` | where the extraction models run: `auto`, `cpu`, `cuda`, `mps`, `xpu` |
| `--no-formula-images` | leave display formulas as placeholders instead of pictures |
| `--img-model` | a vision model that corrects region roles (`none` turns it off) |
| `--img-base-url`, `--img-key` | where that model is served, and its key |
| `--pdf_layout` | text route only: also write a bilingual PDF (`top-bottom`, `side-by-side`, `all`) |

## Docker

`docker run --rm -v "$PWD":/book -e OPENAI_API_KEY ghcr.io/yihong0618/bilingual_book_maker:latest --book_name /book/my_book.epub` translates a book in the mounted folder; the `pdf` tag adds Pandoc and the PDF packages for `--to-epub`. Tags, GPU notes per system and building it yourself are in [Docker](docs/docker.md).

## Notes

1. API token from free trial has limit. If you want to speed up the process, consider paying for the service or use multiple OpenAI tokens
2. PR is welcome

# Thanks

- @[yetone](https://github.com/yetone)

# Contribution

- Any issues or PRs are welcome.
- TODOs in the issue can also be selected.
- Please run `black make_book.py`[^black] before submitting the code.

# Others better

- 书译 BookTranslator -> [Book Translator](https://www.booktranslator.app)

## Appreciation

Thank you, that's enough.

![image](https://user-images.githubusercontent.com/15976103/222407199-1ed8930c-13a8-402b-9993-aaac8ee84744.png)

[^token]: You can get a token from [OpenAI](https://platform.openai.com/account/api-keys) or [Anthropic](https://console.anthropic.com/account/api-keys).
[^black]: https://github.com/psf/black
