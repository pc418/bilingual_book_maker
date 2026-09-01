"""Named endpoints: `--provider NAME`, read from a JSON file.

A provider entry is shorthand for the endpoint flags — where the requests go
(`base_url`), what wire format the host speaks (`api_style`), which models to
run (`default_models`) and which variable holds the key (`env_key`). It saves
repeating `--api_base` and a key on every command; it decides nothing the
command itself said, because anything passed explicitly wins.

Two files are read: `bbm_providers.json` in the current directory and
`~/.bbm/providers.json`. A project entry overrides a global one of the same
name, so a repo can pin the endpoint its scripts expect.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

GLOBAL_CONFIG_PATH = Path.home() / ".bbm" / "providers.json"
LOCAL_CONFIG_FILENAME = "bbm_providers.json"

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/openai/"
DASHSCOPE_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# `api_style` -> (`--api_format`, the base URL that style implies). Gemini and
# Qwen had their own translator classes once; both serve an OpenAI-compatible
# endpoint, so they are the openai format at a fixed address. A `base_url` in
# the entry overrides that address.
API_STYLE_ROUTES = {
    "openai": ("openai", None),
    "claude": ("anthropic", None),
    "gemini": ("openai", GEMINI_BASE),
    "qwen": ("openai", DASHSCOPE_BASE),
}

REQUIRED_FIELDS = {"api_style"}
OPTIONAL_FIELDS = {"base_url", "default_models", "env_key"}
ALL_VALID_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS


@dataclass
class ProviderRoute:
    """The endpoint flags a provider entry stands for."""

    api_format: str
    api_base: str
    models: list
    env_key: str


def _load_json_file(path):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        # Falling through to the other file would silently run the book
        # against a different endpoint than this one configures.
        raise ValueError(f"{path} could not be read: {e}")


def _merge_configs(global_config, local_config):
    """Both files' providers, the project file winning on a shared name."""
    merged = dict((global_config or {}).get("providers", {}))
    merged.update((local_config or {}).get("providers", {}))
    return {"providers": merged}


def load_provider_config():
    global_cfg = _load_json_file(GLOBAL_CONFIG_PATH)
    local_cfg = _load_json_file(os.path.join(os.getcwd(), LOCAL_CONFIG_FILENAME))
    return _merge_configs(global_cfg, local_cfg)


def validate_provider(name, provider):
    """Refuse an entry that cannot be honored, naming what is wrong with it."""
    if not isinstance(provider, dict):
        raise ValueError(f"provider {name!r} must be a JSON object")

    missing = REQUIRED_FIELDS - set(provider)
    if missing:
        raise ValueError(
            f"provider {name!r} is missing required fields: {sorted(missing)}"
        )

    unknown = set(provider) - ALL_VALID_FIELDS
    if unknown:
        raise ValueError(f"provider {name!r} has unknown fields: {sorted(unknown)}")

    api_style = provider["api_style"]
    if api_style not in API_STYLE_ROUTES:
        raise ValueError(
            f"provider {name!r} has unsupported api_style {api_style!r}. "
            f"Supported: {sorted(API_STYLE_ROUTES)}"
        )

    models = provider.get("default_models")
    if models is None:
        return
    if not isinstance(models, list) or not all(isinstance(m, str) for m in models):
        raise ValueError(f"provider {name!r}: default_models must be a list of strings")
    if not models:
        raise ValueError(f"provider {name!r}: default_models must not be empty")


def get_provider(name):
    """The validated entry for `name`, or a failure naming where it was sought."""
    providers = load_provider_config()["providers"]
    if name not in providers:
        known = ", ".join(sorted(providers)) or "none"
        raise ValueError(
            f"--provider {name} has no entry in {LOCAL_CONFIG_FILENAME} (this "
            f"directory) or {GLOBAL_CONFIG_PATH}. Providers defined there: "
            f"{known}."
        )
    provider = providers[name]
    validate_provider(name, provider)
    return provider


def resolve_provider(name):
    """`name` as endpoint settings: format, base, models to rotate, key variable."""
    provider = get_provider(name)
    api_format, style_base = API_STYLE_ROUTES[provider["api_style"]]
    return ProviderRoute(
        api_format=api_format,
        api_base=provider.get("base_url") or style_base or "",
        models=list(provider.get("default_models") or []),
        env_key=provider.get("env_key") or "",
    )
