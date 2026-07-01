# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/fonts/test_cff_binary_utils.py

"""
Unit tests for pdftl.fonts.cff_binary_utils: fontTools.cffLib-based reading
and mutation of advance-width metrics inside bare (non-sfnt-wrapped) CFF
font programs, as embedded in a PDF's /FontFile3 stream for /Type1C and
/CIDFontType0C fonts.

These tests build real, genuine CFF byte streams via fontTools' own
builders rather than mocking fontTools, so a change in fontTools' own
charstring/CFF-table behavior would be caught here rather than only in
pdftl's own dispatch logic. Simple (non-CID) fixtures are built inline via
`_build_bare_cff_bytes` below; CID-keyed and ROS-absent CIDFontType0C
fixtures are built via `tests/fonts/fixtures/font_fixture_builder.py` --
see that module and its README for how the CID-keyed Top DICT is
constructed and what was previously blocking it.
"""

from __future__ import annotations

import sys
from pathlib import Path


import pytest

from pdftl.fonts.cff_binary_utils import (
    _cid_from_charset_name,
    _is_cid_keyed,
    get_widths_from_cff,
    patch_cff_widths,
)

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from font_fixture_builder import (  # noqa: E402
    SQUARE_500,
    TRIANGLE_300,
    build_cid_keyed_cff_bytes,
    build_noncid_cff_as_cidfonttype0_bytes,
)


def _build_bare_cff_bytes(glyphs: dict[str, tuple[int, list]]) -> bytes:
    """
    Builds a genuine, non-CID-keyed bare CFF byte stream via
    fontTools.fontBuilder.FontBuilder, then extracts just the raw CFF table
    bytes -- exactly the byte layout a PDF /FontFile3 /Type1C stream
    contains (no surrounding sfnt container).

    `glyphs` maps glyph name -> (advance_width, pen_commands), where
    pen_commands is a list of (method_name, args) tuples applied to a
    T2CharStringPen, e.g. [("moveTo", ((0, 0),)), ("lineTo", ((0, 500),))].
    """
    import io

    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.t2CharStringPen import T2CharStringPen
    from fontTools.ttLib import TTFont

    glyph_order = [".notdef", *[name for name in glyphs if name != ".notdef"]]
    if ".notdef" not in glyphs:
        glyphs = {".notdef": (0, []), **glyphs}

    charstrings = {}
    metrics = {}
    for name in glyph_order:
        width, commands = glyphs[name]
        pen = T2CharStringPen(width, {})
        for method_name, args in commands:
            getattr(pen, method_name)(*args)
        charstrings[name] = pen.getCharString()
        metrics[name] = (width, 0)

    fb = FontBuilder(1000, isTTF=False)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap({})
    fb.setupCFF("TestFont-Regular", {"FontName": "TestFont-Regular"}, charstrings, {})
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(advanceWidthMax=max(w for w, _ in glyphs.values()) or 1)
    fb.setupNameTable({"familyName": "Test", "styleName": "Regular"})
    fb.setupOS2()
    fb.setupPost()

    buf = io.BytesIO()
    fb.font.save(buf)
    buf.seek(0)
    tt = TTFont(buf)

    writer_buf = io.BytesIO()
    tt["CFF "].cff.compile(writer_buf, tt)
    return writer_buf.getvalue()


_SQUARE_A = (
    500,
    [
        ("moveTo", ((0, 0),)),
        ("lineTo", ((0, 500),)),
        ("lineTo", ((500, 500),)),
        ("lineTo", ((500, 0),)),
        ("closePath", ()),
    ],
)
_TRIANGLE_B = (
    300,
    [
        ("moveTo", ((0, 0),)),
        ("lineTo", ((0, 300),)),
        ("lineTo", ((300, 300),)),
        ("closePath", ()),
    ],
)


@pytest.fixture
def simple_cff_path(tmp_path) -> Path:
    """A genuine, non-CID-keyed bare CFF program with three glyphs."""
    data = _build_bare_cff_bytes({".notdef": (0, []), "A": _SQUARE_A, "B": _TRIANGLE_B})
    path = tmp_path / "simple.cff"
    path.write_bytes(data)
    return path


@pytest.fixture
def cid_keyed_cff_path(tmp_path) -> Path:
    """
    A genuine, CID-keyed bare CFF program (ROS-bearing Top DICT), built via
    fontTools.cffLib's lower-level API since FontBuilder itself has no
    CID-keyed CFF support. CID 1 -> square (width 500), CID 2 -> triangle
    (width 300). See font_fixture_builder.build_cid_keyed_cff_bytes.
    """
    data = build_cid_keyed_cff_bytes({1: SQUARE_500, 2: TRIANGLE_300})
    path = tmp_path / "cid_keyed.cff"
    path.write_bytes(data)
    return path


@pytest.fixture
def noncid_cidfonttype0_cff_path(tmp_path) -> Path:
    """
    A genuine, ordinary (non-CID-keyed, no ROS) bare CFF program, of the
    kind /CIDFontType0C may still wrap per ISO 32000-2 9.7.4.2's second
    branch -- CID used directly as GID, no charset lookup.
    """
    data = build_noncid_cff_as_cidfonttype0_bytes({"glyphA": SQUARE_500, "glyphB": TRIANGLE_300})
    path = tmp_path / "noncid_cidfonttype0.cff"
    path.write_bytes(data)
    return path


class TestGetWidthsFromCff:
    def test_reads_all_glyph_widths_by_name(self, simple_cff_path):
        widths = get_widths_from_cff(simple_cff_path)
        assert widths == {".notdef": 0, "A": 500, "B": 300}

    def test_missing_file_returns_empty_dict(self, tmp_path):
        assert get_widths_from_cff(tmp_path / "does_not_exist.cff") == {}

    def test_malformed_bytes_returns_empty_dict(self, tmp_path):
        bad_path = tmp_path / "bad.cff"
        bad_path.write_bytes(b"this is not a CFF table at all")
        assert get_widths_from_cff(bad_path) == {}

    def test_truncated_valid_header_returns_empty_dict(self, simple_cff_path, tmp_path):
        """A CFF file truncated mid-structure (valid major/minor version
        bytes, but incomplete beyond that) is treated the same as any other
        unreadable font program rather than raising."""
        truncated_path = tmp_path / "truncated.cff"
        truncated_path.write_bytes(simple_cff_path.read_bytes()[:8])
        assert get_widths_from_cff(truncated_path) == {}


class TestPatchCffWidths:
    def test_patches_single_glyph_width(self, simple_cff_path, tmp_path):
        patched_bytes = patch_cff_widths(simple_cff_path, {"A": 999.0})
        assert patched_bytes is not None

        patched_path = tmp_path / "patched.cff"
        patched_path.write_bytes(patched_bytes)
        reread = get_widths_from_cff(patched_path)

        assert reread["A"] == 999.0
        assert reread["B"] == 300.0  # untouched
        assert reread[".notdef"] == 0.0  # untouched

    def test_patches_multiple_glyphs_independently(self, simple_cff_path, tmp_path):
        patched_bytes = patch_cff_widths(simple_cff_path, {"A": 111.0, "B": 222.0})
        assert patched_bytes is not None

        patched_path = tmp_path / "patched.cff"
        patched_path.write_bytes(patched_bytes)
        reread = get_widths_from_cff(patched_path)

        assert reread["A"] == 111.0
        assert reread["B"] == 222.0

    def test_no_matching_glyph_returns_none(self, simple_cff_path):
        assert patch_cff_widths(simple_cff_path, {"NoSuchGlyph": 1.0}) is None

    def test_empty_pdf_widths_returns_none(self, simple_cff_path):
        assert patch_cff_widths(simple_cff_path, {}) is None

    def test_missing_file_returns_none(self, tmp_path):
        assert patch_cff_widths(tmp_path / "does_not_exist.cff", {"A": 1.0}) is None

    def test_malformed_bytes_returns_none(self, tmp_path):
        bad_path = tmp_path / "bad.cff"
        bad_path.write_bytes(b"this is not a CFF table at all")
        assert patch_cff_widths(bad_path, {"A": 1.0}) is None

    def test_patched_bytes_are_a_smaller_or_equal_valid_cff_program(
        self, simple_cff_path, tmp_path
    ):
        """A width-only patch should not corrupt the overall CFF structure
        for glyphs that were not touched -- re-reading every glyph's width
        (not just the patched one) confirms the whole program round-trips,
        not merely the edited entry."""
        original = get_widths_from_cff(simple_cff_path)
        patched_bytes = patch_cff_widths(simple_cff_path, {"A": 750.0})
        patched_path = tmp_path / "patched.cff"
        patched_path.write_bytes(patched_bytes)
        reread = get_widths_from_cff(patched_path)

        assert set(reread.keys()) == set(original.keys())
        assert reread["A"] == 750.0
        assert reread["B"] == original["B"]


class TestCidHelpers:
    """Unit tests for the small CID/charset-naming helpers, independent of
    a real CFF byte stream."""

    def test_cid_from_charset_name_recovers_zero_padded_cid(self):
        assert _cid_from_charset_name("cid00010") == 10
        assert _cid_from_charset_name("cid00000") == 0
        assert _cid_from_charset_name("cid65535") == 65535

    def test_cid_from_charset_name_rejects_non_cid_names(self):
        assert _cid_from_charset_name("A") is None
        assert _cid_from_charset_name("space") is None

    def test_cid_from_charset_name_rejects_malformed_cid_suffix(self):
        assert _cid_from_charset_name("cidXXXXX") is None

    def test_is_cid_keyed_true_for_ros_bearing_topdict(self):
        class _FakeTopDict:
            ROS = ("Adobe", "Identity", 0)

        assert _is_cid_keyed(_FakeTopDict()) is True

    def test_is_cid_keyed_false_for_ordinary_topdict(self):
        class _FakeTopDict:
            pass

        assert _is_cid_keyed(_FakeTopDict()) is False


class TestCidKeyedCffEndToEnd:
    """
    End-to-end tests against a real, genuine CID-keyed CFF byte stream
    (ROS-bearing Top DICT), closing the previously-flagged gap from
    font-fidelity Phase 2, Task 2.6: get_widths_from_cff/patch_cff_widths
    exercised together against real fontTools.cffLib-produced bytes, not
    just the charset-naming helpers in isolation.

    Note the fontTools charset convention confirmed here: GID 0 is always
    named ".notdef" literally, never "cid00000", even in a genuinely
    CID-keyed program -- so CID 0 is never addressable through this
    charset-lookup branch. This is asymmetric with the ROS-absent branch
    below, where CID 0 (== GID 0) is addressable, since that branch never
    does a charset-name lookup at all.
    """

    def test_reads_cid_keyed_widths(self, cid_keyed_cff_path):
        widths = get_widths_from_cff(cid_keyed_cff_path, cid_to_gid_map="cff_native")
        assert widths == {"0001": 500.0, "0002": 300.0}

    def test_is_cid_keyed_true_for_real_program(self, cid_keyed_cff_path):
        from pdftl.fonts.cff_binary_utils import _decompile_bare_cff

        _, topdict = _decompile_bare_cff(cid_keyed_cff_path.read_bytes())
        assert _is_cid_keyed(topdict) is True

    def test_charset_names_cid_via_synthetic_convention(self, cid_keyed_cff_path):
        from pdftl.fonts.cff_binary_utils import _decompile_bare_cff

        _, topdict = _decompile_bare_cff(cid_keyed_cff_path.read_bytes())
        assert topdict.charset == [".notdef", "cid00001", "cid00002"]

    def test_patches_single_cid_width(self, cid_keyed_cff_path, tmp_path):
        patched_bytes = patch_cff_widths(
            cid_keyed_cff_path, {"0001": 650.0}, cid_to_gid_map="cff_native"
        )
        assert patched_bytes is not None

        patched_path = tmp_path / "patched_cid.cff"
        patched_path.write_bytes(patched_bytes)
        reread = get_widths_from_cff(patched_path, cid_to_gid_map="cff_native")

        assert reread["0001"] == 650.0
        assert reread["0002"] == 300.0  # untouched

    def test_patches_multiple_cids_independently(self, cid_keyed_cff_path, tmp_path):
        patched_bytes = patch_cff_widths(
            cid_keyed_cff_path, {"0001": 111.0, "0002": 222.0}, cid_to_gid_map="cff_native"
        )
        assert patched_bytes is not None

        patched_path = tmp_path / "patched_cid.cff"
        patched_path.write_bytes(patched_bytes)
        reread = get_widths_from_cff(patched_path, cid_to_gid_map="cff_native")

        assert reread["0001"] == 111.0
        assert reread["0002"] == 222.0

    def test_notdef_cid_is_not_addressable_via_charset_lookup(self, cid_keyed_cff_path):
        """CID 0's charset name is literally '.notdef', not 'cid00000', so a
        request to patch CID 0 finds no matching glyph and patches nothing."""
        assert (
            patch_cff_widths(cid_keyed_cff_path, {"0000": 999.0}, cid_to_gid_map="cff_native")
            is None
        )

    def test_no_matching_cid_returns_none(self, cid_keyed_cff_path):
        assert (
            patch_cff_widths(cid_keyed_cff_path, {"FFFF": 1.0}, cid_to_gid_map="cff_native")
            is None
        )

    def test_name_keyed_read_ignores_cid_native_mode(self, cid_keyed_cff_path):
        """Without cid_to_gid_map='cff_native', a CID-keyed program is read
        as if it were name-keyed -- returning its synthetic charset names,
        not CID hex strings. Confirms the dispatch flag actually gates
        behavior rather than being ignored."""
        widths = get_widths_from_cff(cid_keyed_cff_path)
        assert widths == {".notdef": 0.0, "cid00001": 500.0, "cid00002": 300.0}


class TestNonCidKeyedCidFontType0CEndToEnd:
    """
    End-to-end tests against a real, ordinary (non-CID-keyed, ROS-absent)
    bare CFF program used as a /CIDFontType0C descendant, exercising ISO
    32000-2 9.7.4.2's second branch: CID used directly as GID.
    """

    def test_reads_cid_widths_via_direct_gid(self, noncid_cidfonttype0_cff_path):
        widths = get_widths_from_cff(noncid_cidfonttype0_cff_path, cid_to_gid_map="cff_native")
        # GID 0 (.notdef) IS addressable as CID 0 here, unlike the
        # ROS-bearing case above, since there is no charset-name lookup at
        # all in this branch -- cid is used directly as gid, including 0.
        assert widths == {"0000": 0.0, "0001": 500.0, "0002": 300.0}

    def test_is_cid_keyed_false_for_real_program(self, noncid_cidfonttype0_cff_path):
        from pdftl.fonts.cff_binary_utils import _decompile_bare_cff

        _, topdict = _decompile_bare_cff(noncid_cidfonttype0_cff_path.read_bytes())
        assert _is_cid_keyed(topdict) is False

    def test_patches_cid_via_direct_gid(self, noncid_cidfonttype0_cff_path, tmp_path):
        patched_bytes = patch_cff_widths(
            noncid_cidfonttype0_cff_path, {"0002": 444.0}, cid_to_gid_map="cff_native"
        )
        assert patched_bytes is not None

        patched_path = tmp_path / "patched_noncid.cff"
        patched_path.write_bytes(patched_bytes)
        reread = get_widths_from_cff(patched_path, cid_to_gid_map="cff_native")

        assert reread["0002"] == 444.0
        assert reread["0001"] == 500.0  # untouched

    def test_notdef_cid_is_addressable_and_patchable(self, noncid_cidfonttype0_cff_path, tmp_path):
        """Unlike the ROS-bearing case, CID 0 == GID 0 here, and IS a real,
        patchable glyph -- confirming the asymmetry documented in the
        fixtures README rather than assuming it."""
        patched_bytes = patch_cff_widths(
            noncid_cidfonttype0_cff_path, {"0000": 42.0}, cid_to_gid_map="cff_native"
        )
        assert patched_bytes is not None

        patched_path = tmp_path / "patched_noncid_notdef.cff"
        patched_path.write_bytes(patched_bytes)
        reread = get_widths_from_cff(patched_path, cid_to_gid_map="cff_native")
        assert reread["0000"] == 42.0

    def test_cid_beyond_glyph_count_returns_none(self, noncid_cidfonttype0_cff_path):
        assert (
            patch_cff_widths(
                noncid_cidfonttype0_cff_path, {"0063": 1.0}, cid_to_gid_map="cff_native"
            )
            is None
        )


class TestCidPatchErrorPaths:
    """
    Closes remaining coverage gaps in the CID-keyed patch dispatch path:
    a non-hex CID key, a CID resolving past the font's actual glyph count
    (_glyph_name_for_gid's own bounds guard, distinct from
    _resolve_cff_cid_to_gid's), and the outer recompile-failure branch in
    patch_cff_widths, none of which are exercised by the "happy path"
    end-to-end tests above.
    """

    def test_non_hex_cid_key_skipped_not_fatal(self, cid_keyed_cff_path, tmp_path):
        """A non-hex key alongside a valid one doesn't abort the whole
        patch pass -- the valid entry still gets applied."""
        patched_bytes = patch_cff_widths(
            cid_keyed_cff_path, {"ZZZZ": 1.0, "0001": 650.0}, cid_to_gid_map="cff_native"
        )
        assert patched_bytes is not None

        patched_path = tmp_path / "patched.cff"
        patched_path.write_bytes(patched_bytes)
        reread = get_widths_from_cff(patched_path, cid_to_gid_map="cff_native")
        assert reread["0001"] == 650.0

    def test_gid_beyond_charset_length_skipped(self, monkeypatch, cid_keyed_cff_path):
        """A CID that resolves to an in-range GID for
        _resolve_cff_cid_to_gid (non-CID-keyed direct-GID branch) but is
        then out of range for _glyph_name_for_gid's own charset-length
        check is skipped rather than raising. Simulated here by patching
        _resolve_cff_cid_to_gid to return an out-of-bounds GID directly,
        since constructing a real font program that hits this exact
        mismatch is not otherwise reachable through the public API."""
        import pdftl.fonts.cff_binary_utils as cbu

        monkeypatch.setattr(cbu, "_resolve_cff_cid_to_gid", lambda topdict, cid: 999)
        assert (
            patch_cff_widths(cid_keyed_cff_path, {"0001": 1.0}, cid_to_gid_map="cff_native")
            is None
        )

    def test_charstring_recompile_failure_falls_back_to_none(self, monkeypatch, simple_cff_path):
        """If the outer CFFFontSet.compile() call itself fails after a
        successful in-memory patch (e.g. a fontTools internal error), the
        whole patch attempt reports failure via None rather than returning
        corrupt or partial bytes."""
        import pdftl.fonts.cff_binary_utils as cbu

        class _BoomCFFFontSet:
            def compile(self, buf, otFont):
                raise ValueError("simulated recompile failure")

        original_decompile = cbu._decompile_bare_cff

        def _patched_decompile(data):
            _, topdict = original_decompile(data)
            return _BoomCFFFontSet(), topdict

        monkeypatch.setattr(cbu, "_decompile_bare_cff", _patched_decompile)
        assert patch_cff_widths(simple_cff_path, {"A": 999.0}) is None

    def test_charstring_compile_failure_skips_that_glyph(self, monkeypatch, simple_cff_path):
        """If an individual charstring's own .compile() call fails after
        its width operand has already been rewritten, that single glyph is
        skipped (logged at debug) rather than aborting the whole patch."""
        import pdftl.fonts.cff_binary_utils as cbu

        def _boom_charstring_compile(topdict, glyph_name, new_width):
            charstring = topdict.CharStrings[glyph_name]
            if cbu._measure_charstring_width(charstring) is None:
                return False
            nominal_width_x = topdict.Private.nominalWidthX
            charstring.program[0] = new_width - nominal_width_x
            charstring.width = new_width

            def _boom():
                raise KeyError("simulated compile failure")

            charstring.compile = _boom
            try:
                charstring.compile()
            except (AttributeError, IndexError, KeyError, ValueError):
                return False
            return True

        monkeypatch.setattr(cbu, "_patch_single_cff_width", _boom_charstring_compile)
        assert patch_cff_widths(simple_cff_path, {"A": 999.0}) is None

    def test_measure_charstring_width_unreadable_charstring_returns_none(self, monkeypatch):
        """A charstring whose .decompile()/.draw() raises during width
        measurement (a malformed or corrupted glyph program) is skipped
        via the debug-logged except branch rather than propagating."""
        from pdftl.fonts.cff_binary_utils import _measure_charstring_width

        class _BoomCharstring:
            def decompile(self):
                raise ValueError("simulated corrupt charstring bytecode")

        assert _measure_charstring_width(_BoomCharstring()) is None

    def test_patch_single_cff_width_skips_when_width_unmeasurable(
        self, monkeypatch, simple_cff_path
    ):
        """_patch_single_cff_width bails out before touching the charstring
        at all if _measure_charstring_width can't establish a baseline
        width for it."""
        import pdftl.fonts.cff_binary_utils as cbu

        monkeypatch.setattr(cbu, "_measure_charstring_width", lambda charstring: None)
        assert patch_cff_widths(simple_cff_path, {"A": 999.0}) is None

    def test_real_charstring_compile_failure_is_caught(self, simple_cff_path):
        """When the real charstring.compile() call itself raises after the
        width operand has already been rewritten (simulated here by
        monkeypatching just that one glyph's bound .compile() method after
        a genuine, successful width measurement), the failure is caught by
        _patch_single_cff_width's own except clause and it returns False
        rather than propagating -- exercised against the real function,
        not a reimplementation."""
        from pdftl.fonts.cff_binary_utils import _decompile_bare_cff, _patch_single_cff_width

        _, topdict = _decompile_bare_cff(simple_cff_path.read_bytes())
        charstring = topdict.CharStrings["A"]

        def _boom():
            raise KeyError("simulated compile failure")

        charstring.compile = _boom

        assert _patch_single_cff_width(topdict, "A", 999.0) is False


def test_resolve_nominal_width_x_falls_back_to_topdict_private():
    from pdftl.fonts.cff_binary_utils import _resolve_nominal_width_x

    class NoPrivateCharstring:
        pass  # deliberately no .private attribute

    topdict = type("TopDict", (), {"Private": type("Private", (), {"nominalWidthX": 42})()})()
    assert _resolve_nominal_width_x(topdict, NoPrivateCharstring()) == 42


def test_find_width_presence_returns_none_for_unrecognized_operator():
    """
    Covers the in-loop 'return None, None' branch: a program that reaches
    an operator this function doesn't recognize (i.e. not a moveto variant,
    not a stem-hint op, not endchar) before any width-presence decision can
    be made. 'callsubr' is used here rather than 'callgsubr' to make sure
    the branch isn't accidentally coupled to one specific delegation op.
    """
    from pdftl.fonts.cff_binary_utils import _find_width_presence

    program = [3, "callsubr", "endchar"]
    width_present, insert_index = _find_width_presence(program)

    assert width_present is None
    assert insert_index is None


def test_find_width_presence_returns_none_for_operator_less_program():
    """
    Covers the post-loop 'return None, None' branch: a program consisting
    purely of numeric operands, with no operator at all to clear the
    stack. This is a degenerate/malformed program that should never occur
    in a real font, but the function must still fail closed (report
    'undeterminable') rather than raising or guessing.
    """
    from pdftl.fonts.cff_binary_utils import _find_width_presence

    program = [10, 20, 30]
    width_present, insert_index = _find_width_presence(program)

    assert width_present is None
    assert insert_index is None


# --- cff_binary_utils.py gaps ---


class TestSubrBiasThresholds:
    def test_subr_bias_mid_range(self):
        """Adobe TN #5177 §4.7: counts in [1240, 33900) use bias 1131."""
        from pdftl.fonts.cff_binary_utils import _subr_bias

        assert _subr_bias(1240) == 1131
        assert _subr_bias(33899) == 1131

    def test_subr_bias_large_range(self):
        """Counts >= 33900 use bias 32768."""
        from pdftl.fonts.cff_binary_utils import _subr_bias

        assert _subr_bias(33900) == 32768
        assert _subr_bias(100000) == 32768


class TestGetLocalSubrsDirectAttribute:
    def test_get_local_subrs_uses_direct_subrs_attribute(self):
        """A charstring exposing `.subrs` directly (the test-double shape)
        is returned as-is, without falling through to `.private.Subrs`."""
        from pdftl.fonts.cff_binary_utils import _get_local_subrs

        charstring = type("CS", (), {"subrs": ["marker"]})()
        assert _get_local_subrs(charstring) == ["marker"]


class TestResolveSubrOutOfRange:
    def test_resolve_subr_index_out_of_range_returns_none(self):
        from pdftl.fonts.cff_binary_utils import _resolve_subr

        subrs = ["a", "b"]  # len 2, bias 107 -> any small negative index resolves way out of range
        assert _resolve_subr(subrs, 500) is None


class TestResolveCalledSubr:
    def test_resolve_called_subr_success(self):
        from pdftl.fonts.cff_binary_utils import _resolve_called_subr

        class DummySubr:
            def __init__(self):
                self.decompiled = False

            def decompile(self):
                self.decompiled = True

        subr = DummySubr()
        # index 0, 1 subr -> bias 107 -> real_index = -107 + 107 = 0
        result = _resolve_called_subr(-107, [subr])
        assert result is subr
        assert subr.decompiled is True

    def test_resolve_called_subr_out_of_range_returns_none(self):
        from pdftl.fonts.cff_binary_utils import _resolve_called_subr

        assert _resolve_called_subr(999, ["only_one"]) is None

    def test_resolve_called_subr_decompile_failure_returns_none(self):
        from pdftl.fonts.cff_binary_utils import _resolve_called_subr

        class BoomSubr:
            def decompile(self):
                raise ValueError("simulated corrupt subr")

        assert _resolve_called_subr(-107, [BoomSubr()]) is None


class TestTraceIntoSubrGlobalSubrsFallback:
    def test_trace_into_subr_falls_back_to_caller_global_subrs(self):
        """If the resolved subr has no `.globalSubrs` of its own, the
        caller's global_subrs list is used instead."""
        from pdftl.fonts.cff_binary_utils import _trace_into_subr

        class Subr:
            program = [10, 20, "rmoveto", "endchar"]
            # deliberately no .globalSubrs attribute

        result = _trace_into_subr(Subr(), [], local_subrs=None, global_subrs=["fallback"], depth=0)
        # width absent (2 args, rmoveto expects 2) -> (False, 0)
        assert result == (False, 0)


class TestStepWidthPresenceLocalSubrDispatch:
    def test_step_width_presence_dispatches_local_subrs_for_callsubr(self):
        """Confirms `callsubr` (not `callgsubr`) selects local_subrs, not
        global_subrs, in the subrs-selection line."""
        from pdftl.fonts.cff_binary_utils import _step_width_presence

        local_subr = type("S", (), {"program": [10, 20, "rmoveto", "return"]})()
        _, verdict = _step_width_presence(
            "callsubr",
            args=[-107],  # index 0 with 1 local subr -> bias 107
            local_subrs=[local_subr],
            global_subrs=None,  # if this were used instead, resolution would fail
            depth=0,
            carried_len=0,
        )
        assert verdict == (False, 0)


# --- font_binary_sfnt.py gaps: _patch_cid_metrics's cff_native branch ---


class TestPatchCidMetricsCffNativeGuards:
    def test_no_cff_table_returns_false(self):
        from pdftl.fonts.font_binary_sfnt import _patch_cid_metrics

        class NoCFFFont:
            def __contains__(self, key):
                return False

            def getGlyphOrder(self):
                return []

        assert _patch_cid_metrics(NoCFFFont(), {}, {"0001": 500.0}, 1.0, "cff_native") is False

    def test_non_hex_cid_key_skipped(self, monkeypatch):
        from pdftl.fonts.font_binary_sfnt import _patch_cid_metrics
        import pdftl.fonts.cff_binary_utils as cbu

        topdict = type("TD", (), {})()
        cff = type("CFF", (), {"fontNames": ["F"], "__getitem__": lambda s, k: topdict})()

        class FakeTT:
            def __contains__(self, key):
                return key == "CFF "

            def __getitem__(self, key):
                return type("CFFTable", (), {"cff": cff})()

            def getGlyphOrder(self):
                return [".notdef", "A"]

        monkeypatch.setattr(cbu, "_resolve_cff_cid_to_gid", lambda td, cid: 1)
        result = _patch_cid_metrics(FakeTT(), {"A": (250, 0)}, {"ZZZZ": 500.0}, 1.0, "cff_native")
        assert result is False

    def test_gid_none_skipped(self, monkeypatch):
        from pdftl.fonts.font_binary_sfnt import _patch_cid_metrics
        import pdftl.fonts.cff_binary_utils as cbu

        topdict = type("TD", (), {})()
        cff = type("CFF", (), {"fontNames": ["F"], "__getitem__": lambda s, k: topdict})()

        class FakeTT:
            def __contains__(self, key):
                return key == "CFF "

            def __getitem__(self, key):
                return type("CFFTable", (), {"cff": cff})()

            def getGlyphOrder(self):
                return [".notdef", "A"]

        monkeypatch.setattr(cbu, "_resolve_cff_cid_to_gid", lambda td, cid: None)
        result = _patch_cid_metrics(FakeTT(), {"A": (250, 0)}, {"0001": 500.0}, 1.0, "cff_native")
        assert result is False

    def test_gname_none_skipped(self, monkeypatch):
        from pdftl.fonts.font_binary_sfnt import _patch_cid_metrics
        import pdftl.fonts.cff_binary_utils as cbu

        topdict = type("TD", (), {})()
        cff = type("CFF", (), {"fontNames": ["F"], "__getitem__": lambda s, k: topdict})()

        class FakeTT:
            def __contains__(self, key):
                return key == "CFF "

            def __getitem__(self, key):
                return type("CFFTable", (), {"cff": cff})()

            def getGlyphOrder(self):
                return [".notdef"]  # gid 5 resolves to nothing

        monkeypatch.setattr(cbu, "_resolve_cff_cid_to_gid", lambda td, cid: 5)
        result = _patch_cid_metrics(FakeTT(), {}, {"0001": 500.0}, 1.0, "cff_native")
        assert result is False

    def test_glyph_missing_from_hmtx_skipped(self, monkeypatch):
        from pdftl.fonts.font_binary_sfnt import _patch_cid_metrics
        import pdftl.fonts.cff_binary_utils as cbu

        topdict = type("TD", (), {})()
        cff = type("CFF", (), {"fontNames": ["F"], "__getitem__": lambda s, k: topdict})()

        class FakeTT:
            def __contains__(self, key):
                return key == "CFF "

            def __getitem__(self, key):
                return type("CFFTable", (), {"cff": cff})()

            def getGlyphOrder(self):
                return [".notdef", "A"]

        monkeypatch.setattr(cbu, "_resolve_cff_cid_to_gid", lambda td, cid: 1)
        result = _patch_cid_metrics(
            FakeTT(),
            {},
            {"0001": 500.0},
            1.0,
            "cff_native",  # hmtx empty -> KeyError
        )
        assert result is False


class TestResolveCalledSubrNonNumericIndex:
    def test_resolve_called_subr_non_numeric_index_returns_none(self):
        """A subr-index operand that can't be coerced to int (e.g. a stray
        operator string reaching this position due to a malformed program)
        is treated as untraceable rather than raising."""
        from pdftl.fonts.cff_binary_utils import _resolve_called_subr

        assert _resolve_called_subr("not_a_number", ["subr0"]) is None


class TestTraceIntoSubrNoProgram:
    def test_trace_into_subr_subr_with_no_program_returns_none(self):
        """A resolved subr object with no `.program` attribute at all
        (distinct from an empty program) can't be traced into."""
        from pdftl.fonts.cff_binary_utils import _trace_into_subr

        class SubrWithNoProgram:
            pass  # no .program attribute

        result = _trace_into_subr(
            SubrWithNoProgram(), args=[], local_subrs=None, global_subrs=None, depth=0
        )
        assert result == (None, None)


class TestStepWidthPresenceEmptyArgsBeforeCall:
    def test_step_width_presence_callsubr_with_no_args_returns_none(self):
        """A callsubr/callgsubr reached with an empty argument stack (no
        subroutine index available to pop) is untraceable."""
        from pdftl.fonts.cff_binary_utils import _step_width_presence

        _, verdict = _step_width_presence(
            "callgsubr",
            args=[],  # nothing to pop as the subr index
            local_subrs=None,
            global_subrs=[],
            depth=0,
            carried_len=0,
        )
        assert verdict == (None, None)
