# tests/operations/test_subset_fonts_shared_stream.py

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
Regression test for a real bug found visually inspecting subset_fonts's
output on a real-world PDF: several /Font dictionaries -- each with its
own /Encoding, subsetting a shared symbol font differently per usage --
pointed at the exact same underlying /FontFile stream object. The
original implementation subsetted and rewrote that shared stream once
per /Font dictionary that referenced it, so whichever dictionary was
processed last silently clobbered every earlier dictionary's glyphs
(e.g. a summation sign one dictionary needed vanished because a later
dictionary sharing the same physical font only needed different
glyphs).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pikepdf

sys.path.insert(0, str(Path(__file__).parent.parent / "fonts" / "fixtures"))
from type1_fixture_builder import build_type1_bytes  # noqa: E402

from pdftl.fonts.font_subsetting import wrap_bare_cff_in_sfnt
from pdftl.operations.subset_fonts import subset_fonts


def _make_shared_type1_pdf() -> pikepdf.Pdf:
    """
    Two /Font dictionaries, /F1 and /F2, both Simple Type 1 fonts with
    DIFFERENT /Differences encodings, but sharing the exact same
    underlying /FontDescriptor/FontFile stream object -- the pattern
    found in the real PDF this was found against (one physical symbol
    font, several /Font dicts each remapping a different subset of its
    codes). /F1's content only paints a code that resolves (via /F1's
    own /Differences) to glyph 'A'; /F2's content only paints a code
    that resolves (via /F2's own, different /Differences) to glyph 'B'.
    """
    t1_bytes = build_type1_bytes(
        {
            ".notdef": (0, ["endchar"]),
            "A": (500, [0, 0, "rmoveto", 500, 0, "rlineto", 500, 500, "rlineto"]),
            "B": (300, [0, 0, "rmoveto", 300, 0, "rlineto"]),
        }
    )

    pdf = pikepdf.new()
    font_file = pikepdf.Stream(pdf, t1_bytes)
    font_file["/Length1"] = len(t1_bytes)

    descriptor = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name("/SharedSymbol"),
                "/Flags": 4,
                "/FontBBox": [0, 0, 1000, 1000],
                "/ItalicAngle": 0,
                "/Ascent": 750,
                "/Descent": -250,
                "/CapHeight": 700,
                "/StemV": 80,
                "/FontFile": font_file,
            }
        )
    )

    def make_font(code_for_glyph_a_or_b: int, glyph_name: str) -> pikepdf.Object:
        return pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Font"),
                    "/Subtype": pikepdf.Name("/Type1"),
                    "/BaseFont": pikepdf.Name("/SharedSymbol"),
                    "/FirstChar": code_for_glyph_a_or_b,
                    "/LastChar": code_for_glyph_a_or_b,
                    "/Widths": [500],
                    "/Encoding": pikepdf.Dictionary(
                        {
                            "/Differences": [
                                code_for_glyph_a_or_b,
                                pikepdf.Name(f"/{glyph_name}"),
                            ]
                        }
                    ),
                    "/FontDescriptor": descriptor,
                }
            )
        )

    font_f1 = make_font(10, "A")
    font_f2 = make_font(20, "B")

    page = pdf.add_blank_page()
    page["/Resources"] = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": font_f1, "/F2": font_f2})}
    )
    # /F1 paints code 10 (-> glyph A), /F2 paints code 20 (-> glyph B)
    content = bytes([10]) + b" ignored"  # placeholder, real content built below
    content = b"/F1 12 Tf <0A> Tj /F2 12 Tf <14> Tj"
    page["/Contents"] = pikepdf.Stream(pdf, content)
    return pdf


def test_shared_font_program_keeps_glyphs_from_all_font_dicts():
    pdf = _make_shared_type1_pdf()

    result = subset_fonts(pdf, [])
    assert result.success

    font_f1 = pdf.pages[0]["/Resources"]["/Font"]["/F1"]
    font_f2 = pdf.pages[0]["/Resources"]["/Font"]["/F2"]
    desc_f1 = font_f1["/FontDescriptor"]
    desc_f2 = font_f2["/FontDescriptor"]

    # Both /Font dicts still reference a (now-converted) FontFile3.
    assert "/FontFile3" in desc_f1
    assert "/FontFile3" in desc_f2

    cff_bytes = desc_f1["/FontFile3"].read_bytes()
    tt = wrap_bare_cff_in_sfnt(cff_bytes)
    glyph_names = set(tt.getGlyphOrder())

    # The regression: without unioning across /Font dicts that share one
    # physical stream, whichever dict is processed last wins and the
    # other's glyph is silently missing.
    assert "A" in glyph_names, "glyph needed by /F1 was clobbered by /F2's subsetting"
    assert "B" in glyph_names, "glyph needed by /F2 was clobbered by /F1's subsetting"
