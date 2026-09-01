"""OrcaRouter: a gateway, so a route rather than a translator class.

The endpoint surface already covers everything about the request — OrcaRouter
speaks the OpenAI shape, so `--api_base https://api.orcarouter.ai/v1 --model
orcarouter/auto` works with no code at all. What is left worth keeping is the
shortcut the docs advertise: `--model orcarouter` and `--model
orcarouter/<id>`, neither of which should require typing the address.

This is not a legacy alias, so it does not go through `legacy_cli.py` and
prints no deprecation notice.
"""

API_BASE = "https://api.orcarouter.ai/v1"
# The gateway's smart-routing model: it picks a model per request rather than
# pinning one, which is what the bare `--model orcarouter` asks for.
DEFAULT_MODEL = "orcarouter/auto"
ENV_KEY = "BBM_ORCAROUTER_API_KEY"

_PREFIX = "orcarouter/"


def resolve(model, api_base=None):
    """`(model, api_base)` for an OrcaRouter model id, else None.

    An `--api_base` the command passed wins: naming the gateway's model is a
    shorthand for its address, not an override of one the user chose.
    """
    name = (model or "").strip()
    lowered = name.lower()
    if lowered == "orcarouter":
        name = DEFAULT_MODEL
    elif not lowered.startswith(_PREFIX):
        return None
    return name, api_base or API_BASE
