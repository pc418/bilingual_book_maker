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

# Old per-vendor key flags. All of them are just a key now.
_KEY_FLAGS = (
    "--openai_key",
    "--claude_key",
    "--gemini_key",
    "--groq_key",
    "--xai_key",
    "--qwen_key",
    "--caiyun_key",
    "--deepl_key",
    "--api_key",
)

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
        try:
            config = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        provider = config.get("providers", {}).get(name)
        if provider:
            return provider
    _fail(
        f"--provider {name} was removed, and no entry for it was found in "
        f"bbm_providers.json or ~/.bbm/providers.json. Pass the endpoint "
        f"directly instead: --api_base <url> --key <key> --model_list <model>."
    )


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
                f"see \"Migrating from the old flags\" in the README."
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
    # What the user wrote that caused a route rewrite, named in the notice.
    route_source = None

    for flag in _KEY_FLAGS:
        if flag in legacy:
            rest = ["--key", legacy[flag]] + rest
            notices.append(f"{flag} is now --key")
            break

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
        model = defaults[0] if defaults else None
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
        elif alias.startswith("claude"):
            api_format = "anthropic"
            model = "claude-haiku-4-5-20251001" if alias == "claude" else alias
            route_source = f"--model {alias}"
        elif alias in _VENDOR_ROUTES:
            api_base, model, env_key = _VENDOR_ROUTES[alias]
            env_keys.append(env_key)
            route_source = f"--model {alias}"
        elif alias in _MT_FORMATS:
            api_format = _MT_FORMATS[alias]
            route_source = f"--model {alias}"
        else:
            _fail(
                f"--model {alias} has no equivalent in this fork, and guessing "
                f"one could bill a book to a model you did not choose. Name the "
                f"endpoint instead: --api_base <url> --model_list <model id>."
            )

    if "--ollama_model" in legacy:
        api_base = api_base or "http://localhost:11434/v1"
        model = legacy["--ollama_model"]
        route_source = "--ollama_model"

    if "--custom_api" in legacy:
        api_format = "customapi"
        api_base = legacy["--custom_api"]
        route_source = "--custom_api"

    if "--deployment_id" in legacy:
        model = legacy["--deployment_id"]
        route_source = "--deployment_id"

    prefix = []
    superseded = []
    if api_format:
        prefix += ["--api_format", api_format] if not has_format else []
        superseded += ["--api_format"] if has_format else []
    if api_base:
        prefix += ["--api_base", api_base] if not has_base else []
        superseded += ["--api_base"] if has_base else []
    if model:
        prefix += ["--model_list", model] if not has_models else []
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
