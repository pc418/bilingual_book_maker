# Environment Settings

You can write credentials into the environment to skip `--key`.

## API key

`BBM_API_KEY` covers every format, so one variable is usually enough:

```
export BBM_API_KEY=${your_api_key}
```

If it is not set, each format also looks at the variable people already
export for that vendor:

| `--api_format` | Fallback variables |
|---|---|
| `openai` | `OPENAI_API_KEY`, `BBM_OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY`, `BBM_CLAUDE_API_KEY` |
| `caiyun` | `BBM_CAIYUN_API_KEY` |
| `deepl` | `BBM_DEEPL_API_KEY` |

`google`, `deeplfree`, `tencent` and `customapi` need no key, and neither
does an endpoint on localhost.

The CLI does not read `.env` files. Export the variables first, or source a
git-ignored file before running: `set -a; source .env; set +a; bbook_maker ...`

## A provider's own variable

`--provider NAME` reads the endpoint from `bbm_providers.json` in the working
directory, else `~/.bbm/providers.json`. When that entry has an `env_key`,
the named variable is consulted for the key ahead of `BBM_API_KEY` and the
fallbacks above — it names the endpoint being called, so its own key is the
right one. The file holds the address and the variable name, never a secret.
See [Endpoints, models and languages](./model_lang.md#named-endpoints---provider).
`--model orcarouter` reads `BBM_ORCAROUTER_API_KEY` the same way.

## Old per-vendor variables

`BBM_GROQ_API_KEY`, `BBM_GOOGLE_GEMINI_KEY`, `BBM_XAI_API_KEY` and
`BBM_QWEN_API_KEY` are still read when an old-style command implies that
route (see "Migrating from the old flags" in the README). They are not
consulted for a command written in the new flags, where `--api_base` decides
the endpoint and the key must match it.

## Prompt overrides

```
export BBM_CHATGPTAPI_USER_MSG_TEMPLATE=${your_prompt_template}
export BBM_CHATGPTAPI_SYS_MSG=${your_system_message}
```
