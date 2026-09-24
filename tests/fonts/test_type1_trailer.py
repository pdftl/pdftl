# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/fonts/test_type1_trailer.py

"""
Tests for pdftl.fonts.type1_trailer: detecting and completing a Type 1
program whose optional eexec trailer was omitted (ISO 32000-2 Table 125,
/Length3 0).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from pdftl.fonts.type1_trailer import (
    MAX_SHORT_ZERO_BLOCKS,
    TRAILER,
    ZeroRunTooIrregular,
    completed,
    completed_if_needed,
    needs_completion,
)

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from type1_fixture_builder import build_type1_bytes  # noqa: E402

_GLYPHS = {
    ".notdef": (0, []),
    "A": (500, [0, 0, "rmoveto", 500, 0, "rlineto"]),
    "B": (300, [0, 0, "rmoveto", 300, 0, "rlineto"]),
}

_HEADER = b"%!FontType1-1.0: Test\n/FontName /Test def\ncurrentfile eexec\n"
_CIPHER = bytes(range(0x80, 0x100)) * 4


@pytest.fixture(scope="module")
def full_program() -> bytes:
    return build_type1_bytes(_GLYPHS)


def _trailer_start(program: bytes) -> int:
    return program.index(b"0" * 64, program.index(b"currentfile eexec"))


def _parse_widths(program: bytes, tmp_path: Path) -> dict[str, float]:
    """Parses via fontTools directly (no pdftl code) and reads each glyph's
    hsbw width operand, as ground truth independent of the module under
    test."""
    from fontTools.t1Lib import T1Font

    path = tmp_path / "font.t1"
    path.write_bytes(program)
    font = T1Font(str(path), kind="OTHER")
    font.parse()
    widths = {}
    for name, cs in font.font["CharStrings"].items():
        cs.decompile()
        widths[name] = cs.program[cs.program.index("hsbw") - 1]
    return widths


class TestNeedsCompletion:
    def test_full_trailer_present(self, full_program):
        assert needs_completion(full_program) is False

    def test_trailer_omitted(self, full_program):
        assert needs_completion(full_program[: _trailer_start(full_program)]) is True

    def test_trailer_truncated_midway(self, full_program):
        start = _trailer_start(full_program)
        partial = full_program[:start] + b"0" * 64 + b"\n" + b"0" * 64
        assert needs_completion(partial) is True

    def test_only_cleartomark_missing(self, full_program):
        # fontTools needs only the zero run, not `cleartomark`.
        without = full_program[: full_program.rindex(b"cleartomark")]
        assert needs_completion(without) is False

    def test_pfb_segmented_program_never_completed(self):
        assert needs_completion(b"\x80\x01" + _HEADER + _CIPHER) is False

    def test_no_eexec_marker(self):
        assert needs_completion(b"%!FontType1-1.0: Test\n/FontName /Test def\n") is False

    @pytest.mark.parametrize("sep", [b"\n", b"\r", b"\r\n", b"\t", b" "])
    def test_whitespace_separated_trailer_recognized(self, sep):
        program = _HEADER + _CIPHER + sep + sep.join([b"0" * 64] * 8) + sep + b"cleartomark"
        assert needs_completion(program) is False

    def test_zeros_split_mid_line_recognized(self):
        zeros = b"0" * 512
        program = _HEADER + _CIPHER + b"\n" + zeros[:7] + b" \r\n\t" + zeros[7:]
        assert needs_completion(program) is False

    def test_long_trailer_is_not_irregular(self):
        program = _HEADER + _CIPHER + b"\n" + b"0" * 2048 + b"\ncleartomark\n"
        assert needs_completion(program) is False

    def test_short_zero_runs_up_to_bound_accepted(self):
        runs = b"x".join([b"0" * 16] * MAX_SHORT_ZERO_BLOCKS)
        assert needs_completion(_HEADER + runs) is True

    def test_short_zero_runs_past_bound_refused(self):
        runs = b"x".join([b"0" * 16] * (MAX_SHORT_ZERO_BLOCKS + 1))
        with pytest.raises(ZeroRunTooIrregular):
            needs_completion(_HEADER + runs)

    def test_pathological_zero_runs_refused_quickly(self):
        # Runs just short of 512 are the worst case for t1Lib's own scan.
        runs = b"x".join([b"0" * 511] * 4000)
        start = time.monotonic()
        with pytest.raises(ZeroRunTooIrregular):
            needs_completion(_HEADER + runs + b"0" * 512)
        assert time.monotonic() - start < 2.0

    def test_refusal_message_is_fixed(self):
        runs = b"x".join([b"0" * 16] * (MAX_SHORT_ZERO_BLOCKS + 1))
        with pytest.raises(ZeroRunTooIrregular) as info:
            needs_completion(_HEADER + b"/Secret" + runs)
        assert "Secret" not in str(info.value)


class TestCompleted:
    def test_trailer_shape(self):
        assert TRAILER.count(b"0") == 512
        assert TRAILER.rstrip().endswith(b"cleartomark")

    def test_appends_trailer(self):
        assert completed(b"abc") == b"abc" + TRAILER

    def test_completed_omitted_trailer_parses(self, full_program, tmp_path):
        stripped = full_program[: _trailer_start(full_program)]
        assert _parse_widths(completed(stripped), tmp_path) == {".notdef": 0, "A": 500, "B": 300}

    def test_completed_partial_trailer_parses(self, full_program, tmp_path):
        partial = full_program[: _trailer_start(full_program)] + b"0" * 128
        assert _parse_widths(completed(partial), tmp_path) == {".notdef": 0, "A": 500, "B": 300}

    def test_short_real_world_trailer_completed(self, full_program, tmp_path):
        # Shape seen in real TeX Live fonts: 7 lines of zeros, then cleartomark.
        short = full_program[: _trailer_start(full_program)] + (b"0" * 64 + b"\n") * 7
        short += b"cleartomark\n"
        assert needs_completion(short) is True
        assert _parse_widths(completed(short), tmp_path) == {".notdef": 0, "A": 500, "B": 300}

    def test_completed_if_needed_leaves_complete_program_alone(self, full_program):
        assert completed_if_needed(full_program) is full_program

    def test_completed_if_needed_completes_stripped_program(self, full_program):
        stripped = full_program[: _trailer_start(full_program)]
        assert completed_if_needed(stripped) == stripped + TRAILER
