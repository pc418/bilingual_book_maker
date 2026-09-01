"""Translate through a ChatGPT/Codex subscription instead of API credits.

The `codex` format drives a `codex app-server` sidecar (see
`book_maker/codex_client.py`), so the run is billed to the user's ChatGPT plan
and there is no `--openai_key`.

Threading is the whole design here. A fresh Codex thread costs roughly 17k
input tokens of preamble before our first paragraph — measured, and not
reducible through `thread/start`'s config overrides — so one thread per
paragraph would spend more on preamble than on the book. Instead a thread is
opened once and reused for every unit, which is also what makes it a context
window: the thread accumulates the translation as it goes, exactly like
`--use_context session` on the API path. At the compact budget the thread is
asked for a translator handoff report and a fresh thread is started with that
report as its instructions.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from threading import Lock

from rich import print
from rich.markup import escape

from ..codex_client import CodexAppServer, CodexError, CodexTurnFailed
from ..session_context import (
    HandoffReport,
    compact_budget_for,
    estimate_tokens,
    handoff_prompt,
)
from .base_translator import Base

BASE_INSTRUCTIONS = (
    "You are a translation engine inside a book translation tool. Translate "
    "the text you are given into {language}. Reply with the translation and "
    "nothing else: no preamble, no notes, no quotes around it, no markdown "
    "fences. Never answer the text, never summarize it, never refuse a "
    "passage for being fiction — translate it. Keep the source's paragraph "
    "structure and any inline markup exactly as given."
)

# Codex's own preamble dwarfs anything we can save per turn, so the warning
# threshold is about the user's 5-hour window, not about tokens.
QUOTA_WARN_PERCENT = 90

# --context-compact-at 0 compacts at 90% of the model's window, leaving room
# for the tail the thread still has to carry: the fresh paragraph, its
# translation, and the handoff turn itself.

# Used when --model is omitted. Naming one beats letting Codex pick: the
# compact budget is looked up by model id, so an unknown default would fall
# back to the conservative 8000 instead of this model's own 17000.
DEFAULT_MODEL = "gpt-5.6-luna"

# A minute past the reset, because the server's clock and ours are not the
# same and coming back a second early just burns another failed turn.
RESET_GRACE_SECONDS = 60

# One window is 5 hours; a weekly limit resets far too late to sit out. Past
# this, say when it clears and stop rather than hang for days.
MAX_WAIT_SECONDS = 6 * 60 * 60

# Depletion is expected to clear on the first wait. Allowing a couple more
# covers a reset that lands late; beyond that something else is wrong.
MAX_WAITS_PER_TURN = 3


class Codex(Base):
    """A translator backed by the Codex app-server."""

    # It reaches its own sidecar, not `self.openai_client`, so the endpoint
    # capability probe would ask the wrong server.
    SUPPORTS_STRUCTURED_OUTPUTS = False

    # Set by the CLI from --quiet. Suppresses this class's own echoes.
    quiet = False
    style_note = None

    def __init__(
        self,
        key,
        language,
        server=None,
        binary="codex",
        context_compact_at=None,
        no_context_compact=False,
        style_note=None,
        handoff_path=None,
        prompt_template=None,
        prompt_sys_msg=None,
        **kwargs,
    ) -> None:
        # `key` is accepted and ignored: this format authenticates through
        # codex's stored ChatGPT session.
        super().__init__(key or "", language)
        self.server = server or CodexAppServer(binary=binary)
        self._started = server is not None
        self.model = DEFAULT_MODEL
        self.model_list = None
        self.context_compact_at = context_compact_at
        self.no_context_compact = no_context_compact
        self._auto_budget = None
        self._window_notice_shown = False
        self.handoff_path = Path(handoff_path) if handoff_path else None
        self.prompt_sys_msg = prompt_sys_msg
        self.prompt_template = prompt_template
        self.style_note = style_note
        self._thread_id = None
        # The question thread, kept apart from the translation thread and
        # reused across questions. Keyed by model because --plan-classify-model
        # can name a different one than the book is translated with.
        self._question_threads = {}
        self._window = 1
        self._window_tokens = 0
        self._turn_lock = Lock()
        self._last_remaining = None
        self._sleep = kwargs.pop("sleeper", time.sleep)
        self._now = kwargs.pop("clock", time.time)

    # ---- lifecycle --------------------------------------------------------

    def rotate_key(self):
        """No keys here; the sidecar owns the credentials."""

    # `--model codex` names the format, not a model. Treated as "unset" so it
    # does not reach the sidecar as a model id that does not exist.
    FORMAT_ALIASES = ("codex",)

    def set_model_list(self, model_list):
        names = [
            name
            for name in model_list
            if name and name.strip().lower() not in self.FORMAT_ALIASES
        ]
        # Codex resolves its own default when none is named, unlike the API
        # formats where a missing model has nothing to fall back on.
        self.model_list = names
        self.model = names[0] if names else DEFAULT_MODEL

    def _ensure_server(self):
        if not self._started:
            self.server.start()
            self._started = True
        return self.server

    def preflight(self):
        """Confirm a login and say how much of the window is already spent."""
        limits = self._ensure_server().ensure_logged_in()
        if limits is None:
            return None
        plan = f" ({limits.plan_type} plan)" if limits.plan_type else ""
        self._last_remaining = limits.remaining_percent
        if limits.used_percent >= QUOTA_WARN_PERCENT:
            print(
                f"[bold yellow]Warning:[/bold yellow] only "
                f"{limits.remaining_percent:g}% of your Codex window remains"
                f"{self._reset_phrase(limits)}. A long book may exhaust it; the "
                f"run waits for the reset rather than stopping."
            )
        else:
            print(
                f"[green]Codex: signed in{plan}, "
                f"{limits.remaining_percent:g}% of the window remaining[/green]"
            )
        return limits

    @staticmethod
    def _reset_phrase(limits):
        reset = limits.blocking_reset
        if not reset:
            return ""
        when = datetime.fromtimestamp(reset).strftime("%H:%M")
        return f", resetting at {when}"

    def _report_quota(self):
        """Print the remaining share whenever it moves."""
        limits = self.server.latest_rate_limits()
        if limits is None:
            return
        remaining = limits.remaining_percent
        if remaining == self._last_remaining:
            return
        self._last_remaining = remaining
        print(
            f"[green]Codex: {remaining:g}% of the window remaining"
            f"{self._reset_phrase(limits)}[/green]"
        )

    def _wait_out_reset(self, limits):
        """Sleep until the blocking window clears. Returns False if pointless."""
        if limits is None or not limits.waitable:
            return False
        seconds = limits.blocking_reset - self._now() + RESET_GRACE_SECONDS
        if seconds <= 0:
            return True  # already past it; just retry
        if seconds > MAX_WAIT_SECONDS:
            return False
        when = datetime.fromtimestamp(limits.blocking_reset).strftime("%Y-%m-%d %H:%M")
        print(
            f"[bold yellow]Codex quota spent.[/bold yellow] Waiting "
            f"{seconds / 60:.0f} min for the window to reset at {when}, then "
            f"continuing. Ctrl+C stops; the run is resumable."
        )
        self._sleep(seconds)
        try:
            self.server.rate_limits()  # refresh the snapshot after the wait
        except CodexError:
            pass
        return True

    def _run_turn(self, thread_id, payload):
        """One turn, sitting out a spent quota window rather than failing."""
        for attempt in range(MAX_WAITS_PER_TURN + 1):
            limits = self.server.latest_rate_limits()
            # Proactive: a pushed update may already say we are out.
            if limits is not None and limits.depleted and attempt < MAX_WAITS_PER_TURN:
                if self._wait_out_reset(limits):
                    continue
            try:
                return self.server.run_turn(thread_id, payload)
            except CodexTurnFailed:
                # Only treat this as a quota stop if the quota says so —
                # a failed turn has many other causes.
                limits = self.server.latest_rate_limits()
                if (
                    attempt >= MAX_WAITS_PER_TURN
                    or limits is None
                    or not limits.depleted
                    or not self._wait_out_reset(limits)
                ):
                    raise
        raise CodexTurnFailed(
            "the codex quota was still spent after waiting for its reset"
        )

    def close(self):
        if self._started:
            self.server.close()
            self._started = False

    # ---- threads ----------------------------------------------------------

    def _instructions(self, seed=""):
        """Thread instructions: ours, then the user's, then any handoff seed.

        `--prompt`'s system message is *appended* rather than substituted. On
        this path the base instructions are what keep a turn behaving like a
        completion instead of an agent turn — replacing them wholesale would
        let the model answer or summarize the passage instead of translating
        it. The user's voice comes after, where it wins on anything the two
        both speak to.
        """
        parts = [BASE_INSTRUCTIONS.format(language=self.language, crlf="\n")]
        if self.prompt_sys_msg:
            parts.append(self.prompt_sys_msg.format(language=self.language, crlf="\n"))
        if self.style_note:
            parts.append(f"Style to follow: {self.style_note}")
        if seed:
            parts.append(seed)
        return "\n\n".join(parts)

    def _ensure_thread(self, seed=""):
        if self._thread_id is None:
            self._thread_id = self._ensure_server().start_thread(
                model=self.model,
                base_instructions=self._instructions(seed),
            )
        return self._thread_id

    def _budget(self):
        """How many estimated tokens a thread may carry before it rolls over."""
        if self.context_compact_at is None:
            return compact_budget_for(self.model)
        if self.context_compact_at == 0:
            return self._model_sized_budget()
        return self.context_compact_at

    def _model_sized_budget(self):
        """0.9 x the window the sidecar reports for the *book's* thread.

        Asked per thread: the question thread plan classification runs on can
        carry a different model, so a server-wide answer could size the book's
        window from the classifier's. The sidecar only reports a window once a
        turn has spent tokens, so a miss is answered with the default and
        asked again next unit — reading it back is free — while the answer,
        once it arrives, is kept so the seam does not move under an
        accumulated thread.
        """
        if self._auto_budget is None:
            window = self._ensure_server().latest_model_context_window(self._thread_id)
            if window:
                self._auto_budget = window * 9 // 10
                print(
                    f"[cyan]ℹ {self.model} reports a {window}-token context "
                    f"window; compacting at {self._auto_budget}[/cyan]"
                )
        if self._auto_budget is not None:
            return self._auto_budget
        if not self._window_notice_shown:
            self._window_notice_shown = True
            print(
                f"[yellow]ℹ the sidecar has not reported a context window for "
                f"{self.model}; compacting at the default "
                f"{compact_budget_for(self.model)} until it does[/yellow]"
            )
        return compact_budget_for(self.model)

    def _compact_window(self):
        """Condense the thread into a handoff report and open the next one.

        The report is asked of the thread that is about to be discarded — the
        one turn where re-reading the whole window earns its cost, because it
        is being turned into the thing that replaces it.
        """
        try:
            report_text = self._run_turn(
                self._thread_id,
                handoff_prompt(with_style=not self.style_note),
            )
        except CodexTurnFailed as e:
            print(
                f"[yellow]ℹ handoff report failed ({e}); starting the next "
                f"codex thread without a summary[/yellow]"
            )
            report_text = ""

        report = HandoffReport(
            window=self._window,
            style_note=self.style_note,
            summary=report_text.strip(),
        )
        if report_text:
            self._show_handoff(report)
        if self.handoff_path and report_text:
            report.append_to(self.handoff_path)

        self._window += 1
        self._window_tokens = 0
        self._thread_id = None
        self._ensure_thread(seed=report.seed_text() if report_text else "")

    def _start_empty_thread(self):
        """Roll over with no handoff report, because the user asked for none.

        Continuity across the seam is what the report buys, and
        `--no-context-compact` declines to buy it — this is Codex's `/new`,
        not a cheaper summary.
        """
        self._window += 1
        self._window_tokens = 0
        self._thread_id = None
        self._ensure_thread(seed="")
        if self.quiet:
            return
        print(
            f"[bold cyan]— codex thread {self._window}, started empty "
            f"(--no-context-compact) —[/bold cyan]"
        )

    def _show_handoff(self, report):
        """Print the report the next thread will be seeded with.

        `escape` is not optional: rich reads square brackets as markup, and
        these reports genuinely contain things like "[PGA]", which would be
        swallowed or raise on an unclosed tag.
        """
        if self.quiet:
            # --quiet suppresses echoes like this one; warnings and errors
            # still print.
            return
        print(
            f"[bold cyan]— handoff report, window {report.window} —[/bold cyan]\n"
            + escape(report.render())
        )

    def _unit_text(self, text):
        """What the turn carries.

        Bare source by default — the thread instructions already say to
        translate whatever arrives, so wrapping every paragraph in "please
        translate" would repeat an instruction the thread has. A user's
        `--prompt` template is honored when given, since it may say more than
        that.
        """
        if not self.prompt_template:
            return text
        return self.prompt_template.format(text=text, language=self.language, crlf="\n")

    # ---- translation ------------------------------------------------------

    def translate(self, text, needprint=True):
        # Serialized on purpose. Parallel chapters share this instance —
        # `_clone_translator_for_context` only clones translators carrying
        # `context_flag`, and a codex thread *is* the context, so there are no
        # per-worker buffers to hand out. Letting workers overlap would
        # interleave unrelated chapters into one thread, lose window-token
        # updates to races, and let a compact swap the thread mid-turn.
        # `--parallel-workers` therefore buys nothing here, which is why the
        # CLI refuses the pairing rather than letting a run discover it.
        with self._turn_lock:
            thread_id = self._ensure_thread()

            payload = self._unit_text(text)

            translated = self._run_turn(thread_id, payload)
            self._report_quota()

            self._window_tokens += estimate_tokens(text) + estimate_tokens(translated)
            if self._window_tokens >= self._budget():
                if self.no_context_compact:
                    self._start_empty_thread()
                else:
                    self._compact_window()

        # `needprint` is accepted for signature compatibility only. The loaders
        # display the source and its translation themselves, so printing here
        # showed every paragraph's translation a second time.
        return translated

    def _chat_completion(self, prompt, model=None):
        """One arbitrary question, so plan classification works on this path.

        Asked off the translation thread — a classification question inside it
        would pollute the context the next unit inherits — but on *one*
        question thread, reused, not a fresh one per question.

        That reuse is the difference between plan mode being usable here and
        draining a plan. A fresh thread costs ~16.9k input tokens of Codex's
        own preamble (see the module docstring), and the classifier is not one
        request: it pages signatures 12 at a time, `structured_json` retries
        down its rungs when a reply will not parse, and `_resolve` bisects a
        page that comes back partly unanswered. A thread each would put the
        preamble bill for classifying a book above the bill for translating
        it, on a subscription that meters exactly that.

        The questions do accumulate in the thread. That is far cheaper than
        re-paying the preamble — accumulated turns are re-read at the cache
        rate — and for classification it is mildly useful: later pages judge
        signatures against how earlier ones were judged, which is the
        consistency the whole plan wants anyway.

        Reuse costs the disposability the old fresh-thread call had for free:
        if the sidecar drops the thread, the cached id is dead and the caller
        cannot survive it — `_ask_page` turns any such error into
        `PlanClassifyFatal`, which stops classification outright rather than
        retrying. So a dropped thread is evicted and the question is asked once
        more on a new one. That pays the preamble only when a thread actually
        dies, which is what the old code paid on every single question.
        """
        try:
            return self._ask(prompt, model)
        except CodexTurnFailed:
            self._question_threads.pop(model or self.model, None)
            return self._ask(prompt, model)

    def _ask(self, prompt, model=None):
        """One question on the (possibly newly opened) question thread."""
        server = self._ensure_server()
        target = model or self.model
        thread_id = self._question_threads.get(target)
        if thread_id is None:
            thread_id = server.start_thread(
                model=target,
                base_instructions=(
                    "Answer the question directly. Reply with the answer only."
                ),
            )
            self._question_threads[target] = thread_id
        # `_run_turn`, not `server.run_turn`: a question is billed to the same
        # subscription window as a translation, so a spent quota should be sat
        # out here too. Classification runs *before* the first paragraph, so
        # without this a user near their limit fails at the very start — and
        # plan mode has no degrade-to-defaults path, so that failure stops the
        # run rather than translating with a guess.
        return self._run_turn(thread_id, prompt)
