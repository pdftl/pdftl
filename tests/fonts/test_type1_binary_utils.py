# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/fonts/test_type1_binary_utils.py

"""
Unit tests for pdftl.fonts.type1_binary_utils: fontTools.t1Lib-based reading
and mutation of advance-width metrics inside classic Type 1 font programs.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pdftl.fonts.type1_binary_utils import (
    _find_width_operator,
    _open_type1_font,
    _read_charstring_width,
    _patch_single_type1_width,
    get_widths_from_type1,
    patch_type1_widths,
)


class DummyCharString:
    """Mock charstring program designed to test parsing and patching branches."""

    def __init__(self, program: list, fail_decompile: bool = False, fail_compile: bool = False):
        self.program = program
        self.width = None
        self._fail_decompile = fail_decompile
        self._fail_compile = fail_compile

    def decompile(self):
        if self._fail_decompile:
            raise ValueError("Simulated decompile failure")

    def compile(self):
        if self._fail_compile:
            raise ValueError("Simulated compile failure")


class DummyT1Font:
    """Mock T1Font matching fontTools.t1Lib.T1Font interface."""

    def __init__(
        self,
        filepath: str,
        kind: str | None = None,
        fail_parse: bool = False,
        fail_create: bool = False,
    ):
        self.filepath = filepath
        self.kind = kind
        self.font = {"CharStrings": {}}
        self._fail_parse = fail_parse
        self._fail_create = fail_create

    def parse(self):
        if self._fail_parse:
            raise ValueError("Simulated parse failure")

    def createData(self):
        if self._fail_create:
            raise ValueError("Simulated createData failure")
        return b"mock_recompiled_font_data"


@pytest.fixture
def mock_t1lib(monkeypatch):
    """Mocks fontTools.t1Lib inside the module namespace."""

    class T1Error(Exception):
        pass

    class PSError(Exception):
        pass

    class PSTokenError(Exception):
        pass

    mock_module = MagicMock()
    mock_module.T1Font = DummyT1Font
    mock_module.T1Error = T1Error
    mock_module.PSError = PSError
    mock_module.PSTokenError = PSTokenError

    monkeypatch.setitem(sys.modules, "fontTools.t1Lib", mock_module)
    return mock_module


# ---------------------------------------------------------------------------
# Core Private Function Tests
# ---------------------------------------------------------------------------


def test_open_type1_font_utilizes_other_kind(mock_t1lib):
    """Ensures _open_type1_font correctly enforces kind='OTHER' and parses."""
    font = _open_type1_font(Path("mock_font.pfb"))
    assert isinstance(font, DummyT1Font)
    assert font.kind == "OTHER"


def test_find_width_operator_locates_hsbw_and_sbw():
    """Verify that hsbw and sbw tokens are matched correctly, and invalid states return None."""
    # Standard hsbw
    idx, op = _find_width_operator([10, 200, "hsbw"])
    assert idx == 2
    assert op == "hsbw"

    # Standard sbw
    idx, op = _find_width_operator([10, 20, 300, 40, "sbw"])
    assert idx == 4
    assert op == "sbw"

    # Missing operator
    idx, op = _find_width_operator([10, 20, "other"])
    assert idx is None
    assert op is None


def test_read_charstring_width_handles_decompile_exceptions():
    """Verify that decompile errors are caught and return None."""
    cs = DummyCharString([10, 200, "hsbw"], fail_decompile=True)
    assert _read_charstring_width(cs) is None


def test_read_charstring_width_missing_operator():
    """Verify that charstrings lacking width operators return None."""
    cs = DummyCharString([10, 200, "other_op"])
    assert _read_charstring_width(cs) is None


def test_read_charstring_width_malformed_stack():
    """Verify that a truncated or malformed program operand stack returns None."""
    # No operands before the operator
    cs = DummyCharString(["hsbw"])
    assert _read_charstring_width(cs) is None


def test_read_charstring_width_non_literal_operand():
    """Verify that non-literal or dynamic width sub-expressions are skipped."""
    cs = DummyCharString(["div", "hsbw"])
    assert _read_charstring_width(cs) is None


def test_read_charstring_width_success():
    """Verify successful extraction of both hsbw and sbw widths."""
    cs_hsbw = DummyCharString([10, 200, "hsbw"])
    assert _read_charstring_width(cs_hsbw) == 200

    cs_sbw = DummyCharString([10, 20, 300, 40, "sbw"])
    assert _read_charstring_width(cs_sbw) == 300


# ---------------------------------------------------------------------------
# get_widths_from_type1 Tests
# ---------------------------------------------------------------------------


def test_get_widths_from_type1_import_error(monkeypatch):
    """Verify that get_widths_from_type1 returns an empty dict if fontTools is unavailable."""
    monkeypatch.setitem(sys.modules, "fontTools.t1Lib", None)
    assert get_widths_from_type1(Path("test.pfb")) == {}


def test_get_widths_from_type1_parse_error(mock_t1lib, monkeypatch):
    """Verify that font file parsing failures are caught gracefully."""

    def raising_open(filepath):
        raise ValueError("Simulated parse failure")

    monkeypatch.setattr("pdftl.fonts.type1_binary_utils._open_type1_font", raising_open)
    assert get_widths_from_type1(Path("corrupt.pfb")) == {}


def test_get_widths_from_type1_success(mock_t1lib):
    """Verify successful extraction of multiple glyph widths from a mock Type 1 font."""
    font = _open_type1_font(Path("test.pfb"))
    font.font["CharStrings"] = {
        "A": DummyCharString([10, 250, "hsbw"]),
        "B": DummyCharString([10, 20, 300, 40, "sbw"]),
        "C": DummyCharString(["other_op"]),  # unparseable
    }

    # Patch our test instance into the open function so it is resolved
    with patch("pdftl.fonts.type1_binary_utils._open_type1_font", return_value=font):
        widths = get_widths_from_type1(Path("test.pfb"))
        assert widths == {"A": 250, "B": 300}


# ---------------------------------------------------------------------------
# _patch_single_type1_width Tests
# ---------------------------------------------------------------------------


def test_patch_single_type1_width_decompile_error():
    """Verify patching aborts and returns False on decompile failure."""
    cs = DummyCharString([10, 200, "hsbw"], fail_decompile=True)
    assert _patch_single_type1_width(cs, 999.0) is False


def test_patch_single_type1_width_missing_operator():
    """Verify patching aborts and returns False if no width operator is found."""
    cs = DummyCharString([10, 200, "other"])
    assert _patch_single_type1_width(cs, 999.0) is False


def test_patch_single_type1_width_malformed_stack():
    """Verify patching aborts and returns False on malformed operand stack."""
    cs = DummyCharString(["hsbw"])
    assert _patch_single_type1_width(cs, 999.0) is False


def test_patch_single_type1_width_compile_error():
    """Verify patching aborts and returns False on compile failure."""
    cs = DummyCharString([10, 200, "hsbw"], fail_compile=True)
    assert _patch_single_type1_width(cs, 999.0) is False


def test_patch_single_type1_width_success():
    """Verify successful in-place width modification and compilation."""
    cs = DummyCharString([10, 200, "hsbw"])
    assert _patch_single_type1_width(cs, 550.0) is True
    assert cs.program[1] == 550.0
    assert cs.width == 550.0


# ---------------------------------------------------------------------------
# patch_type1_widths Tests
# ---------------------------------------------------------------------------


def test_patch_type1_widths_import_error(monkeypatch):
    """Verify that patch_type1_widths returns None if fontTools is unavailable."""
    monkeypatch.setitem(sys.modules, "fontTools.t1Lib", None)
    assert patch_type1_widths(Path("test.pfb"), {"A": 500.0}) is None


def test_patch_type1_widths_parse_error(mock_t1lib, monkeypatch):
    """Verify that parsing errors return None gracefully."""

    def raising_open(filepath):
        raise ValueError("Simulated parse failure")

    monkeypatch.setattr("pdftl.fonts.type1_binary_utils._open_type1_font", raising_open)
    assert patch_type1_widths(Path("corrupt.pfb"), {"A": 500.0}) is None


def test_patch_type1_widths_no_matching_glyphs(mock_t1lib):
    """Verify that if no requested glyphs match the font program, nothing is patched."""
    font = _open_type1_font(Path("test.pfb"))
    font.font["CharStrings"] = {
        "A": DummyCharString([10, 250, "hsbw"]),
    }

    with patch("pdftl.fonts.type1_binary_utils._open_type1_font", return_value=font):
        # "B" is not in CharStrings, and "C" fails to patch
        assert patch_type1_widths(Path("test.pfb"), {"B": 500.0}) is None


def test_patch_type1_widths_compile_error(mock_t1lib):
    """Verify that overall re-compilation failures are caught safely."""
    font = _open_type1_font(Path("test.pfb"))
    font._fail_create = True
    font.font["CharStrings"] = {
        "A": DummyCharString([10, 250, "hsbw"]),
    }

    with patch("pdftl.fonts.type1_binary_utils._open_type1_font", return_value=font):
        assert patch_type1_widths(Path("test.pfb"), {"A": 500.0}) is None


def test_patch_type1_widths_success(mock_t1lib):
    """Verify successful multi-glyph patching and font program serialization."""
    font = _open_type1_font(Path("test.pfb"))
    cs_a = DummyCharString([10, 250, "hsbw"])
    cs_b = DummyCharString([10, 20, 300, 40, "sbw"])
    font.font["CharStrings"] = {
        "A": cs_a,
        "B": cs_b,
    }

    with patch("pdftl.fonts.type1_binary_utils._open_type1_font", return_value=font):
        data = patch_type1_widths(Path("test.pfb"), {"A": 600.0, "B": 700.0})
        assert data == b"mock_recompiled_font_data"
        assert cs_a.program[1] == 600.0
        assert cs_b.program[2] == 700.0
