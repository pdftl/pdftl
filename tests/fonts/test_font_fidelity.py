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
from io import BytesIO
from pathlib import Path
import pytest
from hypothesis import given, strategies as st

from fontTools.misc.psCharStrings import T2CharString
from fontTools.ttLib import TTFont, newTable

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


def build_bare_cff_bytes(glyphs: dict[str, GlyphSpec], font_name: str = "TestFont") -> bytes:
    """Builds a valid, non-CID bare CFF byte stream (Type1C)."""
    from fontTools.cffLib import CFFFontSet, GlobalSubrsIndex, PrivateDict, TopDict, TopDictIndex

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


def build_cid_keyed_cff_bytes_with_fdselect_format3(
    cid_glyphs: dict[int, GlyphSpec], font_name: str = "TestCIDFontF3"
) -> bytes:
    """Builds a bare, CID-keyed CFF byte stream employing an FDSelect Format 3 structure."""
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

    # Set up FDSelect Format 3 structure
    fd_select = FDSelect()
    fd_select.format = 3
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


def test_shared_subroutine_safety_guard():
    """
    Verifies that _find_width_presence detects shared subroutines correctly and returns
    'undeterminable' to protect against font file corruption.
    """
    # Build a program where the width definition resides inside a local subroutine.
    # Subroutine 0: [100, 200, 300, 'rmoveto', 'endchar'] (shared width definition)
    subr_shared = T2CharString()
    subr_shared.program = [100, 200, 300, "rmoveto", "endchar"]

    # Glyph program: [-107, 'callsubr'] (calls Subroutine 0, -107 accounts for len(subrs)=1 bias of 107)
    glyph_program = [-107, "callsubr"]

    # Analyze the calling program with the compiled subr list
    presence, idx = _find_width_presence(glyph_program, local_subrs=[subr_shared])

    # Invariant: Since the width operand only exists inside a shared subroutine
    # and was not pushed in the calling glyph's own program, it must be resolved as
    # undeterminable (None, None) to prevent Cow subroutine rewrite corruption.
    assert presence is None
    assert idx is None

    # Contrasting case: Glyph has its own local width preceding the delegation:
    # Subroutine 0: [200, 300, 'rmoveto', 'endchar'] (only coordinates, no width in subr)
    subr_local = T2CharString()
    subr_local.program = [200, 300, "rmoveto", "endchar"]

    # Program: [100, -107, 'callsubr'] (with width=100 pushed locally, then calling Subroutine 0)
    local_width_program = [100, -107, "callsubr"]
    presence_local, idx_local = _find_width_presence(local_width_program, local_subrs=[subr_local])
    assert presence_local is True
    assert idx_local == 0


def test_cff2_variable_font_safety(tmp_path, monkeypatch):
    """
    Verifies that our sfnt dispatcher handles CFF2 variable fonts safely,
    degrading metrics patching and vector-squashing gracefully to metrics-only
    table adjustments rather than crashing.
    """
    # Create an sfnt font containing a dummy CFF2 table in place of a CFF table
    tt_bytes = build_truetype_bytes({"A": SQUARE_500})
    tt = TTFont(BytesIO(tt_bytes))
    del tt["glyf"]
    tt["CFF2"] = newTable("CFF2")
    tt.recalcBBoxes = False

    # Override compiling of CFF2 to bypass deep CFFFontSet initialization/compilation dependencies
    tt["CFF2"].compile = lambda ttFont: b"\x02\x00\x05\x00\x00"

    # Monkeypatch TTFont to always set recalcBBoxes=False on load to bypass head table bounds recalculations
    orig_init = TTFont.__init__

    def dummy_init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        self.recalcBBoxes = False

    monkeypatch.setattr(TTFont, "__init__", dummy_init)

    stream = BytesIO()
    tt.save(stream)

    font_file = tmp_path / "variable_cff2.otf"
    font_file.write_bytes(stream.getvalue())

    # 1. Verification: metrics load succeeds safely
    widths = get_font_widths_via_ttfont(font_file)
    assert widths.get("41") == 500.0

    # 2. Verification: metrics patching updates the hmtx table and returns updated bytes
    patched = patch_font_file_metrics_via_ttfont(font_file, {"41": 800.0})
    assert patched is not None

    # 3. Verification: vector squashing on CFF2 degrades gracefully to metrics-only table patch
    squashed = squash_font_file_vectors_via_ttfont(font_file, {"41": 800.0})
    assert squashed is not None


def test_cid_keyed_fdselect_format3_cff(tmp_path):
    """
    Verifies that get_widths_from_cff and patch_cff_widths can cleanly parse and
    update bare CID-keyed CFF programs employing FDSelect Format 3 structures.
    """
    font_bytes = build_cid_keyed_cff_bytes_with_fdselect_format3({1: SQUARE_500})
    font_file = tmp_path / "format3.cff"
    font_file.write_bytes(font_bytes)

    # 1. Read width successfully (mapped by hex CID)
    widths = get_widths_from_cff(font_file, cid_to_gid_map="cff_native")
    assert widths.get("0001") == 500.0

    # 2. Patch width successfully
    patched_bytes = patch_cff_widths(font_file, {"0001": 700.0}, cid_to_gid_map="cff_native")
    assert patched_bytes is not None

    patched_file = tmp_path / "format3_patched.cff"
    patched_file.write_bytes(patched_bytes)
    assert get_widths_from_cff(patched_file, cid_to_gid_map="cff_native").get("0001") == 700.0


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
    """Validates general program-safety invariants across randomized token structures."""
    charstring = T2CharString()
    charstring.program = program_list

    try:
        original_bytes = bytes(charstring.compile())
    except Exception:
        return

    presence, idx = _find_width_presence(charstring.program)

    if presence is None:
        assert idx is None
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


# ============================================================================
# 2c. Operation-Level Integration Tests (Mock-Free pikepdf)
# ============================================================================


def make_test_pdf_with_font(tmp_path, font_type="TrueType") -> tuple[Path, pikepdf.Pdf]:
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
            FontName=pikepdf.Name("/TestFont"),
            **{font_file_key: font_stream},
        )
    )

    if font_type == "Type0":
        # CIDFont structures map via /W instead of /Widths and rely on /DescendantFonts
        descendant_dict = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Font"),
                Subtype=pikepdf.Name("/CIDFontType2"),
                BaseFont=pikepdf.Name("/TestFont"),
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
                BaseFont=pikepdf.Name("/TestFont"),
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
                BaseFont=pikepdf.Name("/TestFont"),
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


def test_import_with_stale_manifest_missing_object(tmp_path):
    """Verifies safe skipping when the manifest references an object ID that no longer exists in the PDF."""
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
        api.call("import_fonts", target_pdf, operation_args=[str(export_dir)])


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
