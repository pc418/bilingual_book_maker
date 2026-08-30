"""Append-only history for `--use_context session`, and its compact cycle.

Window mode (`--use_context`) re-sends the last few (source, translation)
pairs on every request, which costs roughly six extra copies of the book for
three paragraphs of context. Session mode instead keeps one append-only
message list: the prefix is byte-stable, so an endpoint with prompt caching
re-reads it at its cache rate (0.1-0.23x on current endpoints) and context can
grow to chapter length for less money than window mode spends on three
paragraphs.

Two rules follow from that and are load-bearing everywhere below:

1. Nothing already sent may be rewritten. Editing an earlier message shifts
   the prefix and the cache misses, which is strictly worse than window mode.
2. Anything that varies per unit (the glossary block) rides in the fresh tail
   message, never in the prefix.

When the history reaches `--context-compact-at`, we ask the model for a
translator handoff report, start a new window seeded with it, and keep going.
Budgets come from the cost model in
`docs/260827-feat-CODEX_TRANSLATOR_PROVIDER.md`; they are the point where
session mode spends what window mode spent, while carrying ~5-10x the context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import NamedTuple
from datetime import datetime, timezone
from pathlib import Path

from book_maker.glossary import Glossary

# Estimated, never billed. A chars-based estimate is what the budget table was
# derived from, so the knob and the math agree by construction; reading
# `usage` back would make the trigger depend on numbers the budget never used.
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]")
_LATIN_CHARS_PER_TOKEN = 4.0
_CJK_CHARS_PER_TOKEN = 1.7

# The cost-balanced budget per model: same spend as window mode, ~5-10x the
# context. Keys are matched as substrings of the model id so vendor prefixes
# ("openai/gpt-5.6-luna") and date suffixes resolve.
_COMPACT_BUDGETS = {
    "gpt-5.6-luna": 17000,
    "deepseek-v4-flash": 7000,
    "glm-5.3": 8000,
}

# Unknown models get the 0.2x-tier balanced figure: conservative for a cheap
# cache, still well above window mode's context.
DEFAULT_COMPACT_BUDGET = 8000


def estimate_tokens(text: str) -> int:
    """Estimated tokens: CJK packs denser per character than latin script."""
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    latin = len(text) - cjk
    return round(latin / _LATIN_CHARS_PER_TOKEN + cjk / _CJK_CHARS_PER_TOKEN)


def compact_budget_for(model: str | None) -> int:
    """The model's balanced compact budget, or the conservative default."""
    if not model:
        return DEFAULT_COMPACT_BUDGET
    name = model.lower()
    for key, budget in _COMPACT_BUDGETS.items():
        if key in name:
            return budget
    return DEFAULT_COMPACT_BUDGET


class SessionHistory:
    """The append-only (source, translation) message list for one window."""

    def __init__(self):
        self._messages: list[dict] = []
        self._tokens = 0
        self.windows = 1

    def messages(self) -> list[dict]:
        """The cached prefix. Callers must not mutate what they get back."""
        return list(self._messages)

    def append(self, source: str, translation: str) -> None:
        self._messages.append({"role": "user", "content": source})
        self._messages.append({"role": "assistant", "content": translation})
        self._tokens += estimate_tokens(source) + estimate_tokens(translation)

    def estimated_tokens(self) -> int:
        return self._tokens

    def should_compact(self, budget: int) -> bool:
        return budget > 0 and self._tokens >= budget

    def reset(self, seed: str) -> None:
        """Start the next window, seeded with the handoff report."""
        self._messages = []
        self._tokens = 0
        self.windows += 1
        if seed:
            self._messages.append({"role": "user", "content": seed})
            self._tokens = estimate_tokens(seed)


# The compact request, revised by the user 2026-08-29. Kept deliberately terse:
# the model is being asked to compress its own context, not to write a report
# for a reader.
_PREAMBLE = (
    "Context is compacting. Summarize content you translated so far in your "
    "context for brief reference of later translations."
)

_SUMMARY_REQUEST = (
    "Summary - of translated content above. What happened, who was "
    "involved, when did those happen."
)

# Capped, and scoped to deviations only. Uncapped this section grew past
# twenty bullets and turned into a second glossary written as prose
# ("'e-book' 统一译为 '电子书'"), duplicating every rendering unparseably.
_STYLE_REQUEST = (
    "Style — up to 3 lines of what translation style is used so far. "
    "Only note down what's different from general translation."
)

# Without a glossary section there is nowhere for term equivalences to go, so
# the style section is not told to exclude them.
_STYLE_REQUEST_NO_GLOSSARY = (
    "Style — up to 3 lines of what translation style is used so far."
)

_GLOSSARY_REQUEST = (
    "Established renderings — every noun we need to keep unified, each "
    "listed exactly once, one per line as `term → translation # note` (the "
    "note is optional). Wrap the whole list in <renderings> and </renderings> "
    "tags so its start and end are unambiguous. This is the only place term "
    "equivalences belong."
)


def handoff_prompt(with_glossary: bool, with_style: bool = True) -> str:
    """The compact turn's request, built from the sections in play.

    Each section costs output tokens and invites the model to spend attention
    on it, so one is only asked for when something downstream consumes it: the
    glossary only behind `--glossary-auto`, and the style only when the user
    has not fixed one via `--prompt`'s `style` field. Numbering follows what
    is actually included, so a fixed style does not leave the glossary
    labelled "3." in a two-section request.
    """
    sections = [_SUMMARY_REQUEST]
    if with_style:
        sections.append(_STYLE_REQUEST if with_glossary else _STYLE_REQUEST_NO_GLOSSARY)
    if with_glossary:
        sections.append(_GLOSSARY_REQUEST)
    numbered = [f"{n}. {body}" for n, body in enumerate(sections, start=1)]
    return "\n\n".join([_PREAMBLE, *numbered])


# The block the report is asked to emit. Tolerant of a missing closing tag:
# a truncated answer should still yield the terms it managed to write.
_RENDERINGS = re.compile(
    r"<renderings>(.*?)(?:</renderings>|\Z)", re.DOTALL | re.IGNORECASE
)

# Fallback only. A glossary line is short and is not a sentence, which is what
# separates it from prose that happens to contain an arrow.
_MAX_TERM_LEN = 60
_SENTENCE_END = ("。", ".", "！", "!", "？", "?", "；", ";")


class HandoffGlossary(NamedTuple):
    """What a handoff report yielded, and how it had to be recovered.

    `source` is reported so a run can say out loud that the model skipped the
    block — with --glossary-auto on, silently learning nothing looks identical
    to a book with no recurring terms.
    """

    glossary: Glossary
    source: str  # "tagged" | "scanned" | "missing"


def _entries_from_lines(lines, strict):
    entries = []
    for raw in lines:
        line = raw.strip().lstrip("-*•").strip()
        if not line or line.startswith("#") or line.startswith("<"):
            continue
        if not strict:
            # Outside the tags, only accept things shaped like an entry.
            head = re.split(r"→|->", line)[0].strip()
            if len(head) > _MAX_TERM_LEN or line.endswith(_SENTENCE_END):
                continue
        try:
            entries.extend(Glossary.parse(line).entries)
        except ValueError:
            continue  # model output; one bad line must not lose the rest
    return entries


def parse_handoff_glossary(text: str) -> HandoffGlossary:
    """Read the renderings the handoff report established.

    Preferred shape is the tagged block the prompt asks for. Models drop it,
    so there is a fallback: scan loose `term → translation` lines, guarded so
    ordinary prose containing an arrow is not mistaken for an entry.
    """
    if not text:
        return HandoffGlossary(Glossary(), "missing")

    match = _RENDERINGS.search(text)
    if match:
        entries = _entries_from_lines(match.group(1).splitlines(), strict=True)
        if entries:
            return HandoffGlossary(Glossary(entries), "tagged")

    entries = _entries_from_lines(text.splitlines(), strict=False)
    if entries:
        return HandoffGlossary(Glossary(entries), "scanned")
    return HandoffGlossary(Glossary(), "missing")


# A markdown heading that introduced the JSON block and is left dangling once
# the block is removed ("## 3. Glossary", "### Established renderings").
_EMPTY_GLOSSARY_HEADING = re.compile(
    r"^#{1,6}\s*\d*\.?\s*(glossary|established renderings|terms)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def strip_handoff_glossary(text: str) -> str:
    """The report's prose, with the renderings block removed.

    The block is parsed into real entries and re-rendered canonically, so
    leaving it in the prose would write every term twice into
    `<book>_handoff.md` and send it twice in the next window's seed.
    """
    if not text:
        return text
    without = _RENDERINGS.sub("", text)
    without = _EMPTY_GLOSSARY_HEADING.sub("", without)
    without = re.sub(r"\n{3,}", "\n\n", without).strip()
    # A heading now left at the very end introduced the block just removed.
    # Matched by position, since the model writes it in the target language.
    return re.sub(r"\n#{1,6}[^\n]*$", "", without).strip()


@dataclass
class HandoffReport:
    """One window's handoff, persisted to `<book>_handoff.md` and re-seeded."""

    window: int
    summary: str
    glossary_lines: str = ""
    # A style the user fixed via --prompt's `style` field. It is not asked of
    # the model, so it is written in here instead — otherwise the next window
    # would inherit a report with no style at all.
    style_note: str = ""

    _MARKER = "## Window "

    def seed_text(self) -> str:
        seed = (
            "You are continuing a translation already in progress. "
            "The previous translator left this handoff report; keep names, "
            "terminology and register consistent with it.\n\n"
            f"{self.summary}"
        )
        if self.glossary_lines:
            seed += f"\n\nEstablished renderings:\n{self.glossary_lines}"
        return seed

    def render(self) -> str:
        """The report body: what is written to the file and shown on screen."""
        body = self.summary
        if self.style_note:
            body += f"\n\n### Style\n\n{self.style_note}"
        if self.glossary_lines:
            body += f"\n\n### Established renderings\n\n{self.glossary_lines}"
        return body

    def append_to(self, path) -> None:
        """Append this window's report. Inspectable and hand-editable by design."""
        path = Path(path)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(
                f"\n{self._MARKER}{self.window} — {stamp}\n\n{self.render()}\n"
            )

    @classmethod
    def latest_seed(cls, path) -> str:
        """The last window's report, for resuming a run mid-book."""
        path = Path(path)
        if not path.exists():
            return ""
        body = path.read_text(encoding="utf-8")
        _, sep, tail = body.rpartition(cls._MARKER)
        return (sep + tail).strip() if sep else body.strip()


def handoff_path(book_path) -> Path:
    """`<book>_handoff.md`, beside the book."""
    path = Path(book_path)
    return path.with_name(f"{path.stem}_handoff.md")
