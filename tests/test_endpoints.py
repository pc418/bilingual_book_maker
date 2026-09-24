"""`--img-*` / `--classify-*`: which endpoint each extra step is asked on.

Packet F (260923). Two chains, one table each:

    image     --img-model  ->  provider img_model       ->  off
    classify  --classify-model  ->  provider classify_model  ->  the run's own

PIN (owner, 260923 22:30, docs/260923-feat-PDF_CHOICES_EXECUTION.md "Owner
rulings 22:30"): the image model is always designated explicitly; the run's
own model is never used for images by fallback, even when it reads them.
"""

from types import SimpleNamespace

import pytest

from book_maker import endpoints
from book_maker.endpoints import (
    CLASSIFY_ENDPOINT_UNSUPPORTED,
    IMG_ENDPOINT_UNSUPPORTED,
    JEV_DEFAULT_BASE,
    JEV_DEFAULT_MODEL,
    EndpointChoice,
    build_translator,
    resolve_classify_endpoint,
    resolve_image_endpoint,
    run_choice,
)
from book_maker.provider_loader import ProviderRoute

RUN_BASE = "https://api.openai.com/v1"
KEY_VARS = (
    "BBM_API_KEY",
    "OPENAI_API_KEY",
    "BBM_OPENAI_API_KEY",
    "JEV_API_KEY",
    "TYPESAFE_API_KEY",
    "IMG_KEY_VAR",
)


@pytest.fixture(autouse=True)
def no_keys(monkeypatch):
    for name in KEY_VARS:
        monkeypatch.delenv(name, raising=False)


def _opts(**kw):
    base = dict(
        img_model=None,
        img_base_url=None,
        img_key=None,
        classify_model=None,
        classify_base_url=None,
        classify_key=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _run(api_format="openai", base=RUN_BASE, key="sk-run"):
    return run_choice("gpt-run", base, key, api_format)


def _provider(**kw):
    route = ProviderRoute("openai", RUN_BASE, ["gpt-run"], "OPENAI_API_KEY")
    for name, value in kw.items():
        setattr(route, name, value)
    return route


# ------------------------------------------------------------------ image


class TestTheImageChain:
    def test_the_flag_wins_over_the_provider(self):
        choice = resolve_image_endpoint(
            _opts(img_model="cli-vision"), _run(), _provider(img_model="prov-vision")
        )
        assert (choice.model, choice.source) == ("cli-vision", "cli")

    def test_the_provider_answers_when_the_flag_is_absent(self):
        choice = resolve_image_endpoint(
            _opts(), _run(), _provider(img_model="prov-vision")
        )
        assert (choice.model, choice.source) == ("prov-vision", "provider")
        # no address of its own: the run's endpoint, the run's key
        assert (choice.api_base, choice.key, choice.api_format) == (
            RUN_BASE,
            "sk-run",
            "openai",
        )

    def test_none_turns_the_provider_s_model_off(self):
        assert (
            resolve_image_endpoint(
                _opts(img_model="none"), _run(), _provider(img_model="prov-vision")
            )
            is None
        )

    def test_nothing_named_is_off_even_when_the_run_model_reads_images(self):
        # PIN 260923 22:30: never the run's model by fallback. A run whose
        # own translator would pass the image probe still gets no image
        # choice without a flag or a provider field.
        class Verified:
            def vision_verdict(self, model=None):
                return "verified"

        run = _run()
        assert Verified().vision_verdict() == "verified"
        assert resolve_image_endpoint(_opts(), run, None) is None
        assert resolve_image_endpoint(_opts(), run, _provider()) is None

    def test_a_base_without_a_model_is_refused(self):
        with pytest.raises(SystemExit, match="no --img-model was given"):
            resolve_image_endpoint(_opts(img_base_url="https://gw/v1"), _run(), None)

    def test_an_anthropic_address_is_refused_in_the_lead_s_words(self):
        with pytest.raises(SystemExit) as err:
            resolve_image_endpoint(
                _opts(img_model="claude-x", img_base_url="https://api.anthropic.com"),
                _run(),
                None,
            )
        assert str(err.value) == IMG_ENDPOINT_UNSUPPORTED.format(
            base="https://api.anthropic.com", api_format="anthropic"
        )

    def test_a_run_route_without_an_image_channel_is_refused(self):
        with pytest.raises(SystemExit, match="resolves to the anthropic format"):
            resolve_image_endpoint(
                _opts(img_model="claude-x"),
                _run(api_format="anthropic", base="https://api.anthropic.com"),
                None,
            )

    def test_jev_cannot_see_images(self):
        with pytest.raises(SystemExit, match="resolves to the jev format"):
            resolve_image_endpoint(_opts(img_model="jev"), _run(), None)


class TestTheKeyRule:
    def test_same_base_takes_the_run_s_key(self):
        choice = resolve_image_endpoint(
            _opts(img_model="v", img_base_url=RUN_BASE + "/"), _run(), None
        )
        assert choice.key == "sk-run"

    def test_another_base_reads_its_format_s_environment(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        choice = resolve_image_endpoint(
            _opts(img_model="v", img_base_url="https://gw.example/v1"), _run(), None
        )
        assert (choice.api_base, choice.key) == ("https://gw.example/v1", "sk-env")

    def test_another_base_with_no_key_anywhere_stops(self):
        with pytest.raises(SystemExit, match="No API key"):
            resolve_image_endpoint(
                _opts(img_model="v", img_base_url="https://gw.example/v1"),
                _run(),
                None,
            )

    def test_a_local_base_needs_no_key(self):
        choice = resolve_image_endpoint(
            _opts(img_model="v", img_base_url="http://localhost:11434/v1"),
            _run(),
            None,
        )
        assert choice.key == "local"

    def test_the_flag_key_wins(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
        choice = resolve_image_endpoint(
            _opts(img_model="v", img_base_url="https://gw.example/v1", img_key="sk-f"),
            _run(),
            None,
        )
        assert choice.key == "sk-f"

    def test_the_provider_s_key_variable_comes_before_the_rule(self, monkeypatch):
        monkeypatch.setenv("IMG_KEY_VAR", "sk-var")
        choice = resolve_image_endpoint(
            _opts(),
            _run(),
            _provider(img_model="v", img_env_key="IMG_KEY_VAR"),
        )
        assert choice.key == "sk-var"

    def test_a_dry_run_resolves_no_key(self):
        choice = resolve_image_endpoint(
            _opts(img_model="v", img_base_url="https://gw.example/v1"),
            _run(),
            None,
            with_key=False,
        )
        assert choice.key is None


# --------------------------------------------------------------- classify


class TestTheClassifyChain:
    def test_the_run_is_the_last_link(self):
        run = _run()
        assert resolve_classify_endpoint(_opts(), run, None) is run
        assert resolve_classify_endpoint(_opts(), run, _provider()) is run

    def test_the_provider_beats_the_run(self):
        choice = resolve_classify_endpoint(
            _opts(), _run(), _provider(classify_model="prov-clf")
        )
        assert (choice.model, choice.source, choice.api_base) == (
            "prov-clf",
            "provider",
            RUN_BASE,
        )

    def test_the_flag_beats_the_provider(self):
        choice = resolve_classify_endpoint(
            _opts(classify_model="cli-clf"),
            _run(),
            _provider(classify_model="prov-clf"),
        )
        assert (choice.model, choice.source) == ("cli-clf", "cli")

    def test_a_base_without_a_model_is_refused(self):
        with pytest.raises(SystemExit, match="no --classify-model was given"):
            resolve_classify_endpoint(
                _opts(classify_base_url="https://gw/v1"), _run(), None
            )

    def test_an_anthropic_address_is_refused_in_the_lead_s_words(self):
        with pytest.raises(SystemExit) as err:
            resolve_classify_endpoint(
                _opts(
                    classify_model="c", classify_base_url="https://api.anthropic.com"
                ),
                _run(),
                None,
            )
        assert str(err.value) == CLASSIFY_ENDPOINT_UNSUPPORTED.format(
            base="https://api.anthropic.com", api_format="anthropic"
        )

    def test_a_model_alone_keeps_the_run_s_route_whatever_it_is(self):
        # --plan-classify-model worked on every LLM route; its new name must
        # too. The classify model on an anthropic run is asked there.
        run = _run(api_format="anthropic", base="https://api.anthropic.com")
        choice = resolve_classify_endpoint(_opts(classify_model="c"), run, None)
        assert (choice.api_format, choice.api_base, choice.key) == (
            "anthropic",
            "https://api.anthropic.com",
            "sk-run",
        )

    @pytest.mark.parametrize("mode", ["agent", "all", "model", "auto"])
    def test_plan_classify_does_not_switch_the_chain_off(self, mode):
        # packet F (owner 260923 22:40): agent|all select the session backend
        # of whichever translator the chain picked; they are not a link in it
        options = _opts(classify_model="cli-clf")
        options.plan_classify = mode
        assert resolve_classify_endpoint(options, _run(), None).model == "cli-clf"


class TestJevIsAClassifyEndpoint:
    def test_the_literal_id_selects_the_default_model_and_host(self, monkeypatch):
        monkeypatch.setenv("JEV_API_KEY", "jev-secret")
        choice = resolve_classify_endpoint(_opts(classify_model="jev"), _run(), None)
        assert (choice.api_format, choice.model, choice.api_base, choice.key) == (
            "jev",
            JEV_DEFAULT_MODEL,
            JEV_DEFAULT_BASE,
            "jev-secret",
        )

    def test_a_versioned_id_is_passed_through(self, monkeypatch):
        monkeypatch.setenv("JEV_API_KEY", "k")
        choice = resolve_classify_endpoint(
            _opts(classify_model="jev-1.13.0"), _run(), None
        )
        assert (choice.api_format, choice.model) == ("jev", "jev-1.13.0")

    def test_a_gateway_s_namespaced_id_is_jev_and_passed_through(self, monkeypatch):
        # Vercel's AI Gateway serves the classifier as `typesafe-ai/jev`
        monkeypatch.setenv("JEV_API_KEY", "k")
        choice = resolve_classify_endpoint(
            _opts(
                classify_model="typesafe-ai/jev",
                classify_base_url="https://ai-gateway.vercel.sh/typesafe",
            ),
            _run(),
            None,
        )
        assert (choice.api_format, choice.model, choice.api_base) == (
            "jev",
            "typesafe-ai/jev",
            "https://ai-gateway.vercel.sh/typesafe",
        )

    def test_another_host(self, monkeypatch):
        monkeypatch.setenv("JEV_API_KEY", "k")
        choice = resolve_classify_endpoint(
            _opts(classify_model="jev", classify_base_url="https://jev.example/"),
            _run(),
            None,
        )
        assert choice.api_base == "https://jev.example"

    def test_the_translation_key_is_never_sent_to_typesafe(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        monkeypatch.setenv("BBM_API_KEY", "sk-bbm")
        with pytest.raises(SystemExit, match="JEV_API_KEY"):
            resolve_classify_endpoint(_opts(classify_model="jev"), _run(), None)

    def test_the_flag_key_and_the_provider_variable(self, monkeypatch):
        choice = resolve_classify_endpoint(
            _opts(classify_model="jev", classify_key="k-flag"), _run(), None
        )
        assert choice.key == "k-flag"
        monkeypatch.setenv("IMG_KEY_VAR", "k-var")
        choice = resolve_classify_endpoint(
            _opts(),
            _run(),
            _provider(classify_model="jev", classify_env_key="IMG_KEY_VAR"),
        )
        assert (choice.key, choice.source) == ("k-var", "provider")


# ---------------------------------------------------------- the translator


def test_build_translator_carries_the_run_s_settings():
    from book_maker.translator.base_translator import PriceTable

    prices = PriceTable({"vision-x": {"input": 1, "output": 2}})
    options = SimpleNamespace(
        context_compact_at=None,
        no_context_compact=False,
        temperature=1.0,
        source_lang="auto",
        extra_body='{"foo": 1}',
        extra_headers='{"X-Gateway": "secret"}',
        price_table=prices,
        no_thinking=True,
        quiet=True,
    )
    same = EndpointChoice("vision-x", RUN_BASE, "sk", "openai", "cli", own_base=False)
    t = build_translator(same, options, "Simplified Chinese")
    assert t.model == "vision-x"
    assert t.usage.prices is prices
    assert t.no_thinking is True
    assert t.extra_body == {"foo": 1}
    assert t.extra_headers == {"X-Gateway": "secret"}

    other = EndpointChoice(
        "vision-x", "https://gw.example/v1", "sk", "openai", "cli", own_base=True
    )
    t = build_translator(other, options, "Simplified Chinese")
    # a header block is where a gateway's credential rides; it does not
    # travel to another host
    assert not t.extra_headers
    assert not t.extra_body
    assert t.no_thinking is True


# --------------------------------------------------------------------------
# The CLI's side: the old flag name, the mode it implies, the loader
# --------------------------------------------------------------------------


def _parsed(*argv):
    from book_maker.cli import normalize_options, parse_args

    options = parse_args(["--book_name", "b.epub", *argv])
    normalize_options(options)
    return options


def test_the_old_flag_name_is_the_same_option_and_wins():
    """PIN (owner 260923 22:40, packet F): --plan-classify-model is the old
    name of --classify-model; typed together, the old spelling wins (the
    packet's test list), and messages name the flag that was typed."""
    assert _parsed("--plan-classify-model", "old").classify_model == "old"
    both = _parsed("--classify-model", "new", "--plan-classify-model", "old")
    assert both.classify_model == "old"
    assert both.classify_model_flag == "--plan-classify-model"
    assert _parsed("--classify-model", "new").classify_model_flag == "--classify-model"


def test_the_old_flag_is_hidden_from_the_help():
    from book_maker.cli import build_parser

    text = build_parser().format_help()
    options = [line.split()[0] for line in text.splitlines() if line.startswith("  -")]
    assert "--classify-model" in options
    assert "--plan-classify-model" not in options
    # the help of the new flag names the old one (HELP_CLASSIFY_MODEL)
    assert "--plan-classify-model is the old name" in " ".join(text.split())


def test_a_classifier_implies_model_mode_on_an_epub_only():
    from book_maker.cli import resolve_classify_mode

    options = _parsed("--classify-model", "m")
    assert resolve_classify_mode(options, "epub") == ("model", False)
    # the PDF route's inner run translates Markdown: plan mode is not asked
    assert resolve_classify_mode(options, "md")[0] == "none"
    # agent and all stay what was typed
    agent = _parsed("--classify-model", "m", "--plan-classify", "agent")
    assert resolve_classify_mode(agent, "epub")[0] == "agent"


class _Meter:
    def __init__(self, line):
        self.line = line

    def summary(self):
        return self.line


def _loader():
    from book_maker.loader.epub_loader import EPUBBookLoader

    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.translate_model = SimpleNamespace(usage_summary=lambda: "run line")
    loader.classify_translator = None
    return loader


def test_a_classifier_of_its_own_gets_its_own_usage_line(capsys):
    from book_maker.classifier import Classifier

    loader = _loader()
    loader._print_usage()
    assert capsys.readouterr().out.split("\n") == ["run line", ""]

    translator = SimpleNamespace(usage=_Meter("tokens: in 5"), model="c-model")
    loader.classify_translator = Classifier(
        translator, None, backends=[], base="http://c/v1", separate=True
    )
    loader._print_usage()
    out = capsys.readouterr().out.split("\n")
    assert out[:2] == ["run line", "Classifier (c-model at http://c/v1): tokens: in 5"]

    # the run's own classifier is on the run's meter: one line
    loader.classify_translator = Classifier(translator, None, backends=[])
    loader._print_usage()
    assert capsys.readouterr().out.split("\n") == ["run line", ""]


def test_plan_classification_goes_through_the_injected_classifier(monkeypatch):
    from book_maker.loader import epub_loader

    seen = {}

    def classify_plan(ledger, translator, model=None):
        seen["translator"] = translator
        return {}, []

    monkeypatch.setattr(epub_loader, "classify_plan", classify_plan)
    loader = _loader()
    loader.plan_classify_model = None
    injected = object()
    loader.classify_translator = injected
    ledger = SimpleNamespace(decide=lambda *a, **k: None)
    try:
        loader._classify_plan(ledger, None, "plan.json")
    except Exception:
        pass  # whatever follows the call is not this test's business
    assert seen["translator"] is injected
    loader.classify_translator = None
    try:
        loader._classify_plan(ledger, None, "plan.json")
    except Exception:
        pass
    assert seen["translator"] is loader.translate_model
