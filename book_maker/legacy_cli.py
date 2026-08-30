"""Rewrite old command lines into the endpoint surface.

This fork selects a translator by endpoint (`--api_base`, `--key`,
`--api_format`, `--model_list`) rather than by model name. Every flag that
change removed is still accepted here and rewritten before argparse runs, so
commands, scripts and CI written against the old CLI keep working.

Two rules keep the layer honest:

* **Say what happened.** Each rewrite prints the modern equivalent, so a user
  who reads the output once can update their command and stop depending on
  this module.
* **Never guess.** An alias with no faithful translation exits with an
  error naming it. Quietly mapping an unknown model name onto some default
  would bill a whole book to a model nobody chose.

Model ids come from the *old* preset lists, not from whatever is newest: the
job is to preserve what the command used to do. Some of those models are
retired by now, and the endpoint's own model check reports that clearly.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# Legacy `--model` value -> what it becomes. `fmt` is an --api_format,
# `base` an --api_base, `model` the head of that alias's old preset list.
_OPENAI_PRESETS = {
    "chatgptapi": "gpt-3.5-turbo",
    "gpt4": "gpt-4",
    "gpt4omini": "gpt-4o-mini",
    "gpt4o": "gpt-4o",
    "gpt5mini": "gpt-5-mini",
    "o1preview": "o1-preview",
    "o1": "o1",
    "o1mini": "o1-mini",
    "o3mini": "o3-mini",
}

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"
_DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# Vendors whose native wrapper was removed because they serve an
# OpenAI-compatible endpoint. Reaching them is now a base URL.
_VENDOR_ROUTES = {
    "gemini": (_GEMINI_BASE, "gemini-flash-latest", "BBM_GOOGLE_GEMINI_KEY"),
    "geminipro": (_GEMINI_BASE, "gemini-pro-latest", "BBM_GOOGLE_GEMINI_KEY"),
    "groq": ("https://api.groq.com/openai/v1", None, "BBM_GROQ_API_KEY"),
    "xai": ("https://api.x.ai/v1", "grok-beta", "BBM_XAI_API_KEY"),
    # Upstream's named route (#557). `orcarouter/auto` is a smart-routing
    # alias, not a model id — the endpoint picks per request — so it is the
    # default here exactly as it was there; any other id it serves works
    # through --api_base with --model.
    "orcarouter": (
        "https://api.orcarouter.ai/v1",
        "orcarouter/auto",
        "BBM_ORCAROUTER_API_KEY",
    ),
    "qwen": (_DASHSCOPE_BASE, "qwen-mt-turbo", "BBM_QWEN_API_KEY"),
    "qwen-mt-turbo": (_DASHSCOPE_BASE, "qwen-mt-turbo", "BBM_QWEN_API_KEY"),
    "qwen-mt-plus": (_DASHSCOPE_BASE, "qwen-mt-plus", "BBM_QWEN_API_KEY"),
}

# Aliases that were always a fixed engine rather than a model.
_MT_FORMATS = {
    "google": "google",
    "caiyun": "caiyun",
    "deepl": "deepl",
    "deeplfree": "deeplfree",
    "tencentransmart": "tencent",
    "customapi": "customapi",
}

# Old per-vendor key flags. All of them are just a key now, but which one to
# take matters: a command carrying two keys must send each vendor its own.
_KEY_FLAGS = (
    "--openai_key",
    "--claude_key",
    "--gemini_key",
    "--groq_key",
    "--xai_key",
    "--orcarouter_key",
    "--qwen_key",
    "--caiyun_key",
    "--deepl_key",
    "--api_key",
)

# `--model` alias -> the key flag that alias used to read.
_ALIAS_KEY_FLAG = {
    "gemini": "--gemini_key",
    "geminipro": "--gemini_key",
    "groq": "--groq_key",
    "xai": "--xai_key",
    "orcarouter": "--orcarouter_key",
    "qwen": "--qwen_key",
    "qwen-mt-turbo": "--qwen_key",
    "qwen-mt-plus": "--qwen_key",
    "caiyun": "--caiyun_key",
    "deepl": "--deepl_key",
}

# Where each provider file's api_style lands on the new surface.
_API_STYLE_ROUTES = {
    "openai": (None, None),
    "claude": ("anthropic", None),
    "gemini": (None, _GEMINI_BASE),
    "qwen": (None, _DASHSCOPE_BASE),
}

_PROVIDER_FILES = (Path("bbm_providers.json"), Path.home() / ".bbm" / "providers.json")


@dataclass
class LegacyTranslation:
    """A rewritten command line, plus what to tell the user about it."""

    argv: list
    notices: list = field(default_factory=list)
    # Key variables the old route used, consulted after the modern ones so an
    # existing BBM_GROQ_API_KEY still authenticates a translated groq command.
    env_keys: tuple = ()


def _fail(message):
    raise SystemExit(message)


def _load_provider(name):
    """The provider entry `name`, from the same files the old CLI read."""
    for path in _PROVIDER_FILES:
        if not path.exists():
            continue
        try:
            config = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            # Falling through to the global file would silently run the book
            # against a different endpoint than this directory configures.
            _fail(f"{path} could not be read: {e}")
        provider = config.get("providers", {}).get(name)
        if provider:
            return provider
    _fail(
        f"--provider {name} was removed, and no entry for it was found in "
        f"bbm_providers.json or ~/.bbm/providers.json. Pass the endpoint "
        f"directly instead: --api_base <url> --key <key> --model_list <model>."
    )


def _value_of(argv, flag):
    """Value of `flag` in argv, in either the spaced or `=` form."""
    for i, arg in enumerate(argv):
        if arg == flag and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith(f"{flag}="):
            return arg.split("=", 1)[1]
    return None


def _drop_flag(argv, flag):
    out = []
    i = 0
    while i < len(argv):
        if argv[i] == flag:
            i += 2
            continue
        if argv[i].startswith(f"{flag}="):
            i += 1
            continue
        out.append(argv[i])
        i += 1
    return out


def _split(argv):
    """Pull the removed flags out of argv, leaving everything else in order.

    Returns (remaining argv, {flag: value}). A flag with no value is left in
    place so argparse produces its own error rather than this module
    inventing one.
    """
    taken = {}
    rest = []
    i = 0
    aliases = {"-m": "--model"}
    known = set(_KEY_FLAGS) | {
        "--model",
        "-m",
        "--ollama_model",
        "--custom_api",
        "--deployment_id",
        "--provider",
        "--interval",
    }
    while i < len(argv):
        arg = argv[i]
        name, _, inline = arg.partition("=")
        if name not in known:
            rest.append(arg)
            i += 1
            continue
        if inline:
            value = inline
        elif i + 1 < len(argv):
            value = argv[i + 1]
            i += 1
        else:
            _fail(
                f"{name} needs a value. It is also a flag this fork replaced; "
                f'see "Migrating from the old flags" in the README.'
            )
        taken[aliases.get(name, name)] = value
        i += 1
    return rest, taken


def translate_legacy_argv(argv):
    """Rewrite `argv`, returning the modern command line and what changed."""
    rest, legacy = _split(list(argv))
    if not legacy:
        return LegacyTranslation(argv=list(argv))

    notices = []
    env_keys = []
    # What the user already said in modern flags always wins.
    has_base = any(a == "--api_base" or a.startswith("--api_base=") for a in rest)
    has_models = any(a == "--model_list" or a.startswith("--model_list=") for a in rest)
    has_format = any(a == "--api_format" or a.startswith("--api_format=") for a in rest)
    api_format = None
    api_base = None
    model = None
    # A --model value that is not an alias: returned untouched, never
    # weighed against the user's other flags here.
    passthrough_model = None
    # What the user wrote that caused a route rewrite, named in the notice.
    route_source = None

    # Which key flag to honor depends on the route, so pick it after the
    # alias is known. Preferring position would hand vendor A's key to B.
    alias = legacy.get("--model", "")
    preferred = _ALIAS_KEY_FLAG.get(alias)
    if alias.startswith("claude"):
        preferred = "--claude_key"
    key_flag = next((f for f in (preferred,) + _KEY_FLAGS if f and f in legacy), None)
    if key_flag:
        rest = ["--key", legacy[key_flag]] + rest
        notices.append(f"{key_flag} is now --key")

    if "--interval" in legacy:
        notices.append(
            "--interval was dropped; it only ever applied to the removed "
            "gemini route and has no effect now"
        )

    if "--provider" in legacy:
        name = legacy["--provider"]
        provider = _load_provider(name)
        style = provider.get("api_style", "openai")
        if style not in _API_STYLE_ROUTES:
            _fail(f"--provider {name} uses unknown api_style {style!r}")
        api_format, style_base = _API_STYLE_ROUTES[style]
        api_base = provider.get("base_url") or style_base
        defaults = provider.get("default_models") or []
        # The old path handed the whole list to set_model_list, which rotates.
        model = ",".join(defaults) if defaults else None
        if provider.get("env_key"):
            env_keys.append(provider["env_key"])
        route_source = f"--provider {name}"

    if "--model" in legacy:
        alias = legacy["--model"]
        if alias in _OPENAI_PRESETS:
            model = _OPENAI_PRESETS[alias]
            route_source = f"--model {alias}"
        elif alias == "openai":
            notices.append("--model openai is now the default; just --model_list")
            route_source = None
        elif alias == "claude":
            # the bare alias was a stand-in for a default model
            model = "claude-haiku-4-5-20251001"
            route_source = "--model claude"
        elif alias in _VENDOR_ROUTES:
            api_base, model, env_key = _VENDOR_ROUTES[alias]
            env_keys.append(env_key)
            route_source = f"--model {alias}"
        elif alias in _MT_FORMATS:
            api_format = _MT_FORMATS[alias]
            route_source = f"--model {alias}"
            if alias == "customapi":
                # the endpoint used to arrive as --custom_api or its variable
                api_base = api_base or os.environ.get("BBM_CUSTOM_API", "")
        else:
            # Not an old alias: --model names an actual model id, which is
            # the normal case now. Hand it straight back so the parser sees
            # the flag the user typed — including any conflict with
            # --model_list, which is not this module's to resolve.
            passthrough_model = alias

    if "--ollama_model" in legacy:
        api_base = api_base or "http://localhost:11434/v1"
        model = legacy["--ollama_model"]
        route_source = "--ollama_model"
        if not key_flag:
            # ollama authenticates nobody, on localhost or a LAN box alike;
            # the old CLI passed this placeholder for every ollama route.
            rest = ["--key", "ollama"] + rest

    if "--custom_api" in legacy:
        api_format = "customapi"
        api_base = legacy["--custom_api"]
        route_source = "--custom_api"

    if "--deployment_id" in legacy:
        model = legacy["--deployment_id"]
        route_source = "--deployment_id"
        # A bare Azure resource root serves nothing at /chat/completions;
        # the OpenAI shape lives under /openai/v1. Rewrite the base the user
        # gave rather than leaving a command that 404s.
        given = _value_of(rest, "--api_base")
        if given and "azure.com" in given and "/openai/v1" not in given:
            api_base = given.rstrip("/") + "/openai/v1"
            rest = _drop_flag(rest, "--api_base")
            has_base = False
            notices.append(
                "Azure now goes through its OpenAI-compatible endpoint; "
                f"--api_base became {api_base}"
            )

    # The old parser defaulted to --model chatgptapi, so a command with only
    # a key named no model at all. Without this it now dies on
    # "--model_list is required" -- a working command turned into an error.
    # A format the user named outright decides whether a model applies at
    # all: the fixed engines have none, so supplying one turns a request to
    # drop a deprecated flag into an error.
    named_format = _value_of(rest, "--api_format")
    if (
        model is None
        and passthrough_model is None
        and not has_models
        and api_format in (None, "openai")
        and named_format in (None, "openai")
        and "--model" not in legacy
    ):
        model = _OPENAI_PRESETS["chatgptapi"]
        notices.append(f"no --model was given; the old default is --model_list {model}")

    if passthrough_model is not None:
        rest = ["--model", passthrough_model] + rest

    prefix = []
    superseded = []
    if api_format:
        prefix += ["--api_format", api_format] if not has_format else []
        superseded += ["--api_format"] if has_format else []
    if api_base:
        prefix += ["--api_base", api_base] if not has_base else []
        superseded += ["--api_base"] if has_base else []
    if model:
        # --model_list exists for rotation across several models; a single
        # model belongs in --model, which is what the user typed.
        flag = "--model_list" if "," in model else "--model"
        prefix += [flag, model] if not has_models else []
        superseded += ["--model_list"] if has_models else []

    # Describe what was actually applied. Announcing a default that the
    # user's own flag overrode would report a run that did not happen.
    if route_source:
        if prefix:
            note = f"{route_source} is now {' '.join(prefix)}"
            if superseded:
                note += f" (your own {', '.join(superseded)} kept)"
        else:
            note = (
                f"{route_source} is fully covered by the "
                f"{', '.join(superseded)} you passed"
            )
        notices.append(note)

    return LegacyTranslation(
        argv=prefix + rest,
        notices=notices,
        env_keys=tuple(dict.fromkeys(k for k in env_keys if k)),
    )


def legacy_env_key(env_keys):
    """First value set among `env_keys`, for the legacy key fallback."""
    return next((os.environ[n] for n in env_keys if os.environ.get(n)), "")
