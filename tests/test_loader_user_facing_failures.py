"""A run that stopped for a stated reason must not report success.

`CodexQuotaExhausted` (a spent weekly plan quota) and `ContextWindowUnknown`
(`--context-compact-at 0` on an endpoint that reports no window) carry their
whole explanation in the message and are raised once the run has decided it
cannot continue. The srt and pdf loaders caught `(KeyboardInterrupt,
Exception)` together and exited 0, so a spent quota looked like a finished
book to every caller.
"""

import sys

import pytest

from book_maker.codex_client import CodexQuotaExhausted
from book_maker.loader.base_loader import is_user_facing
from book_maker.loader.srt_loader import SRTBookLoader

SPENT = "the Codex plan allowance is spent and does not reset until 2026-09-08"


class QuotaSpentModel:
    def __init__(self, key, language, **kwargs):
        pass

    def translate(self, text, *args, **kwargs):
        raise CodexQuotaExhausted(SPENT)

    def translate_list(self, texts, *args, **kwargs):
        raise CodexQuotaExhausted(SPENT)

    def set_deployment_id(self, *a, **k):
        pass


def test_the_marker_is_what_the_loaders_read():
    assert is_user_facing(CodexQuotaExhausted("x"))
    assert not is_user_facing(RuntimeError("x"))


def _srt(tmp_path):
    src = tmp_path / "subs.srt"
    src.write_text(
        "1\n00:00:01,000 --> 00:00:02,000\nHello there\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\nSecond line\n",
        encoding="utf-8",
    )
    return SRTBookLoader(
        str(src), QuotaSpentModel, key="", resume=False, language="zh-hans"
    )


def test_a_spent_quota_fails_the_srt_run(tmp_path, capsys):
    loader = _srt(tmp_path)
    with pytest.raises(SystemExit) as stop:
        loader.make_bilingual_book()
    assert stop.value.code == 1, "a stopped run reported a finished subtitle file"
    assert SPENT in capsys.readouterr().out


def test_an_ordinary_srt_failure_still_saves_and_exits_zero(tmp_path):
    # the existing behaviour for everything else is deliberately untouched
    class Boom(QuotaSpentModel):
        def translate(self, text, *args, **kwargs):
            raise RuntimeError("connection reset")

        def translate_list(self, texts, *args, **kwargs):
            raise RuntimeError("connection reset")

    src = tmp_path / "subs.srt"
    src.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
    loader = SRTBookLoader(str(src), Boom, key="", resume=False, language="zh-hans")
    with pytest.raises(SystemExit) as stop:
        loader.make_bilingual_book()
    assert stop.value.code == 0


def test_a_spent_quota_fails_the_pdf_run(tmp_path, capsys):
    fitz = pytest.importorskip("fitz")
    from book_maker.loader.pdf_loader import PDFBookLoader

    pdf_path = tmp_path / "book.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Hello world\nA second sentence here")
    doc.save(str(pdf_path))

    loader = PDFBookLoader(
        str(pdf_path), QuotaSpentModel, key="", resume=False, language="zh-hans"
    )
    with pytest.raises(SystemExit) as stop:
        loader.make_bilingual_book()
    assert stop.value.code == 1
    assert SPENT in capsys.readouterr().out
