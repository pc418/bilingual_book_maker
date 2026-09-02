import argparse
import json
import os
import sys
from os import environ as env
from pathlib import Path
from urllib.parse import urlparse

from rich import print
from rich.markup import escape

from book_maker.glossary import Glossary
from book_maker.loader import BOOK_LOADER_DICT
from book_maker.legacy_cli import translate_legacy_argv
from book_maker.loader.ledger import PlanLedgerError
from book_maker.provider_loader import resolve_provider
from book_maker.translator import FORMAT_DICT, LLM_FORMATS
from book_maker.translator.base_translator import PriceTable
from book_maker.translator import orcarouter_translator as orcarouter
from book_maker.utils import LANGUAGES, TO_LANGUAGE_CODE

# Where each format looks for a key when --key is absent. $BBM_API_KEY is the
# one this project asks for; the rest are the variables people already have
# exported for that vendor.
FORMAT_ENV_KEYS = {
    "openai": ("BBM_API_KEY", "OPENAI_API_KEY", "BBM_OPENAI_API_KEY"),
    "anthropic": ("BBM_API_KEY", "ANTHROPIC_API_KEY", "BBM_CLAUDE_API_KEY"),
    "caiyun": ("BBM_API_KEY", "BBM_CAIYUN_API_KEY"),
    "deepl": ("BBM_API_KEY", "BBM_DEEPL_API_KEY"),
}

# Formats that will not work at all without a credential. The others are
# public endpoints (google, deeplfree, tencent) or carry their own address
# instead of a key (customapi).
FORMATS_REQUIRING_KEY = ("openai", "anthropic", "caiyun", "deepl")

LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0", "host.docker.internal")

# The loaders that actually forward context settings into the translator. The
# others accept `context_flag` and drop it, so a session budget or a glossary
# passed with them would silently do nothing.
CONTEXT_AWARE_BOOK_TYPES = ("epub", "md", "markdown")

# LLM formats that can resolve a model on their own, so --model is optional.
MODEL_OPTIONAL_FORMATS = ("codex",)

# The model a format falls back to when the command names none. Only the
# openai format has one: it has an obvious cheapest current model, and that
# is what a command carrying nothing but a key used to run before the model
# presets went away. Anthropic has no equivalent, so it keeps asking.
DEFAULT_MODELS = {"openai": "gpt-5.6-luna"}

# `--model codex` selects the format rather than a model id. The sidecar then
# picks its own default, exactly as `--api_format codex` with no --model does.
CODEX_MODEL_ALIASES = ("codex",)


def infer_api_format(api_base, model=""):
    """Which wire format the endpoint speaks, guessed from host then model.

    The address is the stronger signal: whoever names an endpoint has said
    where the request goes. Only when no endpoint is named does the model id
    decide, and there the giveaway is `claude` or `anthropic` — including a
    namespaced `anthropic/claude-sonnet-4-6`, which is still an Anthropic
    model however it is addressed.

    Guessing wrong is cheap: a gateway that serves those ids over the OpenAI
    shape answers the first anthropic request with a 404, and the route falls
    back once (see `Claude._build_openai_fallback`). `--api_format` overrides
    all of this outright.
    """
    name = (model or "").strip().lower()
    # `codex` is not an endpoint, so an --api_base cannot imply it and it must
    # be recognised before the host is consulted. Naming it as the model is
    # also how upstream spells this, and how the other non-endpoint engines
    # have always been selected.
    if name in CODEX_MODEL_ALIASES:
        return "codex"
    if api_base:
        host = (urlparse(api_base).hostname or "").lower()
        return "anthropic" if host.endswith("anthropic.com") else "openai"
    if "claude" in name or "anthropic" in name:
        return "anthropic"
    return "openai"


# Endpoint paths people paste in along with the base. The SDKs build these
# themselves, so a base carrying one produces /v1/chat/completions/chat/completions.
_ENDPOINT_SUFFIXES = ("/chat/completions", "/messages", "/completions")


def normalize_api_base(api_base, api_format):
    """Trim a pasted request path off `--api_base`.

    Copying the URL out of a provider's docs or a curl line is the common
    way to get this flag, and those URLs end at the endpoint rather than the
    base. Trailing slashes go too, so `.../v1/` and `.../v1` are one thing.

    Only for the SDK-backed formats, which build the request path themselves.
    `customapi` posts to this URL verbatim, so a path is the address, not
    noise to strip.
    """
    if not api_base or api_format not in LLM_FORMATS:
        return api_base
    base = api_base.strip().rstrip("/")
    for suffix in _ENDPOINT_SUFFIXES:
        if base.lower().endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
            break
    return base


def is_local_endpoint(api_base):
    if not api_base:
        return False
    return (urlparse(api_base).hostname or "").lower() in LOCAL_HOSTS


def resolve_api_key(api_format, explicit_key, api_base, extra_env_keys=()):
    """The key to use, or a loud failure naming where one was looked for.

    `extra_env_keys` carries the variables an old command line implies — a
    translated `--model groq` still authenticates from BBM_GROQ_API_KEY.
    They come first: they name the endpoint being called, so with both
    OPENAI_API_KEY and BBM_GROQ_API_KEY exported, a groq command must not
    hand the OpenAI key to Groq.
    """
    env_names = tuple(extra_env_keys) + FORMAT_ENV_KEYS.get(
        api_format, ("BBM_API_KEY",)
    )
    key = explicit_key or next((env[n] for n in env_names if env.get(n)), "")
    if key:
        return key

    # A server on this machine is not authenticating anyone, but the OpenAI
    # SDK refuses to construct without some string.
    if is_local_endpoint(api_base):
        return "local"

    if api_format in FORMATS_REQUIRING_KEY:
        raise SystemExit(
            f"No API key for the {api_format} endpoint. Pass --key, or set "
            f"one of: {', '.join(env_names)}."
        )
    return ""


def apply_provider(options):
    """Fill in the endpoint flags `--provider` covers, and name its key variable.

    Only what the command left out: a provider is a shorthand for flags, so
    every flag actually typed outranks it. Returns the variables to consult
    for the key, ahead of the format's conventional ones — the entry names
    the endpoint being called, so its own variable is the right one.
    """
    if not options.provider:
        return ()
    try:
        route = resolve_provider(options.provider)
    except ValueError as err:
        raise SystemExit(str(err))
    options.api_format = options.api_format or route.api_format
    options.api_base = options.api_base or route.api_base
    # Prices are the entry's to know and the meter's to apply; they ride
    # on the options until the translator exists.
    options.price_table = (
        PriceTable(route.prices, route.currency) if route.prices else None
    )
    if route.models and not options.model and not options.model_list:
        # One model belongs in --model; several rotate, first one first.
        if len(route.models) == 1:
            options.model = route.models[0]
        else:
            options.model_list = ",".join(route.models)
    return (route.env_key,) if route.env_key else ()


def named_models(options):
    """The models the command names, in the order it named them."""
    return [
        name.strip()
        for name in (
            options.model_list.split(",")
            if options.model_list
            else [options.model or ""]
        )
        if name.strip()
    ]


def resolve_endpoint(options):
    """`(model names, api format, key variables)` for this command.

    `options.api_base` is left holding the address the run will use.

    Order matters: `--provider` is shorthand for flags the command left out,
    so it is applied only after everything the command itself said — a route
    the model name selected included.
    """
    # A model may be named once, in either flag. Accepting both would leave
    # two answers to "which model is this run using".
    if options.model and options.model_list:
        raise SystemExit(
            "Name the model once: --model for a single model, --model_list "
            "only to rotate across several."
        )
    model_names = named_models(options)

    # A model name that selects a route — `codex`, `orcarouter`,
    # `orcarouter/<id>` — is resolved before `--provider` fills anything in.
    # Such a name says where the request goes; a provider entry only says
    # where it would have gone otherwise, so the endpoint it stands for must
    # not be allowed to capture a route the command named. OrcaRouter is one
    # gateway at one address, so its model id is enough to address it.
    orcarouter_env_keys = ()
    if model_names:
        route = orcarouter.resolve(model_names[0], options.api_base)
        if route:
            model_names[0], options.api_base = route
            orcarouter_env_keys = (orcarouter.ENV_KEY,)
            # the whole list rotates against that one gateway, so every entry
            # goes through the same redirect: the translator dedupes by
            # string, and `orcarouter` beside `orcarouter/auto` is one model
            # named twice, which the endpoint's model check would reject
            model_names = [
                (orcarouter.resolve(name, options.api_base) or (name,))[0]
                for name in model_names
            ]
        if route or model_names[0].lower() in CODEX_MODEL_ALIASES:
            # settled from the route itself, so the provider's api_style
            # cannot answer a question the model name already answered
            options.api_format = options.api_format or infer_api_format(
                options.api_base, model_names[0]
            )

    provider_env_keys = apply_provider(options)
    if not model_names:
        # the entry names the models when the command named none
        model_names = named_models(options)

    api_format = options.api_format or infer_api_format(
        options.api_base, model_names[0] if model_names else ""
    )
    options.api_base = normalize_api_base(options.api_base, api_format)
    if not model_names and api_format in DEFAULT_MODELS:
        model_names = [DEFAULT_MODELS[api_format]]
    return model_names, api_format, orcarouter_env_keys + provider_env_keys


def get_book_type(book_name):
    return Path(book_name).suffix.lower().lstrip(".")


def parse_prompt_arg(prompt_arg):
    prompt = None
    if prompt_arg is None:
        return prompt

    # Check if it's a path to a markdown file (PromptDown format)
    if prompt_arg.endswith(".md") and os.path.exists(prompt_arg):
        try:
            from promptdown import StructuredPrompt

            structured_prompt = StructuredPrompt.from_promptdown_file(prompt_arg)

            # Initialize our prompt structure
            prompt = {}

            # Handle developer_message or system_message
            # Developer message takes precedence if both are present
            if (
                hasattr(structured_prompt, "developer_message")
                and structured_prompt.developer_message
            ):
                prompt["system"] = structured_prompt.developer_message
            elif (
                hasattr(structured_prompt, "system_message")
                and structured_prompt.system_message
            ):
                prompt["system"] = structured_prompt.system_message

            # Extract user message from conversation
            if (
                hasattr(structured_prompt, "conversation")
                and structured_prompt.conversation
            ):
                for message in structured_prompt.conversation:
                    if message.role.lower() == "user":
                        prompt["user"] = message.content
                        break

            # Ensure we found a user message
            if "user" not in prompt or not prompt["user"]:
                raise ValueError(
                    "PromptDown file must contain at least one user message"
                )

            print(f"Successfully loaded PromptDown file: {prompt_arg}")

            # Validate required placeholders
            if any(c not in prompt["user"] for c in ["{text}"]):
                raise ValueError(
                    "User message in PromptDown must contain `{text}` placeholder"
                )

            return prompt
        except Exception as e:
            # Falling through left `prompt` half-built and the next line
            # died on `prompt["user"]` with a KeyError traceback — after
            # the run had already printed that the file loaded. The pinned
            # promptdown reads the block form only; its table form (which
            # this repo's own prompt_md.prompt.md still uses) parses to a
            # conversation with no user message.
            raise ValueError(
                f"could not read the PromptDown file {prompt_arg}: {e}. "
                f"Write the conversation in block form -- a line reading "
                f"`**User:**` followed by the template, which must contain "
                f"`{{text}}`."
            ) from e

    # Existing parsing logic for JSON strings and other formats
    if not any(prompt_arg.endswith(ext) for ext in [".json", ".txt", ".md"]):
        try:
            # user can define prompt by passing a json string
            # eg: --prompt '{"system": "You are a professional translator who translates computer technology books", "user": "Translate \`{text}\` to {language}"}'
            prompt = json.loads(prompt_arg)
        except json.JSONDecodeError:
            # if not a json string, treat it as a template string
            prompt = {"user": prompt_arg}

    elif os.path.exists(prompt_arg):
        if prompt_arg.endswith(".txt"):
            # if it's a txt file, treat it as a template string
            with open(prompt_arg, encoding="utf-8") as f:
                prompt = {"user": f.read()}
        elif prompt_arg.endswith(".json"):
            # if it's a json file, treat it as a json object
            # eg: --prompt prompt_template_sample.json
            with open(prompt_arg, encoding="utf-8") as f:
                prompt = json.load(f)
    else:
        raise FileNotFoundError(f"{prompt_arg} not found")

    # if prompt is None or any(c not in prompt["user"] for c in ["{text}", "{language}"]):
    if prompt is None or any(c not in prompt["user"] for c in ["{text}"]):
        raise ValueError("prompt must contain `{text}`")

    if "user" not in prompt:
        raise ValueError("prompt must contain the key of `user`")

    if (prompt.keys() - {"user", "system", "style"}) != set():
        raise ValueError(
            "prompt can only contain the keys of `user`, `system` and `style`"
        )

    print("prompt config:", prompt)
    return prompt


# Below this a window cannot hold even one paragraph with its translation, so
# every unit would trigger a paid handoff report.
MIN_COMPACT_BUDGET = 500


def compact_budget(value):
    """argparse type for --context-compact-at: a usable positive budget."""
    try:
        budget = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected a whole number, got {value!r}")
    if budget < MIN_COMPACT_BUDGET:
        raise argparse.ArgumentTypeError(
            f"a compact budget of {budget} is too small to be useful; use at "
            f"least {MIN_COMPACT_BUDGET} estimated tokens (2500 is the "
            f"cheapest setting on most endpoints)"
        )
    return budget


def resolve_context_mode(options):
    """`(context_flag, context_mode)` from the parsed `--use_context` value.

    `--use_context` used to be a bare switch and still may be: absent means no
    context, bare means the window mode it has always meant, and only an
    explicit `session` selects cached history.
    """
    mode = getattr(options, "context_mode", None)
    return (mode is not None), mode


# The plan is built from LLM verdicts on a pinned JSON Schema, so it is only
# worth entering automatically where the schema is known to be applied. That
# is established by the capability probe, which speaks the OpenAI wire format
# and grades one endpoint; every other route (anthropic, google, deepl, codex,
# ...) has no such verdict to offer and stays in tag mode.
PLAN_AUTO_FORMAT = "openai"


def resolve_plan_mode(book_type, api_format, translate_tags_given, probe):
    """What `--plan-classify auto` means for this run: `(mode, reason)`.

    `mode` is "model" (plan the book) or "none" (translate the
    `--translate-tags` selection); `reason` is the one line the run prints
    about it. `probe` is called — at most once, and only when its answer can
    still change the outcome — for the endpoint's graded schema support; None
    means this translator has no probe.
    """
    if book_type != "epub":
        return "none", f"plan mode needs an epub; this is a {book_type} book"
    if translate_tags_given:
        return "none", "--translate-tags names what to translate"
    if api_format != PLAN_AUTO_FORMAT:
        return "none", f"the {api_format} route has no JSON-schema verdict"
    if probe is None:
        return "none", "this endpoint offers no JSON-schema verdict"
    try:
        verdict = probe()
    except Exception as e:
        # A probe failure is not a reason to stop: the run has a working
        # answer (tag mode) and whatever is wrong with the endpoint will
        # surface loudly at the first translation request.
        return "none", f"the JSON-schema probe failed: {e}"
    if verdict != "strict":
        return "none", (
            f"the endpoint does not verify a strict JSON schema "
            f"({verdict or 'no schema support'})"
        )
    return "model", "endpoint verified strict JSON schema"


def build_parser():
    translate_format_list = list(FORMAT_DICT.keys())
    # No prefix abbreviation: `--model` must not resolve to `--model_list`.
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument(
        "--book_name",
        dest="book_name",
        type=str,
        help="path of the book/source file to be translated",
    )
    parser.add_argument(
        "--book_from",
        dest="book_from",
        type=str,
        choices=["kobo"],  # support kindle later
        metavar="E-READER",
        help="e-reader type, available: {%(choices)s}",
    )
    parser.add_argument(
        "--device_path",
        dest="device_path",
        type=str,
        help="Path of e-reader device",
    )
    ########## ENDPOINT ##########
    parser.add_argument(
        "--key",
        dest="key",
        type=str,
        default="",
        help="API key for the endpoint. Several comma-separated keys are "
        "rotated to go beyond per-key rate limits. Falls back to $BBM_API_KEY, "
        "then to the format's conventional variable ($OPENAI_API_KEY, "
        "$ANTHROPIC_API_KEY)",
    )
    parser.add_argument(
        "--test",
        dest="test",
        action="store_true",
        help="only the first 10 paragraphs will be translated, for testing",
    )
    parser.add_argument(
        "--test_num",
        dest="test_num",
        type=int,
        default=10,
        help="how many paragraphs will be translated for testing",
    )
    parser.add_argument(
        "-m",
        "--model",
        dest="model",
        type=str,
        default=None,
        metavar="MODEL",
        help="model id, exactly as the endpoint names it (e.g. gpt-5-mini, "
        "claude-sonnet-4-6, or a namespaced openai/gpt-5-mini). Old alias "
        "values are translated to their model with a note. Defaults to "
        "gpt-5.6-luna on the openai format; the anthropic format needs an id",
    )
    parser.add_argument(
        "--api_format",
        dest="api_format",
        type=str,
        default=None,
        choices=translate_format_list,
        metavar="FORMAT",
        help="wire format the endpoint speaks, available: {%(choices)s}. "
        "Inferred from --api_base when omitted (anthropic hosts -> anthropic, "
        "everything else -> openai)",
    )
    parser.add_argument(
        "--language",
        type=str,
        choices=sorted(LANGUAGES.keys())
        + sorted([k.title() for k in TO_LANGUAGE_CODE]),
        default="zh-hans",
        metavar="LANGUAGE",
        help="language to translate to, available: {%(choices)s}",
    )
    parser.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        help="if program stop unexpected you can use this to resume",
    )
    parser.add_argument(
        "-p",
        "--proxy",
        dest="proxy",
        type=str,
        default="",
        help="use proxy like http://127.0.0.1:7890",
    )
    # The endpoint. Everything else about the route is inferred from it.
    parser.add_argument(
        "--api_base",
        metavar="API_BASE_URL",
        dest="api_base",
        type=str,
        help="endpoint to translate against, e.g. https://api.openai.com/v1, "
        "https://api.anthropic.com, a gateway, or http://localhost:11434/v1 "
        "for ollama. Defaults to the format's official host",
    )
    parser.add_argument(
        "--provider",
        dest="provider",
        type=str,
        default="",
        help="named endpoint from bbm_providers.json (this directory) or "
        "~/.bbm/providers.json: its base_url, api_style, default_models and "
        "env_key stand in for --api_base, --api_format, --model and the key. "
        "Anything you pass explicitly wins",
    )
    parser.add_argument(
        "--exclude_filelist",
        dest="exclude_filelist",
        type=str,
        default="",
        help="if you have more than one file to exclude, please use comma to split them, example: --exclude_filelist 'nav.xhtml,cover.xhtml'",
    )
    parser.add_argument(
        "--only_filelist",
        dest="only_filelist",
        type=str,
        default="",
        help="if you only have a few files with translations, please use comma to split them, example: --only_filelist 'nav.xhtml,cover.xhtml'",
    )
    parser.add_argument(
        "--translate-tags",
        dest="translate_tags",
        type=str,
        # None, not "p", so a run can tell a typed selection from an
        # untouched flag: naming tags is how a user opts out of the automatic
        # plan, and `--translate-tags p` must mean that too. Normalized to
        # "p" immediately after parsing.
        default=None,
        help="which tags to translate, example --translate-tags p,blockquote "
        "(default: p). Ignored in plan mode — see --plan-classify",
    )
    parser.add_argument(
        "--plan-dry-run",
        dest="plan_dry_run",
        action="store_true",
        default=False,
        help="build and print the translation plan (per-signature coverage "
        "table), write <book>_plan.json, and exit without translating "
        "(epub only)",
    )
    parser.add_argument(
        "--plan-min-coverage",
        dest="plan_min_coverage",
        type=float,
        default=0.5,
        help="in plan mode, abort if the plan covers less than this fraction "
        "of the book's text (default 0.5)",
    )
    parser.add_argument(
        "--poetry-group-size",
        dest="poetry_group_size",
        type=int,
        default=8,
        help="in plan mode, max poetry lines batched per translation request "
        "(default 8)",
    )
    parser.add_argument(
        "--plan-classify",
        dest="plan_classify",
        # "most" is the old name of "all", still parsed and mapped in main()
        # with a notice. The metavar is what --help and the usage line show,
        # so a retired spelling stays out of the advertised surface.
        choices=["auto", "none", "all", "model", "agent", "most"],
        metavar="{auto,none,all,model,agent}",
        default="auto",
        help="coverage-complete plan mode (epub only): partition the whole "
        "book, then decide which tag signatures are worth translating. "
        "'auto' (default): plan the book as 'model' when it is an epub and "
        "the endpoint is verified to apply a strict JSON schema, otherwise "
        "translate the --translate-tags selection; a plan that cannot be "
        "completed falls back to that selection too. "
        "'none': no plan — translate the --translate-tags "
        "selection as usual. "
        "'all': translate the whole partition, no classification, no plan "
        "file. "
        "'model': an LLM rules on every undecided signature, then the run "
        "continues and translates the book; unresolved rows stop it instead. "
        "'agent': write the plan JSON with samples, print instructions to "
        "paste into a coding-agent session, and stop before translating — "
        "rerun the same command afterwards to translate",
    )
    parser.add_argument(
        "--plan-classify-model",
        dest="plan_classify_model",
        type=str,
        default="",
        help="model for plan-signature classification (default: the "
        "translating model). When set explicitly, a classification failure "
        "aborts the run instead of falling back to the heuristic plan",
    )
    parser.add_argument(
        "--exclude-translate-tags",
        dest="exclude_translate_tags",
        type=str,
        default="sup,code",
        help="Exclude content within specified HTML tags from translation. Use comma to separate multiple tags. Default: sup,code. Example: --exclude-translate-tags code,pre",
    )
    parser.add_argument(
        "--allow_navigable_strings",
        dest="allow_navigable_strings",
        action="store_true",
        default=False,
        help="allow NavigableStrings to be translated",
    )
    parser.add_argument(
        "--prompt",
        dest="prompt_arg",
        type=str,
        metavar="PROMPT_ARG",
        help="used for customizing the prompt. It can be the prompt template string, or a path to the template file. The valid placeholders are `{text}` and `{language}`.",
    )
    parser.add_argument(
        "--accumulated_num",
        dest="accumulated_num",
        type=int,
        default=1,
        help="""Wait for how many tokens have been accumulated before starting the translation.
gpt3.5 limits the total_token to 4090.
For example, if you use --accumulated_num 1600, maybe openai will output 2200 tokens
and maybe 200 tokens for other messages in the system messages user messages, 1600+2200+200=4000,
So you are close to reaching the limit. You have to choose your own value, there is no way to know if the limit is reached before sending
""",
    )
    parser.add_argument(
        "--translation_style",
        dest="translation_style",
        type=str,
        help="""ex: --translation_style "color: #808080; font-style: italic;" """,
    )
    parser.add_argument(
        "--translation_color",
        dest="translation_color",
        type=str,
        help="color for translated text, e.g. --translation_color '#1e90ff' or --translation_color 'red'",
    )
    parser.add_argument(
        "--batch_size",
        dest="batch_size",
        type=int,
        help="how many text units will be translated by aggregated translation for supported loaders",
    )
    parser.add_argument(
        "--pdf_layout",
        dest="pdf_layout",
        choices=["none", "top-bottom", "side-by-side", "all"],
        default="none",
        help="PDF output layout for PDF inputs: top-bottom, side-by-side, all, or none",
    )
    parser.add_argument(
        "--retranslate",
        dest="retranslate",
        nargs=4,
        type=str,
        help="""--retranslate "$translated_filepath" "file_name_in_epub" "start_str" "end_str"
        Retranslate from start_str through end_str. All four arguments are required;
        pass an empty end_str ('') to retranslate only the start_str tag, and an empty
        file_name_in_epub ('') to find the internal filename automatically.
""",
    )
    parser.add_argument(
        "--single_translate",
        action="store_true",
        help="output translated book, no bilingual",
    )
    parser.add_argument(
        "--sentence_mode",
        action="store_true",
        help="translate sentence by sentence within each paragraph instead of the whole paragraph at once",
    )
    parser.add_argument(
        "--use_context",
        dest="context_mode",
        nargs="?",
        const="window",
        default=None,
        choices=("window", "session"),
        help="carry earlier paragraphs into each request for narrative "
        "consistency. Bare (or 'window'): re-send the last few "
        "source/translation pairs, costing ~200 extra tokens per request. "
        "'session': keep one append-only history instead, so an endpoint "
        "with prompt caching re-reads it at its cache rate and the context "
        "can grow to chapter length for less money — compacted into a "
        "handoff report at --context-compact-at",
    )
    parser.add_argument(
        "--context_paragraph_limit",
        dest="context_paragraph_limit",
        type=int,
        default=0,
        help="window mode only: how many paragraph pairs to re-send",
    )
    parser.add_argument(
        "--context-compact-at",
        dest="context_compact_at",
        type=compact_budget,
        default=None,
        help="session mode only: estimated-token budget for the history "
        "before it is compacted into a translator handoff report. Default: "
        "8000, which costs about what window mode costs for several times "
        "the context; 2500 is the cheapest setting on most endpoints",
    )
    parser.add_argument(
        "--glossary",
        dest="glossary",
        type=str,
        default=None,
        # Undocumented on purpose. A pinned 'term → translation' line tells the
        # model to substitute that rendering verbatim, which is also how you
        # would make a translation quietly say something the source does not.
        # The flag still works for anyone who reads the source; it is simply
        # not advertised in --help or the READMEs.
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--glossary-auto",
        dest="glossary_auto",
        action="store_true",
        help=argparse.SUPPRESS,  # see --glossary above
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=1.0,
        help="sampling temperature. Not sent when it equals the API default, "
        "and dropped for models that reject an explicit one",
    )
    parser.add_argument(
        "--source_lang",
        type=str,
        default="auto",
        help="source language, for endpoints that want it stated (default: auto-detect)",
    )
    parser.add_argument(
        "--block_size",
        type=int,
        default=-1,
        help="merge multiple paragraphs into one block, may increase accuracy and speed up the process, but disturb the original format, must be used with `--single_translate`",
    )
    parser.add_argument(
        "--model_list",
        type=str,
        dest="model_list",
        help="several model IDs to rotate across, comma-separated, to spread "
        "rate limits. Kept for compatibility with older commands; a single "
        "model belongs in --model",
    )
    parser.add_argument(
        "--batch",
        dest="batch_flag",
        action="store_true",
        help="Enable batch translation using ChatGPT's batch API for improved efficiency",
    )
    parser.add_argument(
        "--batch-use",
        dest="batch_use_flag",
        action="store_true",
        help="Use pre-generated batch translations to create files. Run with --batch first before using this option",
    )
    parser.add_argument(
        "--parallel-workers",
        dest="parallel_workers",
        type=int,
        default=1,
        help="Number of parallel workers for EPUB chapters or Markdown batches/sections. Use 2-4 for better performance. Default: 1",
    )
    parser.add_argument(
        "--extra_body",
        dest="extra_body",
        type=str,
        default="",
        help='JSON string of extra body parameters to pass to the API. Example: --extra_body \'{"chat_template_kwargs": {"enable_thinking": false}}\'',
    )
    parser.add_argument(
        "--quiet",
        dest="quiet",
        action="store_true",
        help="suppress progress bars and per-paragraph translation echoes "
        "(for log files and non-interactive runs; reports and errors still "
        "print). Currently epub only.",
    )
    return parser


def parse_args(argv):
    return build_parser().parse_args(argv)


def main():
    # Old command lines are rewritten into the endpoint surface before the
    # parser sees them; see book_maker/legacy_cli.py.
    legacy = translate_legacy_argv(sys.argv[1:])
    for notice in legacy.notices:
        print(f"[yellow]deprecated:[/yellow] {escape(notice)}")

    options = parse_args(legacy.argv)
    options.context_flag, options.context_mode = resolve_context_mode(options)
    # A named tag selection is an opt-out from the automatic plan; the
    # parser's None is the only way to tell one from the "p" default.
    translate_tags_given = options.translate_tags is not None
    if not translate_tags_given:
        options.translate_tags = "p"
    if options.plan_classify == "most":
        # renamed: the mode translates the whole partition, and "all" is what
        # that is. Old command lines keep working, quietly corrected once.
        print("[yellow]--plan-classify most is now --plan-classify all[/yellow]")
        options.plan_classify = "all"

    # Kobo mode supplies the source book itself. Resolve it before validating
    # --book_name so users do not need a meaningless placeholder file.
    if options.book_from == "kobo":
        from book_maker import obok

        if options.device_path is None:
            raise Exception(
                "Device path is not given, please specify the path by --device_path <DEVICE_PATH>",
            )
        options.book_name = obok.cli_main(options.device_path)

    if not options.book_name:
        print("Error: please provide the path of your book using --book_name <path>")
        exit(1)
    if not os.path.isfile(options.book_name):
        print(f"Error: the book {options.book_name!r} does not exist.")
        exit(1)

    if options.plan_dry_run:
        # No translation happens, so no credentials are needed: build the
        # plan straight from the file, honoring the file filters.
        if get_book_type(options.book_name) != "epub":
            print("[bold red]--plan-dry-run only works with epub books[/bold red]")
            exit(1)
        from ebooklib import epub as _epub

        from book_maker.loader.plan import build_plan, is_fixed_layout

        from book_maker.loader.epub_loader import check_file_filters_against

        book = _epub.read_epub(options.book_name)
        # a typo in a filter is answerable here too, before a plan that
        # would silently not honor it is written
        check_file_filters_against(
            book, options.only_filelist, options.exclude_filelist
        )
        if is_fixed_layout(book):
            print(
                "[bold yellow]warning: this is a fixed-layout (pre-paginated) "
                "EPUB — its text boxes are sized for the original words, so "
                "translated text may overflow or misplace.[/bold yellow]"
            )
        plan = build_plan(
            book,
            exclude_tags=tuple(
                t for t in options.exclude_translate_tags.split(",") if t
            ),
            poetry_group_size=options.poetry_group_size,
            only_files=set(f for f in options.only_filelist.split(",") if f) or None,
            exclude_files=set(f for f in options.exclude_filelist.split(",") if f)
            or None,
        )
        # samples are book text: rich would eat "[Seven] warriors [they were]"
        print(escape(plan.report()))
        plan_path = f"{os.path.splitext(options.book_name)[0]}_plan.json"
        if os.path.exists(plan_path):
            print(
                f"existing plan {plan_path} kept (may carry your edits); "
                f"delete it to regenerate"
            )
        else:
            plan.save_json(plan_path, book_path=options.book_name)
            print(
                f"plan written to {plan_path} — every row is a question: decide "
                f'it by setting "action", "decided_by" and "content_type"'
            )
            # classification needs credentials, which a dry run must not
            print(
                "note: nothing here is decided yet. A later --plan-classify "
                "model run rules on every row still null, an agent run hands "
                "them to a coding agent, and rows you decide yourself are "
                "left alone by both"
            )
        return

    PROXY = options.proxy
    if PROXY != "":
        os.environ["http_proxy"] = PROXY
        os.environ["https_proxy"] = PROXY

    model_names, api_format, endpoint_env_keys = resolve_endpoint(options)
    translate_model = FORMAT_DICT.get(api_format)
    assert translate_model is not None, f"unsupported api format: {api_format}"

    # What the endpoint alone decides, decided before a key is read, a book
    # is parsed or the codex sidecar is started: a run that cannot honor the
    # flags it was given must say so while nothing has been paid for.

    # Batch translation is OpenAI's Batch API. The codex format has no such
    # thing, and reached it anyway: `AttributeError: batch_init` partway into
    # a run that had already spent plan quota.
    if api_format == "codex" and (options.batch_flag or options.batch_use_flag):
        print(
            "[bold red]Error: --batch / --batch-use are the OpenAI Batch "
            "API, which the codex format does not have. Drop the flag, or "
            "translate through an OpenAI-shaped endpoint.[/bold red]"
        )
        exit(1)

    # A format that does not implement session mode used to accept the flag
    # and translate as though it had never been passed — the run cost more
    # attention than a window run and bought nothing.
    if options.context_mode == "session" and not getattr(
        translate_model, "SUPPORTS_SESSION_CONTEXT", False
    ):
        print(
            f"[bold red]Error: --use_context session is not implemented for "
            f"the {api_format} format; it would be accepted and ignored. Use "
            f"bare --use_context for a re-sent window of paragraph "
            f"pairs.[/bold red]"
        )
        exit(1)

    # One codex thread is the route's whole context. A second worker would
    # interleave chapters into it (measured: 225 document switches in 416
    # turns), and serializing the turns only hides that. Refused here,
    # before the sidecar is started for nothing.
    if options.parallel_workers > 1 and api_format == "codex":
        print(
            "[bold red]Error: --parallel-workers is not supported on the codex "
            "format: one thread is the context, and workers would interleave "
            "chapters into it. Drop --parallel-workers.[/bold red]"
        )
        exit(1)

    # Parallel workers each get a clone carrying their own chapter context.
    # A format that keeps no re-sendable window has nothing to clone, and the
    # run died reading a context attribute it never set — after the chapters
    # were already dispatched.
    if (
        options.parallel_workers > 1
        and options.context_mode is not None
        and not getattr(translate_model, "SUPPORTS_PARALLEL_CONTEXT", False)
    ):
        print(
            f"[bold red]Error: --parallel-workers is not supported with "
            f"--use_context on the {api_format} format, which keeps no "
            f"per-chapter context for a worker to carry. Drop "
            f"--parallel-workers, or drop --use_context for this "
            f"run.[/bold red]"
        )
        exit(1)

    # A codex run's context is the thread, and a thread does not survive the
    # process. The handoff report on disk is written, never read back.
    if api_format == "codex" and options.resume:
        print(
            "[bold yellow]Note:[/bold yellow] a resumed codex run starts a "
            "new thread. Nothing already translated is paid for again, but "
            "the earlier thread's terminology and register are not carried "
            "into it."
        )
    API_KEY = resolve_api_key(
        api_format,
        options.key,
        options.api_base,
        endpoint_env_keys + legacy.env_keys,
    )

    glossary = Glossary()
    if options.glossary:
        try:
            glossary = Glossary.from_file(options.glossary)
        except (OSError, ValueError) as err:
            raise SystemExit(f"Could not read --glossary: {err}")
        print(f"[green]Glossary: {len(glossary)} pinned terms loaded[/green]")

    # These need a context window to act on. Session mode is one; so is the
    # codex format, where the thread *is* the window and compaction is not
    # optional — so the warning must not fire there, or it tells the user a
    # flag was ignored when it was in fact obeyed.
    if options.context_mode != "session" and api_format != "codex":
        for flag, value in (
            ("--context-compact-at", options.context_compact_at),
            ("--glossary-auto", options.glossary_auto),
        ):
            if value:
                print(
                    f"[bold yellow]Warning:[/bold yellow] {flag} only applies "
                    f"to --use_context session; ignoring it."
                )

    book_type = get_book_type(options.book_name)
    support_type_list = list(BOOK_LOADER_DICT.keys())
    if book_type not in support_type_list:
        raise Exception(
            f"now only support files of these formats: {','.join(support_type_list)}",
        )

    book_loader = BOOK_LOADER_DICT.get(book_type)
    assert book_loader is not None, "unsupported loader"
    language = options.language
    if options.language in LANGUAGES:
        # use the value for prompt
        language = LANGUAGES.get(language, language)

    # None lets each SDK use its own official host.
    model_api_base = options.api_base

    loader_kwargs = {}
    if book_type == "pdf":
        loader_kwargs["pdf_layout"] = options.pdf_layout
    if book_type in CONTEXT_AWARE_BOOK_TYPES:
        loader_kwargs.update(
            context_mode=options.context_mode,
            context_compact_at=options.context_compact_at,
            glossary=glossary,
            glossary_auto=options.glossary_auto,
        )
    elif options.glossary or options.context_mode == "session":
        # txt, srt and pdf never hand context to the model, so a pin or a
        # session budget would quietly do nothing at all. Name only the flags
        # actually passed: naming one the user did not use is how an
        # unadvertised flag ends up advertised anyway.
        ignored = [
            flag
            for flag, given in (
                ("--glossary", options.glossary),
                ("--use_context session", options.context_mode == "session"),
            )
            if given
        ]
        print(
            f"[bold yellow]Warning:[/bold yellow] {' and '.join(ignored)} "
            f"{'is' if len(ignored) == 1 else 'are'} not supported for "
            f"{book_type} books; ignoring."
        )

    e = book_loader(
        options.book_name,
        translate_model,
        API_KEY,
        options.resume,
        language=language,
        model_api_base=model_api_base,
        is_test=options.test,
        test_num=options.test_num,
        prompt_config=parse_prompt_arg(options.prompt_arg),
        single_translate=options.single_translate,
        context_flag=options.context_flag,
        context_paragraph_limit=options.context_paragraph_limit,
        temperature=options.temperature,
        source_lang=options.source_lang,
        parallel_workers=options.parallel_workers,
        **loader_kwargs,
    )
    price_table = getattr(options, "price_table", None)
    if price_table is not None and hasattr(e.translate_model, "usage"):
        # the bar shows what was spent instead of token counts
        e.translate_model.usage.prices = price_table
    # Parse and set extra_body only on request paths that consume it. Setting
    # an arbitrary attribute on the other translators used to print success
    # and then silently drop the fields.
    if options.extra_body:
        if api_format != "openai":
            print(
                f"[bold yellow]Warning:[/bold yellow] --extra_body is ignored "
                f"by the {api_format} route"
            )
        else:
            try:
                extra_body = json.loads(options.extra_body)
                e.translate_model.extra_body = extra_body
                print(f"[bold blue]Extra body parameters:[/bold blue] {extra_body}")
            except json.JSONDecodeError as ex:
                print(f"[bold red]Error:[/bold red] Invalid JSON in --extra_body: {ex}")
                exit(1)
    # other options
    if options.sentence_mode:
        e.sentence_mode = True
    if options.allow_navigable_strings:
        e.allow_navigable_strings = True
    # --plan-classify-model names a classifier, which only makes sense in
    # model mode; asking for it alongside a no-classification mode is a
    # contradiction, not a preference to resolve silently.
    classify_mode = options.plan_classify
    # 'auto' cannot be settled yet: its answer comes from the endpoint's
    # capability probe, and the model this run uses is only known once the
    # model list has been validated. Held as tag mode until then.
    plan_auto = classify_mode == "auto"
    if plan_auto:
        classify_mode = "none"
    if options.plan_classify_model:
        # naming a classifier is naming the mode it belongs to
        plan_auto = False
        if classify_mode in ("all", "agent"):
            reason = (
                "agent mode makes no API call"
                if classify_mode == "agent"
                else "all mode skips classification"
            )
            print(
                f"[bold red]Error:[/bold red] --plan-classify-model cannot be "
                f"combined with --plan-classify {classify_mode} ({reason})"
            )
            exit(1)
        classify_mode = "model"
    # Plan mode is epub-only, and 'agent' in particular promises to stop
    # before spending anything; silently translating a txt/md book instead
    # would be the exact opposite of what was asked.
    if classify_mode != "none" and book_type != "epub":
        print(
            f"[bold red]Error:[/bold red] --plan-classify {classify_mode} "
            f"requires an epub book (plan mode is epub-only); got a "
            f"{book_type} book"
        )
        exit(1)

    if options.translate_tags:
        e.translate_tags = options.translate_tags
    # Any classification choice is a choice to have a plan, and the plan
    # partitions the whole book — a tag selection has nothing left to select.
    if classify_mode != "none":
        if options.translate_tags != "p":
            # "p" is argparse's default, so an untouched flag stays quiet;
            # a real selection being discarded deserves a line
            print(
                f"note: --plan-classify {classify_mode} plans the whole book; "
                f"ignoring --translate-tags {options.translate_tags}"
            )
        e.plan_mode = True
        e.translate_tags = "auto"
    # `--exclude-translate-tags ""` is the documented way to exclude nothing
    # (README). Testing for truthiness swallowed it and left the sup,code
    # default standing, with nothing printed to say so.
    if options.exclude_translate_tags is not None:
        e.exclude_translate_tags = options.exclude_translate_tags
    if hasattr(e, "plan_min_coverage"):
        e.plan_min_coverage = options.plan_min_coverage
        e.poetry_group_size = options.poetry_group_size
        # plan mode is triggered by translate_tags == "auto"; the classify
        # entry reaches the loader as chosen. "all" in particular must stay
        # distinguishable from "no plan": it is the deliberate
        # translate-everything decision, and the loader has to know it was
        # made rather than infer it from the absence of one.
        e.plan_classify = classify_mode
        e.plan_classify_model = options.plan_classify_model or None
    if options.quiet:
        if hasattr(e, "quiet"):
            e.quiet = True
        # The translator does its own echoing in session mode (the handoff
        # report). Guarded rather than set blindly, so a translator that does
        # not honor it is not given an attribute it will silently ignore.
        if hasattr(e.translate_model, "quiet"):
            e.translate_model.quiet = True
    if options.exclude_filelist:
        e.exclude_filelist = options.exclude_filelist
    if options.only_filelist:
        e.only_filelist = options.only_filelist
    # Both lists name documents inside the book, which is already open. A
    # typo is answerable here and nowhere cheaper — before any model setup,
    # so a sidecar boot or a context-window lookup cannot precede it.
    if hasattr(e, "check_file_filters"):
        e.check_file_filters()
    if options.accumulated_num > 1:
        e.accumulated_num = options.accumulated_num
    if options.translation_color:
        e.translation_style = f"color: {options.translation_color};"
    if options.translation_style:
        # --translation_style is the whole declaration block, so it replaces
        # the colour rather than merging with it. Losing a flag the user
        # typed is worth a line.
        if options.translation_color:
            print(
                f"[bold yellow]Warning:[/bold yellow] --translation_style "
                f"replaces --translation_color; the colour "
                f"{options.translation_color!r} is ignored. Put it in the "
                f"style instead."
            )
        e.translation_style = options.translation_style
    if options.batch_size:
        e.batch_size = options.batch_size
    if options.block_size > 0:
        e.block_size = options.block_size
    # Note: Default block_size is now 1 (delimiter-based translation) for better quality
    if options.retranslate:
        e.retranslate = options.retranslate
    if api_format in LLM_FORMATS:
        # No preset lists any more: the endpoint names its own models. Only
        # the formats in DEFAULT_MODELS have one obvious id to fall back on;
        # `codex` resolves its own from its config. Everything else must be
        # told, rather than have a whole book billed to a guess.
        if not model_names and api_format not in MODEL_OPTIONAL_FORMATS:
            raise SystemExit(
                f"--model is required for the {api_format} format. Pass the "
                f"model id the endpoint uses, e.g. --model claude-sonnet-4-6"
            )
        try:
            e.translate_model.set_model_list(model_names)
        except Exception as ex:
            print(f"[red]Error: {ex}[/red]")
            exit(1)
        # Everything that must be settled before the first paid request, now
        # that the model list is known: the codex sidecar is up and signed
        # in. A login prompt after ten minutes of work is the wrong time to
        # find out — and a failure the user can act on is one line, not a
        # traceback.
        if hasattr(e.translate_model, "preflight"):
            try:
                e.translate_model.preflight()
            except Exception as err:
                if not getattr(err, "user_facing", False):
                    raise
                print(f"[bold red]{escape(str(err))}[/bold red]")
                exit(1)
    elif model_names:
        # These formats translate through a fixed engine and take no model, so
        # honoring the flag is impossible; saying so beats ignoring it.
        print(
            f"[bold red]Error: the {api_format} format has no model to "
            f"choose, so --model is not supported by it.[/bold red]"
        )
        exit(1)
    if options.block_size > 0:
        e.block_size = options.block_size
    if options.batch_flag:
        e.batch_flag = options.batch_flag
    if options.batch_use_flag:
        e.batch_use_flag = options.batch_use_flag

    if plan_auto:
        # Last, because the probe needs the model this run settled on. The
        # verdict costs one sub-cent request and is cached, so the first
        # translation does not pay for it again.
        mode, reason = resolve_plan_mode(
            book_type,
            api_format,
            translate_tags_given,
            getattr(e.translate_model, "_probe_verdict", None),
        )
        if mode == "model":
            print(f"plan mode: on ({reason})")
            e.plan_mode = True
            e.plan_auto = True
            e.plan_fallback_tags = options.translate_tags
            e.translate_tags = "auto"
            e.plan_classify = "model"
        else:
            print(f"plan mode: off ({reason})")

    try:
        e.make_bilingual_book()
    except PlanLedgerError as err:
        # The plan JSON is the one file this workflow asks a person (or an
        # agent) to hand-edit, so its lint errors are the failure a user is
        # most likely to see — print them like every other plan failure,
        # not as a traceback.
        print(f"[bold red]{escape(str(err))}[/bold red]")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
