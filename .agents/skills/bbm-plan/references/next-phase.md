# bbm-plan phase 2: a book brief for efficient parallel runs

Status: **unbuilt** (designed 2026-08-05).

## The idea

Sequential context (`--use_context`) is what keeps names and terminology
consistent, but it serializes the run: parallel plan-mode workers only get
chapter-local context via per-chapter translator clones. A **static book
brief**, computed once before translation and injected into *every* request,
gives all workers identical shared context — parallel-safe by construction,
no warmup, no cloning subtleties.

The brief is an **intro**: 2–4 sentences on what the book is — title, genre,
period, register. Sourced from the preface / title page / first chapter
(agent's call which; the plan JSON's samples usually already contain
enough).

It is drafted **by the coding agent** during the existing classify handoff
(step 3 of SKILL.md) — the agent is already reading every signature's
samples, so this is nearly free at that point. The user reviews and edits it
the same way they review plan actions.

## Design

- **Sidecar:** `<book>_brief.json` next to the plan:
  `{"language": …, "intro": "…"}`.
- **Injection, v2.0 — zero repo changes:** the skill renders the brief into
  a `prompt.json` system message and passes `--prompt prompt.json`.
  Verified: a custom system message flows into both the single and the
  batch structured path (`chatgptapi_translator.py` builds `sys_content`
  from `prompt_sys_msg` in both). Cost: a few dozen tokens per request —
  noise next to the text being translated.
- **Injection, v2.1 — first-class flag:** `--book-brief <path>` in the bbm
  repo, schema-validated, composed *alongside* a user's own `--prompt`
  instead of occupying it. Upgrade, not prerequisite.
- **Parallelism:** with the brief in place the skill's full run can default
  to `--parallel-workers N` for large books, keeping `--use_context` off.

## Honest limits

- A prompt-injected brief is **advisory** — the model can still deviate
  from it. Enforcement would be a post-check and belongs to a later
  iteration if drift is actually observed. Do not build it speculatively.
- An intro drawn from front matter can be wrong about a book whose preface
  describes a different edition; the user review step is load-bearing.

## Implementation steps (when picked up)

1. Skill-side: brief drafting instructions in the classify step; brief →
   `prompt.json` rendering; `--parallel-workers` default for large books.
2. A/B smoke: same 2 chapters with and without the brief, eyeball name
   consistency across a chapter boundary.
3. Only if drift observed: post-check script. Only if `--prompt` collision
   bites a real user: the `--book-brief` repo flag (+ tests, fail-closed
   validation like the plan sidecar).
