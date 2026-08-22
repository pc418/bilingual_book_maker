# Endpoints, models and languages

A route is chosen by the endpoint it talks to, not by a model name. There is
no built-in model list to keep up to date: `--model` takes whatever id
your endpoint serves.

```sh
bbook_maker --book_name book.epub \
  --api_base https://api.openai.com/v1 --key sk-... \
  --model gpt-5-mini --language ja
```

Old `--model` commands still work — they are rewritten into these flags and
the substitution is printed. See "Migrating from the old flags" in the README
for the full table.

## The three flags

| Flag | Meaning |
|---|---|
| `--model` | Model id, exactly as the endpoint names it. |
| `--api_base` | Endpoint URL. Defaults to the format's official host; `…/v1`, `…/v1/` and `…/v1/chat/completions` all work. |
| `--key` | API key. Comma-separate several to rotate them and spread rate limits. |
| `--api_format` | Wire format. Inferred; pass it only when the guess is wrong. |

`--model_list a,b` rotates across several models and is also what older
commands used; name a model in one flag or the other, not both.

`--api_format` is one of `openai` (default), `anthropic`, or the fixed
machine-translation engines `google`, `caiyun`, `deepl`, `deeplfree`,
`tencent`, `customapi`.

Inference goes in this order: an explicit `--api_format` wins; then the
`--api_base` host (`anthropic.com` means the anthropic shape, anything else
the OpenAI shape); then, when no endpoint was named, a model id mentioning
`claude` or `anthropic` — `anthropic/claude-sonnet-4-6` included.

A wrong guess costs one request rather than the run: if the anthropic shape
was inferred and the endpoint answers 404/405 on `/v1/messages`, the route
switches to `openai` for the rest of the run and prints why. That fallback
only applies to an endpoint you named — on Anthropic's own host a 404 means
the model does not exist, and retrying elsewhere would just fail differently.
A rejected key never triggers it.

Credentials come from `--key`, then `BBM_API_KEY`, then the format's
conventional variable — see [Environment settings](./env_settings.md). An
endpoint on localhost needs no key.

## OpenAI-compatible endpoints

Everything below is the same route with a different `--api_base`. Structured
output, `--use_context`, parallel workers, async and the Batch API are
available on all of them to the extent the endpoint itself supports them —
support is probed at runtime rather than assumed from the model name.

| Vendor | `--api_base` |
|---|---|
| OpenAI | `https://api.openai.com/v1` (default) |
| Groq | `https://api.groq.com/openai/v1` |
| xAI | `https://api.x.ai/v1` |
| DeepSeek | `https://api.deepseek.com/v1` |
| Gemini | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| Alibaba Qwen (DashScope) | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| SiliconFlow | `https://api.siliconflow.cn/v1` |
| OpenRouter | `https://openrouter.ai/api/v1` |
| Azure OpenAI | the deployment's OpenAI-compatible URL; `--model` names the deployment |
| Ollama | `http://localhost:11434/v1` |
| vLLM / LM Studio / llama.cpp | whatever host they serve |

```sh
bbook_maker --book_name book.epub \
  --api_base https://api.groq.com/openai/v1 --key gsk-... \
  --model llama-3.3-70b-versatile
```

`--extra_body` passes vendor-specific request fields on this route.

## Anthropic

```sh
bbook_maker --book_name book.epub \
  --api_base https://api.anthropic.com --key sk-ant-... \
  --model claude-sonnet-4-6 --language zh-hans
```

Any model id the endpoint serves is accepted. Claude uses one model per run,
so extra `--model_list` entries are announced and ignored rather than
silently dropped. A gateway that serves the anthropic shape from an
OpenAI-style `/v1` base is handled: the trailing `/v1` is trimmed, because
the SDK appends its own.

Classification through this format uses the prompt rung — the endpoint is not
asked to compile a schema.

## Machine-translation engines

These speak their own protocols and take no model, so naming one is an error
rather than a silent no-op.

| `--api_format` | Credential |
|---|---|
| `google` | none |
| `deeplfree` | none |
| `tencent` | none |
| `customapi` | none; the endpoint URL goes in `--api_base` |
| `caiyun` | required |
| `deepl` | required (RapidAPI DeepL Translator) |

They translate text and nothing else: no context window, no structured
output, and no plan classification. `--source_lang` reaches `customapi`
(it goes into the request body); the others detect the source themselves.

## Languages

`--language LANGUAGE` sets the target language and defaults to `zh-hans`. The
accepted choices are generated from `book_maker/utils.py`:

```sh
bbook_maker --help
bbook_maker --book_name book.epub --api_format google --language ja
```

`--source_lang` states the source language for endpoints that want it rather
than detecting it; the default is `auto`. Not every endpoint supports every
language the parser accepts.
