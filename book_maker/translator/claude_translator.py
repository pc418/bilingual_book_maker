from itertools import cycle
from urllib.parse import urlparse

from rich import print
from anthropic import (
    Anthropic,
    APIStatusError,
    BadRequestError,
    NotFoundError,
    UnprocessableEntityError,
)

from .base_translator import Base
from ..structured import RungRejected


def _sdk_base_url(api_base):
    """Trim the request path the SDK is going to add back.

    `Anthropic(base_url=...)` appends `/v1/messages` itself, so a URL copied
    from a gateway's docs (`https://host/v1`, or the whole
    `https://host/v1/messages`) produces `/v1/v1/messages` and a 403 whose
    text — "HTTP node only allows access to inference API paths" — points
    nowhere near the cause.
    """
    if not api_base:
        return None
    base = api_base.strip().rstrip("/")
    trimmed = False
    if base.endswith("/messages"):
        base = base[: -len("/messages")].rstrip("/")
        trimmed = True
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
        trimmed = True
    if trimmed:
        print(f"[dim]using anthropic base_url {base} (the SDK adds /v1)[/dim]")
    return base


# A gateway that does not serve /v1/messages answers like this. Auth, quota
# and transport errors say nothing about the wire format and must not trigger
# a second endpoint being tried with the same key.
_WRONG_SHAPE_STATUSES = (404, 405)

# Anthropic's own hosts serve no OpenAI route, so a 404 from one is an answer
# about the *model*, not the wire format. Retrying elsewhere would bury it.
_ANTHROPIC_HOSTS = ("anthropic.com",)


class Claude(Base):
    # Class-level defaults so a partially built instance (tests, subclasses)
    # still answers the questions every request path asks.
    requested_api_base = None
    _fallback = None

    def __init__(
        self,
        key,
        language,
        api_base=None,
        prompt_template=None,
        prompt_sys_msg=None,
        temperature=1.0,
        context_flag=False,
        context_paragraph_limit=5,
        **kwargs,
    ) -> None:
        super().__init__(key, language)
        base_url = _sdk_base_url(api_base)
        self.api_url = base_url or "https://api.anthropic.com"
        # Kept as given: the OpenAI fallback needs the user's own URL, not the
        # trimmed one this SDK wants.
        self.requested_api_base = api_base
        self.key_string = key
        # One key, not the whole comma-separated list; rotate_key advances it.
        self.client = Anthropic(base_url=base_url, api_key=next(self.keys), timeout=20)
        # Set on the first wrong-shape answer; every later call goes through it.
        self._fallback = None
        self.model = "claude-haiku-4-5-20251001"  # default it for now
        self.language = language
        self.prompt_template = (
            prompt_template
            or "Help me translate the text within triple backticks into {language} and provide only the translated result.\n```{text}```"
        )
        self.prompt_sys_msg = prompt_sys_msg or ""
        self.temperature = temperature
        self.context_flag = context_flag
        self.context_list = []
        self.context_translated_list = []
        self.context_paragraph_limit = context_paragraph_limit

    def rotate_key(self):
        """Advance to the next key, as the comma-separated form promises.

        `Anthropic.api_key` is writable, so this needs no new client. Without
        it a multi-key run sent the literal string "a,b" as the credential
        and failed authentication.
        """
        self.client.api_key = next(self.keys)

    def set_model_list(self, model_list):
        """Take the model to use. Any id the endpoint serves is accepted.

        Claude has no model rotation, so when several are named the first
        wins — announced, not silently.
        """
        models = [m.strip() for m in model_list if m and m.strip()]
        if not models:
            raise ValueError("--model_list is empty")
        if len(models) > 1:
            print(
                f"[yellow]ℹ claude uses one model per run; taking "
                f"'{models[0]}' and ignoring {len(models) - 1} more[/yellow]"
            )
        self.model = models[0]

    def _is_wrong_shape(self, error):
        """Whether `error` says this endpoint does not speak the anthropic shape."""
        if not self.requested_api_base:
            # The default host is Anthropic's own. A 404 there means the model
            # does not exist, and retrying on /chat/completions cannot help.
            return False
        host = (urlparse(self.requested_api_base).hostname or "").lower()
        if any(host == h or host.endswith(f".{h}") for h in _ANTHROPIC_HOSTS):
            # Named explicitly, as the documented command does, but still the
            # one host where the OpenAI shape does not exist.
            return False
        if isinstance(error, NotFoundError):
            return True
        return (
            isinstance(error, APIStatusError)
            and getattr(error, "status_code", None) in _WRONG_SHAPE_STATUSES
        )

    def _build_openai_fallback(self):
        """The same endpoint, model and key, spoken as OpenAI instead."""
        from .chatgptapi_translator import ChatGPTAPI

        base = (self.requested_api_base or "").rstrip("/")
        if not base.endswith("/v1"):
            # `_sdk_base_url` trimmed /v1 for the anthropic SDK, which appends
            # its own path; the OpenAI SDK expects the /v1 to be there.
            base = f"{base}/v1"
        fallback = ChatGPTAPI(
            self.key_string,
            self.language,
            api_base=base,
            prompt_template=self.prompt_template,
            prompt_sys_msg=self.prompt_sys_msg,
            temperature=self.temperature,
            context_flag=self.context_flag,
            context_paragraph_limit=self.context_paragraph_limit,
        )
        # Straight assignment rather than set_model_list: the model is already
        # chosen, and re-validating it would spend requests mid-run.
        fallback.model_list = cycle([self.model])
        fallback.model = self.model
        return fallback

    def _switch_to_openai(self, error):
        self._fallback = self._build_openai_fallback()
        print(
            f"[yellow]ℹ this endpoint does not answer the anthropic shape "
            f"({error}); switching to the openai format for the rest of the "
            f"run. Pass --api_format openai to skip this attempt.[/yellow]"
        )
        return self._fallback

    def create_messages(self, text, intermediate_messages=None):
        """Create messages for the current translation request"""
        current_msg = {
            "role": "user",
            "content": self.prompt_template.format(
                text=text,
                language=self.language,
            ),
        }

        messages = []
        if intermediate_messages:
            messages.extend(intermediate_messages)
        messages.append(current_msg)

        return messages

    def create_context_messages(self):
        """Create a message pair containing all context paragraphs"""
        if not self.context_flag or not self.context_list:
            return []

        # Create a single message pair for all previous context
        return [
            {
                "role": "user",
                "content": self.prompt_template.format(
                    text="\n\n".join(self.context_list),
                    language=self.language,
                ),
            },
            {"role": "assistant", "content": "\n\n".join(self.context_translated_list)},
        ]

    def save_context(self, text, t_text):
        """Save the current translation pair to context"""
        if not self.context_flag:
            return

        self.context_list.append(text)
        self.context_translated_list.append(t_text)

        # Keep only the most recent paragraphs within the limit
        if len(self.context_list) > self.context_paragraph_limit:
            self.context_list.pop(0)
            self.context_translated_list.pop(0)

    def _chat_completion(self, prompt, model=None):
        """One question, one answer — the channel plan classification needs.

        No native structured-output rung on purpose: nobody here runs the
        official Anthropic API, so its schema feature cannot be tested, and
        the gateways that serve the anthropic shape drop schema fields
        anyway. Measured 260807 on api.b.ai: Claude ignores `response_format`
        entirely, and still answers 12 of 12 signatures with legal verdicts
        through this rung. The lint is what establishes that, not the request.

        Deliberately outside the translation flow: no context pairs, no
        prompt template, no saved history.
        """
        if self._fallback:
            return self._fallback._chat_completion(prompt, model)
        try:
            r = self.client.messages.create(
                max_tokens=4096,
                model=model or self.model,
                messages=[{"role": "user", "content": prompt}],
            )
        except (BadRequestError, UnprocessableEntityError) as e:
            raise RungRejected(e) from e
        except APIStatusError as e:
            if not self._is_wrong_shape(e):
                raise
            return self._switch_to_openai(e)._chat_completion(prompt, model)
        self._note_usage(r, model)
        return "".join(
            block.text for block in r.content if getattr(block, "type", "") == "text"
        )

    def _note_usage(self, message, model=None):
        """Add what the endpoint billed for this request to the meter.

        Anthropic's `input_tokens` leaves the cached part out, so the prompt
        total is the three input counts together; `cache_read_input_tokens`
        is the number a gateway that drops `cache_control` never reports.
        """
        usage = getattr(message, "usage", None)
        if usage is None:
            return
        read = getattr(usage, "cache_read_input_tokens", 0) or 0
        written = getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.usage.note(
            (getattr(usage, "input_tokens", 0) or 0) + read + written,
            getattr(usage, "output_tokens", 0),
            read,
            model=model or self.model,
        )

    def translate(self, text):
        if self._fallback:
            return self._fallback.translate(text)

        self.rotate_key()

        # Create messages with context
        messages = self.create_messages(text, self.create_context_messages())

        try:
            r = self.client.messages.create(
                max_tokens=4096,
                messages=messages,
                system=self.prompt_sys_msg,
                temperature=self.temperature,
                model=self.model,
            )
        except APIStatusError as e:
            if not self._is_wrong_shape(e):
                raise
            return self._switch_to_openai(e).translate(text)
        self._note_usage(r)
        t_text = r.content[0].text

        if self.context_flag:
            self.save_context(text, t_text)

        return t_text
