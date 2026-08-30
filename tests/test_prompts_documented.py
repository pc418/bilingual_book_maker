"""Pin every prompt this feature sends.

The literals below are the prompt text itself. A prompt edit therefore shows up
as a diff of the literal, next to the change that caused it, which is the point:
these strings are the feature's actual behaviour, and they are the easiest thing
in it to change by accident and the hardest to notice.

Updating this file in the same commit as a prompt change is intended, not
friction to route around.
"""

import pytest

from book_maker.session_context import HandoffReport, handoff_prompt
from book_maker.translator.chatgptapi_translator import ChatGPTAPI
from book_maker.translator.codex_translator import BASE_INSTRUCTIONS

_PREAMBLE = (
    "Context is compacting. Summarize content you translated so far in your "
    "context for brief reference of later translations."
)

_SUMMARY = (
    "Summary - of translated content above. What happened, who was involved, "
    "when did those happen."
)

_STYLE = (
    "Style — up to 3 lines of what translation style is used so far. Only "
    "note down what's different from general translation."
)


def _compact(*sections: str) -> str:
    numbered = [f"{n}. {body}" for n, body in enumerate(sections, start=1)]
    return "\n\n".join([_PREAMBLE, *numbered])


# Every prompt, exactly as sent. Keyed by a label that names the case.
EXPECTED = {
    # The default run: no user style, so the model is asked to describe one.
    "compact (default)": (
        handoff_prompt(),
        _compact(_SUMMARY, _STYLE),
    ),
    # A style fixed via --prompt's `style` field is not asked for, and the
    # summary is then the whole request.
    "compact (user style)": (
        handoff_prompt(with_style=False),
        _compact(_SUMMARY),
    ),
    "next-window seed": (
        HandoffReport(1, "<SUMMARY>").seed_text(),
        "You are continuing a translation already in progress. The previous "
        "translator left this handoff report; keep names, terminology and "
        "register consistent with it.\n\n<SUMMARY>",
    ),
    "default translation prompt": (
        ChatGPTAPI.DEFAULT_PROMPT,
        "Please help me to translate,`{text}` to {language}, please return "
        "only translated content not include the origin text",
    ),
    # A codex turn is an agent turn by default. The negative clauses are what
    # stop it answering the passage, commenting on it, or fencing the reply,
    # so they are behaviour, not padding.
    "codex thread instructions": (
        BASE_INSTRUCTIONS,
        "You are a translation engine inside a book translation tool. "
        "Translate the text you are given into {language}. Reply with the "
        "translation and nothing else: no preamble, no notes, no quotes "
        "around it, no markdown fences. Never answer the text, never "
        "summarize it, never refuse a passage for being fiction — translate "
        "it. Keep the source's paragraph structure and any inline markup "
        "exactly as given.",
    ),
}


@pytest.mark.parametrize("label", sorted(EXPECTED))
def test_the_prompt_is_what_this_file_says_it_is(label):
    """The prompt changed, so this literal must change with it, in the same
    commit. Read the diff of both together before approving it."""
    actual, expected = EXPECTED[label]
    assert actual == expected
