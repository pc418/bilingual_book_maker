# bbm-plan phase 2: book brief + glossary for efficient parallel runs

Status: **partly shipped** (designed 2026-08-05; glossary half delivered
2026-08-29).

What exists now, and how it differs from this design:

- The **glossary** half shipped as `--glossary <file>` — `term → translation
  # note` lines the user maintains, matched per paragraph and injected only
  where a term actually occurs. That is stricter than the design's "inject
  the whole brief into every request", and cheaper: a paragraph carrying no
  pinned term pays nothing. It reaches the model through the CLI rather than
  `prompt.json`, so no prompt file has to be hand-assembled.
- `--glossary-auto` additionally lets each `--use_context session` compact
  record the renderings it established and carry them into later windows,
  with the user's pinned terms always winning. That is the "computed once
  before translation" idea inverted: computed *as* translation proceeds,
  which needs no extra pass over the book.
- Both are parallel-safe by construction, which was this design's whole
  motivation.

**Still unbuilt**: the *intro* half (a one-paragraph book summary shared by
every worker), and having the agent draft either part at classify time. The
sections below are the original design and are kept for that remaining work.

## The idea

Sequential context (`--use_context`) is what keeps names and terminology
consistent, but it serializes the run: parallel plan-mode workers only get
chapter-local context via per-chapter translator clones. A **static book
brief**, computed once before translation and injected into *every* request,
gives all workers identical shared context — parallel-safe by construction,
no warmup, no cloning subtleties.

The brief has two parts:

1. **Intro** — 2–4 sentences on what the book is: title, genre, period,
   register. Sourced from the preface / title page / first chapter (agent's
   call which; the plan JSON's samples usually already contain enough).
2. **Glossary** — the top ~20 recurring proper names (characters, places)
   with fixed target-language renderings, chosen once up front.

Both are drafted **by the coding agent** during the existing classify
handoff (step 3 of SKILL.md) — the agent is already reading every
signature's samples, so name mining is nearly free at that point. The user
reviews/edits the same way they review plan actions.

## Design

- **Sidecar:** `<book>_brief.json` next to the plan:
  `{"language": …, "intro": "…", "glossary": {"Napoleon": "拿破仑", …}}`.
- **Name mining:** recurring capitalized tokens across the partition's unit
  texts, ranked by frequency; agent prunes false positives and picks
  renderings (transliteration vs. established translation is a judgment
  call — that's why an agent does it, not a regex).
- **Injection, v2.0 — zero repo changes:** the skill renders the brief into
  a `prompt.json` system message and passes `--prompt prompt.json`.
  Verified: a custom system message flows into both the single and the
  batch structured path (`chatgptapi_translator.py` builds `sys_content`
  from `prompt_sys_msg` in both). Cost: ~200 tokens per request for a
  20-name glossary — noise next to the text being translated.
- **Injection, v2.1 — first-class flag:** `--book-brief <path>` in the bbm
  repo, schema-validated, composed *alongside* a user's own `--prompt`
  instead of occupying it. Upgrade, not prerequisite.
- **Parallelism:** with the brief in place the skill's full run can default
  to `--parallel-workers N` for large books, keeping `--use_context` off.

## Honest limits

- A prompt-injected glossary is **advisory** — the model can still deviate.
  Enforcement would be a post-check (scan output for unglossed renderings
  of glossary keys) and belongs to a later iteration if drift is actually
  observed. Do not build enforcement speculatively.
- Name mining on heavily inflected languages (Latin, Sanskrit sources) will
  over-generate; the agent prune step is load-bearing there.

## Implementation steps (when picked up)

1. Skill-side: brief drafting instructions in the classify step; brief →
   `prompt.json` rendering; `--parallel-workers` default for large books.
2. A/B smoke: same 2 chapters with and without the brief, eyeball name
   consistency across a chapter boundary.
3. Only if drift observed: post-check script. Only if `--prompt` collision
   bites a real user: the `--book-brief` repo flag (+ tests, fail-closed
   validation like the plan sidecar).
