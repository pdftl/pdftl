# tests/fonts/test_type1_binary_utils_real_fixture.py

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/fonts/test_type1_binary_utils_real_fixture.py

"""
Real (non-mocked) end-to-end tests for pdftl.fonts.type1_binary_utils
against a genuine Type 1 font program, built via
tests/fonts/fixtures/type1_fixture_builder.py using fontTools' own
eexec-encrypting writer -- closing the gap noted in
type1_binary_utils.py's module docstring: every existing test in
test_type1_binary_utils.py mocks T1Font/T1Error entirely, so nothing
exercises the real eexec encrypt/decrypt round trip through
_open_type1_font's actual file parsing and patch_type1_widths's actual
font.createData() re-serialization.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pdftl.fonts.type1_binary_utils import get_widths_from_type1, patch_type1_widths

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from type1_fixture_builder import build_type1_bytes  # noqa: E402


@pytest.fixture
def simple_type1_path(tmp_path) -> Path:
    """A genuine, real Type 1 font program with two glyphs beyond a
    trivial path, written through fontTools' own eexec encryption."""
    data = build_type1_bytes(
        {
            "A": (500, [0, 0, "rmoveto", 500, 0, "rlineto"]),
            "B": (300, [0, 0, "rmoveto", 300, 0, "rlineto"]),
        }
    )
    path = tmp_path / "simple.pfb"
    path.write_bytes(data)
    return path


class TestGetWidthsFromType1RealFont:
    def test_reads_real_eexec_encrypted_widths(self, simple_type1_path):
        widths = get_widths_from_type1(simple_type1_path)
        assert widths == {"A": 500, "B": 300}

    def test_missing_file_returns_empty_dict(self, tmp_path):
        assert get_widths_from_type1(tmp_path / "does_not_exist.pfb") == {}

    def test_malformed_bytes_returns_empty_dict(self, tmp_path):
        bad_path = tmp_path / "bad.pfb"
        bad_path.write_bytes(b"this is not a Type 1 font at all")
        assert get_widths_from_type1(bad_path) == {}


class TestPatchType1WidthsRealFont:
    def test_patches_and_reserializes_through_real_eexec(self, simple_type1_path, tmp_path):
        patched_bytes = patch_type1_widths(simple_type1_path, {"A": 777.0})
        assert patched_bytes is not None

        patched_path = tmp_path / "patched.pfb"
        patched_path.write_bytes(patched_bytes)

        reread = get_widths_from_type1(patched_path)
        assert reread["A"] == 777.0
        assert reread["B"] == 300  # untouched

    def test_patches_multiple_glyphs_independently(self, simple_type1_path, tmp_path):
        patched_bytes = patch_type1_widths(simple_type1_path, {"A": 111.0, "B": 222.0})
        assert patched_bytes is not None

        patched_path = tmp_path / "patched.pfb"
        patched_path.write_bytes(patched_bytes)

        reread = get_widths_from_type1(patched_path)
        assert reread == {"A": 111.0, "B": 222.0}

    def test_no_matching_glyph_returns_none(self, simple_type1_path):
        assert patch_type1_widths(simple_type1_path, {"NoSuchGlyph": 1.0}) is None
