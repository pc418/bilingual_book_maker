"""Route wiring for the non-OpenAI formats.

These cover what the CLI promises each `--api_format` on its way into a
translator: where the endpoint URL comes from, and that a comma-separated
`--key` really rotates rather than being sent verbatim as one credential.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest
from anthropic import AuthenticationError as AnthropicAuthError
from anthropic import NotFoundError as AnthropicNotFound

from book_maker.translator.caiyun_translator import Caiyun
from book_maker.translator.claude_translator import Claude
from book_maker.translator.custom_api_translator import CustomAPI


def _openai_completion(content):
    """An OpenAI-shaped chat completion, for the fallback's client."""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content), finish_reason="stop"
            )
        ]
    )


class TestCustomAPIEndpoint:
    """`--api_format customapi --api_base URL` is the documented route."""

    def test_the_endpoint_comes_from_api_base(self):
        translator = CustomAPI("", "Japanese", api_base="https://host/translate")

        with (
            patch("book_maker.translator.custom_api_translator.requests.post") as post,
            patch("book_maker.translator.custom_api_translator.time.sleep"),
        ):
            post.return_value = SimpleNamespace(text='{"data": "訳"}')
            assert translator.translate("text") == "訳"

        assert post.call_args.kwargs["url"] == "https://host/translate"

    def test_no_endpoint_fails_loud(self):
        # posting to "" is the silent version of this, and it used to be
        # exactly what the documented invocation did
        with pytest.raises(ValueError, match="endpoint URL"):
            CustomAPI("", "Japanese")

    def test_source_lang_reaches_the_request(self):
        import json

        translator = CustomAPI(
            "", "Japanese", api_base="https://host/t", source_lang="English"
        )

        with (
            patch("book_maker.translator.custom_api_translator.requests.post") as post,
            patch("book_maker.translator.custom_api_translator.time.sleep"),
        ):
            post.return_value = SimpleNamespace(text='{"data": "訳"}')
            translator.translate("text")

        assert json.loads(post.call_args.kwargs["data"])["source_lang"] == "English"


class TestAnthropicFallback:
    """A gateway may answer the OpenAI shape even when the id says claude.

    Format inference reads `claude` in a model id as "anthropic", which is
    right for Anthropic's own host and wrong for a gateway that serves the
    same model over /chat/completions. One failed attempt is enough to tell,
    so the route switches instead of ending the run.
    """

    def _claude(self, side_effect, api_base="https://gw.example.com"):
        with patch("book_maker.translator.claude_translator.Anthropic") as client:
            translator = Claude("k", "zh-hans", api_base=api_base)
        translator.client.messages.create = Mock(side_effect=side_effect)
        return translator

    def _not_found(self):
        return AnthropicNotFound(
            "not found",
            response=httpx.Response(
                404, request=httpx.Request("POST", "https://gw.example.com/v1/messages")
            ),
            body=None,
        )

    def test_a_wrong_shape_falls_back_to_the_openai_route(self, capsys):
        translator = self._claude(self._not_found())
        fallback = Mock()
        fallback.translate.return_value = "译文"

        with patch.object(Claude, "_build_openai_fallback", return_value=fallback):
            assert translator.translate("text") == "译文"

        assert "openai" in capsys.readouterr().out.lower()

    def test_the_fallback_is_built_once_and_then_used_directly(self):
        translator = self._claude(self._not_found())
        fallback = Mock()
        fallback.translate.return_value = "译文"

        with patch.object(
            Claude, "_build_openai_fallback", return_value=fallback
        ) as build:
            translator.translate("one")
            translator.translate("two")

        assert build.call_count == 1
        assert fallback.translate.call_count == 2
        # and the anthropic client was only tried for the first paragraph
        assert translator.client.messages.create.call_count == 1

    def test_an_auth_failure_never_falls_back(self):
        # a rejected key is not evidence about the wire format, and retrying
        # elsewhere would send the same key to a second endpoint
        error = AnthropicAuthError(
            "bad key",
            response=httpx.Response(
                401, request=httpx.Request("POST", "https://gw.example.com/v1/messages")
            ),
            body=None,
        )
        translator = self._claude(error)

        with pytest.raises(AnthropicAuthError):
            translator.translate("text")

    def test_no_fallback_without_an_endpoint_of_the_users_choosing(self):
        # the default host is Anthropic's own, which does not speak the
        # OpenAI shape; falling back there would just fail differently
        translator = self._claude(self._not_found(), api_base=None)

        with pytest.raises(AnthropicNotFound):
            translator.translate("text")

    def test_anthropics_own_host_never_falls_back_even_when_named(self):
        # the documented command passes --api_base https://api.anthropic.com;
        # a 404 there means the model does not exist, and retrying on
        # /chat/completions hides that behind a second, stranger error
        translator = self._claude(
            self._not_found(), api_base="https://api.anthropic.com"
        )

        with pytest.raises(AnthropicNotFound):
            translator.translate("text")

    def test_the_fallback_carries_the_context_settings(self):
        with patch("book_maker.translator.claude_translator.Anthropic"):
            translator = Claude(
                "k",
                "zh-hans",
                api_base="https://gw.example.com",
                context_flag=True,
                context_paragraph_limit=7,
            )
        translator.model = "claude-sonnet-4-6"

        with patch("book_maker.translator.chatgptapi_translator.ChatGPTAPI") as chatgpt:
            translator._build_openai_fallback()

        kwargs = chatgpt.call_args.kwargs
        assert kwargs["context_flag"] is True
        assert kwargs["context_paragraph_limit"] == 7

    @pytest.mark.parametrize(
        "given,expected",
        [
            ("https://gw.example.com", "https://gw.example.com/v1"),
            ("https://gw.example.com/v1", "https://gw.example.com/v1"),
        ],
    )
    def test_the_fallback_talks_to_the_openai_path(self, given, expected):
        with patch("book_maker.translator.claude_translator.Anthropic"):
            translator = Claude("k", "zh-hans", api_base=given)
        translator.model = "claude-sonnet-4-6"

        with patch("book_maker.translator.chatgptapi_translator.ChatGPTAPI") as chatgpt:
            translator._build_openai_fallback()

        assert chatgpt.call_args.kwargs["api_base"] == expected

    def test_the_model_check_is_owed_to_the_shape_that_answered(self):
        # the anthropic 404 was about the wire format, not the model, so it
        # is no verdict on the model at all; the endpoint that does answer
        # is the one that gets asked
        with patch("book_maker.translator.claude_translator.Anthropic"):
            translator = Claude("k", "zh-hans", api_base="https://gw.example.com")
        translator.model = "claude-sonnet-4-6"

        with patch("book_maker.translator.chatgptapi_translator.OpenAI"):
            fallback = translator._build_openai_fallback()

        assert fallback._route_state["pending"] == ["claude-sonnet-4-6"]

    def test_the_switch_costs_one_route_check_and_claims_no_missing_model(self, capsys):
        translator = self._claude(self._not_found())
        translator.model = "claude-sonnet-4-6"
        create = Mock(
            side_effect=[
                _openai_completion("PONG"),  # the route check
                _openai_completion("not json"),  # the capability probe
                _openai_completion("译文"),
                _openai_completion("译文二"),
            ]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
            models=SimpleNamespace(list=Mock(side_effect=RuntimeError("no listing"))),
        )

        with patch(
            "book_maker.translator.chatgptapi_translator.OpenAI", return_value=client
        ):
            assert translator.translate("one") == "译文"
            assert translator.translate("two") == "译文二"

        first = create.call_args_list[0].kwargs
        assert first["model"] == "claude-sonnet-4-6"
        # the route check is the one request carrying neither a schema nor a
        # translation: probed once for the whole run, not once per paragraph
        from book_maker.translator.capabilities import ROUTE_PROBE_PROMPT

        route_checks = [
            c
            for c in create.call_args_list
            if c.kwargs["messages"][0]["content"] == ROUTE_PROBE_PROMPT
        ]
        assert len(route_checks) == 1
        assert "does not serve" not in capsys.readouterr().out


class TestBaseNormalization:
    def test_an_sdk_route_loses_a_pasted_request_path(self):
        from book_maker.cli import normalize_api_base

        assert (
            normalize_api_base("https://h/v1/chat/completions", "openai")
            == "https://h/v1"
        )
        assert normalize_api_base("https://h/v1/", "openai") == "https://h/v1"

    def test_a_literal_endpoint_is_left_exactly_as_given(self):
        # customapi posts to this URL itself; trimming a path it needs would
        # send every request somewhere else
        from book_maker.cli import normalize_api_base

        for url in (
            "https://h/completions",
            "https://h/messages",
            "https://h/translate/",
        ):
            assert normalize_api_base(url, "customapi") == url


class TestKeyRotation:
    """`--key a,b` is advertised for every format that takes a key."""

    def test_claude_sends_one_key_not_the_list(self):
        with patch("book_maker.translator.claude_translator.Anthropic") as client:
            translator = Claude("first,second", "zh-hans")

        assert client.call_args.kwargs["api_key"] == "first"

        translator.rotate_key()
        assert translator.client.api_key == "second"
        translator.rotate_key()
        assert translator.client.api_key == "first"  # cycles

    def test_caiyun_sends_one_key_not_the_list(self):
        translator = Caiyun("first,second", "english")

        assert translator.headers["x-authorization"] == "token first"

        translator.rotate_key()
        assert translator.headers["x-authorization"] == "token second"

    def test_caiyun_rotates_per_translation(self):
        translator = Caiyun("first,second", "english")
        translator.headers["x-authorization"] = "token stale"

        with patch("book_maker.translator.caiyun_translator.requests.request") as req:
            req.return_value = SimpleNamespace(json=lambda: {"target": ["translated"]})
            translator.translate("text")

        # the header the request actually carried, not just the final state
        assert req.call_args.kwargs["headers"]["x-authorization"] == "token second"


class TestOrcaRouterRedirect:
    """`--model orcarouter[/<id>]` names a gateway, so it names an address.

    OrcaRouter speaks the OpenAI shape; what the shortcut adds is the base
    URL and, for the bare spelling, the smart-routing model — nothing that
    needs a translator class of its own.
    """

    def _resolve(self, model, api_base=None):
        from book_maker.translator import orcarouter_translator as orcarouter

        return orcarouter.resolve(model, api_base)

    def test_the_bare_name_is_the_smart_routing_model(self):
        assert self._resolve("orcarouter") == (
            "orcarouter/auto",
            "https://api.orcarouter.ai/v1",
        )

    def test_a_namespaced_id_keeps_its_model(self):
        assert self._resolve("orcarouter/anthropic/claude-sonnet-4-6") == (
            "orcarouter/anthropic/claude-sonnet-4-6",
            "https://api.orcarouter.ai/v1",
        )

    def test_an_explicit_endpoint_wins(self):
        # naming the gateway's model is shorthand for its address, not an
        # override of the one the user chose
        assert self._resolve("orcarouter", "https://gw.example.com/v1") == (
            "orcarouter/auto",
            "https://gw.example.com/v1",
        )

    @pytest.mark.parametrize("model", ["gpt-5-mini", "", "orcarouterish", "openai/gpt"])
    def test_anything_else_is_not_this_route(self, model):
        assert self._resolve(model) is None
