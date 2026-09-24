"""The flag-compatibility table: what is refused, what is warned about, and
— just as important — what a plain run still says nothing about.

The table (`book_maker.cli.COMPAT_RULES`) is one list of rows, so the tests
are one list of fixtures: every row must have a command that trips it and a
phrase the operator gets, and the last test here refuses to pass while any
row lacks one. The stops additionally run through the real CLI, because
their whole point is arriving before the run spends anything.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from book_maker.cli import (
    COMPAT_RULES,
    DRY_RUN_RULES,
    _route_can_session_classify,
    accumulated_tokens,
    build_parser,
    check_compatibility,
    coverage_fraction,
    infer_api_format,
    normalize_options,
    parse_args,
    poetry_group,
    preview_endpoint,
    resolve_classify_mode,
    resolve_plan_mode,
    run_facts,
)
from book_maker.translator import FORMAT_DICT

REPO = Path(__file__).resolve().parent.parent
BOOK = REPO / "test_books" / "animal_farm.epub"
TXT_BOOK = REPO / "test_books" / "the_little_prince.txt"
HERMETIC = Path(__file__).resolve().parent / "hermetic"
# --glossary is checked for existence by the parser, so a fixture that trips a
# glossary row has to point at a file that is really there.
GLOSSARY = Path(__file__).resolve().parent / "fixtures" / "glossary.txt"

KEY_ENV_VARS = (
    "BBM_API_KEY",
    "OPENAI_API_KEY",
    "BBM_OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "BBM_CLAUDE_API_KEY",
    "OPENAI_API_SYS_MSG",
)


def _env():
    env = dict(os.environ)
    for name in KEY_ENV_VARS:
        env.pop(name, None)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(HERMETIC), env.get("PYTHONPATH", "")]
    ).rstrip(os.pathsep)
    return env


def _cli(*args):
    return subprocess.run(
        [sys.executable, "make_book.py", *args],
        cwd=REPO,
        capture_output=True,
        text=True,
        env=_env(),
    )


def _book(tmp_path, source=BOOK):
    dst = tmp_path / source.name
    dst.write_bytes(source.read_bytes())
    return dst


def _flat(proc):
    return " ".join((proc.stdout + proc.stderr).split())


# --------------------------------------------------------------- the facts


def facts(argv, **resolved):
    """A resolved run, built exactly the way `main` builds one."""
    options = parse_args(list(argv))
    given = normalize_options(options)
    classify_mode, plan_auto = resolve_classify_mode(options)
    api_format = resolved.pop("api_format", "openai")
    return run_facts(
        options,
        given,
        book_type=resolved.pop("book_type", "epub"),
        api_format=api_format,
        translate_model=resolved.pop("translate_model", FORMAT_DICT[api_format]),
        model_names=resolved.pop("model_names", ["a-model"]),
        classify_mode=classify_mode,
        plan_auto=plan_auto,
        **resolved,
    )


def tripped(f, rules=COMPAT_RULES):
    return [rule.id for rule in rules if rule.when(f)]


# ------------------------------------------------------------------- stops


class TestStops:
    """Refusals: exit 1 before a book is parsed or a request is made."""

    def test_batch_is_broken_on_epub(self, tmp_path):
        # A1: queueing lives on a path the epub loader never takes, so the
        # book is translated live at full price and then an empty batch job
        # is submitted *instead of* writing the book. `groq` is the OpenAI
        # request path at another address, so it really does have the Batch
        # API the earlier refusal checks for.
        proc = _cli(
            "--book_name",
            str(_book(tmp_path)),
            "--api_format",
            "groq",
            "--key",
            "sk-test",
            "--model",
            "m",
            "--batch",
        )
        assert proc.returncode == 1
        assert "broken on epub" in _flat(proc)
        assert "empty batch job" in _flat(proc)

    def test_batch_use_is_refused_on_epub_too(self, tmp_path):
        # A1: the second half of the same dead path
        proc = _cli(
            "--book_name",
            str(_book(tmp_path)),
            "--api_format",
            "groq",
            "--key",
            "sk-test",
            "--model",
            "m",
            "--batch-use",
        )
        assert proc.returncode == 1
        assert "broken on epub" in _flat(proc)

    def test_parallel_accumulated_resume_records_nothing(self, tmp_path):
        # A4: the parallel accumulating path never records a translation
        # result, so the checkpoint --resume wants is never written
        proc = _cli(
            "--book_name",
            str(_book(tmp_path)),
            "--api_format",
            "google",
            "--parallel-workers",
            "2",
            "--accumulated_num",
            "2",
            "--resume",
        )
        assert proc.returncode == 1
        assert "records no progress at all" in _flat(proc)

    def test_to_epub_on_a_book_that_is_not_a_pdf(self, tmp_path):
        # A13: --to-epub changes which route the run takes — it reads a PDF
        # with OCR and translates the Markdown that comes back. On an epub
        # there is nothing to read, and ignoring the flag would leave the
        # operator waiting for a file that is never written.
        proc = _cli(
            "--book_name",
            str(_book(tmp_path)),
            "--api_format",
            "google",
            "--to-epub",
        )
        assert proc.returncode == 1
        assert "--to-epub is the PDF route" in _flat(proc)

    def test_an_image_base_without_its_model(self, tmp_path):
        # A14: --img-base-url says where --img-model is served; alone it
        # names nothing to ask there
        proc = _cli(
            "--book_name",
            str(_book(tmp_path)),
            "--api_format",
            "google",
            "--img-base-url",
            "http://127.0.0.1:9/v1",
        )
        assert proc.returncode == 1
        assert "no --img-model was given" in _flat(proc)

    def test_a_classify_base_without_its_model(self, tmp_path):
        # A15: the same for the classify endpoint
        proc = _cli(
            "--book_name",
            str(_book(tmp_path)),
            "--api_format",
            "google",
            "--classify-base-url",
            "http://127.0.0.1:9/v1",
        )
        assert proc.returncode == 1
        assert "no --classify-model was given" in _flat(proc)

    def test_agent_mode_with_a_classify_model_is_warned_about(self):
        # PIN (owner 260924): agent mode never pre-fills, so a classifier
        # named beside it is ignored, and C32 says so (lead 260924; it had
        # been exempt while the lead thought agent mode asked a session)
        f = facts(
            [
                "--book_name",
                "b.epub",
                "--plan-classify",
                "agent",
                "--classify-model",
                "m",
            ]
        )
        assert "C32" in tripped(f)

    def test_a10_reads_the_resolved_classifier(self):
        """PIN (lead ruling 260923, Codex finding 4): a fixed-engine run is
        stopped only when no classifier of its own was resolved (cli or
        provider), not by which raw flags were typed."""
        argv = ["--book_name", "b.epub", "--plan-classify", "model"]
        assert "A10" in tripped(facts(argv, api_format="google"))
        f = facts(argv, api_format="google", classifier_resolved=True)
        assert "A10" not in tripped(f)

    def test_a_provider_classifier_on_a_book_nothing_classifies(self, capsys):
        # C33, the provider half: an entry's classify_model on a plain md run
        # warns now (intended, lead ruling 260923)
        from book_maker.provider_loader import ProviderRoute

        f = facts(["--book_name", "b.md"], book_type="md")
        assert "C33" not in tripped(f)
        route = ProviderRoute("openai", "", ["m"], "")
        route.classify_model = "cls"
        f.options.provider_route = route
        assert "C33" in tripped(f)
        check_compatibility(f)
        assert (
            "the provider entry's classify_model is ignored on a md book"
            in " ".join(capsys.readouterr().out.split())
        )

    def test_to_epub_on_a_pdf_is_not_refused(self):
        # the same flag on the book it is for: no row fires, and the run is
        # diverted into the pipeline rather than stopped
        f = facts(["--book_name", "b.pdf", "--to-epub"], book_type="pdf")
        assert tripped(f) == []

    def test_pdf_ocr_beside_to_epub_is_not_warned_about(self):
        f = facts(["--book_name", "b.pdf", "--to-epub", "--pdf-ocr"], book_type="pdf")
        assert "C27" not in tripped(f)

    def test_pages_beside_to_epub_is_not_warned_about(self):
        f = facts(
            ["--book_name", "b.pdf", "--to-epub", "--pages", "6-7"], book_type="pdf"
        )
        assert "C28" not in tripped(f)

    def test_pages_without_to_epub_is_warned_about(self):
        f = facts(["--book_name", "b.pdf", "--pages", "6-7"], book_type="pdf")
        assert "C28" in tripped(f)

    def test_ocr_lang_beside_pdf_ocr_is_not_warned_about(self):
        f = facts(
            ["--book_name", "b.pdf", "--to-epub", "--pdf-ocr", "--ocr-lang", "ja"],
            book_type="pdf",
        )
        assert tripped(f) == []

    def test_ocr_lang_without_pdf_ocr_is_inert_even_on_the_route(self):
        f = facts(
            ["--book_name", "b.pdf", "--to-epub", "--ocr-lang", "ja"], book_type="pdf"
        )
        assert "C29" in tripped(f)

    def test_device_without_the_route_is_inert(self):
        # --device chooses where the extraction models run, and they only
        # run on the --to-epub route
        f = facts(["--book_name", "b.pdf", "--device", "cpu"], book_type="pdf")
        assert "C26" in tripped(f)

    def test_device_beside_to_epub_is_not_warned_about(self):
        # the quality tier on the CPU is a supported configuration, not a
        # degraded one, so naming the device on the route warns about
        # nothing (design 260921: --device never selects a parser)
        f = facts(
            ["--book_name", "b.pdf", "--to-epub", "--device", "cpu"],
            book_type="pdf",
        )
        assert tripped(f) == []

    def test_two_of_the_three_are_fine(self, tmp_path):
        # only the triple is refused: the pairs each work
        f = facts(
            [
                "--book_name",
                "b.epub",
                "--parallel-workers",
                "2",
                "--accumulated_num",
                "2",
            ]
        )
        assert "A4" not in tripped(f)

    def test_rotation_cannot_share_one_session(self, tmp_path):
        # A7: caching is per model, so every request would be a full-price
        # cache miss and one conversation would be written by two models
        proc = _cli(
            "--book_name",
            str(_book(tmp_path)),
            "--api_format",
            "openai",
            "--key",
            "sk-test",
            "--model_list",
            "gpt-a,gpt-b",
            "--use_context",
            "session",
        )
        assert proc.returncode == 1
        assert "rotates a different model into every request" in _flat(proc)

    def test_one_model_in_model_list_still_sessions(self):
        # nothing rotates, so nothing is refused
        f = facts(
            [
                "--book_name",
                "b.epub",
                "--model_list",
                "only-one",
                "--use_context",
                "session",
            ],
            model_names=["only-one"],
        )
        assert "A7" not in tripped(f)

    def test_model_classification_needs_a_model(self, tmp_path):
        # A10: the MT engines have no model to ask; the run used to parse the
        # whole book and write a plan file before dying in the classifier
        proc = _cli(
            "--book_name",
            str(_book(tmp_path)),
            "--api_format",
            "google",
            "--plan-classify",
            "model",
        )
        assert proc.returncode == 1
        assert "no model to ask" in _flat(proc)
        assert not (tmp_path / f"{BOOK.stem}_plan.json").exists()

    def test_retranslate_is_epub_only(self, tmp_path):
        # C9: only the epub loader implements it
        book = _book(tmp_path, TXT_BOOK)
        proc = _cli(
            "--book_name",
            str(book),
            "--api_format",
            "google",
            "--retranslate",
            str(book),
            "",
            "start",
            "end",
        )
        assert proc.returncode == 1
        assert "implemented by the epub loader only" in _flat(proc)

    def test_no_thinking_is_refused_on_codex(self, tmp_path):
        # A12: the route runs the codex CLI as a subprocess, so there is no
        # request body a reasoning control could be written into; accepting
        # the flag would translate a whole book as if it had been honoured
        proc = _cli(
            "--book_name",
            str(_book(tmp_path)),
            "--api_format",
            "codex",
            "--no-thinking",
        )
        assert proc.returncode == 1
        assert "no request to travel in on the codex route" in _flat(proc)

    def test_a_stop_silences_the_warnings(self, capsys):
        # a warning about a run that is not going to happen is noise in
        # front of the reason it isn't
        f = facts(
            [
                "--book_name",
                "b.epub",
                "--batch",
                "--batch_size",
                "4",
                "--only_filelist",
                "a.xhtml",
                "--exclude_filelist",
                "b.xhtml",
            ]
        )
        assert {"A1", "C3", "B10"} <= set(tripped(f))
        with pytest.raises(SystemExit) as stopped:
            check_compatibility(f)
        assert stopped.value.code == 1
        out = capsys.readouterr().out
        assert "broken on epub" in " ".join(out.split())
        assert "--batch_size is not read" not in " ".join(out.split())


# -------------------------------------------------------- argparse validation


class TestAccumulatedNumRange:
    """A12: 1 is the documented off switch; below it is a typo."""

    def test_zero_is_refused(self):
        with pytest.raises(Exception) as err:
            accumulated_tokens("0")
        assert "at least 1" in str(err.value)

    def test_negative_is_refused(self):
        with pytest.raises(Exception) as err:
            accumulated_tokens("-5")
        assert "at least 1" in str(err.value)

    def test_one_is_accepted(self):
        assert accumulated_tokens("1") == 1

    def test_the_parser_refuses_it_too(self, tmp_path):
        proc = _cli(
            "--book_name",
            str(_book(tmp_path)),
            "--accumulated_num",
            "0",
            "--plan-dry-run",
        )
        assert proc.returncode == 2
        assert "--accumulated_num" in proc.stderr


class TestPoetryGroupRange:
    """C13: 0 gave every short line its own request, silently."""

    def test_zero_is_refused(self):
        with pytest.raises(Exception) as err:
            poetry_group("0")
        assert "at least 1" in str(err.value)

    def test_one_is_accepted(self):
        assert poetry_group("1") == 1

    def test_the_parser_refuses_it_too(self, tmp_path):
        proc = _cli(
            "--book_name",
            str(_book(tmp_path)),
            "--poetry-group-size",
            "0",
            "--plan-dry-run",
        )
        assert proc.returncode == 2
        assert "--poetry-group-size" in proc.stderr


class TestCoverageRange:
    """A6: the gate fires after classification is paid for, so a value no
    plan can satisfy has to be caught at the parser."""

    def test_above_one_is_refused(self):
        with pytest.raises(Exception) as err:
            coverage_fraction("1.5")
        assert "between 0 and 1" in str(err.value)

    def test_a_percentage_is_refused_with_the_fix(self):
        with pytest.raises(Exception) as err:
            coverage_fraction("50")
        assert "50% is 0.5" in str(err.value)

    def test_negative_is_refused(self):
        with pytest.raises(Exception) as err:
            coverage_fraction("-0.1")
        assert "between 0 and 1" in str(err.value)

    def test_zero_is_legal_and_says_the_guard_is_off(self, capsys):
        assert coverage_fraction("0") == 0
        assert "coverage guard disabled" in " ".join(capsys.readouterr().out.split())

    def test_a_high_value_says_it_will_usually_abort(self, capsys):
        assert coverage_fraction("0.95") == 0.95
        out = " ".join(capsys.readouterr().out.split())
        assert "rarely cover more than ~90%" in out
        assert "after classification is paid for" in out

    def test_the_default_says_nothing(self, capsys):
        assert coverage_fraction("0.5") == 0.5
        assert capsys.readouterr().out == ""


# ------------------------------------------------------------------- warns
#
# One fixture per warn row: the command that trips it, and the phrase the
# operator reads. `test_every_warn_row_has_a_fixture` keeps the two lists in
# step, so a new row cannot be added without a demonstration of it firing.

WARN_FIXTURES = [
    (
        "A8",
        ["--use_context", "session", "--plan-classify", "none"],
        {},
        "outside plan mode leaves grouping off",
    ),
    (
        # the codex thread is a session nobody asked for, so the same
        # grouping-off cost lands there without --use_context
        "A8:codex",
        ["--api_format", "codex", "--plan-classify", "none"],
        {"api_format": "codex"},
        "one growing thread outside plan mode leaves grouping off",
    ),
    (
        "A9",
        ["--prompt", '{"system": "be terse", "user": "translate {text}"}'],
        {"env": {"OPENAI_API_SYS_MSG": "you are a translator"}},
        "$OPENAI_API_SYS_MSG is exported",
    ),
    (
        "A11",
        ["--api_format", "codex"],
        {"api_format": "codex"},
        "~17k tokens of its own preamble",
    ),
    (
        "B6",
        ["--api_format", "anthropic", "--model", "claude-sonnet-4-6"],
        {"api_format": "anthropic"},
        "does not plan automatically",
    ),
    (
        "B8",
        ["--sentence_mode", "--accumulated_num", "1200"],
        {},
        "--sentence_mode is ignored",
    ),
    (
        "B9",
        ["--block_size", "4", "--accumulated_num", "1200"],
        {},
        "--block_size is ignored",
    ),
    (
        "B10",
        ["--only_filelist", "a.xhtml", "--exclude_filelist", "b.xhtml"],
        {},
        "--exclude_filelist is ignored",
    ),
    (
        "B11",
        ["--context_paragraph_limit", "3", "--use_context", "session"],
        {},
        "--context_paragraph_limit",
    ),
    (
        "B12",
        ["--api_format", "codex", "--context-compact-at", "2000"],
        {"api_format": "codex", "book_type": "txt"},
        "--context-compact-at reach the translator",
    ),
    (
        "C1",
        ["--max-batch-units", "4", "--plan-classify", "none"],
        {},
        "nothing reads it outside plan mode",
    ),
    (
        "C2",
        ["--accumulated_num", "1200"],
        {"book_type": "txt"},
        "--accumulated_num is read by the epub loader only",
    ),
    (
        "C3",
        ["--batch_size", "4"],
        {},
        "--batch_size is not read by the epub loader",
    ),
    (
        "C4",
        ["--prompt", "translate {text}"],
        {"book_type": "srt"},
        "replaces the subtitle loader's own",
    ),
    (
        "C5",
        ["--use_context"],
        {"book_type": "txt"},
        "--use_context is not forwarded",
    ),
    (
        "C6",
        ["--parallel-workers", "2"],
        {"book_type": "txt"},
        "--parallel-workers is used by the epub and markdown loaders only",
    ),
    (
        "C7",
        ["--translate-tags", "p,div"],
        {"book_type": "txt"},
        "--translate-tags select markup inside an epub",
    ),
    (
        "C8",
        ["--translation_color", "red"],
        {"book_type": "txt"},
        "--translation_color style the translation",
    ),
    (
        "C10",
        ["--retranslate", "out.epub", "", "start", "end", "--test"],
        {},
        "--retranslate ignores --test",
    ),
    (
        "C11",
        ["--quiet"],
        {"book_type": "txt"},
        "--quiet is implemented by the epub loader only",
    ),
    (
        "C12",
        ["--api_format", "codex", "--api_base", "https://example.invalid/v1"],
        {"api_format": "codex"},
        "ignored on the codex route",
    ),
    (
        "C16",
        ["--api_format", "google", "--source_lang", "english"],
        {"api_format": "google"},
        "detects the source language itself",
    ),
    (
        "C18",
        ["--plan-min-coverage", "0.6", "--plan-classify", "none"],
        {},
        "this run translates the --translate-tags selection",
    ),
    (
        # C19: the MT engines take a string and give one back, and the other
        # LLM routes build their request elsewhere; either way the file is
        # read and then reaches nothing
        "C19",
        ["--api_format", "google", "--glossary", str(GLOSSARY)],
        {"api_format": "google"},
        "The google route does not",
    ),
    (
        # the alias is the same row, and the warning names the word typed
        "C19:terminology",
        ["--api_format", "anthropic", "--terminology", str(GLOSSARY)],
        {"api_format": "anthropic"},
        "--terminology is carried by",
    ),
    (
        # C25: the MT engines and the native vendor SDKs build their own
        # request; the flag is read, reaches nothing, and must say so
        "C25",
        ["--api_format", "google", "--no-thinking"],
        {"api_format": "google"},
        "reaches nothing and this run is unchanged",
    ),
    (
        # C20: the derived glossary comes out of a compact turn, and a
        # windowed run has none
        "C20",
        ["--glossary-auto", "on"],
        {},
        "keeps no session to compact",
    ),
    (
        # C21: txt, srt and pdf loaders forward no context at all
        "C21",
        ["--glossary", str(GLOSSARY)],
        {"book_type": "txt"},
        "forwarded by the epub and markdown loaders only",
    ),
    (
        # C22: only the epub format carries the record file
        "C22",
        ["--translation-metadata"],
        {"book_type": "txt"},
        "only the epub format carries one",
    ),
    (
        # C23: --no_disclosure silences the machine record too
        "C23",
        ["--translation-metadata", "--no_disclosure"],
        {},
        "--translation-metadata records nothing",
    ),
    (
        # C24: a session exists, but the route's handoff is never asked for
        # a renderings block — auto-learning has nothing to read
        "C24",
        ["--glossary-auto", "on", "--use_context", "session"],
        {"api_format": "anthropic"},
        "never asks its report",
    ),
    (
        # C26: the extraction models only run on the --to-epub route, so on
        # any other run there is no device for the flag to choose
        "C26",
        ["--device", "cpu"],
        {},
        "only run on the --to-epub route",
    ),
    (
        # C27: --pdf-ocr reads the pages with no text layer, and the route
        # only runs with --to-epub
        "C27",
        ["--pdf-ocr"],
        {},
        "only runs with --to-epub",
    ),
    (
        # C28: --pages selects what the PDF route reads, and the route only
        # runs with --to-epub; the legacy loader translates the whole file
        "C28",
        ["--pages", "6-7"],
        {},
        "translates the whole file",
    ),
    (
        # C29: --ocr-lang names what the OCR models read, and they only run
        # on the --to-epub route with --pdf-ocr
        "C29",
        ["--ocr-lang", "ch_sim,en"],
        {},
        "only run on the --to-epub route with --pdf-ocr",
    ),
    (
        # C30: --no-formula-images turns off the pictures the PDF route
        # keeps of display formulas, and that route needs --to-epub
        "C30",
        ["--no-formula-images"],
        {},
        "only runs with --to-epub",
    ),
    (
        # C31: the image model serves the PDF route's page-image steps, and
        # no other route has one (packet F, 260923)
        "C31",
        ["--img-model", "gpt-5.6-luna"],
        {},
        "only the PDF route (--to-epub on a PDF) has one",
    ),
    (
        "C31:key",
        ["--img-key", "k"],
        {},
        "does nothing with them",
    ),
    (
        # C32: a classifier named beside --plan-classify all, which asks
        # nothing
        "C32",
        ["--plan-classify", "all", "--classify-model", "gpt-5.6-luna"],
        {},
        "--classify-model names a classifier, and --plan-classify all",
    ),
    (
        # C32 for agent mode too (owner 260924: agent mode never pre-fills)
        "C32:agent",
        ["--plan-classify", "agent", "--classify-model", "gpt-5.6-luna"],
        {},
        "--plan-classify agent leaves every row to your agent and asks no model",
    ),
    (
        # C33: nothing but plan mode classifies yet (lead ruling 260923,
        # Codex finding 3)
        "C33",
        ["--classify-model", "gpt-5.6-luna"],
        {"book_type": "md"},
        "Nothing on this route classifies yet, so --classify-model is ignored "
        "on a md book.",
    ),
    (
        "C32:old-name",
        ["--plan-classify", "all", "--plan-classify-model", "gpt-5.6-luna"],
        {},
        "--plan-classify-model names a classifier",
    ),
]


@pytest.mark.parametrize(
    "row,argv,resolved,phrase",
    WARN_FIXTURES,
    ids=[fixture[0] for fixture in WARN_FIXTURES],
)
def test_a_warn_row_fires_and_says_why(
    row, argv, resolved, phrase, capsys, monkeypatch
):
    resolved = dict(resolved)
    for name, value in resolved.pop("env", {}).items():
        monkeypatch.setenv(name, value)
    f = facts(["--book_name", "b.epub", *argv], **resolved)

    # a row may need more than one demonstration (one route each, say); the
    # part before the colon is the row it belongs to
    assert _row_id(row) in tripped(f)
    check_compatibility(f)

    out = " ".join(capsys.readouterr().out.split())
    assert phrase in out
    assert "Warning:" in out


# ------------------------------------------------------------- the noise guard


class TestNoiseGuard:
    """A run that asked for nothing exotic hears nothing new. The table is
    only worth having while its warnings are rare enough to read."""

    def test_a_plain_epub_run_trips_nothing(self, capsys):
        f = facts(["--book_name", "b.epub", "--key", "sk-test"])
        assert tripped(f) == []
        check_compatibility(f)
        assert capsys.readouterr().out == ""

    def test_a_plain_txt_run_trips_nothing(self, capsys):
        f = facts(["--book_name", "b.txt", "--key", "sk-test"], book_type="txt")
        assert tripped(f) == []
        check_compatibility(f)
        assert capsys.readouterr().out == ""

    def test_the_usual_test_run_trips_nothing(self, capsys):
        f = facts(
            ["--book_name", "b.epub", "--key", "sk-test", "--test", "--test_num", "8"]
        )
        assert tripped(f) == []
        check_compatibility(f)
        assert capsys.readouterr().out == ""

    def test_a_plain_machine_translation_run_trips_nothing(self, capsys):
        f = facts(
            ["--book_name", "b.epub", "--api_format", "google"],
            api_format="google",
            model_names=[],
        )
        assert tripped(f) == []
        check_compatibility(f)
        assert capsys.readouterr().out == ""

    def test_a_default_run_prints_no_new_warning_through_the_cli(self, tmp_path):
        proc = _cli(
            "--book_name",
            str(_book(tmp_path)),
            "--api_format",
            "google",
            "--test",
            "--test_num",
            "1",
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "Warning:" not in proc.stdout


class TestTheLegacySystemVariableIsDeprecated:
    """A9 used to say `$OPENAI_API_SYS_MSG` outranked `--prompt`'s system
    section, which it did — a command asking for a system message translated
    a whole book under an exported variable instead. The flag wins now, so
    the row says which of the two is being used and that the variable is on
    its way out."""

    def test_a_prompt_with_a_system_key_is_told_the_variable_is_ignored(
        self, capsys, monkeypatch
    ):
        monkeypatch.setenv("OPENAI_API_SYS_MSG", "you are a translator")
        f = facts(
            [
                "--book_name",
                "b.epub",
                "--prompt",
                '{"system": "be terse", "user": "translate {text}"}',
            ]
        )

        assert "A9" in tripped(f)
        check_compatibility(f)
        said = " ".join(capsys.readouterr().out.split())
        assert "is exported and is ignored this run" in said

    def test_a_user_only_prompt_is_told_the_variable_is_what_it_is_using(
        self, capsys, monkeypatch
    ):
        monkeypatch.setenv("OPENAI_API_SYS_MSG", "you are a translator")
        f = facts(["--book_name", "b.epub", "--prompt", "translate {text}"])

        assert "A9" in tripped(f)
        check_compatibility(f)
        said = " ".join(capsys.readouterr().out.split())
        assert "The variable is deprecated" in said

    def test_nothing_is_said_when_it_is_not_exported(self, capsys, monkeypatch):
        monkeypatch.delenv("OPENAI_API_SYS_MSG", raising=False)
        f = facts(["--book_name", "b.epub", "--prompt", "translate {text}"])

        assert "A9" not in tripped(f)
        check_compatibility(f)
        assert capsys.readouterr().out == ""

    def test_reading_the_prompt_here_prints_nothing(self, capsys):
        # the run announces its prompt config once, from its own parse; this
        # pass asks the same question and must stay silent
        facts(["--book_name", "b.epub", "--prompt", "translate {text}"])
        tripped(facts(["--book_name", "b.epub", "--prompt", "translate {text}"]))
        assert "prompt config" not in capsys.readouterr().out


class TestTheCodexThreadIsASessionWithoutTheFlag:
    """A8 is about a growing history nothing groups against. The codex
    thread is one whether or not --use_context was typed (0b8e2ef), so the
    row has to reach it."""

    def test_grouping_off_on_the_codex_route_warns_without_the_flag(self, capsys):
        f = facts(
            [
                "--book_name",
                "b.epub",
                "--api_format",
                "codex",
                "--plan-classify",
                "none",
            ],
            api_format="codex",
        )

        assert "A8" in tripped(f)
        check_compatibility(f)
        out = " ".join(capsys.readouterr().out.split())
        assert "the codex route's one growing thread" in out
        assert "leaves grouping off" in out

    def test_a_codex_run_that_groups_is_not_warned(self, capsys):
        f = facts(
            [
                "--book_name",
                "b.epub",
                "--api_format",
                "codex",
                "--plan-classify",
                "none",
                "--accumulated_num",
                "1200",
            ],
            api_format="codex",
        )
        assert "A8" not in tripped(f)

    def test_the_cli_and_the_loader_name_the_same_sessions(self):
        # one attribute behind both answers: a route the loader bills as a
        # session and the table does not would be warned about wrongly, or
        # not at all
        from book_maker.cli import session_run_expected
        from book_maker.loader.epub_loader import EPUBBookLoader

        for api_format, translator in FORMAT_DICT.items():
            f = facts(
                ["--book_name", "b.epub", "--api_format", api_format],
                api_format=api_format,
                translate_model=translator,
            )
            loader = EPUBBookLoader.__new__(EPUBBookLoader)
            loader.context_mode = None
            loader.translate_model = translator
            assert session_run_expected(f) == loader._session_run, api_format


def _row_id(fixture_name):
    """The rule a fixture demonstrates. `A8:codex` demonstrates `A8`."""
    return fixture_name.split(":", 1)[0]


def test_every_warn_row_has_a_fixture():
    # a row nobody has seen fire is a row nobody knows the wording of
    covered = {_row_id(row) for row, *_ in WARN_FIXTURES}
    warns = {rule.id for rule in COMPAT_RULES if rule.level == "warn"}
    assert warns - covered == set()


def test_every_row_id_is_unique():
    ids = [rule.id for rule in COMPAT_RULES + DRY_RUN_RULES]
    assert len(ids) == len(set(ids))


# ------------------------------------------------------- the dry-run preview


class TestDryRunPreview:
    def test_the_preview_says_when_the_run_will_not_be_planned(self, tmp_path):
        # B2: --translate-tags turns plan mode off, and then the real run
        # translates that selection instead of this plan
        proc = _cli(
            "--book_name",
            str(_book(tmp_path)),
            "--plan-dry-run",
            "--translate-tags",
            "p,div",
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "each turn plan mode off" in _flat(proc)

    def test_the_preview_names_the_classify_and_image_endpoints(self, tmp_path):
        # packet F: the preview mirrors the run's two resolutions, without a
        # key; the run's own model classifies unless one is named, and the
        # image model is off unless one is named
        proc = _cli(
            "--book_name",
            str(_book(tmp_path)),
            "--plan-dry-run",
            "--api_format",
            "openai",
            "--model",
            "gpt-5.6-luna",
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        out = _flat(proc)
        assert (
            "Classifier: gpt-5.6-luna at the openai endpoint's default host (run)"
            in out
        )
        assert "Image model: off" in out
        proc = _cli(
            "--book_name",
            str(_book(tmp_path)),
            "--plan-dry-run",
            "--api_format",
            "openai",
            "--model",
            "gpt-5.6-luna",
            "--classify-model",
            "jev",
            "--img-model",
            "vision-m",
            "--img-base-url",
            "http://127.0.0.1:9/v1",
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        out = _flat(proc)
        assert "Classifier: jev-latest at https://api.typesafe.ai (cli)" in out
        assert "Image model: vision-m at http://127.0.0.1:9/v1 (cli)" in out

    def test_the_preview_says_its_request_count_is_a_floor(self, tmp_path):
        # B3: below strict decoding the run halves both the per-request unit
        # cap and the per-request token budget
        proc = _cli("--book_name", str(_book(tmp_path)), "--plan-dry-run")
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "more requests than these" in _flat(proc)

    def test_a_plain_openai_preview_is_not_told_the_plan_will_be_skipped(
        self, tmp_path
    ):
        proc = _cli(
            "--book_name",
            str(_book(tmp_path)),
            "--plan-dry-run",
            "--api_format",
            "openai",
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "each turn plan mode off" not in _flat(proc)

    def test_a_codex_preview_forecasts_a_session_classification_not_off(self, tmp_path):
        # B2 called every non-OpenAI route "plan mode off". The codex route
        # has planned on every run since the session classifier landed, so
        # the preview was forecasting a run nobody makes.
        proc = _cli(
            "--book_name",
            str(_book(tmp_path)),
            "--plan-dry-run",
            "--api_format",
            "codex",
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        out = _flat(proc)
        assert "each turn plan mode off" not in out
        assert "over a plain session" in out

    def test_a_route_that_cannot_hold_a_conversation_is_still_told_off(self, tmp_path):
        # anthropic has neither a JSON-schema verdict nor a classifier
        # session, so plan mode really is off there
        proc = _cli(
            "--book_name",
            str(_book(tmp_path)),
            "--plan-dry-run",
            "--api_format",
            "anthropic",
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "each turn plan mode off" in _flat(proc)

    def test_a_dry_run_infers_the_route_the_way_the_real_run_will(self, tmp_path):
        # The forecast used to read --api_format alone, and the real run's
        # inference happens after --plan-dry-run has already returned: a
        # command that names its route by model id previewed the openai
        # schema route, which is the one route it never takes.
        proc = _cli(
            "--book_name",
            str(_book(tmp_path)),
            "--plan-dry-run",
            "--model",
            "codex",
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        out = _flat(proc)
        assert "each turn plan mode off" not in out
        assert "the codex route has no JSON-schema verdict" in out
        assert "over a plain session" in out

    def test_an_inferred_anthropic_route_is_forecast_as_anthropic(self, tmp_path):
        # a claude-* id with no --api_base is what infer_api_format calls
        # anthropic, and anthropic is a route plan mode is off on
        options = parse_args(["--book_name", "b.epub", "--model", "claude-sonnet-4-6"])
        assert preview_endpoint(options)[0] == infer_api_format("", "claude-sonnet-4-6")
        assert preview_endpoint(options)[0] == "anthropic"

        # the host is the stronger signal, and it is read here too
        by_host = parse_args(
            ["--book_name", "b.epub", "--api_base", "https://api.anthropic.com"]
        )
        assert preview_endpoint(by_host)[0] == infer_api_format(
            "https://api.anthropic.com", ""
        )

        proc = _cli(
            "--book_name",
            str(_book(tmp_path)),
            "--plan-dry-run",
            "--model",
            "claude-sonnet-4-6",
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "each turn plan mode off" in _flat(proc)

    def test_an_explicit_api_format_still_outranks_the_model_id(self, tmp_path):
        # a claude id at an OpenAI-shaped gateway: the flag is the answer
        options = parse_args(
            [
                "--book_name",
                "b.epub",
                "--api_format",
                "openai",
                "--model",
                "claude-sonnet-4-6",
            ]
        )
        assert preview_endpoint(options)[0] == "openai"

        proc = _cli(
            "--book_name",
            str(_book(tmp_path)),
            "--plan-dry-run",
            "--api_format",
            "openai",
            "--model",
            "claude-sonnet-4-6",
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "each turn plan mode off" not in _flat(proc)

    def test_the_preview_derivation_does_not_rewrite_the_run_s_options(self):
        # it runs resolve_endpoint, which fills in --api_base and may take a
        # model from a provider entry; the preview must not keep any of that
        options = parse_args(["--book_name", "b.epub", "--model", "claude-sonnet-4-6"])
        before = vars(options).copy()
        preview_endpoint(options)
        assert vars(options) == before

    def test_an_unresolvable_route_leaves_the_preview_its_default(self):
        # naming a model twice is the run's refusal to make; a dry run
        # resolves no endpoint and must not start reporting one
        options = parse_args(
            ["--book_name", "b.epub", "--model", "a", "--model_list", "b,c"]
        )
        assert preview_endpoint(options)[0] == "openai"

    def test_the_dry_run_forecast_mirrors_resolve_plan_mode(self):
        # the parity rule: what the preview says and what the run derives are
        # the same branches, per route
        from book_maker.cli import dry_run_plan_divergence

        for api_format, translator in FORMAT_DICT.items():
            f = facts(
                ["--book_name", "b.epub", "--api_format", api_format],
                api_format=api_format,
                translate_model=translator,
            )
            mode, _reason = resolve_plan_mode(
                "epub",
                api_format,
                False,
                probe=(
                    (lambda: "strict")
                    if hasattr(translator, "_probe_verdict")
                    else None
                ),
                session=_route_can_session_classify(translator),
            )
            note = dry_run_plan_divergence(f) or ""
            assert ("each turn plan mode off" in note) == (mode == "none"), api_format

    def test_the_divergence_note_counts_the_openai_system_message(self, tmp_path):
        # B4: prompt_overhead_tokens counts $OPENAI_API_SYS_MSG like any
        # other system message, so a preview that ignores it groups wrong
        env = _env()
        env["OPENAI_API_SYS_MSG"] = "you are a translator"
        proc = subprocess.run(
            [
                sys.executable,
                "make_book.py",
                "--book_name",
                str(_book(tmp_path)),
                "--plan-dry-run",
                "--use_context",
                "session",
            ],
            cwd=REPO,
            capture_output=True,
            text=True,
            env=env,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        assert "assumes the stock prompt overhead" in _flat(proc)


# --------------------------------------------------- message and help fixes


def test_an_openai_shaped_route_is_not_told_it_has_no_verdict():
    # B5: groq, xai, litellm and gateways carry the same capability probe as
    # openai, and classify_plan asks it at run time — the JSON path is used
    # wherever it holds, so "no JSON-schema verdict" described another run
    mode, reason = resolve_plan_mode(
        "epub", "groq", False, probe=lambda: "strict", session=True
    )
    assert mode == "session"
    assert "no JSON-schema verdict" not in reason
    assert "probed at run time" in reason


def test_a_route_with_no_probe_still_says_it_has_no_verdict():
    # anthropic really has none; only the message for the probing routes moved
    mode, reason = resolve_plan_mode(
        "epub", "anthropic", False, probe=None, session=True
    )
    assert mode == "session"
    assert "no JSON-schema verdict" in reason


def test_block_size_no_longer_claims_it_needs_single_translate():
    # C14: nothing has enforced that for a long time, and the combined path
    # works without it
    help_text = next(
        action.help for action in build_parser()._actions if action.dest == "block_size"
    )
    assert "single_translate" not in help_text
    assert "--accumulated_num" in help_text
