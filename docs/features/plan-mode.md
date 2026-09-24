# Plan mode

## What it does

An EPUB keeps its text in many kinds of markup: paragraphs, headings, list items, table cells, blockquotes, verse lines, captions. Plan mode finds all of them. The loader partitions the whole book into units, groups the units by their tag signature (the tag and its classes), and asks the model which signatures are worth translating. The answer is written to `<book>_plan.json`, which every later run of the same book reuses. Without a plan, only the `--translate-tags` selection is translated, `<p>` by default, and verse or a table outside `<p>` is left in the source language without a word.

Plan mode also groups the work. Consecutive units share one request up to a token budget (`--accumulated_num`) and a unit cap (`--max-batch-units`, default 16). Each request asks for the units back by id, so a reply that drops, merges or reorders units is caught and retried in smaller pieces instead of shifting every translation by one slot. Plan mode is on by default for an EPUB on any LLM route; you do not need a flag. The numbers behind the defaults are on [Why 16 units per request](../evaluation/grouping-batch-size.md).

## Setup

Nothing to install. Plan mode needs an EPUB and an LLM route. On a [machine-translation](../machine-args.md) route there is no model to ask, so the run falls back to the `--translate-tags` selection.

The flags:

| flag | what it does |
|---|---|
| `--plan-classify auto` | Default. The translating model decides, over a JSON schema where the endpoint verifiably applies one, otherwise over a plain conversation with one-word `skip`/`translate`/`unsure` answers. Unsure and unreadable answers translate. |
| `--plan-classify model` | Like `auto`, but an unresolved row stops the run instead of falling back. |
| `--plan-classify all` | Translate every unit; no classification, no plan file. |
| `--plan-classify none` | No plan: translate the `--translate-tags` selection, as before plan mode existed. |
| `--plan-classify agent` | Write the plan with samples, print instructions for a coding agent (or you), and stop. Rerun the same command to translate. |
| `--plan-classify-model MODEL` | Classify with another model. A classification failure then stops the run. |
| `--plan-dry-run` | Print the per-signature coverage table, write the plan with every decision empty, and exit. No key needed. |
| `--plan-min-coverage FRACTION` | Stop when the plan covers less than this share of the book's text (default 0.5; `0` turns the check off). |
| `--accumulated_num N` | Token budget per request. Unset, the run derives it: 1200 with the stock prompts, up to 1600 under a long custom `--prompt`, 800 on an endpoint without a strict schema (session runs keep the un-halved value). `1` turns grouping off. |
| `--max-batch-units N` | Most units per request. Default 16; 8 on an endpoint without a strict schema. |
| `--exclude-translate-tags TAGS` | Tags whose content is never sent (default `sup,code`). |
| `--only_filelist`, `--exclude_filelist` | Plan only these internal files, or skip these. The dry run honors them too. |

Do not pass these in plan mode: `--translate-tags` (ignored, the plan covers everything), `--allow_navigable_strings` (ignored), `--block_size` and `--batch_size` (they re-cut text the plan already partitioned). `--retranslate`, `--batch` and `--sentence_mode` contradict a plan and are refused.

## Recommended commands

### By document type

=== "Novel"

    The default is right. Add a session so names stay consistent.

    ```bash
    bbook_maker \
      --book_name novel.epub \
      --language zh-hans \
      --use_context session \
      --quiet
    ```

=== "Textbook with tables and formulas"

    Preview the plan first. A textbook puts text in table cells, captions and sidebars, and you want to see what will be skipped before you pay.

    ```bash
    bbook_maker \
      --book_name textbook.epub \
      --plan-dry-run
    ```

    If the table shows kinds of text you want that were not selected, translate every unit instead of classifying:

    ```bash
    bbook_maker \
      --book_name textbook.epub \
      --language zh-hans \
      --plan-classify all \
      --use_context session \
      --quiet
    ```

    Formulas in an EPUB are MathML or images. The plan never sends MathML (`<math>`) or SVG to the model, so equations stay as they are.

=== "Paper"

    A paper as an EPUB takes the same command as a novel. A paper as a PDF has no plan: the PDF route translates every block. See [PDF to bilingual EPUB](pdf-to-epub.md).

    ```bash
    bbook_maker \
      --book_name paper.epub \
      --language zh-hans \
      --use_context session
    ```

=== "Scanned book"

    A scan is a PDF, and plan mode is EPUB only. Use [PDF to bilingual EPUB](pdf-to-epub.md) with `--pdf-ocr`.

=== "Chinese scan"

    A scan is a PDF, and plan mode is EPUB only. Use [PDF to bilingual EPUB](pdf-to-epub.md) with `--pdf-ocr` and `--ocr-lang iso:zh-Hans`. For a Chinese EPUB translated to English, plan mode works as for a novel:

    ```bash
    bbook_maker \
      --book_name chinese_novel.epub \
      --language en \
      --use_context session
    ```

### By system

Plan mode runs on the endpoint, not on your machine, so a hosted endpoint needs the same command everywhere. The system matters when the model runs locally.

=== "macOS (Apple silicon)"

    A local model through Ollama, which uses Metal:

    ```bash
    bbook_maker \
      --book_name novel.epub \
      --api_base http://localhost:11434/v1 \
      --model qwen3:8b \
      --use_context session
    ```

    A small local model has no strict JSON schema, so the run halves the unit cap to 8 by itself. See [On-device models](../llm-args.md#on-device-models-ollama-llamacpp-lm-studio).

=== "Linux with NVIDIA"

    A local server with an OpenAI-compatible API (vLLM, llama.cpp, Ollama) on the GPU:

    ```bash
    bbook_maker \
      --book_name novel.epub \
      --api_base http://localhost:8000/v1 \
      --model your-model-id \
      --use_context session
    ```

=== "CPU only"

    A local model on the CPU is slow for a whole book. Use a hosted endpoint:

    ```bash
    bbook_maker \
      --book_name novel.epub \
      --model gpt-5.6-luna \
      --use_context session
    ```

=== "Docker"

    ```bash
    docker run --rm \
      -v "$PWD":/book \
      -e OPENAI_API_KEY \
      ghcr.io/yihong0618/bilingual_book_maker:latest \
      --book_name /book/novel.epub \
      --use_context session \
      --quiet
    ```

## What can go wrong

These are the lines the run prints, what they mean, and what to do.

- **`plan mode: on (endpoint verified strict JSON schema)`**, or `plan mode: on (no structured output here; classifying over a plain session)`. Not a problem. It says how the plan will be decided on this endpoint.
- **`… doesn't apply JSON schema (…), using delimiter method`** or **`… honors JSON schema shape but not value constraints; using the delimiter method for translation, schema kept for classification`**. Not a problem. The endpoint does not do strict schema decoding, so translation uses a delimiter format. Expected on the anthropic route, most proxies and local servers. [Why the tool probes the endpoint](../evaluation/structured-output-ladder.md) explains it.
- **`plan mode: off (…)`**. The run translates the `--translate-tags` selection. The reason in brackets says why: a route with no model, `--translate-tags` given, or a failed probe. If you did not expect it, read the reason. On a route that does not plan by itself, the run suggests `--plan-classify model`.
- **`N misaligned batches this run — if this keeps happening, a lower --max-batch-units or --accumulated_num may fit this model better`**. Printed from the third recovered batch. The model keeps miscounting large requests. Nothing is lost (each bad batch was retried in halves), but it costs requests. On the next run halve the unit cap: `--max-batch-units 8`, then `4`.
- **`Plan coverage X% is below the required 50.0% — refusing to translate a fraction of the book silently.`** The plan would skip most of the book. Open `<book>_plan.json` and look at what was marked `skip`. A dictionary or a book with a large apparatus may really translate less; then lower `--plan-min-coverage` on purpose.
- **`<book>_plan.json has N undecided signature(s) …`**. After `--plan-classify agent`, some rows still have no decision. Fill every `action` with `translate` or `skip`, then rerun.
- **`…: invalid action '…' — use …`**. A typo in a hand-edited plan. Fix the JSON and rerun.
- **A resume refused with a fingerprint message.** The book or the plan changed since the checkpoint was written. Rerun with the original flags, or delete the checkpoint and start over, knowing what gets paid again.
- **The plan skipped something you wanted.** Delete `<book>_plan.json` and run again, or edit its `action` fields by hand. A `--test` run classifies the whole book, not only the slice, so its plan is the real one.
