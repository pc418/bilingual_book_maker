from book_maker.cli import get_book_type, main
from book_maker.loader.classify import PLAN_HANDOFF_EXIT_CODE


def test_get_book_type_uses_final_suffix_and_lowercases():
    assert get_book_type("/tmp/books/source.v1.README.MD") == "md"


import json
import os
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


def _env():
    env = dict(os.environ)
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
    proc = _cli("--book_name", str(src), "--model", "google", *args)
    return proc, src.parent / (src.stem + "_plan.json")


def test_plan_classify_implies_plan_mode(tmp_path):
    # any classification choice is a choice to have a plan; no second flag
    # is needed to enter plan mode
    proc, plan = _run(tmp_path, "--plan-classify", "agent")
    # the handoff is not a finished translation, and says so
    assert proc.returncode == PLAN_HANDOFF_EXIT_CODE
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
        "--book_name", str(src), "--model", "google", "--plan-classify", "agent"
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


def test_model_list_with_a_preset_model_fails_loud(tmp_path):
    # --model chatgptapi runs a hardcoded GPT-3.5 discovery and ignores
    # --model_list entirely; silently dropping the user's explicit model
    # choice cost a live run — refuse the combination instead
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--model",
        "chatgptapi",
        "--openai_key",
        "sk-test",
        "--model_list",
        "some-model",
    )
    assert proc.returncode == 1
    assert "--model_list" in proc.stdout
    assert "openai" in proc.stdout


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


def test_parallel_workers_is_refused_with_session_context(tmp_path):
    # every worker would carry a history of its own; that pairing has never
    # been tested, and a book-length run is the wrong place to find out
    proc, _ = _run(tmp_path, "--use_context", "session", "--parallel-workers", "4")
    assert proc.returncode == 1
    flat = " ".join(proc.stdout.split())
    assert "--parallel-workers" in flat
    assert "--use_context session" in flat
    assert not (tmp_path / f"{BOOK.stem}_bilingual.epub").exists()


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


def test_groq_model_list_does_not_use_openai_validation(monkeypatch):
    from book_maker.translator.chatgptapi_translator import ChatGPTAPI
    from book_maker.translator.groq_translator import GroqClient

    def fail_if_called(*args, **kwargs):
        raise AssertionError("OpenAI model validation must not run for Groq")

    monkeypatch.setattr(ChatGPTAPI, "_validate_custom_models", fail_if_called)
    client = object.__new__(GroqClient)
    client.set_model_list(["llama-3.3-70b-versatile"])

    assert client.model == "llama-3.3-70b-versatile"
    assert next(client.model_list) == "llama-3.3-70b-versatile"


def test_compact_budget_takes_zero_as_auto():
    """0 is the auto sentinel: size the budget from the model's own window."""
    from book_maker.cli import compact_budget

    assert compact_budget("0") == 0


def test_compact_budget_still_rejects_a_budget_too_small_to_use():
    import argparse

    import pytest

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


def test_model_list_on_codex_is_refused_before_the_sidecar_starts(tmp_path):
    # the refusal used to arrive after preflight had already booted a codex
    # sidecar twice — work for an answer the command line alone gives
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name", str(src), "--model", "codex", "--model_list", "gpt-5.6-luna"
    )
    assert proc.returncode == 1
    flat = " ".join(proc.stdout.split())
    assert "--model_list" in flat
    # the sidecar was never reached: preflight used to boot it twice and
    # print its login line before this refusal
    assert "Codex:" not in proc.stdout
    assert "Traceback" not in proc.stderr


def test_model_list_refusal_does_not_need_a_book(tmp_path):
    # nothing about this answer depends on the book, the key or the endpoint
    proc = _cli(
        "--book_name",
        "no-such-book.epub",
        "--model",
        "codex",
        "--model_list",
        "gpt-5.6-luna",
    )
    assert proc.returncode == 1
    assert "--model_list" in " ".join(proc.stdout.split())


def test_openai_without_a_model_list_fails_clean(tmp_path):
    # it used to raise ValueError: an 11-line traceback for a missing flag
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli("--book_name", str(src), "--model", "openai", "--openai_key", "sk-test")
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    assert "--model_list" in " ".join(proc.stdout.split())


def test_batch_is_refused_on_the_codex_route(tmp_path):
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


def test_batch_use_is_refused_on_the_codex_route(tmp_path):
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


def test_session_context_is_refused_on_a_route_that_has_none(tmp_path):
    # gemini keeps its own chat history and drops --context-compact-at and
    # --no-context-compact; the flag was accepted and silently meant window
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--model",
        "gemini",
        "--gemini_key",
        "k",
        "--use_context",
        "session",
    )
    assert proc.returncode == 1
    flat = " ".join(proc.stdout.split())
    assert "--use_context session" in flat
    assert "gemini" in flat


def test_bare_window_context_is_not_refused_anywhere(tmp_path):
    # window mode is what those routes do have; only session is refused
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--model",
        "gemini",
        "--gemini_key",
        "k",
        "--use_context",
        "--plan-dry-run",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_session_context_is_accepted_on_the_routes_that_implement_it():
    from book_maker.translator import MODEL_DICT

    assert MODEL_DICT["openai"].SUPPORTS_SESSION_CONTEXT
    assert MODEL_DICT["claude"].SUPPORTS_SESSION_CONTEXT
    assert MODEL_DICT["codex"].SUPPORTS_SESSION_CONTEXT
    # OpenAI-shaped gateways are the OpenAI translator with another address
    assert MODEL_DICT["xai"].SUPPORTS_SESSION_CONTEXT
    assert MODEL_DICT["orcarouter"].SUPPORTS_SESSION_CONTEXT
    assert not MODEL_DICT["gemini"].SUPPORTS_SESSION_CONTEXT


def test_an_auto_sized_compact_budget_is_refused_where_nothing_can_size_it(tmp_path):
    # 0 asks the route for the model's context window; a route with no
    # session history has nothing to ask, and 0 meant no budget at all
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--model",
        "gemini",
        "--gemini_key",
        "k",
        "--context-compact-at",
        "0",
    )
    assert proc.returncode == 1
    flat = " ".join(proc.stdout.split())
    assert "--context-compact-at 0" in flat


def test_an_auto_sized_budget_that_cannot_be_sized_fails_clean(tmp_path):
    # the openai format keeps `0` because a gateway can answer it; when the
    # endpoint cannot, the run says so and stops instead of quietly
    # compacting at a default the user did not ask for
    from book_maker.translator.chatgptapi_translator import (
        ChatGPTAPI,
        ContextWindowUnknown,
    )

    t = ChatGPTAPI.__new__(ChatGPTAPI)
    t.context_compact_at = 0
    t._model_windows = {}
    t._model_names = ()
    t.model = "some-model"
    t.openai_client = ModuleType("c")
    t.openai_client.models = ModuleType("m")
    t.openai_client.models.retrieve = lambda model: SimpleNamespace(id=model)
    with pytest.raises(ContextWindowUnknown) as stop:
        t.preflight()
    assert "--context-compact-at 0" in str(stop.value)
    assert getattr(
        stop.value, "user_facing", False
    ), "the CLI prints it, not a traceback"


def test_an_auto_sized_compact_budget_is_kept_on_the_session_routes(tmp_path):
    proc, _ = _run(
        tmp_path,
        "--use_context",
        "session",
        "--context-compact-at",
        "0",
        "--test",
        "--test_num",
        "1",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_named_compact_budget_is_never_refused(tmp_path):
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--model",
        "gemini",
        "--gemini_key",
        "k",
        "--context-compact-at",
        "3000",
        "--plan-dry-run",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_parallel_workers_with_context_is_refused_on_gemini(tmp_path):
    # Gemini.__init__ takes context_paragraph_limit and never stores it, so
    # the parallel banner read an attribute that was not there — an
    # AttributeError after the chapters had already been dispatched
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--model",
        "gemini",
        "--gemini_key",
        "k",
        "--use_context",
        "--parallel-workers",
        "2",
    )
    assert proc.returncode == 1
    flat = " ".join(proc.stdout.split())
    assert "--parallel-workers" in flat
    assert "--use_context" in flat


def test_parallel_workers_without_context_is_left_alone_on_gemini(tmp_path):
    # only the pairing is refused; parallel on its own is untouched
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--model",
        "gemini",
        "--gemini_key",
        "k",
        "--parallel-workers",
        "2",
        "--plan-dry-run",
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_the_routes_that_carry_chapter_context_are_not_refused():
    from book_maker.translator import MODEL_DICT

    assert MODEL_DICT["openai"].SUPPORTS_PARALLEL_CONTEXT
    assert MODEL_DICT["claude"].SUPPORTS_PARALLEL_CONTEXT
    assert MODEL_DICT["qwen"].SUPPORTS_PARALLEL_CONTEXT
    assert not MODEL_DICT["gemini"].SUPPORTS_PARALLEL_CONTEXT
    assert not MODEL_DICT["deepl"].SUPPORTS_PARALLEL_CONTEXT


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
    assert "{none,all,model,agent}" in " ".join(proc.stdout.split())
    assert "'most'" not in proc.stdout


def test_groq_declares_no_context_support():
    # GroqClient overrides create_chat_completion and builds its messages
    # from the prompt template alone, so create_context_messages() — and
    # every history it would carry — is never reached. Its window lookup
    # would also go through self.openai_client, which for Groq means a Groq
    # key sent to api.openai.com.
    from book_maker.translator.groq_translator import GroqClient
    from book_maker.translator.litellm_translator import liteLLM

    for cls in (GroqClient, liteLLM):
        assert not cls.SUPPORTS_SESSION_CONTEXT, cls.__name__
        assert not cls.SUPPORTS_PARALLEL_CONTEXT, cls.__name__


def test_session_context_is_refused_on_groq(tmp_path):
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--model",
        "groq",
        "--model_list",
        "llama3-8b-8192",
        "--use_context",
        "session",
    )
    assert proc.returncode == 1
    flat = " ".join(proc.stdout.split())
    assert "--use_context session" in flat
    assert "groq" in flat


def test_an_auto_sized_budget_on_groq_never_asks_openai(tmp_path):
    # the lookup would carry a Groq key to api.openai.com; the route-level
    # refusal lands before any client is built
    src = tmp_path / BOOK.name
    src.write_bytes(BOOK.read_bytes())
    proc = _cli(
        "--book_name",
        str(src),
        "--model",
        "groq",
        "--model_list",
        "llama3-8b-8192",
        "--context-compact-at",
        "0",
    )
    assert proc.returncode == 1
    assert "--context-compact-at 0" in " ".join(proc.stdout.split())
    assert "Traceback" not in proc.stderr


def test_a_misspelled_exclude_name_fails_loud_in_tag_mode_too(tmp_path):
    # the gate lived inside the plan build, so a tag-mode run translated and
    # paid for the document the user meant to skip
    proc, _ = _run(
        tmp_path,
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


def test_a_gateway_translator_keeps_the_session_it_was_asked_for():
    # the gateway subclasses used to drop every context argument on the
    # floor, and then declared the capability missing to match
    from book_maker.translator import MODEL_DICT

    for name in ("xai", "orcarouter"):
        t = MODEL_DICT[name]("k", "zh-hans", context_flag=True, context_mode="session")
        assert t.session is not None, name
        assert t.api_base and t.openai_client.base_url is not None


def test_ignore_cache_guard_reaches_the_translator_and_silences_it(capsys):
    from types import SimpleNamespace
    from book_maker.translator import MODEL_DICT

    t = MODEL_DICT["openai"](
        "k",
        "zh-hans",
        context_flag=True,
        context_mode="session",
        ignore_cache_guard=True,
    )
    uncached = SimpleNamespace(usage=SimpleNamespace(prompt_tokens_details=None))
    for _ in range(t.CACHE_WARN_AFTER + 2):
        t._note_cache_usage(uncached)
    assert "cached prompt token" not in capsys.readouterr().out
    t2 = MODEL_DICT["openai"]("k", "zh-hans", context_flag=True, context_mode="session")
    for _ in range(t2.CACHE_WARN_AFTER + 2):
        t2._note_cache_usage(uncached)
    assert "cached prompt token" in capsys.readouterr().out
