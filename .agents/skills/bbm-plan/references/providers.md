# Routes: model name → endpoint shape → flags

Loaded from SKILL.md §0/§1b. Everything here is verified against the code
(`book_maker/cli.py`, `book_maker/translator/`) and, where marked, against a
live gateway on 2026-08-07.

## The one rule that decides everything

There is no model-name whitelist any more. A route is three flags plus the
model id the endpoint itself uses:

```
--api_base "$ROOT"  --key "$KEY"  [--api_format ...]  --model "$MODEL"
```

`--api_format` is inferred: the `--api_base` host first (`*.anthropic.com` →
`anthropic`, else `openai`), then the model id (`claude`/`anthropic` in it →
`anthropic`). Pass it only to correct a wrong guess. Any model id reaches any
endpoint; nothing needs to be registered first.

| the endpoint speaks | flags |
|---|---|
| the OpenAI shape | `--api_base "$ROOT/v1" --model "$MODEL"` |
| the anthropic shape, on an anthropic.com host | `--api_base "$ROOT" --model "$MODEL"` |
| the anthropic shape, on a gateway domain | the same plus `--api_format anthropic` |
| an entry in `bbm_providers.json` / `~/.bbm/providers.json` | `--provider NAME`, optionally `--model "$MODEL"` |
| the OrcaRouter gateway | `--model orcarouter` (or `orcarouter/<id>`), no `--api_base` |
| nothing — a local Codex sidecar on the user's plan | `--model codex`, no key, no base (SKILL.md §1c) |

On the openai format `--model` may be left out entirely: it defaults to
`gpt-5.6-luna`. Every other format wants an id, and the anthropic format
errors without one.

`codex` is the one route with no endpoint to probe: it is not a model id and
not a host, so none of the probes below apply to it. `--model codex` and
`--api_format codex` select the same thing.

A wrong anthropic guess costs one request: the endpoint answers 404/405 and
the run switches to `openai` for good, saying so. `--api_format openai` skips
that attempt. `--api_base` may be pasted with its path (`.../v1/chat/completions`).

Gemini, Groq, xAI, Qwen and every aggregator are reached through the OpenAI
shape — their native wrappers were removed, and their OpenAI-compatible
endpoints are better served by the universal translator (it probes for
structured output, keeps context, and can run async and batch).

## `--provider NAME`: the same route, written once

An endpoint that is used repeatedly belongs in a provider file instead of on
every command line. `bbm_providers.json` in the working directory is read
first, then `~/.bbm/providers.json`; a project entry wins on a shared name.
Each entry is the route spelled out:

```json
{
  "providers": {
    "nvidia": {
      "api_style": "openai",
      "base_url": "https://integrate.api.nvidia.com/v1",
      "default_models": ["moonshotai/kimi-k2-thinking"],
      "env_key": "NVIDIA_API_KEY"
    }
  }
}
```

The `providers` wrapper is required — the loader reads nothing from a file
without it.

`api_style` is `openai`, `claude` (→ `--api_format anthropic`), `gemini` or
`qwen` (both the openai format at their compatibility bases, so `base_url`
is optional for them). `default_models` becomes `--model` when it holds one
id and `--model_list` when it holds several; `env_key` is consulted for the
key ahead of `BBM_API_KEY` and the format's conventional variables. **Anything passed
explicitly wins**, so `--provider nvidia --model <id>` keeps the user's
model. An unknown name is an error that names both files.

## `--model orcarouter`: a gateway with no address to type

`--model orcarouter` routes to OrcaRouter's OpenAI-shaped base and asks for
its smart-routing pseudo-model; `--model orcarouter/<id>` pins one model
there. Neither needs `--api_base`, and an `--api_base` you do pass wins.
The key comes from `BBM_ORCAROUTER_API_KEY` (then the usual fallbacks). It
is a shortcut, not a legacy alias — no deprecation notice is printed. Probe
it as any OpenAI-shaped endpoint, against
`https://api.orcarouter.ai/v1`.

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
key_var = entry.get("env_key") or "BBM_API_KEY"
model = (entry.get("default_models") or [""])[0] or ("gpt-5.6-luna" if shape == "openai" else "")
print(f"SHAPE={shape} ROOT={shlex.quote(root)} MODEL={shlex.quote(model)} KEY_VAR={key_var}")
EOF
)"
  [ -n "${SHAPE:-}" ] || return 1
  KEY="$(printenv "$KEY_VAR" 2>/dev/null || true)"   # same in bash and zsh; .env is exported by set -a
  [ -n "$MODEL" ] || { echo "entry $1 names no model (the anthropic format needs one)" >&2; return 1; }
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
The repo's internal probes send no cap for exactly this reason
(`translator/capabilities.py::probe_structured_output` and
`probe_model_route`); match them. Measured 260902: a capped route probe
against this fork's own default model came back "Unsupported parameter:
'max_tokens' is not supported with this model", confirming nothing. The reply is
a few tokens of "Hi!".

**OpenAI shape** — the universal one. Most gateways serve every model they
host on it, whoever made the model.

```bash
curl -sS "$ROOT/v1/chat/completions" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"'"$MODEL"'","messages":[{"role":"user","content":"hi"}]}'
```

Passes when the body has `.choices[0]`. → `--api_base "$ROOT/v1" --key "$KEY"
--model "$MODEL"`

**Anthropic shape**. `max_tokens` is *mandatory* here, unlike above; 16 is
past every floor seen so far.

```bash
curl -sS "$ROOT/v1/messages" \
  -H "x-api-key: $KEY" -H 'anthropic-version: 2023-06-01' \
  -H 'Content-Type: application/json' \
  -d '{"model":"'"$MODEL"'","max_tokens":16,"messages":[{"role":"user","content":"hi"}]}'
```

Passes when the body has `.content[0]`. → `--api_base "$ROOT" --key "$KEY"
--model "$MODEL"` (add `--api_format anthropic` when the host is not
anthropic.com). The reply's `model` field echoes the id the endpoint actually
resolved to (`claude-haiku-4.5` → `claude-haiku-4-5-20251001`). Real Anthropic requires
`x-api-key`; gateways commonly accept `Authorization: Bearer` too, so try
`x-api-key` first and Bearer second.

**`--api_base` for this route takes the root, not `/v1`**: the SDK appends
`/v1/messages` itself. Passing `https://host/v1` used to produce
`/v1/v1/messages` and a 403 reading "HTTP node only allows access to
inference API paths"; a trailing `/v1` is now trimmed automatically (with a
printed note), so either form works — but say `https://host` and mean it.

**Gemini** has no separate route: use its OpenAI-compatible base,
`https://generativelanguage.googleapis.com/v1beta/openai/`, and probe it with
the OpenAI-shape call above.

## Inferring which shape to try first, from the model name

| model name starts with | try first | then |
|---|---|---|
| `claude-` | OpenAI if a gateway base is set; else anthropic | the other one |
| anything else (`gpt-`, `o1`, `o3`, `gemini-`, `grok-`, `llama`, `qwen`, `deepseek`, …) | OpenAI | — |

Why OpenAI-first whenever the entry's `base_url` is a gateway: aggregators
serve Claude and Gemini models on `/chat/completions` too. Go native only
when the endpoint is Anthropic's own, or when the gateway rejects the OpenAI
shape.

**The chat call is the verdict; the listing is only a hint.** `GET
$ROOT/v1/models` (Bearer auth) is free on OpenAI-shaped endpoints and
returns `{"data":[{"id":…}]}`, but it is not the authority on what the
endpoint will serve: gateways routinely serve a model they do not list, or
list it under another id, so a name missing from the listing is no reason
to give up. Run the chat probe above; a reply is the answer. Fetch the
listing when the probe fails, to tell a typo'd id from an unsupported path
— both return 404, and only the listing separates them. The repo does the
same thing internally (`translator/capabilities.py::probe_model_route`,
run once at the first paid call), so what you check here is what the run
checks. Some gateways add `supported_endpoint_types` per row — measured on
one aggregator, every row read `['openai', 'anthropic']`. When that field
is there it answers the shape question outright; read it instead of
guessing.

## Capability caveats per route

| `--api_format` | translation | plan-mode classification |
|---|---|---|
| `openai` (any host) | schema when the probe says `strict`, else delimiter | yes |
| `anthropic` | delimiter (no structured-output work was done for it) | yes, via the prompt rung |
| `codex` | one turn per unit, on a thread that is itself the context window | yes, via the prompt rung — the sidecar compiles no schema |
| `google`, `deepl`, `deeplfree`, `caiyun`, `tencent`, `customapi` | translation only | **no** |

Classification capability does not gate *this* skill — `--plan-classify
agent` makes no API call, you are the classifier. It matters only if someone
switches to `--plan-classify model`.

The machine-translation engines have one channel and it translates whatever
it is handed rather than answering it. They translate fine and cannot be
asked a question.

## What the run's own probe does later

At first paid use, the OpenAI-shaped translator sends a one-key schema probe
and grades the endpoint `strict` / `shape` / `json` / unsupported. Only
`strict` gets a schema for *translation* — the translation schema pins the
target language as a value constraint, so an endpoint that ignores values
would drop it. Everything else falls back to the delimiter method, which is
fine and prints one yellow line. This is capability discovery, not an error;
do not report it to the user as a failure.
