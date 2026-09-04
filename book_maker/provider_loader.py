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

# `api_style` -> `--api_format`: what the host on the other end speaks, and
# so which route calls it. Every `--api_format` that names an endpoint is a
# style, plus `claude` as the older spelling of `anthropic`; `codex` is not,
# having no address or key for an entry to carry. A host with no style of
# its own — any of the many that serve the OpenAI shape — is `"openai"`
# with its address in `base_url`.
API_STYLES = {
    "openai": "openai",
    "anthropic": "anthropic",
    "claude": "anthropic",
    "gemini": "gemini",
    "qwen": "qwen",
    "groq": "groq",
    "xai": "xai",
    "litellm": "litellm",
}

REQUIRED_FIELDS = {"api_style"}
OPTIONAL_FIELDS = {"base_url", "default_models", "env_key", "prices", "currency"}
ALL_VALID_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS

# `prices`: {model id: {"input", "output", optional "cached_input"}}, each a
# price per million tokens in `currency` (default USD). With a price for
# every model the run uses, the progress bar shows what was spent instead
# of token counts. `cached_input` left out means cache reads cost the
# input price — the conservative reading, never an optimistic one.
PRICE_REQUIRED = {"input", "output"}
PRICE_FIELDS = PRICE_REQUIRED | {"cached_input"}


@dataclass
class ProviderRoute:
    """The endpoint flags a provider entry stands for."""

    api_format: str
    api_base: str
    models: list
    env_key: str
    prices: dict = None
    currency: str = "USD"


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
    if api_style not in API_STYLES:
        # Almost always a vendor name for a host that speaks the OpenAI
        # shape, so the fix is worth printing rather than describing.
        raise ValueError(
            f"provider {name!r} has unsupported api_style {api_style!r}. "
            f"Supported: {', '.join(sorted(API_STYLES))}. A host that serves "
            f"the OpenAI shape at its own address is:\n"
            f'  "api_style": "openai",\n'
            f'  "base_url": "{provider.get("base_url") or "https://..."}"'
        )

    _validate_prices(name, provider)

    models = provider.get("default_models")
    if models is None:
        return
    if not isinstance(models, list) or not all(isinstance(m, str) for m in models):
        raise ValueError(f"provider {name!r}: default_models must be a list of strings")
    if not models:
        raise ValueError(f"provider {name!r}: default_models must not be empty")
    if not all(m.strip() for m in models):
        # the CLI strips a blank name away and then falls back to its own
        # default model, so the entry would silently not be honored
        raise ValueError(
            f"provider {name!r}: default_models must not contain a blank name"
        )


def _validate_prices(name, provider):
    currency = provider.get("currency")
    if currency is not None and (not isinstance(currency, str) or not currency.strip()):
        raise ValueError(f"provider {name!r}: currency must be a code such as USD")
    prices = provider.get("prices")
    if prices is None:
        return
    if not isinstance(prices, dict) or not prices:
        raise ValueError(
            f"provider {name!r}: prices must map model ids to "
            f'{{"input", "output", "cached_input"}} per million tokens'
        )
    for model, price in prices.items():
        if not isinstance(price, dict):
            raise ValueError(f"provider {name!r}: prices[{model!r}] must be an object")
        missing = PRICE_REQUIRED - set(price)
        if missing:
            raise ValueError(
                f"provider {name!r}: prices[{model!r}] is missing {sorted(missing)}"
            )
        unknown = set(price) - PRICE_FIELDS
        if unknown:
            raise ValueError(
                f"provider {name!r}: prices[{model!r}] has unknown fields "
                f"{sorted(unknown)}; known: {sorted(PRICE_FIELDS)}"
            )
        for field, value in price.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(
                    f"provider {name!r}: prices[{model!r}].{field} must be a "
                    f"number of {provider.get('currency') or 'USD'} per million tokens"
                )
            if value < 0:
                raise ValueError(
                    f"provider {name!r}: prices[{model!r}].{field} must not be negative"
                )


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
    return ProviderRoute(
        api_format=API_STYLES[provider["api_style"]],
        api_base=provider.get("base_url") or "",
        models=list(provider.get("default_models") or []),
        env_key=provider.get("env_key") or "",
        prices=provider.get("prices") or None,
        currency=(provider.get("currency") or "USD").strip(),
    )
