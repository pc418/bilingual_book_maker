"""The PDF route's dependency split, pinned.

PIN (owner, 2026-09-21, docs/260921-fix-PDF_DEPS_OCR_EXTRA.md): the PDF
route's own packages (the engine's wrapper, pdfium, Pillow; about 31 MB)
are base dependencies ("put it in requirements.txt"), and the OCR runtime
(docling, torch, the CUDA wheels; several gigabytes) is the `ocr` extra and
`requirements-ocr.txt`, so a user who never passes --with-ocr never
downloads torch. OpenDataLoader itself already splits the two (its plain
package declares no Python dependencies; `[hybrid]` is the OCR stack); this
pins that our packaging keeps the split instead of asking for the extra in
the base list, which is what the first cut did.
"""

import re
from pathlib import Path

import pytest

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = pytest.importorskip("tomli")

ROOT = Path(__file__).resolve().parents[1]
ROUTE = ("opendataloader-pdf", "pypdfium2", "pillow")
OCR = ("torch", "docling", "transformers", "easyocr")


def _project():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]


def _name(requirement):
    return re.split(r"[\[><=!~;\s]", requirement, maxsplit=1)[0].lower()


def _pinned(text, name):
    # PDM exports an extra as `docling[easyocr]==…`; the name is what matters.
    return re.search(rf"^{re.escape(name)}(\[[^\]]*\])?==", text, re.M)


def test_the_base_install_has_the_route_and_none_of_the_ocr_runtime():
    deps = _project()["dependencies"]
    names = {_name(d) for d in deps}
    assert set(ROUTE) <= names
    assert not names & set(OCR)
    assert not [d for d in deps if "[" in d], "no extras in the base list"


def test_the_ocr_extra_is_the_hybrid_stack_and_nothing_else():
    extras = _project()["optional-dependencies"]
    assert extras["ocr"] == ["opendataloader-pdf[hybrid]>=2.5.10"]


def test_the_exported_requirement_files_keep_the_split():
    base = (ROOT / "requirements.txt").read_text()
    ocr = (ROOT / "requirements-ocr.txt").read_text()
    for name in ROUTE:
        assert _pinned(base, name), f"{name} missing from the base file"
    for name in OCR:
        assert not _pinned(base, name), f"{name} in the base file"
        assert _pinned(ocr, name), f"{name} missing from the ocr file"
