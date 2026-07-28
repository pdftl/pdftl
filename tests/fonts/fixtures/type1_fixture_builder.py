# tests/fonts/fixtures/type1_fixture_builder.py

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/fonts/fixtures/type1_fixture_builder.py

"""
Builds a genuine, real Type 1 font program via fontTools.t1Lib.T1Font's
own createData()/encode_eexec() writer, for use as a from-scratch fixture
in tests/fonts/test_type1_binary_utils.py's real (non-mocked) round-trip
tests.

There is no public "build a T1Font from scratch" constructor -- T1Font is
normally only ever populated by parsing an existing file's bytes. This
module instead builds the `font` dict directly, in exactly the shape
T1Font.createData() (see fontTools.t1Lib source) expects, and hands it to
a bare T1Font instance's own real serializer. The only fontTools-internal
knowledge this relies on (RD/ND/NP being detected by *value* match, not by
key name; Private needing Subrs/lenIV; CharStrings needing real
psCharStrings.T1CharString objects) was read directly out of the installed
fontTools source rather than assumed.
"""

from __future__ import annotations


def _build_charstring(program: list):
    """
    Builds a genuine fontTools.misc.psCharStrings.T1CharString from a
    plain operand/operator program list (the exact shape
    type1_binary_utils.py's _find_width_operator/_read_charstring_width
    already read/patch), so createData()'s own char_bin.compile() call
    produces real, valid Type 1 bytecode.
    """
    from fontTools.misc.psCharStrings import T1CharString

    cs = T1CharString()
    cs.program = list(program)
    return cs


def build_type1_font_dict(
    glyphs: dict[str, tuple[int, list]],
    encoding_overrides: dict[int, str] | None = None,
    font_matrix: list | None = None,
) -> dict:
    """
    Builds a real T1Font.font dict for one or more glyphs.

    `glyphs` maps glyph name -> (advance_width, path_program), where
    path_program is the charstring's operand/operator list *after* its
    leading width operator, e.g. [0, 0, "rmoveto", "endchar"]. The leading
    `sbx wx hsbw` operand triple is prepended automatically from
    `advance_width`, matching every real Type 1 glyph's required first
    command (Adobe Type 1 Font Format, Chapter 8).

    `encoding_overrides`, if given, maps code -> glyph name entries to
    lay on top of StandardEncoding -- e.g. for a symbol/math font whose
    built-in encoding assigns a non-standard glyph to a code
    StandardEncoding otherwise leaves as .notdef (as real math fonts
    like STIXMath-Italic do), needed to build a fixture for
    resolve_code_to_glyph_names/build_cff_from_glyph_names's Encoding
    preservation.

    `font_matrix`, if given, overrides the default unsheared
    [0.001, 0, 0, 0.001, 0, 0] -- e.g. a sheared matrix like
    [0.001, 0, 0.001*tan(angle), 0.001, 0, 0] to build a fixture for a
    synthetic-italic/oblique font that achieves its slant purely via
    /FontMatrix rather than by redrawing outlines (a real-world pattern
    seen in "...-Slant_NNN" style font names).
    """
    from fontTools.encodings.StandardEncoding import StandardEncoding

    charstrings = {}
    for name, (width, path_program) in glyphs.items():
        program = [0, width, "hsbw", *path_program, "endchar"]
        charstrings[name] = _build_charstring(program)

    encoding = list(StandardEncoding)
    if encoding_overrides:
        for code, name in encoding_overrides.items():
            encoding[code] = name

    private = {
        "RD": ("string", "currentfile", "exch", "readstring", "pop"),
        "ND": ("def",),
        "NP": ("put",),
        "Subrs": [],
        "lenIV": 4,
    }

    return {
        "FontName": "PdftlTestFont",
        "FontType": 1,
        "FontMatrix": font_matrix if font_matrix is not None else [0.001, 0, 0, 0.001, 0, 0],
        # T1Font._make_lines special-cases Encoding by *identity/equality*
        # against the real StandardEncoding list object (see
        # fontTools.t1Lib._make_lines), not the string "StandardEncoding" --
        # passing the real list here (when unmodified) writes the compact
        # "/Encoding StandardEncoding def" form instead of a full
        # 256-entry array. Once encoding_overrides is applied, `encoding`
        # is a plain (no-longer-identical) list, so a full array is
        # written instead -- both are equally valid Type 1 output.
        "Encoding": encoding if encoding_overrides else StandardEncoding,
        "Private": private,
        "CharStrings": charstrings,
    }


def build_type1_bytes(
    glyphs: dict[str, tuple[int, list]],
    encoding_overrides: dict[int, str] | None = None,
    font_matrix: list | None = None,
) -> bytes:
    """
    Builds a genuine, real Type 1 (/FontFile-shaped, plain non-PFB) byte
    stream via T1Font's own createData(), for use in an on-disk fixture.
    """
    from fontTools.t1Lib import T1Font

    font = T1Font.__new__(T1Font)
    font.encoding = "ascii"
    font.font = build_type1_font_dict(glyphs, encoding_overrides, font_matrix)
    return font.createData()
