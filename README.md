**[中文](./README-CN.md) | English**

# bilingual_book_maker

The bilingual_book_maker is an AI translation tool that uses ChatGPT to assist users in creating multi-language versions of epub/txt/md/srt/pdf files and books. This tool is exclusively designed for translating epub and other public domain works and is not intended for copyrighted works. Before using this tool, please review the project's **[disclaimer](./disclaimer.md)**.

![image](https://user-images.githubusercontent.com/15976103/222317531-a05317c5-4eee-49de-95cd-04063d9539d9.png)

## Supported endpoints

You pick an **endpoint**, not a model name. Three things describe a route:

| Flag | What it is |
|------|-----------|
| `--model` | the model id, exactly as your endpoint names it |
| `--api_base` | the endpoint URL. Defaults to the format's official host |
| `--key` | the API key (comma-separate several to rotate them) |
| `--api_format` | the wire format. Inferred; pass it only when the guess is wrong |

`--model` takes a real model id — `gpt-5-mini`, `claude-sonnet-4-6`, or a
namespaced `openai/gpt-5-mini` for gateways that use those. No preset lists,
no alias table, nothing to update when a vendor ships a new model. (Old alias
values like `gpt4` still work; they are translated with a note.)

The format is worked out for you: an explicit `--api_format` wins, otherwise
the `--api_base` host decides, otherwise a model id mentioning `claude` or
`anthropic` means the Anthropic shape. If that guess is wrong — a gateway
serving Claude models over the OpenAI shape — the first request says so and
the run switches to `openai` by itself.

`--api_base` accepts the URL as you find it in a provider's docs:
`https://host/v1`, a trailing slash, or the whole
`https://host/v1/chat/completions` all mean the same thing.

`--api_format` values: `openai` (default), `anthropic`, `codex`, and the fixed
machine-translation engines `google`, `caiyun`, `deepl`, `deeplfree`,
`tencent`, `customapi`.

### `codex`: translate on your ChatGPT subscription

`--api_format codex` spends your ChatGPT/Codex plan allowance instead of API
credits. It needs the [Codex CLI](https://developers.openai.com/codex/cli)
installed and signed in (`codex login`); bbm drives a `codex app-server`
sidecar, which owns that session, so there is no `--openai_key` and no
`--api_base`.

```shell
python3 make_book.py --book_name test_books/animal_farm.epub \
  --model codex --language zh-hans
```

`--model codex` and `--api_format codex` are the same thing: codex is not an
endpoint, so naming it as the model selects it, the way the other
non-endpoint engines have always been chosen.

`--model` is optional here and defaults to `gpt-5.6-luna`; the sidecar also
offers `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.5` and `gpt-5.2`.

Because a fresh Codex thread costs about 17k tokens of preamble before your
first paragraph, one thread is opened and reused for the whole book — which
also makes it a context window. At `--context-compact-at` it is condensed
into a handoff report and a fresh thread is seeded with it, exactly like
`--use_context session`. `--glossary` and `--glossary-auto` work here too.

bbm prints how much of your window remains before starting, and again
whenever that figure moves. If the window runs out mid-book the run does not
stop: it says when the window resets, waits until a minute past it, and
carries on. Ctrl+C still works, and the run is resumable either way.

Waiting only happens where waiting helps. A spent 5-hour window comes back on
a timer; depleted credits and account usage limits do not, so those fail
immediately rather than hanging, as does a reset more than six hours out (a
weekly limit, say) — it tells you when it clears instead.

One caveat: turns run through your own Codex hooks (`~/.codex/hooks.json`),
so per-prompt hooks fire for every paragraph.

Anything speaking the OpenAI shape works through `openai` — OpenAI itself,
Groq, xAI, DeepSeek, SiliconFlow, OpenRouter, OrcaRouter, Together, Alibaba DashScope,
Gemini's OpenAI-compatible endpoint, vLLM, LM Studio, Ollama. See
[Models and languages](./docs/model_lang.md) for a per-vendor cookbook.

## Preparation

1. ChatGPT or OpenAI token [^token]
2. epub/txt/md/pdf books
3. Environment with internet access or proxy
4. Python 3.10+

## Quick Start

```shell
pip install -r requirements.txt
```

The commands below translate `test_books/animal_farm.epub`, the sample in this
repo, so they run as written — point `--book_name` at your own epub/txt/md/pdf
when you have one. Pick how you pay; the book flags are the same either way.

**On a ChatGPT subscription.** Needs the [Codex CLI](https://developers.openai.com/codex/cli)
installed and signed in — `codex login`, once. No key, no `--api_base`:

```shell
python3 make_book.py --book_name test_books/animal_farm.epub --language zh-hans \
  --model codex \
  --plan-classify model --use_context session --glossary-auto
```

**On any OpenAI-compatible API.** Your own endpoint, model and key — DeepSeek
shown, but OpenAI, Groq, xAI, OpenRouter, Gemini, vLLM, Ollama and the rest
work the same way:

```shell
python3 make_book.py --book_name test_books/animal_farm.epub --language ja \
  --api_base https://api.deepseek.com/v1 --key sk-xxx --model deepseek-chat \
  --plan-classify model --use_context session --glossary-auto
```

The three shared flags are what make it a book rather than a tag sweep:

- `--plan-classify model` — partitions the whole book, then has the model rule
  on what is content and what is apparatus (running heads, page numbers), so
  nothing is silently missed.
- `--use_context session` — one running history, compacted into a handoff
  report as it fills, so late chapters still know who the characters are.
- `--glossary-auto` — each compact records the renderings it established and
  carries them on, so names stay stable. Add `--glossary terms.txt` to pin your
  own; those always win.

Add `--test` to try any of it on a few paragraphs first. Runs are resumable —
rerun the same command after a Ctrl+C. Installed from PyPI instead
(`pip install -U bbook_maker`)? Every command here works the same with
`bbook_maker` in place of `python3 make_book.py`.

## Translate Service

- `--key` takes the API key. Several comma-separated keys (`xxx,xxx,xxx`) are
  rotated to spread rate limits. Or set `BBM_API_KEY`; the conventional
  `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` are also honored.
- `--model` is required for the `openai` and `anthropic` formats, and takes
  the endpoint's own model id.
- Add `--use_context` to send a context paragraph with each passage (see below).

* OpenAI, and every OpenAI-compatible endpoint

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --key ${key} --model gpt-5-mini
  ```

  Point `--api_base` elsewhere to use the same route for any other vendor:

  ```shell
  # Groq
  python3 make_book.py --book_name test_books/animal_farm.epub \
    --api_base https://api.groq.com/openai/v1 --key ${groq_key} --model llama-3.3-70b-versatile

  # xAI
  python3 make_book.py --book_name test_books/animal_farm.epub \
    --api_base https://api.x.ai/v1 --key ${xai_key} --model grok-4

  # DeepSeek
  python3 make_book.py --book_name test_books/animal_farm.epub \
    --api_base https://api.deepseek.com/v1 --key ${key} --model deepseek-chat

  # Gemini, through its OpenAI-compatible endpoint
  python3 make_book.py --book_name test_books/animal_farm.epub \
    --api_base https://generativelanguage.googleapis.com/v1beta/openai/ \
    --key ${gemini_key} --model gemini-2.5-flash

  # Alibaba Qwen, through DashScope's compatible mode
  python3 make_book.py --book_name test_books/animal_farm.epub \
    --api_base https://dashscope.aliyuncs.com/compatible-mode/v1 \
    --key ${qwen_key} --model qwen-mt-turbo
  ```

* [Claude](https://console.anthropic.com/docs)

  The anthropic format is inferred from the host, so `--api_format` is optional here.

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub \
    --api_base https://api.anthropic.com --key ${claude_key} \
    --model claude-sonnet-4-6
  ```

* [Ollama](https://github.com/ollama/ollama) and other local servers

  A local endpoint needs no key.

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub \
    --api_base http://localhost:11434/v1 --model ${ollama_model_name}
  ```

* Google Translate

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format google
  ```

* DeepL

  [DeepL Translator](https://rapidapi.com/splintPRO/api/dpl-translator) needs a paid token.

  ```
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format deepl --key ${deepl_key}
  ```

* DeepL free

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format deeplfree
  ```

* Caiyun Translate

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format caiyun --key ${caiyun_key}
  ```

* [Tencent TranSmart](https://transmart.qq.com)

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format tencent
  ```

* Your own translation API

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub \
    --api_format customapi --api_base https://your.host/translate
  ```

## Migrating from the old flags

Commands written against the old CLI keep working. Every removed flag is
rewritten into the endpoint surface before the run starts, and each rewrite
prints what it became so you can update the command at your leisure:

```
$ bbook_maker --book_name book.epub --model gpt4omini --openai_key sk-...
deprecated: --openai_key is now --key
deprecated: --model gpt4omini is now --model gpt-4o-mini
```

| Old | Rewritten to |
|---|---|
| `--model chatgptapi` / `gpt4` / `gpt4o` / `gpt4omini` / `gpt5mini` / `o1` / `o1mini` / `o1preview` / `o3mini` | `--model <that model>` |
| `--model openai --model_list X` | `--model_list X` |
| `--model claude` | `--model claude-haiku-4-5-20251001` |
| an exact `claude-*` id | unchanged — the anthropic format is inferred from the id |
| `--model gemini` / `geminipro` | `--api_base https://generativelanguage.googleapis.com/v1beta/openai/ --model gemini-flash-latest` / `gemini-pro-latest` |
| `--model groq --model_list X` | `--api_base https://api.groq.com/openai/v1 --model_list X` |
| `--model xai` | `--api_base https://api.x.ai/v1 --model grok-beta` |
| `--model orcarouter` | `--api_base https://api.orcarouter.ai/v1 --model orcarouter/auto` |
| `--model qwen` / `qwen-mt-turbo` / `qwen-mt-plus` | `--api_base https://dashscope.aliyuncs.com/compatible-mode/v1 --model qwen-mt-*` |
| `--model google` / `caiyun` / `deepl` / `deeplfree` / `tencentransmart` | `--api_format google` / `caiyun` / `deepl` / `deeplfree` / `tencent` |
| `--custom_api URL` | `--api_format customapi --api_base URL` |
| `--openai_key` / `--claude_key` / `--gemini_key` / `--groq_key` / `--xai_key` / `--orcarouter_key` / `--qwen_key` / `--caiyun_key` / `--deepl_key` / `--api_key` | `--key` |
| `--ollama_model M` | `--api_base http://localhost:11434/v1 --model M` |
| `--deployment_id D` | `--model D`, with `--api_base` rewritten to the deployment's `/openai/v1` path |
| `--provider NAME` | expanded from `bbm_providers.json` into `--api_base` / `--api_format` / `--model` |
| `--interval` | dropped; it only applied to the removed gemini route |

Notes:

- Old key variables still work for the route that implied them:
  `BBM_GROQ_API_KEY` authenticates a translated `--model groq` command,
  `BBM_GOOGLE_GEMINI_KEY` a translated `--model gemini`, and so on.
- Anything you pass in the new flags wins, so `--model gemini --api_base
  https://my-gateway/v1` keeps your gateway.
- Model ids come from the *old* preset lists, so a translated command runs
  what it used to run rather than being moved onto a newer model. Some of
  those models have since been retired; the endpoint's own model check says
  so plainly.
- `--model` values that are not old aliases pass through untouched: they are
  model ids, which is the normal case now.
- No alias changes *which model* runs. Two overlap with real ids: `o1`
  translates to itself, and `qwen-mt-turbo` / `qwen-mt-plus` additionally fill
  in DashScope's endpoint — the only host serving them — which your own
  `--api_base` overrides.

## Use

- Once the translation is complete, a bilingual book named `${book_name}_bilingual.epub` would be generated for EPUB inputs; for TXT/MD/SRT inputs a bilingual text (or subtitle) file named `${book_name}_bilingual.txt` (or `_bilingual.srt`) will be generated. For **PDF inputs** the tool will produce a bilingual `.txt` fallback and will also attempt to create `${book_name}_bilingual.epub` — if EPUB creation fails, the TXT fallback remains so you do not need to retranslate.
- If there are any errors or you wish to interrupt the translation by pressing `CTRL+C`, a temporary bilingual file (for example `{book_name}_bilingual_temp.epub` or `{book_name}_bilingual_temp.txt`) would be generated. You can simply rename it to any desired name.

## Params

- `--model`, `--api_base`, `--key`, `--api_format`:

  How a route is chosen. Most runs pass `--model`, `--key` and sometimes
  `--api_base`; the format is inferred from the endpoint host, or failing
  that from the model id.

  `--model_list a,b,c` is kept for rotating across several models to spread
  rate limits, and for compatibility with older commands. Name a model in one
  flag or the other, not both.

  | `--api_format` | Key | Model |
  |----------------|-----|-------|
  | `openai` (default) | required | `--model` required |
  | `anthropic` | required | `--model` required |
  | `google` | none | fixed engine |
  | `deeplfree` | none | fixed engine |
  | `tencent` | none | fixed engine |
  | `customapi` | none — pass the URL as `--api_base` | fixed engine |
  | `caiyun` | required | fixed engine |
  | `deepl` | required | fixed engine |

  A key is looked for in `--key`, then `BBM_API_KEY`, then the format's
  conventional variable (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
  `BBM_CAIYUN_API_KEY`, `BBM_DEEPL_API_KEY`). Endpoints on localhost need
  no key at all.

- `--source_lang`:

  Name the source language for endpoints that want it stated rather than
  detected (default `auto`).

- `--test`:

  Use `--test` option to preview the result if you haven't paid for the service. Note that there is a limit and it may take some time.

- `--language`:

  Set the target language like `--language "Simplified Chinese"`. Default target language is `"Simplified Chinese"`.
  Read available languages by helper message: `python make_book.py --help`

- `--proxy`:

  Use `--proxy` option to specify proxy server for internet access. Enter a string such as `http://127.0.0.1:7890`.

- `--resume`:

  Use `--resume` option to manually resume the process after an interruption.

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format google --resume
  ```

- `--translate-tags`:

  epub is made of html files. By default, we only translate contents in `<p>`.
  Use `--translate-tags` to specify tags need for translation. Use comma to separate multiple tags.
  For example: `--translate-tags h1,h2,h3,p,div`

- `--plan-classify {none,most,model,agent}` (epub only):

  **Plan mode**: instead of selecting tags, every text node in the book is either assigned
  to a translation unit or skipped for an explicit, reported reason (hidden content,
  page-list navs, symbols, links, ...). This is the right choice for books whose text does
  not live in `<p>` — e.g. poetry rendered as per-line `<div>`s or `<blockquote>`s, which
  the default would silently skip. Runs of short verse lines are batched into stanza
  windows (up to `--poetry-group-size` lines, default 8) and translated in one request so
  the model sees neighboring lines for context.

  The partition is deliberately greedy: it keeps everything it cannot rule out
  structurally, because guessing from shape used to drop real content (verse numbers,
  one-word dialogue, drop caps) to save only 0–6% of characters. Deciding what is not
  worth translating is the classification entry you pick here:

  - `none` (default): no plan — translate the `--translate-tags` selection as usual.
  - `most`: translate the whole partition, no classification. It asks nothing, so it
    writes no plan JSON and ignores an existing one; every signature is recorded in the
    ledger it prints as an explicit `user` decision, so nothing is translated that
    nobody decided to translate.
  - `model`: an LLM rules on **every** signature the plan has not already decided, then the
    run continues and translates the book. Use `--plan-classify-model X` to pick a
    different model for it — naming one implies this mode, and a failure then aborts
    instead of falling back. If the model leaves any signature unresolved, the run stops
    and hands those rows to the agent flow rather than translating them by default.
  - `agent`: makes no API call. Writes the plan, prints a block of instructions to paste
    into a coding-agent session (Claude Code, Codex, ...), and **stops before translating**.
    Edit the actions, then rerun the same command to translate.

  Note the difference in cost between the last two: `agent` always stops, while a `model`
  run whose classification fully resolves goes straight on to translate the whole book in
  the same command. Add `--test --test_num 20` to sample it first.

  Plan mode is entered only by `--plan-classify` (or `--plan-dry-run`), and in it
  `--translate-tags` is ignored — the plan partitions the whole book.

  - `--plan-dry-run`: print the per-signature coverage table, write `<book>_plan.json`, and
    exit. No API key or credits needed. Honors `--only_filelist` / `--exclude_filelist`.
    Its rows are all undecided — a later `model` run classifies them, an `agent` run hands
    them over, and editing them yourself works too.
  - `<book>_plan.json`: a signature is decided by setting three fields together —
    `"action"` (`"translate"` or `"skip"`), `"decided_by"` (`"user"` when you edit it by
    hand), and `"content_type"`, the name of what the text is. Naming it is the reasoning;
    a verdict without one cannot be audited, and the run refuses it. Each row carries up
    to 5 real `samples` so you can judge without opening the epub. Your decisions are
    never overwritten; the file is rewritten only to add rows for signatures a settings
    change introduced (delete it to regenerate from scratch).
  - `--plan-min-coverage` (default 0.5): plan mode aborts if the plan covers less than this
    fraction of the book's text, instead of silently translating a fraction of it.

  ```shell
  # inspect what would be translated (free, no key needed)
  python3 make_book.py --book_name my_book.epub --plan-dry-run
  # translate the whole partition
  python3 make_book.py --book_name my_book.epub --key ${key} --model gpt-5-mini --plan-classify most
  # let a model triage the apparatus first
  python3 make_book.py --book_name my_book.epub --key ${key} --model gpt-5-mini --plan-classify model
  # or hand the triage to a coding agent (stops, prints instructions, then rerun)
  python3 make_book.py --book_name my_book.epub --key ${key} --model gpt-5-mini --plan-classify agent
  ```

- `--exclude-translate-tags`:

  Use `--exclude-translate-tags` to exclude content within specified HTML tags from translation. This is useful for preserving code blocks, preformatted text, or other special content. Use comma to separate multiple tags.
  Default: `sup,code`.
  For example: `--exclude-translate-tags code,pre`

  **Tip**: Use `--exclude-translate-tags ""` to translate all content including code blocks (overrides the default exclusion).

- `--book_from`:

  Use `--book_from` option to specify e-reader type (Now only `kobo` is available), and use `--device_path` to specify the mounting point.

- `--api_base`:

  If you want to change api_base like using Cloudflare Workers, use `--api_base <URL>` to support it.
  **Note: the api url should be '`https://xxxx/v1`'. Quotation marks are required.**

- `--allow_navigable_strings`:

  If you want to translate strings in an e-book that aren't labeled with any tags, you can use the `--allow_navigable_strings` parameter. This will add the strings to the translation queue. **Note that it's best to look for e-books that are more standardized if possible.**

- `--prompt`:

  To tweak the prompt, use the `--prompt` parameter. Valid placeholders for the `user` role template include `{text}` and `{language}`. It supports a few ways to configure the prompt:

  - If you don't need to set the `system` role content, you can simply set it up like this: `--prompt "Translate {text} to {language}."` or `--prompt prompt_template_sample.txt` (example of a text file can be found at [./prompt_template_sample.txt](./prompt_template_sample.txt)).

  - If you need to set the `system` role content, you can use the following format: `--prompt '{"user":"Translate {text} to {language}", "system": "You are a professional translator."}'` or `--prompt prompt_template_sample.json` (example of a JSON file can be found at [./prompt_template_sample.json](./prompt_template_sample.json)).
  
  - You can now use [PromptDown](https://github.com/btfranklin/promptdown) format (`.md` files) for more structured prompts: `--prompt prompt_md.prompt.md`. PromptDown supports both traditional system messages and developer messages (used by newer AI models). Example:
  
      ```markdown
      # Translation Prompt
      
      ## Developer Message
      You are a professional translator who specializes in accurate translations.
      
      ## Conversation
      
      | Role | Content                                                        |
      | ---- | -------------------------------------------------------------- |
      | User | Please translate the following text into {language}:\n\n{text} |
      ```

  - You can also set the `user` and `system` role prompt by setting environment variables: `BBM_CHATGPTAPI_USER_MSG_TEMPLATE` and `BBM_CHATGPTAPI_SYS_MSG`.

- `--batch_size`:

  Use the `--batch_size` parameter to specify the number of lines for batch translation (default is 10, currently only effective for txt files).

- `--accumulated_num`:

  Wait for how many tokens have been accumulated before starting the translation. gpt3.5 limits the total_token to 4090. For example, if you use `--accumulated_num 1600`, maybe openai will output 2200 tokens and maybe 200 tokens for other messages in the system messages user messages, 1600+2200+200=4000, So you are close to reaching the limit. You have to choose your own
  value, there is no way to know if the limit is reached before sending

- `--use_context`:

  prompts the model to create a three-paragraph summary. If it's the beginning of the translation, it will summarize the entire passage sent (the size depending on `--accumulated_num`).
  For subsequent passages, it will amend the summary to include details from the most recent passage, creating a running one-paragraph context payload of the important details of the entire translated work. This improves consistency of flow and tone throughout the translation. This option is available for all ChatGPT-compatible models and Gemini models.

  `--use_context` also takes an optional mode. Bare `--use_context` (or
  `--use_context window`) is the behaviour described above. `--use_context
  session` instead keeps a single append-only history of everything
  translated so far, so an endpoint with prompt caching re-reads it at its
  cache rate — context can then grow to chapter length for less money than
  window mode spends on three paragraphs. When the history reaches the
  compact budget, the model is asked for a translator handoff report
  (summary, style notes, and with `--glossary-auto` the renderings it has
  established), which seeds the next window and is appended to
  `<book>_handoff.md`. That file is plain markdown: readable, hand-editable,
  and re-read when a run resumes.

- `--context_paragraph_limit`:

  Use `--context_paragraph_limit` to set a limit on the number of context paragraphs when using the `--use_context` option (window mode only).

- `--context-compact-at`:

  Session mode only. The estimated-token budget the history may reach before
  it is compacted into a handoff report. Left unset, each model uses its
  default of `8000`, which costs between 0.5x and 1.1x what window mode
  costs while carrying several times the context. `2500` is the cheapest
  setting if you want it; the minimum is `500`.

- `--glossary`:

  Path to a pinned-vocabulary file: `term → translation` lines, with an
  optional `# note`. A pinned term is injected into a paragraph's prompt only
  when that term actually occurs in it, so the cost is a few tokens on the
  paragraphs that need it and nothing elsewhere. Latin-script terms match on
  word boundaries, CJK terms as substrings.

- `--glossary-auto`:

  Session mode only, off by default. Also asks each handoff report for the
  renderings it established — as `term → translation # note` lines inside a
  `<renderings>` block, the same format `--glossary` files use — and carries
  them into later windows.

  Two glossaries are kept apart. Terms from your `--glossary` file are
  *pinned*: they never change, and a model that renders one differently is
  reported rather than silently overruling you. Everything else is *learned*,
  and each window's reading replaces the previous one, since by then the model
  has seen more of the book. Both are injected per paragraph, only where the
  term actually occurs.

  If the model omits the block, loose `term → translation` lines are recovered
  instead, and the run says which route it had to take — with this flag on,
  silently learning nothing looks exactly like a book with no recurring terms.

- `--parallel-workers`:

  Use `--parallel-workers` to process EPUB chapters or Markdown batches/sections in
  parallel. Values greater than `1` spin up multiple workers (recommended: `2-4`) and
  automatically fall back to sequential mode when there is only one unit of work. Other
  input loaders currently accept this shared CLI option but do not parallelize their work.

- `--temperature`:

  Use `--temperature` to set the temperature parameter for `chatgptapi`/`gpt4`/`claude` models.
  For example: `--temperature 0.7`.

- `--block_size`:

  Use `--block_size` to merge multiple paragraphs into one block. This may increase accuracy and speed up the process.
  For example: `--block_size 5`.

- `--single_translate`:

  Use `--single_translate` to output only the translated book without creating a bilingual version.

- `--translation_style`:

  Apply custom CSS to translated EPUB text, for example
  `--translation_style "color: #808080; font-style: italic;"`.

- `--translation_color`:

  Shorthand for setting only the translated EPUB text color, for example
  `--translation_color "#1e90ff"`. If `--translation_style` is also present, the full style
  takes precedence.

- `--pdf_layout {none,top-bottom,side-by-side,all}`:

  Select additional bilingual PDF outputs for PDF inputs. The default `none` creates no
  extra PDF; `all` attempts both top-bottom and side-by-side layouts. The bilingual TXT and
  EPUB outputs are unaffected.

- `--sentence_mode`:

  Translate EPUB text sentence by sentence instead of translating each paragraph as one
  unit. It is incompatible with EPUB plan mode.

- `--batch` / `--batch-use`:

  Two-stage EPUB translation through the ChatGPT Batch API. First run with `--batch` to
  submit the batch, then rerun with `--batch-use` to wait for and consume its results.
  These flags are incompatible with plan mode.

- `--quiet`:

  Suppress EPUB progress bars and per-paragraph source/translation echoes while retaining
  reports and errors. Recommended for log files and non-interactive agent runs.

- `--retranslate "$translated_filepath" "file_name_in_epub" "start_str" "end_str"`:

  Retranslate from start_str to end_str's tag:

  ```shell
  python3 "make_book.py" --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' 'index_split_002.html' 'in spite of the present book shortage which' 'This kind of thing is not a good symptom. Obviously'
  ```

  To retranslate only the tag containing `start_str`, pass an empty fourth argument:

  ```shell
  python3 "make_book.py" --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' 'index_split_002.html' 'in spite of the present book shortage which' ''
  ```

- `--extra_body`:

  Pass additional JSON parameters on the `openai` route. This is the vent for
  vendor-specific request fields; other formats ignore it and say so. Provide a
  JSON string with the desired parameters.

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --key ${openai_key} --model gpt-5-mini --extra_body '{"chat_template_kwargs": {"enable_thinking": false}}'
  ```

### Examples

**Note if use `pip install bbook_maker` all commands can change to `bbook_maker args`**

```shell
# Test quickly
python3 make_book.py --book_name test_books/animal_farm.epub --key ${openai_key} --model gpt-5-mini --test --language zh-hans

# Test quickly for src
python3 make_book.py --book_name test_books/Lex_Fridman_episode_322.srt --key ${openai_key} --model gpt-5-mini --test

# Or translate the whole book
python3 make_book.py --book_name test_books/animal_farm.epub --key ${openai_key} --model gpt-5-mini --language zh-hans

# Or translate the whole book using Gemini flash
python3 make_book.py --book_name test_books/animal_farm.epub --api_base https://generativelanguage.googleapis.com/v1beta/openai/ --key ${gemini_key} --model gemini-2.5-flash

# Translate an EPUB with parallel chapter processing
python3 make_book.py --book_name test_books/animal_farm.epub --key ${openai_key} --model gpt-5-mini --parallel-workers 4

# Use Gemini through its OpenAI-compatible endpoint, rotating two models
python3 make_book.py --book_name test_books/animal_farm.epub --api_base https://generativelanguage.googleapis.com/v1beta/openai/ --key ${gemini_key} --model_list gemini-2.5-flash,gemini-2.0-flash

# Set env BBM_API_KEY (or OPENAI_API_KEY) to omit --key
export BBM_API_KEY=${your_api_key}

# Translate to Japanese with context
python3 make_book.py --book_name test_books/animal_farm.epub --model gpt-5-mini --use_context --language ja

# Name the exact model the endpoint serves
python3 make_book.py --book_name test_books/animal_farm.epub --model gpt-5-mini --key ${openai_key}

**Note** any other OpenAI-compatible host works the same way
python3 make_book.py --book_name test_books/animal_farm.epub --model yi-34b-chat-0205 --key ${openai_key} --api_base "https://api.lingyiwanwu.com/v1"

# Rotate several models to spread rate limits
python3 make_book.py --book_name test_books/animal_farm.epub --model_list gpt-5-mini,gpt-4o-mini --key ${openai_key}

# Use the DeepL model with Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --api_format deepl --key ${deepl_key} --language ja

# Use Claude with Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --api_base https://api.anthropic.com --key ${claude_key} --model claude-sonnet-4-6 --language ja

# Use your own translation API with Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --api_format customapi --api_base ${custom_api} --language ja

# Any OpenAI-compatible vendor (e.g. DeepSeek)
python3 make_book.py --book_name test_books/animal_farm.epub --api_base https://api.deepseek.com/v1 --key sk-xxx --model deepseek-chat --language ja

# Translate contents in <div> and <p>
python3 make_book.py --book_name test_books/animal_farm.epub --translate-tags div,p

# Plan mode: auto-discover translatable content (poetry, blockquotes, table cells,
# ...) and batch verse lines in stanza windows; preview the plan with --plan-dry-run
python3 make_book.py --book_name test_books/animal_farm.epub --plan-dry-run
# Let the model rule on what is content and what is apparatus (see Quick Start)
python3 make_book.py --book_name test_books/animal_farm.epub --plan-classify model
# Or translate the whole partition without classifying anything
python3 make_book.py --book_name test_books/animal_farm.epub --plan-classify most

# Tweaking the prompt
python3 make_book.py --book_name test_books/animal_farm.epub --prompt prompt_template_sample.txt
# or
python3 make_book.py --book_name test_books/animal_farm.epub --prompt prompt_template_sample.json
# or
python3 make_book.py --book_name test_books/animal_farm.epub --prompt "Please translate \`{text}\` to {language}"

# Translate books download from Rakuten Kobo on kobo e-reader
python3 make_book.py --book_from kobo --device_path /tmp/kobo

# translate txt file
python3 make_book.py --book_name test_books/the_little_prince.txt --test --language zh-hans
# aggregated translation txt file
python3 make_book.py --book_name test_books/the_little_prince.txt --test --batch_size 20

# Using Caiyun model to translate
# (the api currently only support: simplified chinese <-> english, simplified chinese <-> japanese)
# the official Caiyun has provided a test token (3975l6lr5pcbvidl6jl2)
# you can apply your own token by following this tutorial(https://bobtranslate.com/service/translate/caiyun.html)
python3 make_book.py --api_format caiyun --key 3975l6lr5pcbvidl6jl2 --book_name test_books/animal_farm.epub


# Set env BBM_CAIYUN_API_KEY to omit --key
export BBM_CAIYUN_API_KEY=${your_api_key}

```

More understandable example

```shell
python3 make_book.py --book_name 'animal_farm.epub' --key sk-XXXXX --model gpt-5-mini --api_base 'https://xxxxx/v1'

# Or python3 is not in your PATH
python make_book.py --book_name 'animal_farm.epub' --key sk-XXXXX --model gpt-5-mini --api_base 'https://xxxxx/v1'
```

Azure OpenAI has no dedicated flag: point `--api_base` at the deployment's
OpenAI-compatible URL and name the deployment in `--model_list`.

## Docker

You can use [Docker](https://www.docker.com/) if you don't want to deal with setting up the environment.

```shell
# Build image
docker build --tag bilingual_book_maker .

# Run container
# "$folder_path" represents the folder where your book file locates. Also, it is where the processed file will be stored.

# Windows PowerShell
$folder_path=your_folder_path # $folder_path="C:\Users\user\mybook\"
$book_name=your_book_name # $book_name="animal_farm.epub"
$openai_key=your_api_key # $openai_key="sk-xxx"
$language=your_language # see utils.py

docker run --rm --name bilingual_book_maker --mount type=bind,source=$folder_path,target='/app/test_books' bilingual_book_maker --book_name "/app/test_books/$book_name" --key $openai_key --model gpt-5-mini --language $language

# Linux
export folder_path=${your_folder_path}
export book_name=${your_book_name}
export openai_key=${your_api_key}
export language=${your_language}

docker run --rm --name bilingual_book_maker --mount type=bind,source=${folder_path},target='/app/test_books' bilingual_book_maker --book_name "/app/test_books/${book_name}" --key ${openai_key} --model gpt-5-mini --language "${language}"
```

For example:

```shell
# Linux
docker run --rm --name bilingual_book_maker --mount type=bind,source=/home/user/my_books,target='/app/test_books' bilingual_book_maker --book_name /app/test_books/animal_farm.epub --key sk-XXX --model gpt-5-mini --test --test_num 1 --language zh-hant
```

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

[^token]: https://platform.openai.com/account/api-keys
[^black]: https://github.com/psf/black
