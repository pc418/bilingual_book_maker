"""The request layer every classification step asks through.

A classification here is a question about a handful of candidates, each
answered with one of a fixed set of labels: plan mode asks whether each tag
signature is worth translating (`loader/classify/`), the PDF route asks
which role each detected region plays (`pipeline/decisions.py`). The callers
own everything about *their* question -- the text, the paging, how a page is
split when it fails, the budget -- and this module owns only how one
question reaches a model and what comes back:

    Question   the caller's prompt (verbatim), a strict JSON schema keyed by
               candidate id, the allowed answers per id, an optional image
    Backend    one way of asking: `schema` (the translator's structured-JSON
               ladder, with or without an image), `session` (a held plain
               conversation, text only), `jev` (TypeSafe's System One
               classifier, text only)
    Classifier the backends one endpoint offers, in the order a run prefers
               them; `ask` picks the first that can take the question and
               lints the reply once for every caller
    Answer     the values that passed the lint, the ids nobody asked about,
               the values outside their candidate's set, which backend
               answered, and what it cost

The lint is deliberately the only judgment made here. A missing id is left
missing: what it means is the caller's (`unsure` for the plan classifier,
`unanswered` for the role pass).
"""

import time
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# The question and the answer
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Question:
    """One question about some candidates, as the caller wrote it.

    `prompt` is the caller's text, sent verbatim. `schema` is the strict
    JSON schema the structured channel takes (`{"name", "strict", "schema"}`),
    keyed by candidate id. `candidates` maps each id to the answers it may
    take; when an answer is an object rather than a label, `field` names the
    member those answers constrain. `abstain` is the answer that means "the
    evidence does not settle it" (a backend that answers with a probability
    uses it for a flat distribution). `accept` is the schema ladder's
    terminating test (see `Base.structured_json`). `per_candidate` holds the
    caller's prompt for each candidate alone (a backend that asks one
    question per candidate sends that). `trunk` is the instruction text a
    held conversation opens with. `deadline` (a `time.monotonic()` value)
    bounds an image question's waiting.
    """

    prompt: str
    schema: dict = None
    candidates: dict = field(default_factory=dict)
    image_png: bytes = None
    field: str = None
    abstain: str = None
    accept: object = None
    per_candidate: dict = None
    trunk: str = None
    deadline: float = None


@dataclass
class Answer:
    """What came back, after the one lint every caller shares."""

    values: dict
    unknown_ids: tuple = ()
    invalid: dict = field(default_factory=dict)
    backend: str = ""
    usage: dict = None
    confidence: dict = field(default_factory=dict)
    raw: object = None
    model: str = None


class Reply(dict):
    """A backend's raw answer (`{id: value}`), with what the call cost.

    `text` is the reply as the endpoint wrote it, where there was one;
    `confidence` a backend's own probability for each chosen value.
    """

    usage = None
    text = None
    confidence = None


class NoBackend(Exception):
    """No backend of this classifier can take the question (an image asked
    of a text-only route, above all). The message says why."""


# --------------------------------------------------------------------------
# Capabilities, asked of a translator without spending anything
# --------------------------------------------------------------------------


def can_session_classify(translator):
    """Whether this route can hold a conversation for a classifier.

    Derived from the implementation, as `supports_structured_json` is, so a
    route cannot advertise a session it never built. Asked without opening
    one: `classify_session` may cost a request (the codex route opens a
    thread).
    """
    from .translator.base_translator import Base

    factory = getattr(type(translator), "classify_session", None)
    return factory is not None and factory is not Base.classify_session


def has_schema_verdict(translator, model=None):
    """Whether the endpoint's graded schema support reaches `json_object`.

    The verdict is cached by the capability ledger, so asking costs nothing
    the run has not already paid for. A probe that cannot answer is not a
    verdict, and a route with no probe at all has none either.
    """
    probe = getattr(translator, "_probe_verdict", None)
    if probe is None:
        return False
    try:
        verdict = probe(model) if model else probe()
    except Exception:
        return False
    return verdict in ("strict", "shape", "json")


def _supports_structured(translator):
    structured = getattr(translator, "structured_json", None)
    supports = getattr(translator, "supports_structured_json", None)
    return structured is not None and (supports is None or supports())


def _metered(translator, call):
    """`(result, {"prompt_tokens", "completion_tokens"} or None)` of one call."""
    usage = getattr(translator, "usage", None)
    before = (usage.prompt, usage.completion) if usage is not None else None
    result = call()
    if before is None:
        return result, None
    return result, {
        "prompt_tokens": usage.prompt - before[0],
        "completion_tokens": usage.completion - before[1],
    }


# --------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------


class SchemaBackend:
    """The translator's structured-JSON ladder, with an image when asked.

    Text: `structured_json`, which descends from a strict schema to a plain
    prompt on its own. Image: `structured_json_with_image`, only once the
    endpoint has read the probe image (`vision_verdict`).
    """

    name = "schema"

    def __init__(self, translator, model=None):
        self.translator = translator
        self.model = model

    def vision(self):
        """'verified', 'unsupported' or 'deferred'; cached by the ledger."""
        verdict = getattr(self.translator, "vision_verdict", None)
        if verdict is not None:
            return verdict(self.model)
        ledger = getattr(self.translator, "capabilities", None)
        if ledger is not None and hasattr(ledger, "ensure_vision"):
            return ledger.ensure_vision(self.model)
        return "unsupported"

    def can(self, question):
        if question.schema is None:
            return False
        if question.image_png is not None:
            return (
                hasattr(self.translator, "structured_json_with_image")
                and self.vision() == "verified"
            )
        return _supports_structured(self.translator)

    def weak(self, question):
        """A text question on an endpoint with no JSON verdict: the plain
        prompt is the only rung that would answer, so a held conversation,
        where there is one, is asked instead."""
        return question.image_png is None and not has_schema_verdict(
            self.translator, self.model
        )

    def why_not(self, question):
        if question.image_png is None:
            return f"{type(self.translator).__name__} has no structured-output support"
        if not hasattr(self.translator, "structured_json_with_image"):
            return f"{type(self.translator).__name__} has no image channel"
        return f"the model did not read the probe image ({self.vision()})"

    def ask(self, question):
        translator = self.translator
        if question.image_png is not None:
            kwargs = {"model": self.model, "accept": question.accept}
            if question.deadline is not None:
                kwargs["deadline"] = question.deadline
            result, usage = _metered(
                translator,
                lambda: translator.structured_json_with_image(
                    question.prompt, question.schema, question.image_png, **kwargs
                ),
            )
        else:
            result, usage = _metered(
                translator,
                lambda: translator.structured_json(
                    question.prompt,
                    question.schema,
                    model=self.model,
                    accept=question.accept,
                ),
            )
        if not isinstance(result, dict):
            return result  # the caller's lint names a malformed reply
        reply = Reply(result)
        reply.usage = usage
        reply.text = getattr(result, "raw", None)
        return reply


class Conversation:
    """A classifier's held session, restarted rather than compacted.

    The trunk is sent with the first turn of each session and never again.
    When the estimated history reaches the session's budget the next turn
    opens a fresh session carrying the trunk: no handoff report is asked
    for, because a verdict depends on the candidates in front of it and on
    nothing said earlier. `seed_tokens` counts whatever the session object
    sends beside the trunk when it starts (a demonstrated exchange).
    """

    def __init__(self, session, trunk, budget, seed_tokens=0):
        self.session = session
        self.trunk = trunk
        self.budget = budget or 0
        self.seed_tokens = seed_tokens
        self.tokens = 0
        self.sessions = 0
        self._open = False

    def ask(self, text):
        from .session_context import estimate_tokens

        if not self._open:
            self.session.start(self.trunk)
            self.tokens = estimate_tokens(self.trunk) + self.seed_tokens
            self.sessions += 1
            self._open = True
        reply = self.session.ask(text)
        self.tokens += estimate_tokens(text) + estimate_tokens(reply or "")
        if self.budget > 0 and self.tokens >= self.budget:
            # The next ask starts over. Closing here rather than opening the
            # replacement now keeps the last session of a run from paying
            # for a trunk nobody uses.
            self._open = False
        return reply


def parse_labels(reply, candidates):
    """`{id: label}` from a comma-separated reply, or {} when it does not parse.

    One label per candidate, in the candidates' order. Case, surrounding
    whitespace and a trailing period are tolerated; a count that does not
    match, or a word outside its candidate's answers, is a reply not
    understood -- and taking the first N of a longer list is the tempting
    leniency that trusts an ordering already in doubt.
    """
    if not isinstance(reply, str):
        return {}
    ids = list(candidates)
    tokens = reply.strip().split(",")
    if len(tokens) != len(ids):
        return {}
    labels = {}
    for cid, token in zip(ids, tokens):
        word = token.strip().rstrip(".").strip().lower()
        if word not in candidates[cid]:
            return {}
        labels[cid] = word
    return labels


class SessionBackend:
    """A held plain conversation: the question as a turn, labels as a reply.

    For an endpoint that returns no JSON (the codex route, a gateway that
    answers prose whatever `response_format` says). Text only. `open`
    starts nothing: it asks the translator for its session object once.
    """

    name = "session"

    def __init__(self, translator, model=None, session=None):
        self.translator = translator
        self.model = model
        self._session = session
        self._conversation = None

    def open(self):
        """The translator's session object, or None when it has none."""
        if self._session is None:
            factory = getattr(self.translator, "classify_session", None)
            self._session = factory(model=self.model) if factory else None
        return self._session

    def can(self, question):
        return (
            question.image_png is None
            and question.trunk is not None
            and (self._session is not None or can_session_classify(self.translator))
        )

    def why_not(self, question):
        if question.image_png is not None:
            return "a plain conversation carries no image"
        return f"{type(self.translator).__name__} cannot hold a classifier session"

    def ask(self, question):
        session = self.open()
        if session is None:
            raise NoBackend(self.why_not(question))
        if self._conversation is None or self._conversation.trunk != question.trunk:
            self._conversation = Conversation(session, question.trunk, session.budget())
        text, usage = _metered(
            self.translator, lambda: self._conversation.ask(question.prompt)
        )
        reply = Reply(parse_labels(text, question.candidates))
        reply.text = text
        reply.usage = usage
        return reply


# --------------------------------------------------------------------------
# The classifier
# --------------------------------------------------------------------------

DEFAULT_PREFER = ("schema", "session")
# `--plan-classify agent|all`: the session backend first (packet F, 260923).
SESSION_FIRST = ("session", "schema")


def default_backends(translator, model=None, session=None):
    """The backends a translator offers: schema where it can be asked a
    structured question, session where it can hold a conversation."""
    backends = []
    if translator is None:
        return backends
    if _supports_structured(translator) or hasattr(
        translator, "structured_json_with_image"
    ):
        backends.append(SchemaBackend(translator, model))
    if session is not None or can_session_classify(translator):
        backends.append(SessionBackend(translator, model, session=session))
    return backends


class Classifier:
    """One classify endpoint's backends, in the order this run prefers them.

    `translator` is the endpoint's translator (None for a backend that needs
    none, such as jev); `model` the model to ask, None for the translator's
    own. `source` is where the choice came from (cli, provider, run), for
    `describe`. `separate` says the translator is not the run's own, so its
    usage is reported on a line of its own.
    """

    def __init__(
        self,
        translator,
        model=None,
        *,
        prefer=DEFAULT_PREFER,
        source="run",
        backends=None,
        session=None,
        base=None,
        separate=False,
    ):
        self.translator = translator
        self._model = model
        self.source = source
        self.base = base
        self.separate = separate
        if backends is None:
            backends = default_backends(translator, model, session=session)
        self.backends = {backend.name: backend for backend in backends}
        order = [name for name in prefer if name in self.backends]
        order += [name for name in self.backends if name not in order]
        self.prefer = tuple(order)

    @property
    def model(self):
        return self._model or getattr(self.translator, "model", None)

    def backend(self, name):
        return self.backends.get(name)

    def ordered(self):
        return [self.backends[name] for name in self.prefer]

    def backend_for(self, question):
        """The backend that takes `question`, or `NoBackend` saying why not.

        The first in preference order that can, except that a structured
        channel with no JSON verdict yields a text question to a backend
        after it that can take it (the rule `session_classify_engaged` has
        always stated: below `json_object` and able to hold a conversation,
        the conversation is asked).
        """
        able = [backend for backend in self.ordered() if backend.can(question)]
        if not able:
            reasons = "; ".join(backend.why_not(question) for backend in self.ordered())
            what = "an image" if question.image_png is not None else "this question"
            raise NoBackend(
                f"no way to ask {self.describe()} {what}"
                + (f": {reasons}" if reasons else "")
            )
        first = able[0]
        if len(able) > 1 and getattr(first, "weak", lambda q: False)(question):
            return able[1]
        return first

    def text_backend(self):
        """Which backend a text question would go to, or None: asked before a
        question is built, by a caller whose paging depends on the answer."""
        probe = Question(prompt="", schema={}, trunk="", per_candidate={})
        try:
            return self.backend_for(probe).name
        except NoBackend:
            return None

    def ask(self, question):
        backend = self.backend_for(question)
        started = time.monotonic()
        raw = backend.ask(question)
        values, invalid, unknown = {}, {}, []
        if isinstance(raw, dict):
            for cid, value in raw.items():
                if cid not in question.candidates:
                    unknown.append(cid)
                    continue
                label = (
                    value.get(question.field)
                    if question.field and isinstance(value, dict)
                    else value
                )
                if isinstance(label, str) and label in question.candidates[cid]:
                    values[cid] = value
                else:
                    invalid[cid] = value
        usage = getattr(raw, "usage", None)
        if isinstance(usage, dict):
            usage = {**usage, "latency_s": round(time.monotonic() - started, 2)}
        return Answer(
            values=values,
            unknown_ids=tuple(unknown),
            invalid=invalid,
            backend=backend.name,
            usage=usage,
            confidence=dict(getattr(raw, "confidence", None) or {}),
            raw=raw,
            model=self.model,
        )

    @property
    def usage(self):
        """The meter this classifier's requests are counted on."""
        for backend in self.ordered():
            meter = getattr(backend, "usage", None)
            if meter is not None:
                return meter
        return getattr(self.translator, "usage", None)

    def where(self):
        base = self.base
        if base is None:
            base = getattr(self.translator, "api_base", None)
        return base or "the endpoint's default host"

    def describe(self, backend=None):
        """`"gpt-5.6-luna at https://... via schema (cli)"`."""
        # Never probes: a dry run describes the classifier without spending.
        via = backend or "/".join(self.prefer) or "nothing"
        return f"{self.model} at {self.where()} via {via} ({self.source})"
