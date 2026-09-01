from book_maker.cli import get_book_type, main


def test_get_book_type_uses_final_suffix_and_lowercases():
    assert get_book_type("/tmp/books/source.v1.README.MD") == "md"


import json
import os

import pytest
import subprocess
import sys
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parent.parent
BOOK = REPO / "test_books" / "animal_farm.epub"
# tests/hermetic/sitecustomize.py swaps the `google` translator for an
# offline one at interpreter startup. These are CLI *contract* tests — flag
# wiring, mode selection, what gets written — and routing them through a
# public translation endpoint made them fail on proxy errors and impossible
# to run offline. Live provider calls belong to tests/test_integration.py.
HERMETIC = Path(__file__).resolve().parent / "hermetic"


# Credentials the CLI falls back to. Left in place they would decide test
# outcomes from whatever the developer happens to have exported.
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


def _run(tmp_path, *args):
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli("--book_name", str(src), "--api_format", "google", *args)
    return proc, src.parent / (src.stem + "_plan.json")


def test_plan_classify_implies_plan_mode(tmp_path):
    # any classification choice is a choice to have a plan; no second flag
    # is needed to enter plan mode
    proc, plan = _run(tmp_path, "--plan-classify", "agent")
    assert proc.returncode == 0
    assert plan.exists()
    assert "Paste the block below" in proc.stdout


def test_no_classify_flag_keeps_legacy_tag_mode(tmp_path):
    # the flag is opt-in: without it nothing about today's behavior changes
    proc, plan = _run(tmp_path, "--test", "--test_num", "1")
    assert proc.returncode == 0
    assert not plan.exists()


def test_explicit_none_is_the_same_as_no_flag(tmp_path):
    # 'none' denotes the default: ordinary --translate-tags selection, no plan
    proc, plan = _run(tmp_path, "--plan-classify", "none", "--test", "--test_num", "1")
    assert proc.returncode == 0
    assert not plan.exists()


def test_most_mode_translates_without_asking_or_writing_a_plan(tmp_path):
    # 'most' is the deliberate translate-everything entry: no questions, so
    # no plan file to answer them in, and no agent stop
    proc, plan = _run(tmp_path, "--plan-classify", "most", "--test", "--test_num", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not plan.exists()
    assert "Paste the block below" not in proc.stdout


def test_most_mode_ignores_an_existing_plan(tmp_path):
    # half-loading an earlier run's skips would make "most" quietly mean
    # "most, except whatever something else decided"
    proc, plan = _run(tmp_path, "--plan-classify", "agent")
    assert plan.exists()
    proc, _ = _run(tmp_path, "--plan-classify", "most", "--test", "--test_num", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ignores the existing plan" in " ".join(proc.stdout.split())


def test_explicit_tag_list_loses_to_the_classify_flag(tmp_path):
    proc, plan = _run(tmp_path, "--plan-classify", "agent", "--translate-tags", "div,p")
    assert proc.returncode == 0
    assert plan.exists()
    # rich wraps long lines at terminal width, so compare wrap-insensitively
    assert "ignoring --translate-tags div,p" in " ".join(proc.stdout.split())


def test_translate_tags_auto_is_an_ordinary_tag(tmp_path):
    # review finding: the loader used to key plan mode off the literal tag
    # string, so `--translate-tags auto` was an undocumented backdoor into
    # plan mode. It is now just a tag name that matches nothing.
    proc, plan = _run(tmp_path, "--translate-tags", "auto", "--test", "--test_num", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not plan.exists()
    assert "Translation plan" not in proc.stdout


def test_default_tags_are_overridden_quietly(tmp_path):
    # the untouched default "p" is not a selection worth a warning
    proc, plan = _run(tmp_path, "--plan-classify", "agent")
    assert proc.returncode == 0
    assert plan.exists()
    assert "ignoring --translate-tags" not in proc.stdout


def test_plan_dry_run_writes_a_fresh_plan(tmp_path):
    # regression: the dry-run path kept a reference to the removed
    # --plan-no-classify option and crashed right after writing the plan
    proc, plan = _run(tmp_path, "--plan-dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert plan.exists()
    assert "plan written to" in proc.stdout


def test_a_corrupted_plan_fails_clean_not_with_a_traceback(tmp_path):
    # the plan JSON is the one file this workflow asks a person to hand-edit,
    # so its lint failure is the error a user is most likely to meet. All four
    # corruption classes already fail correctly (exit 1, before any API call,
    # accurate message) — this pins the *presentation*: the ledger's own
    # words, not an 18-line Python traceback.
    proc, plan = _run(tmp_path, "--plan-classify", "agent")
    assert plan.exists()
    data = json.loads(plan.read_text())
    data["signatures"][0]["action"] = "translate-everything"
    plan.write_text(json.dumps(data))
    proc, _ = _run(tmp_path, "--plan-classify", "agent")
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    flat = " ".join(proc.stdout.split())
    assert "invalid row" in flat
    assert "invalid action" in flat


def test_classify_flag_rejects_non_epub_books(tmp_path):
    # plan mode is epub-only, and agent mode promises to stop before
    # spending anything: silently translating a txt book instead would be
    # the opposite of what was asked
    src = tmp_path / "the_little_prince.txt"
    src.write_bytes((REPO / "test_books" / "the_little_prince.txt").read_bytes())
    proc = _cli(
        "--book_name", str(src), "--api_format", "google", "--plan-classify", "agent"
    )
    assert proc.returncode == 1
    assert "epub-only" in proc.stdout


def test_agent_mode_rejects_a_classifier_model(tmp_path):
    proc, _ = _run(
        tmp_path, "--plan-classify", "agent", "--plan-classify-model", "gpt-4o"
    )
    assert proc.returncode == 1
    assert "cannot be combined" in proc.stdout


def test_most_mode_rejects_a_classifier_model(tmp_path):
    # 'most' explicitly skips classification; naming a classifier alongside
    # it is a contradiction, not a preference to resolve silently
    proc, _ = _run(
        tmp_path, "--plan-classify", "most", "--plan-classify-model", "gpt-4o"
    )
    assert proc.returncode == 1
    assert "cannot be combined" in proc.stdout


def test_classify_model_flag_implies_model_mode(tmp_path):
    # naming a classifier is asking for model mode; it must not silently
    # sit in none mode doing nothing
    proc, plan = _run(
        tmp_path,
        "--plan-classify-model",
        "no-such-model",
        "--test",
        "--test_num",
        "1",
    )
    # google translator has no structured_json, and a classifier that cannot
    # run must block rather than degrade into translating undecided rows
    assert proc.returncode == 1
    assert "no structured-output support" in " ".join(proc.stdout.split())
    # and it must say what to do instead, not just what failed
    assert "--plan-classify agent" in " ".join(proc.stdout.split())


def test_naming_a_model_for_a_fixed_engine_fails_loud(tmp_path):
    # the machine-translation formats run one fixed engine; honoring a model
    # is impossible, so refuse rather than ignore it
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name", str(src), "--api_format", "google", "--model", "some-model"
    )
    assert proc.returncode == 1
    assert "--model" in proc.stdout
    assert "google" in proc.stdout


def test_naming_a_model_twice_fails_loud(tmp_path):
    # two answers to "which model is this run using" is one too many
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--key",
        "k",
        "--model",
        "a",
        "--model_list",
        "b",
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "once" in output


def test_the_openai_format_defaults_to_a_model(tmp_path):
    # a command with only a key used to die on "--model is required"; the
    # openai format has one obvious cheapest current model, so it just runs
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli("--book_name", str(src), "--key", "sk-test", "--test", "--test_num", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "offline model list: ['gpt-5.6-luna']" in proc.stdout


def test_an_old_key_flag_alone_lands_on_the_default_model(tmp_path):
    # the old parser defaulted to chatgptapi, so `--openai_key sk-...` named
    # no model; it now gets the format's default rather than a retired preset
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name", str(src), "--openai_key", "sk-test", "--test", "--test_num", "1"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "offline model list: ['gpt-5.6-luna']" in proc.stdout
    assert "gpt-3.5-turbo" not in proc.stdout


def test_the_anthropic_format_still_asks_for_a_model(tmp_path):
    # no id there is the obvious cheapest one, and guessing would bill a
    # whole book to a model nobody chose
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli("--book_name", str(src), "--key", "sk-test", "--api_format", "anthropic")
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "--model is required for the anthropic format" in " ".join(output.split())


def test_missing_key_names_where_it_looked(tmp_path):
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli("--book_name", str(src), "--model", "some-model")
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "--key" in output
    assert "BBM_API_KEY" in output


def test_anthropic_format_is_inferred_from_the_endpoint(tmp_path):
    # --api_format is not required when the host already says which shape it
    # speaks; the key error proves which route was selected
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--api_base",
        "https://api.anthropic.com",
        "--model",
        "claude-haiku-4-5-20251001",
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "anthropic endpoint" in output
    assert "ANTHROPIC_API_KEY" in output


def test_a_route_specific_key_outranks_a_generic_one(monkeypatch):
    # an old --model groq command implies BBM_GROQ_API_KEY; picking
    # OPENAI_API_KEY instead would send one vendor's credential to another
    from book_maker.cli import resolve_api_key

    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("BBM_GROQ_API_KEY", "groq-secret")

    key = resolve_api_key(
        "openai", "", "https://api.groq.com/openai/v1", ("BBM_GROQ_API_KEY",)
    )

    assert key == "groq-secret"


def test_a_local_endpoint_needs_no_key(tmp_path, monkeypatch):
    # ollama and friends authenticate nobody; requiring a key there was pure
    # ceremony (the old CLI had a dedicated --ollama_model flag for it)
    from book_maker.cli import resolve_api_key

    monkeypatch.delenv("BBM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("BBM_OPENAI_API_KEY", raising=False)

    assert resolve_api_key("openai", "", "http://localhost:11434/v1") == "local"


def test_quiet_flag_is_accepted(tmp_path):
    proc, plan = _run(tmp_path, "--plan-dry-run", "--quiet")
    assert proc.returncode == 0
    assert plan.exists()


def test_kobo_mode_does_not_require_book_name(tmp_path, monkeypatch):
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    fake_obok = ModuleType("book_maker.obok")
    fake_obok.cli_main = lambda device_path: str(src)
    monkeypatch.setitem(sys.modules, "book_maker.obok", fake_obok)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "make_book.py",
            "--book_from",
            "kobo",
            "--device_path",
            "/mounted/kobo",
            "--plan-dry-run",
        ],
    )

    main()

    assert (tmp_path / f"{src.stem}_plan.json").exists()


def test_prompt_json_accepts_a_style_field():
    from book_maker.cli import parse_prompt_arg

    prompt = parse_prompt_arg(
        json.dumps({"user": "translate {text}", "style": "plain modern prose"})
    )
    assert prompt["style"] == "plain modern prose"


def test_prompt_json_still_rejects_unknown_keys():
    from book_maker.cli import parse_prompt_arg

    with pytest.raises(ValueError):
        parse_prompt_arg(json.dumps({"user": "{text}", "nonsense": "x"}))


def test_style_reaches_the_translator_kwargs():
    from book_maker.utils import prompt_config_to_kwargs

    kwargs = prompt_config_to_kwargs({"user": "{text}", "style": "terse"})
    assert kwargs["style_note"] == "terse"


def test_no_style_is_none():
    from book_maker.utils import prompt_config_to_kwargs

    assert prompt_config_to_kwargs({"user": "{text}"})["style_note"] is None


def _cli_in(cwd, *args, **extra_env):
    """The CLI run from `cwd`, so a project bbm_providers.json is in scope."""
    env = _env()
    env["PYTHONPATH"] = os.pathsep.join([str(HERMETIC), str(REPO), env["PYTHONPATH"]])
    env.update(extra_env)
    return subprocess.run(
        [sys.executable, str(REPO / "make_book.py"), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
    )


def _provider_book(tmp_path, **entry):
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    (tmp_path / "bbm_providers.json").write_text(
        json.dumps({"providers": {"p": entry}}), encoding="utf-8"
    )
    return src


def test_a_provider_supplies_the_models_and_its_key_variable(tmp_path):
    # the entry's env_key is consulted for the key, and its default_models
    # rotate in order — no --api_base, --key or --model on the command
    src = _provider_book(
        tmp_path,
        api_style="openai",
        base_url="https://api.deepseek.example/v1",
        default_models=["model-one", "model-two"],
        env_key="BBM_TEST_PROVIDER_KEY",
    )
    proc = _cli_in(
        tmp_path,
        "--book_name",
        str(src),
        "--provider",
        "p",
        "--test",
        "--test_num",
        "1",
        BBM_TEST_PROVIDER_KEY="sk-provider",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "offline model list: ['model-one', 'model-two']" in proc.stdout


def test_a_provider_and_a_model_are_not_mutually_exclusive(tmp_path):
    # naming the gateway and the model at it is the ordinary case; the
    # command's own model wins over the entry's default_models
    src = _provider_book(
        tmp_path,
        api_style="openai",
        base_url="https://api.deepseek.example/v1",
        default_models=["model-one"],
    )
    proc = _cli_in(
        tmp_path,
        "--book_name",
        str(src),
        "--provider",
        "p",
        "--model",
        "my-model",
        "--key",
        "sk-test",
        "--test",
        "--test_num",
        "1",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "offline model list: ['my-model']" in proc.stdout


def test_an_unknown_provider_names_both_config_files(tmp_path):
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli_in(tmp_path, "--book_name", str(src), "--provider", "ghost")
    output = proc.stdout + proc.stderr
    assert proc.returncode != 0
    assert "ghost" in output
    assert "bbm_providers.json" in output
    assert ".bbm/providers.json" in output


def test_orcarouter_needs_no_endpoint_and_reads_its_own_key(tmp_path):
    # upstream's documented command, unchanged: the gateway's model id says
    # where the request goes, and BBM_ORCAROUTER_API_KEY carries the key
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli_in(
        tmp_path,
        "--book_name",
        str(src),
        "--model",
        "orcarouter",
        "--test",
        "--test_num",
        "1",
        BBM_ORCAROUTER_API_KEY="sk-orca",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "offline model list: ['orcarouter/auto']" in proc.stdout
    # a supported shortcut, not a legacy alias: nothing is deprecated here
    assert "deprecated" not in proc.stdout


def test_an_orcarouter_model_id_is_kept_as_named(tmp_path):
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli_in(
        tmp_path,
        "--book_name",
        str(src),
        "--model",
        "orcarouter/openai/gpt-5-mini",
        "--orcarouter_key",
        "sk-orca",
        "--test",
        "--test_num",
        "1",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "offline model list: ['orcarouter/openai/gpt-5-mini']" in proc.stdout
