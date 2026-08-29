"""`--use_context session` inside ChatGPTAPI: prefix stability, compact, glossary placement.

Window mode's regression tests live in test_chatgptapi_translator.py; the ones
here are about the new mode, and above all about the invariant that pays for
it — the prefix of request N+1 must be exactly the prefix of request N plus
appended messages, or the endpoint's cache misses and session mode costs more
than the mode it replaces.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from book_maker.glossary import Glossary
from book_maker.session_context import handoff_prompt
from book_maker.translator.chatgptapi_translator import ChatGPTAPI

# A phrase unique to the compact turn, taken from the real prompt so the
# tests cannot drift from it.
HANDOFF_MARKER = handoff_prompt(with_glossary=False)[:40]


def _completion(content, cached_tokens=None):
    usage = None
    if cached_tokens is not None:
        usage = SimpleNamespace(
            prompt_tokens=100,
            prompt_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
        )
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=content, refusal=None))
        ],
        usage=usage,
    )


def _translator(replies=None, **kwargs):
    """A ChatGPTAPI wired to a scripted client. No network, real __init__."""
    kwargs.setdefault("context_flag", True)
    kwargs.setdefault("context_mode", "session")
    t = ChatGPTAPI(key="k", language="Chinese", **kwargs)
    t.model = "test-model"
    t.capabilities.record("test-model", "unsupported")

    sent = []
    answers = iter(replies or [])

    def create(**call):
        sent.append(call)
        try:
            return _completion(next(answers))
        except StopIteration:
            return _completion("译文")

    t.openai_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=Mock(side_effect=create))
        )
    )
    t.sent = sent
    return t


def _prefix(call):
    """Everything but the final (fresh) user message."""
    return call["messages"][:-1]


def _tail_containing(translator, needle):
    """The tail message of the last request that carried `needle`.

    With a tiny compact budget a compact request follows every unit, so the
    last request is not the last *translation* — say which one is meant.
    """
    for call in reversed(translator.sent):
        content = call["messages"][-1]["content"]
        if needle in content:
            return content
    raise AssertionError(f"no request carried {needle!r}")


class TestSessionHistoryGrows:
    def test_first_request_has_no_history(self):
        t = _translator()
        t.get_translation("one")
        assert _prefix(t.sent[0]) == [t.sent[0]["messages"][0]]  # system only

    def test_second_request_carries_the_first_pair(self):
        t = _translator(["一"])
        t.get_translation("one")
        t.get_translation("two")
        contents = [m["content"] for m in t.sent[1]["messages"]]
        assert any("one" in c for c in contents)
        assert "一" in contents

    def test_history_accumulates_beyond_the_window_limit(self):
        """Window mode caps at context_paragraph_limit pairs; session does not."""
        t = _translator(["1", "2", "3", "4", "5"], context_paragraph_limit=2)
        for i in range(5):
            t.get_translation(f"unit {i}")
        assert sum("unit" in m["content"] for m in t.sent[-1]["messages"]) >= 4


class TestPrefixStability:
    def test_each_request_extends_the_previous_prefix(self):
        t = _translator(["一", "二", "三"])
        for text in ("one", "two", "three"):
            t.get_translation(text)
        for earlier, later in zip(t.sent, t.sent[1:]):
            earlier_prefix = json.dumps(_prefix(earlier), ensure_ascii=False)
            later_prefix = json.dumps(_prefix(later), ensure_ascii=False)
            assert later_prefix.startswith(earlier_prefix[:-1])

    def test_each_request_replays_the_previous_one_exactly(self):
        """The strong form: request N+1 opens with request N's messages verbatim.

        Storing the raw source instead of the text actually sent leaves the
        newest pair out of the cached prefix, so every request re-reads a
        paragraph at full input price for the life of the run.
        """
        t = _translator(["一", "二", "三"])
        for text in ("one", "two", "three"):
            t.get_translation(text)
        for earlier, later in zip(t.sent, t.sent[1:]):
            sent_before = earlier["messages"]
            assert later["messages"][: len(sent_before)] == sent_before

    def test_system_message_is_byte_identical_across_requests(self):
        t = _translator(["一", "二"])
        t.get_translation("one")
        t.get_translation("two")
        assert t.sent[0]["messages"][0] == t.sent[1]["messages"][0]


class TestGlossaryPlacement:
    def test_hit_rides_in_the_fresh_tail_message(self):
        t = _translator(glossary=Glossary.parse("Winston → 温斯顿\n"))
        t.get_translation("Winston went home")
        messages = t.sent[0]["messages"]
        assert "温斯顿" in messages[-1]["content"]
        assert all("温斯顿" not in m["content"] for m in messages[:-1])

    def test_miss_injects_nothing(self):
        t = _translator(glossary=Glossary.parse("Winston → 温斯顿\n"))
        t.get_translation("nothing relevant")
        assert "glossary" not in t.sent[0]["messages"][-1]["content"].lower()

    def test_glossary_never_enters_the_system_message(self):
        """The system message is the one part that must never vary per unit."""
        t = _translator(["一", "二"], glossary=Glossary.parse("Winston → 温斯顿\n"))
        t.get_translation("Winston went home")
        t.get_translation("plain text")
        for call in t.sent:
            assert "<glossary>" not in call["messages"][0]["content"]

    def test_a_units_block_is_frozen_into_history_verbatim(self):
        """Once sent, the block stops varying, and replaying it keeps the
        cache intact — dropping it would make that pair a miss."""
        t = _translator(["一"], glossary=Glossary.parse("Winston → 温斯顿\n"))
        t.get_translation("Winston went home")
        first_tail = t.sent[0]["messages"][-1]
        t.get_translation("next")
        assert first_tail in t.sent[1]["messages"]

    def test_a_unit_with_no_hits_carries_no_block(self):
        t = _translator(["一"], glossary=Glossary.parse("Winston → 温斯顿\n"))
        t.get_translation("nothing relevant")
        t.get_translation("also nothing")
        assert all("<glossary>" not in m["content"] for m in t.sent[1]["messages"])


class TestCompact:
    def test_compacts_when_the_budget_is_reached(self, tmp_path):
        t = _translator(
            ["译文", "译文", "Summary: they walked."],
            context_compact_at=10,
            handoff_path=tmp_path / "book_handoff.md",
        )
        t.get_translation("a" * 200)
        before = len(t.sent)
        t.get_translation("b" * 200)
        assert len(t.sent) > before + 1  # an extra compact request went out

    def test_next_window_is_seeded_with_the_report(self, tmp_path):
        t = _translator(
            ["译文", "Summary: they walked.", "译文"],
            context_compact_at=10,
            handoff_path=tmp_path / "book_handoff.md",
        )
        t.get_translation("a" * 200)
        t.get_translation("b" * 200)
        seeded = [m["content"] for m in t.sent[-1]["messages"]]
        assert any("they walked" in c for c in seeded)

    def test_history_shrinks_after_a_compact(self, tmp_path):
        t = _translator(
            ["译文", "Summary.", "译文"],
            context_compact_at=10,
            handoff_path=tmp_path / "book_handoff.md",
        )
        t.get_translation("a" * 400)
        long_prefix = len(_prefix(t.sent[-1]))
        t.get_translation("b" * 400)
        assert len(_prefix(t.sent[-1])) <= long_prefix + 1

    def test_report_is_persisted(self, tmp_path):
        path = tmp_path / "book_handoff.md"
        t = _translator(
            ["译文", "Summary: they walked.", "译文"],
            context_compact_at=10,
            handoff_path=path,
        )
        t.get_translation("a" * 200)
        t.get_translation("b" * 200)
        assert "they walked" in path.read_text(encoding="utf-8")

    def test_no_compact_in_window_mode(self, tmp_path):
        t = _translator(["译文"] * 4, context_mode="window", context_compact_at=10)
        t.get_translation("a" * 400)
        t.get_translation("b" * 400)
        assert len(t.sent) == 2


class TestAutoGlossary:
    def test_off_by_default_the_compact_prompt_has_no_json(self, tmp_path):
        t = _translator(
            ["译文", "Summary.", "译文"],
            context_compact_at=10,
            handoff_path=tmp_path / "h.md",
        )
        t.get_translation("a" * 200)
        t.get_translation("b" * 200)
        compact_prompt = t.sent[1]["messages"][-1]["content"].lower()
        assert "json" not in compact_prompt

    def test_on_the_compact_prompt_asks_for_the_renderings_block(self, tmp_path):
        t = _translator(
            ["译文", "Summary.", "译文"],
            context_compact_at=10,
            glossary_auto=True,
            handoff_path=tmp_path / "h.md",
        )
        t.get_translation("a" * 200)
        t.get_translation("b" * 200)
        assert "<renderings>" in t.sent[1]["messages"][-1]["content"]

    def test_learned_terms_are_injected_into_later_units(self, tmp_path):
        report = "Summary.\n<renderings>\nBoxer → 拳击手\n</renderings>"
        t = _translator(
            ["译文", report, "译文"],
            context_compact_at=10,
            glossary_auto=True,
            handoff_path=tmp_path / "h.md",
        )
        t.get_translation("a" * 200)
        t.get_translation("Boxer pulled the cart")
        assert "拳击手" in _tail_containing(t, "Boxer pulled the cart")

    def test_pinned_terms_win_over_learned_ones(self, tmp_path):
        report = "Summary.\n<renderings>\nBoxer → 拳击手\n</renderings>"
        t = _translator(
            ["译文", report, "译文"],
            context_compact_at=10,
            glossary_auto=True,
            glossary=Glossary.parse("Boxer → 鲍克瑟\n"),
            handoff_path=tmp_path / "h.md",
        )
        t.get_translation("a" * 200)
        t.get_translation("Boxer pulled the cart")
        tail = _tail_containing(t, "Boxer pulled the cart")
        assert "鲍克瑟" in tail and "拳击手" not in tail


class TestCacheGuardrail:
    def test_warns_when_the_endpoint_never_reports_a_cache_hit(self, capsys):
        t = _translator(["一"] * 12)
        t._session_cache_warned = False
        for i in range(12):
            t.get_translation(f"unit {i}")
        assert "cache" in capsys.readouterr().out.lower()

    def test_silent_when_cache_reads_are_reported(self, capsys):
        t = _translator()
        t.openai_client.chat.completions.create = Mock(
            side_effect=lambda **c: _completion("译文", cached_tokens=64)
        )
        for i in range(12):
            t.get_translation(f"unit {i}")
        assert "not passing through cache" not in capsys.readouterr().out.lower()


class TestWindowModeUnchanged:
    def test_window_mode_still_sends_two_context_messages(self):
        t = _translator(["一", "二"], context_mode="window", context_paragraph_limit=3)
        t.get_translation("one")
        t.get_translation("two")
        assert len(_prefix(t.sent[1])) == 3  # system + one user/assistant pair

    def test_window_mode_respects_the_paragraph_limit(self):
        t = _translator(
            ["1", "2", "3", "4"], context_mode="window", context_paragraph_limit=1
        )
        for text in ("a", "b", "c", "d"):
            t.get_translation(text)
        assert len(_prefix(t.sent[-1])) == 3


class TestParallelIsolation:
    """Each parallel worker needs its own history.

    A shared SessionHistory would be appended to from several threads at
    once, interleaving chapters into one list and destroying the very
    prefix stability the mode exists for.
    """

    def _loader(self, tmp_path, workers):
        from book_maker.loader.epub_loader import EPUBBookLoader

        book = (
            Path(__file__).resolve().parent.parent / "test_books" / "animal_farm.epub"
        )
        target = tmp_path / book.name
        target.write_bytes(book.read_bytes())
        return EPUBBookLoader(
            str(target),
            ChatGPTAPI,
            "k",
            False,
            language="Chinese",
            context_flag=True,
            context_mode="session",
            parallel_workers=workers,
        )

    def test_each_parallel_clone_gets_its_own_history(self, tmp_path):
        loader = self._loader(tmp_path, workers=2)
        loader.translate_model.session.append("chapter one", "第一章")
        first = loader._clone_translator_for_context()
        second = loader._clone_translator_for_context()
        assert first.session is not second.session
        assert first.session is not loader.translate_model.session
        assert first.session.messages() == []

    def test_a_clone_does_not_write_into_the_shared_history(self, tmp_path):
        loader = self._loader(tmp_path, workers=2)
        clone = loader._clone_translator_for_context()
        clone.session.append("worker text", "译文")
        assert loader.translate_model.session.messages() == []

    def test_sequential_runs_keep_the_shared_history(self, tmp_path):
        loader = self._loader(tmp_path, workers=1)
        assert loader._clone_translator_for_context() is loader.translate_model


class TestAsyncPathIsRefused:
    def test_session_mode_fails_loud_on_the_async_path(self):
        """It threads an immutable per-call context, so the session would
        never be appended to and every request would bill uncached."""
        import asyncio

        from book_maker.translator.base_translator import AsyncTranslationUnsupported

        t = _translator()
        with pytest.raises(AsyncTranslationUnsupported):
            asyncio.run(t.translate_async("text"))

    def test_window_mode_still_works_on_the_async_path(self):
        import asyncio

        t = _translator(context_mode="window")
        assert asyncio.iscoroutinefunction(t.translate_async)


class TestCompactResilience:
    """A compact failure must not throw away the book's accumulated context."""

    # A realistic budget: each unit below is ~200 estimated tokens, so the
    # 600-token budget trips after three of them and the window stays well
    # inside the "give up, it will only keep failing" size guard.
    BUDGET = 600
    UNIT = "x" * 800

    def _failing(self, tmp_path, failures, **kw):
        t = _translator(
            ["译文"] * 40,
            context_compact_at=self.BUDGET,
            handoff_path=tmp_path / "h.md",
            **kw,
        )
        real = t.openai_client.chat.completions.create
        state = {"left": failures}

        def create(**call):
            # The compact turn is the one asking for a handoff report.
            is_compact = HANDOFF_MARKER in call["messages"][-1]["content"]
            if is_compact and state["left"] > 0:
                state["left"] -= 1
                raise RuntimeError("boom")
            return real(**call)

        t.openai_client.chat.completions.create = Mock(side_effect=create)
        return t

    def test_a_transient_failure_keeps_the_history(self, tmp_path):
        t = self._failing(tmp_path, failures=1)
        for _ in range(3):
            t.get_translation(self.UNIT)
        assert t.session.messages(), "history was discarded on one failed compact"

    def test_it_retries_the_compact_on_the_next_unit(self, tmp_path):
        t = self._failing(tmp_path, failures=1)
        for _ in range(4):
            t.get_translation(self.UNIT)
        assert (tmp_path / "h.md").exists(), "the retry never produced a report"

    def test_it_gives_up_loudly_rather_than_growing_forever(self, tmp_path, capsys):
        t = self._failing(tmp_path, failures=99)
        for _ in range(10):
            t.get_translation(self.UNIT)
        assert "handoff report failed" in capsys.readouterr().out.lower()
        # Bounded: the window is reset rather than growing without end.
        assert t.session.estimated_tokens() <= 2 * self.BUDGET

    def test_a_failed_handoff_write_does_not_fail_the_translation(self, tmp_path):
        # A directory where the handoff file should be: writing it must fail.
        path = tmp_path / "h.md"
        path.mkdir()
        t = _translator(
            ["译文", "Summary.", "译文"], context_compact_at=10, handoff_path=path
        )
        assert t.get_translation("a" * 200) == "译文"


class TestGlossaryAuthority:
    """Pinned terms are the author's; learned ones follow the model's latest
    preference. A term the author pinned must never drift, but a term only the
    model established should improve as it sees more of the book."""

    def _report(self, *pairs):
        lines = "\n".join(f"{a} → {b}" for a, b in pairs)
        return f"Summary.\n<renderings>\n{lines}\n</renderings>"

    def _t(self, tmp_path, reports, pinned=None):
        answers = []
        for r in reports:
            answers += ["译文", r]
        return _translator(
            answers,
            context_compact_at=10,
            glossary_auto=True,
            glossary=Glossary.parse(pinned) if pinned else None,
            handoff_path=tmp_path / "h.md",
        )

    def test_a_learned_term_is_adopted(self, tmp_path):
        t = self._t(tmp_path, [self._report(("Boxer", "拳击手"))])
        t.get_translation("a" * 200)
        assert t.glossary.lookup("Boxer").translation == "拳击手"

    def test_a_later_window_updates_an_earlier_learned_term(self, tmp_path):
        t = self._t(
            tmp_path,
            [self._report(("Boxer", "拳击手")), self._report(("Boxer", "鲍克瑟"))],
        )
        t.get_translation("a" * 200)
        t.get_translation("b" * 200)
        assert t.glossary.lookup("Boxer").translation == "鲍克瑟"

    def test_a_pinned_term_is_never_overridden(self, tmp_path):
        t = self._t(
            tmp_path,
            [self._report(("Boxer", "拳击手")), self._report(("Boxer", "别的"))],
            pinned="Boxer → 鲍克瑟\n",
        )
        t.get_translation("a" * 200)
        t.get_translation("b" * 200)
        assert t.glossary.lookup("Boxer").translation == "鲍克瑟"

    def test_the_model_disagreeing_with_a_pin_is_reported(self, tmp_path, capsys):
        t = self._t(
            tmp_path, [self._report(("Boxer", "拳击手"))], pinned="Boxer → 鲍克瑟\n"
        )
        t.get_translation("a" * 200)
        assert "conflict" in capsys.readouterr().out.lower()

    def test_unpinned_terms_still_accumulate_alongside_a_pin(self, tmp_path):
        t = self._t(
            tmp_path,
            [self._report(("Boxer", "拳击手"), ("Clover", "苜蓿"))],
            pinned="Boxer → 鲍克瑟\n",
        )
        t.get_translation("a" * 200)
        assert t.glossary.lookup("Clover").translation == "苜蓿"
        assert t.glossary.lookup("Boxer").translation == "鲍克瑟"

    def test_a_learned_term_is_injected_into_a_later_unit(self, tmp_path):
        t = self._t(tmp_path, [self._report(("Boxer", "拳击手"))])
        t.get_translation("a" * 200)
        t.get_translation("Boxer pulled the cart")
        assert "拳击手" in _tail_containing(t, "Boxer pulled the cart")


class TestCompactIsVisible:
    """The handoff report is what the next window inherits, so it is worth
    seeing as it happens rather than only in the file afterwards."""

    def _run(self, tmp_path, report, capsys):
        t = _translator(
            ["译文", report, "译文"],
            context_compact_at=10,
            glossary_auto=True,
            handoff_path=tmp_path / "h.md",
        )
        t.get_translation("a" * 200)
        return capsys.readouterr().out

    def test_the_summary_is_printed(self, tmp_path, capsys):
        out = self._run(tmp_path, "They walked to the barn.", capsys)
        assert "They walked to the barn." in out

    def test_the_renderings_are_printed(self, tmp_path, capsys):
        report = "Summary.\n<renderings>\nBoxer → 鲍克瑟\n</renderings>"
        out = self._run(tmp_path, report, capsys)
        assert "鲍克瑟" in out

    def test_the_window_number_is_printed(self, tmp_path, capsys):
        out = self._run(tmp_path, "Summary.", capsys)
        assert "window 1" in out.lower()

    def test_square_brackets_survive_rich_markup(self, tmp_path, capsys):
        """Reports really do contain things like [PGA]; rich would eat them."""
        out = self._run(tmp_path, "Based on the [PGA] edition.", capsys)
        assert "[PGA]" in out

    def test_an_unclosed_bracket_does_not_raise(self, tmp_path, capsys):
        out = self._run(tmp_path, "A stray [bracket and /close tag", capsys)
        assert "stray" in out
