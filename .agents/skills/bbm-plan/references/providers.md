# Routes: model name → endpoint shape → flags

Loaded from SKILL.md §0/§1b. Everything here is verified against the code
(`book_maker/cli.py`, `book_maker/translator/`) and, where marked, against a
live gateway on 2026-08-07.

## The one rule that decides everything

There is no model-name whitelist any more. A route is three flags plus the
model id the endpoint itself uses:

```
--api_base "$ROOT"  --key "$KEY"  [--api_format anthropic]  --model_list "$MODEL"
```

`--api_format` is inferred from the host (`*.anthropic.com` → `anthropic`,
everything else → `openai`), so pass it only when the endpoint serves the
anthropic protocol from some other domain. Any model id reaches any endpoint;
nothing needs to be registered first.

| the endpoint speaks | flags |
|---|---|
| the OpenAI shape | `--api_base "$ROOT/v1" --model_list "$MODEL"` |
| the anthropic shape, on an anthropic.com host | `--api_base "$ROOT" --model_list "$MODEL"` |
| the anthropic shape, on a gateway domain | the same plus `--api_format anthropic` |

Gemini, Groq, xAI, Qwen and every aggregator are reached through the OpenAI
shape — their native wrappers were removed, and their OpenAI-compatible
endpoints are better served by the universal translator (it probes for
structured output, keeps context, and can run async and batch).

## Binding `$KEY` and `$ROOT` before any probe

The shape decides which key variable to read. Do **not** take "whichever
key is set" — `.env` is sourced into a shell that may already export other
providers' keys from `~/.zshenv`, and a stale one would route the run to an
endpoint the user never chose. Exit before curl when either half is
missing: an empty bearer token produces a 401 that reads like a bad key.

```bash
route_env() {   # $1 = openai | anthropic
  case "$1" in
    openai)    KEY="${BBM_API_KEY:-${OPENAI_API_KEY:-}}";     DEFAULT_ROOT=https://api.openai.com ;;
    anthropic) KEY="${BBM_API_KEY:-${ANTHROPIC_API_KEY:-}}";  DEFAULT_ROOT=https://api.anthropic.com ;;
    *) echo "unknown shape $1" >&2; return 2 ;;
  esac
  ROOT="${BBM_API_BASE:-}"; ROOT="${ROOT%/}"; ROOT="${ROOT%/v1}"
  ROOT="${ROOT:-$DEFAULT_ROOT}"
  [ -n "${MODEL:-}" ] || { echo "MODEL is unset in .env" >&2; return 1; }
  [ -n "$KEY" ]       || { echo "no key set for the $1 route" >&2; return 1; }
}
```

On a gateway the key belongs to the *gateway*, not the model's vendor: a
Claude model reached over the OpenAI shape uses `OPENAI_API_KEY`, because
that is the shape being spoken. Verified in bash and zsh.

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
(`translator/capabilities.py::probe_structured_output`); match it. The reply is
a few tokens of "Hi!".

**OpenAI shape** — the universal one. Most gateways serve every model they
host on it, whoever made the model.

```bash
curl -sS "$ROOT/v1/chat/completions" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"'"$MODEL"'","messages":[{"role":"user","content":"hi"}]}'
```

Passes when the body has `.choices[0]`. → `--api_base "$ROOT/v1" --key "$KEY"
--model_list "$MODEL"`

**Anthropic shape**. `max_tokens` is *mandatory* here, unlike above; 16 is
past every floor seen so far.

```bash
curl -sS "$ROOT/v1/messages" \
  -H "x-api-key: $KEY" -H 'anthropic-version: 2023-06-01' \
  -H 'Content-Type: application/json' \
  -d '{"model":"'"$MODEL"'","max_tokens":16,"messages":[{"role":"user","content":"hi"}]}'
```

Passes when the body has `.content[0]`. → `--api_base "$ROOT" --key "$KEY"
--model_list "$MODEL"` (add `--api_format anthropic` when the host is not
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

Why OpenAI-first whenever `BBM_API_BASE` points at a gateway: aggregators
serve Claude and Gemini models on `/chat/completions` too. Go native only
when the endpoint is Anthropic's own, or when the gateway rejects the OpenAI
shape.

**Verify the name before the path.** `GET $ROOT/v1/models` (Bearer auth) is
free on OpenAI-shaped endpoints and returns `{"data":[{"id":…}]}`. Check
`$MODEL` is in that list *first*: a typo'd id and an unsupported path both
return 404, and only the listing tells them apart. Some gateways add
`supported_endpoint_types` per row — measured on one aggregator, every row
read `['openai', 'anthropic']`. When that field is there it answers the
shape question outright; read it instead of guessing.

## Capability caveats per route

| `--api_format` | translation | plan-mode classification |
|---|---|---|
| `openai` (any host) | schema when the probe says `strict`, else delimiter | yes |
| `anthropic` | delimiter (no structured-output work was done for it) | yes, via the prompt rung |
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
