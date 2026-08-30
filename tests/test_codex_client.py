"""JSON-RPC sidecar client for `codex app-server`.

Driven by a scripted fake process — the real protocol shapes here were taken
from `codex app-server generate-json-schema` and two live probe turns against
codex-cli 0.150.1, and are pinned in docs/260827-feat-CODEX_TRANSLATOR_PROVIDER.md.

Every failure mode in this file is one that must be loud: a missing binary, a
login we do not have, a failed turn, a turn that never completes. Silence on
any of them would look like a hung translation run.
"""

import json
import threading

import pytest

from book_maker.codex_client import (
    CodexAppServer,
    CodexError,
    CodexLoginRequired,
    CodexTurnFailed,
    CodexUnavailable,
)


class FakeProcess:
    """A scripted app-server: maps request methods to canned results."""

    def __init__(self, handlers, notifications_for=None):
        self.handlers = handlers
        self.notifications_for = notifications_for or {}
        self.sent = []
        self._lines = []
        self._ready = threading.Condition()
        self._closed = False
        self.stdin = self
        self.stdout = self
        self.stderr = None

    # -- stdin side --
    def write(self, payload):
        message = json.loads(payload)
        self.sent.append(message)
        method = message.get("method")
        handler = self.handlers.get(method)
        out = []
        if handler is not None:
            result = handler(message) if callable(handler) else handler
            if isinstance(result, dict) and "error" in result:
                out.append({"id": message["id"], "error": result["error"]})
            else:
                out.append({"id": message["id"], "result": result})
        for note in self.notifications_for.get(method, []):
            out.append(note(message) if callable(note) else note)
        with self._ready:
            self._lines.extend(json.dumps(o) for o in out)
            self._ready.notify_all()

    def flush(self):
        pass

    # -- stdout side --
    def __iter__(self):
        while True:
            with self._ready:
                while not self._lines and not self._closed:
                    self._ready.wait(timeout=2)
                if self._lines:
                    yield self._lines.pop(0) + "\n"
                    continue
                if self._closed:
                    return

    def close(self):
        with self._ready:
            self._closed = True
            self._ready.notify_all()

    def terminate(self):
        self.close()

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return None


INIT = {"userAgent": "x", "codexHome": "/tmp"}
THREAD = {"thread": {"id": "th-1"}}


def _turn_completed(status="completed", text="译文", error=None):
    def build(message):
        items = []
        if text is not None:
            items.append(
                {"type": "agentMessage", "phase": "final_answer", "text": text}
            )
        return {
            "method": "turn/completed",
            "params": {
                "threadId": "th-1",
                "turn": {
                    "id": "tu-1",
                    "items": items,
                    "status": status,
                    "error": error,
                },
            },
        }

    return build


def _server(handlers=None, notifications_for=None):
    handlers = {
        "initialize": INIT,
        "thread/start": THREAD,
        "turn/start": {"turn": {"id": "tu-1", "status": "inProgress"}},
        **(handlers or {}),
    }
    proc = FakeProcess(handlers, notifications_for)
    server = CodexAppServer(spawn=lambda: proc)
    # `fake`, not `process`: assigning `process` would make start() a no-op.
    server.fake = proc
    return server


class TestStartup:
    def test_missing_binary_is_a_clear_error(self):
        def spawn():
            raise FileNotFoundError("codex")

        server = CodexAppServer(spawn=spawn)
        with pytest.raises(CodexUnavailable) as e:
            server.start()
        assert "codex" in str(e.value).lower()

    def test_initialize_sends_client_info(self):
        server = _server()
        server.start()
        try:
            init = next(m for m in server.fake.sent if m["method"] == "initialize")
            assert init["params"]["clientInfo"]["name"]
            assert init["params"]["clientInfo"]["version"]
        finally:
            server.close()

    def test_requests_carry_distinct_ids(self):
        server = _server({"account/rateLimits/read": {"rateLimits": {}}})
        server.start()
        try:
            server.request("account/rateLimits/read")
            ids = [m["id"] for m in server.fake.sent]
            assert len(ids) == len(set(ids))
        finally:
            server.close()

    def test_a_request_with_no_reply_times_out_rather_than_hanging(self):
        server = _server()
        server.start()
        try:
            with pytest.raises(CodexError) as e:
                server.request("account/usage/read", timeout=0.4)
            assert "did not answer" in str(e.value)
        finally:
            server.close()


class TestRateLimits:
    LIMITS = {
        "rateLimits": {
            "primary": {
                "usedPercent": 42,
                "windowDurationMins": 300,
                "resetsAt": 1788055986,
            },
            "planType": "plus",
            "rateLimitReachedType": None,
        }
    }

    def test_reads_used_percent_and_reset(self):
        server = _server({"account/rateLimits/read": self.LIMITS})
        server.start()
        try:
            limits = server.rate_limits()
            assert limits.used_percent == 42
            assert limits.plan_type == "plus"
            assert limits.resets_at == 1788055986
        finally:
            server.close()

    def test_missing_rate_limits_is_not_fatal(self):
        server = _server({"account/rateLimits/read": {}})
        server.start()
        try:
            assert server.rate_limits() is None
        finally:
            server.close()


class TestLogin:
    def test_not_logged_in_points_at_the_codex_cli(self):
        server = _server(
            {
                "account/rateLimits/read": {
                    "error": {"code": -32000, "message": "not logged in"}
                }
            }
        )
        server.start()
        try:
            with pytest.raises(CodexLoginRequired, match="codex login"):
                server.ensure_logged_in()
        finally:
            server.close()


class TestThreadAndTurn:
    def test_start_thread_returns_the_id(self):
        server = _server()
        server.start()
        try:
            assert server.start_thread(model="gpt-5.6-sol") == "th-1"
        finally:
            server.close()

    def test_thread_start_is_non_agentic(self):
        """read-only + never-approve is what makes a turn behave like a completion."""
        server = _server()
        server.start()
        try:
            server.start_thread(model="m", base_instructions="be a translator")
            params = next(m for m in server.fake.sent if m["method"] == "thread/start")[
                "params"
            ]
            assert params["sandbox"] == "read-only"
            assert params["approvalPolicy"] == "never"
            assert params["baseInstructions"] == "be a translator"
        finally:
            server.close()

    def test_run_turn_returns_the_final_agent_message(self):
        server = _server(notifications_for={"turn/start": [_turn_completed()]})
        server.start()
        try:
            assert server.run_turn("th-1", "The dog barked.") == "译文"
        finally:
            server.close()

    def test_run_turn_passes_an_output_schema(self):
        schema = {"type": "object", "properties": {}}
        server = _server(notifications_for={"turn/start": [_turn_completed()]})
        server.start()
        try:
            server.run_turn("th-1", "text", output_schema=schema)
            params = next(m for m in server.fake.sent if m["method"] == "turn/start")[
                "params"
            ]
            assert params["outputSchema"] == schema
        finally:
            server.close()

    def test_failed_turn_raises_with_the_message(self):
        server = _server(
            notifications_for={
                "turn/start": [
                    _turn_completed(
                        status="failed", text=None, error={"message": "quota"}
                    )
                ]
            }
        )
        server.start()
        try:
            with pytest.raises(CodexTurnFailed) as e:
                server.run_turn("th-1", "text")
            assert "quota" in str(e.value)
        finally:
            server.close()

    def test_turn_with_no_agent_message_raises(self):
        server = _server(notifications_for={"turn/start": [_turn_completed(text=None)]})
        server.start()
        try:
            with pytest.raises(CodexTurnFailed):
                server.run_turn("th-1", "text")
        finally:
            server.close()

    def test_a_turn_that_never_completes_times_out(self):
        server = _server()  # no turn/completed notification ever arrives
        server.start()
        try:
            with pytest.raises(CodexTurnFailed) as e:
                server.run_turn("th-1", "text", timeout=0.5)
            assert "timed out" in str(e.value).lower()
        finally:
            server.close()

    def test_notifications_arriving_before_the_response_are_not_lost(self):
        """turn/completed can land before turn/start's own reply is processed."""
        server = _server(notifications_for={"turn/start": [_turn_completed()]})
        server.start()
        try:
            assert server.run_turn("th-1", "text") == "译文"
        finally:
            server.close()


class TestContextManager:
    def test_closes_the_process_on_exit(self):
        server = _server()
        with server:
            pass
        assert server.fake._closed


class TestRobustness:
    def test_notifications_do_not_grow_without_bound(self):
        server = _server(notifications_for={"turn/start": [_turn_completed()]})
        server.start()
        try:
            for _ in range(40):
                server.run_turn("th-1", "text")
            assert len(server._notifications) < 40
        finally:
            server.close()

    def test_a_failed_initialize_does_not_leave_the_child_running(self):
        proc = FakeProcess({})  # never answers initialize
        server = CodexAppServer(spawn=lambda: proc)
        with pytest.raises(CodexError):
            server.request_timeout = 0.3
            server.start(init_timeout=0.3)
        assert proc._closed

    def test_close_is_idempotent(self):
        server = _server()
        server.start()
        server.close()
        server.close()  # must not raise
