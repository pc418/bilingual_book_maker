"""The output file says what it is.

A translated book is a new work made by a machine, and the file should say
so where both a person and a library will see it: in the package metadata,
and on one page at the end. Nothing here touches what identifies the
original — `dc:creator`, `dc:rights` and the rest are the source's and stay
untouched. What goes is only what describes *this file* and is no longer
true of it: calibre's record of the book it built.
"""

import re
import zipfile
from datetime import date

import pytest
from ebooklib import epub

from book_maker.loader.disclosure import COLOPHON_FILE, COLOPHON_ID
from book_maker.loader.epub_loader import EPUBBookLoader

OPF_NS = epub.NAMESPACES["OPF"]
DC_NS = epub.NAMESPACES["DC"]


class StubModel:
    """A translator that knows which model it runs."""

    TRANSLATION_ERROR_MARKER = None
    model = "x/y"

    def __init__(self, *args, **kwargs):
        self._fatal_error_detected = False

    def translate(self, text):
        return f"T{text}"

    def translate_list(self, texts):
        return [self.translate(str(text)) for text in texts]


class ModellessModel(StubModel):
    """Some backends are one service with no model to name."""

    model = None


def _source(identifier="urn:uuid:source-1", metadata=()):
    book = epub.EpubBook()
    book.set_identifier(identifier)
    book.set_title("Disclosure fixture")
    book.set_language("en")
    book.add_author("A. Author")
    for namespace, name, value, others in metadata:
        book.add_metadata(namespace, name, value, others)
    chapter = epub.EpubHtml(title="One", file_name="chapter.xhtml", lang="en")
    chapter.content = "<html><body><p>Body text</p></body></html>"
    book.add_item(chapter)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = (chapter,)
    book.spine = ["nav", chapter]
    return book


def _rebuild(
    source, *, model=StubModel, disclose=True, single=False, language="zh-hans"
):
    loader = EPUBBookLoader.__new__(EPUBBookLoader)
    loader.origin_book = source
    loader.language = language
    loader.single_translate = single
    loader.disclose = disclose
    loader.translate_model = model() if model else None
    return loader._make_new_book(source)


def _written_opf(tmp_path, book, name="out.epub"):
    out = tmp_path / name
    epub.write_epub(str(out), book)
    with zipfile.ZipFile(out) as archive:
        opf_name = next(n for n in archive.namelist() if n.endswith(".opf"))
        return archive.read(opf_name).decode("utf-8")


# ------------------------------------------------------------- the credit


def test_the_tool_is_named_as_a_translator(tmp_path):
    opf = _written_opf(tmp_path, _rebuild(_source()))

    assert '<dc:contributor id="trl">bilingual_book_maker</dc:contributor>' in opf
    assert (
        '<meta refines="#trl" property="role" scheme="marc:relators">trl</meta>' in opf
    )


def test_the_author_is_left_alone(tmp_path):
    """The original's creator is the original's; a translation does not
    edit it, add to it, or push the tool into it."""
    rebuilt = _rebuild(_source())

    creators = rebuilt.get_metadata("DC", "creator")

    assert [value for value, _ in creators] == ["A. Author"]


def test_the_description_names_the_model_the_run_used(tmp_path):
    opf = _written_opf(tmp_path, _rebuild(_source()))

    year = date.today().year
    assert (
        f"<dc:description>Machine translation (x/y, {year}). Original text "
        f"unaltered; translation quality not verified.</dc:description>" in opf
    )


def test_a_translator_with_no_model_still_says_what_made_the_file(tmp_path):
    opf = _written_opf(tmp_path, _rebuild(_source(), model=ModellessModel))

    assert "Machine translation (ModellessModel," in opf


def test_the_source_description_survives(tmp_path):
    source = _source(metadata=[("DC", "description", "The publisher's blurb.", None)])

    opf = _written_opf(tmp_path, _rebuild(source))

    assert "<dc:description>The publisher's blurb.</dc:description>" in opf
    assert opf.count("<dc:description>") == 2


# ------------------------------------------------------------ the colophon


def _colophon_of(book):
    return book.get_item_with_id(COLOPHON_ID)


def test_the_colophon_is_the_last_thing_in_the_book(tmp_path):
    rebuilt = _rebuild(_source())

    assert _colophon_of(rebuilt) is not None
    assert rebuilt.spine[-1] is _colophon_of(rebuilt)
    assert rebuilt.spine[0] is not _colophon_of(rebuilt)


def test_the_colophon_says_everything_it_has_to(tmp_path):
    rebuilt = _rebuild(_source(identifier="urn:uuid:source-1"))
    page = _colophon_of(rebuilt).content.decode("utf-8")

    assert "<title>" in page
    assert "Translation note" in page
    assert "bilingual_book_maker" in page
    assert "x/y" in page
    assert date.today().isoformat() in page
    assert "urn:uuid:source-1" in page
    assert "zh-hans" in page
    assert "This translation has not been reviewed by a human translator." in page


def test_the_colophon_omits_a_source_line_there_is_no_identifier_for(tmp_path):
    source = _source()
    source.uid = None
    page = _colophon_of(_rebuild(source)).content.decode("utf-8")

    assert "Source identifier" not in page
    assert "This translation has not been reviewed by a human translator." in page


def test_a_single_translation_gets_the_colophon_too(tmp_path):
    rebuilt = _rebuild(_source(), single=True)

    assert rebuilt.spine[-1] is _colophon_of(rebuilt)


def test_the_colophon_is_a_document_at_the_package_root(tmp_path):
    rebuilt = _rebuild(_source())
    colophon = _colophon_of(rebuilt)

    assert colophon.file_name == COLOPHON_FILE
    assert colophon.media_type == "application/xhtml+xml"


def test_the_colophon_is_not_put_in_the_navigation(tmp_path):
    """It is a note about the file, not a chapter of the book."""
    rebuilt = _rebuild(_source())

    def titles(entries):
        for entry in entries:
            if isinstance(entry, tuple):
                yield from titles(entry[1])
                yield getattr(entry[0], "title", "")
            else:
                yield getattr(entry, "title", "")

    assert "Translation note" not in set(titles(rebuilt.toc))


# ------------------------------------------------------- rebuilding a rebuild


def _translate_file(path):
    loader = EPUBBookLoader(
        str(path), StubModel, key="", resume=False, language="zh-hans"
    )
    loader.quiet = True
    loader.make_bilingual_book()
    return path.with_name(f"{path.stem}_bilingual.epub")


def test_a_rebuild_of_a_translated_book_stacks_nothing(tmp_path):
    """Run the whole tool on its own output and every disclosure is
    replaced, not doubled: one contributor, one machine-translation
    description, one colophon item, one spine entry for it.

    The manifest is what makes this a real test rather than a metadata one
    — the source's colophon arrives as an ordinary document, and a second
    entry under the same id is a book no reading system will open."""
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source())

    once = _translate_file(source)
    twice = _translate_file(once)

    for output in (once, twice):
        with zipfile.ZipFile(output) as archive:
            opf_name = next(n for n in archive.namelist() if n.endswith(".opf"))
            opf = archive.read(opf_name).decode("utf-8")
        assert opf.count('id="trl"') == 1, output.name
        assert opf.count("Machine translation (") == 1, output.name
        assert opf.count(f'href="{COLOPHON_FILE}"') == 1, output.name
        assert opf.count(f'idref="{COLOPHON_ID}"') == 1, output.name
        assert opf.index(f'idref="{COLOPHON_ID}"') > opf.rindex("<spine")


# ------------------------------------------------------------- the off switch


def test_nothing_is_disclosed_when_disclosure_is_off(tmp_path):
    rebuilt = _rebuild(_source(), disclose=False)
    opf = _written_opf(tmp_path, rebuilt)

    assert 'id="trl"' not in opf
    assert "Machine translation (" not in opf
    assert _colophon_of(rebuilt) is None
    assert COLOPHON_FILE not in opf


def test_the_loader_takes_the_switch(tmp_path):
    source_path = tmp_path / "book.epub"
    epub.write_epub(str(source_path), _source())

    loader = EPUBBookLoader(
        str(source_path),
        StubModel,
        key="",
        resume=False,
        language="zh-hans",
        disclose=False,
    )

    assert loader.disclose is False
    assert _colophon_of(loader._make_new_book(loader.origin_book)) is None


def test_disclosure_is_on_by_default(tmp_path):
    source_path = tmp_path / "book.epub"
    epub.write_epub(str(source_path), _source())

    loader = EPUBBookLoader(
        str(source_path), StubModel, key="", resume=False, language="zh-hans"
    )

    assert loader.disclose is True


# ---------------------------------------------------------- calibre's record


CALIBRE_METAS = [
    (None, "meta", None, {"name": "calibre:title_sort", "content": "Sorted"}),
    (None, "meta", None, {"name": "calibre:timestamp", "content": "2016-06-01"}),
]


def _calibre_source():
    source = _source()
    opf_metas = source.metadata.setdefault(OPF_NS, {}).setdefault("meta", [])
    opf_metas.append((None, {"name": "calibre:title_sort", "content": "Sorted"}))
    opf_metas.append((None, {"name": "calibre:timestamp", "content": "2016-06-01"}))
    opf_metas.append((None, {"name": "cover", "content": "cover"}))
    opf_metas.append(
        ("Yes", {"property": "ibooks:specified-fonts"}),
    )
    source.metadata["http://calibre.kovidgoyal.net/2009/metadata"] = {
        "series": [("Some series", {})]
    }
    return source


def test_calibres_record_of_the_file_it_built_is_dropped(tmp_path):
    """It describes a different file — the one calibre made, not this one."""
    opf = _written_opf(tmp_path, _rebuild(_calibre_source()))

    assert "calibre" not in opf


def test_what_the_reading_system_needs_is_kept(tmp_path):
    opf = _written_opf(tmp_path, _rebuild(_calibre_source()))

    assert '<meta name="cover" content="cover"/>' in opf
    assert 'property="ibooks:specified-fonts"' in opf


def test_calibre_metadata_goes_even_with_disclosure_off(tmp_path):
    """Dropping a false description of the file is not a disclosure; it is
    correctness, and has no switch."""
    opf = _written_opf(tmp_path, _rebuild(_calibre_source(), disclose=False))

    assert "calibre" not in opf


# ------------------------------------------------------------- the model id


def test_every_translator_answers_what_model_it_runs():
    """No translator may leave the file unable to say what made it."""
    from book_maker.translator import FORMAT_DICT, ROUTE_DICT

    for name, translator in {**FORMAT_DICT, **ROUTE_DICT}.items():
        assert isinstance(getattr(translator, "model_name", None), property), name


def test_the_llm_translators_report_the_model_they_were_given():
    from book_maker.translator import FORMAT_DICT, LLM_FORMATS

    for name in LLM_FORMATS:
        translator = FORMAT_DICT[name].__new__(FORMAT_DICT[name])
        translator.model = "vendor/some-model"
        assert translator.model_name == "vendor/some-model", name


def test_a_service_with_no_model_names_the_service():
    from book_maker.translator import Google

    assert Google.__new__(Google).model_name == "Google"
