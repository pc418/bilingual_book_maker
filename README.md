**[中文](./README-CN.md) | English**

# bilingual_book_maker

The bilingual_book_maker is an AI translation tool that uses ChatGPT to assist users in creating multi-language versions of epub/txt/md/srt/pdf files and books. This tool is exclusively designed for translating epub and other public domain works and is not intended for copyrighted works. Before using this tool, please review the project's **[disclaimer](./disclaimer.md)**.

![image](https://user-images.githubusercontent.com/15976103/222317531-a05317c5-4eee-49de-95cd-04063d9539d9.png)

## Supported endpoints

A translator is an endpoint, not a model name: `--api_base` (defaults to the format's
official host), `--key`, and `--model <id>` exactly as the endpoint names it —
`gpt-5-mini`, `claude-sonnet-4-6`, `deepseek-chat`. That covers OpenAI, Anthropic, and
anything speaking the OpenAI shape (Gemini's OpenAI-compatible endpoint, Groq, xAI,
DashScope, DeepSeek, OpenRouter, Ollama, vLLM, …). `--api_format` selects the fixed
engines (`google`, `caiyun`, `deepl`, `deeplfree`, `tencent`, `customapi`) and `codex`.
The old preset names (`--model gpt4`, `--model gemini`, `--openai_key`, …) still work and
are rewritten with a note — see [Migrating from the old flags](#migrating-from-the-old-flags).
Per-vendor base URLs are in [Models and languages](./docs/model_lang.md).

## Preparation

1. ChatGPT or OpenAI token [^token]
2. epub/txt/md/pdf books
3. Environment with internet access or proxy
4. Python 3.10+

## Quick Start

A sample book, `test_books/animal_farm.epub`, is provided for testing purposes.

```shell
pip install -r requirements.txt
python3 make_book.py --book_name test_books/animal_farm.epub --key ${openai_key} --test
OR
pip install -U bbook_maker
bbook_maker --book_name test_books/animal_farm.epub --key ${openai_key} --test
```

## Translate Service

- Use `--key` to pass the API key (the old `--openai_key` still works). If you have multiple keys, separate them by commas (xxx,xxx,xxx) to reduce errors caused by API call limits.
  Or, just set environment variable `BBM_API_KEY` (or `OPENAI_API_KEY`) instead.
- A sample book, `test_books/animal_farm.epub`, is provided for testing purposes.
- `--model` names the model exactly as the endpoint does, e.g. `--model gpt-5-mini`. To rotate several models against rate limits, use `--model_list gpt-5-mini,gpt-4o-mini` instead. The old presets (`--model gpt4`, `--model gpt4omini`, `--model openai --model_list …`) still work and print what they became.
- On any OpenAI-compatible endpoint you can add `--use_context` to add a context paragraph to each passage sent to the model for translation (see below).

* DeepL
  Support DeepL model [DeepL Translator](https://rapidapi.com/splintPRO/api/dpl-translator) need pay to get the token

  ```
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format deepl --key ${deepl_key}
  ```

* DeepL free

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format deeplfree
  ```

* [Claude](https://console.anthropic.com/docs)

  Use [Claude](https://console.anthropic.com/docs) model to translate

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --model claude-sonnet-4-6 --key ${claude_key}
  ```

* Google Translate

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format google
  ```

* Caiyun Translate

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format caiyun --key ${caiyun_key}
  ```

* Gemini

  Support Google [Gemini](https://aistudio.google.com/app/apikey) through its OpenAI-compatible endpoint: name the model (eg `gemini-2.5-flash` or `gemini-2.0-flash`), or rotate several with `--model_list gemini-2.5-flash,gemini-2.0-flash`. The old `--model gemini` / `--model geminipro` still work.

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_base https://generativelanguage.googleapis.com/v1beta/openai/ --model gemini-2.5-flash --key ${gemini_key}
  ```

* Qwen

  Support Alibaba Cloud [Qwen-MT](https://bailian.console.aliyun.com/) specialized translation model. Supports 92 languages with features like terminology intervention and translation memory.
  Use `--model qwen-mt-turbo` for faster/cheaper translation, or `--model qwen-mt-plus` for higher quality; both go through DashScope's OpenAI-compatible endpoint, and DashScope's `translation_options` travel in `--extra_body`.

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --key ${qwen_key} --model qwen-mt-turbo --language "Simplified Chinese"
  ```

* [Tencent TranSmart](https://transmart.qq.com)

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_format tencent
  ```

* [xAI](https://x.ai)

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_base https://api.x.ai/v1 --model grok-4 --key ${xai_key}
  ```

* [OrcaRouter](https://www.orcarouter.ai)

  Support [OrcaRouter](https://www.orcarouter.ai) gateway: `--model orcarouter` selects the
  `orcarouter/auto` smart-routing model, and `--model orcarouter/<model>` any other, with no
  `--api_base` needed. It also runs gateway-level, zero-trust
  security for AI agents on the same endpoint — screening every prompt/response and
  governing every tool call on a default-deny basis, with no application code changes.

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --model orcarouter --key ${orcarouter_key}
  ```

* [Ollama](https://github.com/ollama/ollama)

  Support [Ollama](https://github.com/ollama/ollama) self-host models,
  If ollama server is not running on localhost, use `--api_base http://x.x.x.x:port/v1` to point to the ollama server address

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_base http://localhost:11434/v1 --model ${ollama_model_name}
  ```

* [groq](https://console.groq.com/keys)

  GroqCloud currently supports models: you can find from [Supported Models](https://console.groq.com/docs/models)

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --api_base https://api.groq.com/openai/v1 --model llama3-8b-8192 --key [your_key]
  ```

* [Codex](https://developers.openai.com/codex/cli)

  Translate on your ChatGPT/Codex plan allowance instead of API credits. Install the
  [Codex CLI](https://developers.openai.com/codex/cli) and run `codex login` once — a
  `codex app-server` sidecar owns that session, so no key is needed. `--model_list` is
  optional and defaults to `gpt-5.6-luna`. One thread is reused for the whole book and
  compacted at `--context-compact-at`; the sidecar runs sandboxed, with shell, MCP
  servers, browsing and hooks off.

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --model codex --language zh-hans
  ```

* Custom API Provider

  If the built-in routes don't cover your needs, you can define custom providers via a JSON config file. This lets you use any OpenAI-compatible API (DeepSeek, SiliconFlow, local proxies, etc.) by name, without repeating `--api_base` and the key on every command.

  Create `bbm_providers.json` in the current directory (or `~/.bbm/providers.json` for global config):

  ```json
  {
    "providers": {
      "deepseek": {
        "api_style": "openai",
        "base_url": "https://api.deepseek.com/v1",
        "default_models": ["deepseek-chat", "deepseek-reasoner"],
        "env_key": "BBM_DEEPSEEK_API_KEY"
      },
      "siliconflow": {
        "api_style": "openai",
        "base_url": "https://api.siliconflow.cn/v1",
        "default_models": ["Qwen/Qwen2.5-72B-Instruct"],
        "env_key": "BBM_SILICONFLOW_API_KEY"
      }
    }
  }
  ```

  Config fields:

  | Field | Required | Description |
  |-------|----------|-------------|
  | `api_style` | Yes | Translator interface style. Supported: `openai`, `claude`, `gemini`, `qwen` |
  | `base_url` | No | API endpoint URL. Falls back to the api_style's default |
  | `default_models` | No | Default model list. Required if `--model_list` is not provided |
  | `env_key` | No | Environment variable name for API key. Required if `--api_key` is not provided |

  Priority: project-level `./bbm_providers.json` overrides global `~/.bbm/providers.json`.

  `--model` names a model at that provider; without it the first of `default_models` is used.

  ```shell
  python3 make_book.py --provider deepseek --key sk-xxx --book_name test_books/animal_farm.epub

  export BBM_DEEPSEEK_API_KEY=sk-xxx
  python3 make_book.py --provider deepseek --book_name test_books/animal_farm.epub

  python3 make_book.py --provider deepseek --key sk-xxx --model deepseek-reasoner --book_name test_books/animal_farm.epub
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
| `--model qwen` / `qwen-mt-turbo` / `qwen-mt-plus` | `--api_base https://dashscope.aliyuncs.com/compatible-mode/v1 --model qwen-mt-*` |
| `--model google` / `caiyun` / `deepl` / `deeplfree` / `tencentransmart` | `--api_format google` / `caiyun` / `deepl` / `deeplfree` / `tencent` |
| `--custom_api URL` | `--api_format customapi --api_base URL` |
| `--openai_key` / `--claude_key` / `--gemini_key` / `--groq_key` / `--xai_key` / `--qwen_key` / `--caiyun_key` / `--deepl_key` / `--api_key` | `--key` |
| `--ollama_model M` | `--api_base http://localhost:11434/v1 --model M` |
| `--deployment_id D` | `--model D`, with `--api_base` rewritten to the deployment's `/openai/v1` path |
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

- `--model`:

  The model id, exactly as the endpoint names it (`gpt-5-mini`, `claude-sonnet-4-6`, `deepseek-chat`), sent to `--api_base` with `--key`. The `openai` format defaults to `gpt-5.6-luna`; the `anthropic` format needs an id. The old preset values below are still accepted and rewritten to a real id with a note:

  | Model | Key Source | Notes |
  |-------|-----------|-------|
  | `chatgptapi` | `--openai_key` / `BBM_OPENAI_API_KEY` | GPT-3.5-turbo. Auto-detects available models from API |
  | `gpt4` | `--openai_key` / `BBM_OPENAI_API_KEY` | GPT-4 family. Auto-balances across available GPT-4 variants |
  | `gpt4omini` | `--openai_key` / `BBM_OPENAI_API_KEY` | GPT-4o-mini |
  | `gpt4o` | `--openai_key` / `BBM_OPENAI_API_KEY` | GPT-4o |
  | `gpt5mini` | `--openai_key` / `BBM_OPENAI_API_KEY` | GPT-5-mini |
  | `o1preview` | `--openai_key` / `BBM_OPENAI_API_KEY` | o1-preview |
  | `o1` | `--openai_key` / `BBM_OPENAI_API_KEY` | o1 |
  | `o1mini` | `--openai_key` / `BBM_OPENAI_API_KEY` | o1-mini |
  | `o3mini` | `--openai_key` / `BBM_OPENAI_API_KEY` | o3-mini |
  | `openai` | `--openai_key` / `BBM_OPENAI_API_KEY` | `--model openai --model_list X` is now just `--model_list X` |
  | `codex` | No key — `codex login` (Codex CLI) | Your ChatGPT/Codex plan allowance, through a local `codex app-server` sidecar |
  | `claude` | `--claude_key` / `BBM_CLAUDE_API_KEY` | Rewritten to `claude-haiku-4-5-20251001`; any `claude-*` id passes through as-is |
  | `gemini` | `--gemini_key` / `BBM_GOOGLE_GEMINI_KEY` | Gemini Flash. Supports `--model_list` |
  | `geminipro` | `--gemini_key` / `BBM_GOOGLE_GEMINI_KEY` | Gemini Pro |
  | `groq` | `--groq_key` / `BBM_GROQ_API_KEY` | **Requires `--model_list`** |
  | `xai` | `--xai_key` / `BBM_XAI_API_KEY` | Grok |
  | `orcarouter` | `--key` / `BBM_ORCAROUTER_API_KEY` | Still a real route, not a preset: `orcarouter/auto` on OrcaRouter's endpoint; `--model orcarouter/<model>` picks another |
  | `qwen-mt-turbo` | `--qwen_key` / `BBM_QWEN_API_KEY` | Qwen fast translation model |
  | `qwen-mt-plus` | `--qwen_key` / `BBM_QWEN_API_KEY` | Qwen high-quality translation model |
  | `google` | N/A | Free. No API key needed |
  | `caiyun` | `--caiyun_key` / `BBM_CAIYUN_API_KEY` | Caiyun |
  | `deepl` | `--deepl_key` / `BBM_DEEPL_API_KEY` | DeepL (paid) |
  | `deeplfree` | N/A | DeepL Free |
  | `tencentransmart` | N/A | Tencent TranSmart. Free |
  | `customapi` | `--custom_api` / `BBM_CUSTOM_API` | Custom translation API |

  Anything else is an endpoint: `--api_base <url> --key <key> --model <id>`.

- `--key`:

  API key for the endpoint; comma-separate several to rotate them past per-key rate limits. Falls back to `$BBM_API_KEY`, then the format's conventional variable (`$OPENAI_API_KEY`, `$ANTHROPIC_API_KEY`, `$BBM_CAIYUN_API_KEY`, `$BBM_DEEPL_API_KEY`).

- `--api_format`:

  The wire format the endpoint speaks: `openai` (default), `anthropic`, `codex`, or a fixed engine — `google`, `caiyun`, `deepl`, `deeplfree`, `tencent`, `customapi`. Inferred when omitted: an `api.anthropic.com` host, or a model id mentioning `claude`, means `anthropic`; everything else is `openai`. Pass it only when the guess is wrong, or to select an engine.

- `--test`:

  Use `--test` option to preview the result if you haven't paid for the service. Note that there is a limit and it may take some time.

- `--language`:

  Set the target language like `--language "Simplified Chinese"`. Default target language is `"Simplified Chinese"`.
  Read available languages by helper message: `python make_book.py --help`

- `--source_lang`:

  Source language, for endpoints that want it stated (currently only `--api_format customapi`). Default: auto-detect.

- `--proxy`:

  Use `--proxy` option to specify proxy server for internet access. Enter a string such as `http://127.0.0.1:7890`.

- `--resume`:

  Use `--resume` option to manually resume the process after an interruption.

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --model google --resume
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
  - `most`: translate the whole partition, no classification.
  - `model`: an LLM rules on the uncertain signatures first (headings, the prose spine and
    poetry groups are never asked about), then the run continues. Use
    `--plan-classify-model X` to pick a different model for it — naming one implies this
    mode, and a failure then aborts instead of falling back.
  - `agent`: makes no API call. Writes the plan, prints a block of instructions to paste
    into a coding-agent session (Claude Code, Codex, ...), and **stops before translating**.
    Edit the actions, then rerun the same command to translate.

  In plan mode `--translate-tags` is ignored — the plan partitions the whole book.

  - `--plan-dry-run`: print the per-signature coverage table, write `<book>_plan.json`, and
    exit. No API key or credits needed. Honors `--only_filelist` / `--exclude_filelist`.
  - `<book>_plan.json`: edit a signature's `"action"` to `"skip"` to exclude it from the
    real run; the file is never overwritten once it exists (delete it to regenerate).
    Each row carries up to 5 real `samples` so you can judge without opening the epub.
  - `--plan-min-coverage` (default 0.5): plan mode aborts if the plan covers less than this
    fraction of the book's text, instead of silently translating a fraction of it.

  ```shell
  # inspect what would be translated (free, no key needed)
  python3 make_book.py --book_name my_book.epub --plan-dry-run
  # translate the whole partition
  python3 make_book.py --book_name my_book.epub --key ${key} --plan-classify most
  # let a model triage the apparatus first
  python3 make_book.py --book_name my_book.epub --key ${key} --plan-classify model
  # or hand the triage to a coding agent (stops, prints instructions, then rerun)
  python3 make_book.py --book_name my_book.epub --key ${key} --plan-classify agent
  ```

- `--exclude-translate-tags`:

  Use `--exclude-translate-tags` to exclude content within specified HTML tags from translation. This is useful for preserving code blocks, preformatted text, or other special content. Use comma to separate multiple tags.
  Default: `sup,code`.
  For example: `--exclude-translate-tags code,pre`

  **Tip**: Use `--exclude-translate-tags ""` to translate all content including code blocks (overrides the default exclusion).

- `--book_from`:

  Use `--book_from` option to specify e-reader type (Now only `kobo` is available), and use `--device_path` to specify the mounting point.

- `--api_base`:

  The endpoint URL — `https://api.openai.com/v1`, a Cloudflare Workers proxy, a gateway, or `http://localhost:11434/v1` for Ollama. Defaults to the format's official host. `https://host/v1`, with a trailing slash, or the full `.../chat/completions` URL all mean the same thing.

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
  For subsequent passages, it will amend the summary to include details from the most recent passage, creating a running one-paragraph context payload of the important details of the entire translated work. This improves consistency of flow and tone throughout the translation. This option is available on OpenAI-compatible endpoints.

- `--context_paragraph_limit`:

  Use `--context_paragraph_limit` to set a limit on the number of context paragraphs when using the `--use_context` option. This applies to window mode only.

- `--use_context session`:

  `--use_context` also takes a mode. Bare `--use_context` (or `--use_context window`) is the behaviour described above. `--use_context session` instead keeps a single append-only history of everything translated so far, so a model endpoint that supports prompt caching re-reads it at its cache rate. Context can then grow to chapter length for less money than window mode spends on a few paragraphs. When the history reaches the compact budget, the model is asked for a translator handoff report, which seeds the next window and is appended to `<book>_handoff.md`. If the endpoint never reports cached tokens, a warning is printed, since without caching this mode costs more than window mode.

- `--context-compact-at`:

  Session mode only. The estimated-token budget the history may reach before it is compacted into a handoff report. Default `8000`, minimum `500`.

  At `8000` a run costs between roughly 0.5x and 1.1x what window mode costs, while carrying several times the context — the exact ratio depends on how cheaply your endpoint prices cached input. `--context-compact-at 2500` is the cheapest setting (about 0.4-0.5x) if you would rather have that than the longer context.

- `--parallel-workers`:

  Use `--parallel-workers` to process EPUB chapters or Markdown batches/sections in
  parallel. Values greater than `1` spin up multiple workers (recommended: `2-4`) and
  automatically fall back to sequential mode when there is only one unit of work. Other
  input loaders currently accept this shared CLI option but do not parallelize their work.

- `--temperature`:

  Use `--temperature` to set the sampling temperature on the `openai` and `anthropic` formats. It is not sent when it equals the API default, and dropped for a model that rejects it.
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

  Pass additional JSON parameters in every request on the `openai` format — any
  OpenAI-compatible endpoint. The `anthropic` format and the fixed engines ignore it.
  Provide a JSON string with the desired parameters.

  ```shell
  python3 make_book.py --book_name test_books/animal_farm.epub --key ${openai_key} --extra_body '{"chat_template_kwargs": {"enable_thinking": false}}'
  ```

- `--provider`:

  Use a custom provider defined in `bbm_providers.json`: its `base_url`, `api_style`, `default_models` and `env_key` fill in `--api_base`, `--api_format`, `--model` and the key. Anything you pass explicitly wins. See the "Custom API Provider" section above.

- `--api_key`:

  Old spelling of `--key`, still accepted.

### Examples

**Note if use `pip install bbook_maker` all commands can change to `bbook_maker args`**

```shell
# Test quickly
python3 make_book.py --book_name test_books/animal_farm.epub --key ${openai_key} --test --language zh-hans

# Test quickly for src
python3 make_book.py --book_name test_books/Lex_Fridman_episode_322.srt --key ${openai_key} --test

# Or translate the whole book
python3 make_book.py --book_name test_books/animal_farm.epub --key ${openai_key} --language zh-hans

# Or translate the whole book using Gemini flash
python3 make_book.py --book_name test_books/animal_farm.epub --api_base https://generativelanguage.googleapis.com/v1beta/openai/ --model gemini-2.5-flash --key ${gemini_key}

# Translate an EPUB with parallel chapter processing
python3 make_book.py --book_name test_books/animal_farm.epub --key ${openai_key} --parallel-workers 4

# Rotate two Gemini models
python3 make_book.py --book_name test_books/animal_farm.epub --api_base https://generativelanguage.googleapis.com/v1beta/openai/ --model_list gemini-2.5-flash,gemini-2.0-flash --key ${gemini_key}

# Set env OPENAI_API_KEY (or BBM_API_KEY) to omit --key
export OPENAI_API_KEY=${your_api_key}

# Translate to Japanese with context
python3 make_book.py --book_name test_books/animal_farm.epub --use_context --language ja

# Name the exact model the endpoint serves
python3 make_book.py --book_name test_books/animal_farm.epub --model gpt-4-1106-preview --key ${openai_key}

**Note** you can use other `openai like` model in this way
python3 make_book.py --book_name test_books/animal_farm.epub --model yi-34b-chat-0205 --key ${openai_key} --api_base "https://api.lingyiwanwu.com/v1"

# Rotate several models to spread rate limits
python3 make_book.py --book_name test_books/animal_farm.epub --model_list gpt-4-1106-preview,gpt-4-0125-preview,gpt-3.5-turbo-0125 --key ${openai_key}

# Use DeepL with Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --api_format deepl --key ${deepl_key} --language ja

# Use Claude with Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --model claude-sonnet-4-6 --key ${claude_key} --language ja

# Use your own translation API with Japanese
python3 make_book.py --book_name test_books/animal_farm.epub --api_format customapi --api_base ${custom_api} --language ja

# Any OpenAI-compatible vendor (e.g. DeepSeek)
python3 make_book.py --book_name test_books/animal_farm.epub --api_base https://api.deepseek.com/v1 --key sk-xxx --model deepseek-chat --language ja

# Translate contents in <div> and <p>
python3 make_book.py --book_name test_books/animal_farm.epub --translate-tags div,p

# Plan mode: auto-discover translatable content (poetry, blockquotes, table cells,
# ...) and batch verse lines in stanza windows; preview the plan with --plan-dry-run
python3 make_book.py --book_name test_books/animal_farm.epub --plan-dry-run
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
python3 make_book.py --book_name 'animal_farm.epub' --key sk-XXXXX --api_base 'https://xxxxx/v1'

# Or python3 is not in your PATH
python make_book.py --book_name 'animal_farm.epub' --key sk-XXXXX --api_base 'https://xxxxx/v1'
```

Microsoft Azure Endpoints

```shell
python3 make_book.py --book_name 'animal_farm.epub' --key XXXXX --api_base 'https://example-endpoint.openai.azure.com/openai/v1' --model 'deployment-name'

# Or python3 is not in your PATH
python make_book.py --book_name 'animal_farm.epub' --key XXXXX --api_base 'https://example-endpoint.openai.azure.com/openai/v1' --model 'deployment-name'
```

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

docker run --rm --name bilingual_book_maker --mount type=bind,source=$folder_path,target='/app/test_books' bilingual_book_maker --book_name "/app/test_books/$book_name" --key $openai_key --language $language

# Linux
export folder_path=${your_folder_path}
export book_name=${your_book_name}
export openai_key=${your_api_key}
export language=${your_language}

docker run --rm --name bilingual_book_maker --mount type=bind,source=${folder_path},target='/app/test_books' bilingual_book_maker --book_name "/app/test_books/${book_name}" --key ${openai_key} --language "${language}"
```

For example:

```shell
# Linux
docker run --rm --name bilingual_book_maker --mount type=bind,source=/home/user/my_books,target='/app/test_books' bilingual_book_maker --book_name /app/test_books/animal_farm.epub --key sk-XXX --test --test_num 1 --language zh-hant
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
