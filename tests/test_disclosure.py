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

from book_maker.loader.disclosure import (
    model_id,
    COLOPHON_FILE,
    COLOPHON_ID,
    CONTRIBUTOR_ID,
    DESCRIPTION_TAIL,
)
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
    new_book = loader._make_new_book(source)
    # Findings 7/8: `_make_new_book` no longer stamps. The disclosure is
    # applied to the finished book at write time, which is the only moment
    # the model a --model_list run used is settled.
    loader._stamp_disclosure(new_book)
    return new_book


def _written_opf(tmp_path, book, name="out.epub"):
    out = tmp_path / name
    epub.write_epub(str(out), book)
    with zipfile.ZipFile(out) as archive:
        opf_name = next(n for n in archive.namelist() if n.endswith(".opf"))
        return archive.read(opf_name).decode("utf-8")


# ------------------------------------------------------------- the credit


def test_the_tool_is_named_as_a_translator(tmp_path):
    opf = _written_opf(tmp_path, _rebuild(_source()))

    assert (
        f'<dc:contributor id="{CONTRIBUTOR_ID}">bilingual_book_maker</dc:contributor>'
        in opf
    )
    assert (
        f'<meta refines="#{CONTRIBUTOR_ID}" property="role" '
        'scheme="marc:relators">trl</meta>' in opf
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
        assert opf.count(f'id="{CONTRIBUTOR_ID}"') == 1, output.name
        assert opf.count("Machine translation (") == 1, output.name
        assert opf.count(f'href="{COLOPHON_FILE}"') == 1, output.name
        assert opf.count(f'idref="{COLOPHON_ID}"') == 1, output.name
        assert opf.index(f'idref="{COLOPHON_ID}"') > opf.rindex("<spine")


# ------------------------------------------------------------- the off switch


def test_nothing_is_disclosed_when_disclosure_is_off(tmp_path):
    rebuilt = _rebuild(_source(), disclose=False)
    opf = _written_opf(tmp_path, rebuilt)

    assert f'id="{CONTRIBUTOR_ID}"' not in opf
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


def _keys_of(cls):
    """Every `--api_format` / `--provider` key `cls` is registered under.

    A set, because the hermetic stand-in is registered under more than one
    key; a translator answers with one of them.
    """
    from book_maker.translator import FORMAT_DICT, ROUTE_DICT

    return {
        key
        for registry in (FORMAT_DICT, ROUTE_DICT)
        for key, registered in registry.items()
        if registered is cls
    }


def test_a_service_with_no_model_names_the_service():
    """No model to name → the `--api_format` key the service is selected
    by, not the Python class name."""
    from book_maker.translator import FORMAT_DICT

    for key, cls in FORMAT_DICT.items():
        translator = cls.__new__(cls)
        translator.model = None
        assert translator.model_name in _keys_of(cls), key
        assert translator.model_name != cls.__name__, key


def test_a_provider_route_with_no_model_names_the_provider():
    from book_maker.translator import ROUTE_DICT

    for key, cls in ROUTE_DICT.items():
        translator = cls.__new__(cls)
        translator.model = None
        assert translator.model_name in _keys_of(cls), key


def test_a_translator_registered_nowhere_names_its_class():
    from book_maker.translator.base_translator import Base

    class Unregistered(Base):
        def __init__(self):
            pass

        def rotate_key(self):
            pass

        def translate(self, text):
            return text

    assert Unregistered().model_name == "Unregistered"


# ------------------------------- findings 3, 4, 9: names that do not collide


def _source_with(extra_items=(), extra_metadata=(), identifier="urn:uuid:source-1"):
    book = _source(identifier=identifier)
    for item in extra_items:
        book.add_item(item)
        book.spine.append(item)
    for namespace, name, value, others in extra_metadata:
        book.add_metadata(namespace, name, value, others)
    return book


def _chapter(uid, file_name, text="A real chapter."):
    item = epub.EpubHtml(title=uid, file_name=file_name, lang="en")
    item.id = uid
    item.content = (
        "<html xmlns='http://www.w3.org/1999/xhtml'><head><title>t</title></head>"
        f"<body><p>{text}</p></body></html>"
    )
    return item


def _output_of(tmp_path, source, name="book.epub"):
    path = tmp_path / name
    epub.write_epub(str(path), source)
    return _translate_file(path)


def _members_and_opf(output):
    with zipfile.ZipFile(output) as archive:
        members = archive.namelist()
        opf_name = next(n for n in members if n.endswith(".opf"))
        return members, archive.read(opf_name).decode("utf-8")


def _manifest_ids(opf):
    return re.findall(r'<item\b[^>]*\bid="([^"]+)"', opf)


def _all_ids(opf):
    return re.findall(r'\bid="([^"]+)"', opf)


def _assert_sound(members, opf):
    assert len(members) == len(set(members)), "a duplicate zip member"
    ids = _all_ids(opf)
    assert len(ids) == len(set(ids)), f"a duplicate id: {ids}"


def test_a_publishers_colophon_id_is_not_taken_over(tmp_path):
    """Finding 3/9: an `id="colophon"` on something of the book's own must
    keep its id, its file and its place — and ours must go somewhere else."""
    source = _source_with([_chapter("colophon", "notes.xhtml", "The book's notes.")])

    members, opf = _output_of(tmp_path, source), None
    members, opf = _members_and_opf(members)

    _assert_sound(members, opf)
    assert 'id="colophon"' in opf
    assert "notes.xhtml" in opf
    assert "The book" in _text_of(tmp_path, "notes.xhtml")


def test_a_publishers_colophon_filename_is_not_taken_over(tmp_path):
    """Finding 3/9: the source's own colophon.xhtml survives; ours never
    wanted that name in the first place."""
    source = _source_with([_chapter("front", "colophon.xhtml", "Set in Bembo.")])

    members, opf = _members_and_opf(_output_of(tmp_path, source))

    _assert_sound(members, opf)
    assert "EPUB/colophon.xhtml" in members
    assert "Bembo" in _text_of(tmp_path, "colophon.xhtml")


def test_our_own_id_on_a_real_chapter_is_left_alone(tmp_path):
    """Finding 3/4/9: recognition is by the marker in the document, never by
    id — a chapter that happens to carry ours is content, and is translated
    and kept while the note is allocated a suffixed name."""
    source = _source_with(
        [_chapter(COLOPHON_ID, "chapter-two.xhtml", "Chapter two begins.")]
    )

    members, opf = _members_and_opf(_output_of(tmp_path, source))

    _assert_sound(members, opf)
    assert "EPUB/chapter-two.xhtml" in members
    body = _text_of(tmp_path, "chapter-two.xhtml")
    assert "Chapter two begins." in body
    assert "TChapter two begins." in body  # StubModel's translation, still done
    assert f'id="{COLOPHON_ID}-2"' in opf
    assert "bbm_translation_note-2.xhtml" in opf


def test_our_contributor_id_on_someone_elses_metadata_is_left_alone(tmp_path):
    """Finding 4: `dc:creator id="bbm-trl"` belongs to the book. Ours takes
    the next id rather than colliding with it."""
    source = _source_with(
        extra_metadata=[("DC", "creator", "Alice", {"id": CONTRIBUTOR_ID})]
    )

    members, opf = _members_and_opf(_output_of(tmp_path, source))

    _assert_sound(members, opf)
    assert f'<dc:creator id="{CONTRIBUTOR_ID}">Alice</dc:creator>' in opf
    assert f'<dc:contributor id="{CONTRIBUTOR_ID}-2">' in opf
    assert f'refines="#{CONTRIBUTOR_ID}-2"' in opf


def _text_of(tmp_path, file_name):
    output = tmp_path / "book_bilingual.epub"
    with zipfile.ZipFile(output) as archive:
        return archive.read(f"EPUB/{file_name}").decode("utf-8")


class ModelB(StubModel):
    model = "vendor/b"


# ------------------------------------- finding 5: --retranslate copies items


def test_retranslate_does_not_copy_the_previous_translation_note(tmp_path):
    """Finding 5: `retranslate_book` copies every item but the one it edits,
    so a previous output's note was carried into the new book.

    Retranslated with a different model, so a stale note surviving is
    visible: keeping the old page is not merely a duplicate, it is the
    wrong claim about who did the work.
    """
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source())
    once = _translate_file(source)

    loader = EPUBBookLoader(
        str(source), ModelB, key="", resume=False, language="zh-hans"
    )
    loader.quiet = True
    loader.retranslate = [str(once), "chapter.xhtml", "Body text", "Body text"]
    with pytest.raises(SystemExit):
        loader.make_bilingual_book()

    # retranslate_book writes back over the book it was given
    members, opf = _members_and_opf(once)
    _assert_sound(members, opf)
    notes = [m for m in members if "translation_note" in m]
    assert len(notes) == 1
    with zipfile.ZipFile(once) as archive:
        page = archive.read(notes[0]).decode("utf-8")
    assert "vendor/b" in page
    assert "x/y" not in page


# ------------------------------------------ finding 6: the recovery replay


def test_the_recovery_book_survives_a_second_generation_source(tmp_path):
    """Finding 6: `_save_temp_book` filtered our note out of the plan but
    still asked for one plan per document, so an interrupted run over a
    previous output died with StopIteration instead of saving."""
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source())
    once = _translate_file(source)

    loader = EPUBBookLoader(
        str(once), StubModel, key="", resume=False, language="zh-hans"
    )
    loader.quiet = True
    loader.make_bilingual_book()
    # the interruption: some translations done, the run cut short
    loader.p_to_save = loader.p_to_save[:1]

    loader._save_temp_book()

    temp = once.with_name(f"{once.stem}_bilingual_temp.epub")
    assert temp.exists()
    members, opf = _members_and_opf(temp)
    _assert_sound(members, opf)


# -------------------------------- finding 7: a prior stamp is ours to rewrite


def test_a_second_translation_names_only_the_model_that_did_it(tmp_path):
    """Finding 7: the previous run's contributor, refine and description are
    stripped on copy and written again from this run's facts."""
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source())
    once = _translate_file(source)

    loader = EPUBBookLoader(str(once), ModelB, key="", resume=False, language="zh-hans")
    loader.quiet = True
    loader.make_bilingual_book()

    members, opf = _members_and_opf(once.with_name(f"{once.stem}_bilingual.epub"))
    _assert_sound(members, opf)
    assert "vendor/b" in opf
    assert "x/y" not in opf
    assert opf.count("Machine translation (") == 1
    assert opf.count(f'id="{CONTRIBUTOR_ID}"') == 1


def test_a_rebuild_with_disclosure_off_carries_no_prior_stamp(tmp_path):
    """Finding 7: prior disclosure is stripped whether or not a fresh one is
    written — otherwise --no_disclosure would leave the *previous* run's
    claim standing, which is the worst of both."""
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source())
    once = _translate_file(source)

    loader = EPUBBookLoader(
        str(once),
        StubModel,
        key="",
        resume=False,
        language="zh-hans",
        disclose=False,
    )
    loader.quiet = True
    loader.make_bilingual_book()

    members, opf = _members_and_opf(once.with_name(f"{once.stem}_bilingual.epub"))
    _assert_sound(members, opf)
    assert "Machine translation (" not in opf
    assert f'id="{CONTRIBUTOR_ID}"' not in opf
    assert not [m for m in members if "translation_note" in m]


def test_the_books_own_contributors_and_description_are_untouched(tmp_path):
    """Finding 7: only entries carrying *our* value and role refine go."""
    source = _source_with(
        extra_metadata=[
            ("DC", "contributor", "A. Editor", {"id": "ed"}),
            ("DC", "description", "The publisher's blurb.", None),
            ("DC", "contributor", TOOL_NAME_IN_A_CREDIT, {"id": "thanks"}),
        ]
    )

    members, opf = _members_and_opf(_output_of(tmp_path, source))

    _assert_sound(members, opf)
    assert '<dc:contributor id="ed">A. Editor</dc:contributor>' in opf
    assert "The publisher's blurb." in opf
    # credited without a trl refine: the book's statement, not a stamp
    assert '<dc:contributor id="thanks">' in opf


TOOL_NAME_IN_A_CREDIT = "bilingual_book_maker"


# ------------------------------------------- finding 8: which model ran


class RotatingModel(StubModel):
    """A run given --model_list may use any of them — the openai translator's
    shape, where `_model_names` is the readable list beside the cycle."""

    model = "a"
    _model_names = ["a", "b"]


def test_a_model_list_run_names_every_model_it_could_have_used(tmp_path):
    """Finding 8: the model was captured before a single request ran, so a
    rotating run recorded only the first entry."""
    rebuilt = _rebuild(_source(), model=RotatingModel)
    opf = _written_opf(tmp_path, rebuilt, name="rotating.epub")

    assert "Machine translation (a, b," in opf
    page = rebuilt.get_item_with_id(COLOPHON_ID).content.decode("utf-8")
    assert "a, b" in page


class _RotatingStub:
    """The openai translator's shape: `model_list` is an itertools.cycle,
    the readable list lives on `_model_names`."""

    model = "a"
    model_name = "a"

    def __init__(self, names):
        import itertools

        self._model_names = list(names)
        self.model_list = itertools.cycle(names)


class _CycleOnlyStub:
    model = "a"
    model_name = "a"

    def __init__(self):
        import itertools

        self.model_list = itertools.cycle(["a", "b"])


def test_model_id_never_iterates_a_cycle():
    """A `model_list` that is an itertools.cycle used to be iterated into a
    list: unbounded memory at write time on every real openai run (the
    260902 smoke matrix took a 16 GB machine down with it)."""
    assert model_id(_RotatingStub(["a", "b"])) == "a, b"
    assert model_id(_CycleOnlyStub()) == "a"


# --------------------- finding 2 (re-review): whose description is it


def test_a_publishers_own_machine_translation_note_survives(tmp_path):
    """Finding 2 (re-review): the matcher keyed on the opening words alone,
    so a publisher's `<dc:description>Machine translation (French edition)`
    — a statement about the book — was deleted as though this tool had
    written it."""
    source = _source_with(
        extra_metadata=[
            ("DC", "description", "Machine translation (French edition)", None)
        ]
    )

    members, opf = _members_and_opf(_output_of(tmp_path, source))

    _assert_sound(members, opf)
    assert "Machine translation (French edition)" in opf
    # ours is written beside it, not instead of it
    assert opf.count("<dc:description>") == 2
    assert DESCRIPTION_TAIL in opf


def test_our_own_description_is_still_replaced_on_a_rerun(tmp_path):
    """Finding 2 (re-review): recognising the whole sentence must not stop
    the tool from owning what it wrote."""
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source())
    once = _translate_file(source)

    loader = EPUBBookLoader(str(once), ModelB, key="", resume=False, language="zh-hans")
    loader.quiet = True
    loader.make_bilingual_book()

    _, opf = _members_and_opf(once.with_name(f"{once.stem}_bilingual.epub"))

    assert opf.count(DESCRIPTION_TAIL) == 1
    assert "vendor/b" in opf
    assert "x/y" not in opf


# ------------- finding 3 (re-review): the recovery save still copied the note


def _interrupted_loader(tmp_path, disclose=True, model=ModelB):
    """A loader part-way through translating a previous output."""
    source = tmp_path / "book.epub"
    epub.write_epub(str(source), _source())
    once = _translate_file(source)

    loader = EPUBBookLoader(
        str(once),
        model,
        key="",
        resume=False,
        language="zh-hans",
        disclose=disclose,
    )
    loader.quiet = True
    loader.make_bilingual_book()
    loader.p_to_save = loader.p_to_save[:1]
    return loader, once


def test_the_recovery_book_carries_this_runs_note_not_the_last_ones(tmp_path):
    """Finding 3 (re-review): the replay skipped the old note when handing
    out plans but added it to the book anyway, so the stamp saw a note
    already there and left the previous run's claim standing."""
    loader, once = _interrupted_loader(tmp_path)

    loader._save_temp_book()

    temp = once.with_name(f"{once.stem}_bilingual_temp.epub")
    members, opf = _members_and_opf(temp)
    _assert_sound(members, opf)
    notes = [m for m in members if "translation_note" in m]
    assert len(notes) == 1
    with zipfile.ZipFile(temp) as archive:
        page = archive.read(notes[0]).decode("utf-8")
    assert "vendor/b" in page
    assert "x/y" not in page


def test_the_recovery_book_carries_no_note_with_disclosure_off(tmp_path):
    """Finding 3 (re-review): --no_disclosure kept the previous run's note,
    which is the one outcome the flag exists to prevent."""
    loader, once = _interrupted_loader(tmp_path, disclose=False)

    loader._save_temp_book()

    temp = once.with_name(f"{once.stem}_bilingual_temp.epub")
    members, opf = _members_and_opf(temp)
    _assert_sound(members, opf)
    assert not [m for m in members if "translation_note" in m]
    assert DESCRIPTION_TAIL not in opf


# ------------------ finding 4 (re-review): only a rotating run names several


class _StoredListStub:
    """Codex's shape: `set_model_list` keeps every name, but every request
    goes to `self.model`. Naming them all would be a false claim."""

    model = "a"
    model_name = "a"
    model_list = ["a", "b"]


def test_a_stored_model_list_without_rotation_names_one_model():
    """Finding 4 (re-review): a finite `model_list` was read as rotation.
    Only `_model_names`, which the rotating translator keeps, means that."""
    assert model_id(_StoredListStub()) == "a"
