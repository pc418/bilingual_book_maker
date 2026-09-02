from book_maker.cli import get_book_type, main
from book_maker.loader.classify import PLAN_HANDOFF_EXIT_CODE


def test_get_book_type_uses_final_suffix_and_lowercases():
    assert get_book_type("/tmp/books/source.v1.README.MD") == "md"


import json
import os

import pytest
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

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
    # the handoff is not a finished translation, and says so
    assert proc.returncode == PLAN_HANDOFF_EXIT_CODE
    assert plan.exists()
    assert "Paste the block below" in proc.stdout


def test_api_key_is_the_same_flag_as_key(tmp_path):
    # --api_key is a second spelling on the parser, not a legacy flag: the
    # run takes it and nothing is printed about it
    proc, _ = _run(tmp_path, "--api_key", "secret", "--test", "--test_num", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "deprecated" not in proc.stdout
    assert "--api_key" not in proc.stdout


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


def test_all_mode_translates_without_asking_or_writing_a_plan(tmp_path):
    # 'all' is the deliberate translate-everything entry: no questions, so
    # no plan file to answer them in, and no agent stop
    proc, plan = _run(tmp_path, "--plan-classify", "all", "--test", "--test_num", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not plan.exists()
    assert "Paste the block below" not in proc.stdout


def test_all_mode_ignores_an_existing_plan(tmp_path):
    # half-loading an earlier run's skips would make "all" quietly mean
    # "all, except whatever something else decided"
    proc, plan = _run(tmp_path, "--plan-classify", "agent")
    assert plan.exists()
    proc, _ = _run(tmp_path, "--plan-classify", "all", "--test", "--test_num", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ignores the existing plan" in " ".join(proc.stdout.split())


def test_most_is_the_old_name_of_all(tmp_path):
    # the mode translates the whole partition, and "all" is what that is.
    # Old command lines keep working and are corrected once, out loud.
    proc, plan = _run(tmp_path, "--plan-classify", "most", "--test", "--test_num", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "--plan-classify most is now --plan-classify all" in " ".join(
        proc.stdout.split()
    )
    assert not plan.exists()


def test_the_retired_name_is_not_advertised():
    proc = _cli("--help")
    text = " ".join(proc.stdout.split())
    assert "--plan-classify {auto,none,all,model,agent}" in text
    # the choices list is the whole advertisement; "most" is parsed, not shown
    assert "'most'" not in text
    assert "agent,most" not in text


def test_explicit_tag_list_loses_to_the_classify_flag(tmp_path):
    proc, plan = _run(tmp_path, "--plan-classify", "agent", "--translate-tags", "div,p")
    assert proc.returncode == PLAN_HANDOFF_EXIT_CODE
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
    assert proc.returncode == PLAN_HANDOFF_EXIT_CODE
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


def test_all_mode_rejects_a_classifier_model(tmp_path):
    # 'all' explicitly skips classification; naming a classifier alongside
    # it is a contradiction, not a preference to resolve silently
    proc, _ = _run(
        tmp_path, "--plan-classify", "all", "--plan-classify-model", "gpt-4o"
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
    proc = _cli(
        "--book_name", str(src), "--key", "sk-test", "--test", "--test_num", "1"
    )
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
    proc = _cli(
        "--book_name", str(src), "--key", "sk-test", "--api_format", "anthropic"
    )
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


def test_parallel_workers_is_refused_with_codex(tmp_path):
    # codex serializes every turn on one thread, and the parallel path then
    # reads a context attribute Codex does not have — an AttributeError that
    # only lands once the run is already under way. Refuse it up front
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli("--book_name", str(src), "--model", "codex", "--parallel-workers", "4")
    assert proc.returncode == 1
    flat = " ".join(proc.stdout.split())
    assert "--parallel-workers" in flat
    assert "codex" in flat
    # nothing may have been dispatched: no output book, no sidecar preflight
    assert not (tmp_path / f"{src.stem}_bilingual.epub").exists()
    assert "codex app-server" not in proc.stdout + proc.stderr


def test_parallel_workers_with_session_context_is_refused(tmp_path):
    # one history is the context; a worker cannot share it, and a fresh
    # history per chapter is window mode at session prices
    proc, _ = _run(
        tmp_path,
        "--use_context",
        "session",
        "--parallel-workers",
        "4",
        "--test",
        "--test_num",
        "1",
    )
    assert proc.returncode == 1
    flat = " ".join(proc.stdout.split())
    assert "--parallel-workers" in flat
    assert "--use_context session" in flat
    # refused before anything is dispatched
    assert not list(tmp_path.glob("*_bilingual.epub"))


def test_parallel_workers_still_runs_with_window_context(tmp_path):
    # only the two pairings are refused; the openai window route is untouched
    proc, _ = _run(
        tmp_path,
        "--use_context",
        "--parallel-workers",
        "4",
        "--test",
        "--test_num",
        "1",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "--parallel-workers" not in proc.stdout


def test_one_worker_is_never_refused(tmp_path):
    # 1 worker is what every run already does, in either pairing
    proc, _ = _run(
        tmp_path,
        "--use_context",
        "session",
        "--parallel-workers",
        "1",
        "--test",
        "--test_num",
        "1",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr

    # a dry run reaches no model at all, so the codex side of the boundary
    # can be checked without starting a sidecar
    proc = _cli(
        "--book_name",
        str(tmp_path / BOOK.name),
        "--model",
        "codex",
        "--parallel-workers",
        "1",
        "--plan-dry-run",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


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


def _options(**kwargs):
    """The flags `resolve_endpoint` reads, defaulting to "not passed"."""
    from types import SimpleNamespace

    return SimpleNamespace(
        model=kwargs.pop("model", ""),
        model_list=kwargs.pop("model_list", ""),
        api_base=kwargs.pop("api_base", ""),
        api_format=kwargs.pop("api_format", ""),
        provider=kwargs.pop("provider", ""),
        **kwargs,
    )


@pytest.fixture
def provider_entry(tmp_path, monkeypatch):
    """Write a `p` provider entry and run from a directory that sees it."""

    def write(**entry):
        (tmp_path / "bbm_providers.json").write_text(
            json.dumps({"providers": {"p": entry}}), encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)

    return write


class TestProviderPrecedence:
    """`--provider` is shorthand for flags, so it fills gaps and settles nothing.

    A model name that selects a route (`codex`, `orcarouter`) says where the
    request goes; a provider entry only says where it would go otherwise.
    """

    def test_a_provider_alone_supplies_format_base_and_models(self, provider_entry):
        from book_maker.cli import resolve_endpoint

        provider_entry(
            api_style="openai",
            base_url="https://api.provider.example/v1",
            default_models=["model-one", "model-two"],
            env_key="BBM_TEST_PROVIDER_KEY",
        )
        options = _options(provider="p")
        models, api_format, env_keys = resolve_endpoint(options)

        assert models == ["model-one", "model-two"]
        assert api_format == "openai"
        assert options.api_base == "https://api.provider.example/v1"
        assert env_keys == ("BBM_TEST_PROVIDER_KEY",)

    def test_a_priced_entry_puts_a_price_table_on_the_options(self, provider_entry):
        from book_maker.cli import resolve_endpoint

        provider_entry(
            api_style="openai",
            base_url="https://api.provider.example/v1",
            default_models=["model-one"],
            prices={"model-one": {"input": 1, "output": 2}},
            currency="EUR",
        )
        options = _options(provider="p")
        resolve_endpoint(options)
        assert options.price_table.price_for("model-one") == {"input": 1, "output": 2}
        assert options.price_table.currency == "EUR"

    def test_an_unpriced_entry_leaves_the_meter_on_tokens(self, provider_entry):
        from book_maker.cli import resolve_endpoint

        provider_entry(api_style="openai", base_url="https://api.provider.example/v1")
        options = _options(provider="p", model="m")
        resolve_endpoint(options)
        assert options.price_table is None

    def test_a_model_at_a_provider_keeps_the_providers_endpoint(self, provider_entry):
        from book_maker.cli import resolve_endpoint

        provider_entry(
            api_style="openai",
            base_url="https://api.provider.example/v1",
            default_models=["model-one"],
        )
        options = _options(provider="p", model="my-model")
        models, api_format, _ = resolve_endpoint(options)

        assert models == ["my-model"]
        assert api_format == "openai"
        assert options.api_base == "https://api.provider.example/v1"

    def test_model_codex_selects_the_sidecar_at_a_provider(self, provider_entry):
        # `codex` is not a model id at the provider's endpoint: it names the
        # sidecar, so the entry's api_style must not route it to the gateway
        from book_maker.cli import resolve_endpoint

        provider_entry(
            api_style="openai",
            base_url="https://api.provider.example/v1",
            default_models=["model-one"],
        )
        options = _options(provider="p", model="codex")
        models, api_format, _ = resolve_endpoint(options)

        assert api_format == "codex"
        assert models == ["codex"]

    def test_model_orcarouter_keeps_its_own_gateway_at_a_provider(self, provider_entry):
        # the OrcaRouter key would otherwise be sent to the provider's base
        from book_maker.cli import resolve_endpoint
        from book_maker.translator import orcarouter_translator as orcarouter

        provider_entry(
            api_style="openai",
            base_url="https://api.provider.example/v1",
            env_key="BBM_TEST_PROVIDER_KEY",
        )
        options = _options(provider="p", model="orcarouter")
        models, api_format, env_keys = resolve_endpoint(options)

        assert models == ["orcarouter/auto"]
        assert options.api_base == orcarouter.API_BASE
        assert api_format == "openai"
        assert env_keys[0] == orcarouter.ENV_KEY

    def test_an_explicit_api_base_still_outranks_the_orcarouter_shortcut(self):
        from book_maker.cli import resolve_endpoint

        options = _options(model="orcarouter/openai/gpt-5", api_base="https://mine/v1")
        models, _, _ = resolve_endpoint(options)

        assert models == ["orcarouter/openai/gpt-5"]
        assert options.api_base == "https://mine/v1"

    def test_an_explicit_api_format_still_outranks_a_route_selecting_model(self):
        from book_maker.cli import resolve_endpoint

        options = _options(model="codex", api_format="openai")
        _, api_format, _ = resolve_endpoint(options)

        assert api_format == "openai"


class TestOrcaRouterModelList:
    """A rotation list at the gateway is a list of gateway model ids."""

    def test_every_entry_is_redirected_once_the_list_selects_orcarouter(self):
        # only the first entry used to be redirected, so the ordered dedupe
        # downstream saw `orcarouter/auto` and `orcarouter` as two models and
        # the endpoint's model check could reject the bare alias
        from book_maker.cli import resolve_endpoint

        options = _options(model_list="orcarouter,orcarouter")
        models, _, _ = resolve_endpoint(options)

        assert models == ["orcarouter/auto", "orcarouter/auto"]

    def test_ids_already_naming_the_gateway_are_left_as_written(self):
        from book_maker.cli import resolve_endpoint
        from book_maker.translator import orcarouter_translator as orcarouter

        options = _options(model_list="orcarouter,orcarouter/openai/gpt-5-mini")
        models, _, _ = resolve_endpoint(options)

        assert models == ["orcarouter/auto", "orcarouter/openai/gpt-5-mini"]
        assert options.api_base == orcarouter.API_BASE

    def test_a_list_that_does_not_start_at_the_gateway_is_untouched(self):
        from book_maker.cli import resolve_endpoint

        options = _options(model_list="gpt-5-mini,orcarouter")
        models, _, _ = resolve_endpoint(options)

        assert models == ["gpt-5-mini", "orcarouter"]
        assert options.api_base == ""


# --------------------------------------------------------------------------
# The flag audit's fixes (PR #553), on this fork's endpoint surface: the
# route is --api_format / --model <id verbatim>, so a check the branch wrote
# against --model gemini is written here against the format it names.
# --------------------------------------------------------------------------


def test_compact_budget_still_rejects_a_budget_too_small_to_use():
    import argparse

    from book_maker.cli import compact_budget

    with pytest.raises(argparse.ArgumentTypeError):
        compact_budget("499")


def test_a_promptdown_file_with_no_user_message_fails_clearly(tmp_path):
    # the repo's own prompt_md.prompt.md is written in promptdown's table
    # form, which the pinned promptdown does not parse. It used to fall
    # through to `prompt["user"]` and die with a KeyError traceback, after
    # the user had already been told the file loaded
    from book_maker.cli import parse_prompt_arg

    md = tmp_path / "style.prompt.md"
    md.write_text(
        "# Prompt\n\n## Conversation\n\n"
        "| Role | Content |\n|---|---|\n| User | Translate {text} |\n"
    )
    with pytest.raises(ValueError) as excinfo:
        parse_prompt_arg(str(md))
    message = str(excinfo.value)
    assert str(md) in message
    assert "**User:**" in message


def test_a_promptdown_file_in_block_form_still_loads(tmp_path):
    from book_maker.cli import parse_prompt_arg

    md = tmp_path / "style.prompt.md"
    md.write_text(
        "# Prompt\n\n## Developer Message\n\nBe faithful.\n\n"
        "## Conversation\n\n**User:**\nTranslate {text} into {language}\n"
    )
    prompt = parse_prompt_arg(str(md))
    assert "{text}" in prompt["user"]
    assert prompt["system"] == "Be faithful."


def test_an_empty_exclude_translate_tags_excludes_nothing(tmp_path):
    # the README documents --exclude-translate-tags "" as the way to
    # translate code and sup too; the empty string is falsy, so it used to
    # leave the sup,code default in place with no sign anything was ignored
    proc, plan = _run(
        tmp_path, "--plan-classify", "agent", "--exclude-translate-tags", ""
    )
    assert proc.returncode == PLAN_HANDOFF_EXIT_CODE, proc.stdout + proc.stderr
    assert json.loads(plan.read_text())["exclude_tags"] == []


def test_a_style_and_a_colour_together_say_the_colour_is_lost(tmp_path):
    # --translation_style is the whole declaration block and replaces the
    # colour; it used to win in silence
    proc, _ = _run(
        tmp_path,
        "--test",
        "--test_num",
        "1",
        "--translation_color",
        "red",
        "--translation_style",
        "font-style: italic;",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    flat = " ".join(proc.stdout.split())
    assert "--translation_color" in flat
    assert "ignored" in flat


def test_a_colour_on_its_own_is_not_warned_about(tmp_path):
    proc, _ = _run(tmp_path, "--test", "--test_num", "1", "--translation_color", "red")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "--translation_color" not in proc.stdout


def test_a_misspelled_exclude_filelist_name_fails_loud(tmp_path):
    # a typo here used to be silent: the chapter the user meant to skip was
    # translated and paid for. The only-list reached the coverage gate; the
    # exclude-list reached nothing at all
    proc, plan = _run(
        tmp_path, "--plan-classify", "agent", "--exclude_filelist", "titlepage.xhtm"
    )
    assert proc.returncode == 1
    flat = " ".join(proc.stdout.split())
    assert "--exclude_filelist" in flat
    assert "titlepage.xhtm" in flat
    assert not plan.exists()


def test_a_misspelled_filter_name_fails_the_dry_run_too(tmp_path):
    # Codex review 2 on #553: the dry run returned before the filter gate,
    # so it wrote a plan that did not honor the exclusion and exited 0
    proc, plan = _run(
        tmp_path, "--plan-dry-run", "--exclude_filelist", "titlepage.xhtm"
    )
    assert proc.returncode == 1
    flat = " ".join(proc.stdout.split())
    assert "--exclude_filelist" in flat and "titlepage.xhtm" in flat
    assert not plan.exists()


def test_a_misspelled_only_filelist_name_fails_loud(tmp_path):
    proc, plan = _run(
        tmp_path, "--plan-classify", "agent", "--only_filelist", "chpater1.xhtml"
    )
    assert proc.returncode == 1
    assert "--only_filelist" in " ".join(proc.stdout.split())
    assert not plan.exists()


def test_correctly_spelled_file_filters_still_plan(tmp_path):
    proc, plan = _run(
        tmp_path, "--plan-classify", "agent", "--exclude_filelist", "titlepage.xhtml"
    )
    assert proc.returncode == PLAN_HANDOFF_EXIT_CODE, proc.stdout + proc.stderr
    assert plan.exists()


def test_a_misspelled_exclude_name_fails_loud_in_tag_mode_too(tmp_path):
    # the gate lived inside the plan build, so a tag-mode run translated and
    # paid for the document the user meant to skip
    proc, _ = _run(
        tmp_path,
        "--plan-classify",
        "none",
        "--exclude_filelist",
        "titlepage.xhtm",
        "--test",
        "--test_num",
        "1",
    )
    assert proc.returncode == 1
    flat = " ".join(proc.stdout.split())
    assert "--exclude_filelist" in flat
    assert "titlepage.xhtm" in flat
    assert not (tmp_path / f"{BOOK.stem}_bilingual.epub").exists()


def test_the_file_filter_gate_runs_before_any_model_setup(tmp_path):
    # even in plan mode it ran after preflight, so a codex sidecar had
    # already booted and printed its login line before the typo was caught
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--model",
        "codex",
        "--plan-classify",
        "agent",
        "--exclude_filelist",
        "titlepage.xhtm",
    )
    assert proc.returncode == 1
    assert "--exclude_filelist" in " ".join(proc.stdout.split())
    assert "Codex:" not in proc.stdout


def test_batch_is_refused_on_the_codex_format(tmp_path):
    # codex has no Batch API; the run used to die partway through with
    # AttributeError: batch_init, after plan quota had already been spent
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli("--book_name", str(src), "--model", "codex", "--batch")
    assert proc.returncode == 1
    flat = " ".join(proc.stdout.split())
    assert "--batch" in flat
    assert "Codex:" not in proc.stdout
    assert "Traceback" not in proc.stderr


def test_batch_use_is_refused_on_the_codex_format(tmp_path):
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli("--book_name", str(src), "--model", "codex", "--batch-use")
    assert proc.returncode == 1
    assert "--batch" in " ".join(proc.stdout.split())


def test_a_resumed_codex_run_says_continuity_restarts(tmp_path):
    # the thread is the only context this route has and it dies with the
    # process; <book>_handoff.md is written but never read back
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli("--book_name", str(src), "--model", "codex", "--resume")
    assert "new thread" in " ".join(proc.stdout.split())


def test_a_codex_run_without_resume_says_nothing_about_threads(tmp_path):
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli("--book_name", str(src), "--model", "codex", "--batch")
    assert "new thread" not in proc.stdout


def test_session_context_is_refused_on_a_format_that_has_none(tmp_path):
    # a machine-translation format keeps no history at all; --use_context
    # session was accepted and silently meant window
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--api_format",
        "deepl",
        "--use_context",
        "session",
    )
    assert proc.returncode == 1
    flat = " ".join(proc.stdout.split())
    assert "--use_context session" in flat
    assert "deepl" in flat


def test_bare_window_context_is_not_refused_anywhere(tmp_path):
    # window mode is what those formats do have; only session is refused
    proc, _ = _run(tmp_path, "--use_context", "--test", "--test_num", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "--use_context session" not in proc.stdout


def test_session_context_is_accepted_on_the_formats_that_implement_it():
    from book_maker.translator import FORMAT_DICT

    assert FORMAT_DICT["openai"].SUPPORTS_SESSION_CONTEXT
    assert FORMAT_DICT["anthropic"].SUPPORTS_SESSION_CONTEXT
    assert FORMAT_DICT["codex"].SUPPORTS_SESSION_CONTEXT
    # (google is replaced by the hermetic stub, which declares both)
    assert not FORMAT_DICT["deepl"].SUPPORTS_SESSION_CONTEXT
    assert not FORMAT_DICT["caiyun"].SUPPORTS_SESSION_CONTEXT


def test_parallel_workers_with_context_is_refused_where_it_breaks(tmp_path):
    # a format that keeps no re-sendable window has nothing to clone, and
    # the run died reading a context attribute it never set — after the
    # chapters had already been dispatched
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--api_format",
        "deepl",
        "--use_context",
        "--parallel-workers",
        "2",
    )
    assert proc.returncode == 1
    flat = " ".join(proc.stdout.split())
    assert "--parallel-workers" in flat
    assert "--use_context" in flat


def test_parallel_workers_without_context_is_left_alone(tmp_path):
    # only the pairing is refused; parallel on its own is untouched
    proc, _ = _run(tmp_path, "--parallel-workers", "2", "--plan-dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_formats_that_carry_chapter_context_are_not_refused():
    from book_maker.translator import FORMAT_DICT

    assert FORMAT_DICT["openai"].SUPPORTS_PARALLEL_CONTEXT
    assert FORMAT_DICT["anthropic"].SUPPORTS_PARALLEL_CONTEXT
    assert not FORMAT_DICT["deepl"].SUPPORTS_PARALLEL_CONTEXT
    assert not FORMAT_DICT["caiyun"].SUPPORTS_PARALLEL_CONTEXT


def test_the_old_mode_name_still_works_and_says_it_moved(tmp_path):
    # scripts written before the rename keep running
    proc, plan = _run(tmp_path, "--plan-classify", "most", "--test", "--test_num", "1")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert not plan.exists()
    assert "--plan-classify most is now --plan-classify all" in " ".join(
        proc.stdout.split()
    )


def test_the_old_mode_name_is_not_advertised():
    proc = _cli("--help")
    assert "{auto,none,all,model,agent}" in " ".join(proc.stdout.split())
    assert "'most'" not in proc.stdout


def test_compact_budget_takes_zero_as_auto():
    """0 is the auto sentinel: size the budget from the model's own window."""
    from book_maker.cli import compact_budget

    assert compact_budget("0") == 0


def test_compact_budget_still_rejects_a_budget_too_small_to_use():
    import argparse

    from book_maker.cli import compact_budget

    with pytest.raises(argparse.ArgumentTypeError):
        compact_budget("499")


def test_an_auto_sized_compact_budget_is_refused_where_nothing_can_size_it(tmp_path):
    # 0 asks the route for the model's own context window; a route that has no
    # way to ask has nothing to size with, and 0 there meant no budget at all
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--api_format",
        "google",
        "--context-compact-at",
        "0",
    )
    assert proc.returncode == 1
    assert "--context-compact-at 0" in " ".join(proc.stdout.split())
    assert "Traceback" not in proc.stderr


def test_the_codex_route_is_refused_an_auto_sized_budget_too(tmp_path):
    # codex keeps a session history but cannot be asked for a window, and the
    # refusal has to land before the sidecar is started for nothing
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--api_format",
        "codex",
        "--context-compact-at",
        "0",
    )
    assert proc.returncode == 1
    assert "--context-compact-at 0" in " ".join(proc.stdout.split())


def test_a_named_compact_budget_is_never_refused(tmp_path):
    proc, _ = _run(tmp_path, "--context-compact-at", "3000", "--plan-dry-run")
    assert proc.returncode == 0, proc.stdout + proc.stderr
