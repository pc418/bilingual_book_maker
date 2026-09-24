"""The run's extra endpoints: the image model and the classify model.

A run has one endpoint of its own (`--api_base`, `--model`, `--key`, or a
`--provider` entry). Two kinds of step may want another one (owner, 260923):

- the steps that look at a page image (today the PDF route's region-role
  pass) want a vision model, named by `--img-model` / `--img-base-url` /
  `--img-key` or the provider entry's `img_*` fields;
- every classification step (plan mode's unit classifier, the PDF route's
  text-only structure decisions) wants a JSON-schema-capable model, named by
  `--classify-model` / `--classify-base-url` / `--classify-key` or the entry's
  `classify_*` fields.

Resolution, one chain each:

    image     --img-model  ->  provider img_model       ->  off
    classify  --classify-model  ->  provider classify_model  ->  the run's own

The image chain never falls back to the run's model (owner ruling 260923
22:30): a vision step runs only when a model was designated for it, so no
default needs a model that can read images. `--img-model none` turns a
provider entry's image model off for one run.

A choice with no address of its own is the run's endpoint with another
model: the same format, base and key. A choice with its own address speaks
the format that address resolves to (`infer_api_format`); anything but the
OpenAI shape is refused before anything is paid for, because the image and
schema channels exist only there. The key is `--img-key` / `--classify-key`,
else the entry's key variable, else the run's key when the address is the
run's, else what that address's format reads from the environment.

`jev` (TypeSafe's System One classifier) is a classify endpoint of its own
kind: `--classify-model jev` (or a `jev-*` model id, or a TypeSafe address)
selects it, with its own default address and key variables.

Nothing here builds a network client at import, and nothing imports the CLI
at module level (the CLI imports this module).
"""

from dataclasses import dataclass, replace
from os import environ as env
from urllib.parse import urlparse

SOURCE_CLI = "cli"
SOURCE_PROVIDER = "provider"
SOURCE_RUN = "run"
SOURCE_OFF = "off"

# The lead's text, verbatim (packet F, 260923): the six flags' help.
HELP_IMG_MODEL = "Vision model for the steps that look at a page image (today: correcting the layout detector's region roles on the PDF route). Resolution: this flag, else the provider entry's img_model, else off; 'none' turns a provider entry's image model off. The run's own model is never used for images unless named here."
HELP_IMG_BASE_URL = "Endpoint for --img-model when it is not the run's endpoint (OpenAI-compatible only)."
HELP_IMG_KEY = "API key for --img-base-url; defaults to the run's key when the endpoint is the same, else the key the endpoint's format reads from the environment."
HELP_CLASSIFY_MODEL = "Model for every classification step: plan mode's unit classifier and the PDF route's text-only structure decisions. Resolution: this flag, else the provider entry's classify_model, else the run's own model. --plan-classify-model is the old name of this flag. With --plan-classify agent the questions go over that model's session instead of a JSON schema."
HELP_CLASSIFY_BASE_URL = "Endpoint for --classify-model when it is not the run's endpoint (OpenAI-compatible only)."
HELP_CLASSIFY_KEY = "API key for --classify-base-url; same default rule as --img-key."

# The run's closing usage line for a classifier on an endpoint of its own
# (packet F: "Classifier ({model} at {base}): ..."); the image model's is
# `pipeline.messages.IMAGE_MODEL_USAGE`.
CLASSIFIER_USAGE = "Classifier ({model} at {base}): {summary}"

# The lead's text, verbatim (packet F, 260923).
IMG_ENDPOINT_UNSUPPORTED = (
    "--img-model needs an OpenAI-compatible endpoint; {base} resolves to the "
    "{api_format} format."
)
CLASSIFY_ENDPOINT_UNSUPPORTED = (
    "--classify-model needs an OpenAI-compatible endpoint; {base} resolves to "
    "the {api_format} format."
)
IMG_ENDPOINT_UNVERIFIED = (
    "{model} at {base} did not read the probe image ({verdict}); image steps "
    "are skipped this run."
)
# Not the lead's text: a base given without the model it is for.
IMG_BASE_WITHOUT_MODEL = (
    "--img-base-url names where --img-model is served, and no --img-model "
    "was given. Name the model too, or drop --img-base-url."
)
CLASSIFY_BASE_WITHOUT_MODEL = (
    "--classify-base-url names where --classify-model is served, and no "
    "--classify-model was given. Name the model too, or drop "
    "--classify-base-url."
)

IMG_OFF = "none"

# TypeSafe's System One models (docs.typesafe.ai/models, read 260923): the
# aliases `jev-latest` (-> jev-1.13.0) and `jev-preview`, and versioned ids
# such as `jev-1.13.0`, all at one endpoint. The literal `jev` means the
# default alias; any `jev-*` id, or a gateway's namespaced id whose last
# segment is one (`typesafe-ai/jev`), is passed through verbatim.
JEV_FORMAT = "jev"
JEV_ALIAS = "jev"
JEV_DEFAULT_MODEL = "jev-latest"
JEV_DEFAULT_BASE = "https://api.typesafe.ai"
JEV_HOST_SUFFIX = "typesafe.ai"
# Never BBM_API_KEY or a vendor's variable: those are translation keys, and
# sending one to TypeSafe would hand a credential to a host that never
# issued it. TYPESAFE_API_KEY is the name TypeSafe's own SDK reads.
JEV_ENV_KEYS = ("JEV_API_KEY", "TYPESAFE_API_KEY")


@dataclass(frozen=True)
class EndpointChoice:
    """One endpoint as resolved: what to call, where, with which key.

    `source` is where the choice came from (cli, provider, run, off);
    `own_base` whether the address is the choice's own rather than the
    run's. `key` is None when the caller asked not to resolve one (a dry
    run, which needs no credentials).
    """

    model: str
    api_base: str
    key: str
    api_format: str
    source: str
    own_base: bool = False

    def where(self):
        """The address to print: the base, or the format's own host."""
        return self.api_base or f"the {self.api_format} endpoint's default host"

    def describe(self):
        return f"{self.model} at {self.where()} ({self.source})"


def run_choice(model, api_base, key, api_format):
    """The run's own endpoint, as the other two chains fall back to it."""
    return EndpointChoice(
        model=model or "",
        api_base=api_base or "",
        key=key,
        api_format=api_format,
        source=SOURCE_RUN,
    )


def is_jev(model, api_base=""):
    """Whether (model, base) names TypeSafe's classifier."""
    # The id's last segment: a gateway that namespaces its models
    # (`typesafe-ai/jev` on Vercel's AI Gateway) serves the same classifier.
    name = (model or "").strip().lower().rsplit("/", 1)[-1]
    if name == JEV_ALIAS or name.startswith(JEV_ALIAS + "-"):
        return True
    host = (urlparse(api_base or "").hostname or "").lower()
    return host == JEV_HOST_SUFFIX or host.endswith("." + JEV_HOST_SUFFIX)


def _address(api_base, api_format):
    from book_maker.cli import _entry_address

    return _entry_address(api_base, api_format)


def _key(explicit, env_key, choice, run, with_key):
    """The key rule shared by both chains (see the module docstring)."""
    if not with_key:
        return None
    if explicit:
        return explicit
    if env_key and env.get(env_key):
        return env[env_key]
    if choice.api_format == JEV_FORMAT:
        found = next((env[n] for n in JEV_ENV_KEYS if env.get(n)), "")
        if found:
            return found
        raise SystemExit(
            f"No API key for the jev classifier. Pass --classify-key, or set "
            f"one of: {', '.join(((env_key,) if env_key else ()) + JEV_ENV_KEYS)}."
        )
    same_endpoint = not choice.own_base or (
        choice.api_format == run.api_format
        and _address(choice.api_base, choice.api_format)
        == _address(run.api_base, run.api_format)
    )
    if same_endpoint and run.key:
        return run.key
    from book_maker.cli import resolve_api_key

    return resolve_api_key(
        choice.api_format,
        None,
        choice.api_base,
        (env_key,) if env_key else (),
    )


def _choose(model, base, run, source, *, image):
    """The endpoint (format and base) for `model`, before the key."""
    from book_maker.cli import infer_api_format, normalize_api_base

    unsupported = IMG_ENDPOINT_UNSUPPORTED if image else CLASSIFY_ENDPOINT_UNSUPPORTED
    if is_jev(model, base):
        if image:
            raise SystemExit(
                unsupported.format(base=base or JEV_DEFAULT_BASE, api_format=JEV_FORMAT)
            )
        wire = JEV_DEFAULT_MODEL if model.strip().lower() == JEV_ALIAS else model
        return EndpointChoice(
            model=wire,
            api_base=(base or JEV_DEFAULT_BASE).rstrip("/"),
            key=None,
            api_format=JEV_FORMAT,
            source=source,
            own_base=True,
        )
    if base:
        api_format = infer_api_format(base, model)
        if api_format != "openai":
            raise SystemExit(unsupported.format(base=base, api_format=api_format))
        return EndpointChoice(
            model=model,
            api_base=normalize_api_base(base, api_format),
            key=None,
            api_format=api_format,
            source=source,
            own_base=True,
        )
    # The run's endpoint with another model. Its format has to have the
    # channel the step asks through: images only on the OpenAI shape;
    # classification on any route that can be asked a question (the MT
    # engines are refused by compatibility row A10 before this).
    if image and not _reads_images(run.api_format):
        raise SystemExit(
            unsupported.format(base=run.where(), api_format=run.api_format)
        )
    return EndpointChoice(
        model=model,
        api_base=run.api_base,
        key=None,
        api_format=run.api_format,
        source=source,
        own_base=False,
    )


def _reads_images(api_format):
    from book_maker.translator import FORMAT_DICT

    cls = FORMAT_DICT.get(api_format)
    return cls is not None and hasattr(cls, "structured_json_with_image")


def resolve_image_endpoint(options, run, provider, *, with_key=True):
    """The image endpoint, or None when image steps are off.

    `options` carries `img_model`, `img_base_url`, `img_key`; `run` is the
    run's `EndpointChoice`; `provider` the `ProviderRoute` or None.
    """
    model = (getattr(options, "img_model", None) or "").strip()
    base = (getattr(options, "img_base_url", None) or "").strip()
    explicit_key = getattr(options, "img_key", None) or ""
    if model.lower() == IMG_OFF:
        return None
    if base and not model:
        raise SystemExit(IMG_BASE_WITHOUT_MODEL)
    env_key = ""
    if model:
        source = SOURCE_CLI
    elif provider is not None and provider.img_model:
        model, base = provider.img_model, provider.img_base_url
        env_key, source = provider.img_env_key, SOURCE_PROVIDER
    else:
        return None
    choice = _choose(model, base, run, source, image=True)
    return replace(choice, key=_key(explicit_key, env_key, choice, run, with_key))


def resolve_classify_endpoint(options, run, provider, *, with_key=True):
    """The classify endpoint: cli, else the provider entry, else the run's.

    Never None: the run's own translator is the last link, as it always
    was. `options.classify_model` is either spelling of the flag
    (`--plan-classify-model` is the old name).
    """
    model = (getattr(options, "classify_model", None) or "").strip()
    base = (getattr(options, "classify_base_url", None) or "").strip()
    explicit_key = getattr(options, "classify_key", None) or ""
    if base and not model:
        raise SystemExit(CLASSIFY_BASE_WITHOUT_MODEL)
    env_key = ""
    if model:
        source = SOURCE_CLI
    elif provider is not None and provider.classify_model:
        model, base = provider.classify_model, provider.classify_base_url
        env_key, source = provider.classify_env_key, SOURCE_PROVIDER
    else:
        return run
    choice = _choose(model, base, run, source, image=False)
    return replace(choice, key=_key(explicit_key, env_key, choice, run, with_key))


def build_translator(choice, options, language, prompt_config=None):
    """A translator instance for `choice`, built the way the loaders build one.

    The same constructor arguments a loader passes that matter outside
    translation (temperature, source language, the prompt sections, the
    session budget a classifier conversation rolls over at), the model list
    of the one model, the run's prices, and `--no-thinking` where the route
    carries it. `--extra_body` / `--extra_headers` follow only on the run's
    own address: a header block is where a gateway's credential goes, and
    it must not travel to another host.
    """
    import json

    from book_maker.translator import FORMAT_DICT
    from book_maker.utils import prompt_config_to_kwargs

    cls = FORMAT_DICT[choice.api_format]
    translator = cls(
        choice.key or "",
        language,
        api_base=choice.api_base or None,
        context_compact_at=getattr(options, "context_compact_at", None),
        no_context_compact=getattr(options, "no_context_compact", False),
        temperature=getattr(options, "temperature", 1.0),
        source_lang=getattr(options, "source_lang", "auto"),
        **prompt_config_to_kwargs(prompt_config),
    )
    if not choice.own_base:
        extras = {}
        for dest in ("extra_body", "extra_headers"):
            raw = getattr(options, dest, None)
            if not raw:
                continue
            try:
                parsed = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue  # the run's own parse refuses it, in its words
            if isinstance(parsed, dict):
                extras[dest] = parsed
        if extras and getattr(translator, "SUPPORTS_REQUEST_EXTRAS", False):
            translator.set_request_extras(**extras)
    prices = getattr(options, "price_table", None)
    if prices is not None and hasattr(translator, "usage"):
        translator.usage.prices = prices
    if getattr(options, "no_thinking", False) and getattr(
        translator, "SUPPORTS_REQUEST_EXTRAS", False
    ):
        translator.no_thinking = True
    if getattr(options, "quiet", False) and hasattr(translator, "quiet"):
        translator.quiet = True
    if choice.model:
        translator.set_model_list([choice.model])
    return translator


def build_classifier(
    choice, run_translator, options, language, prompt_config=None, *, prefer=None
):
    """The run's `Classifier`, from its classify choice.

    The run's own choice asks the run's translator, as plan mode always did.
    A named model gets a translator of its own (`build_translator`), so its
    requests are metered apart and reported on their own line. `jev` has
    only its own backend. `prefer` is the backend order (the session first
    under `--plan-classify agent|all`).
    """
    from book_maker.classifier import DEFAULT_PREFER, Classifier, JevBackend

    prefer = prefer or DEFAULT_PREFER
    if choice is None or choice.source == SOURCE_RUN:
        return Classifier(
            run_translator,
            None,
            prefer=prefer,
            source=SOURCE_RUN,
            base=getattr(choice, "api_base", None) or None,
        )
    if choice.api_format == JEV_FORMAT:
        return Classifier(
            None,
            choice.model,
            backends=[JevBackend(choice.model, choice.key, choice.api_base)],
            source=choice.source,
            base=choice.api_base,
            separate=True,
        )
    translator = build_translator(choice, options, language, prompt_config)
    return Classifier(
        translator,
        None,
        prefer=prefer,
        source=choice.source,
        base=choice.api_base or None,
        separate=True,
    )
