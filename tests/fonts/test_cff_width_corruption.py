# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
TDD failing tests for bare CFF Type 2 charstring width-patching edge cases.
These tests verify that when patching a glyph's advance width:
1. If the width operand is omitted (equals nominalWidthX), we do not corrupt
   the first coordinate operand of the drawing path (e.g. rmoveto).
2. If the width operand is present, we correctly update it in-place.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from pdftl.fonts.cff_binary_utils import _patch_single_cff_width


class MockPrivateDict:
    def __init__(self, nominal_width_x: float = 100.0, default_width_x: float = 0.0):
        self.nominalWidthX = nominal_width_x
        self.defaultWidthX = default_width_x


class MockCharString:
    def __init__(
        self, program: list, nominal_width_x: float = 100.0, default_width_x: float = 0.0
    ):
        self.program = program
        self.private = MockPrivateDict(nominal_width_x, default_width_x)
        self.width = default_width_x
        self.compile = MagicMock()
        self.decompile = MagicMock()

    def draw(self, pen):
        """Simulates fontTools NullPen drawing, which populates .width during interpretation."""
        # For a standard Type 2 operator like 'rmoveto' (takes 2 arguments):
        # We determine if width was present by checking if there's an extra argument.
        # program is of the form: [args..., 'operator', ..., 'endchar']
        operators = {"rmoveto", "hmoveto", "vmoveto", "endchar"}
        args = []
        for item in self.program:
            if isinstance(item, (int, float)):
                args.append(item)
            elif item in operators:
                if item == "rmoveto":
                    # rmoveto expects 2 coordinates. If len(args) == 3, first is width.
                    if len(args) == 3:
                        self.width = args[0] + self.private.nominalWidthX
                    else:
                        self.width = self.private.defaultWidthX
                break


def test_patch_omitted_width_preserves_geometry():
    """
    FAILING TDD TEST:
    When a glyph's width is omitted because it equals the nominal/default width,
    the program's first operand is a coordinate (e.g., rmoveto X coordinate).
    Patching the width must NOT overwrite this coordinate, but instead prepend the width.
    """
    topdict = MagicMock()

    # Program with NO explicit width operand.
    # The first elements (10, 20) are the x and y offsets for 'rmoveto'.
    # Width is omitted and defaults to defaultWidthX (0).
    charstring = MockCharString(
        program=[10, 20, "rmoveto", "endchar"], nominal_width_x=100.0, default_width_x=0.0
    )
    topdict.CharStrings = {"glyph01": charstring}

    # We want to patch the width to 150.0.
    # Expected relative width in the program: 150.0 - nominal_width_x (100.0) = 50.0.
    new_width = 150.0
    success = _patch_single_cff_width(topdict, "glyph01", new_width)

    assert success is True
    # ASSERTION 1: The original geometry coordinates (10, 20) must remain intact!
    # Under the old implementation, charstring.program[0] was overwritten, turning 10 into 50.
    assert charstring.program[1] == 10, "Geometry coordinate was shifted or corrupted!"
    assert charstring.program[2] == 20, "Geometry coordinate was shifted or corrupted!"

    # ASSERTION 2: The relative width operand (50.0) must be prepended at the very start of the stack.
    assert charstring.program[0] == 50.0, "The relative width operand was not prepended!"
    assert charstring.width == 150.0


def test_patch_existing_width_updates_correct_element():
    """
    When a width operand is already present as the first element in the program stack,
    we must update it in-place without prepending a duplicate entry.
    """
    topdict = MagicMock()

    # Program WITH an explicit width operand (50.0 relative, making width = 50.0 + 100.0 = 150.0).
    charstring = MockCharString(
        program=[50.0, 10, 20, "rmoveto", "endchar"], nominal_width_x=100.0, default_width_x=0.0
    )
    topdict.CharStrings = {"glyph01": charstring}

    # Patch the width to 180.0 (relative: 180.0 - 100.0 = 80.0)
    new_width = 180.0
    success = _patch_single_cff_width(topdict, "glyph01", new_width)

    assert success is True
    # The program should update index 0 to 80.0 in-place, preserving coordinates.
    assert charstring.program[0] == 80.0
    assert charstring.program[1] == 10
    assert charstring.program[2] == 20
    assert charstring.width == 180.0


def test_patch_single_cff_width_returns_false_when_presence_undeterminable():
    """
    Directly covers _patch_single_cff_width's own handling of an
    undeterminable width-presence result, rather than relying on the
    subroutine-delegation gap test to exercise it indirectly.
    """
    topdict = MagicMock()
    charstring = MockCharString(
        program=[3, "callsubr", "endchar"],
        nominal_width_x=100.0,
        default_width_x=0.0,
    )
    topdict.CharStrings = {"weird_glyph": charstring}

    success = _patch_single_cff_width(topdict, "weird_glyph", 200.0)

    assert success is False
    # Program must be left completely untouched.
    assert charstring.program == [3, "callsubr", "endchar"]
