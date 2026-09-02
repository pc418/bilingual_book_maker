"""`--plan-classify auto`: plan mode when the endpoint verifies a strict schema.

Three things are pinned here. What `auto` resolves to (a pure decision over
the book type, the route, the tag flag and the probe verdict), that the probe
is only paid for when its answer can change the outcome, and that a plan that
falls over under `auto` degrades to tag mode instead of ending the run —
while an explicitly requested plan mode still fails loud.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from book_maker.cli import resolve_plan_mode

REPO = Path(__file__).resolve().parent.parent
BOOK = REPO / "test_books" / "animal_farm.epub"
HERMETIC = Path(__file__).resolve().parent / "hermetic"

KEY_ENV_VARS = (
    "BBM_API_KEY",
    "OPENAI_API_KEY",
    "BBM_OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "BBM_CLAUDE_API_KEY",
    "BBM_CAIYUN_API_KEY",
    "BBM_DEEPL_API_KEY",
    "BBM_ORCAROUTER_API_KEY",
)


class _Probe:
    """A probe that records whether it was asked, and what it answered."""

    def __init__(self, verdict="strict", error=None):
        self.verdict = verdict
        self.error = error
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.verdict


# ------------------------------------------------------------ the decision


def test_strict_schema_on_an_epub_turns_the_plan_on():
    probe = _Probe("strict")
    mode, reason = resolve_plan_mode("epub", "openai", False, probe)
    assert mode == "model"
    assert probe.calls == 1
    assert "strict" in reason


@pytest.mark.parametrize("verdict", ["shape", "json", False, "unsupported"])
def test_anything_below_strict_stays_in_tag_mode(verdict):
    probe = _Probe(verdict)
    mode, reason = resolve_plan_mode("epub", "openai", False, probe)
    assert mode == "none"
    assert probe.calls == 1
    assert "schema" in reason


@pytest.mark.parametrize("api_format", ["anthropic", "google", "deepl", "codex"])
def test_other_routes_are_never_probed(api_format):
    # only the openai wire format has the capability probe; asking any other
    # endpoint would be a paid request that cannot answer the question
    probe = _Probe("strict")
    mode, reason = resolve_plan_mode("epub", api_format, False, probe)
    assert mode == "none"
    assert probe.calls == 0
    assert api_format in reason


@pytest.mark.parametrize("book_type", ["txt", "md", "srt", "pdf"])
def test_non_epub_input_is_decided_without_paying(book_type):
    probe = _Probe("strict")
    mode, reason = resolve_plan_mode(book_type, "openai", False, probe)
    assert mode == "none"
    assert probe.calls == 0
    assert "epub" in reason


def test_an_explicit_tag_selection_opts_out_before_the_probe():
    probe = _Probe("strict")
    mode, reason = resolve_plan_mode("epub", "openai", True, probe)
    assert mode == "none"
    assert probe.calls == 0
    assert "--translate-tags" in reason


def test_a_probe_that_fails_is_not_fatal():
    probe = _Probe(error=RuntimeError("no route to host"))
    mode, reason = resolve_plan_mode("epub", "openai", False, probe)
    assert mode == "none"
    assert "no route to host" in reason


def test_a_route_with_no_probe_at_all_stays_in_tag_mode():
    mode, reason = resolve_plan_mode("epub", "openai", False, None)
    assert mode == "none"
    assert reason


# --------------------------------------------------- the fallback, in-loader


class FakeModel:
    """Minimal translator satisfying EPUBBookLoader's expectations."""

    TRANSLATION_ERROR_MARKER = None
    _fatal_error_detected = False

    def __init__(self, key, language, **kwargs):
        pass

    def translate(self, text, needprint=True):
        return f"T[{text}]"

    def translate_list(self, text_list):
        return [f"T[{t}]" for t in text_list]


def _auto_loader(tmp_path):
    from book_maker.loader.epub_loader import EPUBBookLoader

    src = tmp_path / BOOK.name
    shutil.copy(BOOK, src)
    loader = EPUBBookLoader(
        str(src), FakeModel, "dummy-key", resume=False, language="zh-hans"
    )
    # what the CLI sets when `auto` chose the plan
    loader.plan_mode = True
    loader.plan_auto = True
    loader.plan_fallback_tags = "p"
    loader.translate_tags = "auto"
    loader.plan_classify = "model"
    loader.only_filelist = "index_split_004.html"
    loader.is_test = True
    loader.test_num = 1
    return loader, src


def _break_the_plan(loader):
    def boom():
        raise SystemExit(1)

    loader._prepare_translation_plan = boom


def test_a_failing_plan_under_auto_continues_in_tag_mode(tmp_path, capsys):
    loader, src = _auto_loader(tmp_path)
    _break_the_plan(loader)
    loader.make_bilingual_book()
    out = " ".join(capsys.readouterr().out.split())
    assert "plan mode skipped" in out
    assert not loader.plan_mode
    assert loader.translate_tags == "p"
    assert (src.parent / (src.stem + "_bilingual.epub")).exists()


def test_the_same_failure_is_fatal_when_the_plan_was_asked_for(tmp_path):
    loader, _ = _auto_loader(tmp_path)
    loader.plan_auto = False  # an explicit --plan-classify model
    _break_the_plan(loader)
    with pytest.raises(SystemExit):
        loader.make_bilingual_book()


def test_an_incompatible_flag_under_auto_skips_the_plan(tmp_path, capsys):
    # --sentence_mode is a plan-mode conflict. Chosen automatically, the plan
    # is what yields; asked for explicitly, the run stops.
    loader, _ = _auto_loader(tmp_path)
    loader.sentence_mode = True
    loader.make_bilingual_book()
    out = " ".join(capsys.readouterr().out.split())
    assert "plan mode skipped" in out
    assert "--sentence_mode" in out
    assert not loader.plan_mode


def test_an_incompatible_flag_still_stops_an_explicit_plan(tmp_path):
    loader, _ = _auto_loader(tmp_path)
    loader.plan_auto = False
    loader.sentence_mode = True
    with pytest.raises(SystemExit):
        loader.make_bilingual_book()


class ClassifyingModel(FakeModel):
    """A translator that can also answer the plan's questions — and counts
    how often it was asked, because the point of these tests is that it
    was not."""

    def __init__(self, key, language, **kwargs):
        super().__init__(key, language, **kwargs)
        ClassifyingModel.asked = 0

    asked = 0

    def supports_structured_json(self):
        return True

    def structured_json(self, prompt, schema, model=None, accept=None):
        ClassifyingModel.asked += 1
        return {
            k: {"verdict": "translate", "content_type": "prose"}
            for k in schema["schema"]["required"]
        }


def _tag_mode_run(tmp_path):
    """A finished tag-mode run, leaving its checkpoint next to the book."""
    from book_maker.loader.epub_loader import EPUBBookLoader

    src = tmp_path / BOOK.name
    shutil.copy(BOOK, src)
    loader = EPUBBookLoader(
        str(src), FakeModel, "dummy-key", resume=False, language="zh-hans"
    )
    loader.only_filelist = "index_split_004.html"
    loader.is_test = True
    loader.test_num = 2
    loader.make_bilingual_book()
    cache = src.parent / f".{src.stem}.temp.bin"
    assert cache.exists(), "setup did not leave a tag-mode checkpoint"
    return src, cache


def _resumed_auto_loader(src, model_cls=ClassifyingModel):
    from book_maker.loader.epub_loader import EPUBBookLoader

    loader = EPUBBookLoader(
        str(src), model_cls, "dummy-key", resume=True, language="zh-hans"
    )
    loader.plan_mode = True
    loader.plan_auto = True
    loader.plan_fallback_tags = "p"
    loader.translate_tags = "auto"
    loader.plan_classify = "model"
    loader.only_filelist = "index_split_004.html"
    loader.is_test = True
    loader.test_num = 2
    return loader


def test_auto_resuming_a_tag_mode_cache_never_pays_for_a_plan(tmp_path, capsys):
    # the plan would refuse this cache anyway, but only after classifying the
    # whole book and writing a plan JSON. Under auto the answer is known
    # before anything is spent.
    src, _cache = _tag_mode_run(tmp_path)
    capsys.readouterr()

    loader = _resumed_auto_loader(src)
    loader.make_bilingual_book()
    out = " ".join(capsys.readouterr().out.split())

    assert "plan mode skipped (resuming a tag-mode run)" in out
    assert ClassifyingModel.asked == 0
    assert not (src.parent / (src.stem + "_plan.json")).exists()
    assert not loader.plan_mode
    assert loader.translate_tags == "p"
    assert (src.parent / (src.stem + "_bilingual.epub")).exists()


def test_auto_resuming_a_plan_mode_cache_still_plans(tmp_path, capsys):
    # the guard is about *tag-mode* caches; a plan-mode checkpoint carries a
    # fingerprint and is exactly what plan mode knows how to resume
    from book_maker.loader.epub_loader import EPUBBookLoader

    src = tmp_path / BOOK.name
    shutil.copy(BOOK, src)
    first = EPUBBookLoader(
        str(src), ClassifyingModel, "dummy-key", resume=False, language="zh-hans"
    )
    first.plan_mode = True
    first.plan_auto = True
    first.translate_tags = "auto"
    first.plan_classify = "model"
    first.only_filelist = "index_split_004.html"
    first.is_test = True
    first.test_num = 2
    first.make_bilingual_book()
    assert first._resume_plan_fingerprint is None and first._plan_fingerprint
    capsys.readouterr()

    resumed = _resumed_auto_loader(src)
    assert resumed._resume_plan_fingerprint is not None
    resumed.make_bilingual_book()
    out = " ".join(capsys.readouterr().out.split())

    assert "plan mode skipped" not in out
    assert resumed.plan_mode
    assert (src.parent / (src.stem + "_plan.json")).exists()


# ------------------------------------------------------------ the whole CLI


def _env(**extra):
    env = dict(os.environ)
    for name in KEY_ENV_VARS:
        env.pop(name, None)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(HERMETIC), str(REPO), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    env.update(extra)
    return env


def _cli(tmp_path, *args, **envextra):
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = subprocess.run(
        [
            sys.executable,
            "make_book.py",
            "--book_name",
            str(src),
            "--key",
            "fake-key",
            *args,
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        env=_env(**envextra),
    )
    return proc, src.parent / (src.stem + "_plan.json")


def test_a_bare_command_on_a_verified_endpoint_plans(tmp_path):
    proc, plan = _cli(tmp_path, "--test", "--test_num", "1", BBM_FAKE_PROBE="strict")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = " ".join(proc.stdout.split())
    assert "plan mode: on" in out
    assert plan.exists()


def test_a_bare_command_on_an_unverified_endpoint_uses_tags(tmp_path):
    proc, plan = _cli(tmp_path, "--test", "--test_num", "1", BBM_FAKE_PROBE="json")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = " ".join(proc.stdout.split())
    assert "plan mode: off" in out
    assert not plan.exists()


def test_explicit_none_says_nothing_and_plans_nothing(tmp_path):
    proc, plan = _cli(
        tmp_path,
        "--plan-classify",
        "none",
        "--test",
        "--test_num",
        "1",
        BBM_FAKE_PROBE="strict",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "plan mode:" not in proc.stdout
    assert "probe asked" not in proc.stdout
    assert not plan.exists()


def test_agent_mode_is_untouched_and_unprobed(tmp_path):
    from book_maker.loader.classify import PLAN_HANDOFF_EXIT_CODE

    proc, plan = _cli(tmp_path, "--plan-classify", "agent", BBM_FAKE_PROBE="strict")
    # the handoff has its own exit code: 0 is what a finished translation
    # returns, and a caller could not tell the two apart
    assert proc.returncode == PLAN_HANDOFF_EXIT_CODE, proc.stdout + proc.stderr
    assert "plan mode:" not in proc.stdout
    assert "probe asked" not in proc.stdout
    assert plan.exists()


def test_a_tag_selection_keeps_tag_mode_on_a_verified_endpoint(tmp_path):
    proc, plan = _cli(
        tmp_path,
        "--translate-tags",
        "p",
        "--test",
        "--test_num",
        "1",
        BBM_FAKE_PROBE="strict",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = " ".join(proc.stdout.split())
    assert "plan mode: off" in out
    assert "--translate-tags" in out
    assert not plan.exists()


def test_a_dry_run_asks_no_endpoint_anything(tmp_path):
    proc, plan = _cli(tmp_path, "--plan-dry-run", BBM_FAKE_PROBE="strict")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert plan.exists()
    assert "probe asked" not in proc.stdout
    assert "plan mode:" not in proc.stdout
    # the dry run decides nothing, so the plan it writes is all questions
    rows = json.loads(plan.read_text())["signatures"]
    assert all(row["action"] is None for row in rows)
