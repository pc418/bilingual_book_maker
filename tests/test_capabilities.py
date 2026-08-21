"""Unit tests for the extracted capability layer.

`tests/test_chatgptapi_translator.py` covers how the translator *uses* a
verdict; these cover the layer itself — what the probe grades, which errors it
is allowed to swallow, and what the ledger remembers. The boundary matters:
capability is a property of the endpoint, so it has to be establishable
without a translator at all.
"""

import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)

from book_maker.translator.capabilities import (
    STRUCTURED_FAILURE_THRESHOLD,
    CapabilityLedger,
    ProbeDeferred,
    classify_bad_request,
    grade_probe_response,
    probe_structured_output,
)

REQUEST = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")


def _completion(content, finish_reason="stop"):
    message = SimpleNamespace(content=content)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)]
    )


def _api_error(cls, status_code, message="boom"):
    return cls(message, response=httpx.Response(status_code, request=REQUEST), body=None)


def _client(create):
    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )


class TestGrading:
    """Four verdicts, graded from the body alone."""

    @pytest.mark.parametrize(
        "content,verdict",
        [
            ('{"probe":"schema_ok"}', "strict"),
            ('{"probe":"ignored"}', "shape"),  # shape honored, enum dropped
            ('{"answer":"x"}', "json"),  # json mode, schema dropped
            ('{"probe":"schema_ok","extra":1}', "json"),
            ('{"probe":42}', "json"),  # right key, wrong type
            ("[1,2,3]", "json"),  # JSON but not an object
            ("ignored", "unsupported"),  # prose: response_format dropped
            ("", "unsupported"),
        ],
    )
    def test_verdicts(self, content, verdict):
        assert grade_probe_response(_completion(content)) == verdict

    def test_truncation_is_never_graded_as_support(self):
        """A cut-off answer proves nothing; the fragment may even parse later."""
        truncated = _completion('{"probe":"schema', finish_reason="length")
        assert grade_probe_response(truncated) == "unsupported"


class TestProbeErrorTaxonomy:
    """Which failures are answers about the endpoint, and which are not."""

    @pytest.mark.parametrize(
        "error",
        [
            _api_error(AuthenticationError, 401),
            _api_error(PermissionDeniedError, 403),
            _api_error(NotFoundError, 404),
        ],
    )
    def test_permanent_errors_propagate(self, error):
        # A typo'd key must not read as "this endpoint has no schema support"
        # and pin the whole run to the delimiter method.
        with pytest.raises(type(error)):
            probe_structured_output(_client(Mock(side_effect=error)), "m")

    @pytest.mark.parametrize(
        "error",
        [
            APIConnectionError(request=REQUEST),
            APITimeoutError(request=REQUEST),
            _api_error(RateLimitError, 429),
        ],
    )
    def test_outages_defer_rather_than_answer(self, error):
        with pytest.raises(ProbeDeferred):
            probe_structured_output(_client(Mock(side_effect=error)), "m")

    def test_ambiguous_failures_grade_as_unusable(self):
        """A 500 from a quirky local server: not usable, but not fatal either."""
        verdict = probe_structured_output(
            _client(Mock(side_effect=RuntimeError("boom"))), "m"
        )
        assert verdict.startswith("request rejected")

    def test_bad_request_is_a_capability_answer(self):
        verdict = probe_structured_output(
            _client(Mock(side_effect=_api_error(BadRequestError, 400))), "m"
        )
        assert verdict.startswith("request rejected")


class TestProbeRequest:
    def test_prompt_fights_the_schema(self):
        create = Mock(return_value=_completion('{"probe":"schema_ok"}'))

        probe_structured_output(_client(create), "m")

        request = create.call_args.kwargs
        prompt = request["messages"][0]["content"]
        # The expected value must never appear in the prompt: a server that
        # echoes the prompt cannot accidentally pass.
        assert "schema_ok" not in prompt
        assert "json" in prompt.lower()
        assert request["response_format"]["json_schema"]["strict"] is True
        # Exactly one capability under test: no sampling, no cap.
        assert "temperature" not in request
        assert "max_tokens" not in request


class TestLedger:
    def test_probe_runs_once_per_model(self):
        ledger = CapabilityLedger()
        probe = Mock(return_value="strict")

        assert ledger.ensure_verdict("a", probe) == "strict"
        assert ledger.ensure_verdict("a", probe) == "strict"
        assert ledger.ensure_verdict("b", probe) == "strict"

        assert probe.call_count == 2  # once for "a", once for "b"

    def test_one_probe_under_parallel_workers(self):
        """N parallel workers must cost one probe, not N."""
        ledger = CapabilityLedger()
        verdicts = []

        def slow_probe(model):
            time.sleep(0.05)  # wide enough for the others to pile up on the lock
            return "strict"

        probe = Mock(side_effect=slow_probe)
        threads = [
            threading.Thread(
                target=lambda: verdicts.append(ledger.ensure_verdict("a", probe))
            )
            for _ in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
            assert not t.is_alive()

        assert probe.call_count == 1
        assert verdicts == ["strict"] * 4  # and every worker got the answer

    def test_no_probe_means_no_support_and_no_request(self):
        ledger = CapabilityLedger()
        assert ledger.ensure_verdict("a", None) is False
        assert ledger.verdicts["a"] is False

    def test_deferred_probe_caches_nothing(self):
        ledger = CapabilityLedger()
        probe = Mock(side_effect=ProbeDeferred("gateway down"))

        assert ledger.ensure_verdict("a", probe) is False
        assert ledger.verdicts == {}  # nothing learned

        probe.side_effect = None
        probe.return_value = "strict"
        assert ledger.ensure_verdict("a", probe) == "strict"

    @pytest.mark.parametrize(
        "verdict,stored",
        [
            ("strict", "strict"),
            ("shape", "shape"),
            ("json", "json"),
            ("unsupported", False),
            ("request rejected: boom", False),
        ],
    )
    def test_record_normalizes_unusable_verdicts_to_false(self, verdict, stored):
        ledger = CapabilityLedger()
        ledger.record("a", verdict)
        assert ledger.verdicts["a"] == stored

    def test_demotion_takes_a_streak_not_one_bad_reply(self):
        """One garbled proxy reply must not cost a multi-hour run its schema."""
        ledger = CapabilityLedger()
        ledger.record("a", "strict")

        for _ in range(STRUCTURED_FAILURE_THRESHOLD - 1):
            ledger.demote("a", "bad json")
            assert ledger.verdicts["a"] == "strict"

        ledger.demote("a", "bad json")
        assert ledger.verdicts["a"] is False

    def test_success_clears_the_streak(self):
        ledger = CapabilityLedger()
        ledger.record("a", "strict")

        ledger.demote("a", "blip")
        ledger.note_success("a")
        ledger.demote("a", "blip")

        # The streak restarted, so one more failure has not demoted it.
        assert ledger.verdicts["a"] == "strict"

    def test_streaks_are_per_model(self):
        ledger = CapabilityLedger()
        ledger.record("a", "strict")
        ledger.record("b", "strict")

        ledger.demote("a", "blip")
        ledger.demote("b", "blip")

        assert ledger.verdicts["a"] == "strict"
        assert ledger.verdicts["b"] == "strict"


class TestTemperature:
    def test_default_temperature_is_never_sent(self):
        """Sending the API's own default changes nothing and 400s on o-series."""
        ledger = CapabilityLedger()
        assert ledger.sampling_kwargs("m", 1.0) == {}
        assert ledger.sampling_kwargs("m", None) == {}

    def test_explicit_temperature_is_sent(self):
        ledger = CapabilityLedger()
        assert ledger.sampling_kwargs("m", 0.3) == {"temperature": 0.3}

    def test_a_rejection_is_remembered_and_announced_once(self):
        ledger = CapabilityLedger()

        assert ledger.note_temperature_rejected("m") is True
        assert ledger.note_temperature_rejected("m") is False  # no repeat log
        assert ledger.sampling_kwargs("m", 0.3) == {}
        # ...but only for the model that refused.
        assert ledger.sampling_kwargs("other", 0.3) == {"temperature": 0.3}


class TestBadRequestClassification:
    @pytest.mark.parametrize(
        "message,kind",
        [
            ("Unsupported value: 'temperature' does not support 0.3", "temperature"),
            ("Invalid parameter: 'response_format'", "schema"),
            ("json_schema is not supported", "schema"),
            ("context length exceeded", "other"),
        ],
    )
    def test_classification(self, message, kind):
        # Misreading a temperature 400 as "no schema support" demotes the model
        # for the rest of the run and hides the real cause from the user.
        assert classify_bad_request(_api_error(BadRequestError, 400, message)) == kind
