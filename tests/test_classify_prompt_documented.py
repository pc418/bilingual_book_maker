"""Keep the classify-prompt review doc in step with the prompt it quotes.

Same reasoning as test_prompts_documented.py: that file exists to be argued
over, and a copy that has drifted is worse than none, because it would be
revised against text no model ever sees.
"""

from pathlib import Path

import pytest

from book_maker.loader.classify.model import PAGE_SIZE, VERDICTS, build_prompt

DOC = (
    Path(__file__).resolve().parents[1]
    / "docs/260829-eval-CLASSIFY_PROMPT_FOR_REVIEW.md"
)

CANDIDATE = {
    "key": "block:p.calibre_13",
    "units": 286,
    "chars": 188081,
    "pct": 92.9,
    "mean_chars": 657.6,
    "samples": ["Any fairminded person…"],
}


def _doc_text() -> str:
    if not DOC.exists():
        pytest.fail(
            f"{DOC.name} is missing; the classify prompt has no reviewable copy"
        )
    return " ".join(DOC.read_text(encoding="utf-8").split())


def test_every_instruction_line_is_documented():
    haystack = _doc_text()
    prompt = build_prompt([CANDIDATE])
    # The instruction block is everything before the numbered candidates.
    for line in prompt.split("\n"):
        line = line.strip()
        if not line or line.startswith("1."):
            break
        assert " ".join(line.split()) in haystack, (
            f"this instruction is sent to the model but is not in "
            f"{DOC.name}:\n  {line[:120]}..."
        )


def test_the_paging_size_is_stated_correctly():
    assert f"PAGE_SIZE = {PAGE_SIZE}" in _doc_text()


def test_the_verdicts_are_listed():
    haystack = _doc_text()
    for verdict in VERDICTS:
        assert f"`{verdict}`" in haystack


def test_the_doc_says_where_the_prompt_lives():
    assert "book_maker/loader/classify/model.py" in _doc_text()
