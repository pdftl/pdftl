# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/fonts/test_type3_tounicode_synthesis.py

import pikepdf

from pdftl.fonts.cmap_utils import compile_to_unicode_cmap
from pdftl.fonts.type3_tounicode_synthesis import (
    _iter_unique_font_objects,
    _resolve_via_encoding_and_agl,
    _resolve_via_to_unicode,
    build_synthetic_to_unicode_map,
    patch_missing_to_unicode,
    resolve_simple_font_char_to_unicode,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _blank_pdf():
    return pikepdf.Pdf.new()


def _add_font_to_page(pdf, page, font_dict_kwargs, resource_name="F1"):
    font = pdf.make_indirect(pikepdf.Dictionary(**font_dict_kwargs))
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(**{resource_name: font}))
    return font


# ---------------------------------------------------------------------------
# _resolve_via_to_unicode
# ---------------------------------------------------------------------------


def test_resolve_via_to_unicode_absent():
    font = pikepdf.Dictionary(Subtype=pikepdf.Name("/Type3"))
    assert _resolve_via_to_unicode(font) == {}


def test_resolve_via_to_unicode_present():
    pdf = _blank_pdf()
    cmap_bytes = compile_to_unicode_cmap({"41": "A"})
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Subtype=pikepdf.Name("/Type3"),
            ToUnicode=pdf.make_stream(cmap_bytes),
        )
    )
    result = _resolve_via_to_unicode(font)
    assert result == {0x41: "A"}


def test_resolve_via_to_unicode_unreadable_stream():
    """A malformed /ToUnicode value that raises on read_bytes() is treated
    as unresolvable rather than propagating the exception."""

    class _BadStream:
        def read_bytes(self):
            raise TypeError("not a stream")

    font = {"/ToUnicode": _BadStream()}
    assert _resolve_via_to_unicode(font) == {}


# ---------------------------------------------------------------------------
# _resolve_via_encoding_and_agl
# ---------------------------------------------------------------------------


def test_resolve_via_encoding_bare_name():
    font = pikepdf.Dictionary(
        Subtype=pikepdf.Name("/Type1"), Encoding=pikepdf.Name("/WinAnsiEncoding")
    )
    result = _resolve_via_encoding_and_agl(font)
    assert result[0x41] == "A"


def test_resolve_via_encoding_dictionary_with_differences_and_base():
    font = pikepdf.Dictionary(
        Subtype=pikepdf.Name("/Type1"),
        Encoding=pikepdf.Dictionary(
            BaseEncoding=pikepdf.Name("/WinAnsiEncoding"),
            Differences=[65, pikepdf.Name("/B")],
        ),
    )
    result = _resolve_via_encoding_and_agl(font)
    # /Differences overrides the base table for code 0x41.
    assert result[0x41] == "B"
    # Other WinAnsi-derived codes still resolve via the base table.
    assert result[0x42] == "B"  # base table: 0x42 -> "B" glyph name -> "B"


def test_resolve_via_encoding_dictionary_malformed_differences():
    class _BadDifferences:
        def __iter__(self):
            raise AttributeError("boom")

    # Build a minimal object exposing get()/"in" like pikepdf.Dictionary,
    # but with a /Differences value that raises when list()'d, to exercise
    # the malformed-/Differences except branch without pikepdf's own type
    # coercion getting in the way.
    class _FakeEncoding(dict):
        def get(self, key, default=None):
            return dict.get(self, key, default)

    fake_encoding = _FakeEncoding(
        {"/Differences": _BadDifferences(), "/BaseEncoding": "WinAnsiEncoding"}
    )
    font_obj = {"/Encoding": fake_encoding}
    result = _resolve_via_encoding_and_agl(font_obj)
    # Malformed /Differences is swallowed; base encoding still resolves.
    assert result[0x41] == "A"


def test_resolve_via_encoding_no_encoding_key_uses_standard_fallback():
    font = pikepdf.Dictionary(Subtype=pikepdf.Name("/Type1"))
    result = _resolve_via_encoding_and_agl(font)
    assert result[0x41] == "A"


def test_resolve_via_encoding_unrecognized_encoding_shape():
    """An /Encoding value that is neither a pikepdf.Name nor dict-like
    (e.g. a bare int, which can't legally occur in a real PDF but is
    defensively handled) falls through both isinstance branches, leaving
    differences and base_encoding both None, and resolves to nothing."""
    font_obj = {"/Encoding": 12345}
    result = _resolve_via_encoding_and_agl(font_obj)
    assert result == {}


def test_resolve_via_encoding_nothing_resolvable():
    """/Encoding present as a dict-like with neither /Differences nor
    /BaseEncoding still leaves both None, hitting the final empty-return
    branch only when /Encoding itself is entirely absent -- this test
    instead confirms an /Encoding dict with no usable content still
    resolves nothing but doesn't crash."""
    font = pikepdf.Dictionary(Subtype=pikepdf.Name("/Type1"), Encoding=pikepdf.Dictionary())
    result = _resolve_via_encoding_and_agl(font)
    assert result == {}


def test_resolve_via_encoding_glyph_name_not_in_agl_but_single_char():
    """A /Differences glyph name absent from the AGL but exactly one
    character long is used verbatim as the resolved character."""
    font = pikepdf.Dictionary(
        Subtype=pikepdf.Name("/Type3"),
        Encoding=pikepdf.Dictionary(Differences=[65, pikepdf.Name("/1")]),
    )
    result = _resolve_via_encoding_and_agl(font)
    assert result[0x41] == "1"


def test_resolve_via_encoding_glyph_name_unresolvable():
    """A /Differences glyph name absent from the AGL and longer than one
    character resolves to nothing (silently dropped, logged only)."""
    font = pikepdf.Dictionary(
        Subtype=pikepdf.Name("/Type3"),
        Encoding=pikepdf.Dictionary(Differences=[65, pikepdf.Name("/glyph00042")]),
    )
    result = _resolve_via_encoding_and_agl(font)
    assert 0x41 not in result


def test_resolve_via_encoding_code_present_in_neither_map_is_skipped():
    """A defensive branch: a code enumerated from the union of both maps
    but missing from both individual lookups (impossible in practice
    given how `codes` is built, but the `gname is None: continue` guard
    exists). Exercised by forcing _get_maps to return a base_encoding_map
    entry that maps to None for one of its own keys."""
    import pdftl.fonts.type3_tounicode_synthesis as mod

    font_obj = pikepdf.Dictionary(
        Subtype=pikepdf.Name("/Type1"), Encoding=pikepdf.Name("/WinAnsiEncoding")
    )

    original = mod._get_maps
    mod._get_maps = lambda differences, base_encoding: ({}, {0x41: None, 0x42: "B"})
    try:
        result = mod._resolve_via_encoding_and_agl(font_obj)
    finally:
        mod._get_maps = original

    assert 0x41 not in result
    assert result[0x42] == "B"


# ---------------------------------------------------------------------------
# resolve_simple_font_char_to_unicode
# ---------------------------------------------------------------------------


def test_resolve_simple_font_prefers_to_unicode():
    pdf = _blank_pdf()
    cmap_bytes = compile_to_unicode_cmap({"41": "Z"})
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Subtype=pikepdf.Name("/Type3"),
            ToUnicode=pdf.make_stream(cmap_bytes),
            Encoding=pikepdf.Name("/WinAnsiEncoding"),
        )
    )
    result = resolve_simple_font_char_to_unicode(font)
    assert result[0x41] == "Z"


def test_resolve_simple_font_falls_back_to_encoding():
    font = pikepdf.Dictionary(
        Subtype=pikepdf.Name("/Type3"), Encoding=pikepdf.Name("/WinAnsiEncoding")
    )
    result = resolve_simple_font_char_to_unicode(font)
    assert result[0x41] == "A"


def test_build_synthetic_to_unicode_map_wrapper():
    font = pikepdf.Dictionary(
        Subtype=pikepdf.Name("/Type3"), Encoding=pikepdf.Name("/WinAnsiEncoding")
    )
    assert build_synthetic_to_unicode_map(font) == resolve_simple_font_char_to_unicode(font)


# ---------------------------------------------------------------------------
# _iter_unique_font_objects
# ---------------------------------------------------------------------------


def test_iter_unique_font_objects_no_resources():
    pdf = _blank_pdf()
    page = pdf.add_blank_page()
    del page["/Resources"]
    assert _iter_unique_font_objects(pdf) == []


def test_iter_unique_font_objects_resources_without_font():
    pdf = _blank_pdf()
    page = pdf.add_blank_page()
    page.Resources = pikepdf.Dictionary()
    assert _iter_unique_font_objects(pdf) == []


def test_iter_unique_font_objects_dedupes_shared_font():
    pdf = _blank_pdf()
    page1 = pdf.add_blank_page()
    page2 = pdf.add_blank_page()
    font = pdf.make_indirect(pikepdf.Dictionary(Subtype=pikepdf.Name("/Type1")))
    page1.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    page2.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    fonts = _iter_unique_font_objects(pdf)
    assert len(fonts) == 1


def test_iter_unique_font_objects_multiple_distinct_fonts():
    pdf = _blank_pdf()
    page = pdf.add_blank_page()
    font1 = pdf.make_indirect(pikepdf.Dictionary(Subtype=pikepdf.Name("/Type1")))
    font2 = pdf.make_indirect(pikepdf.Dictionary(Subtype=pikepdf.Name("/Type3")))
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font1, F2=font2))
    fonts = _iter_unique_font_objects(pdf)
    assert len(fonts) == 2


def test_iter_unique_font_objects_finds_font_in_nested_form_xobject():
    """A Type3 font confined entirely to a nested Form XObject (e.g. a
    generated chart) must still be found, not just fonts on the page's
    own top-level /Resources/Font."""
    pdf = _blank_pdf()
    page = pdf.add_blank_page()
    font = pdf.make_indirect(pikepdf.Dictionary(Subtype=pikepdf.Name("/Type3")))
    xobj_resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    xobj = pdf.make_indirect(
        pikepdf.Stream(pdf, b"", Subtype=pikepdf.Name("/Form"), Resources=xobj_resources)
    )
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(X1=xobj))

    fonts = _iter_unique_font_objects(pdf)
    assert len(fonts) == 1
    assert fonts[0].objgen == font.objgen


def test_iter_unique_font_objects_skips_image_xobject():
    pdf = _blank_pdf()
    page = pdf.add_blank_page()
    image_xobj = pdf.make_indirect(pikepdf.Stream(pdf, b"", Subtype=pikepdf.Name("/Image")))
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(X1=image_xobj))
    assert _iter_unique_font_objects(pdf) == []


def test_iter_unique_font_objects_form_xobject_inherits_parent_resources():
    """A Form XObject with no /Resources of its own inherits the invoking
    scope's resources, so a font referenced only via the page's
    /Resources but reached while recursing through such an XObject is
    still found (and not double-counted against the page's own pass)."""
    pdf = _blank_pdf()
    page = pdf.add_blank_page()
    font = pdf.make_indirect(pikepdf.Dictionary(Subtype=pikepdf.Name("/Type3")))
    xobj = pdf.make_indirect(pikepdf.Stream(pdf, b"", Subtype=pikepdf.Name("/Form")))
    page.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(F1=font), XObject=pikepdf.Dictionary(X1=xobj)
    )
    fonts = _iter_unique_font_objects(pdf)
    assert len(fonts) == 1


def test_iter_unique_font_objects_avoids_xobject_cycle():
    """A self-referencing Form XObject must not cause infinite recursion."""
    pdf = _blank_pdf()
    page = pdf.add_blank_page()
    xobj = pdf.make_indirect(pikepdf.Stream(pdf, b"", Subtype=pikepdf.Name("/Form")))
    xobj.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(X1=xobj))
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(X1=xobj))

    fonts = _iter_unique_font_objects(pdf)
    assert fonts == []


def test_iter_unique_font_objects_respects_max_recursion_depth():
    """A very deeply nested chain of Form XObjects stops recursing at
    the hard depth backstop rather than blowing the stack."""
    pdf = _blank_pdf()
    page = pdf.add_blank_page()

    font = pdf.make_indirect(pikepdf.Dictionary(Subtype=pikepdf.Name("/Type3")))
    deepest_resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    current_xobj = pdf.make_indirect(
        pikepdf.Stream(pdf, b"", Subtype=pikepdf.Name("/Form"), Resources=deepest_resources)
    )
    # Chain depth well beyond _MAX_XOBJECT_RECURSION_DEPTH so the font at
    # the bottom is unreachable.
    for _ in range(20):
        wrapper_resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(X1=current_xobj))
        current_xobj = pdf.make_indirect(
            pikepdf.Stream(pdf, b"", Subtype=pikepdf.Name("/Form"), Resources=wrapper_resources)
        )

    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(X1=current_xobj))
    fonts = _iter_unique_font_objects(pdf)
    assert fonts == []


# ---------------------------------------------------------------------------
# patch_missing_to_unicode
# ---------------------------------------------------------------------------


def test_patch_missing_to_unicode_patches_type3_font():
    pdf = _blank_pdf()
    page = pdf.add_blank_page()
    font = _add_font_to_page(
        pdf,
        page,
        {
            "Subtype": pikepdf.Name("/Type3"),
            "Encoding": pikepdf.Dictionary(Differences=[65, pikepdf.Name("/A")]),
        },
    )
    patched = patch_missing_to_unicode(pdf)
    assert patched == 1
    assert "/ToUnicode" in font
    result = _resolve_via_to_unicode(font)
    assert result[0x41] == "A"


def test_patch_missing_to_unicode_skips_non_type3():
    pdf = _blank_pdf()
    page = pdf.add_blank_page()
    font = _add_font_to_page(
        pdf,
        page,
        {
            "Subtype": pikepdf.Name("/TrueType"),
            "Encoding": pikepdf.Name("/WinAnsiEncoding"),
        },
    )
    patched = patch_missing_to_unicode(pdf)
    assert patched == 0
    assert "/ToUnicode" not in font


def test_patch_missing_to_unicode_skips_font_with_existing_to_unicode():
    pdf = _blank_pdf()
    page = pdf.add_blank_page()
    original_cmap = compile_to_unicode_cmap({"41": "Q"})
    font = _add_font_to_page(
        pdf,
        page,
        {
            "Subtype": pikepdf.Name("/Type3"),
            "ToUnicode": pdf.make_stream(original_cmap),
        },
    )
    patched = patch_missing_to_unicode(pdf)
    assert patched == 0
    # The original mapping must be left untouched, not overwritten.
    assert _resolve_via_to_unicode(font) == {0x41: "Q"}


def test_patch_missing_to_unicode_leaves_unresolvable_font_alone():
    """A Type3 font with no /ToUnicode and no /Encoding information that
    resolves to anything (StandardEncoding always resolves something for
    a Simple font, so force a genuinely empty case with a symbolic-only
    encoding dict that carries neither /Differences nor /BaseEncoding,
    which itself still triggers the StandardEncoding path since
    /Encoding *object* is absent -- to hit the truly-empty branch, patch
    build_synthetic_to_unicode_map to return {})."""
    pdf = _blank_pdf()
    page = pdf.add_blank_page()
    font = _add_font_to_page(pdf, page, {"Subtype": pikepdf.Name("/Type3")})

    import pdftl.fonts.type3_tounicode_synthesis as mod

    original = mod.build_synthetic_to_unicode_map
    mod.build_synthetic_to_unicode_map = lambda font_obj: {}
    try:
        patched = patch_missing_to_unicode(pdf)
    finally:
        mod.build_synthetic_to_unicode_map = original

    assert patched == 0
    assert "/ToUnicode" not in font


def test_patch_missing_to_unicode_patches_font_nested_in_form_xobject():
    """Mirrors the real-world case: a Type3 font used only inside a Form
    XObject (e.g. a chart), with no /ToUnicode, must still be patched."""
    pdf = _blank_pdf()
    page = pdf.add_blank_page()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Subtype=pikepdf.Name("/Type3"),
            Encoding=pikepdf.Dictionary(Differences=[65, pikepdf.Name("/A")]),
        )
    )
    xobj_resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font))
    xobj = pdf.make_indirect(
        pikepdf.Stream(pdf, b"", Subtype=pikepdf.Name("/Form"), Resources=xobj_resources)
    )
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(X1=xobj))

    patched = patch_missing_to_unicode(pdf)
    assert patched == 1
    assert "/ToUnicode" in font


def test_patch_missing_to_unicode_no_fonts_at_all():
    pdf = _blank_pdf()
    pdf.add_blank_page()
    assert patch_missing_to_unicode(pdf) == 0


def test_patch_missing_to_unicode_empty_cmap_bytes_short_circuits():
    """If compile_to_unicode_cmap somehow yields empty bytes despite a
    non-empty char_map (defensive branch), no /ToUnicode is attached."""
    pdf = _blank_pdf()
    page = pdf.add_blank_page()
    font = _add_font_to_page(pdf, page, {"Subtype": pikepdf.Name("/Type3")})

    import pdftl.fonts.type3_tounicode_synthesis as mod

    original_build = mod.build_synthetic_to_unicode_map
    original_compile = mod.compile_to_unicode_cmap
    mod.build_synthetic_to_unicode_map = lambda font_obj: {0x41: "A"}
    mod.compile_to_unicode_cmap = lambda mappings: b""
    try:
        patched = patch_missing_to_unicode(pdf)
    finally:
        mod.build_synthetic_to_unicode_map = original_build
        mod.compile_to_unicode_cmap = original_compile

    assert patched == 0
    assert "/ToUnicode" not in font


def test_iter_unique_font_objects_direct_object():
    """A font that is a direct object (inline dict, no objgen) must not crash
    the deduping logic or be silently swallowed."""
    pdf = _blank_pdf()
    page = pdf.add_blank_page()

    font_dict = pikepdf.Dictionary(Subtype=pikepdf.Name("/Type3"))
    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font_dict))

    fonts = _iter_unique_font_objects(pdf)
    assert len(fonts) == 1


def test_resolve_via_to_unicode_malformed_cmap_content():
    """A /ToUnicode stream that can be read but contains malformed CMap data
    should fail gracefully rather than bubbling an unhandled exception."""
    pdf = _blank_pdf()
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            Subtype=pikepdf.Name("/Type3"),
            ToUnicode=pdf.make_stream(b"garbage not a cmap"),
        )
    )

    result = _resolve_via_to_unicode(font)
    assert result == {}


def test_resolve_via_encoding_dynamic_agl_names():
    """Tests resolution behavior for procedural AGL names like /uniXXXX or
    ligatures via fontTools.agl.toUnicode."""
    font = pikepdf.Dictionary(
        Subtype=pikepdf.Name("/Type3"),
        Encoding=pikepdf.Dictionary(
            Differences=[65, pikepdf.Name("/uni0041"), 66, pikepdf.Name("/f_i")]
        ),
    )
    result = _resolve_via_encoding_and_agl(font)

    assert result.get(0x41) == "A"
    assert result.get(0x42) == "fi"


def test_patch_missing_to_unicode_out_of_bounds_code():
    """Tests that a character code > 255 formats properly without crashing
    the CMap compiler."""
    import pdftl.fonts.type3_tounicode_synthesis as mod

    pdf = _blank_pdf()
    page = pdf.add_blank_page()
    _add_font_to_page(pdf, page, {"Subtype": pikepdf.Name("/Type3")})

    original_build = mod.build_synthetic_to_unicode_map
    mod.build_synthetic_to_unicode_map = lambda f: {256: "A"}

    try:
        patched = patch_missing_to_unicode(pdf)
        assert patched == 1
    finally:
        mod.build_synthetic_to_unicode_map = original_build


def test_resolve_via_encoding_contiguous_differences_array():
    """Tests that a contiguous /Differences array maps multiple glyphs incrementally."""
    font = pikepdf.Dictionary(
        Subtype=pikepdf.Name("/Type1"),
        Encoding=pikepdf.Dictionary(
            BaseEncoding=pikepdf.Name("/WinAnsiEncoding"),
            Differences=[65, pikepdf.Name("/A"), pikepdf.Name("/B"), pikepdf.Name("/C")],
        ),
    )
    result = _resolve_via_encoding_and_agl(font)

    assert result[0x41] == "A"
    assert result[0x42] == "B"
    assert result[0x43] == "C"
