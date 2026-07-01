# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/fonts/test_cff_width_gaps.py

"""
TDD test suite demonstrating architectural gaps in CFF width patching.

Type 2 CharStrings (Adobe TN #5177) do not store the advance width in a
dedicated property. The width is an optional leading numeric operand.
If omitted, the width falls back to defaultWidthX. fontTools evaluates
and stores the calculated width in `.width` during `.draw()`, but it
does not rewrite the underlying `.program` list to insert a missing width.

A direct assignment to `charstring.program[0]` implicitly assumes the
width operand is always physically present in the bytecode, which
corrupts the instruction stream for glyphs using defaultWidthX or
subroutine delegation.
"""

from unittest.mock import MagicMock

from fontTools.cffLib import PrivateDict
from fontTools.misc.psCharStrings import T2CharString

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


def test_patch_corrupts_implicit_default_width():
    """
    A charstring with no explicit width operand uses defaultWidthX.
    Overwriting program[0] destroys the first drawing coordinate instead
    of prepending the new width operand.
    """
    private = PrivateDict()
    private.defaultWidthX = 500.0
    private.nominalWidthX = 100.0

    charstring = T2CharString()
    # 4 operands before the stack-clearing 'hstem' operator.
    # An even number of operands means no width is present (parity rule).
    # program[0] is the coordinate '10', NOT the width.
    charstring.program = [10, 20, 30, 40, "hstem", "endchar"]
    charstring.private = private
    charstring.globalSubrs = []

    topdict = MagicMock()
    topdict.Private = private
    topdict.CharStrings = {"gap_glyph": charstring}

    # Target width is 600.0. The relative width to nominalWidthX (100.0) is 500.0.
    _patch_single_cff_width(topdict, "gap_glyph", 600.0)

    # Decompile to reload the program array from compiled bytecode
    charstring.decompile()

    # The new relative width must be prepended to make the stack length odd.
    # If the patcher overwrites program[0], the coordinate '10' is lost.
    assert charstring.program[0] == 500.0
    assert charstring.program[1] == 10
    assert charstring.program[2] == 20
    assert len(charstring.program) == 7


def test_patch_corrupts_subroutine_delegation():
    """
    A charstring that delegates its execution to a subroutine may rely
    on the subroutine to pop the width. Overwriting program[0] destroys
    the subroutine index or the 'callsubr' operator.
    """
    private = PrivateDict()
    private.nominalWidthX = 100.0
    private.defaultWidthX = 500.0

    subr_charstring = T2CharString()
    # Pushes width (150 relative = 250 actual) and draws.
    subr_charstring.program = [150.0, 10, 20, "rmoveto", "return"]
    subr_charstring.private = private

    charstring = T2CharString()
    # Main program immediately calls global subroutine index 0.
    charstring.program = [0, "callgsubr", "endchar"]
    charstring.private = private
    charstring.globalSubrs = [subr_charstring]

    topdict = MagicMock()
    topdict.Private = private
    topdict.CharStrings = {"gap_subr": charstring}

    # Target width is 600.0. The relative width is 500.0.
    _patch_single_cff_width(topdict, "gap_subr", 600.0)

    # Decompile to reload the program array from compiled bytecode
    charstring.decompile()

    # If the patcher assumes program[0] is the width, it will overwrite the
    # subroutine index '0' with '500.0', causing the font to look for GlobalSubr 500.
    assert charstring.program[0] == 0
    assert charstring.program[1] == "callgsubr"


def _make_mock_charstring_with_subrs(
    program, local_subrs=None, global_subrs=None, nominal_width_x=100.0, default_width_x=0.0
):
    """Extends MockCharString with subr lists for tracing tests."""
    cs = MockCharString(program, nominal_width_x, default_width_x)
    cs.subrs = local_subrs
    cs.globalSubrs = global_subrs if global_subrs is not None else []
    return cs


def test_patch_width_present_before_subroutine_delegation():
    """
    Real-world-correct case: width IS pushed in the MAIN program (per spec,
    a width operand is always pushed before any subr delegation, since
    subroutines are shared across many glyphs with different widths). The
    subroutine itself only supplies the drawing coordinates. The patch must
    land at index 0 of the MAIN program, leaving the subr call untouched.
    """
    subr = MockCharString(program=[10, 20, "rmoveto", "return"])
    # Real index 0, 1 global subr => bias = 107 => pushed operand = 0 - 107 = -107
    main = _make_mock_charstring_with_subrs(
        program=[50.0, -107, "callgsubr", "endchar"],
        global_subrs=[subr],
        nominal_width_x=100.0,
    )
    topdict = MagicMock()
    topdict.CharStrings = {"g": main}

    success = _patch_single_cff_width(topdict, "g", 250.0)  # relative: 150.0

    assert success is True
    assert main.program[0] == 150.0
    assert main.program[1] == -107
    assert main.program[2] == "callgsubr"


def test_patch_width_absent_before_subroutine_delegation():
    """
    Width genuinely absent: main program pushes only the subr index (no
    leading width), subroutine supplies exactly 2 coords for rmoveto
    (even parity => absent). The new relative width must be prepended at
    index 0 of the MAIN program.
    """
    subr = MockCharString(program=[10, 20, "rmoveto", "return"])
    main = _make_mock_charstring_with_subrs(
        program=[-107, "callgsubr", "endchar"],
        global_subrs=[subr],
        nominal_width_x=100.0,
    )
    topdict = MagicMock()
    topdict.CharStrings = {"g": main}

    success = _patch_single_cff_width(topdict, "g", 250.0)  # relative: 150.0

    assert success is True
    assert main.program[0] == 150.0
    assert main.program[1] == -107
    assert main.program[2] == "callgsubr"


def test_patch_subroutine_trace_gives_up_past_depth_guard():
    """
    A pathological/self-referential subr chain must degrade to
    'undeterminable' (False, untouched) rather than looping forever.
    """
    subr_a = MockCharString(program=[])
    subr_b = MockCharString(program=[])
    # subr_a calls subr_b calls subr_a ... (index 0 both ways, bias=107 => -107)
    subr_a.program = [-107, "callgsubr", "return"]
    subr_b.program = [-107, "callgsubr", "return"]
    global_subrs = [subr_a, subr_b]  # length 2, bias still 107 (< 1240)
    main = _make_mock_charstring_with_subrs(
        program=[-107, "callgsubr", "endchar"],
        global_subrs=global_subrs,
    )
    topdict = MagicMock()
    topdict.CharStrings = {"g": main}
    original = list(main.program)

    success = _patch_single_cff_width(topdict, "g", 999.0)

    assert success is False
    assert main.program == original


def test_patch_width_present_before_implicit_vstem_hintmask():
    """hintmask with an odd leading arg count (width + an even number of
    paired implicit vstem hints) means a leading width operand IS present."""
    cs = MockCharString(
        program=[50.0, 10, 20, 30, 40, "hintmask", 0b10101010, "endchar"],
        nominal_width_x=100.0,
    )
    topdict = MagicMock()
    topdict.CharStrings = {"g": cs}

    success = _patch_single_cff_width(topdict, "g", 250.0)

    assert success is True
    assert cs.program[0] == 150.0
    assert cs.program[1] == 10
    assert cs.program[2] == 20


def test_patch_width_absent_before_implicit_vstem_hintmask():
    """hintmask with an even leading arg count means implicit vstem only,
    no width -- must prepend, not overwrite."""
    cs = MockCharString(
        program=[10, 20, 30, 40, "hintmask", 0b10101010, "endchar"], nominal_width_x=100.0
    )
    topdict = MagicMock()
    topdict.CharStrings = {"g": cs}

    success = _patch_single_cff_width(topdict, "g", 250.0)  # relative: 150.0

    assert success is True
    assert cs.program[0] == 150.0
    assert cs.program[1] == 10
    assert cs.program[2] == 20


def test_patch_width_owned_entirely_by_shared_subroutine_is_undeterminable():
    """
    A glyph whose ENTIRE program is just a subr-index push + callgsubr,
    with the width (if any) pushed inside the shared subroutine itself
    rather than in the calling glyph's own (unique) program, must not be
    treated as patchable -- overwriting the subroutine's own leading
    operand would corrupt every other glyph sharing it. This is the real
    shape hit by real AFDKO-built fonts (see
    test_cff_roundtrip_integration.py), not a synthetic edge case.
    """
    # subr itself pushes an "extra" leading operand before its 2 rmoveto
    # coords, making width_present look True if judged in isolation.
    subr = MockCharString(program=[50.0, 10, 20, "rmoveto", "return"])
    main = _make_mock_charstring_with_subrs(
        program=[-107, "callgsubr", "endchar"],  # nothing pushed before delegating
        global_subrs=[subr],
        nominal_width_x=100.0,
    )
    topdict = MagicMock()
    topdict.CharStrings = {"g": main}
    original_main_program = list(main.program)

    success = _patch_single_cff_width(topdict, "g", 250.0)

    assert success is False
    assert main.program == original_main_program  # untouched
