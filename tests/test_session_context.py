"""Append-only session history, compact budgets, and the handoff report.

The invariant these tests defend: in session mode the request prefix must stay
byte-identical between requests, or the endpoint's prompt cache misses and the
whole history is re-billed at 1x — which costs more than the window mode this
feature replaces.
"""

import json

import pytest

from book_maker.session_context import (
    strip_handoff_glossary,
    DEFAULT_COMPACT_BUDGET,
    GLOSSARY_JSON_SCHEMA,
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
    def test_without_auto_glossary_asks_for_prose_only(self):
        prompt = handoff_prompt(with_glossary=False)
        assert "json" not in prompt.lower()

    def test_with_auto_glossary_asks_for_json(self):
        prompt = handoff_prompt(with_glossary=True)
        assert "json" in prompt.lower()
        assert "term" in prompt.lower()

    def test_both_forms_ask_for_summary_and_style(self):
        for flag in (True, False):
            prompt = handoff_prompt(with_glossary=flag).lower()
            assert "summary" in prompt
            assert "style" in prompt or "register" in prompt

    def test_the_summary_is_asked_for_content_not_process(self):
        """Left unsaid, the model reports which sections it processed."""
        prompt = handoff_prompt(with_glossary=True).lower()
        assert "content" in prompt
        assert "not about the translation" in prompt or "never describe" in prompt

    def test_style_is_capped_so_it_stays_short(self):
        prompt = handoff_prompt(with_glossary=True).lower()
        assert "at most" in prompt

    def test_with_a_glossary_term_pairs_are_kept_out_of_the_style_section(self):
        """Otherwise every rendering is written twice, in two formats."""
        prompt = handoff_prompt(with_glossary=True).lower()
        assert "only place" in prompt or "not list term" in prompt

    def test_without_a_glossary_the_style_section_may_carry_key_renderings(self):
        """There is nowhere else for them to go when the glossary is off."""
        prompt = handoff_prompt(with_glossary=False).lower()
        assert "only place" not in prompt


class TestParseHandoffGlossary:
    def test_parses_a_fenced_json_block(self):
        text = 'Summary here.\n```json\n[{"term": "Winston", "translation": "温斯顿"}]\n```\n'
        g = parse_handoff_glossary(text)
        assert g.lookup("Winston").translation == "温斯顿"

    def test_parses_a_bare_json_array(self):
        g = parse_handoff_glossary('[{"term": "Julia", "translation": "茱莉亚"}]')
        assert len(g) == 1

    def test_no_json_yields_empty_glossary(self):
        assert len(parse_handoff_glossary("just prose, no json at all")) == 0

    def test_malformed_json_yields_empty_glossary_not_an_exception(self):
        assert len(parse_handoff_glossary("```json\n[{term: broken]\n```")) == 0


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


class TestGlossarySchema:
    def test_schema_shape_is_an_array_of_term_objects(self):
        assert GLOSSARY_JSON_SCHEMA["type"] == "array"
        props = GLOSSARY_JSON_SCHEMA["items"]["properties"]
        assert "term" in props and "translation" in props


class TestStripHandoffGlossary:
    """The JSON block is parsed into entries, so leaving it in the prose too
    would duplicate every term in the file and in the next window's seed."""

    def test_removes_a_fenced_json_block(self):
        text = 'Summary.\n\n## 3. Glossary\n\n```json\n[{"term": "A", "translation": "B"}]\n```\n'
        out = strip_handoff_glossary(text)
        assert "```json" not in out
        assert '"term"' not in out
        assert "Summary." in out

    def test_keeps_prose_that_follows_the_block(self):
        text = 'Before.\n```json\n[{"term": "A", "translation": "B"}]\n```\nAfter.'
        out = strip_handoff_glossary(text)
        assert "Before." in out and "After." in out

    def test_drops_an_emptied_glossary_heading(self):
        text = "Summary.\n\n## 3. Glossary\n\n```json\n[]\n```\n"
        assert "Glossary" not in strip_handoff_glossary(text)

    def test_drops_a_trailing_heading_in_any_language(self):
        """The model writes the heading in the target language."""
        text = 'Summary.\n\n### 术语表\n\n```json\n[{"term": "A", "translation": "B"}]\n```\n'
        out = strip_handoff_glossary(text)
        assert "术语表" not in out
        assert "Summary." in out

    def test_a_heading_with_prose_after_it_is_kept(self):
        text = "Summary.\n\n### Notes\n\nStill relevant.\n\n```json\n[]\n```"
        out = strip_handoff_glossary(text)
        assert "### Notes" in out and "Still relevant." in out

    def test_text_without_json_is_unchanged(self):
        text = "Just a summary, no glossary at all."
        assert strip_handoff_glossary(text) == text
