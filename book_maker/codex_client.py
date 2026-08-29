"""A JSON-RPC client for the `codex app-server` sidecar.

This is how the `codex` translator format spends a ChatGPT/Codex subscription
allowance instead of API credits. The app-server is the same interface the
official Codex clients drive: it owns OAuth, token refresh and credential
storage, so none of that lives here.

Extracting the OAuth token and calling api.openai.com with it is deliberately
not supported — ChatGPT credits and API credits are separate billing systems,
and doing that would bill the wrong one.

Protocol notes, verified against codex-cli 0.150.1 (see
docs/260827-feat-CODEX_TRANSLATOR_PROVIDER.md):

- Requests are newline-delimited JSON with an `id`; replies carry the same
  `id` plus `result` or `error`. Anything with a `method` and no `id` is a
  notification.
- `turn/start` returns as soon as the turn is accepted. The answer arrives
  later as a `turn/completed` notification carrying `turn.items`; the
  translation is the item with `type: "agentMessage"` and
  `phase: "final_answer"`.
- With `sandbox: "read-only"` and `approvalPolicy: "never"` a turn behaves
  like a chat completion rather than an agent session.
"""

from __future__ import annotations

import json
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass

# A turn is a model call, and a slow model on a long paragraph is normal.
DEFAULT_TURN_TIMEOUT = 600.0
# Everything else is local bookkeeping inside the sidecar.
DEFAULT_REQUEST_TIMEOUT = 60.0

CLIENT_NAME = "bilingual-book-maker"
CLIENT_VERSION = "1.0"


class CodexError(Exception):
    """Anything that went wrong talking to the app-server."""


class CodexUnavailable(CodexError):
    """The `codex` binary is missing, too old, or would not start."""


class CodexLoginRequired(CodexError):
    """No usable ChatGPT session. Carries what the user must do about it."""


class CodexTurnFailed(CodexError):
    """A turn failed, timed out, or produced no assistant message."""


@dataclass(frozen=True)
class RateLimits:
    used_percent: float
    window_minutes: int | None
    resets_at: int | None
    plan_type: str | None
    reached_type: str | None


@dataclass(frozen=True)
class LoginPrompt:
    """What to show a user so they can complete a login we cannot do for them."""

    login_id: str
    auth_url: str = ""
    user_code: str = ""
    verification_url: str = ""

    def describe(self) -> str:
        if self.user_code:
            return f"Open {self.verification_url} and enter the code {self.user_code}"
        return f"Open this URL to sign in to ChatGPT:\n  {self.auth_url}"


def _spawn_codex(binary="codex"):
    return subprocess.Popen(
        [binary, "app-server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
    )


class CodexAppServer:
    """One sidecar process, driven request/response with a reader thread."""

    def __init__(self, binary="codex", spawn=None, cwd=None):
        self._spawn = spawn or (lambda: _spawn_codex(binary))
        self.binary = binary
        self.cwd = cwd
        self.process = None
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        # stdin is written by callers *and* by the reader thread's denials, so
        # it needs a lock of its own — one that is never held while waiting on
        # `_cond`, or the reader could block the only stdout consumer.
        self._write_lock = threading.Lock()
        self._next_id = 0
        self._replies: dict[int, dict] = {}
        self._notifications: list[dict] = []
        # Notifications are consumed by index, so pruning shifts every live
        # mark. `_consumed` records how many were dropped so marks stay valid.
        self._consumed = 0
        # Absolute marks of in-flight waiters; pruning never goes below them.
        self._active_marks: list[int] = []
        self._reader = None
        self._stopped = False

    # ---- lifecycle --------------------------------------------------------

    def start(self, init_timeout=DEFAULT_REQUEST_TIMEOUT):
        if self.process is not None:
            return self
        try:
            self.process = self._spawn()
        except FileNotFoundError as e:
            raise CodexUnavailable(
                f"could not run the {self.binary!r} binary. Install the Codex "
                f"CLI (https://developers.openai.com/codex/cli) and make sure "
                f"`{self.binary} app-server` works."
            ) from e
        except OSError as e:
            raise CodexUnavailable(
                f"could not start `{self.binary} app-server`: {e}"
            ) from e

        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()
        try:
            self.request(
                "initialize",
                {"clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION}},
                timeout=init_timeout,
            )
        except CodexError:
            # A sidecar that never finished the handshake is useless and must
            # not be left running: a retry loop would spawn one per attempt.
            self.close()
            raise
        return self

    def close(self):
        self._stopped = True
        process = self.process
        self.process = None
        if process is None:
            return
        try:
            process.terminate()
        except Exception:
            pass
        # Reap it, so a long-lived run does not accumulate zombies. A sidecar
        # that ignores SIGTERM gets killed rather than left behind.
        try:
            process.wait(timeout=5)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=5)
            except Exception:
                pass
        for pipe in (getattr(process, "stdin", None), getattr(process, "stdout", None)):
            try:
                pipe.close()
            except Exception:
                pass
        with self._cond:
            self._cond.notify_all()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()
        return False

    # ---- transport --------------------------------------------------------

    def _read_loop(self):
        try:
            for line in self.process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue  # the sidecar also prints non-protocol chatter
                deny_id = None
                with self._cond:
                    if "id" in message and ("result" in message or "error" in message):
                        self._replies[message["id"]] = message
                    elif "method" in message and "id" in message:
                        # A ServerRequest (an approval). We run read-only with
                        # approvals off, so one arriving is unplanned: refuse
                        # it rather than let the turn block on it.
                        deny_id = message["id"]
                    elif "method" in message:
                        self._notifications.append(message)
                    self._cond.notify_all()
                if deny_id is not None:
                    # Off this thread entirely: the denial writes to stdin,
                    # stdin can backpressure, and this is the only stdout
                    # reader. Blocking here while the sidecar blocks writing
                    # stdout is a deadlock, so hand it to a throwaway thread
                    # and keep draining.
                    threading.Thread(
                        target=self._deny, args=(deny_id,), daemon=True
                    ).start()
        except (ValueError, OSError):
            pass  # process went away; waiters fail on their own timeouts
        finally:
            with self._cond:
                self._stopped = True
                self._cond.notify_all()

    def _deny(self, request_id):
        try:
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {
                        "code": -32601,
                        "message": "bilingual-book-maker runs codex non-interactively",
                    },
                }
            )
        except CodexError:
            pass  # the connection is already gone; waiters will time out

    def _write(self, payload):
        process = self.process
        if process is None:
            raise CodexError("the codex app-server is not running")
        try:
            # One writer at a time: a TextIOWrapper gives no interleaving
            # guarantee, and a half-written line would corrupt the stream for
            # every later request.
            with self._write_lock:
                process.stdin.write(json.dumps(payload) + "\n")
                process.stdin.flush()
        except (BrokenPipeError, ValueError, OSError) as e:
            raise CodexError(f"lost the connection to codex app-server: {e}") from e

    def request(self, method, params=None, timeout=DEFAULT_REQUEST_TIMEOUT):
        """Send a request and wait for its reply. Raises on an error reply."""
        with self._cond:
            self._next_id += 1
            request_id = self._next_id
        self._write(
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        )

        with self._cond:
            if not self._cond.wait_for(
                lambda: request_id in self._replies or self._stopped, timeout=timeout
            ):
                raise CodexError(f"codex did not answer {method} within {timeout}s")
            message = self._replies.pop(request_id, None)
        if message is None:
            raise CodexError(f"codex app-server stopped before answering {method}")
        if "error" in message:
            raise CodexError(
                f"{method} failed: {message['error'].get('message', message['error'])}"
            )
        return message.get("result") or {}

    # ---- account ----------------------------------------------------------

    def rate_limits(self):
        """Current quota, or None when the server reports none."""
        result = self.request("account/rateLimits/read")
        limits = result.get("rateLimits")
        if not limits:
            return None
        primary = limits.get("primary") or {}
        return RateLimits(
            used_percent=primary.get("usedPercent", 0),
            window_minutes=primary.get("windowDurationMins"),
            resets_at=primary.get("resetsAt"),
            plan_type=limits.get("planType"),
            reached_type=limits.get("rateLimitReachedType"),
        )

    def ensure_logged_in(self):
        """Confirm a usable session, or say exactly how to get one.

        Reading the quota is the cheapest call that needs an account, so an
        error here is the login signal.
        """
        try:
            return self.rate_limits()
        except CodexError as e:
            raise CodexLoginRequired(
                f"codex is not signed in to ChatGPT ({e}). Run `codex login`, "
                f"or pass --codex-login to sign in from here."
            ) from e

    def login_start(self, device_code=False):
        """Begin a login. Returns what the user has to do; codex does the rest."""
        kind = "chatgptDeviceCode" if device_code else "chatgpt"
        # Reserved before the request goes out: a login that completes fast
        # can push `account/login/completed` before we get here, and a mark
        # taken afterwards would skip past it and wait out the whole timeout.
        # The reservation also keeps a concurrent prune off it.
        self._login_reservation = self._reserve_mark()
        self._login_mark = self._login_reservation.__enter__()
        result = self.request("account/login/start", {"type": kind})
        return LoginPrompt(
            login_id=result.get("loginId", ""),
            auth_url=result.get("authUrl", ""),
            user_code=result.get("userCode", ""),
            verification_url=result.get(
                "verificationUrl", "https://auth.openai.com/codex/device"
            ),
        )

    def wait_for_login(self, timeout=300.0):
        """Block until codex reports the login finished."""
        try:
            note = self._await_notification(
                lambda m: m.get("method") == "account/login/completed",
                timeout=timeout,
                mark=getattr(self, "_login_mark", self._consumed),
            )
        finally:
            reservation = self.__dict__.pop("_login_reservation", None)
            if reservation is not None:
                reservation.__exit__(None, None, None)
        if note is None:
            raise CodexLoginRequired("timed out waiting for the ChatGPT login")
        if not note.get("params", {}).get("success"):
            raise CodexLoginRequired("the ChatGPT login did not complete")
        return True

    # ---- threads and turns ------------------------------------------------

    def start_thread(self, model=None, base_instructions=None, cwd=None):
        """A new thread configured to answer, not to act.

        `read-only` + `never` is what keeps a turn shaped like a chat
        completion; `ephemeral` keeps translation runs out of the user's
        Codex thread history.
        """
        params = {
            "sandbox": "read-only",
            "approvalPolicy": "never",
            "ephemeral": True,
            "cwd": cwd or self.cwd or ".",
        }
        if model:
            params["model"] = model
        if base_instructions:
            params["baseInstructions"] = base_instructions
        result = self.request("thread/start", params)
        thread_id = (result.get("thread") or {}).get("id")
        if not thread_id:
            raise CodexError(f"thread/start returned no thread id: {result!r}")
        return thread_id

    def run_turn(self, thread_id, text, output_schema=None, timeout=None):
        """One turn; returns the final assistant message.

        The reply is a notification that can land before `turn/start`'s own
        response is processed, so the notification list is marked *before*
        sending and scanned from that mark.
        """
        timeout = DEFAULT_TURN_TIMEOUT if timeout is None else timeout
        # Reserved before the request goes out, not when the wait starts: in
        # the gap between the two, another turn's prune could drop the very
        # completion this call is about to look for.
        with self._reserve_mark() as mark:
            return self._run_turn_from(mark, thread_id, text, output_schema, timeout)

    def _run_turn_from(self, mark, thread_id, text, output_schema, timeout):
        params = {"threadId": thread_id, "input": [{"type": "text", "text": text}]}
        if output_schema is not None:
            params["outputSchema"] = output_schema
        result = self.request("turn/start", params)
        turn_id = (result.get("turn") or {}).get("id")

        note = self._await_notification(
            lambda m: (
                m.get("method") == "turn/completed"
                and (
                    turn_id is None
                    or (m.get("params", {}).get("turn") or {}).get("id") == turn_id
                )
            ),
            timeout=timeout,
            mark=mark,
        )
        # Whatever the outcome, this turn's notifications are finished with. A
        # book is thousands of turns and every `turn/completed` carries the
        # full translated text, so keeping them would hold most of the book in
        # memory a second time.
        self._prune_notifications()
        if note is None:
            raise CodexTurnFailed(
                f"the codex turn timed out after {timeout}s with no answer"
            )
        return self._turn_text(note["params"].get("turn") or {})

    @contextmanager
    def _reserve_mark(self):
        """Hold a position in the notification stream so pruning cannot pass it."""
        with self._cond:
            mark = self._consumed + len(self._notifications)
            self._active_marks.append(mark)
        try:
            yield mark
        finally:
            with self._cond:
                self._active_marks.remove(mark)

    def _prune_notifications(self):
        """Drop notifications no live waiter can still be scanning for.

        Marks are absolute positions in the whole stream, so pruning is safe
        as long as nothing is dropped below the earliest mark still in use.
        """
        with self._cond:
            floor = min(
                self._active_marks, default=self._consumed + len(self._notifications)
            )
            drop = floor - self._consumed
            if drop > 0:
                del self._notifications[:drop]
                self._consumed += drop

    @staticmethod
    def _turn_text(turn):
        status = turn.get("status")
        if status != "completed":
            message = (turn.get("error") or {}).get("message") or status
            raise CodexTurnFailed(f"the codex turn did not complete: {message}")
        for item in turn.get("items") or []:
            if item.get("type") == "agentMessage" and item.get("text"):
                return item["text"]
        raise CodexTurnFailed("the codex turn produced no assistant message")

    def _await_notification(self, matches, timeout, mark):
        """Wait for the first notification at or after `mark` that matches.

        `mark` is an absolute position in the whole notification stream, so it
        stays valid across pruning; `_consumed` converts it to a list index.
        The mark is registered while waiting so pruning cannot drop out from
        under this scan.
        """

        def found():
            start = max(0, mark - self._consumed)
            return next((m for m in self._notifications[start:] if matches(m)), None)

        with self._cond:
            if not self._cond.wait_for(
                lambda: found() is not None or self._stopped, timeout=timeout
            ):
                return None
            return found()
