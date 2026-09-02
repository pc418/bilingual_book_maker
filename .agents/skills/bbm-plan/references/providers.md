# Routes: model name → endpoint shape → flags

Loaded from SKILL.md §0/§1b. Everything here is verified against the code
(`book_maker/cli.py`, `book_maker/translator/`) and, where marked, against a
live gateway on 2026-08-07.

## The one rule that decides everything

`--model` only accepts keys of `MODEL_DICT`
(`book_maker/translator/__init__.py`) — argparse rejects anything else. So a
model id the repo has never heard of (`gpt-5.6-luna`, `claude-haiku-4.5`,
`deepseek-v4-flash`) can only arrive through a flag that carries an
arbitrary string. **This skill's route is `--provider`** (SKILL.md §0): one
entry names the endpoint, the shape, the model and the key variable, so
none of them has to be retyped per run.

| the endpoint | how the run reaches it |
|---|---|
| an entry in `bbm_providers.json` / `~/.bbm/providers.json` | `--provider NAME`, plus `--model_list "$MODEL"` only to override the entry's model |
| an OpenAI-shaped host with no entry | `--model openai --model_list "$MODEL" --api_base "$ROOT/v1"` |
| a MODEL_DICT key that already means the model you want | `--model <that key>` |
| there is no endpoint — the user's ChatGPT/Codex plan | `--model codex`, no key, no base, nothing to probe (SKILL.md §1c) |

`--provider` and `--model` are mutually exclusive, so on a provider route a
different model is named in `--model_list`, never in `--model`.

**Never `--model chatgptapi` for an arbitrary id**: that preset runs a
hardcoded GPT-3.5-family discovery and ignores `--model_list`. Only
`openai`, `groq`, `gemini` and `--provider` honor it; every other `--model`
value refuses the combination loudly, on the command line alone, before a
key is read or a codex sidecar is started.

## `--provider`: the route, written once (`provider_loader.py`)

`bbm_providers.json` in the working directory is read first, then
`~/.bbm/providers.json`; a project entry wins on a shared name. The
`providers` wrapper is required — the loader reads nothing from a file
without it.

```json
{"providers": {"mygw": {
  "api_style": "claude",
  "base_url": "https://api.example.com",
  "env_key": "MY_GATEWAY_KEY",
  "default_models": ["claude-haiku-4.5"]
}}}
```

`api_style` is `openai`, `claude`, `gemini` or `qwen`, and it is the only
required field. `default_models` becomes the model list when `--model_list`
is absent; `env_key` names the variable the key is read from, and without
it (or an explicit `--api_key`) the run stops asking for one. An unknown
provider name is an error that lists the names both files do define.

**Only `openai` and `claude` entries carry a session history.** `gemini`
and `qwen` entries refuse `--use_context session` outright, which makes
them the wrong route for this workflow — see the capability table below.

## Binding `$KEY`, `$ROOT`, `$MODEL` from the entry before any probe

The entry decides which key variable to read. Do **not** take "whichever
key is set" — `.env` is sourced into a shell that may already export other
providers' keys from `~/.zshenv`, and a stale one would route the run to an
endpoint the user never chose. Exit before curl when anything is missing:
an empty bearer token produces a 401 that reads like a bad key.

```bash
route_env() {   # $1 = provider name, as in bbm_providers.json / ~/.bbm/providers.json
  eval "$(python3 - "$1" <<'EOF'
import json, os, pathlib, shlex, sys
name = sys.argv[1]; entry = None
for f in (pathlib.Path.home()/".bbm"/"providers.json", pathlib.Path("bbm_providers.json")):
    if f.is_file():
        entry = json.load(open(f)).get("providers", {}).get(name, entry)
if entry is None:
    print(f'echo "no provider entry named {name}" >&2; return 1'); sys.exit()
shape = {"claude": "anthropic"}.get(entry.get("api_style"), "openai")
host = {"gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
        "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1"}.get(entry.get("api_style"))
root = (entry.get("base_url") or host or {"anthropic": "https://api.anthropic.com"}.get(shape, "https://api.openai.com")).rstrip("/")
root = root[:-3] if root.endswith("/v1") else root
key_var = entry.get("env_key") or ""
model = (entry.get("default_models") or [""])[0]
print(f"SHAPE={shape} ROOT={shlex.quote(root)} MODEL={shlex.quote(model)} KEY_VAR={key_var}")
EOF
)"
  [ -n "${SHAPE:-}" ] || return 1
  [ -n "$KEY_VAR" ] || { echo "entry $1 names no env_key" >&2; return 1; }
  KEY="$(printenv "$KEY_VAR" 2>/dev/null || true)"   # same in bash and zsh; .env is exported by set -a
  [ -n "$MODEL" ] || { echo "entry $1 names no model" >&2; return 1; }
  [ -n "$KEY" ]   || { echo "$KEY_VAR is unset — fill .env" >&2; return 1; }
}
```

`$ROOT` comes out with no trailing `/v1` whatever the entry wrote, so every
probe path below is spelled in full. On a gateway the key belongs to the
*gateway*, not the model's vendor: a Claude model reached over the OpenAI
shape reads the gateway entry's `env_key`, because that is the shape being
spoken.

## Shapes, and how to probe each

`$ROOT` is the scheme+host with no `/v1` (`route_env` guarantees it), so
every path below is written out in full. Each probe is one tiny call, a
fraction of a cent.

**No token cap on the OpenAI shape.** `max_tokens: 1` looks thrifty and is a
false negative twice over: gateways reject caps below their own floor
(measured: `max_tokens must be greater than 2` on *every* model of one
gateway), and OpenAI's own o-series/gpt-5 models reject `max_tokens`
outright in favour of `max_completion_tokens`. A probe must test one thing.
The repo's internal probe sends no cap for exactly this reason
(`chatgptapi_translator.py:_test_structured_outputs`); match it. The reply is
a few tokens of "Hi!".

**OpenAI shape** — the universal one. Most gateways serve every model they
host on it, whoever made the model.

```bash
curl -sS "$ROOT/v1/chat/completions" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"'"$MODEL"'","messages":[{"role":"user","content":"hi"}]}'
```

Passes when the body has `.choices[0]`. → the entry is `"api_style":
"openai"` with `"base_url": "$ROOT/v1"`.

**Anthropic shape**. `max_tokens` is *mandatory* here, unlike above; 16 is
past every floor seen so far.

```bash
curl -sS "$ROOT/v1/messages" \
  -H "x-api-key: $KEY" -H 'anthropic-version: 2023-06-01' \
  -H 'Content-Type: application/json' \
  -d '{"model":"'"$MODEL"'","max_tokens":16,"messages":[{"role":"user","content":"hi"}]}'
```

Passes when the body has `.content[0]`. → the entry is `"api_style":
"claude"` with `"base_url": "$ROOT"`. The reply's `model` field echoes the
id the endpoint actually resolved to (`claude-haiku-4.5` →
`claude-haiku-4-5-20251001`), which is the id to write into
`default_models`. Real Anthropic requires `x-api-key`; gateways commonly
accept `Authorization: Bearer` too, so try `x-api-key` first and Bearer
second.

**`--api_base` for this route takes the root, not `/v1`**: the SDK appends
`/v1/messages` itself. Passing `https://host/v1` used to produce
`/v1/v1/messages` and a 403 reading "HTTP node only allows access to
inference API paths"; a trailing `/v1` is now trimmed automatically (with a
printed note), so either form works — but say `https://host` and mean it.

**Gemini shape**

```bash
curl -sS "$ROOT/v1beta/models/$MODEL:generateContent" \
  -H "x-goog-api-key: $KEY" -H 'Content-Type: application/json' \
  -d '{"contents":[{"parts":[{"text":"hi"}]}]}'
```

Passes when the body has `.candidates[0]`. → a `"api_style": "gemini"`
entry, default root `https://generativelanguage.googleapis.com`. **Not a
route for this workflow**: it refuses `--use_context session`. Reach a
Gemini model through an OpenAI-shaped gateway entry instead when the
translation needs continuity.

## Choosing an `api_style` for a new entry

The entry declares the shape, so this only comes up when writing one.

| model name starts with | try first | then |
|---|---|---|
| `gpt-`, `o1`, `o3`, `chatgpt` | `openai` | — |
| `claude-` | `openai` **if** the host is a gateway; `claude` if it is anthropic's own | the other one |
| `gemini-` | `openai` at a gateway (a `gemini` entry cannot keep a session) | — |
| `llama`, `mixtral`, `gemma`, `qwen3`, `deepseek`, anything else | `openai` | — |

Why OpenAI-first at a gateway: aggregators serve Claude and Gemini models
on `/chat/completions` too, and that shape is the one with structured
outputs, a session history and an auto-sized compact budget behind it. Go
native only when the endpoint is the vendor's own, or when the gateway
rejects the OpenAI shape.

**Verify the name before the path.** `GET $ROOT/v1/models` (Bearer auth) is
free on OpenAI-shaped endpoints and returns `{"data":[{"id":…}]}`. Check
`$MODEL` is in that list *first*: a typo'd id and an unsupported path both
return 404, and only the listing tells them apart. Some gateways add
`supported_endpoint_types` per row — measured on one aggregator, every row
read `['openai', 'anthropic']`. When that field is there it answers the
shape question outright; read it instead of guessing.

## Capability caveats per route

| route | translation | session context | plan-mode classification |
|---|---|---|---|
| `openai` (any host), groq | schema when the probe says `strict`, else delimiter | yes | yes |
| `claude` | delimiter (no structured-output work was done for it) | yes | yes, via the prompt rung |
| `codex` | one turn per unit, on a thread that is itself the context window | the thread | yes, via the prompt rung — the sidecar compiles no schema |
| `gemini` | native schema | **no** — `--use_context session` is refused | yes |
| `qwen`, `qwen-mt-*`, `customapi` | translation only | **no** | **no** |
| `xai`, `orcarouter` | schema/delimiter as above | **no** — their `__init__` drops the context arguments | yes |
| google, deepl, deeplfree, caiyun, tencentransmart | translation only | **no** | **no** |

Classification capability does not gate *this* skill — `--plan-classify
agent` makes no API call, you are the classifier. It matters only if someone
switches to `--plan-classify model`.

`qwen-mt-*` and `customapi` are dedicated translation engines: their only
channel translates whatever it is handed rather than answering it. They
translate fine and cannot be asked a question.

An unset `--temperature` still sends `1.0` on the `claude` and `gemini`
routes; only the openai-shaped translator leaves it out of the request.

## What the run's own probe does later

At first paid use, the OpenAI-shaped translator sends a one-key schema probe
and grades the endpoint `strict` / `shape` / `json` / unsupported. Only
`strict` gets a schema for *translation* — the translation schema pins the
target language as a value constraint, so an endpoint that ignores values
would drop it. Everything else falls back to the delimiter method, which is
fine and prints one yellow line. This is capability discovery, not an error;
do not report it to the user as a failure.
