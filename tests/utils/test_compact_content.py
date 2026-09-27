# tests/utils/test_compact_content.py
#
# Ground truth: hand-written spellings and byte strings, and pdfium renders.

import io
from decimal import Decimal

import numpy as np
import pikepdf
import pytest

import pdftl.utils.compact_content as cc


@pytest.mark.parametrize(
    "value,spelled",
    [
        ("0.50", b".5"),
        ("-0.25", b"-.25"),
        ("2.0", b"2"),
        ("-3.000", b"-3"),
        ("12.340", b"12.34"),
        ("0", b"0"),
        ("-0.0", b"0"),
        ("100", b"100"),
        ("0.0001", b".0001"),
    ],
)
def test_shortest_number_spelling(value, spelled):
    assert cc._number(Decimal(value)) == spelled


def test_unreadable_number_keeps_its_spelling():
    assert cc._shortest(b"+-1") == b"+-1"
    assert cc._shortest(b"0\xff") == b"0\xff"


@pytest.mark.parametrize(
    "data,compact",
    [
        (
            b"q 1.0 0 0 1.00 0.5 -0.50 cm\nBT /F1 12.0 Tf [ (A) -120.0 (B) ] TJ ET\n"
            b"/Tag << /MCID 3 >> BDC EMC [1 2] 0 d Q",
            b"q 1 0 0 1 .5 -.5 cm BT/F1 12 Tf[(A)-120(B)]TJ ET/Tag<</MCID 3>>BDC EMC[1 2]0 d Q",
        ),
        (b"1 2 % note\n3", b"1 2 3"),  # a comment ends a token
        (b"q\r\n%only a comment\n Q", b"q Q"),
        (b"+5 -0 00012 .50 -.0 1.", b"5 0 12 .5 0 1"),
        (b"true false null /A 1 <0a> (s) <</K 2>>", b"true false null/A 1<0a>(s)<</K 2>>"),
        (b"/A /B 1 1.5 .5", b"/A/B 1 1.5 .5"),
        (b"", b""),
    ],
)
def test_compact_stream(data, compact):
    assert cc.compact_stream(data) == compact


def test_inline_image_is_copied_byte_for_byte():
    data = b"q  1 0 0 1 0 0 cm BI /W 2 /H 1  /BPC 8 /CS /G ID \x00 \xff EI  Q"
    assert (
        cc.compact_stream(data)
        == b"q 1 0 0 1 0 0 cm BI /W 2 /H 1  /BPC 8 /CS /G ID \x00 \xff EI Q"
    )


@pytest.mark.parametrize(
    "data",
    [
        b"q (unterminated",
        b"<zz> Tj",
        b"q BI /W 1 ID",  # no EI
        b"q BI /W 1 /H 1 /BPC 8 /CS /G ID \x10",
    ],
)
def test_compact_stream_refuses_malformed_content(data):
    assert cc.compact_stream(data) is None


def test_compact_stream_refuses_a_rewrite_with_other_tokens(monkeypatch):
    monkeypatch.setattr(cc, "significant_tokens", lambda data: 99)
    assert cc.compact_stream(b"1 0 0 1 0 0 cm") is None


def _page_pdf(content: bytes):
    pdf = pikepdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica
        )
    )
    page = pdf.add_blank_page(page_size=(200, 120))
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    page.Contents = pdf.make_stream(content)
    return pdf


def _render(pdf):
    import pypdfium2

    buf = io.BytesIO()
    pdf.save(buf)
    doc = pypdfium2.PdfDocument(buf.getvalue())
    try:
        return np.asarray(doc[0].render(scale=2).to_pil().convert("RGB"))
    finally:
        doc.close()


RICH = (
    b"q 0.50 0 0 0.50 10.0 10.00 cm 1 0 0.0 rg 0 0 100.00 50.0 re f Q\n"
    b"BT /F1 14.000 Tf 20 60.0 Td [ (Kern) -250.000 (ing) 120.0 (\\(x\\)) ] TJ ET\n"
    b"/Span << /ActualText (hi) >> BDC BT /F1 9 Tf 20 20 Td (tag#1) Tj ET EMC\n"
    b"q 30 0 0 20 150 80 cm BI /W 2 /H 2 /BPC 8 /CS /G ID \x00\xff\xff\x00 EI Q\n"
)


def test_compacted_page_renders_identically():
    pdf = _page_pdf(RICH)
    before = _render(pdf)
    data = cc.compact_stream(RICH)
    assert data is not None and len(data) < len(RICH)
    pdf.pages[0].Contents = pdf.make_stream(data)
    assert (_render(pdf) == before).all()


def test_significant_tokens():
    assert cc.significant_tokens(b"1 0 0 1 .5 0 cm % note\n[1 2]0 d") == 13
    assert cc.significant_tokens(b"BI /W 1 /H 1 /BPC 8 /CS /G ID \x10 EI") == 12
    assert cc.significant_tokens(b"q (unterminated") is None
    assert cc.significant_tokens(b"<zz> Tj") is None


_SCRATCH = pikepdf.new()


def _parse(data: bytes):
    return pikepdf.parse_content_stream(_SCRATCH.make_stream(data))


@pytest.mark.parametrize(
    "data,tokens",
    [
        (b"q 1 0 0 1 0 0 cm Q", 9),
        (b"[1 [2 3]] 0 d", 9),
        (b"/Tag << /MCID 3 /K [1] >> BDC", 10),
        (b"BI /W 1 /H 1 /BPC 8 /CS /G ID \x10 EI", 12),
    ],
)
def test_instruction_tokens(data, tokens):
    assert cc.instruction_tokens(_parse(data)) == tokens


@pytest.mark.parametrize(
    "data,complete",
    [
        (b"q 1 0 0 1 0 0 cm Q", True),
        (b"q 1 2", False),  # trailing operands the parser drops
        (b"q [1 2", False),  # an unclosed array: dropped likewise
        (b"q (unterminated", False),  # malformed
    ],
)
def test_parsed_completely(data, complete):
    assert cc.parsed_completely(data, _parse(data)) is complete
