"""The translation fingerprint ignores the sidecar endpoint fields.

PIN (Codex, packet H review 260924, record
docs/260924-fix-HELP_COMPAT_README_DRIFT_AND_CLASSIFY_MIN_CONFIDENCE.md):
`option_identity` walks the whole parsed namespace, so a new parser field
re-fingerprints every finished PDF bundle -- a completed one would be
translated and paid for again, an interrupted one would refuse to resume
with SETTINGS_CHANGED. The image and classify fields shape the extraction
(hashed through `source`) or nothing at all on the Markdown run, so they are
left out, and a bundle written before they existed still matches.
"""

import argparse

from book_maker.pipeline.translate import (
    SIDECAR_FIELDS,
    option_identity,
    parse_bbm_options,
)

COMMAND = ["--model", "gpt-5.6-luna", "--language", "zh-hans", "--test"]


def _names(identity):
    return {name for name, _value in identity}


def test_the_sidecar_fields_are_not_in_the_identity():
    options = parse_bbm_options(
        COMMAND
        + [
            "--img-model",
            "gpt-5.6-luna",
            "--classify-model",
            "jev",
            "--classify-min-confidence",
            "0.7",
        ]
    )
    names = _names(option_identity(options))
    assert names.isdisjoint(SIDECAR_FIELDS)
    assert {"model", "language", "test"} <= names


def test_a_bundle_from_before_the_sidecar_flags_still_matches():
    # the namespace a pre-flag parser produced: the same command, none of
    # the sidecar fields present at all
    now = parse_bbm_options(COMMAND)
    before = argparse.Namespace(
        **{k: v for k, v in vars(now).items() if k not in SIDECAR_FIELDS}
    )
    assert option_identity(before) == option_identity(now)


def test_typing_a_sidecar_flag_does_not_change_the_identity():
    plain = parse_bbm_options(COMMAND)
    typed = parse_bbm_options(COMMAND + ["--classify-min-confidence", "0.7"])
    assert option_identity(plain) == option_identity(typed)
