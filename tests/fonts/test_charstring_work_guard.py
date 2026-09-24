# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/fonts/test_charstring_work_guard.py

"""
Tests for pdftl.fonts.charstring_work_guard: bounding charstring
interpretation (CFF width reading, Type 1 -> CFF conversion) against
recursive or exponentially branching subroutines.
"""

from __future__ import annotations

import io
import sys
import threading
import time
from pathlib import Path

import pytest

from pdftl.fonts import charstring_work_guard
from pdftl.fonts.cff_binary_utils import (
    _decompile_bare_cff,
    _measure_charstring_width,
    get_widths_from_cff,
    patch_cff_widths,
)
from pdftl.fonts.charstring_work_guard import (
    MAX_CHARSTRING_DEPTH,
    CharstringBudgetExceeded,
    bounded_charstring_interpreter,
)

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from type1_fixture_builder import build_type1_font_dict  # noqa: E402

WALL_CLOCK_CEILING = 8.0
_BIAS = 107  # Type 2 subr bias for fewer than 1240 subrs


def _self_recursive_subrs() -> list[list]:
    return [[0 - _BIAS, "callsubr", "return"]]


def _branching_subrs(levels: int) -> list[list]:
    """Subr i calls subr i+1 twice: 2^levels calls, depth only `levels`."""
    subrs = []
    for i in range(levels):
        nxt = i + 1 - _BIAS
        subrs.append([nxt, "callsubr", nxt, "callsubr", "return"])
    subrs.append(["return"])
    return subrs


def _nested_subrs(depth: int) -> list[list]:
    """A plain chain subr 0 -> 1 -> ... -> depth-1, each called once."""
    subrs = [[i + 1 - _BIAS, "callsubr", "return"] for i in range(depth - 1)]
    subrs.append(["return"])
    return subrs


def _build_cff(glyph_programs: dict[str, list], subrs: list[list]) -> bytes:
    """A real bare CFF whose glyphs' Type 2 programs are given literally,
    sharing one local Subrs INDEX."""
    from fontTools.cffLib import SubrsIndex
    from fontTools.fontBuilder import FontBuilder
    from fontTools.misc.psCharStrings import T2CharString

    order = [".notdef", *glyph_programs]
    fb = FontBuilder(1000, isTTF=False)
    fb.setupGlyphOrder(order)
    placeholder = {name: T2CharString(program=["endchar"]) for name in order}
    fb.setupCFF("GuardTest", {"FullName": "GuardTest"}, placeholder, {})
    cff = fb.font["CFF "].cff
    top = cff[cff.fontNames[0]]
    private = top.Private
    gsubrs = cff.GlobalSubrs
    local = SubrsIndex()
    for program in subrs:
        local.append(T2CharString(program=program, private=private, globalSubrs=gsubrs))
    private.Subrs = local
    charstrings = top.CharStrings
    for name, program in glyph_programs.items():
        charstrings[name].program = program
    for name in order:
        charstrings[name].private = private
    buf = io.BytesIO()

    class _Stub:
        recalcBBoxes = False

    cff.compile(buf, _Stub())
    return buf.getvalue()


# Width 600 with nominalWidthX 0: leading width operand, then an outline.
_NORMAL = [600, 0, 0, "rmoveto", 600, 0, "rlineto", "endchar"]
_NORMAL_VIA_SUBR = [450, 0, 0, "rmoveto", 0 - _BIAS, "callsubr", "endchar"]


def _hostile_cff_path(tmp_path, subrs, extra=None) -> Path:
    glyphs = {"hostile": [300, 0 - _BIAS, "callsubr", "endchar"], "normal": _NORMAL}
    glyphs.update(extra or {})
    path = tmp_path / "hostile.cff"
    path.write_bytes(_build_cff(glyphs, subrs))
    return path


def _charstring(path: Path, name: str):
    _, topdict = _decompile_bare_cff(path.read_bytes())
    return topdict.CharStrings[name]


class TestCffWidthMeasurement:
    def test_self_recursion_returns_none(self, tmp_path):
        path = _hostile_cff_path(tmp_path, _self_recursive_subrs())
        start = time.monotonic()
        assert _measure_charstring_width(_charstring(path, "hostile")) is None
        assert time.monotonic() - start < WALL_CLOCK_CEILING

    def test_exponential_fanout_returns_none(self, tmp_path):
        path = _hostile_cff_path(tmp_path, _branching_subrs(30))
        start = time.monotonic()
        assert _measure_charstring_width(_charstring(path, "hostile")) is None
        assert time.monotonic() - start < WALL_CLOCK_CEILING

    def test_fanout_trips_work_budget_not_depth(self, tmp_path):
        levels = 30
        assert levels < MAX_CHARSTRING_DEPTH
        path = _hostile_cff_path(tmp_path, _branching_subrs(levels))
        charstring = _charstring(path, "hostile")
        with pytest.raises(CharstringBudgetExceeded):
            with bounded_charstring_interpreter():
                charstring.decompile()

    def test_nesting_past_depth_limit_returns_none(self, tmp_path):
        path = _hostile_cff_path(tmp_path, _nested_subrs(MAX_CHARSTRING_DEPTH + 5))
        assert _measure_charstring_width(_charstring(path, "hostile")) is None

    def test_nesting_within_depth_limit_measured(self, tmp_path):
        path = _hostile_cff_path(tmp_path, _nested_subrs(20))
        assert _measure_charstring_width(_charstring(path, "hostile")) == 300

    def test_normal_glyph_with_subr_measured(self, tmp_path):
        subrs = [[450, 0, "rlineto", "return"]]
        path = tmp_path / "normal.cff"
        path.write_bytes(_build_cff({"A": _NORMAL_VIA_SUBR, "B": _NORMAL}, subrs))
        assert get_widths_from_cff(path) == {".notdef": 0, "A": 450, "B": 600}

    def test_message_is_fixed(self, tmp_path):
        path = _hostile_cff_path(tmp_path, _self_recursive_subrs())
        charstring = _charstring(path, "hostile")
        with pytest.raises(CharstringBudgetExceeded) as info:
            with bounded_charstring_interpreter():
                charstring.decompile()
        assert "hostile" not in str(info.value)


class TestCffEntryPointsSkipOnlyTheHostileGlyph:
    @pytest.mark.parametrize(
        "subrs", [_self_recursive_subrs(), _branching_subrs(30)], ids=["recursion", "fanout"]
    )
    def test_get_widths_from_cff(self, subrs, tmp_path):
        path = _hostile_cff_path(tmp_path, subrs)
        assert get_widths_from_cff(path) == {".notdef": 0, "normal": 600}

    @pytest.mark.parametrize(
        "subrs", [_self_recursive_subrs(), _branching_subrs(30)], ids=["recursion", "fanout"]
    )
    def test_patch_cff_widths(self, subrs, tmp_path):
        path = _hostile_cff_path(tmp_path, subrs)
        patched = patch_cff_widths(path, {"hostile": 999.0, "normal": 720.0})
        assert patched is not None
        out = tmp_path / "patched.cff"
        out.write_bytes(patched)
        assert get_widths_from_cff(out) == {".notdef": 0, "normal": 720}

    def test_patch_only_hostile_glyph_returns_none(self, tmp_path):
        path = _hostile_cff_path(tmp_path, _self_recursive_subrs())
        assert patch_cff_widths(path, {"hostile": 999.0}) is None


def _type1_with_subrs(subrs: list[list]) -> bytes:
    from fontTools.misc.psCharStrings import T1CharString
    from fontTools.t1Lib import T1Font

    font_dict = build_type1_font_dict(
        {
            ".notdef": (0, []),
            "A": (500, [0, 0, "rmoveto", 500, 0, "rlineto"]),
            "B": (300, [0, "callsubr"]),
        }
    )
    font_dict["Private"]["Subrs"] = [T1CharString(program=p) for p in subrs]
    font = T1Font.__new__(T1Font)
    font.encoding = "ascii"
    font.font = font_dict
    return font.createData()


class TestType1ToCffDrawing:
    """Type 1 glyphs are drawn through the same SimpleT2Decompiler.execute."""

    @pytest.mark.parametrize(
        "subrs",
        [
            [[0, "callsubr", "return"]],
            [[i + 1, "callsubr", i + 1, "callsubr", "return"] for i in range(30)] + [["return"]],
        ],
        ids=["recursion", "fanout"],
    )
    def test_hostile_glyph_dropped_rest_converted(self, subrs):
        from fontTools.cffLib import CFFFontSet

        from pdftl.fonts.type1_to_cff import type1_to_cff

        start = time.monotonic()
        cff_bytes = type1_to_cff(_type1_with_subrs(subrs), codes={65, 66})
        assert time.monotonic() - start < WALL_CLOCK_CEILING
        assert cff_bytes is not None
        cff = CFFFontSet()
        cff.decompile(io.BytesIO(cff_bytes), otFont=None)
        assert set(cff[cff.fontNames[0]].CharStrings.keys()) == {".notdef", "A"}

    def test_normal_subr_glyph_converted(self):
        from fontTools.cffLib import CFFFontSet
        from fontTools.pens.basePen import NullPen

        from pdftl.fonts.type1_to_cff import type1_to_cff

        subrs = [[0, 0, "rmoveto", 300, 0, "rlineto", "return"]]
        cff_bytes = type1_to_cff(_type1_with_subrs(subrs), codes={65, 66})
        cff = CFFFontSet()
        cff.decompile(io.BytesIO(cff_bytes), otFont=None)
        glyph = cff[cff.fontNames[0]].CharStrings["B"]
        glyph.draw(NullPen())
        assert glyph.width == 300


class TestScoping:
    def test_stock_behaviour_outside_context(self, tmp_path):
        charstring_work_guard._install()
        path = _hostile_cff_path(tmp_path, _self_recursive_subrs())
        with pytest.raises(RecursionError):
            _charstring(path, "hostile").decompile()

    def test_each_context_gets_a_fresh_budget(self, tmp_path):
        path = _hostile_cff_path(tmp_path, _nested_subrs(20))
        for _ in range(3):
            with bounded_charstring_interpreter():
                _charstring(path, "hostile").decompile()

    def test_budget_restored_after_exception(self, tmp_path):
        path = _hostile_cff_path(tmp_path, _self_recursive_subrs())
        with pytest.raises(CharstringBudgetExceeded):
            with bounded_charstring_interpreter():
                _charstring(path, "hostile").decompile()
        assert getattr(charstring_work_guard._state, "budget", None) is None

    def test_install_is_idempotent(self):
        from fontTools.misc.psCharStrings import SimpleT2Decompiler

        charstring_work_guard._install()
        first = SimpleT2Decompiler.execute
        charstring_work_guard._install()
        assert SimpleT2Decompiler.execute is first

    def test_other_threads_unaffected_by_active_guard(self, tmp_path):
        path = _hostile_cff_path(tmp_path, _nested_subrs(MAX_CHARSTRING_DEPTH + 5))
        entered, release = threading.Event(), threading.Event()
        errors = []

        def guarded_thread():
            try:
                with bounded_charstring_interpreter():
                    entered.set()
                    release.wait(10)
                    _charstring(path, "hostile").decompile()
            except CharstringBudgetExceeded:
                errors.append("bounded")

        worker = threading.Thread(target=guarded_thread)
        worker.start()
        try:
            assert entered.wait(10)
            _charstring(path, "hostile").decompile()  # deep but finite: stock succeeds
        finally:
            release.set()
            worker.join(WALL_CLOCK_CEILING)
        assert errors == ["bounded"]
