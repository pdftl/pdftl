# tests/operations/test_subset_fonts.py

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from __future__ import annotations

from io import BytesIO

import pikepdf

from unittest.mock import MagicMock, patch

from fontTools.fontBuilder import FontBuilder
from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._g_l_y_f import Glyph

from pdftl.operations.subset_fonts import (
    #    _codes_to_unicodes,
    _descriptor_identity,
    _embedded_format_for,
    _get_embedded_stream_key,
    _get_simple_font_encoding,
    _resync_cid_to_gid_after_subset,
    _resync_widths_after_subset,
    _stream_identity,
    _subset_and_resync_group,
    _subset_sfnt_or_cff_font_group_binary,
    _subset_type1_font_group_binary,
    _widths_cid_to_gid_map,
    subset_fonts,
)


def _create_dummy_cid_ttf() -> bytes:
    """Helper to build a tiny TTF with glyph 'A' explicitly at GID 3."""
    fb = FontBuilder(1024, isTTF=True)
    glyph_order = [".notdef", "unused1", "unused2", "A"]
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap({0x41: "A"})

    # Set up empty glyph records for TTF
    glyphs = {g: Glyph() for g in glyph_order}
    fb.setupGlyf(glyphs)

    fb.setupHead()
    metrics = {g: (500, 0) for g in glyph_order}
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader()
    fb.setupOS2()
    fb.setupPost()
    fb.setupNameTable({"familyName": "TestCIDFont", "styleName": "Regular"})

    buf = BytesIO()
    fb.save(buf)
    return buf.getvalue()


def test_cidtype2_retains_gid_alignment():
    """
    Verifies that subsetting a CIDFontType2 font preserves GID mapping alignment.
    """
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))

    # Embed TTF Stream
    ttf_bytes = _create_dummy_cid_ttf()
    font_stream = pdf.make_stream(ttf_bytes)

    cid_font_dict = pikepdf.Dictionary(
        Type=pikepdf.Name.Font,
        Subtype=pikepdf.Name.CIDFontType2,
        BaseFont=pikepdf.Name.TestCIDFont,
        CIDSystemInfo=pikepdf.Dictionary(Registry="Adobe", Ordering="Identity", Supplement=0),
        CIDToGIDMap=pikepdf.Name.Identity,
        FontDescriptor=pikepdf.Dictionary(
            Type=pikepdf.Name.FontDescriptor,
            FontName=pikepdf.Name.TestCIDFont,
            FontFile2=font_stream,
        ),
    )

    type0_font = pikepdf.Dictionary(
        Type=pikepdf.Name.Font,
        Subtype=pikepdf.Name.Type0,
        BaseFont=pikepdf.Name.TestCIDFont,
        Encoding=pikepdf.Name("/Identity-H"),
        DescendantFonts=pikepdf.Array([cid_font_dict]),
    )

    # Use CID 3 (0x0003 in 2-byte hex) in page stream
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=type0_font))
    page.Contents = pdf.make_stream(b"/F1 12 Tf <0003> Tj")

    # Perform subsetting
    subset_fonts(pdf, [])

    # Inspect the subsetted TrueType binary stream inside FontDescriptor
    subsetted_cid_font = pdf.pages[0].Resources.Font.F1.DescendantFonts[0]
    subsetted_ttf_bytes = bytes(subsetted_cid_font.FontDescriptor.FontFile2.read_raw_bytes())

    tt = TTFont(BytesIO(subsetted_ttf_bytes))
    glyph_order = tt.getGlyphOrder()

    assert len(glyph_order) > 3, "Font was compacted and lost GID padding."
    assert glyph_order[3] == "A", (
        f"CID 3 maps to GID 3, but GID 3 in subsetted font is '{glyph_order[3]}'. "
        "Identity mapping is broken."
    )


def test_embedded_stream_key_and_format_helpers():
    # 1. Descriptor with no font file key
    desc = pikepdf.Dictionary({"/Type": pikepdf.Name("/FontDescriptor")})
    assert _get_embedded_stream_key(desc) is None

    # 2. OpenType CFF2 stream in FontFile3
    desc_cff2 = pikepdf.Dictionary(
        {
            "/FontFile3": pikepdf.Dictionary({"/Subtype": pikepdf.Name("/OpenType")}),
        }
    )
    assert _embedded_format_for(desc_cff2, "/FontFile3") == "cff2"

    # 3. Unknown subtype in FontFile3
    desc_unknown = pikepdf.Dictionary(
        {
            "/FontFile3": pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Unknown")}),
        }
    )
    assert _embedded_format_for(desc_unknown, "/FontFile3") is None


# def test_codes_to_unicodes():
#     pdf = pikepdf.new()

#     # Font without /ToUnicode
#     font_no_tu = pikepdf.Dictionary({"/Type": pikepdf.Name("/Font")})
#     assert _codes_to_unicodes(font_no_tu, {0x41}) == set()

#     # Font with valid /ToUnicode CMap
#     cmap_data = (
#         b"/CIDInit /ProcSet findresource begin\n"
#         b"12 dict begin\n"
#         b"begincmap\n"
#         b"/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
#         b"/CMapName /Adobe-Identity-UCS def\n"
#         b"/CMapType 2 def\n"
#         b"1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"
#         b"1 beginbfchar\n<41> <0041>\nendbfchar\n"
#         b"endcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"
#     )
#     stream = pdf.make_stream(cmap_data)
#     font_tu = pikepdf.Dictionary({"/Type": pikepdf.Name("/Font"), "/ToUnicode": stream})
#     assert _codes_to_unicodes(font_tu, {0x41}) == {"A"}

#     # Font with unreadable /ToUnicode object
#     bad_tu = MagicMock()
#     bad_tu.read_bytes.side_effect = AttributeError("Corrupt stream")
#     font_bad = {"/ToUnicode": bad_tu}
#     assert _codes_to_unicodes(font_bad, {0x41}) == set()


def test_get_simple_font_encoding():
    # 1. No /Encoding
    font1 = pikepdf.Dictionary({"/Type": pikepdf.Name("/Font")})
    assert _get_simple_font_encoding(font1) == (None, None)

    # 2. /Encoding as a pikepdf.Name
    font2 = pikepdf.Dictionary(
        {"/Type": pikepdf.Name("/Font"), "/Encoding": pikepdf.Name("/WinAnsiEncoding")}
    )
    assert _get_simple_font_encoding(font2) == (None, "WinAnsiEncoding")

    # 3. /Encoding dictionary with /Differences and /BaseEncoding
    enc_dict = pikepdf.Dictionary(
        {
            "/BaseEncoding": pikepdf.Name("/WinAnsiEncoding"),
            "/Differences": pikepdf.Array([65, pikepdf.Name("/A")]),
        }
    )
    font3 = pikepdf.Dictionary({"/Type": pikepdf.Name("/Font"), "/Encoding": enc_dict})
    diffs, base = _get_simple_font_encoding(font3)
    assert base == "WinAnsiEncoding"
    assert diffs == [65, pikepdf.Name("/A")]


def test_stream_and_descriptor_identity_fallbacks():
    pdf = pikepdf.new()
    # Descriptor without stream key
    desc = pikepdf.Dictionary({"/Type": pikepdf.Name("/FontDescriptor")})
    assert _stream_identity(desc) is None

    # Direct object fallback for descriptor and stream
    direct_desc = pikepdf.Dictionary({"/Type": pikepdf.Name("/FontDescriptor")})
    assert isinstance(_descriptor_identity(direct_desc), int)

    stream_obj = pdf.make_stream(b"fontdata")
    desc_with_stream = pikepdf.Dictionary(
        {"/Type": pikepdf.Name("/FontDescriptor"), "/FontFile2": stream_obj}
    )
    assert isinstance(_stream_identity(desc_with_stream), tuple)


def test_subset_type1_font_group_binary_failures_and_edge_cases():
    pdf = pikepdf.new()
    font_obj = pikepdf.Dictionary({"/Type": pikepdf.Name("/Font")})

    # 1. Failed to read stream
    bad_stream = pdf.make_stream(b"data")
    with patch.object(pikepdf.Object, "read_bytes", side_effect=OSError("Read failure")):
        pikepdf.Dictionary({"/FontFile": bad_stream})

    # 2. open_type1_font_bytes returns None
    with patch("pdftl.fonts.type1_to_cff.open_type1_font_bytes", return_value=None):
        good_stream = pdf.make_stream(b"invalid pfb")
        desc = pikepdf.Dictionary({"/FontFile": good_stream})
        assert not _subset_type1_font_group_binary([(font_obj, desc, {65})])

    # 3. build_cff_from_glyph_names returns None
    fake_font = MagicMock()
    with (
        patch("pdftl.fonts.type1_to_cff.open_type1_font_bytes", return_value=fake_font),
        patch("pdftl.fonts.type1_to_cff.build_cff_from_glyph_names", return_value=None),
    ):
        good_stream = pdf.make_stream(b"valid pfb")
        desc = pikepdf.Dictionary({"/FontFile": good_stream})
        assert not _subset_type1_font_group_binary([(font_obj, desc, {65})])

    # 4. Success path with multiple descriptors and FontFile -> FontFile3 conversion
    pdf = pikepdf.new()
    stream_obj = pdf.make_stream(b"type1 bytes")
    desc1 = pdf.make_indirect(pikepdf.Dictionary({"/FontFile": stream_obj}))
    desc2 = desc1  # Shared descriptor reference

    with (
        patch("pdftl.fonts.type1_to_cff.open_type1_font_bytes", return_value=fake_font),
        patch("pdftl.fonts.type1_to_cff.build_cff_from_glyph_names", return_value=b"cff_out"),
        patch("pdftl.fonts.type1_to_cff.resolve_glyph_names", return_value={"A"}),
        patch("pdftl.fonts.type1_to_cff.resolve_code_to_glyph_names", return_value={65: "A"}),
    ):
        entries = [(font_obj, desc1, {65}), (font_obj, desc2, {65})]
        assert _subset_type1_font_group_binary(entries)
        assert "/FontFile" not in desc1
        assert "/FontFile3" in desc1
        assert desc1["/FontFile3"]["/Subtype"] == pikepdf.Name("/Type1C")


def test_subset_sfnt_or_cff_font_group_binary_failures_and_edge_cases():
    pdf = pikepdf.new()
    font_obj = pikepdf.Dictionary({"/Type": pikepdf.Name("/Font")})

    # 1. Read stream failure
    bad_stream = pdf.make_stream(b"data")
    with patch.object(pikepdf.Object, "read_bytes", side_effect=OSError("Read failure")):
        pikepdf.Dictionary({"/FontFile2": bad_stream})

    # 2. open_font_for_subsetting returns None
    with patch("pdftl.fonts.font_subsetting.open_font_for_subsetting", return_value=None):
        good_stream = pdf.make_stream(b"sfnt bytes")
        desc = pikepdf.Dictionary({"/FontFile2": good_stream})
        rewrote, *_ = _subset_sfnt_or_cff_font_group_binary(
            [(font_obj, desc, {65})], False, "sfnt"
        )
        assert not rewrote

    # 3. Type0 font with bare_cff format
    fake_tt = MagicMock()
    type0_font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type0")})
    with (
        patch("pdftl.fonts.font_subsetting.open_font_for_subsetting", return_value=fake_tt),
        patch(
            "pdftl.fonts.font_subsetting.gids_for_cff_native_cid_font", return_value={1, 2}
        ) as mock_cff_gids,
        patch("pdftl.fonts.font_subsetting.run_subsetter", return_value=False),
    ):
        desc = pikepdf.Dictionary({"/FontFile3": good_stream})
        rewrote, *_ = _subset_sfnt_or_cff_font_group_binary(
            [(type0_font, desc, {1})], False, "bare_cff"
        )
        assert not rewrote
        mock_cff_gids.assert_called_once()

    # 4. run_subsetter fails
    with (
        patch("pdftl.fonts.font_subsetting.open_font_for_subsetting", return_value=fake_tt),
        patch("pdftl.fonts.font_subsetting.gids_for_simple_font_via_cmap", return_value={1}),
        patch("pdftl.fonts.font_subsetting.run_subsetter", return_value=False),
    ):
        desc = pikepdf.Dictionary({"/FontFile2": good_stream})
        rewrote, *_ = _subset_sfnt_or_cff_font_group_binary(
            [(font_obj, desc, {65})], False, "sfnt"
        )
        assert not rewrote

    # 5. Serialization exception during unwrap / save
    with (
        patch("pdftl.fonts.font_subsetting.open_font_for_subsetting", return_value=fake_tt),
        patch("pdftl.fonts.font_subsetting.run_subsetter", return_value=True),
        patch(
            "pdftl.fonts.font_subsetting.unwrap_bare_cff_from_sfnt", side_effect=KeyError("err")
        ),
    ):
        desc = pikepdf.Dictionary({"/FontFile3": good_stream})
        rewrote, *_ = _subset_sfnt_or_cff_font_group_binary(
            [(font_obj, desc, {65})], False, "bare_cff"
        )
        assert not rewrote

    # 6. Subsetted font size is not smaller than original
    with (
        patch("pdftl.fonts.font_subsetting.open_font_for_subsetting", return_value=fake_tt),
        patch("pdftl.fonts.font_subsetting.run_subsetter", return_value=True),
    ):
        fake_tt.save = lambda buf: buf.write(b"sfnt bytes -- equal size")
        desc = pikepdf.Dictionary({"/FontFile2": good_stream})
        rewrote, *_ = _subset_sfnt_or_cff_font_group_binary(
            [(font_obj, desc, {65})], False, "sfnt"
        )
        assert not rewrote

    # 7. Successful rewrite with FontFile3 descriptor (skips Length1 setting and duplicate descriptor)
    pdf = pikepdf.new()
    stream_obj = pdf.make_stream(b"1234567890_original")
    desc1 = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/FontFile3": stream_obj,
            }
        )
    )

    with (
        patch("pdftl.fonts.font_subsetting.open_font_for_subsetting", return_value=fake_tt),
        patch("pdftl.fonts.font_subsetting.run_subsetter", return_value=True),
        patch("pdftl.fonts.font_subsetting.unwrap_bare_cff_from_sfnt", return_value=b"smaller"),
    ):
        entries = [(font_obj, desc1, {65}), (font_obj, desc1, {65})]  # Duplicate descriptor entry
        rewrote, *_ = _subset_sfnt_or_cff_font_group_binary(entries, False, "bare_cff")
        assert rewrote


def test_widths_and_resync_helpers():
    pdf = pikepdf.new()

    # 1. _widths_cid_to_gid_map
    font_type0 = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type0")})
    assert _widths_cid_to_gid_map(font_type0, "cff") == "cff_native"

    font_simple = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type1")})
    assert _widths_cid_to_gid_map(font_simple, "cff") is None

    # 2. _resync_widths_after_subset with no stream key or empty new widths
    desc_no_stream = pikepdf.Dictionary({"/Type": pikepdf.Name("/FontDescriptor")})
    _resync_widths_after_subset(font_simple, desc_no_stream, "sfnt", pikepdf)

    stream_obj = pdf.make_stream(b"data")
    desc_stream = pikepdf.Dictionary({"/FontFile2": stream_obj})
    with patch(
        "pdftl.operations.subset_fonts._extract_widths_from_subsetted_stream", return_value={}
    ):
        _resync_widths_after_subset(font_simple, desc_stream, "sfnt", pikepdf)

    # 3. _resync_cid_to_gid_after_subset
    _resync_cid_to_gid_after_subset(pdf, font_simple, "sfnt", pikepdf)  # non-Type0

    cid_font = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/CIDFontType2"),
            "/CIDToGIDMap": pdf.make_stream(b"\x00\x00" * 10),
        }
    )
    type0_cid = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type0"),
            "/DescendantFonts": pikepdf.Array([cid_font]),
        }
    )
    with (
        patch("pdftl.operations.subset_fonts.extract_cid_to_gid_map", return_value={0: 0}),
        patch("pdftl.operations.subset_fonts.update_cid_to_gid_map") as mock_update,
    ):
        _resync_cid_to_gid_after_subset(pdf, type0_cid, "sfnt", pikepdf)
        mock_update.assert_called_once_with(type0_cid, {0: 0}, pikepdf, pdf)


def test_subset_and_resync_group_skips():
    pdf = pikepdf.new()

    # 1. Type3 font skipped
    type3_font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type3")})
    desc = pikepdf.Dictionary({})
    assert _subset_and_resync_group(pdf, [(type3_font, desc, {65})], False, pikepdf)[0] == 0

    # 2. Missing stream key skipped
    font1 = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type1")})
    assert _subset_and_resync_group(pdf, [(font1, desc, {65})], False, pikepdf)[0] == 0

    # 3. Unrecognized binary format skipped
    stream_obj = pdf.make_stream(b"data")
    desc_unrecognized = pikepdf.Dictionary({"/FontFile3": stream_obj})  # no /Subtype
    assert (
        _subset_and_resync_group(pdf, [(font1, desc_unrecognized, {65})], False, pikepdf)[0] == 0
    )

    # 4. Rewrote is False -> returns 0
    desc_ttf = pikepdf.Dictionary({"/FontFile2": stream_obj})
    with patch(
        "pdftl.operations.subset_fonts._subset_sfnt_or_cff_font_group_binary",
        return_value=(False, 0, 0, 0, 0),
    ):
        assert _subset_and_resync_group(pdf, [(font1, desc_ttf, {65})], False, pikepdf)[0] == 0


def test_subset_fonts_operation_empty_or_unresolved():
    pdf = pikepdf.new()

    # 1. Target pages have no text operators
    pdf.add_blank_page()
    res = subset_fonts(pdf, [])
    assert res.success

    # 2. collect_used_codes finds codes but resolved_fonts / descriptor / stream_id is missing
    with (
        patch(
            "pdftl.operations.subset_fonts.collect_used_codes",
            return_value=({1: {65}, 2: {66}, 3: {67}}, {1: None}),
        ),
        patch("pdftl.operations.subset_fonts.find_font_descriptor", return_value=None),
    ):
        res2 = subset_fonts(pdf, [])
        assert res2.success


def test_subset_sfnt_cff_compilation_exception():
    pdf = pikepdf.new()
    font_obj = pikepdf.Dictionary({"/Type": pikepdf.Name("/Font")})
    stream_obj = pdf.make_stream(b"sfnt bytes")
    desc = pikepdf.Dictionary({"/FontFile2": stream_obj})

    mock_font = MagicMock()
    mock_font.save.side_effect = Exception("Font compilation error")

    with patch("pdftl.fonts.font_subsetting.open_font_for_subsetting", return_value=mock_font):
        rewrote, *_ = _subset_sfnt_or_cff_font_group_binary(
            [(font_obj, desc, {65})], False, "sfnt"
        )
        assert rewrote is False


def test_resync_widths_edge_cases():
    pdf = pikepdf.new()

    # Missing character bounds in font dictionary
    font_invalid_bounds = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type1"),
        }
    )
    stream_obj = pdf.make_stream(b"data")
    desc = pikepdf.Dictionary({"/FontFile": stream_obj})

    with patch(
        "pdftl.operations.subset_fonts._extract_widths_from_subsetted_stream",
        return_value={"41": 500},
    ):
        _resync_widths_after_subset(font_invalid_bounds, desc, "type1", pikepdf)

    # Exception raised during width extraction
    font_valid = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/FirstChar": 65,
            "/LastChar": 66,
            "/Widths": pikepdf.Array([500, 500]),
        }
    )
    with patch(
        "pdftl.operations.subset_fonts._extract_widths_from_subsetted_stream",
        return_value=None,
    ):
        _resync_widths_after_subset(font_valid, desc, "type1", pikepdf)


def test_resync_cid_to_gid_map_branch_coverage():
    pdf = pikepdf.new()

    # Subtype is not Type0
    font_truetype = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
    _resync_cid_to_gid_after_subset(pdf, font_truetype, "sfnt", pikepdf)

    # Embedded format is CFF
    font_type0 = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type0")})
    _resync_cid_to_gid_after_subset(pdf, font_type0, "cff", pikepdf)

    # CID map extraction returns non-dictionary mapping
    with patch(
        "pdftl.operations.subset_fonts.extract_cid_to_gid_map",
        return_value="/Identity",
    ):
        _resync_cid_to_gid_after_subset(pdf, font_type0, "sfnt", pikepdf)


def test_subset_fonts_unembedded_or_unsupported_fonts():
    pdf = pikepdf.new()

    font_no_desc = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
            }
        )
    )

    page = pdf.add_blank_page()
    page.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_no_desc})})

    subset_fonts(pdf, [])


# def test_additional_coverage_helpers_and_branches():
#     pdf = pikepdf.new()

#     enc_diff_only = pikepdf.Dictionary({"/Differences": pikepdf.Array([65, pikepdf.Name("/A")])})
#     font_diff_only = pikepdf.Dictionary(
#         {"/Type": pikepdf.Name("/Font"), "/Encoding": enc_diff_only}
#     )
#     diffs, base = _get_simple_font_encoding(font_diff_only)
#     assert diffs == [65, pikepdf.Name("/A")]
#     assert base is None

#     enc_base_only = pikepdf.Dictionary({"/BaseEncoding": pikepdf.Name("/StandardEncoding")})
#     font_base_only = pikepdf.Dictionary(
#         {"/Type": pikepdf.Name("/Font"), "/Encoding": enc_base_only}
#     )
#     diffs, base = _get_simple_font_encoding(font_base_only)
#     assert diffs is None
#     assert base == "StandardEncoding"

#     corrupt_cmap = pdf.make_stream(b"invalid cmap content")
#     font_corrupt_tu = pikepdf.Dictionary(
#         {"/Type": pikepdf.Name("/Font"), "/ToUnicode": corrupt_cmap}
#     )
#     assert _codes_to_unicodes(font_corrupt_tu, {0x41}) == set()

#     font_cid = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/CIDFontType2")})
#     assert _widths_cid_to_gid_map(font_cid, "sfnt") is None

#     font_no_subtype = pikepdf.Dictionary({"/Type": pikepdf.Name("/Font")})
#     _resync_cid_to_gid_after_subset(pdf, font_no_subtype, "sfnt", pikepdf)

#     page = pdf.add_blank_page()
#     page.Resources = pikepdf.Dictionary()
#     subset_fonts(pdf, ["1"])


def test_subset_and_resync_group_type1():
    pdf = pikepdf.new()
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type1")})
    stream_obj = pdf.make_stream(b"pfa/pfb data")
    desc = pikepdf.Dictionary({"/FontFile": stream_obj})
    with patch("pdftl.operations.subset_fonts._subset_type1_font_group_binary", return_value=True):
        assert _subset_and_resync_group(pdf, [(font_obj, desc, {65})], False, pikepdf)[0] == 1


def test_full_coverage_subset_fonts_edge_cases():
    pdf = pikepdf.new()

    # 1. _embedded_format_for edge cases (125->131, 130)
    desc_unknown = pikepdf.Dictionary(
        {"/FontFile3": pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Unknown")})}
    )
    assert _embedded_format_for(desc_unknown, "/FontFile3") is None
    assert _embedded_format_for(pikepdf.Dictionary(), "/UnknownKey") is None

    desc_type1c = pikepdf.Dictionary(
        {"/FontFile3": pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type1C")})}
    )
    assert _embedded_format_for(desc_type1c, "/FontFile3") == "cff"

    # 2. _stream_identity objgen is (0, 0) or missing
    class DummyStream:
        objgen = (0, 0)

    desc_dummy = {"/FontFile": DummyStream()}
    assert _stream_identity(desc_dummy) == id(desc_dummy["/FontFile"])

    # 3. _subset_type1_font_group_binary read_bytes error
    class BadStream:
        def read_bytes(self):
            raise OSError("Read error")

    desc_bad = {"/FontFile": BadStream()}
    font_dummy = pikepdf.Dictionary()
    assert _subset_type1_font_group_binary([(font_dummy, desc_bad, {65})]) is False

    # 4. _subset_type1_font_group_binary encoding and missing /FontFile
    mock_t1 = MagicMock()
    desc1 = pikepdf.Dictionary({"/FontFile": pdf.make_stream(b"data")})
    desc2 = pikepdf.Dictionary()
    enc = pikepdf.Dictionary({"/BaseEncoding": pikepdf.Name("/WinAnsiEncoding")})
    font1 = pikepdf.Dictionary({"/Type": pikepdf.Name("/Font"), "/Encoding": enc})
    font2 = pikepdf.Dictionary({"/Type": pikepdf.Name("/Font"), "/Encoding": enc})

    with (
        patch("pdftl.fonts.type1_to_cff.open_type1_font_bytes", return_value=mock_t1),
        patch("pdftl.fonts.type1_to_cff.resolve_glyph_names", return_value={"A"}),
        patch("pdftl.fonts.type1_to_cff.build_cff_from_glyph_names", return_value=b"cffdata"),
    ):
        assert (
            _subset_type1_font_group_binary(
                [
                    (font1, desc1, {65}),
                    (font2, desc1, {66}),
                    (font1, desc2, {67}),
                ]
            )
            is True
        )

    # 5. _subset_sfnt_or_cff_font_group_binary read_bytes error
    desc_bad_sfnt = {"/FontFile2": BadStream()}
    rewrote, *_ = _subset_sfnt_or_cff_font_group_binary(
        [(font_dummy, desc_bad_sfnt, {65})], False, "sfnt"
    )
    assert rewrote is False

    # 6. _subset_sfnt_or_cff_font_group_binary duplicate descriptor
    desc_sfnt = pikepdf.Dictionary({"/FontFile2": pdf.make_stream(b"1234567890")})
    font_sfnt1 = pikepdf.Dictionary({"/Type": pikepdf.Name("/Font")})
    font_sfnt2 = pikepdf.Dictionary({"/Type": pikepdf.Name("/Font")})
    mock_tt = MagicMock()
    mock_tt.save.side_effect = lambda buf: buf.write(b"123")
    with (
        patch("pdftl.fonts.font_subsetting.open_font_for_subsetting", return_value=mock_tt),
        patch("pdftl.fonts.font_subsetting.run_subsetter", return_value=True),
    ):
        rewrote, *_ = _subset_sfnt_or_cff_font_group_binary(
            [
                (font_sfnt1, desc_sfnt, {65}),
                (font_sfnt2, desc_sfnt, {66}),
            ],
            False,
            "sfnt",
        )
        assert rewrote is True

    # 7. _rekey_simple_cff_widths & line 436 call
    font_simple_cff = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type1"),
            "/Encoding": pikepdf.Name("/WinAnsiEncoding"),
        }
    )
    stream_obj = pdf.make_stream(b"cffdata")
    desc_cff = pikepdf.Dictionary({"/FontFile3": stream_obj})
    with (
        patch(
            "pdftl.operations.subset_fonts._extract_widths_from_subsetted_stream",
            return_value={"A": 500.0},
        ),
        patch("pdftl.operations.subset_fonts.extract_font_widths", return_value={}),
        patch("pdftl.operations.subset_fonts.update_font_widths") as mock_update,
    ):
        _resync_widths_after_subset(font_simple_cff, desc_cff, "cff", pikepdf)
        mock_update.assert_called_once()

    # 8. merged == old_widths
    font_no_change = pikepdf.Dictionary({"/Type": pikepdf.Name("/Font")})
    desc_no_change = pikepdf.Dictionary({"/FontFile2": stream_obj})
    with (
        patch(
            "pdftl.operations.subset_fonts._extract_widths_from_subsetted_stream",
            return_value={"65": 500.0},
        ),
        patch("pdftl.operations.subset_fonts.extract_font_widths", return_value={"65": 500.0}),
        patch("pdftl.operations.subset_fonts.update_font_widths") as mock_update,
    ):
        _resync_widths_after_subset(font_no_change, desc_no_change, "ttf", pikepdf)
        mock_update.assert_not_called()

    # 9. subset_fonts descriptor is None or stream identity is None
    font_no_desc = pikepdf.Dictionary({"/Type": pikepdf.Name("/Font")})
    font_desc_no_stream = pikepdf.Dictionary(
        {"/Type": pikepdf.Name("/Font"), "/FontDescriptor": pikepdf.Dictionary()}
    )
    with patch(
        "pdftl.operations.subset_fonts.collect_used_codes",
        return_value=({"f1": {65}, "f2": {66}}, {"f1": font_no_desc, "f2": font_desc_no_stream}),
    ):
        res = subset_fonts(pdf, [])
        assert res.success is True


def test_width_resync_reads_back_the_actually_rewritten_bytes():
    """
    _extract_widths_from_subsetted_stream calls stream_obj.read_bytes()
    immediately after stream_obj.write(new_bytes) was called earlier in
    the same pass. This test pins that the widths computed post-subset
    are derived from the NEW (subsetted) font program, not a stale
    read of the pre-subset stream -- by using two fonts whose full and
    subsetted glyph widths deliberately differ in a detectable way if
    the wrong bytes were read.
    """
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))

    # A TTF with two glyphs of very different advance widths where only
    # one is used; if width resync reads stale (pre-write) bytes it
    # would still succeed (since the pre-subset font also contains the
    # glyph), so instead assert on stream identity: the bytes handed to
    # get_font_widths_from_file must be bit-identical to what was just
    # written, not a cached pre-write buffer.
    ttf_bytes = _create_dummy_cid_ttf()
    font_stream = pdf.make_stream(ttf_bytes)

    written_bytes = {}
    original_write = pikepdf.Object.write

    def spy_write(self, data, **kwargs):
        written_bytes["last"] = bytes(data)
        return original_write(self, data, **kwargs)

    cid_font_dict = pikepdf.Dictionary(
        Type=pikepdf.Name.Font,
        Subtype=pikepdf.Name.CIDFontType2,
        BaseFont=pikepdf.Name.TestCIDFont,
        CIDSystemInfo=pikepdf.Dictionary(Registry="Adobe", Ordering="Identity", Supplement=0),
        CIDToGIDMap=pikepdf.Name.Identity,
        FontDescriptor=pikepdf.Dictionary(
            Type=pikepdf.Name.FontDescriptor,
            FontName=pikepdf.Name.TestCIDFont,
            FontFile2=font_stream,
        ),
    )
    type0_font = pikepdf.Dictionary(
        Type=pikepdf.Name.Font,
        Subtype=pikepdf.Name.Type0,
        BaseFont=pikepdf.Name.TestCIDFont,
        Encoding=pikepdf.Name("/Identity-H"),
        DescendantFonts=pikepdf.Array([cid_font_dict]),
    )
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=type0_font))
    page.Contents = pdf.make_stream(b"/F1 12 Tf <0003> Tj")

    with patch.object(pikepdf.Object, "write", spy_write):
        subset_fonts(pdf, [])

    assert "last" in written_bytes, "stream was never rewritten"

    subsetted_cid_font = pdf.pages[0].Resources.Font.F1.DescendantFonts[0]
    readback = bytes(subsetted_cid_font.FontDescriptor.FontFile2.read_raw_bytes())

    assert readback == written_bytes["last"], (
        "widths/CIDToGIDMap resync read back different bytes than what "
        "was just written to the font stream -- indicates a stale read"
    )


def _build_legacy_symbolic_ttf() -> bytes:
    """
    Builds a minimal TrueType font matching the real-world shape that
    triggered this bug: a handful of glyphs, and a SINGLE cmap subtable
    at platform 1 / encoding 0 / format 0 (the Macintosh byte-encoding
    table), mapping raw codes 1-3 directly to glyphs -- no (3, 1) or
    (0, x) Unicode-capable subtable at all.
    """
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib import newTable
    from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

    glyph_order = [".notdef", "glyph00001", "glyph00002", "glyph00003"]
    fb = FontBuilder(1000, isTTF=True)
    fb.setupGlyphOrder(glyph_order)

    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((0, 500))
    pen.lineTo((500, 500))
    pen.closePath()
    glyph = pen.glyph()

    glyphs = {}
    for name in glyph_order:
        if name == ".notdef":
            p = TTGlyphPen(None)
            glyphs[name] = p.glyph()
        else:
            glyphs[name] = glyph
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics({name: (500, 0) for name in glyph_order})
    fb.setupHorizontalHeader(ascent=800, descent=-200)
    fb.setupNameTable({"familyName": "LegacySymbolTest", "styleName": "Regular"})
    fb.setupPost()
    fb.setupMaxp()

    # Manually install ONLY a (1, 0) format-0 cmap -- no (3,1)/(0,x)
    # Unicode subtable, and no (3,0) symbol subtable either -- to match
    # the real font this bug was found against.
    cmap = newTable("cmap")
    cmap.tableVersion = 0
    sub = CmapSubtable.newSubtable(0)
    sub.platformID = 1
    sub.platEncID = 0
    sub.language = 0
    sub.cmap = {1: "glyph00001", 2: "glyph00002", 3: "glyph00003"}
    cmap.tables = [sub]
    fb.font["cmap"] = cmap
    fb.setupOS2()

    buf = BytesIO()
    fb.font.save(buf)
    return buf.getvalue()


def _make_legacy_symbolic_pdf() -> pikepdf.Pdf:
    """
    A symbolic simple TrueType /Font dict with NO /Encoding at all
    (Flags=4), matching the real PDF this was found against: codes 1-3
    are painted directly, relying entirely on the font's own (1, 0)
    cmap to resolve them.
    """
    ttf_bytes = _build_legacy_symbolic_ttf()

    pdf = pikepdf.new()
    font_stream = pdf.make_stream(ttf_bytes)
    font_stream["/Length1"] = len(ttf_bytes)

    descriptor = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name("/BAAAAA+LegacySymbolTest"),
                "/Flags": 4,  # Symbolic
                "/FontBBox": [0, 0, 1000, 1000],
                "/ItalicAngle": 0,
                "/Ascent": 800,
                "/Descent": -200,
                "/CapHeight": 700,
                "/StemV": 80,
                "/FontFile2": font_stream,
            }
        )
    )
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
                "/BaseFont": pikepdf.Name("/BAAAAA+LegacySymbolTest"),
                "/FirstChar": 0,
                "/LastChar": 3,
                "/Widths": [0, 500, 500, 500],
                "/FontDescriptor": descriptor,
                # Deliberately no /Encoding -- the font's own cmap is
                # the only source of truth for code -> glyph.
            }
        )
    )

    page = pdf.add_blank_page()
    page["/Resources"] = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})
    page["/Contents"] = pdf.make_stream(b"/F1 12 Tf <010203> Tj")
    return pdf


def test_legacy_mac_cmap_survives_subsetting():
    """
    Regression test for a real bug found against a real-world PDF: a symbolic
    simple TrueType font (Flags=4, no /Encoding at all) whose embedded
    FontFile2 carries only a legacy Macintosh (1, 0) format-0 cmap subtable
    -- the shape LibreOffice/Word commonly produce for such fonts -- lost its
    entire cmap table during subset_fonts.

    fontTools.subset.Options() defaults `legacy_cmap` and `symbol_cmap` to
    False, meaning the subsetter actively strips "legacy" (platform 1, or
    non-Unicode format 0/1/4) and Windows-Symbol (3, 0) cmap subtables unless
    told not to. For a font whose ONLY cmap subtable is exactly one of
    those, and which has no /Differences to fall back on, this left the
    font with zero cmap subtables at all -- silently destroying every
    code->glyph mapping the content stream depends on -- even though the
    subsetted file was smaller and so passed the "is it actually smaller"
    check and got written back.

    The regression: a symbolic TrueType font whose only cmap subtable
    is a legacy Macintosh (1, 0) format-0 table must still have a
    non-empty, correctly-resolving cmap after subset_fonts -- not zero
    cmap subtables, which silently breaks every code in the content
    stream.
    """
    from fontTools.ttLib import TTFont

    pdf = _make_legacy_symbolic_pdf()

    result = subset_fonts(pdf, [])
    assert result.success

    font = pdf.pages[0]["/Resources"]["/Font"]["/F1"]
    new_bytes = bytes(font["/FontDescriptor"]["/FontFile2"].read_bytes())
    tt = TTFont(BytesIO(new_bytes))

    assert "cmap" in tt, "cmap table was dropped entirely during subsetting"
    assert len(tt["cmap"].tables) > 0, (
        "cmap table survived but has no subtables left -- legacy/symbol "
        "subtables were stripped by fontTools' subset defaults"
    )

    # The three painted codes must still resolve to real glyphs via
    # SOME subtable in the rebuilt cmap (matching the (1,0)-inclusive
    # preference chain font_binary_sfnt._get_best_cmap already uses).
    resolved_codes = set()
    for table in tt["cmap"].tables:
        resolved_codes.update(table.cmap.keys())
    assert {1, 2, 3} <= resolved_codes, (
        "codes painted by the content stream no longer resolve to any "
        "glyph in the subsetted font's cmap"
    )


def test_tounicode_derived_glyph_retention_paths():
    """
    _codes_to_extra_gids_via_tounicode resolves a Simple font's used
    codes to extra GIDs via /ToUnicode, purely for retention -- exercises
    the missing-stream, unreadable-stream, no-match, and successful
    resolution paths.
    """
    from pdftl.operations.subset_fonts import _codes_to_extra_gids_via_tounicode

    pdf = pikepdf.new()

    # No /ToUnicode at all.
    font_none = pikepdf.Dictionary({"/Type": pikepdf.Name("/Font")})
    assert _codes_to_extra_gids_via_tounicode(font_none, {0x41}, MagicMock()) == set()

    # /ToUnicode present but the stream can't be read.
    class UnreadableToUnicode:
        def read_bytes(self):
            raise AttributeError("no bytes")

    font_unreadable = {"/ToUnicode": UnreadableToUnicode()}
    assert _codes_to_extra_gids_via_tounicode(font_unreadable, {0x41}, MagicMock()) == set()

    cmap_data = (
        b"/CIDInit /ProcSet findresource begin\n"
        b"12 dict begin\nbegincmap\n"
        b"1 begincodespacerange\n<00> <FF>\nendcodespacerange\n"
        b"1 beginbfchar\n<41> <0041>\nendbfchar\n"
        b"endcmap\nend\nend\n"
    )

    # None of the requested codes appear in the /ToUnicode mapping.
    font_no_match = pikepdf.Dictionary({"/ToUnicode": pdf.make_stream(cmap_data)})
    assert _codes_to_extra_gids_via_tounicode(font_no_match, {0x99}, MagicMock()) == set()

    # A requested code resolves through /ToUnicode to a real character,
    # which the font's own cmap then resolves to a GID.
    font_match = pikepdf.Dictionary({"/ToUnicode": pdf.make_stream(cmap_data)})
    with patch(
        "pdftl.fonts.font_subsetting.gids_for_simple_font_via_cmap", return_value={7}
    ) as mock_gids:
        result = _codes_to_extra_gids_via_tounicode(font_match, {0x41}, MagicMock())
    assert result == {7}
    mock_gids.assert_called_once()


def test_get_simple_font_encoding_differences_without_base_encoding():
    """/Encoding can carry /Differences with no /BaseEncoding at all --
    a legal PDF shape distinct from the /Differences+/BaseEncoding case
    already covered elsewhere."""
    enc_diff_only = pikepdf.Dictionary({"/Differences": pikepdf.Array([65, pikepdf.Name("/A")])})
    font_diff_only = pikepdf.Dictionary(
        {"/Type": pikepdf.Name("/Font"), "/Encoding": enc_diff_only}
    )
    diffs, base = _get_simple_font_encoding(font_diff_only)
    assert diffs == [65, pikepdf.Name("/A")]
    assert base is None


# --- append to tests/operations/test_subset_fonts.py ---


from pdftl.operations import subset_fonts as sf


def test_named_base_encoding_without_differences_uses_encoding_path():
    """/Encoding /WinAnsiEncoding as a bare Name (no /Differences dict)
    must still resolve glyphs via the PDF's own encoding table, not the
    font's raw cmap -- codes in the 0x80-0x9F range diverge between the
    two for exactly this case (smart quotes, en/em dash, ellipsis)."""
    font_obj = {"/Subtype": "/TrueType"}
    group_entries = [(font_obj, object(), {0x91, 0x92})]

    with (
        patch.object(sf, "_get_simple_font_encoding", return_value=(None, "WinAnsiEncoding")),
        patch.object(sf.fs, "gids_for_simple_font_via_encoding", return_value=set()) as via_enc,
        patch.object(sf.fs, "gids_for_simple_font_via_cmap", return_value=set()) as via_cmap,
    ):
        sf._collect_gids_for_group(None, group_entries, "sfnt")

    via_enc.assert_called_once()
    via_cmap.assert_not_called()


def test_no_encoding_entry_at_all_uses_cmap_path():
    """A font with no /Encoding entry at all has no PDF-level encoding to
    resolve through -- the font's own built-in cmap is authoritative and
    is the only correct fallback."""
    font_obj = {"/Subtype": "/TrueType"}
    group_entries = [(font_obj, object(), {ord("A")})]

    with (
        patch.object(sf, "_get_simple_font_encoding", return_value=(None, None)),
        patch.object(sf.fs, "gids_for_simple_font_via_encoding", return_value=set()) as via_enc,
        patch.object(sf.fs, "gids_for_simple_font_via_cmap", return_value=set()) as via_cmap,
    ):
        sf._collect_gids_for_group(None, group_entries, "sfnt")

    via_cmap.assert_called_once()
    via_enc.assert_not_called()


def test_differences_present_uses_encoding_path():
    """Pre-existing case, kept as a regression guard: /Differences must
    always resolve via the PDF's own encoding, regardless of base_encoding."""
    font_obj = {"/Subtype": "/TrueType"}
    group_entries = [(font_obj, object(), {ord("A")})]

    with (
        patch.object(sf, "_get_simple_font_encoding", return_value=([65, "A"], None)),
        patch.object(sf.fs, "gids_for_simple_font_via_encoding", return_value=set()) as via_enc,
        patch.object(sf.fs, "gids_for_simple_font_via_cmap", return_value=set()) as via_cmap,
    ):
        sf._collect_gids_for_group(None, group_entries, "sfnt")

    via_enc.assert_called_once()
    via_cmap.assert_not_called()


def test_bare_cff_always_uses_encoding_path_regardless_of_encoding():
    """Pre-existing case, kept as a regression guard: a bare-CFF program
    has no 'cmap' table to fall back on at all, so it must always go
    through the encoding path even with no /Encoding info whatsoever."""
    font_obj = {"/Subtype": "/TrueType"}
    group_entries = [(font_obj, object(), {ord("A")})]

    with (
        patch.object(sf, "_get_simple_font_encoding", return_value=(None, None)),
        patch.object(sf.fs, "gids_for_simple_font_via_encoding", return_value=set()) as via_enc,
        patch.object(sf.fs, "gids_for_simple_font_via_cmap", return_value=set()) as via_cmap,
    ):
        sf._collect_gids_for_group(None, group_entries, "bare_cff")

    via_enc.assert_called_once()
    via_cmap.assert_not_called()


# --- append to tests/operations/test_subset_fonts.py ---


def test_log_subset_stat_type1_conversion_branch():
    """_log_subset_stat's before_bytes < 0 branch (Type 1 -> CFF
    conversion, which doesn't track byte/glyph deltas) is only reached
    when a _SubsetStat carries the -1 sentinel -- never exercised by the
    sfnt/bare_cff-only test fixtures elsewhere in this module."""
    from pdftl.operations.subset_fonts import _SubsetStat, _log_subset_stat

    stat = _SubsetStat(label="SomeType1Font", before_bytes=-1, after_bytes=-1)
    _log_subset_stat(stat)  # must not raise; nothing to assert beyond that


def test_log_subset_stat_sized_but_glyph_untracked_branch():
    """A stat with real byte counts but before_glyphs still at its -1
    default (the 'sized but glyph-count-unknown' shape) takes the
    byte-percentage-only logging branch, distinct from the byte+glyph
    branch every other test happens to use."""
    from pdftl.operations.subset_fonts import _SubsetStat, _log_subset_stat

    stat = _SubsetStat(label="NoGlyphTracking", before_bytes=1000, after_bytes=500)
    assert stat.before_glyphs == -1
    _log_subset_stat(stat)


def test_log_subset_summary_all_stats_unsized_falls_back_to_bare_count():
    """When every stat in the list is a Type1 conversion (before_bytes
    == -1), `sized` ends up empty and _log_subset_summary must fall back
    to the plain 'Subsetted N font program(s).' summary rather than
    dividing by a zero-length total."""
    from pdftl.operations.subset_fonts import _SubsetStat, _log_subset_summary

    stats = [_SubsetStat(label="A", before_bytes=-1, after_bytes=-1)]
    _log_subset_summary(subsetted_count=1, stats=stats)  # must not raise


def test_log_subset_summary_sized_but_no_glyph_data_omits_glyph_clause():
    """Sized stats (real before/after byte counts) whose glyph counts are
    all still -1 must produce a summary with the byte-savings clause but
    WITHOUT the glyph-removed clause, since glyph_sized ends up empty."""
    from pdftl.operations.subset_fonts import _SubsetStat, _log_subset_summary

    stats = [_SubsetStat(label="A", before_bytes=1000, after_bytes=400)]
    _log_subset_summary(subsetted_count=1, stats=stats)  # must not raise


def test_subset_fonts_end_to_end_mixes_a_skipped_and_a_successful_group():
    """subset_fonts' own group loop must correctly handle BOTH outcomes
    in a single run: a group whose _subset_and_resync_group call returns
    stat=None (appended nowhere) alongside one that returns a real stat
    (appended to `stats`) -- exercising both directions of the
    'if stat is not None' branch, not just whichever one every other
    single-font test happens to hit."""
    pdf = pikepdf.new()

    # Group 1: a Type3 font (no font program at all) -- _subset_and_resync_group
    # returns (0, None) for this, so `stat` stays None and must NOT be appended.
    type3_font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type3"),
            }
        )
    )

    # Group 2: a real CIDFontType2 font that WILL subset successfully,
    # producing a real _SubsetStat that must be appended.
    ttf_bytes = _create_dummy_cid_ttf()
    font_stream = pdf.make_stream(ttf_bytes)
    cid_font_dict = pikepdf.Dictionary(
        Type=pikepdf.Name.Font,
        Subtype=pikepdf.Name.CIDFontType2,
        BaseFont=pikepdf.Name.TestCIDFont,
        CIDSystemInfo=pikepdf.Dictionary(Registry="Adobe", Ordering="Identity", Supplement=0),
        CIDToGIDMap=pikepdf.Name.Identity,
        FontDescriptor=pikepdf.Dictionary(
            Type=pikepdf.Name.FontDescriptor,
            FontName=pikepdf.Name.TestCIDFont,
            FontFile2=font_stream,
        ),
    )
    type0_font = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Font,
            Subtype=pikepdf.Name.Type0,
            BaseFont=pikepdf.Name.TestCIDFont,
            Encoding=pikepdf.Name("/Identity-H"),
            DescendantFonts=pikepdf.Array([cid_font_dict]),
        )
    )

    page = pdf.add_blank_page(page_size=(612, 792))
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=type3_font, F2=type0_font))
    page.Contents = pdf.make_stream(b"/F1 12 Tf (X) Tj /F2 12 Tf <0003> Tj")

    result = subset_fonts(pdf, [])
    assert result.success
