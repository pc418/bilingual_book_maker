---
name: bbm-plan
description: Translate a whole EPUB into a bilingual book with bilingual_book_maker's plan mode - greedy partition, agent-reviewed classification plan, cheap smoke test, then a full resumable run. Use when the user wants a book translated well (running heads, page numbers, and apparatus skipped deliberately) rather than a quick --translate-tags pass, or asks for "plan mode" / "bbm" translation.
---

# bbm-plan: plan-mode EPUB translation

**Hard constraint: this skill uses `--plan-classify agent`, and only that.**
The plan arrives with its uncertain signatures set to `"action": null` and
the translate run refuses to start while any null remains, so *you*, the
coding agent, own the classification step against the real samples. Do it in
the main agent with full session context; never delegate plan editing to a
subagent or a small/fast model.

Repo: the repository this skill ships in (`make_book.py` at its root). Run
every command from the repo root. Plan mode is **epub-only**.

You are the trained CLI operator. You pick every flag from the **flag menu**
below and state each choice with a one-line reason; the menu's defaults are
the recommendation, and you follow them unless the user asks for something
else or the book argues otherwise. The user hands over a book, credentials
and (if they have one) a prompt file, approves the plan and the cost, and
gets a bilingual epub back — they are never asked to experiment with flags,
halt semantics or resume mechanics.

Three runs of one command with small flag changes: **plan → smoke → full**.
All state lives on disk (`.env`, `<book>_plan.json`, the resume cache,
`run.log`), so any step can be redone after a crash or in a new session.

## 0. Credentials

Copy `assets/env.example` (this skill dir) to `.env` at the repo root and
have the user fill it. Two fields matter: `MODEL`, the exact model id, and
one API key for wherever that model lives. `MODEL` and `BBM_API_BASE` are
skill-level fields — make_book.py does not read them from env, you translate
them into flags.

Source `.env` in the same Bash call as the run:
`set -a; source .env; set +a; …`. Never echo values; check presence with
`[ -n "$MODEL" ]`. If `.env` is unfilled, stop and ask — never accept a
pasted key as a command-line argument, it leaks into shell history and
prompt logs.

**Prefer the environment over a key flag, and never guess a key flag's
name.** A wrong flag name is not a harmless error: argparse answers
`unrecognized arguments:` by printing the whole command line back, so a
mistyped key flag prints the key itself into the terminal, the log and the
transcript. Read the flag name out of `book_maker/cli.py` before the first
run, or pass no key flag at all and let the run read the environment
variable. If a key ever does reach the terminal, say so at once and tell the
user to rotate it.

## 1. Intake — what to ask for

1. **Book path** and **target language** (`--language`, e.g. `zh-hans`,
   `ja`, `Simplified Chinese`).
2. **Their prompt file, if they have one.** Ask outright: *"Do you have a
   prompt or style file you want this translation to use?"* A user's own
   prompt is how register, honorifics and terminology get fixed for the
   whole book, and it costs nothing to ask. If they hand one over, lint it
   before the first paid run — contract and commands in
   **`references/prompt-files.md`**. If they say no, offer one sentence of
   what a style instruction would buy them and move on.
   Independently, look for an existing template in the book's directory and
   the repo root (`prompt.json`, `prompt.txt`, `prompt*.md`,
   `prompt_template*`). **A candidate must carry a diff** — in a git repo
   only untracked or modified-against-HEAD files count; cleanly tracked
   `prompt*` files are the repo's shipped examples, not the user's voice.
   Found one? **Ask before doing anything with it** — never adopt or ignore
   it silently.
3. Anything they want to change from the flag menu's defaults: bilingual vs
   replaced text, a visual style for the translation, specific chapters
   only.

Base command, with the route flags settled by step 1b (OpenAI shape shown —
the common case):

```bash
set -a; source .env; set +a
API_BASE_FLAG=()
[ -n "$BBM_API_BASE" ] && API_BASE_FLAG=(--api_base "$BBM_API_BASE")
ROUTE=(--model openai --model_list "$MODEL" --openai_key "$KEY")   # ← from step 1b
python make_book.py --book_name "$BOOK" "${ROUTE[@]}" \
  --language "$LANG" --plan-classify agent "${API_BASE_FLAG[@]}" \
  --use_context session
```

(The conditional flag is an array on purpose: `${VAR:+--flag "$VAR"}`
mis-tokenizes under zsh — macOS's default shell — into a single argv word
that argparse rejects. The array form works in bash and zsh alike.)

## 1b. Endpoint probe — infer the route, then verify it (sub-cent)

Never assume a route from the key alone. Infer it from the **model name**,
then prove it, so a typo or a wrong shape surfaces here and not after the
classify work. Three ordered questions, each answered by a call:

**0. Bind `$KEY` and `$ROOT` for the shape you are about to probe**, and
refuse to curl without them. **The shape names the key variable** — never
scan for whichever key happens to be set, because a stale export in
`~/.zshenv` would silently route the run somewhere the user never asked
for. `route_env` below is copied verbatim from `references/providers.md`,
which also carries the per-shape defaults:

```bash
set -a; source .env; set +a
route_env openai        # or: anthropic | gemini — sets KEY, ROOT, or exits
```

**1. Does this model id exist here?** On any OpenAI-shaped base the model
listing is free:

```bash
curl -sS "$ROOT/v1/models" -H "Authorization: Bearer $KEY" |
  python3 -c 'import json,sys; d=json.load(sys.stdin)["data"]; print([m["id"] for m in d])'
```

If `$MODEL` is absent, stop and show the near matches — a typo'd id and an
unsupported path both return 404 later, and only this tells them apart. A
non-OpenAI-shaped endpoint has no such listing: skip to question 2 and let
the probe judge the id.

**2. Which shape does it speak?** Use the reference's three recipes
**verbatim**, token-cap rule included. `gpt-*` and unknown ids → OpenAI;
`claude-*`/`gemini-*` → OpenAI first when `BBM_API_BASE` names a gateway,
native when it is the vendor's own endpoint.

**3. Which flags does that make?** Record them once as the `ROUTE` array
every later step uses, and state the choice in one line with its reason.

Probe the format, don't ask the user to fix it: on 404 retry with `/v1`
added or removed; on an auth rejection try that shape's native scheme.
Whatever passes is what `--api_base` gets. Stop and ask only when the key
itself is rejected by its own provider.

## 1c. The codex route — a subscription, not an endpoint

`--model codex` is neither a model id nor a host: the run drives a local
`codex app-server` sidecar and spends the user's ChatGPT/Codex plan
allowance instead of API credits. **Step 1b does not apply** — there is
nothing to curl, and no key to resolve.

```bash
python make_book.py --book_name "$BOOK" --model codex --language "$LANG" \
  --plan-classify agent --use_context session
```

- **The model is not selectable here.** `--model_list` is rejected outright
  with `--model codex`, so the sidecar's own default is what runs
  (`gpt-5.6-luna`). Do not offer the user a choice of model on this route.
- **No key flag, no `--api_base`** (an `--api_base` is swallowed). Run
  `codex login` once beforehand; the
  run checks the sidecar is up and signed in before parsing the book and
  reports how much of the 5-hour window is left.
- **`--parallel-workers` buys nothing here** — turns serialize on one
  thread so chapters cannot interleave into it.
- **A spent 5-hour window is waited out**, not failed. A weekly limit is
  reported instead, because its reset is too far off to sit through; rerun
  later, the run is resumable either way.
- **The user's `~/.codex/hooks.json` fires on every turn**, so book text
  passes through whatever those hooks do. Say so before the first run on a
  machine that has them.
- Tell the user which allowance this spends: plan quota, not the API key in
  `.env`.

Plan, classify, smoke and full run are otherwise identical.

## 2. Plan (free — agent mode makes no API call)

Run the base command once. It partitions the whole book, writes
`<book>_plan.json`, prints a handoff block, and exits without translating.

What the report gives you, and what each part is for:

- **A signature per row** (`p.calibre_13`, `span.page-no` …) with unit
  count, char total, share of the book, and up to 5 real samples — this is
  the evidence you classify from in step 3.
- **Coverage**, checked against `--plan-min-coverage`. Every text node is
  either a translation unit or a skip with a stated structural reason, and
  the run proves the accounting adds up, so a low number means the book
  really is mostly apparatus — not that something was quietly dropped.
- **Poetry windows.** Stanza-shaped runs are batched `--poetry-group-size`
  lines per request so verse is translated with its neighbours.

Symptom → knob, when reading the report:

| symptom in report | knob |
|---|---|
| verse split awkwardly across requests | raise `--poetry-group-size` |
| legit low coverage (dictionary, critical edition, apparatus-heavy) | lower `--plan-min-coverage` deliberately, and say so in the plan summary |
| visible text under a `hidden` skip reason, or vice versa | inspect the epub's CSS before overriding |

## 3. Classify (you are the classifier)

Read `<book>_plan.json`. Rows with `"action": null` are the plan's open
questions — every one must become `"translate"` or `"skip"`, and the
translate run refuses to start while any null remains. For each: **name what
the text is first** (prose, verse, dialogue, heading, caption, running head,
page/line number, sigla, cross-reference label, boilerplate, decorative
marker), *then* rule — naming before ruling prevents rationalizing a snap
verdict. Judge from `samples`, `units`, `chars`, `pct`, `mean_chars`; when
the samples do not settle it, choose `"translate"` — over-translating is
cheap, losing content is not. Want more evidence? `unzip -p <book> <file>`
and read the markup around the signature.

**An inline skip cuts its text out of the surrounding block.** Skipping a
signature that wraps part of a word — a drop-cap initial, a small-caps
fragment, a decorative first letter — does not leave the word alone: the
block is assembled without that text, so `CHAPTER I` is sent to the model as
`HAPTER` and `MR. JONES` loses its `M`. Skip an inline signature only when
its text is *whole* (a URL, a page number, a standalone marker). After
editing the plan, rerun the plan step and read the affected block rows'
samples again — a decapitated sample is the tell, and it is visible before
anything is paid for.

Non-null rows (prose spine, headings, poetry) may also be changed if their
samples convince you, but the nulls are the required work. Hold a non-null
override to the same name-then-rule discipline, and **record every one in
the step-6 report** — the user should see where you disagreed with the
plan's defaults, not discover it in the output. Edit **only** the `action`,
`decided_by` and `content_type` fields. Validation is fail-closed: a typo'd
action, missing hash or edited book is a hard error on the next run, never a
silent default.

## 4. Smoke test (pennies)

Base command + `--quiet --test --test_num 8 > smoke.log 2>&1`. You check
results **after** the run, from files — never from live output.

Units are consumed in **spine order**, so before running, check which
documents the first 8 units come from. A large nav or title page can absorb
the whole budget (a 458 KB nav once ate all 20 units of a poetry smoke, so
the smoke translated zero verse); when that happens, point the smoke at a
body chapter with `--only_filelist <content doc>` rather than raising
`--test_num`.

**Verify from the epub itself, always** — a zero exit code and a clean log
do not pass the smoke. Unzip the partial `<book>_bilingual.epub` and read
the markup around a translated unit:

- is it actually in the target language?
- does the translation sit **next to** its original, carrying the same tag
  and class (unless `--single_translate`)?
- are `id` attributes and internal fragment links intact?
- did the plan's `skip` decisions actually hold?

Then check `smoke.log` for error lines. The cache carries into the full run,
so nothing paid here is re-paid.

Not a failure at this step: the endpoint being graded below `strict` and the
run announcing the delimiter method. That is the expected line on claude, on
most proxies, and on anything not natively OpenAI.

## 5. Full run

Base command + `--quiet --resume`, minus `--test`. Always in the background
with output to a log:

```bash
… --quiet --resume > run.log 2>&1
```

(Bash `run_in_background: true`; poll with `tail -5 run.log`.) On any crash,
rerun the identical command. If the run stops with a fatal translation error,
fix the cause (key quota, endpoint down) and rerun; do not delete the cache
unless the book or plan changed intentionally.

## Flag menu — every choice, with the recommended default

**Defaults below are the recommendation.** Pick them unless the user asks
for something else or the book argues otherwise; the alternatives are listed
so you can honour a request without guessing at legal values.

### Route

**`--model` is a whitelist here.** It takes a registered name — `openai`,
`claude`, `gemini`, `groq`, `qwen`, `google`, `deepl`, `caiyun`, `codex`, … —
not an arbitrary model id. The id the endpoint actually uses travels in
`--model_list`. Passing an unknown id to `--model` is rejected by argparse
before anything runs.

| flag | values | default / recommended | choose otherwise when |
|---|---|---|---|
| `--model` | a registered name; `openai` for any OpenAI-shaped endpoint, `codex` for the subscription route | **`openai`** | the endpoint is one of the fixed engines (`google`, `deepl`, …), or the user wants their ChatGPT plan spent (`codex`, §1c) |
| `--model_list` | comma-separated model ids, rotated across | `$MODEL` from `.env` | the user wants several ids rotated to spread rate limits. **Only `--model openai`, `groq`, `gemini` and `--provider` accept it** — with any other `--model`, `codex` included, the run exits 1 |
| `--openai_key` | one key, or several comma-separated to rotate | *pass nothing* — `$OPENAI_API_KEY` / `$BBM_OPENAI_API_KEY` are read automatically | another engine: `--claude_key`, `--gemini_key`, `--groq_key`, `--qwen_key`, `--xai_key`. None at all on `codex`, which takes no key. (`--api_key` is **`--provider`-only** and is silently ignored with `--model` — do not reach for it as a generic key flag) |
| `--api_base` | endpoint URL | *unset* (the vendor's own host), or `$BBM_API_BASE` | a gateway, proxy or local server — OpenAI-shaped bases want the `…/v1` form |
| `--provider` | a name in `bbm_providers.json` / `~/.bbm/providers.json` | *unset* | a non-OpenAI-shaped gateway that needs its own entry (see `references/providers.md`) |
| `--proxy` | `http://127.0.0.1:7890`-style | *unset* | the user is behind one |

### Plan mode

| flag | values | default / recommended | choose otherwise when |
|---|---|---|---|
| `--plan-classify` | `none`, `all`, `model`, `agent` | **`agent`** — this skill's hard constraint | never, inside this skill |
| `--plan-min-coverage` | 0.0–1.0 | **0.5** | a dictionary, critical edition or apparatus-heavy book legitimately translates less; lower it deliberately and say so |
| `--poetry-group-size` | integer lines per request | **8** | verse is split awkwardly (raise it), or stanzas are long enough that a window is unwieldy (lower it) |
| `--exclude-translate-tags` | comma-separated tags | **`sup,code`** | the book puts real prose in one of those, or another tag is pure apparatus |

### Context and consistency

| flag | values | default / recommended | choose otherwise when |
|---|---|---|---|
| `--use_context` | bare/`window`, `session` | **`session`** | the run warns that the endpoint never reported a cached prompt token — session mode costs more there, so drop to bare `--use_context`. Also use bare window mode if a run must go parallel |
| `--context-compact-at` | an estimated-token budget | **unset → 8000** | the user asks for the cheapest setting (`2500`, compacts more often) or a longer window (raise it). **Needs `--use_context session`** on an API route — without it the flag is accepted and does nothing, with no warning. On `codex` it applies unconditionally |
| `--no-context-compact` | on/off | **off** | the user explicitly wants no handoff report — the window still rolls over, but the next one starts empty, losing exactly the continuity this workflow is for. Same `--use_context session` precondition |
| `--context_paragraph_limit` | integer | *unset* | window mode only; the user wants more or fewer re-sent pairs |
| `--prompt` | path to `.json` / `.txt` / `.md`, or a template string | *unset* unless the user has one (§1) | the user hands over their own voice/register — the usual reason to set it |
| `--temperature` | float | *unset* (the API default is not sent) | output is erratic; lower it and re-smoke. Ignored on `codex`, and permanently dropped for a model that rejects it once |

### Output form

| flag | values | default / recommended | choose otherwise when |
|---|---|---|---|
| *(bilingual)* | — | **bilingual: translation added beside the original** | — |
| `--single_translate` | on/off | **off** | the user wants a translated-only book, original replaced. `--translation_style` is **not** applied on this path |
| `--translation_style` | CSS declarations | *unset* | the translation should be visually separated, e.g. `"color:#808080;font-style:italic"` |
| `--translation_color` | a colour | *unset* | the user wants only a colour and no other CSS. Passing both loses this one silently — `--translation_style` wins |

### Scope and run control

| flag | values | default / recommended | choose otherwise when |
|---|---|---|---|
| `--only_filelist` / `--exclude_filelist` | comma-separated internal filenames | *unset* (whole book) | the user wants specific chapters, or the smoke must skip front matter. Exact names — a typo fails loud at the coverage gate. **An only-list wins outright**: pass both and the exclude-list is unreachable |
| `--test` / `--test_num` | flag + integer | **`--test --test_num 8`** at the smoke step only | poetry-heavy books: ~20, once you have confirmed the first N units are body text |
| `--quiet` | on/off | **on for every paid run** | never off for a run that translates — bars and per-unit echoes flood the log and your context; warnings and errors still print |
| `--resume` | on/off | **on for the full run** | never off after a crash — replay is positional and fingerprint-guarded |
| `--parallel-workers` | integer | **1 (sequential)** | a long book where wall-clock has to beat consistency. Then say so to the user and drop `--use_context session` for that run: workers hold separate histories, so the pairing is untested and self-defeating. **Never on `codex`** — turns serialize on one lock so it buys nothing, and with `--use_context` it crashes the translate run outright |
| `--extra_body` | JSON string | *unset* | the endpoint needs a vendor-specific parameter |

### Never pass in plan mode

`--translate-tags` (the plan overrides it), `--plan-dry-run` (it writes a
plan with every action already decided, which suppresses the null questions
and the agent handoff), `--accumulated_num` and `--allow_navigable_strings`
(both explicitly ignored; a non-1 `accumulated_num` also disables the
interrupt-save path), `--batch` / `--batch-use` (untested with plan mode),
`--block_size` / `--sentence_mode` / `--batch_size` (they re-cut text the
plan has already partitioned), `--interval` (Gemini only, and only when it
rate-limits).

## Halt / resume — safe by construction

- **Progress saves after every chapter** and on interrupt or crash. To halt
  a background run: `kill -INT <pid>`; even SIGKILL loses at most the
  current chapter.
- **SIGINT does not halt a `--parallel-workers` run promptly** — every
  chapter is dispatched up front, so the process exits only after all of
  them finish (measured 260807: a signal at 20/70 chapters done still
  exited having translated 70/70). Checkpoints stay correct; it is the
  *stopping* that does not work. Tell the user before a big parallel run,
  and use SIGKILL when a run must actually stop now.
- **Resume = rerun the identical command with `--resume`.** Same book, same
  plan, continues where it stopped. This is also why the smoke test is never
  wasted money.
- **Do not edit the plan or swap the book between halt and resume** — the
  fingerprint refusal protects against translations landing on the wrong
  paragraphs. Changed your mind mid-book? Finish the run, or delete
  `.<book>.temp.bin` and restart cleanly. Never work around the refusal
  without telling the user what gets re-paid.

## 6. Deliver

Report the end-of-run coverage/skip stats, every classification decision you
made (resolved nulls and any non-null overrides, with the name-then-rule
reasoning), and hand over `<book>_bilingual.epub`. Suggest spot-checking one
early and one late chapter.

## Context hygiene

- **Never** let a translation run stream into the conversation — every paid
  run gets `--quiet` *and* a log-file redirect.
- Everything the next step needs is on disk; nothing critical lives only in
  conversation.
- **Compaction threshold** — *your* context, not the run's: for a small book
  do not compact at all. Only compact when context is genuinely pressured
  (≳70% used), at most once, at the natural boundary — after plan editing,
  before the full run.

## Failure modes (all fail loud by design)

| symptom | meaning |
|---|---|
| `doesn't apply JSON schema … using delimiter method`, `honors JSON schema shape but not value constraints`, `no strict structured-output support` | **not a failure.** The endpoint does not do strict schema decoding, so translation uses the delimiter method. Expected on claude and most proxies; note it, do not switch models over it |
| `refused the … request shape; using a simpler one` | classification's ladder descended a rung. Informational |
| fingerprint refusal on `--resume` | book file or plan changed since the cache was written; delete the cache only if that was intentional |
| `undecided signature(s)` on plan load | null actions remain — answer every open question, then rerun |
| `invalid action` on plan load | typo in a hand-edited `action` — fix the JSON, rerun |
| coverage-gate error / empty plan | `--only_filelist` misspelled, or the plan skips nearly everything — re-check the plan |
| legacy-cache refusal | the cache came from an old tag-mode run — delete it |
| codex: `… codex login, then run this again` | the sidecar is up but not signed in. One `codex login`, then rerun; nothing was paid |
| codex: waiting *N* min for the window to reset | the 5-hour plan window is spent — the run sleeps and continues by itself |
| `handoff report failed (…); starting the next window` | one compact produced no report. Informational; translation continues |

## Reference files

- **`references/providers.md`** — route table, probe recipes, per-format
  capability caveats. Read before the first command whenever the endpoint is
  not a plain OpenAI-shaped host.
- **`references/prompt-files.md`** — the `--prompt` file contract, linting,
  and keeping a user's prompt out of git.
- **`references/next-phase.md`** — an unbuilt design note (a per-book brief
  for parallel runs). Not part of this workflow.
