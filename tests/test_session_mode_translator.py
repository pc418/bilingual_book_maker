"""`--use_context session` inside ChatGPTAPI: prefix stability and compact.

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

from openai import LengthFinishReasonError

from book_maker.session_context import DEFAULT_COMPACT_BUDGET, handoff_prompt
from book_maker.translator.chatgptapi_translator import (
    ChatGPTAPI,
    ContextWindowUnknown,
    batch_field_name,
    single_field_name,
)

SINGLE_FIELD = single_field_name("Chinese")
BATCH_FIELD = batch_field_name("Chinese")

# A phrase unique to the compact turn, taken from the real prompt so the
# tests cannot drift from it.
HANDOFF_MARKER = handoff_prompt()[:40]


def _usage(cached_tokens):
    if cached_tokens is None:
        return None
    return SimpleNamespace(
        prompt_tokens=100,
        prompt_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
    )


def _completion(content, cached_tokens=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content=content, refusal=None))
        ],
        usage=_usage(cached_tokens),
    )


def _parsed_completion(content, cached_tokens=None):
    """`.parse` style completion — what a "strict" endpoint answers with."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    parsed=SimpleNamespace(**{SINGLE_FIELD: content}),
                    refusal=None,
                    content=None,
                ),
                finish_reason="stop",
            )
        ],
        usage=_usage(cached_tokens),
    )


def _translator(replies=None, verdict=False, cached_tokens=None, **kwargs):
    """A ChatGPTAPI wired to a scripted client. No network, real __init__.

    `verdict` picks the path under test: the default forces the plain
    (delimiter) path, since most of these tests are about the session history
    rather than structured outputs. `"strict"` sends translation through
    Structured Outputs — the path a strict endpoint really uses.
    """
    kwargs.setdefault("context_flag", True)
    kwargs.setdefault("context_mode", "session")
    t = ChatGPTAPI(key="k", language="Chinese", **kwargs)
    t.model = "test-model"
    t._structured_support["test-model"] = verdict

    sent = []
    answers = iter(replies or [])

    def _reply():
        try:
            return next(answers)
        except StopIteration:
            return "译文"

    def create(**call):
        sent.append(call)
        return _completion(_reply(), cached_tokens)

    def parse(**call):
        sent.append(call)
        return _parsed_completion(_reply(), cached_tokens)

    t.openai_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=Mock(side_effect=create),
                parse=Mock(side_effect=parse),
            )
        )
    )
    t.sent = sent
    return t


def _prefix(call):
    """Everything but the final (fresh) user message."""
    return call["messages"][:-1]


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

    # The guardrail has to hold on the structured path too: every "strict"
    # endpoint translates through `.parse`, and that is exactly where session
    # mode is used, so a reading taken only on the plain path is never taken.
    def test_warns_on_the_structured_path_too(self, capsys):
        t = _translator(["一"] * 12, verdict="strict")
        for i in range(12):
            t.get_translation(f"unit {i}")
        assert t.openai_client.chat.completions.parse.call_count == 12
        assert "cache" in capsys.readouterr().out.lower()

    def test_structured_path_silent_when_cache_reads_are_reported(self, capsys):
        t = _translator(["一"] * 12, verdict="strict", cached_tokens=64)
        for i in range(12):
            t.get_translation(f"unit {i}")
        assert "cache" not in capsys.readouterr().out.lower()

    def test_structured_batch_requests_are_counted_too(self, capsys):
        """`--accumulated_num` sends whole batches through `.parse`. Those are
        the requests being billed, so they are the ones to read."""
        t = _translator(verdict="strict")
        t.openai_client.chat.completions.parse = Mock(
            side_effect=lambda **c: SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            parsed=SimpleNamespace(**{BATCH_FIELD: ["一", "二"]}),
                            refusal=None,
                            content=None,
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=None,
            )
        )
        for i in range(12):
            assert t.translate_list([f"a {i}", f"b {i}"]) == ["一", "二"]
        assert "cache" in capsys.readouterr().out.lower()

    def test_a_truncated_structured_answer_still_counts_its_request(self):
        """The truncated request was billed before it was thrown away; not
        counting it delays the warning by one request per truncation."""
        error = LengthFinishReasonError(
            completion=SimpleNamespace(
                usage=None,
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"x":"半'),
                        finish_reason="length",
                    )
                ],
            )
        )
        t = _translator(verdict="strict")
        t.openai_client.chat.completions.parse = Mock(side_effect=error)

        t.get_translation("one")

        # Two billed requests went out for this paragraph: the truncated
        # structured one and the plain retranslation.
        assert t._session_requests == 2


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


class TestCompactIsVisible:
    """The handoff report is what the next window inherits, so it is worth
    seeing as it happens rather than only in the file afterwards."""

    def _run(self, tmp_path, report, capsys):
        t = _translator(
            ["译文", report, "译文"],
            context_compact_at=10,
            handoff_path=tmp_path / "h.md",
        )
        t.get_translation("a" * 200)
        return capsys.readouterr().out

    def test_the_summary_is_printed(self, tmp_path, capsys):
        out = self._run(tmp_path, "They walked to the barn.", capsys)
        assert "They walked to the barn." in out

    def test_the_window_number_is_printed(self, tmp_path, capsys):
        out = self._run(tmp_path, "Summary.", capsys)
        assert "window 1" in out.lower()

    def test_square_brackets_survive_rich_markup(self, tmp_path, capsys):
        """Reports really do contain things like [PGA]; rich would eat them."""
        out = self._run(tmp_path, "Based on the [PGA] edition.", capsys)
        assert "[PGA]" in out

    def test_quiet_suppresses_the_report(self, tmp_path, capsys):
        t = _translator(
            ["译文", "They walked to the barn.", "译文"],
            context_compact_at=10,
            handoff_path=tmp_path / "h.md",
        )
        t.quiet = True
        t.get_translation("a" * 200)
        assert "They walked to the barn." not in capsys.readouterr().out

    def test_quiet_still_writes_the_file(self, tmp_path, capsys):
        """Suppressing the echo must not lose the record."""
        path = tmp_path / "h.md"
        t = _translator(
            ["译文", "They walked to the barn.", "译文"],
            context_compact_at=10,
            handoff_path=path,
        )
        t.quiet = True
        t.get_translation("a" * 200)
        assert "They walked to the barn." in path.read_text(encoding="utf-8")

    def test_quiet_does_not_silence_a_failed_compact(self, tmp_path, capsys):
        """--quiet drops echoes, not warnings."""
        t = _translator(
            ["译文"] * 6, context_compact_at=10, handoff_path=tmp_path / "h.md"
        )
        t.quiet = True
        real = t.openai_client.chat.completions.create

        def create(**call):
            # Fail only the compact turn; the translation itself must succeed
            # or the run never reaches the compact at all.
            if HANDOFF_MARKER in call["messages"][-1]["content"]:
                raise RuntimeError("boom")
            return real(**call)

        t.openai_client.chat.completions.create = Mock(side_effect=create)
        t.get_translation("a" * 200)
        assert "handoff report failed" in capsys.readouterr().out.lower()

    def test_an_unclosed_bracket_does_not_raise(self, tmp_path, capsys):
        out = self._run(tmp_path, "A stray [bracket and /close tag", capsys)
        assert "stray" in out


class TestCompactionDisabled:
    """`--no-context-compact`: roll the window over, never pay for a report.

    The seam still happens where the budget puts it — what the flag removes is
    the handoff turn and everything it carries forward, so the next window
    starts empty, like Codex's `/new`.
    """

    UNIT = "x" * 800  # ~200 estimated tokens

    def _disabled(self, **kw):
        return _translator(
            ["译文"] * 40, no_context_compact=True, context_compact_at=600, **kw
        )

    def test_it_rolls_the_window_over_without_a_handoff_turn(self):
        t = self._disabled()
        for _ in range(4):
            t.get_translation(self.UNIT)
        assert t.session.windows > 1, "the window never rolled over"
        assert not any(
            HANDOFF_MARKER in call["messages"][-1]["content"] for call in t.sent
        ), "a handoff report was requested with compaction disabled"

    def test_the_next_window_starts_empty(self):
        t = self._disabled()
        for _ in range(4):
            t.get_translation(self.UNIT)
        # Only what was appended after the reset, and no seed message before it.
        assert t.session.estimated_tokens() < 600

    def test_it_writes_no_handoff_file(self, tmp_path):
        path = tmp_path / "h.md"
        t = self._disabled(handoff_path=path)
        for _ in range(4):
            t.get_translation(self.UNIT)
        assert not path.exists(), "a handoff file was written with compaction off"

    def test_without_the_flag_a_budget_still_compacts(self):
        t = _translator(["译文"] * 40, context_compact_at=600)
        for _ in range(4):
            t.get_translation(self.UNIT)
        assert any(
            HANDOFF_MARKER in call["messages"][-1]["content"] for call in t.sent
        ), "a positive budget must still ask for the handoff report"


class TestAutoCompactBudget:
    """`--context-compact-at 0`: size the budget from the model's own window."""

    def _with_model(self, model_object, **kw):
        kw.setdefault("context_compact_at", 0)
        t = _translator(["译文"] * 40, **kw)
        t.openai_client.models = SimpleNamespace(
            retrieve=Mock(
                side_effect=(
                    model_object
                    if callable(model_object)
                    else lambda *a, **k: model_object
                )
            )
        )
        return t

    def test_it_takes_nine_tenths_of_the_reported_window(self, capsys):
        t = self._with_model(SimpleNamespace(id="test-model", context_length=10_000))
        assert t._session_budget() == 9_000
        assert "10000" in capsys.readouterr().out

    def test_an_endpoint_reporting_no_window_stops_the_run(self):
        # `0` asked for the endpoint's number. Falling back to the default
        # would be a guess about the one model nobody could size, which is
        # what passing `0` was meant to avoid.
        t = self._with_model(SimpleNamespace(id="test-model"))
        with pytest.raises(ContextWindowUnknown) as stop:
            t._session_budget()
        message = str(stop.value)
        assert "--context-compact-at 0" in message
        assert "test-model" in message
        assert "8000" in message, "it must say what to pass instead"

    def test_a_lookup_that_never_answers_stops_the_run(self):
        def boom(*a, **k):
            raise RuntimeError("connection reset")

        t = self._with_model(boom)
        with pytest.raises(ContextWindowUnknown) as stop:
            t._session_budget()
        assert "could not be asked" in str(stop.value)

    def test_a_number_needs_no_lookup_at_all(self):
        t = self._with_model(SimpleNamespace(id="test-model"), context_compact_at=600)
        t.preflight()
        assert t._session_budget() == 600
        assert t.openai_client.models.retrieve.call_count == 0

    def test_preflight_asks_before_anything_is_translated(self):
        t = self._with_model(SimpleNamespace(id="test-model", context_length=10_000))
        t.preflight()
        assert t.openai_client.models.retrieve.call_count == 1
        assert not t.sent, "preflight must not send a translation request"

    def test_preflight_is_where_an_unanswerable_endpoint_is_caught(self):
        t = self._with_model(SimpleNamespace(id="test-model"))
        with pytest.raises(ContextWindowUnknown):
            t.preflight()
        assert not t.sent, "nothing may be paid for before the refusal"

    def test_the_window_is_looked_up_once(self):
        t = self._with_model(SimpleNamespace(id="test-model", context_length=10_000))
        for _ in range(3):
            t._session_budget()
        assert t.openai_client.models.retrieve.call_count == 1


class TestAutoBudgetAcrossModels:
    """`--model_list` rotates models; the budget has to survive the smallest."""

    def _with_windows(self, windows):
        """`windows`: model name -> what `models.retrieve` answers with."""
        t = _translator(["译文"] * 40, context_compact_at=0)

        def retrieve(model):
            answer = windows[model]
            if isinstance(answer, Exception):
                raise answer
            return answer

        t.openai_client.models = SimpleNamespace(retrieve=Mock(side_effect=retrieve))
        return t

    def test_it_takes_the_smallest_window_in_play(self):
        t = self._with_windows(
            {
                "big": SimpleNamespace(id="big", context_length=100_000),
                "small": SimpleNamespace(id="small", context_length=10_000),
            }
        )
        t.model = "big"
        assert t._session_budget() == 90_000
        t.model = "small"
        assert t._session_budget() == 9_000, "a smaller model must shrink the budget"

    def test_every_configured_model_is_measured_before_the_seam(self):
        """A smaller model must not first be discovered by failing on it."""
        t = self._with_windows(
            {
                "big": SimpleNamespace(id="big", context_length=100_000),
                "small": SimpleNamespace(id="small", context_length=10_000),
            }
        )
        t._model_names = ["big", "small"]
        t.model = "big"
        assert t._session_budget() == 9_000, "the smaller model was not measured"

    def test_one_unmeasurable_model_stops_the_whole_run(self):
        from openai import NotFoundError

        missing = NotFoundError(
            "no such model", response=Mock(status_code=404, headers={}), body=None
        )
        t = self._with_windows(
            {
                "big": SimpleNamespace(id="big", context_length=100_000),
                "mystery": missing,
            }
        )
        t._model_names = ["big", "mystery"]
        t.model = "big"
        # The unknown model may be the smallest of them, and the history is
        # shared, so there is no budget here that is not a guess.
        with pytest.raises(ContextWindowUnknown) as stop:
            t._session_budget()
        assert "mystery" in str(stop.value)

    def test_each_model_is_looked_up_once(self):
        t = self._with_windows(
            {"a": SimpleNamespace(id="a", context_length=10_000)},
        )
        t.model = "a"
        for _ in range(3):
            t._session_budget()
        assert t.openai_client.models.retrieve.call_count == 1

    def test_a_transient_failure_is_retried_before_it_becomes_fatal(self):
        answers = [
            RuntimeError("connection reset"),
            SimpleNamespace(id="a", context_length=10_000),
        ]

        def retrieve(model):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        t = self._with_windows({})
        t.openai_client.models.retrieve = Mock(side_effect=retrieve)
        t.model = "a"
        assert t._session_budget() == 9_000, "one blip must not end the run"

    def test_it_stops_asking_a_hopeless_endpoint_and_then_refuses(self):
        t = self._with_windows({"a": RuntimeError("connection reset")})
        t.model = "a"
        with pytest.raises(ContextWindowUnknown):
            t._session_budget()
        assert t.openai_client.models.retrieve.call_count <= 3

    def test_a_missing_model_is_settled_not_retried(self):
        from openai import NotFoundError

        error = NotFoundError(
            "no such model", response=Mock(status_code=404, headers={}), body=None
        )
        t = self._with_windows({"a": error})
        t.model = "a"
        with pytest.raises(ContextWindowUnknown) as stop:
            t._session_budget()
        assert "no record of" in str(stop.value)
        assert t.openai_client.models.retrieve.call_count == 1


class TestReportedWindowIsChecked:
    """A number off the wire decides how much context a run carries."""

    def _budget_for(self, model_object):
        t = _translator(["译文"], context_compact_at=0)
        t.openai_client.models = SimpleNamespace(
            retrieve=Mock(return_value=model_object)
        )
        return t._session_budget()

    def _refused(self, model_object):
        """A record with nothing usable in it is a record with no window."""
        with pytest.raises(ContextWindowUnknown) as stop:
            self._budget_for(model_object)
        assert "no usable one" in str(stop.value)

    def test_a_boolean_is_not_a_window(self):
        # `True` is an int in Python, and 0.9 * True is 0 — no rollover at all.
        self._refused(SimpleNamespace(id="m", context_length=True))

    def test_a_window_too_small_to_hold_a_paragraph_is_refused(self):
        self._refused(SimpleNamespace(id="m", context_length=100))

    def test_an_absurd_window_is_refused(self):
        self._refused(SimpleNamespace(id="m", context_length=10**30))

    def test_a_string_is_refused(self):
        self._refused(SimpleNamespace(id="m", context_length="128000"))
