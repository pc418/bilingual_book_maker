"""The migration layer for commands written against the old CLI.

Old invocations keep working: every removed flag is rewritten into the
endpoint surface before argparse sees it, and each rewrite prints what it
became so the user can update their command. Nothing is guessed silently —
a legacy flag with no honest equivalent fails loudly instead.
"""

import json

import pytest

from book_maker.legacy_cli import translate_legacy_argv


def rewrite(*argv):
    """The rewritten argv, ignoring notices."""
    return translate_legacy_argv(list(argv)).argv


def notices(*argv):
    return " ".join(translate_legacy_argv(list(argv)).notices)


def flags(*argv):
    """Rewritten argv as {flag: value}, for cases where order is irrelevant."""
    out = translate_legacy_argv(list(argv)).argv
    return dict(zip(out[::2], out[1::2]))


class TestUntouched:
    def test_a_modern_command_is_passed_through_unchanged(self):
        argv = ["--book_name", "b.epub", "--key", "k", "--model_list", "gpt-5-mini"]
        assert rewrite(*argv) == argv
        assert notices(*argv) == ""

    def test_flags_that_merely_start_the_same_are_left_alone(self):
        argv = ["--model_list", "gpt-5-mini"]
        assert rewrite(*argv) == argv


class TestOpenAIPresets:
    @pytest.mark.parametrize(
        "alias,model",
        [
            ("chatgptapi", "gpt-3.5-turbo"),
            ("gpt4", "gpt-4"),
            ("gpt4omini", "gpt-4o-mini"),
            ("gpt4o", "gpt-4o"),
            ("gpt5mini", "gpt-5-mini"),
            ("o1preview", "o1-preview"),
            ("o1", "o1"),
            ("o1mini", "o1-mini"),
            ("o3mini", "o3-mini"),
        ],
    )
    def test_each_preset_becomes_its_first_model(self, alias, model):
        # the preset's own head model, not a newer one: translating must not
        # quietly move a run onto a model the user never asked for
        assert rewrite("--model", alias) == ["--model_list", model]

    def test_the_openai_alias_keeps_the_users_model_list(self):
        assert rewrite("--model", "openai", "--model_list", "yi-34b") == [
            "--model_list",
            "yi-34b",
        ]

    def test_an_explicit_model_list_wins_over_the_preset_default(self):
        assert rewrite("--model", "gpt4", "--model_list", "gpt-4.1") == [
            "--model_list",
            "gpt-4.1",
        ]

    def test_the_equals_form_is_understood(self):
        assert rewrite("--model=gpt4o") == ["--model_list", "gpt-4o"]

    def test_the_short_form_is_understood(self):
        assert rewrite("-m", "gpt4o") == ["--model_list", "gpt-4o"]


class TestVendorRoutes:
    def test_claude_becomes_the_anthropic_format(self):
        assert rewrite("--model", "claude") == [
            "--api_format",
            "anthropic",
            "--model_list",
            "claude-haiku-4-5-20251001",
        ]

    def test_an_exact_claude_id_is_carried_over(self):
        assert rewrite("--model", "claude-opus-4-6") == [
            "--api_format",
            "anthropic",
            "--model_list",
            "claude-opus-4-6",
        ]

    @pytest.mark.parametrize(
        "alias,base,model",
        [
            (
                "gemini",
                "https://generativelanguage.googleapis.com/v1beta/openai/",
                "gemini-flash-latest",
            ),
            (
                "geminipro",
                "https://generativelanguage.googleapis.com/v1beta/openai/",
                "gemini-pro-latest",
            ),
            ("xai", "https://api.x.ai/v1", "grok-beta"),
            (
                "qwen",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "qwen-mt-turbo",
            ),
            (
                "qwen-mt-plus",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "qwen-mt-plus",
            ),
        ],
    )
    def test_vendors_become_their_openai_compatible_endpoint(self, alias, base, model):
        assert rewrite("--model", alias) == [
            "--api_base",
            base,
            "--model_list",
            model,
        ]

    def test_groq_keeps_the_required_model_list(self):
        assert rewrite("--model", "groq", "--model_list", "llama3-8b-8192") == [
            "--api_base",
            "https://api.groq.com/openai/v1",
            "--model_list",
            "llama3-8b-8192",
        ]

    def test_an_explicit_api_base_is_never_overridden(self):
        # a gateway serving gemini ids is the whole reason someone passes both
        assert flags("--model", "gemini", "--api_base", "https://gw/v1") == {
            "--api_base": "https://gw/v1",
            "--model_list": "gemini-flash-latest",
        }

    @pytest.mark.parametrize(
        "alias,fmt",
        [
            ("google", "google"),
            ("caiyun", "caiyun"),
            ("deepl", "deepl"),
            ("deeplfree", "deeplfree"),
            ("tencentransmart", "tencent"),
        ],
    )
    def test_machine_translation_aliases_become_formats(self, alias, fmt):
        assert rewrite("--model", alias) == ["--api_format", fmt]


class TestKeys:
    @pytest.mark.parametrize(
        "flag",
        [
            "--openai_key",
            "--claude_key",
            "--gemini_key",
            "--groq_key",
            "--xai_key",
            "--qwen_key",
            "--caiyun_key",
            "--deepl_key",
            "--api_key",
        ],
    )
    def test_every_key_flag_becomes_key(self, flag):
        assert rewrite(flag, "secret") == ["--key", "secret"]

    def test_the_legacy_key_variable_is_still_consulted(self):
        # --model gemini now routes through the openai format, whose variables
        # are not the one an old user exported
        result = translate_legacy_argv(["--model", "gemini"])
        assert "BBM_GOOGLE_GEMINI_KEY" in result.env_keys

    def test_a_modern_command_adds_no_extra_variables(self):
        assert translate_legacy_argv(["--model_list", "gpt-5-mini"]).env_keys == ()


class TestEndpointFlags:
    def test_ollama_becomes_a_local_endpoint(self):
        assert rewrite("--ollama_model", "llama3") == [
            "--api_base",
            "http://localhost:11434/v1",
            "--model_list",
            "llama3",
        ]

    def test_custom_api_carries_its_url_to_api_base(self):
        assert rewrite("--custom_api", "https://host/t") == [
            "--api_format",
            "customapi",
            "--api_base",
            "https://host/t",
        ]

    def test_azure_deployment_becomes_the_model(self):
        assert flags(
            "--api_base", "https://x.openai.azure.com", "--deployment_id", "dep"
        ) == {"--api_base": "https://x.openai.azure.com", "--model_list": "dep"}

    def test_interval_is_dropped_with_a_word_about_it(self):
        assert rewrite("--interval", "0.5", "--model_list", "m") == [
            "--model_list",
            "m",
        ]
        assert "interval" in notices("--interval", "0.5", "--model_list", "m").lower()


class TestProvider:
    def _write(self, tmp_path, monkeypatch, config):
        (tmp_path / "bbm_providers.json").write_text(json.dumps(config))
        monkeypatch.chdir(tmp_path)

    def test_a_provider_becomes_its_endpoint(self, tmp_path, monkeypatch):
        self._write(
            tmp_path,
            monkeypatch,
            {
                "providers": {
                    "deepseek": {
                        "api_style": "openai",
                        "base_url": "https://api.deepseek.com/v1",
                        "default_models": ["deepseek-chat"],
                        "env_key": "BBM_DEEPSEEK_API_KEY",
                    }
                }
            },
        )

        result = translate_legacy_argv(["--provider", "deepseek"])

        assert result.argv == [
            "--api_base",
            "https://api.deepseek.com/v1",
            "--model_list",
            "deepseek-chat",
        ]
        assert "BBM_DEEPSEEK_API_KEY" in result.env_keys

    def test_a_claude_style_provider_keeps_its_format(self, tmp_path, monkeypatch):
        self._write(
            tmp_path,
            monkeypatch,
            {
                "providers": {
                    "gw": {
                        "api_style": "claude",
                        "base_url": "https://gw.example.com",
                        "default_models": ["claude-haiku-4.5"],
                    }
                }
            },
        )

        assert translate_legacy_argv(["--provider", "gw"]).argv == [
            "--api_format",
            "anthropic",
            "--api_base",
            "https://gw.example.com",
            "--model_list",
            "claude-haiku-4.5",
        ]

    def test_an_unknown_provider_fails_loud(self, tmp_path, monkeypatch):
        self._write(tmp_path, monkeypatch, {"providers": {}})

        with pytest.raises(SystemExit, match="ghost"):
            translate_legacy_argv(["--provider", "ghost"])


class TestNotices:
    def test_each_rewrite_states_its_replacement(self):
        text = notices("--model", "gpt4", "--openai_key", "sk")
        assert "--model" in text and "--model_list" in text
        assert "--openai_key" in text and "--key" in text

    def test_a_secret_is_never_echoed(self):
        assert "sk-secret" not in notices("--openai_key", "sk-secret")

    def test_an_unknown_model_alias_fails_loud(self):
        # silently treating it as a model id would send an alias like "gpt3"
        # to the endpoint and fail there with a confusing message
        with pytest.raises(SystemExit, match="gpt3"):
            translate_legacy_argv(["--model", "gpt3"])
