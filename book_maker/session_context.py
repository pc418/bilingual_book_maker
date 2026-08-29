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

import json
import re
from dataclasses import dataclass
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

GLOSSARY_JSON_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "term": {"type": "string"},
            "translation": {"type": "string"},
            "note": {"type": "string"},
        },
        "required": ["term", "translation"],
        "additionalProperties": False,
    },
}


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


_SUMMARY_REQUEST = (
    "You are handing this translation over to another translator who has not "
    "seen any of the text above.\n\n"
    "1. Summary — what the book itself says so far: the narrative or argument, "
    "who the people are, the places and events, and anything a later passage "
    "might refer back to. Write about the book's content, not about the "
    "translation work: never describe which sections or front matter you "
    "processed, and do not list what you have finished.\n\n"
)

# Capped and scoped. Left open, this section grows into a second glossary
# written as prose ("'e-book' 统一译为 '电子书'"), which duplicates every
# rendering in a format nothing can parse.
_STYLE_REQUEST = (
    "2. Style — at most 6 short lines, on voice and convention only: register "
    "and formality, how names, titles and quotation marks are handled, and any "
    "recurring choice about sentence shape."
)

_STYLE_NO_GLOSSARY_SUFFIX = (
    " If a particular rendering matters for consistency, you may name it here."
)

_STYLE_WITH_GLOSSARY_SUFFIX = (
    " Do not put term translations in this section: the glossary below is the "
    "only place they go."
)

_GLOSSARY_REQUEST = (
    "\n\n3. Glossary — every name, place, title and recurring term you have "
    "already translated, each listed exactly once, as a JSON array inside a "
    "```json fenced block. Each element is "
    '{"term": <source>, "translation": <your rendering>, "note": <optional>}. '
    "This is the only place term equivalences belong."
)


def handoff_prompt(with_glossary: bool) -> str:
    """The compact turn's request.

    Without `--glossary-auto` the model is not asked for JSON at all: nothing
    downstream would consume it, and an unused JSON section is output tokens
    billed for nothing.
    """
    if with_glossary:
        return (
            _SUMMARY_REQUEST
            + _STYLE_REQUEST
            + _STYLE_WITH_GLOSSARY_SUFFIX
            + _GLOSSARY_REQUEST
        )
    return _SUMMARY_REQUEST + _STYLE_REQUEST + _STYLE_NO_GLOSSARY_SUFFIX


_FENCED_JSON = re.compile(r"```(?:json)?\s*(\[.*?\])\s*```", re.DOTALL)
_BARE_JSON = re.compile(r"(\[\s*\{.*?\}\s*\])", re.DOTALL)


def parse_handoff_glossary(text: str) -> Glossary:
    """Pull the JSON glossary out of a handoff report.

    Model output, so it is parsed leniently and never raises: a window whose
    glossary section came back malformed still has a usable prose summary, and
    losing the run over it would be the wrong trade.
    """
    if not text:
        return Glossary()
    for pattern in (_FENCED_JSON, _BARE_JSON):
        match = pattern.search(text)
        if not match:
            continue
        try:
            return Glossary.from_json(json.loads(match.group(1)))
        except (json.JSONDecodeError, ValueError):
            continue
    return Glossary()


# A markdown heading that introduced the JSON block and is left dangling once
# the block is removed ("## 3. Glossary", "### Established renderings").
_EMPTY_GLOSSARY_HEADING = re.compile(
    r"^#{1,6}\s*\d*\.?\s*(glossary|established renderings|terms)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def strip_handoff_glossary(text: str) -> str:
    """The report's prose, with the JSON glossary block removed.

    The block is parsed into real entries and re-rendered, so leaving it in
    the prose would write every term twice into `<book>_handoff.md` and send
    it twice in the next window's seed — a measured 84KB of duplication over
    nine windows on a short test run.
    """
    if not text:
        return text
    without = _FENCED_JSON.sub("", text)
    if without == text:
        without = _BARE_JSON.sub("", text)
    without = _EMPTY_GLOSSARY_HEADING.sub("", without)
    # Collapse the blank runs the removal leaves behind.
    without = re.sub(r"\n{3,}", "\n\n", without).strip()
    # A heading now left at the very end introduced the block we just removed.
    # Matched by position rather than by wording, since the model writes it in
    # the target language ("### 术语表").
    return re.sub(r"\n#{1,6}[^\n]*$", "", without).strip()


@dataclass
class HandoffReport:
    """One window's handoff, persisted to `<book>_handoff.md` and re-seeded."""

    window: int
    summary: str
    glossary_lines: str = ""

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

    def append_to(self, path) -> None:
        """Append this window's report. Inspectable and hand-editable by design."""
        path = Path(path)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
        block = f"\n{self._MARKER}{self.window} — {stamp}\n\n{self.summary}\n"
        if self.glossary_lines:
            block += f"\n### Established renderings\n\n{self.glossary_lines}\n"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(block)

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
