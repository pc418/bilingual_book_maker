"""`--structure-model`: how the region-role pass is reached, and when not.

PIN (lead 260923, packet E2): the pass is opt-in. Without the flag nothing
of it is imported, built or probed; with it, the model runs on the
translation's own OpenAI-shaped endpoint and key (the
`--plan-classify-model` pattern), an endpoint of another format is refused
before a page is read, an endpoint that cannot see a page image leaves the
detector's labels and says so, and the model plus the prompt/policy
revision are part of the extraction identity.

The model is a fake translator exposing the two names the pass uses
(`structured_json_with_image`, `vision_verdict` / `capabilities.
ensure_vision`); the conversion is docling's `_converter` stubbed with a
real `DoclingDocument`, so the decisions, the apply and the export are
real.
"""

import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from pipeline_helpers import pandoc_or_skip, write_pdf  # noqa: E402

from book_maker import cli  # noqa: E402
from book_maker.pipeline import docling_parser, stages, to_epub  # noqa: E402
from book_maker.pipeline.bundle import Bundle  # noqa: E402
from book_maker.pipeline.errors import PipelineError  # noqa: E402
from book_maker.pipeline.messages import (  # noqa: E402
    DEVICE_SELECTED,
    STRUCTURE_ROUTE_UNSUPPORTED,
    STRUCTURE_VISION_UNVERIFIED,
)
from book_maker.pipeline.pdf_settings import ExtractionSettings  # noqa: E402

REV = "260923a/260923a"


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------
def test_the_structure_model_and_revision_are_identity():
    plain = ExtractionSettings()
    luna = ExtractionSettings(structure="gpt-5.6-luna", structure_rev=REV)
    assert plain.identity()["structure"] is None
    assert luna.identity()["structure"] == "gpt-5.6-luna"
    assert luna.identity()["structure_rev"] == REV
    assert luna.identity() != plain.identity()
    other_model = ExtractionSettings(structure="gpt-5.6-sol", structure_rev=REV)
    other_rev = ExtractionSettings(structure="gpt-5.6-luna", structure_rev="x/y")
    assert len({str(s.identity()) for s in (luna, other_model, other_rev)}) == 3
    # a revision without a model changes nothing: no pass ran
    assert ExtractionSettings(structure_rev=REV).identity() == plain.identity()


def test_from_manifest_reads_the_structure_back():
    luna = ExtractionSettings(structure="gpt-5.6-luna", structure_rev=REV)
    manifest = {"structure": "gpt-5.6-luna", "structure_rev": REV}
    assert ExtractionSettings.from_manifest(manifest).identity() == luna.identity()
    # a manifest from before the flag is a run without it
    assert ExtractionSettings.from_manifest({}).identity() == (
        ExtractionSettings().identity()
    )


# --------------------------------------------------------------------------
# A fake translator and a real document
# --------------------------------------------------------------------------
class FakeLedger:
    def __init__(self, verdict):
        self.verdict = verdict
        self.asked = []

    def ensure_vision(self, model, probe=None):
        self.asked.append(model)
        return self.verdict


class FakeUsage:
    prompt = completion = 0


class FakeVisionTranslator:
    """The two names the pass needs, and nothing else."""

    def __init__(self, answers, verdict="verified"):
        self.answers = answers
        self.capabilities = FakeLedger(verdict)
        self.usage = FakeUsage()
        self.calls = []

    def structured_json_with_image(self, prompt, schema, image_png, model=None):
        from book_maker.pipeline import decisions

        self.calls.append(model)
        self.usage.prompt += 1000
        self.usage.completion += 20
        regions = json.loads(prompt[len(decisions.PROMPT) :])
        return {
            str(r["id"]): self.answers[r["text_head"]]
            for r in regions
            if r["text_head"] in self.answers
        }


def real_document():
    docling = pytest.importorskip("docling_core.types.doc")
    d = docling
    document = d.DoclingDocument(name="wired")
    document.add_page(page_no=1, size=d.Size(width=612.0, height=792.0))

    def prov(top):
        return d.ProvenanceItem(
            page_no=1,
            bbox=d.BoundingBox(
                l=72, t=top, r=400, b=top - 14, coord_origin=d.CoordOrigin.BOTTOMLEFT
            ),
            charspan=(0, 1),
        )

    document.add_text(label=d.DocItemLabel.TEXT, text="1 Introduction", prov=prov(740))
    document.add_text(label=d.DocItemLabel.TEXT, text="Prose one.", prov=prov(720))
    document.add_text(label=d.DocItemLabel.FOOTNOTE, text="import os", prov=prov(700))
    document.add_text(label=d.DocItemLabel.TEXT, text="Prose two.", prov=prov(680))
    document.add_text(label=d.DocItemLabel.TEXT, text="Prose three.", prov=prov(660))
    return document


ANSWERS = {
    "1 Introduction": "section_header",
    "import os": "code",
    "Prose one.": "text",
    "Prose two.": "text",
    "Prose three.": "text",
}


@pytest.fixture
def real_pdf(tmp_path):
    pytest.importorskip("pypdfium2")
    pytest.importorskip("PIL")
    return write_pdf(tmp_path / "paper.pdf", ["A line of prose."])


@pytest.fixture
def stub_docling(monkeypatch):
    """docling's converter replaced by one returning a real document."""
    state = {}

    def converter(device, settings):
        state["document"] = real_document()

        class Converter:
            def convert(self, source, page_range=None):
                return types.SimpleNamespace(document=state["document"])

        return Converter()

    monkeypatch.setattr(docling_parser, "_converter", converter)
    # the heading glyph reader needs nothing from this fixture's page
    monkeypatch.setattr(
        docling_parser.pdf_headings,
        "styles",
        lambda pdf_path, boxes: {index: None for index in boxes},
    )
    return state


def request(translator, model="gpt-5.6-luna"):
    return docling_parser._structure_ask(
        types.SimpleNamespace(api_format="openai"), model, translator=translator
    )


def test_convert_writes_the_overlay_and_exports_the_new_roles(
    tmp_path, real_pdf, stub_docling
):
    translator = FakeVisionTranslator(ANSWERS)
    out_dir = tmp_path / "staging"
    out_dir.mkdir()
    report = {}
    markdown, _count, _warnings = docling_parser._convert(
        real_pdf,
        out_dir=out_dir,
        span=None,
        device="cpu",
        settings=ExtractionSettings(),
        report=report,
        structure=request(translator),
    )
    overlay = json.loads((out_dir / "decisions.json").read_text(encoding="utf-8"))
    assert overlay["prompt_rev"] == "260923a" and overlay["policy_rev"] == "260923a"
    assert overlay["model"] == "gpt-5.6-luna"
    [call] = overlay["pages"]["1"]["calls"]
    assert call["usage"] == {"prompt_tokens": 1000, "completion_tokens": 20}
    assert translator.calls == ["gpt-5.6-luna"]
    summary = report["structure"]
    assert summary["applied"] == 2 and summary["asked"] == 5
    # the export saw the corrected items; the snapshot did not
    # a lone bare number is level 2 in pdf_headings, lifted by one: `##`
    assert "## 1 Introduction" in markdown.splitlines()
    assert "```\nimport os\n```" in markdown
    raw = json.loads((out_dir / docling_parser.SNAPSHOT).read_text(encoding="utf-8"))
    assert [t["label"] for t in raw["texts"]][:3] == ["text", "text", "footnote"]


@pytest.fixture
def device(monkeypatch):
    monkeypatch.setattr(
        docling_parser,
        "resolve_device",
        lambda requested: ("cpu", DEVICE_SELECTED.format(device="cpu")),
    )


def test_a_verified_endpoint_runs_the_pass_and_the_manifest_says_so(
    tmp_path, real_pdf, stub_docling, device, capsys
):
    pandoc = pandoc_or_skip()
    bundle = Bundle(tmp_path / "b").create()
    structure = request(FakeVisionTranslator(ANSWERS))
    stages.prepare(bundle, real_pdf, pandoc=pandoc, structure=structure, progress=False)
    out = " ".join(capsys.readouterr().out.split())
    assert (
        "Region roles: 2 of 5 asked items changed by gpt-5.6-luna (3 kept, "
        "0 rejected, 0 pages quarantined); overlay at "
        ".work/extraction/decisions.json." in out
    )
    extraction = bundle.read_manifest()["extraction"]
    assert extraction["structure"] == "gpt-5.6-luna"
    assert extraction["structure_rev"] == REV
    assert extraction["structure_applied"] is True
    assert extraction["structure_totals"]["applied"] == 2
    assert (bundle.work_file("extraction") / "decisions.json").is_file()
    source = bundle.source.read_text(encoding="utf-8")
    assert "```\nimport os\n```" in source
    # the same request is answered from the bundle; another model is not
    luna = ExtractionSettings(structure="gpt-5.6-luna", structure_rev=REV)
    assert stages.already_prepared(bundle, real_pdf, "docling", None, luna)
    sol = ExtractionSettings(structure="gpt-5.6-sol", structure_rev=REV)
    assert not stages.already_prepared(bundle, real_pdf, "docling", None, sol)
    assert not stages.already_prepared(bundle, real_pdf, "docling", None)


def test_an_endpoint_that_cannot_see_keeps_the_detector_s_labels_and_says_so(
    tmp_path, real_pdf, stub_docling, device, capsys
):
    pandoc = pandoc_or_skip()
    bundle = Bundle(tmp_path / "b").create()
    translator = FakeVisionTranslator(ANSWERS, verdict="unsupported")
    stages.prepare(
        bundle, real_pdf, pandoc=pandoc, structure=request(translator), progress=False
    )
    line = STRUCTURE_VISION_UNVERIFIED.format(
        model="gpt-5.6-luna", verdict="unsupported"
    )
    assert line in capsys.readouterr().out.replace("\n", "")
    assert translator.calls == []  # not one page was sent
    manifest = bundle.read_manifest()
    assert line in manifest["limitations"]
    extraction = manifest["extraction"]
    assert extraction["structure"] == "gpt-5.6-luna"
    assert extraction["structure_applied"] is False
    assert not (bundle.work_file("extraction") / "decisions.json").exists()
    assert "import os" in bundle.source.read_text(encoding="utf-8")
    assert "```" not in bundle.source.read_text(encoding="utf-8")


def test_vision_verdict_is_preferred_when_the_translator_has_one():
    translator = FakeVisionTranslator(ANSWERS, verdict="unsupported")
    translator.vision_verdict = lambda model: "verified"
    assert request(translator).vision() == "verified"
    assert translator.capabilities.asked == []


# --------------------------------------------------------------------------
# The route
# --------------------------------------------------------------------------
TRANSLATION = ["--api_format", "google", "--language", "zh-hans"]


@pytest.fixture
def fake_pdf(tmp_path):
    path = tmp_path / "book.pdf"
    path.write_bytes(b"%PDF-1.7\n%fake\n")
    return path


def refusing_stages():
    def prepare_stage(*args, **kwargs):
        pytest.fail("the extraction was reached")

    return {
        "prepare_stage": prepare_stage,
        "translate_stage": prepare_stage,
        "export_stage": prepare_stage,
    }


def test_another_api_format_is_refused_before_anything_is_extracted(
    fake_pdf, monkeypatch
):
    monkeypatch.setattr(to_epub, "find_pandoc", lambda explicit=None: "pandoc")
    with pytest.raises(PipelineError) as refused:
        to_epub.pdf_to_epub(
            fake_pdf,
            TRANSLATION,
            structure_model="gpt-5.6-luna",
            **refusing_stages(),
        )
    assert refused.value.detail == STRUCTURE_ROUTE_UNSUPPORTED.format(
        api_format="google"
    )
    assert not (fake_pdf.parent / "book_book").exists()


def test_a_translator_without_an_image_channel_is_refused():
    with pytest.raises(PipelineError) as refused:
        request(object())
    assert refused.value.detail == STRUCTURE_ROUTE_UNSUPPORTED.format(
        api_format="openai"
    )


def test_the_structure_model_runs_on_the_translation_s_endpoint_and_key(
    fake_pdf, monkeypatch
):
    built = {}

    class Built(FakeVisionTranslator):
        def __init__(self, key, language, api_base=None, **kwargs):
            super().__init__(ANSWERS)
            built.update(key=key, api_base=api_base)

        def set_model_list(self, models):
            built["models"] = list(models)

    monkeypatch.setitem(cli.FORMAT_DICT, "openai", Built)
    monkeypatch.setattr(to_epub, "find_pandoc", lambda explicit=None: "pandoc")
    seen = {}

    def prepare_stage(bundle, source, **kwargs):
        seen.update(kwargs)
        raise PipelineError("stop here", stage="extract")

    stages_ = {
        "prepare_stage": prepare_stage,
        "translate_stage": None,
        "export_stage": None,
    }
    translation = [
        "--model",
        "gpt-5.6-nano",
        "--api_base",
        "https://gateway.example/v1",
        "--key",
        "sk-test",
        "--language",
        "zh-hans",
    ]
    with pytest.raises(PipelineError):
        to_epub.pdf_to_epub(
            fake_pdf, translation, structure_model="gpt-5.6-luna", **stages_
        )
    assert built == {
        "key": "sk-test",
        "api_base": "https://gateway.example/v1",
        "models": ["gpt-5.6-luna"],
    }
    structure = seen["structure"]
    assert structure.model == "gpt-5.6-luna"
    assert structure.rev == REV


def test_without_the_flag_nothing_of_the_pass_is_loaded_or_probed(
    fake_pdf, monkeypatch
):
    """PIN (packet E2): defaults off -- no client, no probe, no import."""
    from book_maker.translator import capabilities

    def never(*args, **kwargs):
        pytest.fail("the image probe ran without --structure-model")

    monkeypatch.setattr(capabilities.CapabilityLedger, "ensure_vision", never)
    monkeypatch.setattr(docling_parser, "_structure_ask", never)
    monkeypatch.delitem(sys.modules, "book_maker.pipeline.decisions", raising=False)
    monkeypatch.setattr(to_epub, "find_pandoc", lambda explicit=None: "pandoc")
    seen = {}

    def prepare_stage(bundle, source, **kwargs):
        seen.update(kwargs)

    def translate_stage(bundle, options, *, pandoc):
        pass

    def export_stage(bundle, *, pandoc):
        bundle.epub.write_bytes(b"PK")
        return bundle.epub

    to_epub.pdf_to_epub(
        fake_pdf,
        TRANSLATION,
        prepare_stage=prepare_stage,
        translate_stage=translate_stage,
        export_stage=export_stage,
    )
    assert seen["structure"] is None
    assert "book_maker.pipeline.decisions" not in sys.modules


def test_the_flag_reaches_the_route_and_not_the_translation(fake_pdf, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        to_epub,
        "pdf_to_epub",
        lambda path, argv, **kwargs: seen.update(argv=list(argv), **kwargs),
    )
    cli.main(
        [
            "--book_name",
            str(fake_pdf),
            "--to-epub",
            "--structure-model",
            "gpt-5.6-luna",
            *TRANSLATION,
        ]
    )
    assert seen["structure_model"] == "gpt-5.6-luna"
    assert to_epub.translation_argv(seen["argv"]) == TRANSLATION


def test_the_harness_hands_the_structure_request_to_the_stage(
    tmp_path, fake_pdf, monkeypatch
):
    pandoc = pandoc_or_skip()
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "pdf_to_book",
        Path(__file__).resolve().parent.parent / "tools" / "pdf_to_book.py",
    )
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    seen = {}

    def record(bundle, path, **kwargs):
        seen.update(kwargs)
        raise PipelineError("stopped before the models", stage="extract")

    made = []
    monkeypatch.setattr(harness, "prepare", record)
    monkeypatch.setattr(
        docling_parser,
        "_structure_ask",
        lambda options, model: made.append(model) or "request",
    )
    base = ["--pandoc", pandoc, "extract", str(fake_pdf), "--output", str(tmp_path)]
    assert harness.main(base) == 1
    assert "structure" not in seen and made == []
    assert harness.main([*base, "--structure-model", "gpt-5.6-luna"]) == 1
    assert seen["structure"] == "request" and made == ["gpt-5.6-luna"]
