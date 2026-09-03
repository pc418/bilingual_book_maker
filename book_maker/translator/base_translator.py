import itertools
import re
import threading
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

from rich import print

from ..structured import (
    extract_json_object,
    prompt_with_schema,
    run_rungs,
    unwrap_schema_echo,
)

# Special delimiter for batch translation - UUID-based token unlikely to appear in any text
BATCH_DELIMITER = "\n\n@@\n\n"


def short_count(n):
    """12345 -> '12.3k', 1234567 -> '1.23M': a progress bar has no room for digits."""
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k"
    return f"{n / 1_000_000:.2f}M"


def _count(value):
    """A token count as an int; anything a gateway sends that is not one is 0.

    The meter is a readout: a field reported as a string, None or a nested
    object costs the number on the bar, never the run.
    """
    if isinstance(value, bool):
        return 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£", "CNY": "¥", "JPY": "¥"}


class PriceTable:
    """A provider entry's `prices`: what a model charges per million tokens.

    Looked up by the model id the run asked for: exactly, then by the part
    after a router's `vendor/` prefix, then by the longest listed id the
    model's name starts with — so `gpt-5.6-luna-2026-07-30` is priced as
    `gpt-5.6-luna`. A model none of that finds has no price, and the meter
    says so instead of guessing.
    """

    def __init__(self, prices, currency="USD"):
        self.prices = dict(prices or {})
        self.currency = currency or "USD"

    def price_for(self, model):
        if not model:
            return None
        if model in self.prices:
            return self.prices[model]
        tail = model.rsplit("/", 1)[-1]
        if tail in self.prices:
            return self.prices[tail]
        prefixes = [key for key in self.prices if model.startswith(key)]
        if prefixes:
            return self.prices[max(prefixes, key=len)]
        return None

    def cost(self, model, prompt=0, completion=0, cached=0):
        """What one request cost, or None when its model has no price."""
        price = self.price_for(model)
        if price is None:
            return None
        cached = min(cached or 0, prompt or 0)
        cached_rate = price.get("cached_input", price["input"])
        return (
            (prompt - cached) * price["input"]
            + cached * cached_rate
            + (completion or 0) * price["output"]
        ) / 1_000_000

    def money(self, amount):
        symbol = CURRENCY_SYMBOLS.get(self.currency.upper())
        digits = 4 if amount < 0.01 else 3 if amount < 1 else 2
        figure = f"{amount:.{digits}f}"
        return f"{symbol}{figure}" if symbol else f"{figure} {self.currency}"


class UsageMeter:
    """Tokens billed so far, summed over every request that reported them.

    With a `PriceTable` in `prices` the meter also keeps what those tokens
    cost, and shows that instead: a number the operator can weigh against
    the book. A request for a model the table does not price makes the
    cost unknowable, so the display falls back to tokens and the summary
    names the model.

    `cached` is the part of `prompt` the endpoint read back from its cache.
    That is the number session mode lives on: a history that is re-read at
    full price every request costs more than window mode, and nothing in
    the output says so — only this does. It is shown on the progress bar
    rather than judged: whether the ratio is worth it is the operator's
    call, made while watching.
    """

    def __init__(self):
        self.prompt = 0
        self.completion = 0
        self.cached = 0
        self.requests = 0
        self.prices = None
        self.spent = 0.0
        self.unpriced = set()
        self._lock = threading.Lock()

    def note(self, prompt=0, completion=0, cached=0, model=None):
        prompt, completion, cached = _count(prompt), _count(completion), _count(cached)
        with self._lock:
            self.prompt += prompt
            self.completion += completion
            self.cached += cached
            self.requests += 1
            if self.prices is not None:
                cost = self.prices.cost(model, prompt, completion, cached)
                if cost is None:
                    self.unpriced.add(model or "(unnamed model)")
                else:
                    self.spent += cost

    def _priced(self):
        return self.prices is not None and not self.unpriced

    def postfix(self):
        """What the progress bar shows — nothing until a request reported usage."""
        if not self.requests:
            return None
        if self._priced():
            return {"spent": self.prices.money(self.spent)}
        return {
            "in": short_count(self.prompt),
            "out": short_count(self.completion),
            "cached": short_count(self.cached),
        }

    def summary(self):
        if not self.requests:
            return None
        tokens = (
            f"in {short_count(self.prompt)}, out "
            f"{short_count(self.completion)}, cached {short_count(self.cached)} "
            f"({self.requests} request{'s' if self.requests != 1 else ''})"
        )
        if self._priced():
            return f"spent {self.prices.money(self.spent)} — tokens: {tokens}"
        line = f"tokens: {tokens}"
        if self.unpriced:
            line += (
                f"; no price for {', '.join(sorted(self.unpriced))} in the "
                f"provider entry, so spent is not shown"
            )
        return line


class AsyncTranslationUnsupported(NotImplementedError):
    """Raised when a provider has no native asynchronous implementation."""


@dataclass(frozen=True)
class TranslationContext:
    """Immutable translation history passed explicitly between requests."""

    source_texts: tuple[str, ...] = ()
    translated_texts: tuple[str, ...] = ()

    def __post_init__(self):
        if len(self.source_texts) != len(self.translated_texts):
            raise ValueError("Source and translated context lengths must match")

    def append(
        self, source_text: str, translated_text: str, limit: int
    ) -> "TranslationContext":
        if limit <= 0:
            return self
        return type(self)(
            (self.source_texts + (source_text,))[-limit:],
            (self.translated_texts + (translated_text,))[-limit:],
        )


@dataclass(frozen=True)
class TranslationResult:
    text: str
    context: TranslationContext


@dataclass(frozen=True)
class BatchTranslationResult:
    texts: tuple[str, ...]
    context: TranslationContext


def service_name(translator):
    """The `--api_format` or `--provider` key a translator is registered
    under, or its class name for one registered nowhere.

    Imported lazily: the registries live in the package `__init__`, which
    imports every translator, which imports this module.
    """
    from . import FORMAT_DICT, ROUTE_DICT

    cls = type(translator)
    for registry in (FORMAT_DICT, ROUTE_DICT):
        for key, registered in registry.items():
            if registered is cls:
                return key
    return cls.__name__


class Base(ABC):
    # Default values for fatal error handling - subclasses can override
    TRANSLATION_ERROR_MARKER = None

    # Does this format implement `--use_context session` — one append-only
    # history, compacted into a handoff report at --context-compact-at? A
    # format that does not gets the flag refused rather than accepting it
    # and translating as if it had never been passed. The same answer
    # settles `--context-compact-at 0`, whose auto-sizing lives beside the
    # history it sizes.
    SUPPORTS_SESSION_CONTEXT = False

    # Can this route be asked what context window the model has, for
    # `--context-compact-at 0`? A machine-translation engine has no model to
    # ask about, so the CLI refuses `0` there.
    SUPPORTS_AUTO_COMPACT_BUDGET = False

    # Does this format survive `--parallel-workers` with `--use_context`?
    # Each worker is handed a clone carrying its own chapter context, which
    # a format that keeps no re-sendable window cannot provide.
    SUPPORTS_PARALLEL_CONTEXT = False

    # Whether a system message can be borrowed for the length of one request.
    # False where it is not sent per request at all: the codex route folds it
    # into a thread's base instructions when the thread opens, and the thread
    # outlives the window, so a batch system message set there would not
    # describe one group of paragraphs — it would tell every later unit to
    # come back in "@@"-separated segments. Those routes carry the batch
    # contract in the prompt instead, which does ride with the request.
    BATCH_SYS_MSG_PER_REQUEST = True

    # Whether a batch's context is recorded as one pair per line. Window-style
    # context wants that, for the reason `_do_batch_translate` gives. A history
    # replayed verbatim wants the opposite: a grouped request was one exchange,
    # and recording it as several pairs leaves the history no longer matching
    # what was sent, which is a broken cache prefix.
    BATCH_CONTEXT_PER_LINE = True

    # Does this route implement the OpenAI Batch API — `--batch` to submit a
    # book and `--batch-use` to collect it? Only the OpenAI translator does.
    # A route that does not gets the flag refused: the loader calls
    # batch_init/add_to_batch_translate_queue/is_completed_batch on the
    # translator, so accepting it would be an AttributeError partway in.
    SUPPORTS_BATCH_API = False

    # Refusals of one rung, by one model, before we stop offering it.
    RUNG_REFUSAL_THRESHOLD = 2

    def __init__(self, key, language) -> None:
        self.keys = itertools.cycle(key.split(","))
        self.language = language
        self._fatal_error_detected = False
        # {model: {rung name: refusal count}} — see _retire_rung. Dict writes
        # are atomic under the GIL, which is all the synchronization this
        # needs: the worst a race costs is one repeated rejection, never a
        # wrong answer.
        self._rung_refusals = {}
        # Filled by the translators that get usage back from the endpoint;
        # the loader reads it for the progress bar and the closing line.
        self.usage = UsageMeter()

    @property
    def model_name(self):
        """The model id this translator runs with, for the record an output
        file keeps of how it was made.

        Every LLM translator here settles on `self.model` — the endpoint
        needs a model id to call, so the attribute is not optional for
        them. The ones that call a single fixed service (Google, DeepL,
        Caiyun, TranSmart) have no model to name, and the name the service
        is selected by — its `--api_format` or `--provider` key — is the
        honest answer: it says which service, and claims no more.
        """
        return getattr(self, "model", None) or service_name(self)

    def usage_postfix(self):
        return self.usage.postfix()

    def usage_summary(self):
        return self.usage.summary()

    @abstractmethod
    def rotate_key(self):
        pass

    @abstractmethod
    def translate(self, text):
        pass

    # ---------------------------------------------------------------- JSON
    # Plan classification asks a translator one structured question. It needs
    # a JSON object carrying legal values — not an endpoint that honors the
    # `json_schema` request field. So the bottom rung, defined here, is a
    # plain prompt: every LLM-backed translator can classify, whatever its
    # provider supports.

    def _chat_completion(self, prompt, model=None):
        """Answer one arbitrary prompt in one turn; return the raw text.

        The single primitive classification needs. Translators that speak to
        an LLM implement it and get classification for free. Dedicated MT
        engines (google, deepl, caiyun, tencent transmart, qwen-mt, a custom
        translate endpoint) cannot: their only channel *translates* what it is
        handed instead of answering it, so asking would return a translated
        copy of the question. They keep failing loudly instead.
        """
        raise NotImplementedError(
            f"{type(self).__name__} has no arbitrary-prompt channel"
        )

    def supports_structured_json(self):
        """Whether this translator can be asked a question at all.

        Derived from the implementation rather than a per-class flag, so a new
        translator cannot advertise a capability it never implemented, or
        implement one it forgot to advertise. Either half counts: the prompt
        primitive, or a `structured_json` of the provider's own.
        """
        cls = type(self)
        return (
            cls._chat_completion is not Base._chat_completion
            or cls.structured_json is not Base.structured_json
        )

    def structured_rungs(self, prompt, schema, model=None):
        """(name, callable) pairs, most constrained first.

        Providers prepend their native structured-output mechanisms; the
        prompt rung is the floor and is always last.
        """
        return [("prompt", lambda: self._prompt_rung(prompt, schema, model))]

    def _prompt_rung(self, prompt, schema, model=None):
        """Schema described in the prompt, answer recovered from free text."""
        text = self._chat_completion(prompt_with_schema(prompt, schema), model=model)
        return unwrap_schema_echo(extract_json_object(text))

    def structured_json(self, prompt, schema, model=None, accept=None):
        """One structured question, over whatever rungs this provider has.

        Returns the first object `accept` approves, else the last object any
        rung parsed (the caller lints it: a partial answer beats a discarded
        page), else raises `StructuredJSONFailed`. Value constraints are never
        guaranteed — the caller owns validation.
        """
        target = model or getattr(self, "model", None)
        rungs = self.structured_rungs(prompt, schema, model)
        refusals = self._rung_refusals.get(target, {})
        live = [
            (name, rung)
            for name, rung in rungs
            if refusals.get(name, 0) < self.RUNG_REFUSAL_THRESHOLD
        ]
        # The floor is always asked. Callers divide a failed request and ask
        # again with less; handing them an empty ladder would answer that
        # smaller request without making a single call.
        floor = rungs[-1] if rungs else None
        if not live and floor is not None:
            live = [floor]
        return run_rungs(
            live,
            accept=accept,
            on_reject=lambda name: self._retire_rung(name, target, floor[0]),
        )

    def _retire_rung(self, name, model, floor):
        """Stop paying for a rung the endpoint keeps refusing.

        Two deliberate limits on this:

        - **Never the floor.** A plain prompt has no simpler form to fall
          back to, and an endpoint refusing one is not saying anything about
          our schema.
        - **Never on the first refusal.** A 400 is as often about *this
          request* — too long, too many properties — as about the shape, and
          those are exactly the failures the caller recovers from by dividing
          and asking again. Retiring on one refusal would confiscate the rung
          the retry needs. Same threshold idiom as the translation-side
          demotion in `chatgptapi_translator`.
        """
        if name == floor:
            return
        counts = self._rung_refusals.setdefault(model, {})
        counts[name] = counts.get(name, 0) + 1
        if counts[name] == self.RUNG_REFUSAL_THRESHOLD:
            print(
                f"[yellow]ℹ '{model}' keeps refusing the {name} request "
                f"shape; using a simpler one from here on[/yellow]"
            )

    def translate_list(self, text_list):
        """
        Translate a list of texts. Default implementation translates one by one.
        Subclasses can override for batch efficiency.
        """
        return [self.translate(t) for t in text_list]

    async def translate_async(
        self, text: str, *, context: TranslationContext | None = None
    ) -> TranslationResult:
        raise AsyncTranslationUnsupported(
            f"{type(self).__name__} does not implement native async translation"
        )

    async def translate_list_async(
        self,
        text_list: Sequence[str],
        *,
        context: TranslationContext | None = None,
    ) -> BatchTranslationResult:
        """Translate a list sequentially while threading explicit context."""
        current_context = context or TranslationContext()
        translations = []
        for text in text_list:
            result = await self.translate_async(text, context=current_context)
            translations.append(result.text)
            current_context = result.context
        return BatchTranslationResult(tuple(translations), current_context)

    async def close_async(self) -> None:
        """Release provider-specific asynchronous resources."""

    def _build_batch_prompt(
        self, text_list, prompt_template, system_content, default_prompt
    ):
        """
        Build batch translation prompt and system message.

        Args:
            text_list: List of texts to translate
            prompt_template: User's custom prompt template (can be None)
            system_content: User's custom system message (can be None)
            default_prompt: Default prompt template to use if prompt_template is None

        Returns:
            Tuple of (batch_prompt, batch_sys_msg, batch_text)
        """
        plist_len = len(text_list)
        if plist_len == 0:
            return None, None, None

        if plist_len == 1:
            return None, None, None  # Signal to use single translation

        # Build stripped texts list once
        stripped_texts = [str(t).strip() for t in text_list]
        batch_text = BATCH_DELIMITER.join(stripped_texts)

        # Build batch instruction
        batch_instruction = (
            f"Translate the following {plist_len} text segments to {{language}}. "
            f"Separate each translation with '{BATCH_DELIMITER}'. "
            f"Output EXACTLY {plist_len} translations.\n\n"
        )

        # Use the user's custom prompt template, or fall back to default
        user_prompt = prompt_template if prompt_template else default_prompt
        batch_prompt = batch_instruction + user_prompt

        # Preserve user's system message, adding batch-specific context
        if system_content:
            batch_sys_msg = (
                f"{system_content} Input has {plist_len} segments separated by '{BATCH_DELIMITER}'. "
                f"Output {plist_len} translations with '{BATCH_DELIMITER}' between each."
            )
        else:
            batch_sys_msg = (
                f"Professional translator. Input has {plist_len} segments separated by '{BATCH_DELIMITER}'. "
                f"Output {plist_len} translations with '{BATCH_DELIMITER}' between each."
            )

        return batch_prompt, batch_sys_msg, batch_text

    def _extract_paragraphs(self, text, paragraph_count):
        """
        Extract paragraphs from translated text, ensuring paragraph count is preserved.

        Args:
            text: Translated text containing multiple paragraphs
            paragraph_count: Expected number of paragraphs

        Returns:
            List of extracted paragraphs
        """
        result_list = []

        # First try to extract by paragraph numbers (1), (2), etc.
        for i in range(1, paragraph_count + 1):
            pattern = rf"\({i}\)\s*(.*?)(?=\s*\({i + 1}\)|\Z)"
            match = re.search(pattern, text, re.DOTALL)
            if match:
                result_list.append(match.group(1).strip())

        # If exact pattern matching failed, try another approach
        if len(result_list) != paragraph_count:
            pattern = r"\((\d+)\)\s*(.*?)(?=\s*\(\d+\)|\Z)"
            matches = re.findall(pattern, text, re.DOTALL)
            if matches:
                # Sort by paragraph number
                matches.sort(key=lambda x: int(x[0]))
                result_list = [match[1].strip() for match in matches]

        # Fallback: try splitting by BATCH_DELIMITER with flexible whitespace
        if len(result_list) != paragraph_count:
            # Extract the core delimiter (e.g., '@@' from BATCH_DELIMITER)
            core_delimiter = BATCH_DELIMITER.strip()
            # Split by the core delimiter with any surrounding whitespace/newlines
            parts = re.split(r"\s*" + re.escape(core_delimiter) + r"\s*", text)
            # Filter out empty strings
            result_list = [p.strip() for p in parts if p.strip()]

        # Final fallback: split by double newlines if still not matching
        if len(result_list) != paragraph_count:
            lines = text.splitlines()
            result_list = [line.strip() for line in lines if line.strip() != ""]

        return result_list

    def _do_batch_translate(
        self, text_list, prompt_template, system_content, default_prompt, translate_func
    ):
        """
        Perform batch translation with fallback to one-by-one translation.

        Args:
            text_list: List of texts to translate
            prompt_template: User's custom prompt template
            system_content: User's custom system message
            default_prompt: Default prompt template
            translate_func: Function to call for actual translation (single or batch)

        Returns:
            List of translated texts
        """
        plist_len = len(text_list)

        if plist_len == 0:
            return []

        if plist_len == 1:
            return [translate_func(str(text_list[0]).strip())]

        # Build batch prompt
        batch_prompt, batch_sys_msg, batch_text = self._build_batch_prompt(
            text_list, prompt_template, system_content, default_prompt
        )

        # Store original values
        original_prompt = prompt_template
        original_sys_msg = system_content

        # Detect which attribute names this translator uses
        # ChatGPT uses prompt_template/system_content, Gemini uses prompt/prompt_sys_msg
        prompt_attr = (
            "prompt_template" if hasattr(self, "prompt_template") else "prompt"
        )
        sys_msg_attr = (
            "system_content" if hasattr(self, "system_content") else "prompt_sys_msg"
        )

        # --use_context must see one pair per paragraph. translate() saves
        # whatever it was handed, so letting it run on the joined batch would
        # store a single entry full of "@@" markers and evict three real
        # paragraphs of context. Suppress it here; save per pair on success,
        # the way the structured batch path already does.
        #
        # A history that is replayed verbatim rather than windowed wants the
        # joined exchange saved exactly as it was sent, and says so through
        # BATCH_CONTEXT_PER_LINE; there translate() is left to do its own
        # saving and nothing is suppressed.
        context_flag = getattr(self, "context_flag", False)
        per_line = context_flag and self.BATCH_CONTEXT_PER_LINE

        try:
            # Set batch values
            setattr(self, prompt_attr, batch_prompt)
            if (
                batch_sys_msg
                and self.BATCH_SYS_MSG_PER_REQUEST
                and hasattr(self, sys_msg_attr)
            ):
                setattr(self, sys_msg_attr, batch_sys_msg)
            if per_line:
                self.context_flag = False

            translated_text = translate_func(batch_text)
        finally:
            # Restore original values
            setattr(self, prompt_attr, original_prompt)
            # Restored even when it was None. A translator that carries no
            # system message of its own — codex, whose voice lives in the
            # thread instructions — would otherwise keep the batch one, and
            # describe "@@"-separated segments to every later request.
            if hasattr(self, sys_msg_attr):
                setattr(self, sys_msg_attr, original_sys_msg)
            if per_line:
                self.context_flag = True

        # Handle None or empty response
        if not translated_text:
            print(
                f"[bold red]Error: Translation API returned empty response for batch request.[/bold red]"
            )
            raise Exception("Translation API returned empty response")

        translated_paragraphs = self._extract_paragraphs(translated_text, plist_len)

        # Fallback to one-by-one translation if paragraph count doesn't match
        if len(translated_paragraphs) != plist_len:
            print(
                f"Warning: Expected {plist_len} translations, got {len(translated_paragraphs)}. Falling back to one-by-one translation."
            )
            print(f"\n[Debug] Input text_list ({plist_len} items):")
            stripped_texts = [str(t).strip() for t in text_list]
            for i, t in enumerate(stripped_texts, 1):
                print(f"  [{i}] {t!r}")
            print(f"\n[Debug] Model response ({len(translated_text)} chars):")
            print(translated_text)
            print(f"\n[Debug] Split result ({len(translated_paragraphs)} items):")
            for i, p in enumerate(translated_paragraphs, 1):
                print(f"  [{i}] {p!r}")
            print()
            # context_flag is restored here, so each single call saves its own
            # pair — no extra bookkeeping needed on this path
            return [translate_func(t) for t in stripped_texts]

        if per_line:
            for original, translated in zip(text_list, translated_paragraphs):
                self.save_context(str(original).strip(), translated)

        return translated_paragraphs
