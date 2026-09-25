# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/fonts/test_type1_interp_guard.py

"""
Tests for pdftl.fonts.type1_interp_guard: bounding fontTools' PostScript
interpreter against hostile Type 1 programs, and the degradation of every
Type 1 entry point that parses through it.

Hostile payloads go in the cleartext header, before `currentfile eexec`,
which psLib executes like any other PostScript.
"""

from __future__ import annotations

import sys
import threading
import time
import tracemalloc
from pathlib import Path

import pytest

from pdftl.fonts import type1_interp_guard
from pdftl.fonts.type1_binary_utils import get_widths_from_type1, patch_type1_widths
from pdftl.fonts.type1_interp_guard import (
    MAX_INTERP_STACK,
    InterpreterBudgetExceeded,
    bounded_type1_interpreter,
)
from pdftl.fonts.type1_to_cff import open_type1_font_bytes, type1_to_cff

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from type1_fixture_builder import build_type1_bytes  # noqa: E402

_GLYPHS = {
    ".notdef": (0, []),
    "A": (500, [0, 0, "rmoveto", 500, 0, "rlineto"]),
}

INFINITE_LOOP = b"0 0 -1 {pop} for"
STACK_FLOOD = b"0 0 -1 {} for"
HUGE_ARRAY = b"50000000 array pop"
HUGE_STRING = b"400000000 string pop"
CUMULATIVE_ARRAYS = b"0 1 200 {pop 50000 array pop} for"

HOSTILE = {
    "infinite_loop": INFINITE_LOOP,
    "stack_flood": STACK_FLOOD,
    "huge_array": HUGE_ARRAY,
    "huge_string": HUGE_STRING,
    "cumulative_arrays": CUMULATIVE_ARRAYS,
}

# Catches a budget grown far too large. Generous because exhausting the
# real step budget takes a few seconds, several times that under coverage
# on a loaded CI runner; a guard that stops firing hangs instead.
WALL_CLOCK_CEILING = 30.0


def _with_payload(payload: bytes) -> bytes:
    program = build_type1_bytes(_GLYPHS)
    marker = program.index(b"currentfile eexec")
    return program[:marker] + payload + b"\n" + program[marker:]


def _parse_guarded(program: bytes, tmp_path: Path):
    from fontTools.t1Lib import T1Font

    path = tmp_path / "font.t1"
    path.write_bytes(program)
    font = T1Font(str(path), kind="OTHER")
    with bounded_type1_interpreter():
        font.parse()
    return font


def _run_ps(source: bytes):
    """Interprets bare PostScript through whatever psLib.PSInterpreter
    currently is."""
    from fontTools.misc import psLib

    interp = psLib.PSInterpreter()
    interp.interpret(source)
    return interp


class TestBudgetsFire:
    @pytest.mark.parametrize(
        "payload, reason",
        [
            (INFINITE_LOOP, "step budget"),
            (STACK_FLOOD, "stack"),
            (HUGE_ARRAY, "memory"),
            (HUGE_STRING, "memory"),
            (CUMULATIVE_ARRAYS, "memory"),
        ],
    )
    def test_payload_trips_expected_budget(self, payload, reason, tmp_path):
        start = time.monotonic()
        with pytest.raises(InterpreterBudgetExceeded, match=reason):
            _parse_guarded(_with_payload(payload), tmp_path)
        assert time.monotonic() - start < WALL_CLOCK_CEILING

    @pytest.mark.parametrize("payload", [HUGE_ARRAY, HUGE_STRING])
    def test_oversized_allocation_refused_before_allocating(self, payload, tmp_path):
        program = _with_payload(payload)
        tracemalloc.start()
        try:
            with pytest.raises(InterpreterBudgetExceeded):
                _parse_guarded(program, tmp_path)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        assert peak < 20_000_000

    def test_message_is_fixed(self, tmp_path):
        with pytest.raises(InterpreterBudgetExceeded) as info:
            _parse_guarded(_with_payload(b"/SecretName " + HUGE_ARRAY), tmp_path)
        assert "SecretName" not in str(info.value)
        assert "50000000" not in str(info.value)

    def test_non_integer_array_count_left_to_base_operator(self):
        from fontTools.misc.psLib import PSError

        with bounded_type1_interpreter():
            with pytest.raises(PSError):
                _run_ps(b"/notanumber array")

    def test_negative_array_count_left_to_base_operator(self):
        with bounded_type1_interpreter():
            interp = _run_ps(b"-5 array")
        assert interp.stack[-1].value == []


class TestRealFontsUnaffected:
    def test_small_font_parses(self, tmp_path):
        font = _parse_guarded(build_type1_bytes(_GLYPHS), tmp_path)
        assert set(font.font["CharStrings"]) == {".notdef", "A"}

    def test_large_font_parses(self, tmp_path):
        # Far more glyphs than any real Type 1 font, to catch a budget set
        # too tight for the per-glyph interpreter work.
        glyphs = {f"g{i}": (i, [0, 0, "rmoveto", i, 0, "rlineto"]) for i in range(3000)}
        font = _parse_guarded(build_type1_bytes({".notdef": (0, []), **glyphs}), tmp_path)
        assert len(font.font["CharStrings"]) == 3001

    def test_program_within_budgets_parses(self, tmp_path):
        benign = b"0 1 1000 {pop} for 1000 array pop 1000 string pop"
        font = _parse_guarded(_with_payload(benign), tmp_path)
        assert "A" in font.font["CharStrings"]


class TestEntryPointsDegrade:
    @pytest.fixture(autouse=True)
    def small_step_budget(self, monkeypatch):
        # TestBudgetsFire covers the real budget's timing; here only the
        # degradation contract matters.
        monkeypatch.setattr(type1_interp_guard, "MAX_INTERP_STEPS", 50_000)

    @pytest.mark.parametrize("name", sorted(HOSTILE))
    def test_get_widths_from_type1_returns_empty(self, name, tmp_path):
        path = tmp_path / "hostile.pfb"
        path.write_bytes(_with_payload(HOSTILE[name]))
        start = time.monotonic()
        assert get_widths_from_type1(path) == {}
        assert time.monotonic() - start < WALL_CLOCK_CEILING

    @pytest.mark.parametrize("name", sorted(HOSTILE))
    def test_patch_type1_widths_returns_none(self, name, tmp_path):
        path = tmp_path / "hostile.pfb"
        path.write_bytes(_with_payload(HOSTILE[name]))
        assert patch_type1_widths(path, {"A": 640.0}) is None

    @pytest.mark.parametrize("name", sorted(HOSTILE))
    def test_type1_to_cff_returns_none(self, name):
        assert type1_to_cff(_with_payload(HOSTILE[name]), codes={65}) is None

    @pytest.mark.parametrize("name", sorted(HOSTILE))
    def test_trailerless_hostile_program_parsed_once(self, name, monkeypatch):
        # A trailerless program is the shape that used to be retried.
        from fontTools.t1Lib import T1Font

        program = _with_payload(HOSTILE[name])
        program = program[: program.index(b"0" * 64, program.index(b"currentfile eexec"))]
        calls = []
        real_parse = T1Font.parse

        def counting_parse(self):
            calls.append(1)
            return real_parse(self)

        monkeypatch.setattr(T1Font, "parse", counting_parse)
        assert open_type1_font_bytes(program) is None
        assert len(calls) == 1


class TestScoping:
    def test_unbounded_outside_context(self):
        type1_interp_guard._install()
        flood = b"0 1 %d {} for" % (MAX_INTERP_STACK + 10)
        interp = _run_ps(flood)
        assert len(interp.stack) > MAX_INTERP_STACK
        with bounded_type1_interpreter():
            with pytest.raises(InterpreterBudgetExceeded):
                _run_ps(flood)
        assert len(_run_ps(flood).stack) > MAX_INTERP_STACK

    def test_allocation_unbounded_outside_context(self, monkeypatch):
        type1_interp_guard._install()
        monkeypatch.setattr(type1_interp_guard, "MAX_INTERP_CELLS", 10)
        interp = _run_ps(b"100 array 100 string")
        assert len(interp.stack[-2].value) == 100
        assert len(interp.stack[-1].value) == 100
        with bounded_type1_interpreter():
            with pytest.raises(InterpreterBudgetExceeded, match="memory"):
                _run_ps(b"100 array")

    def test_nested_contexts_stay_bounded_until_outermost_exit(self):
        with bounded_type1_interpreter():
            with bounded_type1_interpreter():
                pass
            with pytest.raises(InterpreterBudgetExceeded):
                _run_ps(STACK_FLOOD)

    def test_state_cleared_after_exception(self):
        with pytest.raises(InterpreterBudgetExceeded):
            with bounded_type1_interpreter():
                _run_ps(STACK_FLOOD)
        assert not type1_interp_guard._is_active()

    def test_install_is_idempotent(self):
        from fontTools.misc import psLib

        type1_interp_guard._install()
        first = psLib.PSInterpreter
        type1_interp_guard._install()
        assert psLib.PSInterpreter is first

    def test_other_threads_unaffected_by_active_guard(self):
        type1_interp_guard._install()
        entered, release = threading.Event(), threading.Event()
        errors = []

        def guarded_thread():
            try:
                with bounded_type1_interpreter():
                    entered.set()
                    release.wait(10)
                    _run_ps(STACK_FLOOD)
            except InterpreterBudgetExceeded:
                errors.append("bounded")

        worker = threading.Thread(target=guarded_thread)
        worker.start()
        try:
            assert entered.wait(10)
            flood = b"0 1 %d {} for" % (MAX_INTERP_STACK + 10)
            assert len(_run_ps(flood).stack) > MAX_INTERP_STACK
        finally:
            release.set()
            worker.join(WALL_CLOCK_CEILING)
        assert errors == ["bounded"]
