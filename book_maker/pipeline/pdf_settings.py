"""What an extraction was asked to do, as one typed value.

The PDF route configures docling from these fields and nothing else, and
the same value is what a rerun is compared against: a bundle is reused
only when the settings that decide its text are the ones asked for now.

Device is deliberately not here. The models produce the same text on any
processor, so the device is provenance -- recorded, reported when it
differs, never a reason to extract again.

No docling import in this module: it is read on every resume check, and a
Markdown run must not pay for the PDF parser.
"""

from dataclasses import dataclass

from .errors import PipelineError

OCR_ENGINES = ("auto", "rapidocr", "easyocr", "ocrmac", "tesseract")
OCR_MODES = ("default", "full_page", "layout_regions", "pdf_aware_layout_regions")
TABLE_MODES = ("accurate", "fast", "v2")


@dataclass(frozen=True)
class ExtractionSettings:
    ocr: bool = False
    ocr_engine: str = "auto"
    ocr_mode: str = "default"
    ocr_lang: tuple = ()
    table_mode: str = "accurate"
    formula_images: bool = True

    def __post_init__(self):
        # Frozen, so the normalising goes through object.__setattr__: a
        # list handed in (as the manifest stores it) becomes the tuple
        # that keeps the value hashable and comparable.
        object.__setattr__(self, "ocr", bool(self.ocr))
        object.__setattr__(self, "formula_images", bool(self.formula_images))
        object.__setattr__(
            self, "ocr_lang", tuple(str(code) for code in (self.ocr_lang or ()))
        )
        for field, value, allowed in (
            ("ocr_engine", self.ocr_engine, OCR_ENGINES),
            ("ocr_mode", self.ocr_mode, OCR_MODES),
            ("table_mode", self.table_mode, TABLE_MODES),
        ):
            if value not in allowed:
                raise PipelineError(
                    f"{field} {value!r} is not one of {', '.join(allowed)}",
                    stage="extract",
                )

    def identity(self):
        """The fields that decide the extracted text, as the manifest keeps them.

        Without OCR the OCR fields changed nothing, so they are written as
        their defaults: a bundle made without OCR is not invalidated by an
        engine or a language list nobody used.
        """
        return {
            "ocr": self.ocr,
            "ocr_engine": self.ocr_engine if self.ocr else "auto",
            "ocr_mode": self.ocr_mode if self.ocr else "default",
            "ocr_lang": list(self.ocr_lang) if self.ocr and self.ocr_lang else None,
            "table_mode": self.table_mode,
            "formula_images": self.formula_images,
        }

    @classmethod
    def from_manifest(cls, extraction):
        """The settings a past extraction ran with.

        A key the manifest lacks takes its default, because that is what
        the runs from before the key existed did. The engine asked for is
        `ocr_engine_requested`; `ocr_engine` is what docling resolved it to.
        """
        extraction = extraction or {}
        return cls(
            ocr=bool(extraction.get("ocr", False)),
            ocr_engine=extraction.get("ocr_engine_requested") or "auto",
            ocr_mode=extraction.get("ocr_mode") or "default",
            ocr_lang=tuple(extraction.get("ocr_lang") or ()),
            table_mode=extraction.get("table_mode") or "accurate",
            formula_images=bool(extraction.get("formula_images", True)),
        )
