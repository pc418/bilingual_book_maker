"""Obfuscated fonts are unscrambled on read.

`META-INF/encryption.xml` is a manifest-less OCF member, so ebooklib never
carries it into the output — which is the policy we want, since the output
declares no encryption of any kind. That only stays *correct* if the fonts
it described are plain by then. Otherwise the translated book ships a
scrambled font with nothing left to explain it.

The reference obfuscation below is written out independently of the
implementation on purpose: a test that reuses the module's own XOR would
pass on any key it computed, right or wrong.
"""

import hashlib
import zipfile

import pytest
from ebooklib import epub

from book_maker.loader.epub_loader import EPUBBookLoader
from book_maker.loader.font_obfuscation import deobfuscate_fonts

IDPF_ALGORITHM = "http://www.idpf.org/2008/embedding"
ADOBE_ALGORITHM = "http://ns.adobe.com/pdf/enc#RC"

IDENTIFIER = "urn:uuid:0d6e2b1f-4c1a-4f7a-9a0e-2f4f7d8a1b2c"

LONG_FONT = bytes(range(256)) * 8  # 2048 bytes, past both key windows
SHORT_FONT = bytes(range(256)) + bytes(range(44))  # 300 bytes, inside both


class SilentModel:
    TRANSLATION_ERROR_MARKER = "[Translation unavailable]"

    def __init__(self, *args, **kwargs):
        self._fatal_error_detected = False

    def translate(self, text):
        return f"<T>{text}</T>"

    def translate_list(self, texts):
        return [self.translate(str(text)) for text in texts]


# ------------------------------------------- an independent reference impl


def _idpf_key(identifier):
    stripped = "".join(identifier.split())
    return hashlib.sha1(stripped.encode("utf-8")).digest()


def _adobe_key(identifier):
    hexed = "".join(identifier.split())
    hexed = hexed.replace("urn:uuid:", "").replace("-", "")
    return bytes.fromhex(hexed)


def _scramble(data, key, window):
    out = bytearray(data)
    for index in range(min(window, len(out))):
        out[index] ^= key[index % len(key)]
    return bytes(out)


def _obfuscate(data, algorithm, identifier):
    if algorithm == IDPF_ALGORITHM:
        return _scramble(data, _idpf_key(identifier), 1040)
    try:
        key = _adobe_key(identifier)
    except ValueError:
        return data  # no UUID, no key — the fixture ships the font as it is
    return _scramble(data, key, 1024)


# ------------------------------------------------------------- the fixture


ENCRYPTION = """<?xml version="1.0" encoding="UTF-8"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container"
            xmlns:enc="http://www.w3.org/2001/04/xmlenc#">
  <enc:EncryptedData>
    <enc:EncryptionMethod Algorithm="{algorithm}"/>
    <enc:CipherData><enc:CipherReference URI="{uri}"/></enc:CipherData>
  </enc:EncryptedData>
</encryption>
"""


def _write_source(path, font, algorithm, identifier=IDENTIFIER):
    book = epub.EpubBook()
    book.set_identifier(identifier)
    book.set_title("Obfuscated font fixture")
    book.set_language("en")
    chapter = epub.EpubHtml(title="One", file_name="chapter.xhtml", lang="en")
    chapter.content = "<html><body><p>Body text</p></body></html>"
    book.add_item(chapter)
    book.add_item(
        epub.EpubItem(
            uid="font",
            file_name="fonts/obfuscated.otf",
            media_type="application/vnd.ms-opentype",
            content=font,
        )
    )
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    book.toc = (chapter,)
    book.spine = ["nav", chapter]
    epub.write_epub(str(path), book)

    font_member = "EPUB/fonts/obfuscated.otf"
    replacement = path.with_suffix(".replacement.epub")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(replacement, "w") as target:
        for info in source.infolist():
            target.writestr(
                info,
                (
                    _obfuscate(font, algorithm, identifier)
                    if info.filename == font_member
                    else source.read(info.filename)
                ),
            )
        target.writestr(
            "META-INF/encryption.xml",
            ENCRYPTION.format(algorithm=algorithm, uri=font_member),
        )
        # a signature must survive the same drop, and change nothing
        target.writestr("META-INF/signatures.xml", "<signatures/>")
    replacement.replace(path)
    return path


def _load(path):
    return EPUBBookLoader(
        str(path), SilentModel, key="", resume=False, language="zh-hans"
    )


# --------------------------------------------------------------- the cases


@pytest.mark.parametrize("algorithm", [IDPF_ALGORITHM, ADOBE_ALGORITHM])
@pytest.mark.parametrize("font", [LONG_FONT, SHORT_FONT], ids=["long", "short"])
def test_the_font_is_plain_once_the_book_is_loaded(tmp_path, algorithm, font):
    path = _write_source(tmp_path / "book.epub", font, algorithm)
    loader = _load(path)
    assert loader.origin_book.get_item_with_id("font").content == font


def test_the_written_book_carries_the_plain_font_and_no_declaration(tmp_path):
    path = _write_source(tmp_path / "book.epub", LONG_FONT, IDPF_ALGORITHM)
    loader = _load(path)
    loader.quiet = True
    loader.make_bilingual_book()

    output = tmp_path / "book_bilingual.epub"
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert archive.read("EPUB/fonts/obfuscated.otf") == LONG_FONT
    assert "META-INF/encryption.xml" not in names
    assert "META-INF/signatures.xml" not in names


def test_a_book_with_no_encryption_declaration_is_untouched(tmp_path):
    path = _write_source(tmp_path / "book.epub", LONG_FONT, IDPF_ALGORITHM)
    # rebuild without the declaration; the font stays scrambled and nothing
    # in the code may decide to unscramble it anyway
    plain = tmp_path / "plain.epub"
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(plain, "w") as target:
        for info in source.infolist():
            if info.filename == "META-INF/encryption.xml":
                continue
            target.writestr(info, source.read(info.filename))

    loader = _load(plain)
    scrambled = _obfuscate(LONG_FONT, IDPF_ALGORITHM, IDENTIFIER)
    assert loader.origin_book.get_item_with_id("font").content == scrambled


def test_an_unresolvable_reference_is_reported_not_swallowed(tmp_path):
    """A URI naming nothing in the manifest leaves a scrambled font behind;
    the caller has to be able to say so."""
    path = _write_source(tmp_path / "book.epub", LONG_FONT, IDPF_ALGORITHM)
    member = "META-INF/encryption.xml"
    replacement = tmp_path / "broken.epub"
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(replacement, "w") as target:
        for info in source.infolist():
            target.writestr(
                info,
                (
                    ENCRYPTION.format(
                        algorithm=IDPF_ALGORITHM, uri="EPUB/fonts/absent.otf"
                    ).encode("utf-8")
                    if info.filename == member
                    else source.read(info.filename)
                ),
            )

    book = epub.read_epub(str(replacement))
    restored, unresolved = deobfuscate_fonts(book, str(replacement))
    assert restored == []
    assert unresolved == ["EPUB/fonts/absent.otf"]


def test_an_identifier_that_is_not_a_uuid_defeats_the_adobe_key(tmp_path):
    """Adobe's key *is* the UUID; there is no key to derive without one, so
    the font is reported unresolved rather than mangled further."""
    path = _write_source(
        tmp_path / "book.epub", LONG_FONT, ADOBE_ALGORITHM, identifier="not-a-uuid"
    )
    book = epub.read_epub(str(path))
    restored, unresolved = deobfuscate_fonts(book, str(path))
    assert restored == []
    assert unresolved == ["EPUB/fonts/obfuscated.otf"]


# ---------------------------- finding 2: which identifier holds the key


UNIQUE_ID = "urn:uuid:44444444-4444-4444-8444-444444444444"
OTHER_ID = "urn:uuid:99999999-9999-4999-8999-999999999999"


def _two_identifier_source(tmp_path, algorithm):
    """A book whose `unique-identifier` is the *first* dc:identifier.

    ebooklib sets `book.uid` from the last identified dc:identifier it
    reads, not the one the package names, so a book like this hands the
    wrong key to anything that trusts `uid`.
    """
    path = _write_source(tmp_path / "two-ids.epub", LONG_FONT, algorithm, UNIQUE_ID)

    with zipfile.ZipFile(path) as archive:
        member = next(n for n in archive.namelist() if n.endswith(".opf"))
        opf = archive.read(member).decode("utf-8")
    opf = opf.replace(
        "</metadata>",
        f'<dc:identifier id="isbn">{OTHER_ID}</dc:identifier></metadata>',
        1,
    )
    replacement = path.with_suffix(".fixed.epub")
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(replacement, "w") as target:
        for info in source.infolist():
            target.writestr(
                info,
                (
                    opf.encode("utf-8")
                    if info.filename == member
                    else source.read(info.filename)
                ),
            )
    replacement.replace(path)
    return path


@pytest.mark.parametrize("algorithm", [IDPF_ALGORITHM, ADOBE_ALGORITHM])
def test_the_key_comes_from_the_identifier_the_package_names(tmp_path, algorithm):
    """Finding 2: the key is the *unique-identifier*, not whichever
    dc:identifier ebooklib happened to read last."""
    path = _two_identifier_source(tmp_path, algorithm)

    book = epub.read_epub(str(path))
    assert book.uid == OTHER_ID, "the fixture must actually mislead ebooklib"

    restored, unresolved = deobfuscate_fonts(book, str(path))

    assert unresolved == []
    assert restored == ["EPUB/fonts/obfuscated.otf"]
    assert book.get_item_with_id("font").content == LONG_FONT
