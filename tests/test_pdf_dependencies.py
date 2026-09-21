"""The PDF route's dependency split, pinned.

PIN (owner, 2026-09-21, docs/260921-fix-PDF_DEPS_OCR_EXTRA.md): "all in pdf
should be passed explicitly and don't influence existing users." Nothing
of the PDF route is a base dependency: the engine's wrapper, pdfium and
Pillow are the `pdf` extra (`requirements-pdf.txt`), and the OCR runtime
(docling, torch, the CUDA wheels) is the `ocr` extra
(`requirements-ocr.txt`), so a user who never passes --to-epub installs
none of it and one who never passes --with-ocr never downloads torch.
OpenDataLoader itself already splits the two (its plain package declares no
Python dependencies; `[hybrid]` is the OCR stack); this pins that our
packaging keeps the split instead of asking for the extra in the base list,
which is what the first cut did.
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


def test_the_base_install_carries_nothing_of_the_pdf_route():
    deps = _project()["dependencies"]
    names = {_name(d) for d in deps}
    assert not names & set(ROUTE), "the route is the pdf extra, not the base"
    assert not names & set(OCR)
    assert not [d for d in deps if "[" in d], "no extras in the base list"


def test_the_pdf_and_ocr_extras_are_exactly_the_route_and_its_backend():
    extras = _project()["optional-dependencies"]
    assert extras["pdf"] == ["opendataloader-pdf>=2.5.10", "pypdfium2>=5", "pillow>=10"]
    assert extras["ocr"] == [
        "opendataloader-pdf[hybrid]>=2.5.10",
        "pypdfium2>=5",
        "pillow>=10",
    ]


def test_the_exported_requirement_files_keep_the_split():
    base = (ROOT / "requirements.txt").read_text()
    pdf = (ROOT / "requirements-pdf.txt").read_text()
    ocr = (ROOT / "requirements-ocr.txt").read_text()
    for name in ROUTE:
        assert not _pinned(base, name), f"{name} in the base file"
        assert _pinned(pdf, name), f"{name} missing from the pdf file"
        assert _pinned(ocr, name), f"{name} missing from the ocr file"
    for name in OCR:
        assert not _pinned(base, name), f"{name} in the base file"
        assert not _pinned(pdf, name), f"{name} in the pdf file"
        assert _pinned(ocr, name), f"{name} missing from the ocr file"
