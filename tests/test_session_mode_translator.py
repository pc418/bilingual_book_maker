"""`--use_context session` inside ChatGPTAPI: prefix stability, compact, glossary placement.

Window mode's regression tests live in test_chatgptapi_translator.py; the ones
here are about the new mode, and above all about the invariant that pays for
it — the prefix of request N+1 must be exactly the prefix of request N plus
appended messages, or the endpoint's cache misses and session mode costs more
than the mode it replaces.
"""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from book_maker.glossary import Glossary
from book_maker.translator.chatgptapi_translator import ChatGPTAPI


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
        assert "one" in contents
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

    def test_glossary_never_enters_the_cached_history(self):
        """A varying block in the prefix would break the cache for every later request."""
        t = _translator(["一", "二"], glossary=Glossary.parse("Winston → 温斯顿\n"))
        t.get_translation("Winston went home")
        t.get_translation("plain text")
        assert all("<glossary>" not in m["content"] for m in _prefix(t.sent[1]))

    def test_history_stores_the_source_without_the_glossary_block(self):
        t = _translator(["一"], glossary=Glossary.parse("Winston → 温斯顿\n"))
        t.get_translation("Winston went home")
        t.get_translation("next")
        history = [m["content"] for m in _prefix(t.sent[1])]
        assert "Winston went home" in history


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

    def test_on_the_compact_prompt_asks_for_json(self, tmp_path):
        t = _translator(
            ["译文", "Summary.", "译文"],
            context_compact_at=10,
            glossary_auto=True,
            handoff_path=tmp_path / "h.md",
        )
        t.get_translation("a" * 200)
        t.get_translation("b" * 200)
        assert "json" in t.sent[1]["messages"][-1]["content"].lower()

    def test_learned_terms_are_injected_into_later_units(self, tmp_path):
        report = 'Summary.\n```json\n[{"term": "Boxer", "translation": "拳击手"}]\n```'
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
        report = 'Summary.\n```json\n[{"term": "Boxer", "translation": "拳击手"}]\n```'
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
