"""The `codex` translator format: threads, windows, glossary, preflight."""

import pytest

from book_maker.glossary import Glossary
from book_maker.codex_client import CodexLoginRequired, RateLimits
from book_maker.translator import FORMAT_DICT, LLM_FORMATS
from book_maker.translator.codex_translator import Codex


class FakeServer:
    """Stands in for CodexAppServer."""

    def __init__(self, answers=None, limits=None):
        self.answers = list(answers or [])
        self.turns = []
        self.threads = []
        self.closed = False
        self._limits = (
            limits
            if limits is not None
            else RateLimits(
                used_percent=10,
                window_minutes=300,
                resets_at=1788055986,
                plan_type="plus",
                reached_type=None,
            )
        )

    def start(self):
        return self

    def close(self):
        self.closed = True

    def ensure_logged_in(self):
        return self._limits

    def rate_limits(self):
        return self._limits

    def start_thread(self, model=None, base_instructions=None, cwd=None):
        self.threads.append({"model": model, "base_instructions": base_instructions})
        return f"th-{len(self.threads)}"

    def run_turn(self, thread_id, text, output_schema=None, timeout=None):
        self.turns.append({"thread": thread_id, "text": text, "schema": output_schema})
        return self.answers.pop(0) if self.answers else "译文"


def _codex(answers=None, limits=None, **kwargs):
    server = FakeServer(answers, limits)
    translator = Codex(key="", language="Chinese", server=server, **kwargs)
    translator.server = server
    return translator


class TestRegistration:
    def test_registered_as_a_format(self):
        assert FORMAT_DICT["codex"] is Codex

    def test_counts_as_a_model_bearing_format(self):
        assert "codex" in LLM_FORMATS


class TestPreflight:
    def test_login_failure_is_raised_not_swallowed(self):
        server = FakeServer()
        server.ensure_logged_in = lambda: (_ for _ in ()).throw(
            CodexLoginRequired("run codex login")
        )
        translator = Codex(key="", language="Chinese", server=server)
        with pytest.raises(CodexLoginRequired):
            translator.preflight()

    def test_warns_when_the_window_is_nearly_spent(self, capsys):
        limits = RateLimits(
            used_percent=95,
            window_minutes=300,
            resets_at=1788055986,
            plan_type="plus",
            reached_type=None,
        )
        _codex(limits=limits).preflight()
        assert "95" in capsys.readouterr().out

    def test_quiet_when_there_is_plenty_left(self, capsys):
        _codex().preflight()
        assert "Warning" not in capsys.readouterr().out


class TestTranslation:
    def test_returns_the_turn_text(self):
        t = _codex(["狗叫了。"])
        assert t.translate("The dog barked.") == "狗叫了。"

    def test_starts_one_thread_and_reuses_it(self):
        """A fresh thread costs ~17k tokens of preamble; reuse is the point."""
        t = _codex(["一", "二", "三"])
        for text in ("one", "two", "three"):
            t.translate(text)
        assert len(t.server.threads) == 1
        assert {turn["thread"] for turn in t.server.turns} == {"th-1"}

    def test_the_thread_carries_the_translation_instructions(self):
        t = _codex(["一"])
        t.translate("one")
        assert "translat" in t.server.threads[0]["base_instructions"].lower()

    def test_the_model_is_passed_through(self):
        t = _codex(["一"])
        t.set_model_list(["gpt-5.6-sol"])
        t.translate("one")
        assert t.server.threads[0]["model"] == "gpt-5.6-sol"

    def test_glossary_hits_ride_with_the_unit(self):
        t = _codex(["一"], glossary=Glossary.parse("Winston → 温斯顿\n"))
        t.translate("Winston went home")
        assert "温斯顿" in t.server.turns[0]["text"]

    def test_glossary_misses_inject_nothing(self):
        t = _codex(["一"], glossary=Glossary.parse("Winston → 温斯顿\n"))
        t.translate("nothing relevant")
        assert "glossary" not in t.server.turns[0]["text"].lower()


class TestWindowing:
    # Each 200-char unit is ~50 estimated tokens, so a 100-token budget trips
    # after the second one — not after every one.
    ANSWERS = ["一", "二", "Summary: they walked."]

    def test_a_new_thread_starts_when_the_budget_is_reached(self, tmp_path):
        t = _codex(self.ANSWERS, context_compact_at=100, handoff_path=tmp_path / "h.md")
        t.translate("a" * 200)
        assert len(t.server.threads) == 1
        t.translate("b" * 200)
        assert len(t.server.threads) == 2

    def test_the_next_thread_is_seeded_with_the_handoff(self, tmp_path):
        t = _codex(self.ANSWERS, context_compact_at=100, handoff_path=tmp_path / "h.md")
        t.translate("a" * 200)
        t.translate("b" * 200)
        assert "they walked" in t.server.threads[1]["base_instructions"]

    def test_the_handoff_turn_runs_on_the_thread_being_retired(self, tmp_path):
        t = _codex(self.ANSWERS, context_compact_at=100, handoff_path=tmp_path / "h.md")
        t.translate("a" * 200)
        t.translate("b" * 200)
        handoff = t.server.turns[-1]
        assert handoff["thread"] == "th-1"
        assert "handoff" in handoff["text"].lower()

    def test_the_report_is_persisted(self, tmp_path):
        path = tmp_path / "h.md"
        t = _codex(self.ANSWERS, context_compact_at=100, handoff_path=path)
        t.translate("a" * 200)
        t.translate("b" * 200)
        assert "they walked" in path.read_text(encoding="utf-8")

    def test_no_windowing_without_a_budget_being_hit(self, tmp_path):
        t = _codex(["一", "二"], context_compact_at=100_000)
        t.translate("short")
        t.translate("also short")
        assert len(t.server.threads) == 1


class TestKeyHandling:
    def test_needs_no_api_key(self):
        assert Codex(key="", language="Chinese", server=FakeServer()) is not None

    def test_rotate_key_is_a_no_op(self):
        _codex().rotate_key()  # must not raise: there is no key to rotate


class TestConcurrency:
    """Parallel workers share one Codex instance and therefore one thread.

    `_clone_translator_for_context` only clones translators that carry
    `context_flag`, which this one does not: a codex thread *is* the context,
    so there are no per-worker buffers to reset. Turns must therefore
    serialize, or chapters interleave into one thread and the window
    accounting races.
    """

    def test_concurrent_translations_are_serialized(self):
        import threading

        t = _codex()
        overlaps = []
        active = []

        original = t.server.run_turn

        def slow_turn(thread_id, text, output_schema=None, timeout=None):
            active.append(text)
            if len(active) > 1:
                overlaps.append(tuple(active))
            threading.Event().wait(0.02)
            active.remove(text)
            return original(thread_id, text, output_schema, timeout)

        t.server.run_turn = slow_turn
        threads = [
            threading.Thread(
                target=t.translate, args=(f"unit {i}",), kwargs={"needprint": False}
            )
            for i in range(4)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert overlaps == []
        assert len(t.server.turns) == 4

    def test_window_accounting_survives_concurrent_calls(self):
        import threading

        t = _codex(context_compact_at=100_000)
        threads = [
            threading.Thread(
                target=t.translate, args=("a" * 200,), kwargs={"needprint": False}
            )
            for _ in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        # 8 units of ~50 source tokens each, none lost to a lost update.
        assert t._window_tokens >= 8 * 50
