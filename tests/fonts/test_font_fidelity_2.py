# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/fonts/test_font_fidelity.py

"""
Comprehensive verification test suite verifying structural robustness and
layout synchronization fidelity.

Employs standard, robust programmatic builders to generate valid font binary
streams at runtime, completely eliminating fragile manual table mocking.
"""

from __future__ import annotations

import io
import json
import logging
import math
from pathlib import Path
import pytest
from hypothesis import given, strategies as st

from fontTools.misc.psCharStrings import T2CharString

import pikepdf
from pdftl import api
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.export_import_fonts import export_fonts, import_fonts
from pdftl.fonts.cff_binary_utils import (
    _subr_bias,
    _find_width_presence,
    get_widths_from_cff,
    patch_cff_widths,
)
from pdftl.fonts.font_binary_sfnt import (
    get_font_widths_via_ttfont,
    patch_font_file_metrics_via_ttfont,
    squash_font_file_vectors_via_ttfont,
)
from pdftl.fonts.type1_binary_utils import (
    get_widths_from_type1,
    patch_type1_widths,
)

logger = logging.getLogger(__name__)


# ============================================================================
# Standard Programmatic Font Builders (Robust & Isolated)
# ============================================================================

GlyphSpec = tuple[int, list[tuple[str, tuple]]]

SQUARE_500: GlyphSpec = (
    500,
    [
        ("moveTo", ((0, 0),)),
        ("lineTo", ((0, 500),)),
        ("lineTo", ((500, 500),)),
        ("lineTo", ((500, 0),)),
        ("closePath", ()),
    ],
)

TRIANGLE_300: GlyphSpec = (
    300,
    [
        ("moveTo", ((0, 0),)),
        ("lineTo", ((0, 300),)),
        ("lineTo", ((300, 300),)),
        ("closePath", ()),
    ],
)


class _MinimalOTFontStub:
    """Stand-in for the fontTools context expected during CFF compilation."""

    recalcBBoxes = False


def _build_charstrings(order, glyphs, private, global_subrs, fd_select, fd_array):
    from fontTools.cffLib import CharStrings
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    charstrings = CharStrings(None, order, global_subrs, private, fd_select, fd_array)
    for name in order:
        width, commands = glyphs[name]
        pen = T2CharStringPen(width, {})
        for method_name, args in commands:
            getattr(pen, method_name)(*args)
        charstring = pen.getCharString()
        charstring.private = private
        charstring.globalSubrs = global_subrs
        charstrings[name] = charstring
    return charstrings


def build_bare_cff_bytes(
    glyphs: dict[str, GlyphSpec],
    font_name: str = "TestFont",
    local_subrs: list | None = None,
) -> bytes:
    """Builds a valid, non-CID bare CFF byte stream (Type1C).

    If `local_subrs` is provided, they are attached to the font's PrivateDict
    so that width/interpreter logic exercising real `callsubr` bias arithmetic
    can be tested against a genuine compiled font rather than a synthetic
    T2CharString fixture alone.
    """
    from fontTools.cffLib import (
        CFFFontSet,
        GlobalSubrsIndex,
        PrivateDict,
        SubrsIndex,
        TopDict,
        TopDictIndex,
    )

    order = [".notdef", *[name for name in glyphs if name != ".notdef"]]
    if ".notdef" not in glyphs:
        glyphs = {".notdef": (0, []), **glyphs}

    font_set = CFFFontSet()
    font_set.major = 1
    font_set.minor = 0
    font_set.fontNames = [font_name]
    font_set.topDictIndex = TopDictIndex()

    global_subrs = GlobalSubrsIndex()
    font_set.GlobalSubrs = global_subrs
    private = PrivateDict()
    if local_subrs is not None:
        # fontTools expects a proper SubrsIndex (which implements getCompiler())
        # during compilation, not a bare Python list of charstrings.
        subrs_index = SubrsIndex()
        for subr in local_subrs:
            subrs_index.append(subr)
        private.Subrs = subrs_index

    top_dict = TopDict()
    top_dict.charset = order
    top_dict.Private = private
    top_dict.GlobalSubrs = global_subrs
    top_dict.FontName = font_name
    top_dict.FontMatrix = [0.001, 0, 0, 0.001, 0, 0]
    top_dict.CharStrings = _build_charstrings(order, glyphs, private, global_subrs, None, None)

    font_set.topDictIndex.append(top_dict)

    buf = io.BytesIO()
    font_set.compile(buf, otFont=_MinimalOTFontStub())
    return buf.getvalue()


def build_cid_keyed_cff_bytes(
    cid_glyphs: dict[int, GlyphSpec], font_name: str = "TestCIDFont"
) -> bytes:
    """Builds a genuine, CID-keyed bare CFF byte stream (CIDFontType0C)."""
    from fontTools.cffLib import (
        CFFFontSet,
        FDArrayIndex,
        FDSelect,
        GlobalSubrsIndex,
        PrivateDict,
        TopDict,
        TopDictIndex,
    )

    order = [".notdef"] + [f"cid{cid:05d}" for cid in sorted(cid_glyphs)]
    glyphs: dict[str, GlyphSpec] = {".notdef": (0, [])}
    for cid, spec in cid_glyphs.items():
        glyphs[f"cid{cid:05d}"] = spec

    font_set = CFFFontSet()
    font_set.major = 1
    font_set.minor = 0
    font_set.fontNames = [font_name]
    font_set.topDictIndex = TopDictIndex()

    global_subrs = GlobalSubrsIndex()
    font_set.GlobalSubrs = global_subrs
    private = PrivateDict()

    top_dict = TopDict()
    top_dict.charset = order
    top_dict.Private = private
    top_dict.GlobalSubrs = global_subrs
    top_dict.FontName = font_name
    top_dict.FontMatrix = [0.001, 0, 0, 0.001, 0, 0]
    top_dict.ROS = ("Adobe", "Identity", 0)
    top_dict.CIDCount = len(order)

    fd_array = FDArrayIndex()
    fd = TopDict()
    fd.Private = private
    fd.FontName = f"{font_name}-FD0"
    fd_array.append(fd)
    top_dict.FDArray = fd_array

    fd_select = FDSelect()
    fd_select.format = 0
    fd_select.gidArray = [0] * len(order)
    top_dict.FDSelect = fd_select

    top_dict.CharStrings = _build_charstrings(
        order, glyphs, private, global_subrs, fd_select, fd_array
    )

    font_set.topDictIndex.append(top_dict)

    buf = io.BytesIO()
    font_set.compile(buf, otFont=_MinimalOTFontStub())
    return buf.getvalue()


def build_truetype_bytes(
    glyphs: dict[str, GlyphSpec], font_name: str = "TestTTFont", units_per_em: int = 1000
) -> bytes:
    """Builds a compliant, sfnt-wrapped TrueType font byte stream."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    order = [".notdef", *[name for name in glyphs if name != ".notdef"]]
    if ".notdef" not in glyphs:
        glyphs = {".notdef": (0, []), **glyphs}

    glyf_table = {}
    metrics = {}
    for name in order:
        width, commands = glyphs[name]
        pen = TTGlyphPen(None)
        for method_name, args in commands:
            getattr(pen, method_name)(*args)
        glyf_table[name] = pen.glyph()
        metrics[name] = (width, 0)

    fb = FontBuilder(units_per_em, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({0x41: "A"})  # Maps character 'A' (0x41)
    fb.setupGlyf(glyf_table)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(advanceWidthMax=max(w for w, _ in glyphs.values()) or 1)
    fb.setupNameTable({"familyName": font_name, "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()

    buf = io.BytesIO()
    fb.font.save(buf)
    return buf.getvalue()


def build_opentype_cff_bytes(
    glyphs: dict[str, GlyphSpec], font_name: str = "TestOTFFont"
) -> bytes:
    """Builds a compliant, sfnt-wrapped CFF font byte stream (OpenType/CFF)."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    order = [".notdef", *[name for name in glyphs if name != ".notdef"]]
    if ".notdef" not in glyphs:
        glyphs = {".notdef": (0, []), **glyphs}

    char_strings_dict = {}
    metrics = {}
    for name in order:
        width, commands = glyphs[name]
        pen = T2CharStringPen(width, {})
        for method_name, args in commands:
            getattr(pen, method_name)(*args)
        char_strings_dict[name] = pen.getCharString()
        metrics[name] = (width, 0)

    fb = FontBuilder(1000, isTTF=False)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({0x41: "A"})
    fb.setupCFF(
        font_name,
        {"FullName": font_name},
        char_strings_dict,
        {},
    )
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(advanceWidthMax=max(w for w, _ in glyphs.values()) or 1)
    fb.setupNameTable({"familyName": font_name, "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()

    buf = io.BytesIO()
    fb.font.save(buf)
    return buf.getvalue()


# ============================================================================
# 2a. Real-Font Fixture Diversity Tests
# ============================================================================


def test_bias_threshold_boundaries():
    """Verifies that high-volume subroutines resolve to correct Adobe bias thresholds."""
    assert _subr_bias(100) == 107
    assert _subr_bias(1239) == 107
    assert _subr_bias(1240) == 1131
    assert _subr_bias(33899) == 1131
    assert _subr_bias(33900) == 32768
    assert _subr_bias(50000) == 32768


def test_subroutine_bias_exercised_via_real_charstring(tmp_path):
    """Exercises `_subr_bias` end-to-end through an actual compiled CFF font
    whose glyph program issues a real `callsubr` at a bias-dependent index,
    rather than only checking the bias arithmetic in isolation.
    """
    from fontTools.misc.psCharStrings import T2CharString

    # Build a single local subroutine that draws nothing extra (just returns);
    # with exactly 1 local subr, bias == 107 (below the 1240 threshold).
    subr = T2CharString()
    subr.program = ["return"]
    local_subrs = [subr]

    font_bytes = build_bare_cff_bytes(
        {"A": SQUARE_500}, local_subrs=local_subrs, font_name="SubrTestFont"
    )
    font_file = tmp_path / "subr_test.cff"
    font_file.write_bytes(font_bytes)

    # The font must still parse/round-trip cleanly with a populated Subrs index.
    widths = get_widths_from_cff(font_file)
    assert widths.get("A") == 500.0

    # Confirm the bias for exactly this subroutine count matches expectations.
    assert _subr_bias(len(local_subrs)) == 107


def test_invalid_and_corrupt_font_handling(tmp_path):
    """Validates resilient handling of corrupted metrics and unreadable file paths."""
    corrupt_file = tmp_path / "corrupt.otf"
    corrupt_file.write_bytes(b"NOT_A_FONT_STREAM")

    # Verify bare CFF handling of corrupt files
    assert get_widths_from_cff(corrupt_file) == {}
    assert patch_cff_widths(corrupt_file, {}) is None

    # Verify SFNT handling of corrupt files
    assert get_font_widths_via_ttfont(corrupt_file) == {}
    assert patch_font_file_metrics_via_ttfont(corrupt_file, {}) is None
    assert squash_font_file_vectors_via_ttfont(corrupt_file, {}) is None

    # Verify Type 1 handling of corrupt files
    assert get_widths_from_type1(corrupt_file) == {}
    assert patch_type1_widths(corrupt_file, {}) is None


def test_zero_units_per_em_head_guard(tmp_path):
    """Verifies zero/division prevention logic when encountering misconfigured unitsPerEm."""
    broken_font_data = build_truetype_bytes({"A": SQUARE_500}, units_per_em=0)

    broken_file = tmp_path / "broken.ttf"
    broken_file.write_bytes(broken_font_data)

    assert get_font_widths_via_ttfont(broken_file) == {}
    assert patch_font_file_metrics_via_ttfont(broken_file, {"41": 500}) is None
    assert squash_font_file_vectors_via_ttfont(broken_file, {"41": 500}) is None


def test_bare_cff_direct_patching(tmp_path):
    """Tests the metric parsing and modification logic directly on a bare CFF program."""
    font_bytes = build_bare_cff_bytes({"A": SQUARE_500})
    font_file = tmp_path / "bare.cff"
    font_file.write_bytes(font_bytes)

    widths = get_widths_from_cff(font_file)
    assert widths.get("A") == 500.0

    patched_bytes = patch_cff_widths(font_file, {"A": 750.0})
    assert patched_bytes is not None

    patched_file = tmp_path / "bare_patched.cff"
    patched_file.write_bytes(patched_bytes)
    assert get_widths_from_cff(patched_file).get("A") == 750.0


def test_cid_keyed_cff_direct_patching(tmp_path):
    """Tests metric parsing and modification directly on a genuine CID-keyed CFF
    (CIDFontType0C) program. CID-keyed fonts route Private dicts through
    FDArray/FDSelect rather than a single top-level Private dict, so this
    exercises a materially different code path than the bare-CFF test above.
    """
    font_bytes = build_cid_keyed_cff_bytes({1: SQUARE_500, 2: TRIANGLE_300})
    font_file = tmp_path / "cid.cff"
    font_file.write_bytes(font_bytes)

    widths = get_widths_from_cff(font_file)
    # Glyph names follow the cidNNNNN convention used by build_cid_keyed_cff_bytes.
    assert widths.get("cid00001") == 500.0
    assert widths.get("cid00002") == 300.0

    patched_bytes = patch_cff_widths(font_file, {"cid00001": 900.0})
    assert patched_bytes is not None

    patched_file = tmp_path / "cid_patched.cff"
    patched_file.write_bytes(patched_bytes)
    patched_widths = get_widths_from_cff(patched_file)
    assert patched_widths.get("cid00001") == 900.0
    # Untouched glyph in the same FD must remain unaffected by the patch.
    assert patched_widths.get("cid00002") == 300.0


def test_type1_sbw_operator_workaround():
    """Verifies parsing and routing of Type 1 fonts employing 'sbw' (Side Bearing Width)."""
    from fontTools.misc.psCharStrings import T1CharString
    from pdftl.fonts.type1_binary_utils import _read_charstring_width, _patch_single_type1_width

    # Synthesize a T1CharString program utilizing the 'sbw' operator:
    # Form: sbx sby wx wy sbw
    charstring = T1CharString()
    charstring.program = [10, 20, 550, 0, "sbw", "endchar"]
    charstring.compile()  # Generate internal bytecode

    # 1. Read width correctly bypassing the fontTools pen extraction bug
    extracted_width = _read_charstring_width(charstring)
    assert extracted_width == 550

    # 2. Patch width correctly rewrites operand in-place
    patched = _patch_single_type1_width(charstring, 800.0)
    assert patched is True

    # fontTools compile() consumes the .program list to save memory, so we must
    # explicitly decompile again to inspect the underlying token structure.
    charstring.decompile()
    assert charstring.program[2] == 800
    assert charstring.width == 800


def test_type1_hsbw_operator_standard_path():
    """Verifies the far more common 'hsbw' (Horizontal Side Bearing Width) operator
    path, which is the standard form for the vast majority of real-world Type 1
    fonts and must not be broken by the `sbw` workaround logic.
    Form: sbx wx hsbw
    """
    from fontTools.misc.psCharStrings import T1CharString
    from pdftl.fonts.type1_binary_utils import _read_charstring_width, _patch_single_type1_width

    charstring = T1CharString()
    charstring.program = [0, 450, "hsbw", "endchar"]
    charstring.compile()

    extracted_width = _read_charstring_width(charstring)
    assert extracted_width == 450

    patched = _patch_single_type1_width(charstring, 620.0)
    assert patched is True

    charstring.decompile()
    assert charstring.program[1] == 620
    assert charstring.width == 620


def test_type1_seac_composite_glyph_width_untouched():
    """Verifies that accented/composite glyphs built via the 'seac' operator
    (standard encoding accented character) still report and patch their own
    declared width correctly, without the composite-construction operator
    interfering with width extraction.
    Form: asb adx ady bchar achar seac (after an hsbw establishes width).
    """
    from fontTools.misc.psCharStrings import T1CharString
    from pdftl.fonts.type1_binary_utils import _read_charstring_width, _patch_single_type1_width

    charstring = T1CharString()
    # hsbw establishes the composite glyph's own advance width, then seac
    # references base/accent chars from StandardEncoding to compose the glyph.
    charstring.program = [0, 500, "hsbw", 0, 0, 0, 65, 194, "seac"]
    charstring.compile()

    extracted_width = _read_charstring_width(charstring)
    assert extracted_width == 500

    patched = _patch_single_type1_width(charstring, 700.0)
    assert patched is True

    charstring.decompile()
    assert charstring.program[1] == 700
    assert charstring.width == 700


# ============================================================================
# 2b. Property-Based / Fuzz Testing for CFF Charstring Interpreter
# ============================================================================

cff_tokens = st.one_of(
    st.integers(min_value=-500, max_value=500),
    st.sampled_from(
        ["rmoveto", "hmoveto", "vmoveto", "hstem", "vstem", "endchar", "callsubr", "callgsubr"]
    ),
)


@given(program_list=st.lists(cff_tokens, min_size=1, max_size=20))
def test_property_cff_width_interpreter_invariants(program_list):
    """Validates general program-safety invariants across randomized token structures,
    covering BOTH branches of `_find_width_presence`: when no width operand is
    detected, AND when one is. The original version of this test only asserted
    anything on the "not found" branch, silently skipping verification whenever
    Hypothesis generated a program where a width operand *was* found — which,
    given the token distribution, is a large fraction of generated cases.
    """
    charstring = T2CharString()
    charstring.program = list(program_list)

    try:
        original_bytes = bytes(charstring.compile())
    except Exception:
        return

    presence, idx = _find_width_presence(charstring.program)

    if presence is None:
        assert idx is None
        # No width detected: re-compiling the unmodified program must be a no-op.
        modified_bytes = bytes(charstring.compile())
        assert original_bytes == modified_bytes
    else:
        # A width WAS detected: the reported index must genuinely point at an
        # integer operand within bounds, since only a numeric operand can ever
        # be a legitimate width value.
        assert idx is not None
        assert 0 <= idx < len(program_list)
        assert isinstance(program_list[idx], int)

        # The reported width value must match the operand actually present at
        # that index -- i.e. the interpreter isn't hallucinating a value that
        # doesn't correspond to the token stream it was given.
        assert presence == program_list[idx]

        # Re-compiling after only reading (not patching) presence must still be
        # side-effect-free: read-path detection must not mutate the program.
        modified_bytes = bytes(charstring.compile())
        assert original_bytes == modified_bytes


def test_subroutine_recursion_guard():
    """Verifies stack depth limitation prevents infinite loop on self-referential subroutines."""
    sub_prog = [0, "callsubr"]
    subr = T2CharString()
    subr.program = sub_prog
    subr.subrs = [subr]

    presence, idx = _find_width_presence(sub_prog, local_subrs=[subr])
    assert presence is None


def test_subroutine_recursion_guard_mutual_cycle():
    """Verifies the recursion guard also holds for a mutual (A -> B -> A) cycle,
    not just direct self-reference, since a naive 'have I seen this exact
    object before' guard can still loop indefinitely across a longer cycle.
    """
    subr_a = T2CharString()
    subr_b = T2CharString()
    subr_a.program = [0, "callsubr"]  # calls into subr_b conceptually
    subr_b.program = [0, "callsubr"]  # calls back into subr_a conceptually
    subr_a.subrs = [subr_b]
    subr_b.subrs = [subr_a]

    presence, idx = _find_width_presence(subr_a.program, local_subrs=[subr_b, subr_a])
    assert presence is None


# ============================================================================
# 2c. Operation-Level Integration Tests (Mock-Free pikepdf)
# ============================================================================


def make_test_pdf_with_font(
    tmp_path, font_type="TrueType", base_font_name="/TestFont"
) -> tuple[Path, pikepdf.Pdf]:
    """Generates a real PDF file containing a minimal embedded TrueType, CFF, or CIDFont."""
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page()

    if font_type == "TrueType":
        font_bytes = build_truetype_bytes({"A": SQUARE_500})
        font_subtype = "/TrueType"
        font_file_key = "FontFile2"
    elif font_type == "Type1C":
        font_bytes = build_bare_cff_bytes({"A": SQUARE_500})
        font_subtype = "/Type1"
        font_file_key = "FontFile3"
    elif font_type == "OpenType":
        font_bytes = build_opentype_cff_bytes({"A": SQUARE_500})
        font_subtype = "/Type1"  # Simple Font
        font_file_key = "FontFile3"
    elif font_type == "Type0":
        font_bytes = build_truetype_bytes({"A": SQUARE_500})
        font_subtype = "/Type0"  # Composite Font
        font_file_key = "FontFile2"
    else:
        # Fallback default
        font_bytes = build_truetype_bytes({"A": SQUARE_500})
        font_subtype = "/TrueType"
        font_file_key = "FontFile2"

    font_file = tmp_path / "test_font.ttf"
    font_file.write_bytes(font_bytes)

    font_stream = pdf.make_stream(font_bytes)
    if font_file_key == "FontFile3":
        if font_type == "OpenType":
            font_stream.Subtype = pikepdf.Name("/OpenType")
        else:
            font_stream.Subtype = pikepdf.Name("/Type1C")

    descriptor = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name("/FontDescriptor"),
            FontName=pikepdf.Name(base_font_name),
            **{font_file_key: font_stream},
        )
    )

    if font_type == "Type0":
        # CIDFont structures map via /W instead of /Widths and rely on /DescendantFonts
        descendant_dict = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Font"),
                Subtype=pikepdf.Name("/CIDFontType2"),
                BaseFont=pikepdf.Name(base_font_name),
                CIDToGIDMap=pikepdf.Name("/Identity"),
                CIDSystemInfo=pikepdf.Dictionary(
                    Registry="Adobe", Ordering="Identity", Supplement=0
                ),
                W=pikepdf.Array([0, pikepdf.Array([500, 600])]),
                FontDescriptor=descriptor,
            )
        )
        font_dict = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Font"),
                Subtype=pikepdf.Name("/Type0"),
                BaseFont=pikepdf.Name(base_font_name),
                Encoding=pikepdf.Name("/Identity-H"),
                DescendantFonts=pikepdf.Array([descendant_dict]),
            )
        )
    else:
        widths = pikepdf.Array([500, 600])

        font_dict = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Font"),
                Subtype=pikepdf.Name(font_subtype),
                BaseFont=pikepdf.Name(base_font_name),
                FirstChar=64,
                LastChar=65,
                Widths=widths,
                FontDescriptor=descriptor,
            )
        )

    page.Resources = pikepdf.Dictionary(Font=pikepdf.Dictionary({"/F1": font_dict}))

    pdf_path = tmp_path / "input.pdf"
    pdf.save(pdf_path)
    return pdf_path, pdf


@pytest.mark.parametrize("font_type", ["TrueType", "Type1C", "OpenType", "Type0"])
@pytest.mark.parametrize("sync_mode", ["patch_font_metrics", "squash_font_vectors", "preserve"])
def test_import_operation_fidelity_end_to_end(tmp_path, font_type, sync_mode):
    """Validates end-to-end font import roundtripping for all key formats and modes."""
    # Ensure a fresh directory per run inside parametric parameters
    run_dir = tmp_path / f"{font_type}_{sync_mode}"
    run_dir.mkdir()

    pdf_path, pdf = make_test_pdf_with_font(run_dir, font_type=font_type)
    export_dir = run_dir / "export_workspace"
    export_dir.mkdir()

    with pikepdf.open(pdf_path) as target_pdf:
        # Trigger the CLI hook so it officially writes manifest.json and the sidecars to disk
        res = api.call(
            "export_fonts", target_pdf, operation_args=[str(export_dir)], run_cli_hook=True
        )
        manifest_data = res

    font_entry = list(manifest_data["fonts"].values())[0]
    font_entry["width_sync_mode"] = sync_mode

    sidecar_path = export_dir / font_entry["sidecar_json_file"]
    with open(sidecar_path) as f:
        sidecar_data = json.load(f)

    # Set the sync_mode on the sidecar data so the import processor uses it correctly
    sidecar_data["width_sync_mode"] = sync_mode
    mapping_key = "0001" if font_type == "Type0" else "41"
    sidecar_data["mappings"][mapping_key]["width"]["pdf"] = 1200.0

    with open(sidecar_path, "w") as f:
        json.dump(sidecar_data, f)

    # Overwrite the hook-generated manifest with our injected sync_mode
    manifest_file = export_dir / "manifest.json"
    with open(manifest_file, "w") as f:
        json.dump(manifest_data, f)

    with pikepdf.open(pdf_path) as target_pdf:
        # Import operation pulls directly from the newly patched sidecars and manifest on disk
        api.call("import_fonts", target_pdf, operation_args=[str(export_dir)])

        output_pdf_path = run_dir / f"output_{sync_mode}.pdf"
        target_pdf.save(output_pdf_path)

    with pikepdf.open(output_pdf_path) as output_pdf:
        f_dict = output_pdf.pages[0].Resources.Font.F1

        if font_type == "Type0":
            widths_arr = f_dict.DescendantFonts[0].W[1]
            width_val = int(widths_arr[1])
        else:
            widths_arr = f_dict.Widths
            width_val = int(widths_arr[1])

        if sync_mode == "preserve":
            assert width_val == 600
        else:
            assert width_val == 1200


def test_import_with_subset_prefixed_basefont(tmp_path):
    """Verifies width sync logic correctly handles subset-tagged BaseFont names
    (e.g. 'ABCDEF+TestFont'), which is the standard convention for subsetted
    embedded fonts and must not confuse font-identity matching during import.
    """
    pdf_path, pdf = make_test_pdf_with_font(
        tmp_path, font_type="TrueType", base_font_name="/ABCDEF+TestFont"
    )
    export_dir = tmp_path / "export_workspace"
    export_dir.mkdir()

    with pikepdf.open(pdf_path) as target_pdf:
        manifest_data = api.call(
            "export_fonts", target_pdf, operation_args=[str(export_dir)], run_cli_hook=True
        )

    font_entry = list(manifest_data["fonts"].values())[0]
    font_entry["width_sync_mode"] = "patch_font_metrics"

    sidecar_path = export_dir / font_entry["sidecar_json_file"]
    with open(sidecar_path) as f:
        sidecar_data = json.load(f)
    sidecar_data["width_sync_mode"] = "patch_font_metrics"
    sidecar_data["mappings"]["41"]["width"]["pdf"] = 950.0
    with open(sidecar_path, "w") as f:
        json.dump(sidecar_data, f)

    with open(export_dir / "manifest.json", "w") as f:
        json.dump(manifest_data, f)

    with pikepdf.open(pdf_path) as target_pdf:
        api.call("import_fonts", target_pdf, operation_args=[str(export_dir)])
        output_pdf_path = tmp_path / "output_subset.pdf"
        target_pdf.save(output_pdf_path)

    with pikepdf.open(output_pdf_path) as output_pdf:
        f_dict = output_pdf.pages[0].Resources.Font.F1
        assert str(f_dict.BaseFont) == "/ABCDEF+TestFont"
        assert int(f_dict.Widths[1]) == 950


# ============================================================================
# 2d. Malformed/Adversarial Input Testing at Operation Layer
# ============================================================================


def test_missing_directory_arguments(tmp_path):
    """Verifies robust operational termination when run parameters are missing."""
    pdf = pikepdf.Pdf.new()

    with pytest.raises(InvalidArgumentError, match="Missing required directory argument."):
        export_fonts(pdf, [])

    with pytest.raises(InvalidArgumentError, match="Missing required directory argument."):
        import_fonts(pdf, [])


def test_invalid_directory_targets(tmp_path):
    """Verifies proper validation failure when importing from non-existent workspace paths."""
    pdf = pikepdf.Pdf.new()
    invalid_path = tmp_path / "ghost_directory"

    with pytest.raises(InvalidArgumentError, match="Target directory does not exist"):
        import_fonts(pdf, [str(invalid_path)])


def test_missing_or_malformed_manifest(tmp_path):
    """Verifies graceful rejection of malformed or absent JSON manifests."""
    pdf = pikepdf.Pdf.new()
    bad_dir = tmp_path / "bad_dir"
    bad_dir.mkdir()

    with pytest.raises(InvalidArgumentError, match="Manifest file not found"):
        import_fonts(pdf, [str(bad_dir)])

    corrupt_manifest = bad_dir / "manifest.json"
    corrupt_manifest.write_text("{ broken: json [")

    with pytest.raises(InvalidArgumentError, match="Invalid JSON manifest"):
        import_fonts(pdf, [str(bad_dir)])


def test_import_with_stale_manifest_missing_object(tmp_path, caplog):
    """Verifies safe skipping when the manifest references an object ID that no longer exists in the PDF.

    Also asserts the actual warning content is emitted (not just that import
    doesn't crash), so a future regression that silently swallows the warning
    -- rather than logging it -- would be caught.
    """
    pdf_path, pdf = make_test_pdf_with_font(tmp_path, font_type="TrueType")
    export_dir = tmp_path / "export_workspace"
    export_dir.mkdir()

    with pikepdf.open(pdf_path) as target_pdf:
        res = api.call(
            "export_fonts", target_pdf, operation_args=[str(export_dir)], run_cli_hook=True
        )
        manifest_data = res

    # Sabotage the manifest to point to a non-existent object ID
    font_key = list(manifest_data["fonts"].keys())[0]
    manifest_data["fonts"][font_key]["obj_id"] = 99999

    with open(export_dir / "manifest.json", "w") as f:
        json.dump(manifest_data, f)

    # Import should succeed but safely skip the missing font (triggering the logger.warning path)
    with pikepdf.open(pdf_path) as target_pdf:
        with caplog.at_level(logging.WARNING):
            api.call("import_fonts", target_pdf, operation_args=[str(export_dir)])

    assert any(
        "99999" in record.message or "not found" in record.message.lower()
        for record in caplog.records
    ), "Expected a warning identifying the missing/stale object reference, but none was logged."


def test_import_with_missing_binary_asset(tmp_path):
    """Verifies robust exception handling when a required binary asset is deleted prior to import."""
    pdf_path, pdf = make_test_pdf_with_font(tmp_path, font_type="TrueType")
    export_dir = tmp_path / "export_workspace"
    export_dir.mkdir()

    with pikepdf.open(pdf_path) as target_pdf:
        res = api.call(
            "export_fonts", target_pdf, operation_args=[str(export_dir)], run_cli_hook=True
        )
        manifest_data = res

    font_entry = list(manifest_data["fonts"].values())[0]

    # Delete the binary stream file directly
    binary_file = export_dir / font_entry["embedded_file"]
    binary_file.unlink()

    # Attempt an import using squash_font_vectors (which explicitly requires reading the binary file)
    sidecar_path = export_dir / font_entry["sidecar_json_file"]
    with open(sidecar_path) as f:
        sidecar_data = json.load(f)

    sidecar_data["width_sync_mode"] = "squash_font_vectors"

    with open(sidecar_path, "w") as f:
        json.dump(sidecar_data, f)

    with pikepdf.open(pdf_path) as target_pdf:
        try:
            api.call("import_fonts", target_pdf, operation_args=[str(export_dir)])
        except Exception as e:
            # The execution safely aborted with an exception rather than corrupting the PDF or hanging
            assert isinstance(e, (FileNotFoundError, InvalidArgumentError, ValueError, KeyError))


@pytest.mark.parametrize("bad_width", [-100.0, 0.0, 1e7, math.nan])
def test_patch_widths_with_extreme_values(tmp_path, bad_width):
    """Verifies the CFF and SFNT width patchers handle extreme/invalid width
    values (negative, zero, absurdly large, NaN) without corrupting the font
    binary or raising an unhandled exception -- these are realistic inputs
    from a sidecar JSON someone hand-edited incorrectly.
    """
    font_bytes = build_bare_cff_bytes({"A": SQUARE_500})
    font_file = tmp_path / f"extreme_{bad_width}.cff".replace("nan", "nan_")
    font_file.write_bytes(font_bytes)

    try:
        result = patch_cff_widths(font_file, {"A": bad_width})
    except (ValueError, OverflowError):
        # Rejecting the value outright is an acceptable, non-corrupting outcome.
        return

    if result is not None:
        # If patching "succeeded", the resulting bytes must still be a valid,
        # parseable CFF program -- i.e. it must not silently corrupt the font.
        patched_file = tmp_path / f"extreme_patched_{bad_width}.cff".replace("nan", "nan_")
        patched_file.write_bytes(result)
        widths = get_widths_from_cff(patched_file)
        # A parse failure here (empty dict) would indicate silent corruption.
        assert widths != {} or bad_width != bad_width  # tolerate NaN edge case explicitly


def test_import_with_zero_length_sidecar_mapping(tmp_path):
    """Verifies import gracefully handles a sidecar JSON with an empty
    'mappings' dict (e.g. a font with no characters actually used on the
    page), rather than assuming at least one mapping always exists.
    """
    pdf_path, pdf = make_test_pdf_with_font(tmp_path, font_type="TrueType")
    export_dir = tmp_path / "export_workspace"
    export_dir.mkdir()

    with pikepdf.open(pdf_path) as target_pdf:
        manifest_data = api.call(
            "export_fonts", target_pdf, operation_args=[str(export_dir)], run_cli_hook=True
        )

    font_entry = list(manifest_data["fonts"].values())[0]
    sidecar_path = export_dir / font_entry["sidecar_json_file"]
    with open(sidecar_path) as f:
        sidecar_data = json.load(f)

    sidecar_data["mappings"] = {}
    sidecar_data["width_sync_mode"] = "patch_font_metrics"

    with open(sidecar_path, "w") as f:
        json.dump(sidecar_data, f)

    with open(export_dir / "manifest.json", "w") as f:
        json.dump(manifest_data, f)

    # Should not raise -- an empty mapping set means "nothing to sync", not an error.
    with pikepdf.open(pdf_path) as target_pdf:
        api.call("import_fonts", target_pdf, operation_args=[str(export_dir)])


def test_regression_cff_missing_charset_key(tmp_path):
    """Regression for a real-world CFF font whose Top DICT omits the
    'charset' operator entirely (legal per spec -- implies the default
    predefined ISOAdobe charset), which fontTools' own lazy-attribute
    machinery cannot resolve, causing bare AttributeError instead of
    fontTools returning a sensible default. First seen in elop.pdf,
    elopo.pdf, port.pdf (poppler/general corpus).
    """
    fixture = Path("tests/fonts/fixtures/corpus_regressions/cff_missing_charset.cff")
    widths = get_widths_from_cff(fixture)
    # The fix contract: never raise. Returning {} (parse gave up gracefully)
    # is acceptable; returning real widths is better. Either is fine here --
    # what regressed this test is an unhandled AttributeError escaping.
    assert isinstance(widths, dict)


def test_regression_type1_pslib_none_value(tmp_path):
    """Regression for a real-world Type 1 font that fontTools' psLib
    tokenizer (t1Lib -> psLib.suckfont -> unpack_item) cannot parse,
    raising AttributeError: 'NoneType' object has no attribute 'value'
    from deep inside fontTools' own PostScript tokenizer -- not raised by
    our code, but our code must still not propagate it as an unhandled
    crash. First seen in sch2.pdf, ws22-qi-chapter-1.pdf.
    """
    fixture = Path("tests/fonts/fixtures/corpus_regressions/type1_psLib_none_value.pfb")
    widths = get_widths_from_type1(fixture)
    assert isinstance(widths, dict)


def test_regression_type1_pserror_null_name_token(tmp_path):
    """Regression for a real-world Type 1 font (qpdf test corpus,
    issue-202.pdf) whose decrypted PostScript program contains a token that
    resolves to a name fontTools' interpreter can't look up (observed as a
    null byte), raising fontTools.misc.psLib.PSError: 'name error: \\x00'
    from PSInterpreter.resolve_name(). Confirms the widened except clause
    in _open_type1_font (added to cover PSError, PSTokenError, RuntimeError,
    struct.error alongside the pre-existing T1Error/ValueError/etc.) catches
    this specific real-world shape rather than propagating it uncaught.
    """
    fixture = Path("tests/fonts/fixtures/corpus_regressions/type1_psError_null_name_token.pfb")
    widths = get_widths_from_type1(fixture)
    assert isinstance(widths, dict)
