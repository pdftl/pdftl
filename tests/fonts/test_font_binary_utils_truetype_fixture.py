# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/fonts/test_font_binary_utils_truetype_fixture.py

"""
Real (non-mocked) end-to-end tests for pdftl.fonts.font_binary_utils against
a genuine, fontTools.fontBuilder-produced sfnt/TrueType font byte stream --
closing part of the gap noted in font_binary_utils.py's module docstring
and tests/fonts/fixtures/font_fixture_builder.py's README: the existing
tests in test_font_binary_utils.py exercise this module purely against
hand-rolled dict-based mocks of fontTools.ttLib.TTFont, never against a
real font program's actual bytes going through fontTools' own decompile/
recompile machinery.

This does not replace the existing mock-based tests (those remain useful
for cheaply covering error/edge branches), it adds a real-bytes baseline
for the main success paths, mirroring how test_cff_binary_utils.py already
does this for the CFF side.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from pdftl.fonts.font_binary_utils import (
    get_font_widths_from_file,
    patch_font_file_metrics,
    squash_font_file_vectors,
)

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from font_fixture_builder import (  # noqa: E402
    SQUARE_500,
    TRIANGLE_300,
    build_truetype_bytes,
)


@pytest.fixture
def simple_ttf_path(tmp_path) -> Path:
    """A genuine, real sfnt/TrueType program with two glyphs beyond .notdef."""
    data = build_truetype_bytes({"A": SQUARE_500, "B": TRIANGLE_300})
    path = tmp_path / "simple.ttf"
    path.write_bytes(data)
    return path


class TestGetFontWidthsFromFileRealTrueType:
    def test_reads_widths_via_cmap(self, simple_ttf_path):
        # build_truetype_bytes sets up an empty cmap (setupCharacterMap({})),
        # matching the CFF fixtures' name-keyed convention -- there is no
        # Unicode code point wired to "A"/"B" here, so the cmap-based read
        # path legitimately returns nothing for this fixture. This assertion
        # exists to document that fact rather than assume it.
        widths = get_font_widths_from_file(simple_ttf_path)
        assert widths == {}

    def test_missing_file_returns_empty_dict(self, tmp_path):
        assert get_font_widths_from_file(tmp_path / "does_not_exist.ttf") == {}

    def test_malformed_bytes_returns_empty_dict(self, tmp_path):
        bad_path = tmp_path / "bad.ttf"
        bad_path.write_bytes(b"this is not a TrueType font at all")
        assert get_font_widths_from_file(bad_path) == {}


class TestPatchAndSquashRealTrueTypeViaDifferences:
    """
    Since the fixture's cmap is empty, /Differences is the natural way to
    reach real glyphs by name -- also exercising the exact
    _resolve_glyph_name priority path (/Differences over cmap) against a
    real font program rather than a dict-based hmtx/glyf stand-in.
    """

    def test_patch_font_file_metrics_via_differences(self, simple_ttf_path):
        # Code 0x41 ('A') is remapped to real glyph "A" via /Differences.
        differences = [0x41, "/A"]
        patched = patch_font_file_metrics(simple_ttf_path, {"41": 777.0}, differences=differences)
        assert patched is not None

        patched_path = simple_ttf_path.parent / "patched.ttf"
        patched_path.write_bytes(patched)

        # Re-read via the same /Differences override to confirm the real
        # hmtx table was actually rewritten (not just accepted silently).
        from fontTools.ttLib import TTFont

        tt = TTFont(patched_path)
        raw_width, _ = tt["hmtx"]["A"]
        scale = 1000.0 / tt["head"].unitsPerEm
        assert raw_width * scale == pytest.approx(777.0)

    def test_patch_font_file_metrics_no_match_returns_none(self, simple_ttf_path):
        assert patch_font_file_metrics(simple_ttf_path, {"FF": 1.0}) is None

    def test_squash_font_file_vectors_via_differences(self, simple_ttf_path):
        differences = [0x42, "/B"]
        squashed = squash_font_file_vectors(
            simple_ttf_path, {"42": 900.0}, differences=differences
        )
        assert squashed is not None

        squashed_path = simple_ttf_path.parent / "squashed.ttf"
        squashed_path.write_bytes(squashed)

        from fontTools.ttLib import TTFont

        tt = TTFont(squashed_path)
        raw_width, _ = tt["hmtx"]["B"]
        scale = 1000.0 / tt["head"].unitsPerEm
        assert raw_width * scale == pytest.approx(900.0)

    def test_squash_font_file_vectors_no_match_returns_none(self, simple_ttf_path):
        assert squash_font_file_vectors(simple_ttf_path, {"FF": 1.0}) is None
