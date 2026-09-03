"""Say, in the file itself, that the file is a machine translation.

The rule this follows is the one the rest of the branch follows: preserve
what identifies the work and its rights, rewrite what describes *this*
file, drop what is now false. So `dc:creator`, `dc:rights` and the source's
own `dc:description` are never touched — the tool is appended as a
*contributor* with the MARC relator for translator, and its own description
sits beside the publisher's. Calibre's metadata is the "now false" case: it
records the file calibre built, which this is not.

The colophon says the same thing in prose, on one page at the end, because
metadata is not something a reader sees.
"""

from datetime import date
from html import escape

from ebooklib import epub

TOOL_NAME = "bilingual_book_maker"

# MARC relator "trl" — translator. The id is what the refining meta points
# at, and what a rebuild recognises so it replaces rather than stacks.
CONTRIBUTOR_ID = "trl"
CONTRIBUTOR_ROLE = "trl"
MARC_SCHEME = "marc:relators"

DESCRIPTION_PREFIX = "Machine translation ("

COLOPHON_ID = "colophon"
COLOPHON_FILE = "colophon.xhtml"
COLOPHON_TITLE = "Translation note"
UNREVIEWED = "This translation has not been reviewed by a human translator."

# Calibre writes its record two ways: EPUB 2 `<meta name="calibre:…">` and,
# in books it has converted, whole elements in a namespace of its own.
CALIBRE = "calibre"


def model_id(translator):
    """The model id a translator runs with, for the record the file keeps.

    Never hard-coded: a book that says it was translated by a model it was
    not translated by is worse than one that says nothing. `model_name` is
    the property every translator answers; the fallbacks are for stand-ins
    that are not translators at all.
    """
    if translator is None:
        return "unspecified model"
    name = getattr(translator, "model_name", None)
    if name:
        return str(name)
    return str(getattr(translator, "model", None) or type(translator).__name__)


def is_calibre_metadata(namespace, name, others):
    """Whether a copied metadata entry is calibre describing its own output.

    Matched on the namespace and on the `name`/`property` prefix, because
    the same record arrives either way depending on how the book was built.
    `ibooks:` and every other vendor prefix is left alone: only the entries
    that assert something about a file that no longer exists are dropped.
    """
    if CALIBRE in (namespace or "").lower():
        return True
    if CALIBRE in (name or "").lower().split(":")[0]:
        return True
    for attribute in ("name", "property"):
        value = (others or {}).get(attribute) or ""
        if value.lower().startswith(f"{CALIBRE}:"):
            return True
    return False


def _has_contributor(book):
    return any(
        (others or {}).get("id") == CONTRIBUTOR_ID
        for _, others in book.get_metadata("DC", "contributor")
    )


def _has_description(book):
    return any(
        (value or "").startswith(DESCRIPTION_PREFIX)
        for value, _ in book.get_metadata("DC", "description")
    )


def add_translation_credit(book, model, when=None):
    """Name the tool as translator, once, without touching the creator."""
    when = when or date.today()
    if not _has_contributor(book):
        book.add_metadata("DC", "contributor", TOOL_NAME, {"id": CONTRIBUTOR_ID})
        book.add_metadata(
            None,
            "meta",
            CONTRIBUTOR_ROLE,
            {
                "refines": f"#{CONTRIBUTOR_ID}",
                "property": "role",
                "scheme": MARC_SCHEME,
            },
        )
    if not _has_description(book):
        book.add_metadata(
            "DC",
            "description",
            f"{DESCRIPTION_PREFIX}{model}, {when.year}). Original text "
            f"unaltered; translation quality not verified.",
        )


def is_colophon(item):
    """A colophon this tool wrote, recognised for replacement on a rebuild.

    Both the id and the file name have to match: a source that happens to
    call something of its own "colophon" keeps it.
    """
    return getattr(item, "id", None) == COLOPHON_ID and (
        getattr(item, "file_name", "") or ""
    ).endswith(COLOPHON_FILE)


def build_colophon(model, language, source_identifier=None, when=None):
    """The one page that says, in prose, what this file is."""
    when = when or date.today()
    lines = [
        ("Translated by", TOOL_NAME),
        ("Model", model),
        ("Date", when.isoformat()),
    ]
    if source_identifier:
        lines.append(("Source identifier", source_identifier))
    lines.append(("Target language", language))

    rows = "\n".join(
        f"    <p><strong>{escape(label)}:</strong> {escape(str(value))}</p>"
        for label, value in lines
    )
    document = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<html xmlns="http://www.w3.org/1999/xhtml">\n'
        f"  <head>\n    <title>{COLOPHON_TITLE}</title>\n  </head>\n"
        "  <body>\n"
        f"    <h1>{COLOPHON_TITLE}</h1>\n"
        f"{rows}\n"
        f"    <p>{UNREVIEWED}</p>\n"
        "  </body>\n</html>\n"
    )

    item = epub.EpubHtml(uid=COLOPHON_ID, file_name=COLOPHON_FILE, title=COLOPHON_TITLE)
    item.content = document.encode("utf-8")
    return item


def _entry_is_colophon(source_book, entry):
    """A spine entry may be an item or, on a book that was read from disk,
    a bare idref string that only the source book can resolve."""
    target = entry[0] if isinstance(entry, tuple) else entry
    if isinstance(target, str):
        target = source_book.get_item_with_id(target) if source_book else None
    return target is not None and is_colophon(target)


def attach_colophon(
    book, model, language, source_identifier=None, when=None, source_book=None
):
    """Put the colophon last in the spine, replacing any earlier one.

    Last and linear: a note about the file belongs after the work, never in
    front of it. It is deliberately not added to the navigation — a reader
    looking for chapter one should not find this in the way.
    """
    book.spine = [
        entry for entry in book.spine if not _entry_is_colophon(source_book, entry)
    ]
    item = build_colophon(model, language, source_identifier, when)
    book.add_item(item)
    book.spine.append(item)
    return item
