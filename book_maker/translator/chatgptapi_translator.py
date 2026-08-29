import re
import time
import os
import shutil
from os import environ
from itertools import cycle
import json
from functools import lru_cache
from pathlib import Path
from threading import Lock

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    BadRequestError,
    InternalServerError,
    LengthFinishReasonError,
    NotFoundError,
    OpenAI,
    RateLimitError,
)
from pydantic import ConfigDict, Field, ValidationError, create_model
from rich import print
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    retry_if_not_exception_type,
)

from .base_translator import (
    AsyncTranslationUnsupported,
    Base,
    TranslationContext,
    TranslationResult,
)
from .capabilities import (
    ENTRY_RUNG,
    RUNG_REFUSAL_ERRORS,
    CapabilityLedger,
    StructuredOutputUnsupported,
    classify_bad_request,
    probe_structured_output,
    verify_models,
)
from ..structured import (
    RungRejected,
    extract_json_object,
    prompt_with_schema,
    unwrap_schema_echo,
)
from ..config import config
from ..glossary import Glossary
from ..session_context import (
    HandoffReport,
    SessionHistory,
    compact_budget_for,
    handoff_prompt,
    parse_handoff_glossary,
    strip_handoff_glossary,
)

CHATGPT_CONFIG = config["translator"]["chatgptapi"]

PROMPT_ENV_MAP = {
    "user": "BBM_CHATGPTAPI_USER_MSG_TEMPLATE",
    "system": "BBM_CHATGPTAPI_SYS_MSG",
}


# The schema is the last thing the model reads before it decodes, and a bare
# `translated`/`paragraphs` field says nothing about *which* language to produce
# — the target language would otherwise live only in the middle of the prompt.
# So the language is baked into the field name, its description and the schema
# name: `simplified chinese` -> `simplified_chinese_translation`. Separator is
# `_` rather than `-`; hyphens are legal JSON keys, but this file exists because
# OpenAI-compatible proxies mishandle things, and `_` is the conservative shape.
def _language_slug(language):
    """Field-name token for `language`, or "" when there is nothing usable."""
    return re.sub(r"[^a-z0-9]+", "_", (language or "").strip().lower()).strip("_")


def single_field_name(language):
    """Name of the single-translation field for `language`.

    Shared by the SDK path and the hand-built Batch API schema so the two cannot
    drift; the Batch API reader keys off this too.
    """
    slug = _language_slug(language)
    return f"{slug}_translation" if slug else "translated"


def batch_field_name(language):
    """Name of the batch-translation field for `language`."""
    slug = _language_slug(language)
    return f"{slug}_paragraphs" if slug else "paragraphs"


# The schema name is sent to the model, which never has to tell the single
# schema from the batch one -- a request carries exactly one. So name each
# schema after the field it wraps rather than after our own call sites.
@lru_cache(maxsize=None)
def single_translation_model(language):
    """Structured single translation output, pinned to `language`."""
    field = single_field_name(language)
    return create_model(
        field,
        __config__=ConfigDict(extra="forbid"),
        **{
            field: (
                str,
                Field(description=_single_field_description(language)),
            )
        },
    )


@lru_cache(maxsize=None)
def batch_translation_model(language):
    """Structured batch translation output, pinned to `language`."""
    field = batch_field_name(language)
    return create_model(
        field,
        __config__=ConfigDict(extra="forbid"),
        **{
            field: (
                list[str],
                Field(description=_batch_field_description(language)),
            )
        },
    )


def _single_field_description(language):
    target = language or "the target language"
    return f"The source text translated into {target}."


def _batch_field_description(language):
    target = language or "the target language"
    return (
        f"The source paragraphs translated into {target}, one per input "
        f"paragraph and in the same order."
    )


@lru_cache(maxsize=None)
def single_translation_schema(language):
    """Mirror of `single_translation_model` for the Batch API.

    Batch JSONL bodies are built by hand and so cannot use the SDK's Pydantic
    support; both sides take their field name from `single_field_name`.
    """
    field = single_field_name(language)
    return {
        "name": field,
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                field: {
                    "type": "string",
                    "description": _single_field_description(language),
                }
            },
            "required": [field],
            "additionalProperties": False,
        },
    }


class ChatGPTAPI(Base):
    DEFAULT_PROMPT = "Please help me to translate,`{text}` to {language}, please return only translated content not include the origin text"

    # Subclasses that do not route through `self.openai_client` must opt out:
    # probing them would send the capability request to the wrong endpoint.
    SUPPORTS_STRUCTURED_OUTPUTS = True

    # Session-mode state, declared here so the window-mode path is well
    # defined on any instance — including the subclasses and test fixtures
    # that build one without running __init__. `session is None` means window
    # mode everywhere in this class.
    session = None
    glossary = None
    pinned = None
    learned = None
    glossary_auto = False
    handoff_path = None
    context_compact_at = None
    context_mode = "window"

    def __init__(
        self,
        key,
        language,
        api_base=None,
        prompt_template=None,
        prompt_sys_msg=None,
        temperature=1.0,
        context_flag=False,
        context_paragraph_limit=0,
        context_mode="window",
        context_compact_at=None,
        glossary=None,
        glossary_auto=False,
        handoff_path=None,
        extra_body=None,
        **kwargs,
    ) -> None:
        super().__init__(key, language)
        self.key_len = len(key.split(","))
        self.openai_client = OpenAI(api_key=next(self.keys), base_url=api_base)
        self.api_base = api_base

        self.prompt_template = (
            prompt_template
            or environ.get(PROMPT_ENV_MAP["user"])
            or self.DEFAULT_PROMPT
        )
        self.prompt_sys_msg = (
            prompt_sys_msg
            or environ.get(
                "OPENAI_API_SYS_MSG",
            )  # XXX: for backward compatibility, deprecate soon
            or environ.get(PROMPT_ENV_MAP["system"])
            or ""
        )
        self.system_content = environ.get("OPENAI_API_SYS_MSG") or ""
        self.temperature = temperature
        self.model_list = None
        self.context_flag = context_flag
        self.context_list = []
        self.context_translated_list = []
        # Session mode replaces the window entirely; `session is None` is the
        # single test for "are we in window mode" everywhere below.
        self.context_mode = context_mode or "window"
        self.session = (
            SessionHistory()
            if context_flag and self.context_mode == "session"
            else None
        )
        self.context_compact_at = context_compact_at
        # `pinned` is the author's --glossary file and never changes.
        # `learned` accumulates what compacts establish. `glossary` is the two
        # combined, pins on top, and is what gets injected per unit.
        self.pinned = glossary or Glossary()
        self.learned = Glossary()
        self.glossary = self.pinned
        self.glossary_auto = glossary_auto
        self.handoff_path = Path(handoff_path) if handoff_path else None
        self._session_cache_warned = False
        self._session_cache_seen = False
        self._session_requests = 0
        self._compact_failures = 0
        if context_paragraph_limit > 0:
            # not set by user, use default
            self.context_paragraph_limit = context_paragraph_limit
        else:
            # set by user, use user's value
            self.context_paragraph_limit = CHATGPT_CONFIG["context_paragraph_limit"]
        self.batch_text_list = []
        self.batch_info_cache = None
        self.result_content_cache = {}
        self._api_lock = Lock()
        self._async_clients = {}
        self.extra_body = extra_body or {}

        # What this endpoint turned out to support, learned at runtime and
        # keyed by model because --model_list rotates across models of
        # differing capability.
        self.capabilities = CapabilityLedger()
        self.model = (
            None  # Will be set by rotate_model() after model_list is initialized
        )

    def _probe_verdict(self, model=None):
        """The endpoint's graded schema support, probed once per model.

        One of "strict", "shape", "json" or False. Subclasses that do not route
        through `self.openai_client` probe nothing: sending the capability
        request to the wrong endpoint would answer about the wrong server.
        """
        model = model or self.model
        probe = self._probe if self.SUPPORTS_STRUCTURED_OUTPUTS else None
        return self.capabilities.ensure_verdict(model, probe)

    def _probe(self, model):
        return probe_structured_output(self.openai_client, model)

    def _ensure_structured_support(self, model=None):
        """Whether *translation* may use a schema. Only "strict" qualifies.

        Our translation schema pins the target language as a value constraint
        (#544), so an endpoint that honors shape but ignores values gives us a
        schema that cannot do the one job we added it for — worse than the
        delimiter method, which at least states the language in the prompt.

        Classification does not come through here at all: it needs a JSON
        object with legal values, not an endpoint that applied our schema, so
        the verdict only picks its entry rung (`structured_rungs`).
        """
        return self._probe_verdict(model) == "strict"

    def _structured_enabled(self):
        return self.capabilities.verdicts.get(self.model, False) == "strict"

    def _note_structured_success(self):
        """A working structured call clears the model's failure streak."""
        self.capabilities.note_success(self.model)

    def _demote_structured_outputs(self, reason):
        """Count a capability failure and, on a streak, stop paying for it."""
        self.capabilities.demote(self.model, reason)

    # Hoisted to `structured.py` — every provider's bottom rung needs it.
    _extract_json_object = staticmethod(extract_json_object)

    def structured_rungs(self, prompt, schema, model=None):
        """json_schema -> json_object + described schema -> plain prompt.

        A real ladder now: `run_rungs` descends whenever a rung is refused or
        answers unusably, so an endpoint that accepts the one-key probe schema
        and then rejects a twelve-property one still classifies, and a
        `strict` endpoint that returns prose falls through instead of aborting
        the run.
        """
        target = model or self.model
        ladder = [
            ("json_schema", lambda: self._json_schema_rung(prompt, schema, target)),
            ("json_object", lambda: self._json_object_rung(prompt, schema, target)),
            ("prompt", lambda: self._prompt_rung(prompt, schema, target)),
        ]
        entry = ENTRY_RUNG.get(self._probe_verdict(target), "prompt")
        start = next(i for i, (name, _) in enumerate(ladder) if name == entry)
        return ladder[start:]

    def _completion_text(self, model, content, **kwargs):
        """One single-turn request, with shape refusals marked as such."""
        try:
            completion = self.openai_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": content}],
                **kwargs,
            )
        except RUNG_REFUSAL_ERRORS as e:
            raise RungRejected(e) from e
        return completion.choices[0].message.content

    def _json_schema_rung(self, prompt, schema, model):
        text = self._completion_text(
            model,
            prompt,
            response_format={"type": "json_schema", "json_schema": schema},
        )
        return unwrap_schema_echo(extract_json_object(text))

    def _json_object_rung(self, prompt, schema, model):
        text = self._completion_text(
            model,
            prompt_with_schema(prompt, schema),
            response_format={"type": "json_object"},
        )
        return unwrap_schema_echo(extract_json_object(text))

    def _chat_completion(self, prompt, model=None):
        return self._completion_text(model or self.model, prompt)

    def rotate_key(self):
        with self._api_lock:
            self.openai_client.api_key = next(self.keys)

    def rotate_model(self):
        with self._api_lock:
            if self.model_list:
                self.model = next(self.model_list)

    def _user_content(self, text):
        """The user message for one unit: pinned terms plus the prompt.

        Deterministic for a given (text, glossary), which is what lets session
        mode store exactly what it sent without threading the string around.
        """
        content = self.prompt_template.format(
            text=text, language=self.language, crlf="\n"
        )
        # Pinned terms belong to *this* unit, so they go in the fresh tail
        # message rather than the system prompt: a block that varies per unit
        # sitting in a fixed position would invalidate the cached prefix on
        # every request. Once this message is frozen into the history it stops
        # varying, so it is stable there.
        glossary_block = self.glossary.prompt_block(text) if self.glossary else ""
        return f"{glossary_block}\n\n{content}" if glossary_block else content

    def create_messages(self, text, intermediate_messages=None):
        content = self._user_content(text)

        sys_content = self.system_content or self.prompt_sys_msg.format(crlf="\n")
        messages = [
            {"role": "system", "content": sys_content},
        ]

        if intermediate_messages:
            messages.extend(intermediate_messages)

        messages.append({"role": "user", "content": content})
        return messages

    def create_context_messages(self, context: TranslationContext | None = None):
        messages = []
        if self.session is not None:
            # Session mode: the whole append-only history is the prefix. The
            # per-unit window below does not apply — that is the mode this
            # one replaces.
            return self.session.messages()
        if self.context_flag:
            if context is None:
                source_texts = self.context_list
                translated_texts = self.context_translated_list
            else:
                source_texts = context.source_texts
                translated_texts = context.translated_texts
                if not source_texts:
                    return messages
            messages.append({"role": "user", "content": "\n".join(source_texts)})
            messages.append(
                {
                    "role": "assistant",
                    "content": "\n".join(translated_texts),
                }
            )
        return messages

    def _create_async_client(self, key):
        return AsyncOpenAI(api_key=key, base_url=self.api_base)

    def _get_async_client(self, key):
        cache_key = (self.api_base, key)
        with self._api_lock:
            if cache_key not in self._async_clients:
                self._async_clients[cache_key] = self._create_async_client(key)
            return self._async_clients[cache_key]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type(
            (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError)
        ),
        reraise=True,
    )
    async def translate_async(
        self, text: str, *, context: TranslationContext | None = None
    ) -> TranslationResult:
        if self.session is not None:
            # This path threads an immutable per-call TranslationContext,
            # which is the opposite of one shared append-only history: the
            # session would never be appended to, and every request would be
            # billed as an uncached fresh prefix. No loader uses this path
            # today; failing here keeps that from becoming a silent cost
            # regression the moment one does.
            raise AsyncTranslationUnsupported(
                "session context is not supported on the async path; "
                "use --use_context (window mode) there"
            )
        if type(self).create_chat_completion is not ChatGPTAPI.create_chat_completion:
            return await super().translate_async(text, context=context)

        with self._api_lock:
            key = next(self.keys)
            if self.model_list:
                model = (
                    next(self.model_list)
                    if hasattr(self.model_list, "__next__")
                    else self.model_list[0]
                )
            else:
                model = self.model

        current_context = context or TranslationContext()
        messages = self.create_messages(
            text, self.create_context_messages(current_context)
        )
        client = self._get_async_client(key)

        async def create(sampling):
            return await client.chat.completions.create(
                model=model,
                messages=messages,
                extra_body=self.extra_body if self.extra_body else None,
                **sampling,
            )

        try:
            completion = await create(self._sampling_kwargs(model))
        except BadRequestError as e:
            if classify_bad_request(e) != "temperature":
                raise
            self._note_temperature_rejected(model)
            completion = await create({})

        translated = completion.choices[0].message.content or ""
        if self.context_flag:
            current_context = current_context.append(
                text, translated, self.context_paragraph_limit
            )
        return TranslationResult(translated, current_context)

    async def close_async(self) -> None:
        clients = list(self._async_clients.values())
        self._async_clients.clear()
        for client in clients:
            await client.close()

    def _sampling_kwargs(self, model=None):
        """Sampling parameters to send, or nothing when the model owns them."""
        return self.capabilities.sampling_kwargs(model or self.model, self.temperature)

    _classify_bad_request = staticmethod(classify_bad_request)

    def _note_temperature_rejected(self, model):
        if self.capabilities.note_temperature_rejected(model):
            print(
                f"[yellow]ℹ '{model}' rejected temperature={self.temperature}; "
                f"retrying with the model default[/yellow]"
            )

    def _request(self, call, model=None):
        """Issue an API call, retrying once without temperature if refused."""
        model = model or self.model
        try:
            return call(self._sampling_kwargs(model))
        except BadRequestError as e:
            if classify_bad_request(e) != "temperature":
                raise
            self._note_temperature_rejected(model)
            return call({})

    def create_chat_completion(self, text):
        """Plain (delimiter-mode) completion. Overridden by some subclasses."""
        messages = self.create_messages(text, self.create_context_messages())

        return self._request(
            lambda sampling: self.openai_client.chat.completions.create(
                model=self.model,
                messages=messages,
                extra_body=self.extra_body if self.extra_body else None,
                **sampling,
            )
        )

    def _structured_single_translation(self, text):
        """Translate one paragraph via Structured Outputs.

        Raises `StructuredOutputUnsupported` when the endpoint turns out not to
        honor the schema, `LengthFinishReasonError` when the answer was cut off
        (never returns the truncated JSON fragment), and `ValueError` on refusal.
        """
        messages = self.create_messages(text, self.create_context_messages())
        field = single_field_name(self.language)

        try:
            completion = self._request(
                lambda sampling: self.openai_client.chat.completions.parse(
                    model=self.model,
                    messages=messages,
                    response_format=single_translation_model(self.language),
                    extra_body=self.extra_body if self.extra_body else None,
                    **sampling,
                )
            )
        except BadRequestError as e:
            if self._classify_bad_request(e) != "schema":
                raise  # not a capability answer — do not blame the schema
            raise StructuredOutputUnsupported(str(e)) from e
        except (ValidationError, json.JSONDecodeError) as e:
            # Answered with something that is not the schema.
            raise StructuredOutputUnsupported(str(e)) from e

        message = completion.choices[0].message
        if getattr(message, "refusal", None):
            raise ValueError(f"Model refused to translate: {message.refusal}")
        if message.parsed is None:
            raise StructuredOutputUnsupported("no parsed content in response")

        self._note_structured_success()
        return getattr(message.parsed, field)

    def _plain_translation(self, text):
        completion = self.create_chat_completion(text)
        self._note_cache_usage(completion)
        content = completion.choices[0].message.content
        return content.encode("utf8").decode() if content else ""

    # ---- session mode -----------------------------------------------------

    # How many requests to allow before concluding the endpoint is not billing
    # cache reads. One miss is normal (nothing is cached yet), and short books
    # should not be nagged.
    CACHE_WARN_AFTER = 10

    # Compact attempts before giving up on a summary and starting clean. More
    # than one so a transient error does not cost the accumulated context;
    # bounded so a broken endpoint cannot grow the history forever.
    COMPACT_ATTEMPTS = 3

    def _note_cache_usage(self, completion):
        """Warn once if session mode never gets a cache read billed back.

        Without pass-through caching this mode re-reads the whole history at
        full input price on every request — strictly worse than the window
        mode it replaces. That is invisible in the output and only shows up on
        the bill, so it has to be said out loud.
        """
        if self.session is None or self._session_cache_warned:
            return
        usage = getattr(completion, "usage", None)
        details = getattr(usage, "prompt_tokens_details", None)
        if getattr(details, "cached_tokens", 0):
            self._session_cache_seen = True
            return
        self._session_requests += 1
        if self._session_cache_seen or self._session_requests < self.CACHE_WARN_AFTER:
            return
        self._session_cache_warned = True
        print(
            "[bold yellow]Warning:[/bold yellow] this endpoint has not reported "
            "a single cached prompt token after "
            f"{self._session_requests} requests. Session mode assumes prompt "
            "caching is billed through; without it the history is charged at "
            "full price every request. Consider --use_context (window mode)."
        )

    def _session_budget(self):
        return (
            self.context_compact_at
            if self.context_compact_at is not None
            else compact_budget_for(self.model)
        )

    def _compact_session(self):
        """Ask for a handoff report, then start the next window seeded with it.

        The report is requested on top of the existing history — that is the
        one turn where the whole window is worth re-reading, because it is
        being condensed into what replaces it.
        """
        budget = self._session_budget()
        prompt = handoff_prompt(with_glossary=self.glossary_auto)
        messages = [
            *self.session.messages(),
            {"role": "user", "content": prompt},
        ]
        try:
            completion = self._request(
                lambda sampling: self.openai_client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    extra_body=self.extra_body if self.extra_body else None,
                    **sampling,
                )
            )
            report_text = completion.choices[0].message.content or ""
        except Exception as e:
            # Keep the window. One rate-limited or dropped request is not a
            # reason to throw away a book's worth of accumulated context — the
            # budget stays exceeded, so the next unit simply tries again.
            self._compact_failures += 1
            # Give up on attempts, or as soon as the window has outgrown its
            # budget badly enough that retrying is the wrong bet: a compact
            # that fails because the history is too long will keep failing,
            # and the translation requests carrying that history fail with it.
            give_up = (
                self._compact_failures >= self.COMPACT_ATTEMPTS
                or self.session.estimated_tokens() > 2 * budget
            )
            print(
                f"[yellow]ℹ handoff report failed ({e}); "
                + (
                    "starting the next context window without a summary"
                    if give_up
                    else "keeping the current context and retrying on the next paragraph"
                )
                + "[/yellow]"
            )
            if give_up:
                # Bounded: without this the history would grow past the
                # budget forever on a persistently failing endpoint.
                self._compact_failures = 0
                self.session.reset(seed="")
            return
        self._compact_failures = 0

        glossary_lines = ""
        if self.glossary_auto:
            learned, source = parse_handoff_glossary(report_text)
            if source == "scanned":
                # The block is what makes this parseable; say so rather than
                # let a quietly degraded recovery look like a clean one.
                print(
                    f"[yellow]ℹ the handoff report left out its <renderings> "
                    f"block; recovered {len(learned)} terms from loose "
                    f"lines[/yellow]"
                )
            elif source == "missing":
                print(
                    "[yellow]ℹ the handoff report established no renderings; "
                    "this window carries no learned terms[/yellow]"
                )
            if learned:
                # This window's reading wins over earlier ones: the model has
                # seen more of the book than it had last time. Then the
                # author's pins are laid over the top, so a term they chose
                # never drifts, while everything else keeps improving.
                self.learned, _ = learned.merge(self.learned)
                self.glossary, conflicts = self.pinned.merge(self.learned)
                glossary_lines = self.glossary.to_lines()
                for conflict in conflicts:
                    print(
                        f"[yellow]ℹ glossary conflict — {conflict.describe()}[/yellow]"
                    )

        report = HandoffReport(
            window=self.session.windows,
            # The JSON block is parsed into `glossary_lines` below, so it is
            # stripped from the prose rather than stored and re-seeded twice.
            summary=strip_handoff_glossary(report_text),
            glossary_lines=glossary_lines,
        )
        if self.handoff_path:
            try:
                report.append_to(self.handoff_path)
            except OSError as e:
                # The paragraph is already translated and billed. Failing here
                # would send get_translation's retry policy round again and
                # pay for the same paragraph up to three more times.
                print(
                    f"[yellow]ℹ could not write {self.handoff_path} ({e}); "
                    f"the run continues without a saved handoff[/yellow]"
                )
        self.session.reset(seed=report.seed_text())

    def _save_session_context(self, text, t_text):
        # Store what was *sent*, not the bare source. The next request replays
        # this message verbatim, so any difference — the prompt template, a
        # glossary block — would make the newest pair a cache miss, and the
        # run would re-read a paragraph at full input price every request.
        self.session.append(self._user_content(text), t_text)
        if self.session.should_compact(self._session_budget()):
            self._compact_session()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_exception_type((RateLimitError, Exception)),
        reraise=True,
    )
    def get_translation(self, text):
        self.rotate_key()
        self.rotate_model()  # rotate all the model to avoid the limit

        if self._ensure_structured_support():
            try:
                t_text = self._structured_single_translation(text)
            except StructuredOutputUnsupported as e:
                self._demote_structured_outputs(e)
                t_text = self._plain_translation(text)
            except LengthFinishReasonError:
                # The answer was cut off mid-JSON. Nothing partial may be used,
                # but the plain path has no JSON to truncate — retranslate there
                # rather than ending a multi-hour run over one paragraph.
                print(
                    "[yellow]ℹ structured answer was truncated; retranslating "
                    "this paragraph without a schema[/yellow]"
                )
                t_text = self._plain_translation(text)
        else:
            t_text = self._plain_translation(text)

        if self.context_flag:
            self.save_context(text, t_text)

        return t_text

    def save_context(self, text, t_text):
        if self.session is not None:
            self._save_session_context(text, t_text)
            return
        if self.context_paragraph_limit > 0:
            self.context_list.append(text)
            self.context_translated_list.append(t_text)
            # Remove the oldest context
            if len(self.context_list) > self.context_paragraph_limit:
                self.context_list.pop(0)
                self.context_translated_list.pop(0)

    def translate(self, text, needprint=True):
        try:
            t_text = self.get_translation(text)
            return t_text
        except Exception as e:
            print(f"Translation failed after retries: {e}")
            raise

    def translate_and_split_lines(self, text):
        result_str = self.translate(text, False)
        lines = result_str.splitlines()
        lines = [line.strip() for line in lines if line.strip() != ""]
        return lines

    def log_retry(self, state, retry_count, elapsed_time, log_path="log/buglog.txt"):
        if retry_count == 0:
            return
        print(f"retry {state}")
        with open(log_path, "a", encoding="utf-8") as f:
            print(
                f"retry {state}, count = {retry_count}, time = {elapsed_time:.1f}s",
                file=f,
            )

    def log_translation_mismatch(
        self,
        plist_len,
        result_list,
        new_str,
        sep,
        log_path="log/buglog.txt",
    ):
        if len(result_list) == plist_len:
            return
        newlist = new_str.split(sep)
        with open(log_path, "a", encoding="utf-8") as f:
            print(f"problem size: {plist_len - len(result_list)}", file=f)
            for i in range(len(newlist)):
                print(newlist[i], file=f)
                print(file=f)
                if i < len(result_list):
                    print("............................................", file=f)
                    print(result_list[i], file=f)
                    print(file=f)
                print("=============================", file=f)

        print(
            f"bug: {plist_len} paragraphs of text translated into {len(result_list)} paragraphs",
        )
        print("continue")

    def join_lines(self, text):
        lines = text.splitlines()
        new_lines = []
        temp_line = []

        # join
        for line in lines:
            if line.strip():
                temp_line.append(line.strip())
            else:
                if temp_line:
                    new_lines.append(" ".join(temp_line))
                    temp_line = []
                new_lines.append(line)

        if temp_line:
            new_lines.append(" ".join(temp_line))

        text = "\n".join(new_lines)
        # try to fix #372
        if not text:
            return ""

        # del ^M
        text = text.replace("^M", "\r")
        lines = text.splitlines()
        filtered_lines = [line for line in lines if line.strip() != "\r"]
        new_text = "\n".join(filtered_lines)

        return new_text

    def translate_list(self, text_list):
        """
        Translate multiple texts using the best available method.
        Priority: 1. Structured Outputs (strict) -> 2. Delimiter-based
        Returns a list of translated texts.
        """
        # Use structured outputs if available (probed once per model)
        if self._ensure_structured_support():
            return self._do_structured_batch_translate(text_list)

        # Fallback to delimiter-based method
        return self._do_batch_translate(
            text_list,
            self.prompt_template,
            self.system_content,
            self.DEFAULT_PROMPT,
            lambda text: self.translate(text, False),
        )

    def _create_structured_batch_messages(self, text_list):
        """Create messages for structured batch translation"""
        plist_len = len(text_list)

        # Build the user message with all texts, incorporating user's prompt template
        texts_json = json.dumps(text_list, ensure_ascii=False)

        # Format user's prompt template with the JSON array as {text}
        user_prompt = self.prompt_template.format(
            text=texts_json, language=self.language, crlf="\n"
        )

        # Add structured format instruction. The target language goes last: this
        # is the final thing the model reads before decoding, and a shape-only
        # tail leaves `{language}` buried behind the source JSON blob above.
        field = batch_field_name(self.language)
        content = (
            f"{user_prompt}\n\n"
            f"Return a JSON object whose '{field}' array contains EXACTLY "
            f"{plist_len} strings, one per input paragraph and in the same "
            f"order, each written in {self.language}."
        )

        sys_content = self.system_content or self.prompt_sys_msg.format(crlf="\n")

        messages = [
            {"role": "system", "content": sys_content},
        ]

        if self.context_flag:
            messages.extend(self.create_context_messages())

        messages.append({"role": "user", "content": content})
        return messages

    def _do_structured_batch_translate(self, text_list):
        """Batch translate using structured outputs"""
        plist_len = len(text_list)

        if plist_len == 0:
            return []

        if plist_len == 1:
            return [self.get_translation(text_list[0])]

        try:
            result = self._execute_structured_batch_translate(text_list, plist_len)
            return result
        except StructuredOutputUnsupported as e:
            # Capability answer, not a transient failure: stop paying for it.
            self._demote_structured_outputs(e)
            return [self.translate(t, False) for t in text_list]
        except Exception as e:
            print(
                f"[yellow]Structured batch translation failed after retries: {e}. "
                f"Falling back to one-by-one translation.[/yellow]"
            )
            return [self.translate(t, False) for t in text_list]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        retry=retry_if_not_exception_type(StructuredOutputUnsupported),
        reraise=True,
    )
    def _execute_structured_batch_translate(self, text_list, plist_len):
        """Execute the actual structured batch translation with tenacity retry"""
        self.rotate_key()
        self.rotate_model()
        if not self._ensure_structured_support(self.model):
            # eligibility was decided for the model current at call time, but
            # rotation may have moved us to a different one: a model that
            # never passed the probe must not be handed a schema
            raise StructuredOutputUnsupported(
                f"'{self.model}' has no strict structured-output support"
            )

        messages = self._create_structured_batch_messages(text_list)

        try:
            completion = self._request(
                lambda sampling: self.openai_client.chat.completions.parse(
                    model=self.model,
                    messages=messages,
                    response_format=batch_translation_model(self.language),
                    extra_body=self.extra_body if self.extra_body else None,
                    **sampling,
                )
            )
        except BadRequestError as e:
            if self._classify_bad_request(e) != "schema":
                raise  # not a capability answer — do not blame the schema
            raise StructuredOutputUnsupported(str(e)) from e
        except (ValidationError, json.JSONDecodeError) as e:
            raise StructuredOutputUnsupported(str(e)) from e

        message = completion.choices[0].message
        if getattr(message, "refusal", None):
            raise ValueError(f"Model refused to translate: {message.refusal}")
        if message.parsed is None:
            raise StructuredOutputUnsupported("no parsed content in response")

        paragraphs = getattr(message.parsed, batch_field_name(self.language))

        # A wrong count is a model error, not a capability answer: retry it.
        if len(paragraphs) != plist_len:
            raise ValueError(
                f"Expected {plist_len} translations, got {len(paragraphs)}"
            )

        # Count is not alignment. A model that merges two source lines into
        # one slot (routine on verse: one sentence spans two pādas) keeps
        # the count by shifting the rest and padding a slot with "" — the
        # only unambiguous symptom of the shift. An empty slot for a
        # non-empty input is therefore a misaligned window, never a valid
        # translation: retry it.
        empty_slots = [
            i
            for i, (src, out) in enumerate(zip(text_list, paragraphs))
            if not out.strip() and src.strip()
        ]
        if empty_slots:
            raise ValueError(
                f"Empty translation for non-empty paragraph(s) {empty_slots}: "
                f"batch alignment lost"
            )

        if self.context_flag:
            for orig, trans in zip(text_list, paragraphs):
                self.save_context(orig, trans)

        self._note_structured_success()
        return paragraphs

    def _validate_custom_models(self, custom_model_list):
        """Which of these models this endpoint will serve (see capabilities)."""
        return verify_models(self.openai_client, custom_model_list)

    def set_model_list(self, model_list):
        """The only way models get set: whatever the user named, in that order.

        No name is special. Rotation follows the order given — the old
        `set()` pass made it depend on hash order, so the same command could
        start on a different model between runs.
        """
        seen = {}
        for name in model_list:
            name = (name or "").strip()
            if name:
                seen.setdefault(name, None)
        model_list = list(seen)
        if not model_list:
            raise ValueError(
                "Empty model list provided. Use --model_list with at least one model name."
            )

        validation_result = self._validate_custom_models(model_list)
        if not validation_result["success"]:
            raise ValueError(
                f"Custom model validation failed. "
                f"Requested: {model_list}. "
                f"Unavailable: {validation_result['unavailable_models']}. "
                f"Available models in API: {validation_result['api_models']}. "
                f"Check your model name, API key, and permissions."
            )
        # If some models were partially available, use only the available ones
        if validation_result["unavailable_models"]:
            model_list = [
                m for m in model_list if m in set(validation_result["available_models"])
            ]

        print(f"Using model list {model_list}")
        self.model_list = cycle(model_list)
        # Set the initial model so it is available before rotate_model() runs.
        self.model = model_list[0]

    def batch_init(self, book_name):
        self.book_name = self.sanitize_book_name(book_name)

    def add_to_batch_translate_queue(self, book_index, text):
        self.batch_text_list.append({"book_index": book_index, "text": text})

    def sanitize_book_name(self, book_name):
        # Replace any characters that are not alphanumeric, underscore, hyphen, or dot with an underscore
        sanitized_book_name = re.sub(r"[^\w\-_\.]", "_", book_name)
        # Remove leading and trailing underscores and dots
        sanitized_book_name = sanitized_book_name.strip("._")
        return sanitized_book_name

    def batch_metadata_file_path(self):
        return os.path.join(os.getcwd(), "batch_files", f"{self.book_name}_info.json")

    def batch_dir(self):
        return os.path.join(os.getcwd(), "batch_files", self.book_name)

    def custom_id(self, book_index):
        return f"{self.book_name}-{book_index}"

    def is_completed_batch(self):
        batch_metadata_file_path = self.batch_metadata_file_path()

        if not os.path.exists(batch_metadata_file_path):
            print("Batch result file does not exist")
            raise Exception("Batch result file does not exist")

        with open(batch_metadata_file_path, "r", encoding="utf-8") as f:
            batch_info = json.load(f)

        for batch_file in batch_info["batch_files"]:
            batch_status = self.check_batch_status(batch_file["batch_id"])
            if batch_status.status != "completed":
                return False

        return True

    def batch_translate(self, book_index):
        if self.batch_info_cache is None:
            batch_metadata_file_path = self.batch_metadata_file_path()
            with open(batch_metadata_file_path, "r", encoding="utf-8") as f:
                self.batch_info_cache = json.load(f)

        batch_info = self.batch_info_cache
        target_batch = None
        for batch in batch_info["batch_files"]:
            if batch["start_index"] <= book_index < batch["end_index"]:
                target_batch = batch
                break

        if not target_batch:
            raise ValueError(f"No batch found for book_index {book_index}")

        if target_batch["batch_id"] in self.result_content_cache:
            result_content = self.result_content_cache[target_batch["batch_id"]]
        else:
            batch_status = self.check_batch_status(target_batch["batch_id"])
            if batch_status.output_file_id is None:
                raise ValueError(f"Batch {target_batch['batch_id']} is not completed")
            result_content = self.get_batch_result(batch_status.output_file_id)
            self.result_content_cache[target_batch["batch_id"]] = result_content

        result_lines = result_content.text.split("\n")
        custom_id = self.custom_id(book_index)
        for line in result_lines:
            if line.strip():
                result = json.loads(line)
                if result["custom_id"] == custom_id:
                    return self._read_batch_choice(
                        result["response"]["body"]["choices"][0],
                        custom_id,
                        self.language,
                    )

        raise ValueError(f"No result found for custom_id {custom_id}")

    @staticmethod
    def _read_batch_choice(choice, custom_id, language):
        """Unwrap one Batch API choice.

        Results are often fetched by a later process that never probed the
        model, so this decides from the payload itself rather than from cached
        capability state — and refuses to hand back a truncated JSON fragment.
        `language` only names the field to look for; nothing here inspects the
        text itself.
        """
        message = choice.get("message", {})
        if message.get("refusal"):
            raise ValueError(
                f"Model refused to translate {custom_id}: {message['refusal']}"
            )
        if choice.get("finish_reason") == "length":
            raise ValueError(
                f"Batch result for {custom_id} was truncated by the token limit"
            )

        content = message.get("content") or ""
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return content  # delimiter-mode batch, plain text is expected

        if not isinstance(parsed, dict):
            return content  # delimiter-mode batch, plain text is expected

        # A structured answer whose key we cannot find is not text: returning
        # `content` would paste the raw JSON into the book. The usual cause is a
        # result file produced under a different --language than this run.
        field = single_field_name(language)
        value = parsed.get(field)
        if not isinstance(value, str):
            raise ValueError(
                f"Batch result for {custom_id} has no '{field}' string; "
                f"got keys {sorted(parsed)}. A result file from a run with a "
                f"different --language cannot be resumed under this one."
            )
        return value

    def create_batch_context_messages(self, index):
        messages = []
        if self.context_flag:
            if index % CHATGPT_CONFIG[
                "batch_context_update_interval"
            ] == 0 or not hasattr(self, "cached_context_messages"):
                context_messages = []
                for i in range(index - 1, -1, -1):
                    item = self.batch_text_list[i]
                    if len(item["text"].split()) >= 100:
                        context_messages.append(item["text"])
                        if len(context_messages) == self.context_paragraph_limit:
                            break

                if len(context_messages) == self.context_paragraph_limit:
                    print("Creating cached context messages")
                    self.cached_context_messages = [
                        {"role": "user", "content": "\n".join(context_messages)},
                        {
                            "role": "assistant",
                            "content": self.get_translation(
                                "\n".join(context_messages)
                            ),
                        },
                    ]

            if hasattr(self, "cached_context_messages"):
                messages.extend(self.cached_context_messages)

        return messages

    def make_batch_request(self, book_index, text):
        messages = self.create_messages(
            text, self.create_batch_context_messages(book_index)
        )

        batch_body = {
            "model": self.batch_model,
            "messages": messages,
            **self._sampling_kwargs(self.batch_model),
        }

        # The Batch API takes hand-built bodies, so the schema cannot come from
        # the SDK here; single_translation_schema mirrors the Pydantic model.
        if self._ensure_structured_support(self.batch_model):
            batch_body["response_format"] = {
                "type": "json_schema",
                "json_schema": single_translation_schema(self.language),
            }

        return {
            "custom_id": self.custom_id(book_index),
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": batch_body,
        }

    def create_batch_files(self, dest_file_path):
        file_paths = []
        # max request 50,000 and max size 100MB
        lines_per_file = 40000
        current_file = 0

        for i in range(0, len(self.batch_text_list), lines_per_file):
            current_file += 1
            file_path = os.path.join(dest_file_path, f"{current_file}.jsonl")
            start_index = i
            end_index = i + lines_per_file

            # TODO: Split the file if it exceeds 100MB
            with open(file_path, "w", encoding="utf-8") as f:
                for text in self.batch_text_list[i : i + lines_per_file]:
                    batch_req = self.make_batch_request(
                        text["book_index"], text["text"]
                    )
                    json.dump(batch_req, f, ensure_ascii=False)
                    f.write("\n")
            file_paths.append(
                {
                    "file_path": file_path,
                    "start_index": start_index,
                    "end_index": end_index,
                }
            )

        return file_paths

    def batch(self):
        self.rotate_model()
        self.batch_model = self.model
        # current working directory
        batch_dir = self.batch_dir()
        batch_metadata_file_path = self.batch_metadata_file_path()
        # cleanup batch dir and result file
        if os.path.exists(batch_dir):
            shutil.rmtree(batch_dir)
        if os.path.exists(batch_metadata_file_path):
            os.remove(batch_metadata_file_path)
        os.makedirs(batch_dir, exist_ok=True)
        # batch execute
        batch_files = self.create_batch_files(batch_dir)
        batch_info = []
        for batch_file in batch_files:
            file_id = self.upload_batch_file(batch_file["file_path"])
            batch = self.batch_execute(file_id)
            batch_info.append(
                self.create_batch_info(
                    file_id, batch, batch_file["start_index"], batch_file["end_index"]
                )
            )
        # save batch info
        batch_info_json = {
            "book_id": self.book_name,
            "batch_date": time.strftime("%Y-%m-%d %H:%M:%S"),
            "batch_files": batch_info,
        }
        with open(batch_metadata_file_path, "w", encoding="utf-8") as f:
            json.dump(batch_info_json, f, ensure_ascii=False, indent=2)

    def create_batch_info(self, file_id, batch, start_index, end_index):
        return {
            "input_file_id": file_id,
            "batch_id": batch.id,
            "start_index": start_index,
            "end_index": end_index,
            "prefix": self.book_name,
        }

    def upload_batch_file(self, file_path):
        batch_input_file = self.openai_client.files.create(
            file=open(file_path, "rb"), purpose="batch"
        )
        return batch_input_file.id

    def batch_execute(self, file_id):
        current_time = time.strftime("%Y-%m-%d %H:%M:%S")
        res = self.openai_client.batches.create(
            input_file_id=file_id,
            endpoint="/v1/chat/completions",
            completion_window="24h",
            metadata={
                "description": f"Batch job for {self.book_name} at {current_time}"
            },
        )
        if res.errors:
            print(res.errors)
            raise Exception(f"Batch execution failed: {res.errors}")
        return res

    def check_batch_status(self, batch_id):
        return self.openai_client.batches.retrieve(batch_id)

    def get_batch_result(self, output_file_id):
        return self.openai_client.files.content(output_file_id)
