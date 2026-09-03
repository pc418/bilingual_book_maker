"""Undo EPUB font obfuscation so the translated book can ship plain fonts.

Obfuscation is not DRM and this is not circumvention: both algorithms are
published in the OCF specification, both keys are derived from the book's
own public identifier, and the scheme exists so a foundry's font is not
trivially extractable — not to control who may read the book. `check_epub`
has already refused anything else that `META-INF/encryption.xml` might
declare, so by the time this runs the only algorithms that can appear are
the two below.

Why it has to happen at all: `META-INF/encryption.xml` is not a manifest
item, so ebooklib never carries it into the output — which is the outcome
we want, since the translated book encrypts nothing. But dropping the
declaration while keeping scrambled bytes would ship a font no reading
system can parse. Unscrambling on read is what makes the drop correct.

This runs on the raw OCF zip, beside the reader rather than inside it — the
package document is read directly (`helper.read_package`) rather than
through ebooklib, because the key is derived from the identifier the
package *names*, which is not the one ebooklib reports.
"""

import hashlib
import posixpath
import xml.etree.ElementTree as ET
import zipfile
from urllib.parse import unquote

from .helper import read_package

ENCRYPTION_PATH = "META-INF/encryption.xml"

ENC_NS = "{http://www.w3.org/2001/04/xmlenc#}"

IDPF_ALGORITHM = "http://www.idpf.org/2008/embedding"
ADOBE_ALGORITHM = "http://ns.adobe.com/pdf/enc#RC"

# How much of the file each scheme scrambles, and with what key length.
IDPF_WINDOW = 1040  # 52 rounds of a 20-byte SHA-1 digest
ADOBE_WINDOW = 1024  # 64 rounds of the 16-byte UUID


def _idpf_key(identifier):
    """SHA-1 of the unique identifier with every whitespace character gone."""
    return hashlib.sha1("".join(identifier.split()).encode("utf-8")).digest()


def _adobe_key(identifier):
    """The 16 raw bytes of the identifier's UUID, or None if it has none."""
    hexed = "".join(identifier.split()).replace("urn:uuid:", "").replace("-", "")
    try:
        key = bytes.fromhex(hexed)
    except ValueError:
        return None
    return key if len(key) == 16 else None


def _unscramble(data, key, window):
    """XOR the leading window with the key, cycled. Its own inverse."""
    out = bytearray(data)
    for index in range(min(window, len(out))):
        out[index] ^= key[index % len(key)]
    return bytes(out)


def _declarations(archive):
    """(algorithm, container-relative URI) for every encrypted resource."""
    root = ET.fromstring(archive.read(ENCRYPTION_PATH))
    found = []
    for data in root.iter(f"{ENC_NS}EncryptedData"):
        method = data.find(f"{ENC_NS}EncryptionMethod")
        reference = data.find(f"{ENC_NS}CipherData/{ENC_NS}CipherReference")
        if method is None or reference is None:
            continue
        uri = reference.get("URI")
        if uri:
            found.append((method.get("Algorithm"), unquote(uri)))
    return found


def deobfuscate_fonts(book, epub_path):
    """Replace every obfuscated item's content with its plain bytes.

    Returns `(restored, unresolved)` — the URIs put back, and the ones that
    could not be, which the caller should say out loud rather than leave the
    reader to discover as a broken font.

    `book` is an already-read ebooklib book whose items still hold the raw
    bytes; it is mutated in place, and the output copies those same item
    objects by reference.
    """
    try:
        with zipfile.ZipFile(epub_path) as archive:
            if ENCRYPTION_PATH not in archive.namelist():
                return [], []
            declarations = _declarations(archive)
    except Exception:
        # check_epub already read this file; anything failing here is a race
        # or a truncation the reader below will report far better.
        return [], []

    # The OCF key is derived from the identifier the package names as
    # `unique-identifier`. `book.uid` is ebooklib's guess at that — the last
    # identified dc:identifier it read — which on a book carrying an ISBN
    # after its UUID is a different string and therefore a wrong key. It
    # stays only as a last resort, for a package this could not read at all.
    package = read_package(epub_path)
    opf_dir = package.opf_dir
    identifier = package.unique_identifier or getattr(book, "uid", None) or ""

    # A CipherReference URI is relative to the container root, while an
    # ebooklib item's file_name is relative to the OPF. Both spellings are
    # accepted: producers have written each.
    items = {}
    for item in book.get_items():
        name = getattr(item, "file_name", None)
        if not name:
            continue
        items.setdefault(posixpath.normpath(posixpath.join(opf_dir, name)), item)
        items.setdefault(posixpath.normpath(name), item)

    restored, unresolved = [], []
    for algorithm, uri in declarations:
        if algorithm == IDPF_ALGORITHM:
            key, window = _idpf_key(identifier), IDPF_WINDOW
        elif algorithm == ADOBE_ALGORITHM:
            key, window = _adobe_key(identifier), ADOBE_WINDOW
        else:
            # unreachable: check_epub refuses any other algorithm outright
            unresolved.append(uri)
            continue

        item = items.get(posixpath.normpath(uri))
        if key is None or item is None or not getattr(item, "content", None):
            unresolved.append(uri)
            continue
        item.content = _unscramble(item.content, key, window)
        restored.append(uri)
    return restored, unresolved
