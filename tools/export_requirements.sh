#!/usr/bin/env bash
# Regenerate the three pinned requirement files from pdm.lock, the same way
# every time. Run from the repo root after editing pyproject.toml.
#
#   requirements.txt      the base install (default group)
#   requirements-pdf.txt  the `pdf` extra alone: the PDF route's packages
#   requirements-ocr.txt  the `ocr` extra alone: the route plus its OCR backend
#
# The extras are exported without the default group so they are installed
# after requirements.txt, each file self-contained for pip's hash-checking
# mode. `--update-reuse` keeps every pin the lock already has, so a change
# to one group does not move the others.
set -euo pipefail
cd "$(dirname "$0")/.."
pdm lock --update-reuse -G :all
pdm export --prod -o requirements.txt
pdm export --prod --no-default -G pdf -o requirements-pdf.txt
pdm export --prod --no-default -G ocr -o requirements-ocr.txt
