# tests/fonts/test_font_encoding_tables.py

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Regression test for a real bug found running subset_fonts against a
real PDF: _expand_differences_map's old `isinstance(item, str)` check
silently missed pikepdf.Name entries (not a str subclass), leaving a
leading "/" in every resolved glyph name and making /Differences-based
glyph resolution a de facto no-op for any live-PDF /Differences array."""

from __future__ import annotations

import pikepdf

from pdftl.fonts.font_encoding_tables import _expand_differences_map


def test_expand_differences_map_strips_slash_from_str_names():
    result = _expand_differences_map([65, "/A", "/B"])
    assert result == {"41": "A", "42": "B"}


def test_expand_differences_map_strips_slash_from_pikepdf_name():
    """The regression: pikepdf.Name (as read live off a real PDF, unlike
    a manifest's plain strings) must resolve to a bare glyph name too."""
    result = _expand_differences_map([65, pikepdf.Name("/A"), pikepdf.Name("/B")])
    assert result == {"41": "A", "42": "B"}
    for name in result.values():
        assert not name.startswith("/")


# --- append to tests/fonts/test_font_encoding_tables.py ---

from pdftl.fonts.font_encoding_tables import (
    _build_codec_based_encoding_table,
    _get_base_encoding_table,
)


def test_build_codec_based_encoding_table_still_correct_for_ascii():
    """_build_codec_based_encoding_table is dead code now (WinAnsi and
    MacRoman both moved off it), but it's still present in the module --
    this locks in its behavior directly: ASCII resolves correctly, and
    it reproduces the exact gap that motivated moving off it (ligature
    codepoints absent from fontTools' AGLFN-derived reverse map)."""
    table = _build_codec_based_encoding_table("cp1252")
    assert table[ord("A")] == "A"
    assert table[ord("0")] == "zero"
    # Control codes have no printable glyph -> absent.
    assert 0x00 not in table
    # WinAnsi code 0xA0 (nbspace) decodes via cp1252 fine, but its
    # codepoint (U+00A0) not being present in AGLFN's reverse map -- this
    # was one of the confirmed real gaps in the codec+AGL approach.
    assert 0xA0 not in table


def test_build_codec_based_encoding_table_macroman_ligature_gap():
    """mac_roman correctly decodes 0xDE -> U+FB01 ('fi' ligature), but
    that codepoint is absent from fontTools.agl.UV2AGL (AGLFN excludes
    legacy compatibility ligatures) -- so it's absent from the built
    table. This is the exact bug this function used to cause when it
    was still wired up for MacRomanEncoding."""
    table = _build_codec_based_encoding_table("mac_roman")
    assert 0xDE not in table
    assert 0xDF not in table


def test_macexpert_encoding_is_registered_and_cached():
    """MacExpertEncoding must be reachable through the same public
    entrypoint as the other three named encodings, and _get_base_encoding_table's
    @cache must return the identical dict object on repeat calls."""
    first = _get_base_encoding_table("MacExpertEncoding")
    second = _get_base_encoding_table("MacExpertEncoding")
    assert first is second
    assert len(first) > 0


def test_macexpert_encoding_table_resolves_expert_glyphs():
    table = _get_base_encoding_table("MacExpertEncoding")
    assert table[0x20] == "space"
    assert table[0x56] == "ff"
    assert table[0x57] == "fi"
    assert table[0x58] == "fl"
    assert table[0x59] == "ffi"
    assert table[0x5A] == "ffl"
    assert table[0xDA] == "onesuperior"
    # Undefined codes stay absent, not filled with a fallback (unlike
    # WinAnsi's "bullet" convention -- MacExpertEncoding has no such rule).
    assert 0x00 not in table
    assert 0x3C not in table  # gap inside the printable range
