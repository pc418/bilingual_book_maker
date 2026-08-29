"""Keep docs/260829-refactor-PROMPTS_FOR_REVIEW.md in step with the real prompts.

That file exists to be revised: it is where prompt wording gets read and
argued about. A copy that has drifted from the code is worse than no copy,
because it would be revised against text no model ever sees.

Same idea as test_cli_documentation.py, which pins the flag list to argparse.
"""

from pathlib import Path

import pytest

from book_maker.glossary import Glossary
from book_maker.session_context import HandoffReport, handoff_prompt
from book_maker.translator.chatgptapi_translator import ChatGPTAPI
from book_maker.translator.codex_translator import BASE_INSTRUCTIONS

DOC = Path(__file__).resolve().parents[1] / "docs/260829-refactor-PROMPTS_FOR_REVIEW.md"


def _doc_text() -> str:
    if not DOC.exists():
        pytest.fail(f"{DOC.name} is missing; the prompts have no reviewable copy")
    # Paragraph wrapping in the file must not matter, only the wording.
    return " ".join(DOC.read_text(encoding="utf-8").split())


def _assert_documented(prompt: str, label: str):
    haystack = _doc_text()
    for chunk in [c for c in prompt.split("\n\n") if c.strip()]:
        needle = " ".join(chunk.split())
        assert needle in haystack, (
            f"{label}: this text is sent to the model but is not in "
            f"{DOC.name}:\n  {needle[:120]}..."
        )


def test_handoff_prompt_with_glossary_is_documented():
    _assert_documented(handoff_prompt(with_glossary=True), "handoff (glossary on)")


def test_handoff_prompt_without_glossary_is_documented():
    _assert_documented(handoff_prompt(with_glossary=False), "handoff (glossary off)")


def test_seed_text_is_documented():
    seed = HandoffReport(1, "<SUMMARY>", "<TERMS>").seed_text()
    _assert_documented(seed, "next-window seed")


def test_glossary_block_is_documented():
    block = Glossary.parse("Winston → 温斯顿 # note\n").prompt_block("Winston")
    _assert_documented(block, "glossary injection block")


def test_codex_base_instructions_are_documented():
    _assert_documented(BASE_INSTRUCTIONS, "codex thread instructions")


def test_default_translation_prompt_is_documented():
    _assert_documented(ChatGPTAPI.DEFAULT_PROMPT, "default translation prompt")


def test_the_doc_points_at_the_modules_that_hold_each_prompt():
    text = _doc_text()
    for module in (
        "book_maker/session_context.py",
        "book_maker/glossary.py",
        "book_maker/translator/codex_translator.py",
        "book_maker/translator/chatgptapi_translator.py",
    ):
        assert module in text, f"{DOC.name} does not say where {module} prompts live"
