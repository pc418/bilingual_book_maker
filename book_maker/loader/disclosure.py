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

Two rules keep this from colliding with the book it is stamping:

- **Nothing is recognised by its id or file name.** A publisher's own
  `colophon.xhtml`, or an `id="colophon"` on a real chapter, must survive
  untouched. What this tool wrote is recognised only by a marker it put
  inside the document, and a contributor only by its value plus its role
  refine.
- **Every id and file name is allocated against what is already there.**
  If the book already uses the name this would take, the stamp takes the
  next one instead — a duplicate id is a book no reading system opens.

And what a previous run of this tool left behind is *owned*, not respected:
it is stripped on copy and rewritten from this run's facts, so a book
translated by model A and then again by model B does not claim both.
"""

import re
from datetime import date
from html import escape

from ebooklib import epub

TOOL_NAME = "bilingual_book_maker"

# MARC relator "trl" — translator.
CONTRIBUTOR_ID = "bbm-trl"
CONTRIBUTOR_ROLE = "trl"
MARC_SCHEME = "marc:relators"

DESCRIPTION_PREFIX = "Machine translation ("

COLOPHON_ID = "bbm-translation-note"
COLOPHON_STEM = "bbm_translation_note"
COLOPHON_FILE = f"{COLOPHON_STEM}.xhtml"
COLOPHON_TITLE = "Translation note"
UNREVIEWED = "This translation has not been reviewed by a human translator."

# What makes a colophon *ours*, written into the document and read back out
# of it. An id or a file name is a coincidence waiting to happen; a
# generator marker in the head is a statement.
GENERATOR_MARK = "bilingual_book_maker translation note"

# Calibre writes its record two ways: EPUB 2 `<meta name="calibre:…">` and,
# in books it has converted, whole elements in a namespace of its own.
CALIBRE = "calibre"


def model_id(translator):
    """What to record as the model, from the translator that will run.

    Never hard-coded: a book that says it was translated by a model it was
    not translated by is worse than one that says nothing. A run given a
    `--model_list` may use any of them — which one a given paragraph went
    to is not knowable from here — so all of them are named. That is the
    honest statement; picking the first would be a false one.
    """
    if translator is None:
        return "unspecified model"
    # `_model_names` is the readable list a rotating translator keeps beside
    # its `model_list`; `model_list` itself may be an itertools.cycle, and
    # iterating one never ends. Only a real sequence is read here — a
    # smoke run on 260902 found the write step of every openai cell
    # growing past 2.5 GB inside this comprehension.
    for attribute in ("_model_names", "model_list"):
        models = getattr(translator, attribute, None)
        if isinstance(models, (list, tuple)):
            names = [str(name) for name in models if name]
            if len(names) > 1:
                return ", ".join(names)
            break
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


# ------------------------------------------------- recognising our own work

_HEAD_RE = re.compile(rb"<head\b[^>]*>(.*?)</head>", re.IGNORECASE | re.DOTALL)
_META_RE = re.compile(rb"<meta\b[^>]*?/?>", re.IGNORECASE)
_GENERATOR_RE = re.compile(rb"""\bname\s*=\s*(["'])generator\1""", re.IGNORECASE)
_MARK_RE = re.compile(
    rb"""\bcontent\s*=\s*(["'])"""
    + re.escape(GENERATOR_MARK.encode("utf-8"))
    + rb"""\1""",
    re.IGNORECASE,
)


def is_our_colophon(item):
    """Whether this document is a translation note *this tool* wrote.

    Read from the head's generator meta, never from the id or the file
    name: a book of its own may legitimately carry either, and mistaking
    one for ours would delete a page of the book. The head is isolated
    first so a marker quoted in the body text cannot pass for a
    declaration.
    """
    content = getattr(item, "content", None)
    if not content:
        return False
    if isinstance(content, str):
        content = content.encode("utf-8", "ignore")
    head = _HEAD_RE.search(content)
    if head is None:
        return False
    return any(
        _GENERATOR_RE.search(meta) and _MARK_RE.search(meta)
        for meta in _META_RE.findall(head.group(1))
    )


def _iter_metadata(book):
    """(namespace, name, value, others) for every metadata entry."""
    for namespace, metas in book.metadata.items():
        if not isinstance(metas, dict):
            continue
        for name, values in metas.items():
            for entry in values:
                if isinstance(entry, tuple):
                    value = entry[0]
                    others = entry[1] if len(entry) > 1 else None
                else:
                    value, others = entry, None
                yield namespace, name, value, others


def tool_contributor_ids(book):
    """The ids of `dc:contributor` entries a previous run of this tool wrote.

    Ours is the tool's name refined by the `trl` role — both halves, so a
    book that merely credits this project in its own contributor list is
    not mistaken for a stamp and quietly deleted.
    """
    refined = {
        (others or {}).get("refines", "").lstrip("#")
        for _, name, value, others in _iter_metadata(book)
        if name == "meta"
        and (others or {}).get("property") == "role"
        and (value or "").strip() == CONTRIBUTOR_ROLE
    }
    refined.discard("")
    return {
        (others or {}).get("id")
        for _, name, value, others in _iter_metadata(book)
        if name == "contributor"
        and value == TOOL_NAME
        and (others or {}).get("id") in refined
    }


def is_prior_disclosure(name, value, others, owned_ids):
    """Whether a copied entry is a previous run's stamp, to be replaced.

    A previous run's claim is this tool's to rewrite, not to preserve:
    translate model A's output with model B and only B did the work in
    front of the reader.
    """
    attributes = others or {}
    if (
        name == "contributor"
        and value == TOOL_NAME
        and attributes.get("id") in owned_ids
    ):
        return True
    if (
        name == "meta"
        and attributes.get("property") == "role"
        and attributes.get("refines", "").lstrip("#") in owned_ids
    ):
        return True
    if name == "description" and (value or "").startswith(DESCRIPTION_PREFIX):
        return True
    return False


# ------------------------------------------------------------- allocating


def taken_ids(book):
    """Every id already spoken for in the package: metadata and manifest."""
    ids = {
        (others or {}).get("id")
        for _, _, _, others in _iter_metadata(book)
        if (others or {}).get("id")
    }
    for item in book.get_items():
        item_id = item.get_id()
        if item_id:
            ids.add(item_id)
    return ids


def _suffixes():
    yield ""
    counter = 2
    while True:
        yield f"-{counter}"
        counter += 1


def allocate_contributor_id(book):
    ids = taken_ids(book)
    for suffix in _suffixes():
        candidate = f"{CONTRIBUTOR_ID}{suffix}"
        if candidate not in ids:
            return candidate


def allocate_colophon_names(book):
    """An id and a file name neither of which the book already uses.

    Both move together: a reader that finds `bbm_translation_note-2.xhtml`
    should find it under the matching id, not under a third name.
    """
    ids = taken_ids(book)
    files = {
        item.file_name for item in book.get_items() if getattr(item, "file_name", None)
    }
    for suffix in _suffixes():
        item_id = f"{COLOPHON_ID}{suffix}"
        file_name = f"{COLOPHON_STEM}{suffix}.xhtml"
        if item_id not in ids and file_name not in files:
            return item_id, file_name


# ------------------------------------------------------------- the stamp


def build_colophon(
    model, language, source_identifier=None, when=None, item_id=None, file_name=None
):
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
        "  <head>\n"
        f"    <title>{COLOPHON_TITLE}</title>\n"
        f'    <meta name="generator" content="{GENERATOR_MARK}"/>\n'
        "  </head>\n"
        "  <body>\n"
        f"    <h1>{COLOPHON_TITLE}</h1>\n"
        f"{rows}\n"
        f"    <p>{UNREVIEWED}</p>\n"
        "  </body>\n</html>\n"
    )

    item = epub.EpubHtml(
        uid=item_id or COLOPHON_ID,
        file_name=file_name or COLOPHON_FILE,
        title=COLOPHON_TITLE,
    )
    item.content = document.encode("utf-8")
    return item


def entry_is_our_colophon(source_book, entry):
    """A spine entry may be an item or, on a book read from disk, a bare
    idref string that only the source book can resolve."""
    target = entry[0] if isinstance(entry, tuple) else entry
    if isinstance(target, str):
        target = source_book.get_item_with_id(target) if source_book else None
    return target is not None and is_our_colophon(target)


def stamp_disclosure(book, model, language, source_identifier=None, when=None):
    """Add the credit, the description and the colophon, once.

    Called on the finished book just before it is written, not while it is
    being built: `--model_list` rotation means the model a run actually
    used is not known until the last paragraph is done.

    Idempotent, because one route writes the book twice.
    """
    if any(is_our_colophon(item) for item in book.get_items()):
        return None

    contributor_id = allocate_contributor_id(book)
    book.add_metadata("DC", "contributor", TOOL_NAME, {"id": contributor_id})
    book.add_metadata(
        None,
        "meta",
        CONTRIBUTOR_ROLE,
        {
            "refines": f"#{contributor_id}",
            "property": "role",
            "scheme": MARC_SCHEME,
        },
    )
    when = when or date.today()
    book.add_metadata(
        "DC",
        "description",
        f"{DESCRIPTION_PREFIX}{model}, {when.year}). Original text "
        f"unaltered; translation quality not verified.",
    )

    item_id, file_name = allocate_colophon_names(book)
    item = build_colophon(
        model,
        language,
        source_identifier,
        when,
        item_id=item_id,
        file_name=file_name,
    )
    book.add_item(item)
    book.spine.append(item)
    return item
