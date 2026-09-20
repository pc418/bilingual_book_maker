"""Shared pieces for the bundle-pipeline tests.

A deterministic stand-in translator, registered as an ordinary endpoint
format, so the tests drive `book_maker.cli.main` exactly the way an operator
does instead of reaching past it.
"""

import base64

from book_maker.pipeline.preflight import find_pandoc
from book_maker.pipeline.errors import PipelineError

# 1x1 PNG.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhg"
    "GAWjR9awAAAABJRU5ErkJggg=="
)

FIXTURE = """# Chapter One

The first paragraph is ordinary prose that the model will translate.

The second paragraph carries a [link](https://example.com) and `inline code`,
and its translation comes back as two paragraphs.

![A plate](assets/plate.png)

- first item
- second item

```python
print("never translated")
```

| a | b |
| --- | --- |
| 1 | 2 |

## Notes

A closing paragraph under the second heading.
"""


class FakeTranslator:
    """Fixed answers, so a rendered book is a fact and not a sample."""

    SUPPORTS_REQUEST_EXTRAS = False
    SUPPORTS_BATCH_API = False
    SUPPORTS_SESSION_CONTEXT = False
    SUPPORTS_STRUCTURED_OUTPUT = False

    instances = []
    fail_after = None  # raise KeyboardInterrupt once this many texts are done

    def __init__(self, key, language, api_base=None, **kwargs):
        self.language = language
        self.model_name = "fake-test-model"
        self.translated = []
        self.context_list = []
        self.context_translated_list = []
        type(self).instances.append(self)

    # The one deterministic rule, plus a second paragraph for one block.
    def _answer(self, text):
        stripped = text.strip()
        if stripped.startswith("#"):
            hashes, _, title = stripped.partition(" ")
            return f"{hashes} 译:{title.strip()}"
        if "two paragraphs" in stripped:
            return "译:第一段。\n\n译:第二段。"
        lines = stripped.splitlines()
        if lines and all(line.startswith("- ") for line in lines):
            return "\n".join(f"- 译:{line[2:]}" for line in lines)
        return f"译:{stripped}"

    def translate(self, text):
        self._account(text)
        return self._answer(text)

    def translate_list(self, texts):
        return [self.translate(text) for text in texts]

    def _account(self, text):
        cls = type(self)
        done = sum(len(instance.translated) for instance in cls.instances)
        if cls.fail_after is not None and done >= cls.fail_after:
            raise KeyboardInterrupt("injected interruption")
        self.translated.append(text)

    # Attributes the CLI sets or reads on a translator it built.
    def set_interval(self, interval):
        pass


def register_fake_format(monkeypatch, name="faketest", cls=FakeTranslator):
    from book_maker import cli
    from book_maker.translator import FORMAT_DICT

    monkeypatch.setitem(FORMAT_DICT, name, cls)
    monkeypatch.setitem(cli.FORMAT_DICT, name, cls)
    cls.instances = []
    cls.fail_after = None
    return name


def pandoc_or_skip():
    import pytest

    try:
        return find_pandoc()
    except PipelineError as err:
        pytest.skip(f"pandoc is not available on PATH: {err}")


def write_fixture(directory, text=FIXTURE):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "assets").mkdir(exist_ok=True)
    (directory / "assets" / "plate.png").write_bytes(PNG)
    book = directory / "book.md"
    book.write_text(text, encoding="utf-8")
    return book


def write_pdf(path, pages=("Hello from an embedded text layer.",)):
    """A real PDF, one page per entry; `None` writes a page with no text.

    Built by hand rather than by a library: the tests need a file pdfium
    can actually open and read, and the only thing they vary is whether a
    page carries a text layer at all.
    """
    objects = []

    def add(body):
        objects.append(body)
        return len(objects)  # object numbers start at 1

    catalog = add(b"")  # 1, patched once the page tree number is known
    tree = add(b"")  # 2
    font = add(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
        b"/Encoding /WinAnsiEncoding >>"
    )
    kids = []
    for text in pages:
        if text is None:
            stream = b""
        else:
            escaped = (
                str(text)
                .replace("\\", r"\\")
                .replace("(", r"\(")
                .replace(")", r"\)")
                .encode("ascii", "replace")
            )
            stream = b"BT /F1 18 Tf 72 700 Td (" + escaped + b") Tj ET"
        content = add(
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)
        )
        kids.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
                % (tree, font, content)
            )
        )
    objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % tree
    objects[tree - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % kid for kid in kids),
        len(kids),
    )

    out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        catalog,
        start,
    )
    path.write_bytes(bytes(out))
    return path
