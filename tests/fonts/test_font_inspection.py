import pikepdf
from pikepdf import Dictionary, Name

from pdftl.fonts.font_inspection import (
    inspect_pdf_fonts,
    list_fonts,
    missing_fonts,
    _check_embedding,
    _process_single_font,
)


def test_inspect_pdf_fonts_sees_inherited_resources():
    """Fails on current code: a page whose /Resources is only inherited
    from a /Pages ancestor (not its own dict) is invisible to
    _process_page_fonts, so inspect_pdf_fonts/list_fonts/missing_fonts
    silently report zero fonts for it."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    parent = page.obj["/Parent"]

    font = Dictionary(Subtype=Name.Type1, BaseFont=Name("/NotEmbeddedFont"))
    parent[Name.Resources] = Dictionary(Font=Dictionary(F1=font))
    if Name.Resources in page.obj:
        del page.obj[Name.Resources]

    registry = inspect_pdf_fonts(pdf)
    assert "/NotEmbeddedFont" in registry

    missing = missing_fonts(pdf)
    assert any(m["font_name"] == "NotEmbeddedFont" for m in missing)


# ---------------------------------------------------------------------------
# list_fonts
# ---------------------------------------------------------------------------


def test_list_fonts_reports_embedded_and_non_embedded():
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))

    embedded = Dictionary(
        Subtype=Name.Type1,
        BaseFont=Name("/EmbeddedFont"),
        FontDescriptor=Dictionary(FontFile=pdf.make_stream(b"fake")),
    )
    not_embedded = Dictionary(Subtype=Name.Type1, BaseFont=Name("/PlainFont"))
    page.Resources = Dictionary(Font=Dictionary(F1=embedded, F2=not_embedded))

    results = list_fonts(pdf)
    by_name = {r["font_name"]: r for r in results}

    assert by_name["EmbeddedFont"]["is_embedded"] is True
    assert by_name["EmbeddedFont"]["subtype"] == "Type1"
    assert by_name["EmbeddedFont"]["pages"] == [1]
    assert by_name["PlainFont"]["is_embedded"] is False


def test_list_fonts_tracks_font_across_multiple_pages():
    """The same BaseFont used on two pages should collect both page numbers
    under one registry entry, not create duplicate entries."""
    pdf = pikepdf.new()
    shared_font = pdf.make_indirect(Dictionary(Subtype=Name.Type1, BaseFont=Name("/Shared")))
    page1 = pdf.add_blank_page(page_size=(200, 200))
    page1.Resources = Dictionary(Font=Dictionary(F1=shared_font))
    page2 = pdf.add_blank_page(page_size=(200, 200))
    page2.Resources = Dictionary(Font=Dictionary(F1=shared_font))

    results = list_fonts(pdf)
    matches = [r for r in results if r["font_name"] == "Shared"]
    assert len(matches) == 1
    assert matches[0]["pages"] == [1, 2]


def test_missing_fonts_skips_standard_14():
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    page.Resources = Dictionary(
        Font=Dictionary(F1=Dictionary(Subtype=Name.Type1, BaseFont=Name("/Helvetica")))
    )
    assert missing_fonts(pdf) == []


# ---------------------------------------------------------------------------
# _check_embedding
# ---------------------------------------------------------------------------


def test_check_embedding_type3_always_true():
    font = Dictionary(Subtype=Name.Type3)
    assert _check_embedding(font) is True


def test_check_embedding_via_font_descriptor_fontfile2():
    pdf = pikepdf.new()
    font = Dictionary(
        Subtype=Name.TrueType,
        FontDescriptor=Dictionary(FontFile2=pdf.make_stream(b"fake")),
    )
    assert _check_embedding(font) is True


def test_check_embedding_font_descriptor_without_file_is_false():
    font = Dictionary(Subtype=Name.Type1, FontDescriptor=Dictionary())
    assert _check_embedding(font) is False


def test_check_embedding_type0_recurses_into_descendant():
    pdf = pikepdf.new()
    descendant = Dictionary(
        Subtype=Name("/CIDFontType2"),
        FontDescriptor=Dictionary(FontFile2=pdf.make_stream(b"fake")),
    )
    font = Dictionary(Subtype=Name.Type0, DescendantFonts=pikepdf.Array([descendant]))
    assert _check_embedding(font) is True


def test_check_embedding_no_descriptor_no_descendants_is_false():
    font = Dictionary(Subtype=Name.Type1, BaseFont=Name("/Arial"))
    assert _check_embedding(font) is False


# ---------------------------------------------------------------------------
# _process_single_font
# ---------------------------------------------------------------------------


def test_process_single_font_skips_non_dict_like():
    registry = {}
    _process_single_font(1, "not dict-like", registry)
    assert registry == {}


def test_process_single_font_skips_missing_basefont():
    registry = {}
    font = Dictionary(Subtype=Name.Type1)
    _process_single_font(1, font, registry)
    assert registry == {}


def test_check_embedding_type0_empty_descendants_is_false():
    """A Type0 font with a /DescendantFonts array present but empty must
    fall through to False, not recurse (len(descendants) > 0 is False)."""
    font = Dictionary(Subtype=Name.Type0, DescendantFonts=pikepdf.Array([]))
    assert _check_embedding(font) is False


def test_process_single_font_same_page_twice_does_not_duplicate():
    """Calling _process_single_font twice for the same font on the same
    page_idx must not append a duplicate page number."""
    registry = {}
    font = Dictionary(Subtype=Name.Type1, BaseFont=Name("/Dup"))
    _process_single_font(1, font, registry)
    _process_single_font(1, font, registry)
    assert registry["/Dup"].pages == [1]


def test_process_page_fonts_no_font_key_is_noop():
    """A page with a /Resources dict that has no /Font entry at all must
    be a no-op, not raise."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    page.Resources = Dictionary(XObject=Dictionary())

    registry = inspect_pdf_fonts(pdf)
    assert registry == {}
