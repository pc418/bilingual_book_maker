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

from pathlib import Path

from rich import print

from ..codex_client import CodexAppServer, CodexTurnFailed
from ..glossary import Glossary
from ..session_context import (
    HandoffReport,
    compact_budget_for,
    estimate_tokens,
    handoff_prompt,
    parse_handoff_glossary,
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


class Codex(Base):
    """A translator backed by the Codex app-server."""

    # It reaches its own sidecar, not `self.openai_client`, so the endpoint
    # capability probe would ask the wrong server.
    SUPPORTS_STRUCTURED_OUTPUTS = False

    def __init__(
        self,
        key,
        language,
        server=None,
        binary="codex",
        context_compact_at=None,
        glossary=None,
        glossary_auto=False,
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
        self.model = None
        self.model_list = None
        self.context_compact_at = context_compact_at
        self.glossary = glossary or Glossary()
        self.glossary_auto = glossary_auto
        self.handoff_path = Path(handoff_path) if handoff_path else None
        self.prompt_sys_msg = prompt_sys_msg
        self._thread_id = None
        self._window = 1
        self._window_tokens = 0

    # ---- lifecycle --------------------------------------------------------

    def rotate_key(self):
        """No keys here; the sidecar owns the credentials."""

    def set_model_list(self, model_list):
        names = [name for name in model_list if name]
        # Codex resolves its own default when none is named, unlike the API
        # formats where a missing model has nothing to fall back on.
        self.model_list = names
        self.model = names[0] if names else None

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
        if limits.used_percent >= QUOTA_WARN_PERCENT:
            reset = f", resets at {limits.resets_at}" if limits.resets_at else ""
            print(
                f"[bold yellow]Warning:[/bold yellow] {limits.used_percent}% of "
                f"your Codex rate-limit window is already used{reset}. A long "
                f"book may exhaust it mid-run; the translation is resumable."
            )
        else:
            print(
                f"[green]Codex: signed in"
                + (f" ({limits.plan_type} plan)" if limits.plan_type else "")
                + f", {limits.used_percent}% of the window used[/green]"
            )
        return limits

    def close(self):
        if self._started:
            self.server.close()
            self._started = False

    # ---- threads ----------------------------------------------------------

    def _instructions(self, seed=""):
        base = (self.prompt_sys_msg or BASE_INSTRUCTIONS).format(
            language=self.language, crlf="\n"
        )
        return f"{base}\n\n{seed}" if seed else base

    def _ensure_thread(self, seed=""):
        if self._thread_id is None:
            self._thread_id = self._ensure_server().start_thread(
                model=self.model,
                base_instructions=self._instructions(seed),
            )
        return self._thread_id

    def _budget(self):
        return (
            self.context_compact_at
            if self.context_compact_at is not None
            else compact_budget_for(self.model)
        )

    def _compact_window(self):
        """Condense the thread into a handoff report and open the next one.

        The report is asked of the thread that is about to be discarded — the
        one turn where re-reading the whole window earns its cost, because it
        is being turned into the thing that replaces it.
        """
        try:
            report_text = self.server.run_turn(
                self._thread_id, handoff_prompt(with_glossary=self.glossary_auto)
            )
        except CodexTurnFailed as e:
            print(
                f"[yellow]ℹ handoff report failed ({e}); starting the next "
                f"codex thread without a summary[/yellow]"
            )
            report_text = ""

        glossary_lines = ""
        if self.glossary_auto and report_text:
            learned = parse_handoff_glossary(report_text)
            if learned:
                merged, conflicts = self.glossary.merge(learned)
                self.glossary = merged
                glossary_lines = learned.to_lines()
                for conflict in conflicts:
                    print(
                        f"[yellow]ℹ glossary conflict — {conflict.describe()}[/yellow]"
                    )

        report = HandoffReport(
            window=self._window,
            summary=report_text.strip(),
            glossary_lines=glossary_lines,
        )
        if self.handoff_path and report_text:
            report.append_to(self.handoff_path)

        self._window += 1
        self._window_tokens = 0
        self._thread_id = None
        self._ensure_thread(seed=report.seed_text() if report_text else "")

    # ---- translation ------------------------------------------------------

    def translate(self, text, needprint=True):
        thread_id = self._ensure_thread()

        # Pinned terms belong to this unit, so they ride with it rather than
        # with the thread instructions, which every later turn would re-read.
        block = self.glossary.prompt_block(text) if self.glossary else ""
        payload = f"{block}\n\n{text}" if block else text

        translated = self.server.run_turn(thread_id, payload)
        if needprint:
            print(f"[bold green]{translated}[/bold green]")

        self._window_tokens += estimate_tokens(text) + estimate_tokens(translated)
        if self._window_tokens >= self._budget():
            self._compact_window()
        return translated

    def _chat_completion(self, prompt, model=None):
        """One arbitrary question, so plan classification works on this path.

        Asked on its own thread: a classification question inside the
        translation thread would pollute the context the next unit inherits.
        """
        server = self._ensure_server()
        thread_id = server.start_thread(
            model=model or self.model,
            base_instructions="Answer the question directly. Reply with the answer only.",
        )
        return server.run_turn(thread_id, prompt)
