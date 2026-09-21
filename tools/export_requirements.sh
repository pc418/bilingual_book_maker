#!/usr/bin/env bash
# Regenerate the two pinned requirement files from pdm.lock, the same way
# every time. Run from the repo root after editing pyproject.toml.
#
#   requirements.txt      the base install (default group), PDF route included
#   requirements-ocr.txt  the `ocr` extra alone: the PDF parser's OCR backend
#
# The extra is exported without the default group so it is installed after
# requirements.txt, each file self-contained for pip's hash-checking mode. `--update-reuse` keeps every pin the lock already has, so a change
# to one group does not move the others.
set -euo pipefail
cd "$(dirname "$0")/.."
pdm lock --update-reuse -G :all
pdm export --prod -o requirements.txt
pdm export --prod --no-default -G ocr -o requirements-ocr.txt
