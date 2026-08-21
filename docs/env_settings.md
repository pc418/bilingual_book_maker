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

## Prompt overrides

```
export BBM_CHATGPTAPI_USER_MSG_TEMPLATE=${your_prompt_template}
export BBM_CHATGPTAPI_SYS_MSG=${your_system_message}
```
