"""`--use_context` grew an optional value; every old command line must still mean what it meant.

The back-compat case is the one that matters: bare `--use_context` has been in
READMEs and shell scripts for years, and it has to keep selecting window mode
with the same paragraph limit as before.
"""

import pytest

from book_maker.cli import parse_args, resolve_context_mode


def _parse(*args):
    return parse_args(["--book_name", "book.epub", *args])


class TestUseContextValue:
    def test_absent_means_no_context(self):
        options = _parse()
        assert options.context_mode is None
        assert resolve_context_mode(options) == (False, None)

    def test_bare_flag_still_means_window(self):
        options = _parse("--use_context")
        assert resolve_context_mode(options) == (True, "window")

    def test_explicit_window(self):
        assert resolve_context_mode(_parse("--use_context", "window")) == (
            True,
            "window",
        )

    def test_session_mode(self):
        assert resolve_context_mode(_parse("--use_context", "session")) == (
            True,
            "session",
        )

    def test_unknown_mode_is_rejected(self):
        with pytest.raises(SystemExit):
            _parse("--use_context", "nonsense")

    def test_paragraph_limit_still_parses_alongside(self):
        options = _parse("--use_context", "--context_paragraph_limit", "5")
        assert options.context_paragraph_limit == 5

    def test_flag_does_not_swallow_a_following_option(self):
        options = _parse("--use_context", "--single_translate")
        assert resolve_context_mode(options) == (True, "window")
        assert options.single_translate is True


class TestCompactBudgetFlag:
    def test_defaults_to_unset_so_the_model_decides(self):
        assert _parse("--use_context", "session").context_compact_at is None

    def test_explicit_value_is_kept(self):
        options = _parse("--use_context", "session", "--context-compact-at", "2500")
        assert options.context_compact_at == 2500

    def test_rejects_a_non_integer(self):
        with pytest.raises(SystemExit):
            _parse("--context-compact-at", "lots")

    def test_rejects_zero_and_negative_budgets(self):
        """0 disables compaction outright; a negative one is meaningless."""
        for value in ("0", "-1"):
            with pytest.raises(SystemExit):
                _parse("--context-compact-at", value)

    def test_rejects_a_budget_too_small_to_hold_a_paragraph(self):
        with pytest.raises(SystemExit):
            _parse("--context-compact-at", "50")


class TestGlossaryFlags:
    def test_glossary_defaults_to_none(self):
        assert _parse().glossary is None

    def test_glossary_path_is_kept(self):
        assert _parse("--glossary", "terms.txt").glossary == "terms.txt"

    def test_glossary_auto_defaults_off(self):
        assert _parse().glossary_auto is False

    def test_glossary_auto_can_be_enabled(self):
        assert _parse("--glossary-auto").glossary_auto is True


class TestSessionOnlyWarnings:
    """`--context-compact-at` and `--glossary-auto` need a context window.

    Session mode is one. So is the codex format, whose thread *is* the window
    and which compacts whether or not --use_context was passed — warning there
    would claim a flag was ignored when it was obeyed.
    """

    def _warnings(self, capsys, *args, book):
        """Run the CLI far enough to emit the warning, then let it fail.

        The warning is printed before the book is opened, so a placeholder
        file is enough — whatever it raises afterwards is not the point.
        """
        import sys
        from unittest.mock import patch

        from book_maker.cli import main

        argv = ["make_book.py", "--book_name", book, *args]
        with patch.object(sys, "argv", argv):
            try:
                main()
            except BaseException:
                pass
        return capsys.readouterr().out

    def test_warns_for_a_plain_openai_run(self, capsys, tmp_path):
        book = tmp_path / "b.epub"
        book.write_bytes(b"not really an epub")
        out = self._warnings(
            capsys, "--glossary-auto", "--api_format", "google", book=str(book)
        )
        assert "only applies" in out

    def test_does_not_warn_for_codex(self, capsys, tmp_path):
        book = tmp_path / "b.epub"
        book.write_bytes(b"not really an epub")
        out = self._warnings(
            capsys, "--glossary-auto", "--api_format", "codex", book=str(book)
        )
        assert "only applies" not in out
