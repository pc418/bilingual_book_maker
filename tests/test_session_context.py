"""Append-only session history, compact budgets, and the handoff report.

The invariant these tests defend: in session mode the request prefix must stay
byte-identical between requests, or the endpoint's prompt cache misses and the
whole history is re-billed at 1x — which costs more than the window mode this
feature replaces.
"""

import json

import pytest

from book_maker.session_context import (
    HandoffGlossary,
    strip_handoff_glossary,
    DEFAULT_COMPACT_BUDGET,
    HandoffReport,
    SessionHistory,
    compact_budget_for,
    estimate_tokens,
    handoff_prompt,
    parse_handoff_glossary,
)


class TestEstimateTokens:
    def test_latin_is_about_chars_over_four(self):
        assert estimate_tokens("a" * 400) == pytest.approx(100, rel=0.05)

    def test_cjk_is_denser_than_latin(self):
        assert estimate_tokens("温" * 100) > estimate_tokens("a" * 100)

    def test_cjk_is_about_chars_over_one_point_seven(self):
        assert estimate_tokens("温" * 170) == pytest.approx(100, rel=0.05)

    def test_empty_is_zero(self):
        assert estimate_tokens("") == 0

    def test_mixed_script_counts_both(self):
        mixed = estimate_tokens("a" * 400 + "温" * 170)
        assert mixed == pytest.approx(200, rel=0.05)


class TestCompactBudget:
    def test_known_models_use_their_balanced_point(self):
        assert compact_budget_for("gpt-5.6-luna") == 17000
        assert compact_budget_for("deepseek-v4-flash-0731") == 7000
        assert compact_budget_for("glm-5.3") == 8000

    def test_unknown_model_falls_back(self):
        assert compact_budget_for("some-new-model") == DEFAULT_COMPACT_BUDGET

    def test_none_model_falls_back(self):
        assert compact_budget_for(None) == DEFAULT_COMPACT_BUDGET

    def test_lookup_is_case_insensitive_and_prefix_tolerant(self):
        assert compact_budget_for("openai/GPT-5.6-Luna") == 17000

    def test_vendor_prefixed_id_resolves(self):
        assert compact_budget_for("deepseek/deepseek-v4-flash-0731") == 7000


class TestSessionHistory:
    def test_starts_empty(self):
        assert SessionHistory().messages() == []

    def test_append_adds_a_user_assistant_pair(self):
        h = SessionHistory()
        h.append("source", "translated")
        assert [m["role"] for m in h.messages()] == ["user", "assistant"]
        assert h.messages()[0]["content"] == "source"
        assert h.messages()[1]["content"] == "translated"

    def test_history_is_append_only_and_prefix_stable(self):
        """The whole feature rests on this: earlier messages never change."""
        h = SessionHistory()
        h.append("one", "1")
        before = json.dumps(h.messages())
        h.append("two", "2")
        after = json.dumps(h.messages())
        assert after.startswith(before[:-1])

    def test_estimated_tokens_grows_with_content(self):
        h = SessionHistory()
        empty = h.estimated_tokens()
        h.append("a" * 400, "b" * 400)
        assert h.estimated_tokens() > empty + 100

    def test_should_compact_only_past_budget(self):
        h = SessionHistory()
        h.append("a" * 400, "b" * 400)
        assert not h.should_compact(10_000)
        assert h.should_compact(50)

    def test_reset_with_seed_clears_history_and_seeds_next_window(self):
        h = SessionHistory()
        h.append("one", "1")
        h.reset(seed="story so far")
        assert len(h.messages()) == 1
        assert "story so far" in h.messages()[0]["content"]
        assert h.messages()[0]["role"] == "user"

    def test_reset_without_seed_is_empty(self):
        h = SessionHistory()
        h.append("one", "1")
        h.reset(seed="")
        assert h.messages() == []

    def test_windows_counts_compactions(self):
        h = SessionHistory()
        assert h.windows == 1
        h.reset(seed="x")
        assert h.windows == 2


class TestHandoffPrompt:
    """Assert the prompt's *structure*, not its wording.

    The wording is owned by whoever is tuning translation quality and gets
    rewritten often; pinning phrases here would make every revision look like
    a broken test. What must not silently change is which sections are asked
    for, and that JSON is requested only when something consumes it.
    """

    def test_without_auto_glossary_asks_for_no_renderings(self):
        assert "<renderings>" not in handoff_prompt(with_glossary=False)

    def test_with_auto_glossary_asks_for_the_tagged_block(self):
        prompt = handoff_prompt(with_glossary=True)
        assert "<renderings>" in prompt and "</renderings>" in prompt
        assert "→" in prompt  # the line format the parser reads

    def test_both_forms_ask_for_a_summary_and_a_style_section(self):
        for flag in (True, False):
            prompt = handoff_prompt(with_glossary=flag).lower()
            assert "1." in prompt and "summary" in prompt
            assert "2." in prompt and "style" in prompt

    def test_only_the_glossary_form_has_a_third_section(self):
        assert "3." in handoff_prompt(with_glossary=True)
        assert "3." not in handoff_prompt(with_glossary=False)

    def test_the_prompt_and_the_parser_agree_on_the_tag(self):
        """The one coupling that silently loses every learned term."""
        from book_maker.session_context import parse_handoff_glossary

        prompt = handoff_prompt(with_glossary=True)
        assert "<renderings>" in prompt
        assert (
            parse_handoff_glossary("<renderings>\nA → B\n</renderings>").source
            == "tagged"
        )

    def test_the_style_section_is_capped(self):
        """Uncapped it grew past twenty bullets and became a prose glossary."""
        assert "3" in handoff_prompt(with_glossary=True).split("2.")[1].split("3.")[0]

    def test_the_summary_is_asked_for_before_the_style(self):
        prompt = handoff_prompt(with_glossary=True)
        assert prompt.index("1.") < prompt.index("2.") < prompt.index("3.")


def parse_handoff_glossary_g(text):
    """Just the glossary, for the cases that do not assert provenance."""
    return parse_handoff_glossary(text).glossary


class TestParseHandoffGlossary:
    """The report now returns the same `term → translation # note` lines the
    handoff file is written in, wrapped in tags so the block's start and end
    are unambiguous. Glossary.parse already reads that format."""

    def test_parses_a_tagged_block(self):
        text = (
            "Summary.\n\n<renderings>\n"
            "Winston → 温斯顿\nJulia → 茱莉亚 # his lover\n"
            "</renderings>\n"
        )
        g = parse_handoff_glossary(text).glossary
        assert g.lookup("Winston").translation == "温斯顿"
        assert g.lookup("Julia").note == "his lover"

    def test_ignores_prose_outside_the_tags(self):
        text = "The arrow → in prose is not a term.\n<renderings>\nA → B\n</renderings>"
        g = parse_handoff_glossary(text).glossary
        assert len(g) == 1

    def test_skips_malformed_lines_instead_of_failing(self):
        text = "<renderings>\nWinston → 温斯顿\nthis line has no arrow\n\n</renderings>"
        assert len(parse_handoff_glossary_g(text)) == 1

    def test_tolerates_list_bullets(self):
        text = "<renderings>\n- Winston → 温斯顿\n* Julia → 茱莉亚\n</renderings>"
        assert len(parse_handoff_glossary_g(text)) == 2

    def test_an_unclosed_tag_still_yields_what_follows(self):
        text = "<renderings>\nWinston → 温斯顿\n"
        assert len(parse_handoff_glossary_g(text)) == 1

    def test_no_tags_yields_an_empty_glossary(self):
        assert len(parse_handoff_glossary_g("just prose, no renderings at all")) == 0

    def test_empty_block_is_empty(self):
        assert len(parse_handoff_glossary_g("<renderings>\n</renderings>")) == 0


class TestGlossaryFallback:
    """Models drop the block; recovery must work and must be visible."""

    def test_loose_lines_are_recovered_when_the_block_is_missing(self):
        text = "Summary.\n\nWinston → 温斯顿\nJulia → 茱莉亚\n"
        result = parse_handoff_glossary(text)
        assert result.source == "scanned"
        assert len(result.glossary) == 2

    def test_a_tagged_block_is_reported_as_tagged(self):
        result = parse_handoff_glossary("<renderings>\nA → B\n</renderings>")
        assert result.source == "tagged"

    def test_nothing_at_all_is_reported_as_missing(self):
        result = parse_handoff_glossary("Just prose with no entries.")
        assert result.source == "missing"
        assert len(result.glossary) == 0

    def test_the_scan_does_not_mistake_prose_for_an_entry(self):
        text = "The publisher went from acceptance → rejection after consulting them."
        assert parse_handoff_glossary(text).source == "missing"

    def test_the_scan_rejects_an_over_long_term_side(self):
        text = ("x" * 80) + " → short"
        assert parse_handoff_glossary(text).source == "missing"


class TestStripHandoffRenderings:
    def test_removes_the_tagged_block(self):
        text = "Summary.\n\n<renderings>\nA → B\n</renderings>\n"
        out = strip_handoff_glossary(text)
        assert "<renderings>" not in out and "A → B" not in out
        assert "Summary." in out

    def test_keeps_prose_after_the_block(self):
        text = "Before.\n<renderings>\nA → B\n</renderings>\nAfter."
        out = strip_handoff_glossary(text)
        assert "Before." in out and "After." in out

    def test_drops_a_heading_left_dangling_in_any_language(self):
        text = "Summary.\n\n### 术语表\n\n<renderings>\nA → B\n</renderings>\n"
        assert "术语表" not in strip_handoff_glossary(text)

    def test_removes_an_unclosed_block_to_the_end(self):
        text = "Summary.\n<renderings>\nA → B\n"
        assert "A → B" not in strip_handoff_glossary(text)

    def test_text_without_a_block_is_unchanged(self):
        text = "Just a summary, no renderings at all."
        assert strip_handoff_glossary(text) == text


class TestHandoffReport:
    def test_persists_and_reloads(self, tmp_path):
        path = tmp_path / "book_handoff.md"
        HandoffReport(window=1, summary="so far", glossary_lines="A → B").append_to(
            path
        )
        assert "so far" in path.read_text(encoding="utf-8")

    def test_appends_without_clobbering(self, tmp_path):
        path = tmp_path / "book_handoff.md"
        HandoffReport(window=1, summary="first").append_to(path)
        HandoffReport(window=2, summary="second").append_to(path)
        body = path.read_text(encoding="utf-8")
        assert "first" in body and "second" in body

    def test_seed_text_carries_summary_and_glossary(self):
        seed = HandoffReport(
            window=1, summary="so far", glossary_lines="Winston → 温斯顿"
        ).seed_text()
        assert "so far" in seed
        assert "Winston" in seed

    def test_latest_seed_reads_back_the_last_window(self, tmp_path):
        path = tmp_path / "book_handoff.md"
        HandoffReport(window=1, summary="first").append_to(path)
        HandoffReport(window=2, summary="second").append_to(path)
        assert "second" in HandoffReport.latest_seed(path)

    def test_latest_seed_of_missing_file_is_empty(self, tmp_path):
        assert HandoffReport.latest_seed(tmp_path / "nope.md") == ""
