# bilingual book maker

bilingual book maker translates a book with a language model and writes a bilingual edition: every paragraph of the original, followed by its translation. It reads EPUB, TXT, Markdown, SRT and PDF files. It talks to OpenAI, Anthropic, Gemini, Qwen, any OpenAI-compatible endpoint (a gateway, a reseller, or a model on your own machine), a Codex subscription, and a few machine-translation services.

```bash
pip install -U bbook_maker
export OPENAI_API_KEY=sk-...
bbook_maker --book_name my_book.epub --use_context session
```

That writes `my_book_bilingual.epub` next to `my_book.epub`. The default model is `gpt-5.6-luna`; the default target language is Simplified Chinese (`--language zh-hans`).

## Where to go next

- [Quick start](quickstart.md): install, then one EPUB, one TXT and one PDF, with the file names you get back.
- [Installation](installation.md): pip, a checkout, the [PDF extra](installation-pdf.md) and [Docker](docker.md).
- [Translating with an LLM](llm-args.md): model, key, endpoint, provider file, prompt, retries, on-device models.
- [Machine translation](machine-args.md): Google, DeepL, Caiyun, Tencent and a custom API, and what they cannot do.
- [Formats](formats/epub.md): every flag that applies to each input format, and every flag that does not.
- Features: [plan mode](features/plan-mode.md), [session mode](features/session-mode.md) and [PDF to bilingual EPUB](features/pdf-to-epub.md), each with recommended commands per document type and per system.
- [Evaluation](evaluation/index.md): the measurements behind the defaults, one question per page.
- [Command line options](cmd.md): the flat list of every flag.

## Use it on material you may translate

Use it only with material you have the right to translate — works for which you hold the necessary rights, suitably licensed or permitted works, public-domain books, or uses otherwise allowed by applicable law. Before using this tool, please review the project's **[disclaimer](disclaimer.md)**.
