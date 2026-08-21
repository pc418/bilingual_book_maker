"""Route wiring for the non-OpenAI formats.

These cover what the CLI promises each `--api_format` on its way into a
translator: where the endpoint URL comes from, and that a comma-separated
`--key` really rotates rather than being sent verbatim as one credential.
"""

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from book_maker.translator.caiyun_translator import Caiyun
from book_maker.translator.claude_translator import Claude
from book_maker.translator.custom_api_translator import CustomAPI


class TestCustomAPIEndpoint:
    """`--api_format customapi --api_base URL` is the documented route."""

    def test_the_endpoint_comes_from_api_base(self):
        translator = CustomAPI("", "Japanese", api_base="https://host/translate")

        with patch(
            "book_maker.translator.custom_api_translator.requests.post"
        ) as post, patch("book_maker.translator.custom_api_translator.time.sleep"):
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

        with patch(
            "book_maker.translator.custom_api_translator.requests.post"
        ) as post, patch("book_maker.translator.custom_api_translator.time.sleep"):
            post.return_value = SimpleNamespace(text='{"data": "訳"}')
            translator.translate("text")

        assert json.loads(post.call_args.kwargs["data"])["source_lang"] == "English"


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
