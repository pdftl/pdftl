# tests/visual/test_visual_style_text_split_stream.py
import re
import pikepdf
import pdftl.api


def _make_split_stream_pdf() -> pikepdf.Pdf:
    """
    Construct a PDF whose page /Contents splits a TJ operator across a stream
    boundary: the operand array [ ... ] ends stream 1, and the TJ operator
    begins stream 2. This is valid per the PDF spec (renderers must concatenate
    Contents streams before parsing) but pikepdf's parse_content_stream, called
    per-stream, silently drops the trailing operand array as incomplete,
    producing a bare TJ in stream 2 with no operands.
    """
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(400, 300))
    page = pdf.pages[0]

    # Stream 1: ends with a complete [ ... ] array but NO TJ operator
    stream1 = pdf.make_stream(
        b"BT "
        b"/F1 24 Tf "
        b"0.8 0.1 0.1 rg "
        b"50 240 Td "
        b"(Hello, split stream!) Tj "
        b"ET "
        b"BT "
        b"/F1 18 Tf "
        b"0.1 0.1 0.8 rg "
        b"50 160 Td "
        b"[ (SECOND) -250 (LINE) ] "
        # intentionally NO TJ here — operator is in stream 2
    )

    # Stream 2: starts with the bare TJ operator for the array above,
    # then has more content that poppler will stop rendering if TJ is malformed
    stream2 = pdf.make_stream(
        b"TJ ET BT /F1 18 Tf 0.1 0.8 0.1 rg 50 80 Td (THIRD LINE MUST APPEAR) Tj ET"
    )

    font = pikepdf.Dictionary(
        Type=pikepdf.Name("/Font"),
        Subtype=pikepdf.Name("/Type1"),
        BaseFont=pikepdf.Name("/Helvetica"),
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    page.Contents = pikepdf.Array([stream1, stream2])
    return pdf


def test_visual_style_text_split_streams(assert_pdf_match):
    """
    Regression test for split-stream TJ operand loss bug.

    When a page /Contents is an array of streams and a TJ operand array [ ... ]
    ends one stream while the TJ operator begins the next, pikepdf's
    parse_content_stream silently drops the pending operands at end-of-stream.
    style_text then writes back the truncated stream, producing a bare TJ with
    no operands — which poppler/evince treat as a fatal content stream error and
    stop rendering the page at that point (pdfium is more lenient and skips the
    bare TJ, so line 2 disappears but line 3 still renders there).

    The fix is contents_coalesce() before parsing, which is what the PDF spec
    requires of readers: treat a Contents array as a single concatenated stream.
    """
    pdf = _make_split_stream_pdf()
    assert_pdf_match(pdf, suffix="before")

    result = pdftl.api.style_text(
        pdf=pdf,
        operation_args=["fill_color=0 0 0"],
    )
    assert_pdf_match(result, suffix="after")

    # After the fix, contents_coalesce() replaces the array with a single
    # stream, so the split-stream problem cannot recur.
    page = result.pages[0]
    contents = page.get("/Contents")
    assert not isinstance(contents, pikepdf.Array), (
        "Contents should be a single coalesced stream after style_text, not an array"
    )

    # The single stream must not start with a bare TJ/Tj — that would indicate
    # the operand array was lost during parsing.
    raw = contents.read_bytes()
    assert not re.match(rb"^\s*TJ", raw), "Stream starts with bare TJ — operand array was lost"
    assert not re.match(rb"^\s*Tj", raw), "Stream starts with bare Tj — operand was lost"

    # All three text runs must be present in the coalesced stream.
    assert b"Hello, split stream!" in raw, "Line 1 lost"
    assert b"SECOND" in raw, "Line 2 (the split TJ) lost"
    assert b"THIRD LINE MUST APPEAR" in raw, "Line 3 lost"
