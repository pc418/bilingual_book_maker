# Command Line Options

`book_maker/cli.py` and `python3 make_book.py --help` are the runtime source of truth.
The inventory below is checked against every long option registered with `argparse`; the
sections after it provide additional notes for selected workflows.

## Complete option inventory

### Input, scope, and output

| Option | Purpose |
|---|---|
| `--book_name PATH` | Input EPUB, TXT, Markdown, SRT, or PDF path (required). |
| `--book_from kobo` | Import from a Kobo device instead of a normal source path. |
| `--device_path PATH` | Kobo mount path used with `--book_from`. |
| `--language LANGUAGE` | Target language; default `zh-hans`. |
| `--source_lang LANGUAGE` | Source language for models such as Qwen; default `auto`. |
| `--single_translate` | Output translation only instead of bilingual text. |
| `--no_disclosure` | Do not mark the epub as a machine translation (translator credit, description line, closing note). |
| `--translate-tags TAGS` | Comma-separated EPUB tags; default `p`, ignored in plan mode. |
| `--exclude-translate-tags TAGS` | EPUB ancestor tags to exclude; default `sup,code`; `""` clears it. |
| `--allow_navigable_strings` | Include otherwise untagged EPUB strings; redundant in plan mode. |
| `--only_filelist FILES` | Include only these comma-separated internal EPUB files. |
| `--exclude_filelist FILES` | Exclude these comma-separated internal EPUB files. |
| `--translation_style CSS` | CSS applied to translated EPUB entries. |
| `--translation_color COLOR` | Color-only shorthand; `--translation_style` takes precedence. |
| `--pdf_layout MODE` | Additional PDF output: `none`, `top-bottom`, `side-by-side`, or `all`. |
| `--retranslate OUT FILE START END` | Retranslate an EPUB range in an existing output. |

### EPUB plan mode

| Option | Purpose |
|---|---|
| `--plan-dry-run` | Build and print the EPUB plan, write `<book>_plan.json` with every `action` still `null`, and exit. No credentials needed. |
| `--plan-classify {auto,none,all,model,agent}` | No plan, the whole partition, model triage, or coding-agent triage. Default `auto`: model triage on an epub whose endpoint is verified to apply a strict JSON schema, tag mode otherwise. |
| `--plan-classify-model MODEL` | Classification model; implies model mode and conflicts with `all`/`agent`. |
| `--plan-min-coverage FRACTION` | Fail if selected planned text is below this fraction; default `0.5`. |
| `--poetry-group-size N` | Maximum verse lines per planned translation request; default `8`. |

### Translation and execution

| Option | Purpose |
|---|---|
| `--test` | Translate only a preview sample. |
| `--test_num N` | Number of test units; default `10`. |
| `--resume` | Continue from the loader's saved checkpoint. |
| `--prompt VALUE_OR_FILE` | Prompt config: `user` (must contain `{text}`), `system`, and `style`. A `style` goes into every request and verbatim into each handoff report. On the `codex` format `system` is appended to the built-in instructions. |
| `--temperature FLOAT` | Sampling temperature; default `1.0`. |
| `--use_context [window\|session]` | Send earlier paragraphs as context. Bare or `window`: re-send the last few source/translation pairs (the long-standing behaviour). `session`: one append-only history, re-read at the endpoint's prompt-cache rate. |
| `--context_paragraph_limit N` | Window mode only: context history limit. Parser default `0` means the translator default (3 paragraphs for ChatGPT), not zero history. |
| `--context-compact-at N` | Session mode only: estimated-token budget before the history is compacted into a handoff report. Default `8000`, minimum `500`; `2500` is the cheapest setting. `0` sizes the budget from the model's context window (90% of it, of the smallest window when several models are in play) on the openai, anthropic and codex routes. When the window cannot be read, the openai route stops; the other two say so and use the default. |
| `--no-context-compact` | Session mode only: skip the handoff report. The window still rolls over at the budget, but the next one starts empty. |
| `--accumulated_num N` | EPUB token/character accumulation and SRT subtitle-block character batching (capped at 512 for SRT); ignored in EPUB plan mode. |
| `--batch_size N` | Aggregated unit count for loaders that support it. |
| `--block_size N` | Merge paragraphs into delimiter-translated blocks. |
| `--sentence_mode` | Translate EPUB paragraphs sentence by sentence; incompatible with plan mode. |
| `--parallel-workers N` | Parallel EPUB chapters or Markdown batches/sections; default `1`. Refused with `--use_context session` (one history) and on the `codex` format (one thread). |
| `--batch` | Submit an EPUB ChatGPT Batch API job; incompatible with plan mode. |
| `--batch-use` | Consume a previously submitted batch job; incompatible with plan mode. |
| `--extra_body JSON` | Extra request fields for the `openai` route; other formats ignore it and say so. |
| `--quiet` | Suppress EPUB progress bars and paragraph echoes, not reports/errors. |
| `--proxy URL` | Set HTTP/HTTPS proxy environment variables for the run. |

### Endpoint and credentials

A route is an endpoint, not a model name.

| Option | Purpose |
|---|---|
| `--model MODEL` | The model id, exactly as the endpoint names it (`gpt-5-mini`, `claude-sonnet-4-6`, `openai/gpt-5-mini`). Defaults to `gpt-5.6-luna` on the `openai` format; the `anthropic` format needs one. Old alias values are rewritten with a note. |
| `--api_base URL` | The endpoint. Defaults to the format's official host. A pasted `…/v1/chat/completions` or a trailing slash is trimmed. |
| `--key KEY` | API key; comma-separate several to rotate them. Prefer `BBM_API_KEY` or the format's own variable. |
| `--api_format FORMAT` | The API the endpoint speaks: `openai` (default), `anthropic`, `codex`, `google`, `caiyun`, `deepl`, `deeplfree`, `tencent`, `customapi`. Inferred from the `--api_base` host, else from a `claude`/`anthropic` model id. |
| `--model codex` | The Codex CLI sidecar on a ChatGPT plan, the same as `--api_format codex`. It runs `gpt-5.6-luna`; `--api_format codex --model <id>` names another (the sidecar also offers `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.5`, `gpt-5.2`). |
| `--model orcarouter` | The OrcaRouter gateway and its smart-routing model `orcarouter/auto`. Needs no `--api_base`; one you pass wins. The key comes from `BBM_ORCAROUTER_API_KEY`. Not a legacy alias: nothing is rewritten. |
| `--model_list IDS` | Several model ids to rotate across, comma-separated. A single model belongs in `--model`; naming a model in both flags is an error. |
| `--source_lang LANG` | Source language, for endpoints that want it stated; default `auto`. |
| `--provider NAME` | A named endpoint from `bbm_providers.json` (this directory) or `~/.bbm/providers.json`; the project file wins on a shared name. Its `base_url`, `api_style` (`openai` or `anthropic`), `default_models` and `env_key` stand in for `--api_base`, `--api_format`, `--model`/`--model_list` and the key. Flags you pass yourself win. |

A gateway that serves Claude models speaks the OpenAI shape, and a gateway
`--api_base` is taken to be that shape. The anthropic format is inferred only
on Anthropic's own host, or from a `claude` model id with no `--api_base`. A
gateway asked for the anthropic shape it does not serve answers 404, and the
run stops naming `--api_format openai` as the fix.

Key lookup order: `--key` (`--api_key` is the same flag), then `BBM_API_KEY`,
then `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `BBM_CAIYUN_API_KEY` /
`BBM_DEEPL_API_KEY` depending on the format. Endpoints on localhost need no
key.

The old `--model` preset names, the per-vendor `--*_key` flags,
`--ollama_model`, `--deployment_id` and `--interval` are no longer in the
parser, but old command lines still run: `book_maker/legacy_cli.py` rewrites
them into the flags above before the run starts and prints each rewrite. The
table is in [Migrating from the old flags](migration.md). The old
per-vendor key variables (`BBM_GROQ_API_KEY`, `BBM_GOOGLE_GEMINI_KEY`, …) are
still read for the route that used them.

Do not put secrets directly on a shared command line. Environment variables are safer for
agent and CI use. The CLI does **not** load `.env` files itself: export the variables first,
or source a local git-ignored file before running, for example
`set -a; source .env; set +a; bbook_maker ...`.

## Test translate
`--test` <br>

Use this option to preview the result if you haven't paid for the service or just want to test. Note that there is a limit and it may take some time.

```sh
bbook_maker --book_name test_books/Lex_Fridman_episode_322.srt --key ${openai_key} --model gpt-5-mini  --test
```

```sh
bbook_maker --book_name test_books/animal_farm.epub --key ${openai_key} --model gpt-5-mini  --test --language zh-hans
```

`--test_num <TEST_NUM>`<br>

Use this option to set how many paragraph you want to translate for testing. Default is 10.

## Resume
`--resume` <br>

Use this option to manually resume the process after an interruption.

## Retranslate (epub only)
`--retranslate <translated_filepath> <file_name_in_epub> <start_str> <end_str>`<br>

If a file in an EPUB is not translated well, this re-translates part of it separately.
Argparse requires all four values. Use an empty `end_str` to retranslate only the starting
tag; an empty `file_name_in_epub` enables automatic filename lookup.

- Retranslate from start_str to end_str's tag:

        bbook_maker --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' 'index_split_002.html' 'in spite of the present book shortage which' 'This kind of thing is not a good symptom. Obviously'

- Retranslate the `start_str` tag (empty fourth value):
        
        bbook_maker --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' 'index_split_002.html' 'in spite of the present book shortage which' ''

- Retranslate the `start_str` tag and auto-find the filename (empty second and fourth values):
        
        bbook_maker --book_name "test_books/animal_farm.epub" --retranslate 'test_books/animal_farm_bilingual.epub' '' 'in spite of the present book shortage which' ''

**Warning:**

**It deletes from the tag at start_str of the finished book to the next tag at end_str, and then re-translates.**

**Therefore, make sure the tag after `end_str` is translated content. When `end_str` is an empty string, the tag after `start_str` is used. There can be missing translations between the two strings, but a non-translated end boundary will cause problems.**




## Customize output style (epub only)
`--translation_style <TRANSLATION_STYLE>`<br>

Support changing the output style of epub files.

    bbook_maker --book_name test_books/animal_farm.epub --translation_style "color: #4a4a4a; font-style: normal; background-color: #f7f7f7; padding: 5px; margin: 10px 0; border-radius: 5px;"

![output_style](https://user-images.githubusercontent.com/89069008/226104545-7c029bb1-5325-46d4-a1eb-ec4e7bbaee97.png)
## Proxy
`--proxy <PROXY>` <br>

Use this option to specify proxy server for internet access. Enter a string such as `http://127.0.0.1:7890` .

## API base
`--api_base <API_BASE_URL>`<br>

If you want to change api_base like using Cloudflare Workers, use this option to support it.<br>

    bbook_maker --book_name 'animal_farm.epub' --key sk-XXXXX --model gpt-5-mini --api_base 'https://xxxxx/v1'
**Note: the api url should be '`https://xxxx/v1`'. Quotation marks are required.**

## Microsoft Azure Endpoints

Azure has no flag of its own. Point `--api_base` at the deployment's
OpenAI-compatible URL and name the deployment in `--model`:

    bbook_maker --book_name 'animal_farm.epub' --key XXXXX --api_base 'https://example-endpoint.openai.azure.com/openai/v1' --model 'deployment-name'

## Batch size (txt only)
`--batch_size`<br>

Use this parameter to specify the number of lines for batch translation. Default is 10. (Currently only effective for txt files).
```sh
python3 make_book.py --book_name test_books/the_little_prince.txt --test --batch_size 20
```

## Accumulated Num
`--accumulated_num <ACCUMULATED_NUM>`<br>

Wait for how many tokens have been accumulated before starting the translation. gpt3.5 limits the total_token to 4090. 

For example, if you use --accumulated_num 1600, maybe openai will
output 2200 tokens and maybe 200 tokens for other messages in the system messages user messages. 1600+2200+200=4000, so you are close to the limit. 

You have to choose your own
value, there is no way to tell if the limit is reached before sending request.
