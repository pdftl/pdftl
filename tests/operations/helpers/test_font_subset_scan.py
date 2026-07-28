# tests/operations/helpers/test_font_subset_scan.py

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
Real (non-mocked) tests for collect_used_codes, driven against genuine
pikepdf documents.

Written test-first for a real bug found while running subset_fonts
end-to-end against a real PDF: pikepdf returns a *new* Python wrapper
object every time the same underlying indirect PDF object is accessed
(confirmed directly: `page["/Resources"]["/Font"]["/F1"] is
page["/Resources"]["/Font"]["/F1"]` is False), so keying
`codes_by_font_id` by `id(font_obj)` -- as the original implementation
did -- silently fragments a single logical font, re-selected via
multiple `Tf` operators across a content stream, into many spurious
dict entries instead of one. On a real multi-hundred-operator content
stream this manifested as ~5x too many "font" entries, each carrying
only a handful of codes, and correspondingly a Type 1 font being
reconverted to CFF from scratch many times over instead of once.
"""

from __future__ import annotations

import pikepdf
from unittest.mock import MagicMock, patch

from pdftl.operations.helpers.font_subset_scan import (
    _PageFontCodeScanner,
    _apply_text_showing_operator,
    _scan_stream,
    collect_used_codes,
)


from io import BytesIO
from fontTools.fontBuilder import FontBuilder
from fontTools.ttLib.tables._g_l_y_f import Glyph


def _build_ttf_with_named_gids(glyph_order: list[str]) -> bytes:
    """
    Builds a minimal, valid TTF whose glyph order is exactly
    `glyph_order` (so a specific glyph name lands at a specific GID,
    e.g. index 5 -> glyph_order[5]). Every glyph is an empty outline;
    only glyph identity/order matters for these tests, not rendering.

    All names in `glyph_order` must be unique -- fontTools keys its
    internal glyph table by name, so a repeated name (e.g. multiple
    literal ".notdef" placeholder entries) collapses to one glyph and
    desyncs glyphOrder's length from the actual glyph table's length.
    Callers padding out GID slots should use distinct placeholder names
    (e.g. "pad0", "pad1", ...), not repeats of the same string.
    """
    assert len(glyph_order) == len(set(glyph_order)), "glyph_order must contain unique names"

    fb = FontBuilder(1024, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap({})

    glyphs = {g: Glyph() for g in glyph_order}
    fb.setupGlyf(glyphs)

    fb.setupHead()
    metrics = {g: (500, 0) for g in glyph_order}
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader()
    fb.setupOS2()
    fb.setupPost()
    fb.setupNameTable({"familyName": "TestFont", "styleName": "Regular"})

    buf = BytesIO()
    fb.save(buf)
    return buf.getvalue()


def _make_page_with_font(pdf: pikepdf.Pdf, font_name: str = "/F1") -> pikepdf.Object:
    font_obj = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
            }
        )
    )
    page = pdf.add_blank_page()
    page["/Resources"] = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({font_name: font_obj})})
    return page


class TestCollectUsedCodesFontIdentity:
    def test_same_font_selected_many_times_is_one_entry(self):
        """The core regression: a content stream that re-selects the same
        font via Tf many times (e.g. interspersed with other operators,
        as any real-world PDF producer emits) must still be recognized as
        ONE font with the UNION of all codes shown under it -- not one
        fragmented entry per Tf operator."""
        pdf = pikepdf.new()
        page = _make_page_with_font(pdf)

        # Ten separate Tf/Tj pairs, each re-fetching "the same" /F1 --
        # exactly the pattern a real content stream uses whenever text
        # runs are interrupted by other operators (color changes,
        # positioning, etc.) in between showing text under the same font.
        parts = []
        for code in range(10):
            parts.append(f"/F1 12 Tf ({chr(65 + code)}) Tj\n".encode("latin-1"))
        content = b"".join(parts)

        page["/Contents"] = pikepdf.Stream(pdf, content)

        codes_by_font, resolved = collect_used_codes([page])

        assert len(codes_by_font) == 1, (
            f"expected exactly 1 font entry, got {len(codes_by_font)}: "
            f"the same font is being fragmented across multiple Tf operators"
        )
        (only_codes,) = codes_by_font.values()
        assert only_codes == {65 + i for i in range(10)}

    def test_two_distinct_fonts_stay_distinct(self):
        """Guards against overcorrecting into merging genuinely different
        fonts -- two different font resources must still produce two
        separate entries."""
        pdf = pikepdf.new()
        font_a = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Font"),
                    "/Subtype": pikepdf.Name("/Type1"),
                    "/BaseFont": pikepdf.Name("/Helvetica"),
                }
            )
        )
        font_b = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Font"),
                    "/Subtype": pikepdf.Name("/Type1"),
                    "/BaseFont": pikepdf.Name("/Times-Roman"),
                }
            )
        )
        page = pdf.add_blank_page()
        page["/Resources"] = pikepdf.Dictionary(
            {"/Font": pikepdf.Dictionary({"/F1": font_a, "/F2": font_b})}
        )
        content = b"/F1 12 Tf (A) Tj /F2 12 Tf (B) Tj /F1 12 Tf (C) Tj"
        page["/Contents"] = pikepdf.Stream(pdf, content)

        codes_by_font, resolved = collect_used_codes([page])

        assert len(codes_by_font) == 2
        code_sets = set(frozenset(s) for s in codes_by_font.values())
        assert code_sets == {frozenset({ord("A"), ord("C")}), frozenset({ord("B")})}

    def test_same_font_across_multiple_pages_is_one_entry(self):
        """The same underlying font resource, shared across multiple
        pages' /Resources, must also dedupe to one entry with the union
        of codes from every page -- not one entry per page."""
        pdf = pikepdf.new()
        font_obj = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Font"),
                    "/Subtype": pikepdf.Name("/Type1"),
                    "/BaseFont": pikepdf.Name("/Helvetica"),
                }
            )
        )
        page1 = pdf.add_blank_page()
        page1["/Resources"] = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_obj})})
        page1["/Contents"] = pikepdf.Stream(pdf, b"/F1 12 Tf (A) Tj")

        page2 = pdf.add_blank_page()
        page2["/Resources"] = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_obj})})
        page2["/Contents"] = pikepdf.Stream(pdf, b"/F1 12 Tf (B) Tj")

        codes_by_font, resolved = collect_used_codes([page1, page2])

        assert len(codes_by_font) == 1
        (only_codes,) = codes_by_font.values()
        assert only_codes == {ord("A"), ord("B")}


def test_scan_collects_used_codes_from_form_xobjects():
    """
    Verifies that character codes used inside Form XObjects (/Do operator)
    are scanned and collected.
    """
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))

    # Define a simple Type1 font dictionary
    font_dict = pikepdf.Dictionary(
        Type=pikepdf.Name.Font,
        Subtype=pikepdf.Name.Type1,
        BaseFont=pikepdf.Name.Helvetica,
    )

    # Create Form XObject stream containing text: /F1 12 Tf (FormText) Tj
    xobj_data = b"/F1 12 Tf (FormText) Tj"
    xobj_dict = pikepdf.Dictionary(
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Form,
        BBox=[0, 0, 100, 100],
        Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font_dict)),
    )
    form_xobject = pdf.make_stream(xobj_data, xobj_dict)

    # Main page content stream only executes /Fm1 Do
    page.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(F1=font_dict),
        XObject=pikepdf.Dictionary(Fm1=form_xobject),
    )
    page.Contents = pdf.make_stream(b"/Fm1 Do")

    # Execute scanner
    codes_by_font_id, _ = collect_used_codes([page])
    expected_codes = {ord(c) for c in "FormText"}

    assert len(codes_by_font_id) > 0, "Font inside Form XObject was completely missed."

    font_key = next(iter(codes_by_font_id.keys()))
    collected_codes = codes_by_font_id[font_key]

    assert expected_codes.issubset(collected_codes), (
        f"Character codes inside Form XObject were missed. "
        f"Expected: {expected_codes}, Got: {collected_codes}"
    )


def test_scan_stream_restores_font_state_after_nested_stream():
    """
    Ensures _scan_stream restores parent font state upon completion,
    preventing state leaks when a child Form XObject changes the active font.
    """
    # 1. Arrange: Create mock scanner with an active parent font ('F1')
    scanner = MagicMock()
    scanner._resources = {"Font": {"/F1": "ParentFontObj", "/F2": "ChildFontObj"}}
    scanner._font_dict = scanner._resources["Font"]
    scanner._xobject_dict = {}

    # Set initial parent active font state
    scanner._current_font_obj = "ParentFontObj"
    scanner._current_font_key = "/F1"
    scanner._current_bytes_per_code = 1

    # Mock stream object and pikepdf parser output
    mock_stream = MagicMock()

    # Simulate child stream execution that mutates scanner font state (e.g., executing `/F2 10 Tf`)
    def simulate_child_stream_execution(stream, *args, **kwargs):
        scanner._current_font_obj = "ChildFontObj"
        scanner._current_font_key = "/F2"
        scanner._current_bytes_per_code = 2
        return []  # Return empty instruction list for simplicity

    # 2. Act: Run _scan_stream on child stream with mocked instruction parser
    with patch(
        "pikepdf.parse_content_stream",
        side_effect=simulate_child_stream_execution,
    ):
        _scan_stream(mock_stream, scanner._resources, scanner)

    # 3. Assert: Verify active font state reverted back to the parent font ('F1')
    assert scanner._current_font_obj == "ParentFontObj", "Failed to restore parent font object"
    assert scanner._current_font_key == "/F1", "Failed to restore parent font key"
    assert scanner._current_bytes_per_code == 1, "Failed to restore parent bytes_per_code setting"


def test_set_font_missing_font_clears_state():
    scanner = _PageFontCodeScanner({}, {})
    font_f1 = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type1")})
    scanner._font_dict = {"/F1": font_f1}
    scanner.set_font(pikepdf.Name("/F1"))
    assert scanner._current_font_obj == font_f1

    scanner.set_font(pikepdf.Name("/F2"))
    assert scanner._current_font_obj is None
    assert scanner._current_font_key is None


def test_record_string_without_active_font_does_nothing():
    codes_by_font = {}
    resolved = {}
    scanner = _PageFontCodeScanner(codes_by_font, resolved)
    scanner.record_string(b"ABC")
    assert codes_by_font == {}
    assert resolved == {}


def test_handle_do_edge_cases():
    scanner = _PageFontCodeScanner({}, {})

    # 1. _xobject_dict is None
    scanner._xobject_dict = None
    scanner.handle_do(pikepdf.Name("/Fm1"))

    # 2. XObject name missing from _xobject_dict
    scanner._xobject_dict = {}
    scanner.handle_do(pikepdf.Name("/Fm1"))

    # 3. Subtype is not /Form (e.g., /Image)
    img_xobj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Image")})
    scanner._xobject_dict = {"/Img1": img_xobj}
    scanner.handle_do(pikepdf.Name("/Img1"))

    # 4. Already visited XObject
    form_xobj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Form")})
    scanner._xobject_dict = {"/Fm1": form_xobj}
    scanner._visited.add((id(scanner._xobject_dict), "/Fm1"))
    with patch("pdftl.operations.helpers.font_subset_scan._scan_stream") as mock_scan:
        scanner.handle_do(pikepdf.Name("/Fm1"))
        mock_scan.assert_not_called()


def test_text_showing_operators():
    scanner = MagicMock()

    # Operator '
    _apply_text_showing_operator(scanner, "'", [pikepdf.String("line")], pikepdf)
    scanner.record_string.assert_called_with(b"line")

    # Operator "
    scanner.reset_mock()
    _apply_text_showing_operator(scanner, '"', [0, 0, pikepdf.String("spaced")], pikepdf)
    scanner.record_string.assert_called_with(b"spaced")

    # Operator TJ with mixed operands (strings and numeric kerning values)
    scanner.reset_mock()
    _apply_text_showing_operator(
        scanner, "TJ", [[pikepdf.String("Hello"), -10, pikepdf.String("World")]], pikepdf
    )
    assert scanner.record_string.call_count == 2
    scanner.record_string.assert_any_call(b"Hello")
    scanner.record_string.assert_any_call(b"World")

    # Empty/unrecognized operator calls
    scanner.reset_mock()
    _apply_text_showing_operator(scanner, "Tf", [], pikepdf)
    _apply_text_showing_operator(scanner, "UNKNOWN", [1, 2], pikepdf)
    scanner.set_font.assert_not_called()


def test_scan_stream_with_none_resources_preserves_scanner_resources():
    scanner = _PageFontCodeScanner({}, {})
    scanner._font_dict = {"/F1": "FontObj"}
    scanner._xobject_dict = {}

    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    page["/Contents"] = pikepdf.Stream(pdf, b"")

    _scan_stream(page, None, scanner)
    assert scanner._font_dict == {"/F1": "FontObj"}


def test_scan_stream_handles_parser_errors_gracefully():
    scanner = _PageFontCodeScanner({}, {})
    pdf = pikepdf.new()
    page = pdf.add_blank_page()

    with patch("pikepdf.parse_content_stream", side_effect=ValueError("Corrupt stream")):
        _scan_stream(page, {}, scanner)


def test_collect_used_codes_empty_page():
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    codes_by_font_id, resolved = collect_used_codes([page])
    assert codes_by_font_id == {}
    assert resolved == {}


def test_non_identity_cmap_type0_font_is_not_silently_corrupted():
    import pikepdf
    from pdftl.operations.subset_fonts import subset_fonts

    pdf = pikepdf.new()

    ttf_bytes = _build_ttf_with_named_gids(
        glyph_order=[".notdef"] + [f"pad{i}" for i in range(4)] + ["glyphA", "glyphB"]
    )
    font_stream = pdf.make_stream(ttf_bytes)

    # CIDToGIDMap covers CIDs 0..2 only: CID 1 -> GID 5 (glyphA), CID 2 -> GID 6 (glyphB)
    cid_to_gid_bytes = b"".join({1: 5, 2: 6}.get(cid, 0).to_bytes(2, "big") for cid in range(3))
    cid_to_gid_stream = pdf.make_stream(cid_to_gid_bytes)

    cid_font_dict = pikepdf.Dictionary(
        Type=pikepdf.Name.Font,
        Subtype=pikepdf.Name.CIDFontType2,
        BaseFont=pikepdf.Name.TestFont,
        CIDSystemInfo=pikepdf.Dictionary(Registry="Adobe", Ordering="GB1", Supplement=0),
        CIDToGIDMap=cid_to_gid_stream,
        FontDescriptor=pikepdf.Dictionary(
            Type=pikepdf.Name.FontDescriptor,
            FontName=pikepdf.Name.TestFont,
            FontFile2=font_stream,
        ),
    )
    type0_font = pikepdf.Dictionary(
        Type=pikepdf.Name.Font,
        Subtype=pikepdf.Name.Type0,
        BaseFont=pikepdf.Name.TestFont,
        Encoding=pikepdf.Name("/UniGB-UCS2-H"),
        DescendantFonts=pikepdf.Array([cid_font_dict]),
    )

    page = pdf.add_blank_page()
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=type0_font))
    # Content shows raw code 0x0002. Under a correct /UniGB-UCS2-H
    # resolution this is some CID -- but pdftl's scanner just treats
    # the raw code as the CID directly, so it would look up CID 2
    # (-> glyphB) regardless of what the real CMap says. We pick a
    # code that is NOT the CID the naive path would produce a valid
    # in-range result for CID 1 (glyphA), to distinguish "resolved
    # correctly" from "resolved via the wrong shortcut but happened to
    # still be in range."
    page.Contents = pdf.make_stream(b"/F1 12 Tf <0002> Tj")

    subset_fonts(pdf, [])

    subsetted_cid_font = pdf.pages[0].Resources.Font.F1.DescendantFonts[0]
    new_ttf_bytes = bytes(subsetted_cid_font.FontDescriptor.FontFile2.read_raw_bytes())

    # First: confirm subsetting actually happened at all (rules out the
    # "silently skipped, so nothing was corrupted because nothing ran"
    # false negative).
    assert new_ttf_bytes != ttf_bytes, (
        "font stream was not rewritten at all -- either subsetting was "
        "silently skipped (which is itself the bug: a Type0 font with a "
        "non-Identity /Encoding should either resolve correctly or be "
        "explicitly skipped with a clear signal, not silently no-op) or "
        "this test needs a different code to trigger a resolution attempt"
    )

    from fontTools.ttLib import TTFont
    from io import BytesIO

    tt = TTFont(BytesIO(new_ttf_bytes))
    glyph_order = tt.getGlyphOrder()

    # pad0-pad3 must be gone (real subsetting occurred); glyphB must be
    # gone too since only code 0x0002 was shown and the CORRECT CMap
    # resolution for /UniGB-UCS2-H code 0x0002 is not "CID 2" -- if
    # glyphB survived, that's the raw-code-as-CID shortcut at work.
    assert "glyphB" not in glyph_order, (
        "glyphB (reachable only by naively treating raw code 0x0002 as "
        "CID 2) survived subsetting -- confirms the scanner is treating "
        "the Type0 font's raw content-stream code as the CID directly, "
        "bypassing the font's actual /Encoding CMap"
    )


def test_direct_object_font_dict_still_fragments_across_tf_calls():
    """
    _font_key falls back to id(font_obj) when .objgen is (0, 0) or
    missing -- i.e. for a font resource that is a *direct* object
    rather than an indirect reference. pikepdf hands back a fresh
    Python wrapper on every dict access regardless of whether the
    underlying PDF object is direct or indirect, so this fallback is
    exactly as unstable as the id()-based bug the .objgen fix was
    written to solve -- just for the (rarer, but spec-permitted absent
    stricter validation) direct-object case.

    This currently reproduces fragmentation: the same logical font,
    selected via Tf multiple times, ends up as more than one entry
    when it's a *direct* (non-indirect) dictionary.
    """
    import pikepdf
    from pdftl.operations.helpers.font_subset_scan import collect_used_codes

    pdf = pikepdf.new()

    # Deliberately a DIRECT (non-indirect) font object -- not wrapped
    # in pdf.make_indirect.
    font_obj = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/Type1"),
            "/BaseFont": pikepdf.Name("/Helvetica"),
        }
    )
    page = pdf.add_blank_page()
    page["/Resources"] = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_obj})})

    parts = [f"/F1 12 Tf ({chr(65 + i)}) Tj\n".encode("latin-1") for i in range(10)]
    page["/Contents"] = pikepdf.Stream(pdf, b"".join(parts))

    codes_by_font, _resolved = collect_used_codes([page])

    assert len(codes_by_font) == 1, (
        f"expected exactly 1 font entry for a direct (non-indirect) font "
        f"dict re-selected via Tf, got {len(codes_by_font)} -- the "
        f"id()-fallback in _font_key is unstable across repeated "
        f"pikepdf attribute accesses just like the indirect-object bug "
        f"this module was originally fixed for"
    )


def test_text_showing_handlers_ignore_empty_operands():
    """Every text-showing handler's own guard against an empty operand
    list is exercised directly -- a malformed or truncated content
    stream can hand an operator zero operands, and each handler must
    no-op rather than index into an empty list."""
    scanner = MagicMock()

    _apply_text_showing_operator(scanner, "Tj", [], pikepdf)
    scanner.record_string.assert_not_called()

    _apply_text_showing_operator(scanner, "'", [], pikepdf)
    scanner.record_string.assert_not_called()

    _apply_text_showing_operator(scanner, '"', [0, 0], pikepdf)  # fewer than 3 operands
    scanner.record_string.assert_not_called()

    _apply_text_showing_operator(scanner, "TJ", [], pikepdf)
    scanner.record_string.assert_not_called()

    _apply_text_showing_operator(scanner, "Do", [], pikepdf)
    scanner.handle_do.assert_not_called()


def test_annotation_appearance_stream_collection():
    """
    _iter_annotation_appearance_streams collects /N, /D, and /R directly
    when they're plain streams, expands an appearance-state
    subdictionary (e.g. a checkbox's /On, /Off states) into every state's
    stream, and skips a key that's absent or neither a stream nor a
    dictionary -- and _scan_page_annotations wires the result into
    collect_used_codes' output for every stream found this way.
    """
    from collections import defaultdict
    from pdftl.operations.helpers.font_subset_scan import (
        _iter_annotation_appearance_streams,
        _scan_page_annotations,
    )

    pdf = pikepdf.new()

    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
            }
        )
    )

    def _stream_with_text(text: str) -> pikepdf.Object:
        s = pdf.make_stream(f"/F1 12 Tf ({text}) Tj".encode("latin-1"))
        s.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})
        return s

    n_stream = _stream_with_text("Normal")
    d_stream = _stream_with_text("Down")
    on_stream = _stream_with_text("On")
    off_stream = _stream_with_text("Off")

    annot = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Annot"),
            "/AP": pikepdf.Dictionary(
                {
                    "/N": n_stream,
                    "/D": d_stream,
                    "/R": pikepdf.Dictionary({"/On": on_stream, "/Off": off_stream}),
                }
            ),
        }
    )

    streams = _iter_annotation_appearance_streams(annot)
    assert len(streams) == 4

    # No /AP at all -- nothing to collect.
    annot_no_ap = pikepdf.Dictionary({"/Type": pikepdf.Name("/Annot")})
    assert _iter_annotation_appearance_streams(annot_no_ap) == []

    # /AP present but with a key that's neither a Stream nor a Dictionary.
    annot_bad_entry = pikepdf.Dictionary(
        {"/AP": pikepdf.Dictionary({"/N": pikepdf.Array([1, 2, 3])})}
    )
    assert _iter_annotation_appearance_streams(annot_bad_entry) == []

    page = pdf.add_blank_page()
    page["/Annots"] = pikepdf.Array([annot])

    codes_by_font: dict = defaultdict(set)
    resolved: dict = {}
    _scan_page_annotations(page, codes_by_font, resolved)

    all_codes: set = set()
    for codes in codes_by_font.values():
        all_codes |= codes
    for word in ("Normal", "Down", "On", "Off"):
        assert {ord(c) for c in word} <= all_codes

    # A page with no /Annots entry at all is a clean no-op.
    page_no_annots = pdf.add_blank_page()
    codes_by_font2: dict = defaultdict(set)
    resolved2: dict = {}
    _scan_page_annotations(page_no_annots, codes_by_font2, resolved2)
    assert codes_by_font2 == {}


def test_font_key_direct_object_without_scope_key_falls_back_to_id():
    """_font_key's bare id() fallback only fires when no scope_key is
    supplied at all -- exercised directly here since both real callers
    in this module always pass one now."""
    from pdftl.operations.helpers.font_subset_scan import _font_key

    class DirectObject(dict):
        objgen = (0, 0)

    obj = DirectObject()
    assert _font_key(obj) == id(obj)
    assert _font_key(obj, scope_key=None) == id(obj)


def test_annotation_appearance_substate_skips_non_stream_entries():
    """An appearance-state subdictionary (e.g. /On, /Off) can contain a
    non-Stream value for one of its states -- that entry is skipped
    rather than appended, while a genuine Stream sibling is still
    collected."""
    from pdftl.operations.helpers.font_subset_scan import (
        _iter_annotation_appearance_streams,
    )

    pdf = pikepdf.new()
    real_stream = pdf.make_stream(b"/F1 12 Tf (X) Tj")

    ap_state = pikepdf.Dictionary({"/On": real_stream, "/Off": pikepdf.Name("/NotAStream")})
    annot = pikepdf.Dictionary({"/AP": pikepdf.Dictionary({"/N": ap_state})})

    streams = _iter_annotation_appearance_streams(annot)
    assert streams == [real_stream]


# --- append to tests/operations/helpers/test_font_subset_scan.py ---


def _make_font(pdf, base_font="/Helvetica", subtype="/Type1"):
    return pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name("/Font"),
            Subtype=pikepdf.Name(subtype),
            BaseFont=pikepdf.Name(base_font),
        )
    )


def _codes_for(pdf, pages, font_ref):
    """Runs collect_used_codes and returns the code set attributed to
    the given indirect font object, matched by objgen (mirroring how
    _font_key identifies fonts) rather than by dict key, since
    collect_used_codes keys its result by that same stable identity."""
    codes_by_font_id, resolved = collect_used_codes(pages)
    for key, obj in resolved.items():
        if obj.objgen == font_ref.objgen:
            return codes_by_font_id[key]
    return set()


def test_q_Q_restores_font_selection():
    """A Tf made inside a q/Q bracket must not leak into text shown
    after the matching Q -- text state is part of graphics state and is
    restored by Q per spec."""
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    f1 = _make_font(pdf, "/F1")
    f2 = _make_font(pdf, "/F2")
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=f1, F2=f2))
    page.Contents = pdf.make_stream(b"/F1 12 Tf (A) Tj q /F2 12 Tf (B) Tj Q (C) Tj")

    codes_f1 = _codes_for(pdf, [page], f1)
    codes_f2 = _codes_for(pdf, [page], f2)

    assert codes_f1 == {ord("A"), ord("C")}
    assert codes_f2 == {ord("B")}


def test_nested_q_Q_restores_correct_font_at_each_level():
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    f1 = _make_font(pdf, "/F1")
    f2 = _make_font(pdf, "/F2")
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=f1, F2=f2))
    page.Contents = pdf.make_stream(
        b"/F1 12 Tf (A) Tj q /F2 12 Tf (B) Tj   q /F1 12 Tf (C) Tj Q (D) Tj Q (E) Tj"
    )

    codes_f1 = _codes_for(pdf, [page], f1)
    codes_f2 = _codes_for(pdf, [page], f2)

    assert codes_f1 == {ord("A"), ord("C"), ord("E")}
    assert codes_f2 == {ord("B"), ord("D")}


def test_unbalanced_Q_does_not_raise_and_leaves_state_as_is():
    """A malformed content stream with more Q than q shouldn't crash the
    scan; per pop_state's contract, an empty-stack Q is a no-op."""
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    f1 = _make_font(pdf, "/F1")
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=f1))
    page.Contents = pdf.make_stream(b"/F1 12 Tf (A) Tj Q (B) Tj")

    codes_f1 = _codes_for(pdf, [page], f1)

    assert codes_f1 == {ord("A"), ord("B")}


def test_unbalanced_q_inside_form_xobject_does_not_corrupt_parent_stack():
    """A Form XObject gets its own independent q/Q stack. An unbalanced
    'q' left open inside the form (no matching Q before the form's
    stream ends) must not leak onto the caller's stack, or the parent's
    own subsequent Q would incorrectly pop the form's leftover entry
    instead of the parent's."""
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    f1 = _make_font(pdf, "/F1")
    f2 = _make_font(pdf, "/F2")

    xobj = pdf.make_stream(b"/F1 12 Tf (X) Tj q /F2 12 Tf (Y) Tj")
    xobj.Subtype = pikepdf.Name("/Form")
    xobj.BBox = pikepdf.Array([0, 0, 200, 200])
    xobj.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=f1, F2=f2))

    page.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(F1=f1, F2=f2),
        XObject=pikepdf.Dictionary(Fx1=xobj),
    )
    page.Contents = pdf.make_stream(
        b"/F1 12 Tf (A) Tj "
        b"q /F2 12 Tf (B) Tj "
        b"/Fx1 Do "
        b"(C) Tj "  # still under parent's own F2 selection -- Do doesn't change text state
        b"Q "
        b"(D) Tj"  # Q must restore the PARENT's pre-q font (F1), not leak from the form
    )

    codes_f1 = _codes_for(pdf, [page], f1)
    codes_f2 = _codes_for(pdf, [page], f2)

    assert codes_f1 == {ord("A"), ord("X"), ord("D")}
    assert codes_f2 == {ord("B"), ord("Y"), ord("C")}


def test_do_referencing_missing_xobject_does_not_raise():
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    f1 = _make_font(pdf, "/F1")
    page.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(F1=f1), XObject=pikepdf.Dictionary()
    )
    page.Contents = pdf.make_stream(b"/F1 12 Tf (A) Tj /Fx1 Do (B) Tj")

    codes_f1 = _codes_for(pdf, [page], f1)

    assert codes_f1 == {ord("A"), ord("B")}


# --- append to tests/operations/helpers/test_font_subset_scan.py ---


def test_annotation_appearance_stream_falls_back_to_page_resources():
    """
    An appearance stream's own /Resources can be absent, or present but
    missing /Font entirely (both legal PDF shapes -- ISO 32000-2 doesn't
    require an appearance stream to duplicate resources already inherited
    from the page) -- _scan_page_annotations must fall back to the
    page's own /Resources in either case rather than scanning with no
    font dictionary at all and silently missing every code painted.
    """
    pdf = pikepdf.new()

    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/Helvetica"),
            }
        )
    )

    # Case 1: appearance stream has NO /Resources at all.
    stream_no_resources = pdf.make_stream(b"/F1 12 Tf (NoRes) Tj")
    annot1 = pikepdf.Dictionary({"/AP": pikepdf.Dictionary({"/N": stream_no_resources})})

    # Case 2: appearance stream HAS /Resources, but it has no /Font key.
    stream_no_font_key = pdf.make_stream(b"/F1 12 Tf (NoFontKey) Tj")
    stream_no_font_key.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary()})
    annot2 = pikepdf.Dictionary({"/AP": pikepdf.Dictionary({"/N": stream_no_font_key})})

    page = pdf.add_blank_page()
    page["/Resources"] = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})
    page["/Annots"] = pikepdf.Array([annot1, annot2])

    from collections import defaultdict
    from pdftl.operations.helpers.font_subset_scan import _scan_page_annotations

    codes_by_font: dict = defaultdict(set)
    resolved: dict = {}
    _scan_page_annotations(page, codes_by_font, resolved)

    all_codes: set = set()
    for codes in codes_by_font.values():
        all_codes |= codes

    for word in ("NoRes", "NoFontKey"):
        assert {ord(c) for c in word} <= all_codes, (
            f"'{word}' codes missing -- page-resources fallback for an "
            "appearance stream with no usable /Font of its own failed"
        )
