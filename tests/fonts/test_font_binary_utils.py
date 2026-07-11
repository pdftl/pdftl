# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/fonts/test_font_binary_utils.py

"""
Unit tests for pdftl.fonts.font_binary_utils: pure fontTools-based reading
and mutation of advance-width metrics inside TTF/OTF binaries.
"""

from __future__ import annotations

import sys
from pathlib import Path

from pdftl.fonts.font_binary_utils import (
    get_font_widths_from_file,
    patch_font_file_metrics,
    squash_font_file_vectors,
    _expand_differences_map,
    _resolve_glyph_name,
    _resolve_cid_glyph_name,
    _collect_target_codes,
    _patch_single_metric,
    _patch_single_cid_metric,
    _process_glyph_squash,
    _process_cid_glyph_squash,
    _squash_cid_glyphs,
    _build_winansi_encoding_table,
    _build_macroman_encoding_table,
    _build_standard_encoding_table,
    _get_base_encoding_table,
)


def _mock_font_tools(monkeypatch):
    """Mocks fontTools to safely simulate ttLib behavior without depending on it."""

    class DummyGlyph:
        def __init__(self):
            self.coordinates = [(100.0, 200.0)]
            self.components = []

    class DummyTTFont:
        def __init__(self, filepath=None):
            self.filepath = filepath
            self.head = type("Head", (), {"unitsPerEm": 1000})()
            self.cmap = {1: "A", 2: "B"}
            self.hmtx = {"A": [250.0, 0], "B": [500.0, 0]}
            self.glyf = {"A": DummyGlyph(), "B": DummyGlyph()}

        def getBestCmap(self, cmapPreferences=None):
            return self.cmap

        def __getitem__(self, key):
            if key == "hmtx":
                return type(
                    "Hmtx",
                    (),
                    {
                        "metrics": self.hmtx,
                        "__getitem__": lambda s, k: self.hmtx[k],
                        "__setitem__": lambda s, k, v: self.hmtx.update({k: v}),
                    },
                )()
            if key == "glyf":
                return self.glyf
            return getattr(self, key)

        def __contains__(self, key):
            return key in ("glyf", "hmtx")

        def save(self, path_or_stream):
            if hasattr(path_or_stream, "write"):
                path_or_stream.write(b"mock_saved_bytes")

    monkeypatch.setattr("fontTools.ttLib.TTFont", DummyTTFont)


def _mock_font_tools_with_extra_glyph(monkeypatch):
    """
    Like _mock_font_tools, but the font's hmtx/glyf tables carry a third glyph
    "C" that is NOT reachable via the font's own cmap (codes 1 and 2 only map
    to "A" and "B"). This simulates a font where a /Differences-remapped code
    points at a real glyph the font's own Unicode cmap never exposes.
    """

    class DummyGlyph:
        def __init__(self):
            self.coordinates = [(100.0, 200.0)]
            self.components = []

    class DummyTTFont:
        def __init__(self, filepath=None):
            self.filepath = filepath
            self.head = type("Head", (), {"unitsPerEm": 1000})()
            self.cmap = {1: "A", 2: "B"}
            self.hmtx = {"A": [250.0, 0], "B": [500.0, 0], "C": [300.0, 0]}
            self.glyf = {"A": DummyGlyph(), "B": DummyGlyph(), "C": DummyGlyph()}

        def getBestCmap(self, cmapPreferences=None):
            return self.cmap

        def __getitem__(self, key):
            if key == "hmtx":
                return type(
                    "Hmtx",
                    (),
                    {
                        "metrics": self.hmtx,
                        "__getitem__": lambda s, k: self.hmtx[k],
                        "__setitem__": lambda s, k, v: self.hmtx.update({k: v}),
                    },
                )()
            if key == "glyf":
                return self.glyf
            return getattr(self, key)

        def __contains__(self, key):
            return key in ("glyf", "hmtx")

        def save(self, path_or_stream):
            if hasattr(path_or_stream, "write"):
                path_or_stream.write(b"mock_saved_bytes")

    monkeypatch.setattr("fontTools.ttLib.TTFont", DummyTTFont)


# ---------------------------------------------------------------------------
# ImportError branches (blocked via sys.modules trick)
# ---------------------------------------------------------------------------


def test_get_font_widths_import_error(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "fontTools.ttLib", None)
    assert get_font_widths_from_file(tmp_path / "x.ttf") == {}


def test_patch_font_file_metrics_import_error(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "fontTools.ttLib", None)
    f = tmp_path / "x.ttf"
    f.write_bytes(b"x")
    # Should safely return None
    assert patch_font_file_metrics(f, {"41": 100.0}) is None


def test_squash_font_file_vectors_import_error(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "fontTools.ttLib", None)
    f = tmp_path / "x.ttf"
    f.write_bytes(b"x")
    assert squash_font_file_vectors(f, {"41": 100.0}) is None


# ---------------------------------------------------------------------------
# get_font_widths_from_file: success and error handler coverage
# ---------------------------------------------------------------------------


def test_get_font_widths_success(tmp_path, monkeypatch):
    """Successfully reads font widths, covering loop, scaling, and dictionary population."""
    _mock_font_tools(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    widths = get_font_widths_from_file(f)
    assert widths == {"01": 250.0, "02": 500.0}


def test_get_font_widths_inner_exception(tmp_path, monkeypatch):
    """Triggers the inner except block in get_font_widths_from_file (lines 42-56)."""

    class BadFont:
        def __init__(self, filepath=None):
            raise ValueError("simulated corrupt font file structures")

    monkeypatch.setattr("fontTools.ttLib.TTFont", BadFont)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    assert get_font_widths_from_file(f) == {}


# ---------------------------------------------------------------------------
# patch_font_file_metrics: except clause + key fallbacks + no-match paths
# ---------------------------------------------------------------------------


def test_patch_font_file_metrics_exception_on_missing_file(tmp_path):
    # Real fontTools; TTFont() on a nonexistent/garbage file raises -> caught.
    missing = tmp_path / "nope.ttf"
    assert patch_font_file_metrics(missing, {"41": 100.0}) is None


def test_patch_font_file_metrics_no_match(tmp_path, monkeypatch):
    """Ensures patch_font_file_metrics returns None if no hex keys matched the font cmap."""
    _mock_font_tools(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    # No target hex keys ("99") match the mock cmap ("01", "02").
    # Thus patched_any remains False, triggering the early return block.
    assert patch_font_file_metrics(f, {"99": 500.0}) is None


def test_patch_font_file_metrics_four_digit_fallback(tmp_path, monkeypatch):
    _mock_font_tools(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    # cmap codes 1, 2 -> 2-digit hex "01"/"02" not in pdf_widths, but the
    # 4-digit fallback "0001"/"0002" is -> exercises the fallback branch.
    res = patch_font_file_metrics(f, {"0001": 999.0, "0002": 888.0})
    assert res == b"mock_saved_bytes"


def test_patch_font_file_metrics_success_main_branch(tmp_path, monkeypatch):
    """Covers lines where the 2-digit hex key is directly found in pdf_widths."""
    _mock_font_tools(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    # Direct matching of "01" and "02" hex keys
    res = patch_font_file_metrics(f, {"01": 999.0, "02": 888.0})
    assert res == b"mock_saved_bytes"


def test_patch_font_file_metrics_inner_exception(tmp_path, monkeypatch):
    """Triggers the inner except block in patch_font_file_metrics."""

    class BadFont:
        def __init__(self, filepath=None):
            pass

        def getBestCmap(self, cmapPreferences=None):
            raise KeyError("missing cmap table")

    monkeypatch.setattr("fontTools.ttLib.TTFont", BadFont)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    assert patch_font_file_metrics(f, {"01": 999.0}) is None


# ---------------------------------------------------------------------------
# squash_font_file_vectors: exceptions, guards, continue paths, components & coordinates
# ---------------------------------------------------------------------------


def test_squash_font_file_vectors_exception_on_missing_file(tmp_path):
    missing = tmp_path / "nope.ttf"
    assert squash_font_file_vectors(missing, {"41": 100.0}) is None


def test_squash_font_file_vectors_no_glyf_table(tmp_path, monkeypatch):
    class NoGlyfTTFont:
        def __init__(self, filepath=None):
            self.head = type("Head", (), {"unitsPerEm": 1000})()
            self.cmap = {1: "A"}
            self.hmtx = {"A": [250.0, 0]}

        def getBestCmap(self, cmapPreferences=None):
            return self.cmap

        def __getitem__(self, key):
            if key == "hmtx":
                return type("Hmtx", (), {"__getitem__": lambda s, k: self.hmtx[k]})()
            return getattr(self, key)

        def __contains__(self, key):
            return key == "hmtx"  # no "glyf" -> triggers the guard

        def save(self, path):
            pass

    monkeypatch.setattr("fontTools.ttLib.TTFont", NoGlyfTTFont)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    assert squash_font_file_vectors(f, {"41": 500.0}) is None


def test_squash_font_file_vectors_continue_branches(tmp_path, monkeypatch):
    class DummyGlyph:
        def __init__(self):
            self.coordinates = [(10.0, 20.0)]

    class DummyTTFont:
        def __init__(self, filepath=None):
            self.head = type("Head", (), {"unitsPerEm": 1000})()
            self.cmap = {1: "A", 2: "B"}
            self.hmtx = {"A": [250.0, 0], "B": [500.0, 0]}
            self.glyf = {"A": DummyGlyph(), "B": DummyGlyph()}

        def getBestCmap(self, cmapPreferences=None):
            return self.cmap

        def __getitem__(self, key):
            if key == "hmtx":
                return type(
                    "Hmtx",
                    (),
                    {
                        "__getitem__": lambda s, k: self.hmtx[k],
                        "__setitem__": lambda s, k, v: self.hmtx.update({k: v}),
                    },
                )()
            if key == "glyf":
                return self.glyf
            return getattr(self, key)

        def __contains__(self, key):
            return key in ("glyf", "hmtx")

        def save(self, path):
            pass

    monkeypatch.setattr("fontTools.ttLib.TTFont", DummyTTFont)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    # "A" (code 1) has no entry at all -> target_hex not in pdf_widths -> continue
    # "B" (code 2) has a zero width -> pdf_w <= 0 -> continue
    # Since neither branch scales, it skips saving and returns None
    assert squash_font_file_vectors(f, {"02": 0.0}) is None


def test_squash_font_file_vectors_components_branch(tmp_path, monkeypatch):
    class DummyTransform:
        def scale(self, x, y):
            return self

    class DummyComponent:
        def __init__(self):
            self.transform = DummyTransform()

    class DummyGlyphWithComponents:
        def __init__(self):
            self.components = [DummyComponent()]
            # deliberately no .coordinates attribute

    class DummyTTFont:
        def __init__(self, filepath=None):
            self.head = type("Head", (), {"unitsPerEm": 1000})()
            self.cmap = {1: "A"}
            self.hmtx = {"A": [250.0, 0]}
            self.glyf = {"A": DummyGlyphWithComponents()}

        def getBestCmap(self, cmapPreferences=None):
            return self.cmap

        def __getitem__(self, key):
            if key == "hmtx":
                return type(
                    "Hmtx",
                    (),
                    {
                        "__getitem__": lambda s, k: self.hmtx[k],
                        "__setitem__": lambda s, k, v: self.hmtx.update({k: v}),
                    },
                )()
            if key == "glyf":
                return self.glyf
            return getattr(self, key)

        def __contains__(self, key):
            return key in ("glyf", "hmtx")

        def save(self, path_or_stream):
            if hasattr(path_or_stream, "write"):
                path_or_stream.write(b"mock_saved_bytes")

    monkeypatch.setattr("fontTools.ttLib.TTFont", DummyTTFont)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    # width 500 vs font width 250 -> ratio=2.0, well outside the 1e-3 tolerance
    res = squash_font_file_vectors(f, {"01": 500.0})
    assert res == b"mock_saved_bytes"


def test_squash_font_file_vectors_coordinates_branch(tmp_path, monkeypatch):
    """Successfully squashes coordinates of a TrueType glyph, covering save, log & loop success."""
    _mock_font_tools(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    # Triggers coordinate modification branch and successfully saves
    res = squash_font_file_vectors(f, {"01": 500.0, "02": 1000.0})
    assert res == b"mock_saved_bytes"


def test_squash_font_file_vectors_ratio_close_to_one(tmp_path, monkeypatch):
    """Line 148: Skips squashing if target width is extremely close to font width (ratio ~ 1.0)."""
    _mock_font_tools(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    # "A" (code 1) has native font width 250.0. Specifying 250.1 makes ratio 1.0004.
    # The deviation is 0.0004 (which is less than the 1e-3 tolerance threshold), triggering 'continue'.
    assert squash_font_file_vectors(f, {"01": 250.1}) is None


def test_squash_font_file_vectors_inner_exception(tmp_path, monkeypatch):
    """Triggers the inner except block in squash_font_file_vectors."""

    class BadFont:
        def __init__(self, filepath=None):
            pass

        def getBestCmap(self, cmapPreferences=None):
            raise KeyError("missing cmap table")

    monkeypatch.setattr("fontTools.ttLib.TTFont", BadFont)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    assert squash_font_file_vectors(f, {"01": 999.0}) is None


# ---------------------------------------------------------------------------
# Zero/None unitsPerEm guard conditions (covers coverage gaps)
# ---------------------------------------------------------------------------


def test_get_font_widths_zero_units_per_em(tmp_path, monkeypatch):
    """Line 36-40: Exits early with empty width map if unitsPerEm is 0."""

    class ZeroUnitsFont:
        def __init__(self, filepath=None):
            self.head = type("Head", (), {"unitsPerEm": 0})()
            self.cmap = {1: "A"}
            self.hmtx = {"A": [250.0, 0]}

        def getBestCmap(self, cmapPreferences=None):
            return self.cmap

        def __getitem__(self, key):
            if key == "hmtx":
                return type("Hmtx", (), {"__getitem__": lambda s, k: self.hmtx[k]})()
            return getattr(self, key)

        def __contains__(self, key):
            return key == "hmtx"

    monkeypatch.setattr("fontTools.ttLib.TTFont", ZeroUnitsFont)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    assert get_font_widths_from_file(f) == {}


def test_patch_font_file_metrics_zero_units_per_em(tmp_path, monkeypatch):
    """Line 74-78: Exits early without patching if unitsPerEm is 0."""

    class ZeroUnitsFont:
        def __init__(self, filepath=None):
            self.head = type("Head", (), {"unitsPerEm": 0})()
            self.cmap = {1: "A"}
            self.hmtx = {"A": [250.0, 0]}

        def getBestCmap(self, cmapPreferences=None):
            return self.cmap

        def __getitem__(self, key):
            if key == "hmtx":
                return type("Hmtx", (), {"__getitem__": lambda s, k: self.hmtx[k]})()
            return getattr(self, key)

        def __contains__(self, key):
            return key == "hmtx"

        def save(self, path):
            pass

    monkeypatch.setattr("fontTools.ttLib.TTFont", ZeroUnitsFont)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    assert patch_font_file_metrics(f, {"01": 500.0}) is None


def test_squash_font_file_vectors_zero_units_per_em(tmp_path, monkeypatch):
    """Line 120-124: Exits early without squashing if unitsPerEm is 0."""

    class ZeroUnitsFont:
        def __init__(self, filepath=None):
            self.head = type("Head", (), {"unitsPerEm": 0})()
            self.cmap = {1: "A"}
            self.hmtx = {"A": [250.0, 0]}

        def getBestCmap(self, cmapPreferences=None):
            return self.cmap

        def __getitem__(self, key):
            if key == "hmtx":
                return type("Hmtx", (), {"__getitem__": lambda s, k: self.hmtx[k]})()
            return getattr(self, key)

        def __contains__(self, key):
            return key == "hmtx"

        def save(self, path):
            pass

    monkeypatch.setattr("fontTools.ttLib.TTFont", ZeroUnitsFont)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    assert squash_font_file_vectors(f, {"01": 500.0}) is None


# ---------------------------------------------------------------------------
# _expand_differences_map: raw /Differences array -> flat hex-code -> glyph-name map
# ---------------------------------------------------------------------------


def test_expand_differences_map_sequential_run():
    """A single code followed by multiple names implicitly increments the code."""
    diffs = [1, "/A", "/B", "/C"]
    assert _expand_differences_map(diffs) == {"01": "A", "02": "B", "03": "C"}


def test_expand_differences_map_multiple_code_blocks():
    """Multiple code/name blocks in one array are each parsed independently."""
    diffs = [1, "/A", 10, "/B", "/C"]
    assert _expand_differences_map(diffs) == {"01": "A", "0A": "B", "0B": "C"}


def test_expand_differences_map_leading_name_skipped():
    """A glyph name appearing before any code entry is malformed; it is skipped."""
    diffs = ["/Orphan", 1, "/A"]
    assert _expand_differences_map(diffs) == {"01": "A"}


def test_expand_differences_map_non_string_non_int_item_stringified():
    """A non-str, non-int item (e.g. a float, as might survive odd serialization)
    is stringified rather than raising."""
    diffs = [1, 3.5]
    assert _expand_differences_map(diffs) == {"01": "3.5"}


def test_expand_differences_map_bool_item_skipped():
    """bool is a subclass of int in Python; defensively treated as a non-code token."""
    diffs = [True, 1, "/A"]
    assert _expand_differences_map(diffs) == {"01": "A"}


def test_expand_differences_map_empty_list():
    assert _expand_differences_map([]) == {}


# ---------------------------------------------------------------------------
# _resolve_glyph_name
# ---------------------------------------------------------------------------


def test_resolve_glyph_name_prefers_differences_override():
    assert _resolve_glyph_name(1, "A", {"01": "B"}) == "B"


def test_resolve_glyph_name_falls_back_to_cmap_name():
    assert _resolve_glyph_name(1, "A", {"02": "B"}) == "A"


def test_resolve_glyph_name_no_differences_map():
    assert _resolve_glyph_name(1, "A", None) == "A"


def test_resolve_glyph_name_no_cmap_name_and_no_override():
    assert _resolve_glyph_name(1, None, None) is None


def test_resolve_glyph_name_no_cmap_name_but_override_present():
    """A code absent from the font's own cmap can still resolve via Differences."""
    assert _resolve_glyph_name(3, None, {"03": "C"}) == "C"


# ---------------------------------------------------------------------------
# _collect_target_codes
# ---------------------------------------------------------------------------


def test_collect_target_codes_union_of_cmap_and_differences():
    cmap = {1: "A", 2: "B"}
    differences_map = {"02": "X", "03": "C"}
    assert _collect_target_codes(cmap, differences_map) == {1, 2, 3}


def test_collect_target_codes_no_differences_map():
    cmap = {1: "A", 2: "B"}
    assert _collect_target_codes(cmap, None) == {1, 2}


def test_collect_target_codes_empty_differences_map():
    cmap = {1: "A"}
    assert _collect_target_codes(cmap, {}) == {1}


# ---------------------------------------------------------------------------
# _patch_single_metric: direct unit tests with a plain-dict hmtx stand-in
# ---------------------------------------------------------------------------


def test_patch_single_metric_differences_override_remaps_glyph():
    """Code 1 is cmap-mapped to 'A', but /Differences remaps it to 'B'; the
    override must win and 'B' (not 'A') gets patched."""
    hmtx = {"A": (250, 0), "B": (500, 0)}
    differences_map = {"01": "B"}
    result = _patch_single_metric(1, "A", hmtx, {"01": 999.0}, 1.0, differences_map)
    assert result is True
    assert hmtx["B"] == (999, 0)
    assert hmtx["A"] == (250, 0)  # untouched


def test_patch_single_metric_differences_only_code_absent_from_cmap():
    """A code with no cmap entry (gname=None) can still be patched via Differences."""
    hmtx = {"A": (250, 0), "C": (300, 0)}
    differences_map = {"03": "C"}
    result = _patch_single_metric(3, None, hmtx, {"03": 777.0}, 1.0, differences_map)
    assert result is True
    assert hmtx["C"] == (777, 0)


def test_patch_single_metric_no_resolvable_glyph_name_returns_false():
    hmtx = {"A": (250, 0)}
    result = _patch_single_metric(9, None, hmtx, {"09": 100.0}, 1.0, None)
    assert result is False


def test_patch_single_metric_differences_glyph_missing_from_font_skipped():
    """A /Differences override naming a glyph the font doesn't actually have
    is skipped gracefully rather than raising KeyError."""
    hmtx = {"A": (250, 0)}
    differences_map = {"01": "NotInFont"}
    result = _patch_single_metric(1, "A", hmtx, {"01": 500.0}, 1.0, differences_map)
    assert result is False
    assert hmtx["A"] == (250, 0)  # untouched


# ---------------------------------------------------------------------------
# _process_glyph_squash: direct unit tests with plain-dict hmtx/glyf stand-ins
# ---------------------------------------------------------------------------


class _FakeGlyph:
    def __init__(self):
        self.coordinates = [(10.0, 20.0)]


def test_process_glyph_squash_differences_override_remaps_glyph():
    hmtx = {"A": (250, 0), "B": (500, 0)}
    glyf = {"A": _FakeGlyph(), "B": _FakeGlyph()}
    differences_map = {"01": "B"}
    result = _process_glyph_squash(
        1, "A", hmtx, glyf, {"01": 1000.0}, 1000.0, 1.0, differences_map
    )
    assert result is True
    assert hmtx["B"][0] == 1000
    assert hmtx["A"] == (250, 0)  # untouched


def test_process_glyph_squash_differences_only_code_absent_from_cmap():
    hmtx = {"A": (250, 0), "C": (300, 0)}
    glyf = {"A": _FakeGlyph(), "C": _FakeGlyph()}
    differences_map = {"03": "C"}
    result = _process_glyph_squash(
        3, None, hmtx, glyf, {"03": 900.0}, 1000.0, 1.0, differences_map
    )
    assert result is True
    assert hmtx["C"][0] == 900


def test_process_glyph_squash_no_resolvable_glyph_name_returns_false():
    hmtx = {"A": (250, 0)}
    glyf = {"A": _FakeGlyph()}
    result = _process_glyph_squash(9, None, hmtx, glyf, {"09": 500.0}, 1000.0, 1.0, None)
    assert result is False


def test_process_glyph_squash_differences_glyph_missing_from_font_skipped():
    hmtx = {"A": (250, 0)}
    glyf = {"A": _FakeGlyph()}
    differences_map = {"01": "NotInFont"}
    result = _process_glyph_squash(1, "A", hmtx, glyf, {"01": 500.0}, 1000.0, 1.0, differences_map)
    assert result is False
    assert hmtx["A"] == (250, 0)  # untouched


# ---------------------------------------------------------------------------
# patch_font_file_metrics / squash_font_file_vectors: end-to-end `differences` wiring
# ---------------------------------------------------------------------------


def test_patch_font_file_metrics_with_differences_remaps_existing_code(tmp_path, monkeypatch):
    """Integration: passing `differences` through the public API remaps code 1
    from its cmap glyph 'A' to 'B', and the patch still succeeds/saves."""
    _mock_font_tools(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    differences = [1, "/B"]
    res = patch_font_file_metrics(f, {"01": 999.0}, differences=differences)
    assert res == b"mock_saved_bytes"


def test_patch_font_file_metrics_with_differences_only_code(tmp_path, monkeypatch):
    """Integration: a code reachable only via /Differences (not the font's own
    cmap) is still visited and patched thanks to the union-of-codes iteration."""
    _mock_font_tools_with_extra_glyph(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    differences = [3, "/C"]
    res = patch_font_file_metrics(f, {"03": 777.0}, differences=differences)
    assert res == b"mock_saved_bytes"


def test_patch_font_file_metrics_with_differences_missing_glyph_no_crash(tmp_path, monkeypatch):
    """A /Differences override naming a nonexistent glyph doesn't crash the
    whole patch pass; other codes still patch normally."""
    _mock_font_tools(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    differences = [9, "/DoesNotExist"]
    # Code 2 still patches normally via the font's own cmap.
    res = patch_font_file_metrics(f, {"02": 555.0, "09": 100.0}, differences=differences)
    assert res == b"mock_saved_bytes"


def test_squash_font_file_vectors_with_differences_remaps_existing_code(tmp_path, monkeypatch):
    _mock_font_tools(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    differences = [1, "/B"]
    res = squash_font_file_vectors(f, {"01": 999.0}, differences=differences)
    assert res == b"mock_saved_bytes"


def test_squash_font_file_vectors_with_differences_only_code(tmp_path, monkeypatch):
    _mock_font_tools_with_extra_glyph(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    differences = [3, "/C"]
    res = squash_font_file_vectors(f, {"03": 900.0}, differences=differences)
    assert res == b"mock_saved_bytes"


def test_squash_font_file_vectors_with_differences_missing_glyph_no_crash(tmp_path, monkeypatch):
    _mock_font_tools(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    differences = [9, "/DoesNotExist"]
    res = squash_font_file_vectors(f, {"01": 999.0, "09": 100.0}, differences=differences)
    assert res == b"mock_saved_bytes"


def test_patch_font_file_metrics_no_differences_arg_is_backward_compatible(tmp_path, monkeypatch):
    """Omitting `differences` entirely preserves prior cmap-only behavior."""
    _mock_font_tools(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    res = patch_font_file_metrics(f, {"01": 999.0, "02": 888.0})
    assert res == b"mock_saved_bytes"


def _mock_font_tools_with_winansi_glyph(monkeypatch):
    """
    Like _mock_font_tools, but the font's cmap only exposes ASCII code 0x41
    ("A"); the font's hmtx/glyf tables additionally carry a "quoteright"
    glyph (the WinAnsiEncoding glyph name for code 0x92) that is NOT
    reachable via the font's own cmap at all. This simulates the real-world
    case this fix targets: a PDF code (0x92) whose byte value coincidentally
    isn't a meaningful Unicode code point on its own, but resolves correctly
    through the declared /BaseEncoding.
    """

    class DummyGlyph:
        def __init__(self):
            self.coordinates = [(100.0, 200.0)]
            self.components = []

    class DummyTTFont:
        def __init__(self, filepath=None):
            self.filepath = filepath
            self.head = type("Head", (), {"unitsPerEm": 1000})()
            self.cmap = {0x41: "A"}
            self.hmtx = {"A": [250.0, 0], "quoteright": [180.0, 0]}
            self.glyf = {"A": DummyGlyph(), "quoteright": DummyGlyph()}

        def getBestCmap(self, cmapPreferences=None):
            return self.cmap

        def __getitem__(self, key):
            if key == "hmtx":
                return type(
                    "Hmtx",
                    (),
                    {
                        "metrics": self.hmtx,
                        "__getitem__": lambda s, k: self.hmtx[k],
                        "__setitem__": lambda s, k, v: self.hmtx.update({k: v}),
                    },
                )()
            if key == "glyf":
                return self.glyf
            return getattr(self, key)

        def __contains__(self, key):
            return key in ("glyf", "hmtx")

        def save(self, path_or_stream):
            if hasattr(path_or_stream, "write"):
                path_or_stream.write(b"mock_saved_bytes")

    monkeypatch.setattr("fontTools.ttLib.TTFont", DummyTTFont)


# ---------------------------------------------------------------------------
# Base encoding table builders (WinAnsiEncoding / MacRomanEncoding / StandardEncoding)
# ---------------------------------------------------------------------------


def test_build_winansi_encoding_table_known_code_points():
    table = _build_winansi_encoding_table()
    # 0x41 'A' is identical across encodings.
    assert table[0x41] == "A"
    # 0x92 is WinAnsiEncoding's right single quotation mark.
    assert table[0x92] == "quoteright"
    # 0xE9 is WinAnsiEncoding's e-acute.
    assert table[0xE9] == "eacute"


def test_build_winansi_encoding_table_bullet_fallback_codes():
    """cp1252 leaves these bytes fully undefined; WinAnsiEncoding conventionally
    maps them to the 'bullet' glyph rather than leaving them unresolved."""
    table = _build_winansi_encoding_table()
    for code in (0x81, 0x8D, 0x8F, 0x90, 0x9D):
        assert table[code] == "bullet"


def test_build_macroman_encoding_table_known_code_points():
    table = _build_macroman_encoding_table()
    assert table[0x41] == "A"


def test_build_standard_encoding_table_known_code_points():
    table = _build_standard_encoding_table()
    assert table[0x41] == "A"
    assert table[32] == "space"
    # .notdef entries must be excluded from the table entirely.
    assert all(name != ".notdef" for name in table.values())


def test_get_base_encoding_table_dispatches_by_name():
    assert _get_base_encoding_table("WinAnsiEncoding")[0x92] == "quoteright"
    assert _get_base_encoding_table("MacRomanEncoding")[0x41] == "A"
    assert _get_base_encoding_table("StandardEncoding")[0x41] == "A"


def test_get_base_encoding_table_unknown_name_returns_empty():
    assert _get_base_encoding_table("SomeFutureEncoding") == {}


def test_get_base_encoding_table_is_cached():
    """Calling twice with the same name returns the same (cached) dict object."""
    first = _get_base_encoding_table("WinAnsiEncoding")
    second = _get_base_encoding_table("WinAnsiEncoding")
    assert first is second


def test_get_base_encoding_table_builder_exception_returns_empty(monkeypatch):
    """If a builder itself raises (e.g. a stdlib codec or fontTools submodule
    becomes unavailable), the lookup degrades to an empty table rather than
    propagating the exception and aborting the whole patch/squash pass.

    Monkeypatches pdftl.fonts.font_encoding_tables directly (rather than
    the font_binary_utils re-export) since _get_base_encoding_table is
    defined there and resolves _BASE_ENCODING_BUILDERS against its own
    module globals; patching the re-exported name on font_binary_utils
    would silently rebind an unused reference instead.
    """
    import pdftl.fonts.font_encoding_tables as fet

    def broken_builder():
        raise LookupError("simulated missing codec")

    monkeypatch.setattr(fet, "_BASE_ENCODING_BUILDERS", {"WinAnsiEncoding": broken_builder})
    fet._get_base_encoding_table.cache_clear()
    try:
        assert fet._get_base_encoding_table("WinAnsiEncoding") == {}
    finally:
        fet._get_base_encoding_table.cache_clear()


# ---------------------------------------------------------------------------
# _resolve_glyph_name: priority ordering across Differences / BaseEncoding / cmap
# ---------------------------------------------------------------------------


def test_resolve_glyph_name_base_encoding_overrides_cmap_fallback():
    base_encoding_map = {0x92: "quoteright"}
    assert _resolve_glyph_name(0x92, "cmap_guess", None, base_encoding_map) == "quoteright"


def test_resolve_glyph_name_differences_overrides_base_encoding():
    """/Differences is the highest-priority source, ahead of /BaseEncoding."""
    base_encoding_map = {0x92: "quoteright"}
    differences_map = {"92": "explicit_override"}
    result = _resolve_glyph_name(0x92, "cmap_guess", differences_map, base_encoding_map)
    assert result == "explicit_override"


def test_resolve_glyph_name_base_encoding_map_miss_falls_back_to_cmap():
    base_encoding_map = {0x92: "quoteright"}  # no entry for code 0x41
    assert _resolve_glyph_name(0x41, "A", None, base_encoding_map) == "A"


def test_resolve_glyph_name_no_base_encoding_map_falls_back_to_cmap():
    assert _resolve_glyph_name(0x41, "A", None, None) == "A"


def test_resolve_glyph_name_base_encoding_only_no_cmap_entry():
    """A code absent from the font's own cmap can still resolve purely via BaseEncoding."""
    base_encoding_map = {0x92: "quoteright"}
    assert _resolve_glyph_name(0x92, None, None, base_encoding_map) == "quoteright"


# ---------------------------------------------------------------------------
# _collect_target_codes: base_encoding_map contributes to the union
# ---------------------------------------------------------------------------


def test_collect_target_codes_includes_base_encoding_only_codes():
    cmap = {0x41: "A"}
    base_encoding_map = {0x41: "A", 0x92: "quoteright"}
    assert _collect_target_codes(cmap, None, base_encoding_map) == {0x41, 0x92}


def test_collect_target_codes_union_of_all_three_sources():
    cmap = {1: "A"}
    differences_map = {"02": "B"}
    base_encoding_map = {3: "C"}
    assert _collect_target_codes(cmap, differences_map, base_encoding_map) == {1, 2, 3}


def test_collect_target_codes_no_base_encoding_map_unaffected():
    cmap = {1: "A"}
    assert _collect_target_codes(cmap, None, None) == {1}


# ---------------------------------------------------------------------------
# _patch_single_metric / _process_glyph_squash: base_encoding_map wiring
# ---------------------------------------------------------------------------


def test_patch_single_metric_base_encoding_resolves_code_absent_from_cmap():
    hmtx = {"A": (250, 0), "quoteright": (180, 0)}
    base_encoding_map = {0x92: "quoteright"}
    result = _patch_single_metric(0x92, None, hmtx, {"92": 400.0}, 1.0, None, base_encoding_map)
    assert result is True
    assert hmtx["quoteright"] == (400, 0)


def test_patch_single_metric_base_encoding_glyph_missing_from_font_skipped():
    hmtx = {"A": (250, 0)}
    base_encoding_map = {0x92: "quoteright"}  # not present in hmtx
    result = _patch_single_metric(0x92, None, hmtx, {"92": 400.0}, 1.0, None, base_encoding_map)
    assert result is False


def test_process_glyph_squash_base_encoding_resolves_code_absent_from_cmap():
    hmtx = {"A": (250, 0), "quoteright": (180, 0)}
    glyf = {"A": _FakeGlyph(), "quoteright": _FakeGlyph()}
    base_encoding_map = {0x92: "quoteright"}
    result = _process_glyph_squash(
        0x92, None, hmtx, glyf, {"92": 900.0}, 1000.0, 1.0, None, base_encoding_map
    )
    assert result is True
    assert hmtx["quoteright"][0] == 900


def test_process_glyph_squash_base_encoding_glyph_missing_from_font_skipped():
    hmtx = {"A": (250, 0)}
    glyf = {"A": _FakeGlyph()}
    base_encoding_map = {0x92: "quoteright"}
    result = _process_glyph_squash(
        0x92, None, hmtx, glyf, {"92": 900.0}, 1000.0, 1.0, None, base_encoding_map
    )
    assert result is False


# ---------------------------------------------------------------------------
# patch_font_file_metrics / squash_font_file_vectors: end-to-end `base_encoding` wiring
# ---------------------------------------------------------------------------


def test_patch_font_file_metrics_with_base_encoding_resolves_non_cmap_code(tmp_path, monkeypatch):
    """Integration: a code only meaningful via WinAnsiEncoding (0x92 ->
    quoteright), not present in the font's own cmap at all, is patched
    correctly once `base_encoding` is supplied."""
    _mock_font_tools_with_winansi_glyph(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    res = patch_font_file_metrics(f, {"92": 400.0}, base_encoding="WinAnsiEncoding")
    assert res == b"mock_saved_bytes"


def test_patch_font_file_metrics_without_base_encoding_non_cmap_code_not_patched(
    tmp_path, monkeypatch
):
    """Without `base_encoding`, code 0x92 isn't in the font's cmap and has no
    other source, so nothing is patched and the function returns None."""
    _mock_font_tools_with_winansi_glyph(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    assert patch_font_file_metrics(f, {"92": 400.0}) is None


def test_squash_font_file_vectors_with_base_encoding_resolves_non_cmap_code(tmp_path, monkeypatch):
    _mock_font_tools_with_winansi_glyph(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    res = squash_font_file_vectors(f, {"92": 900.0}, base_encoding="WinAnsiEncoding")
    assert res == b"mock_saved_bytes"


def test_patch_font_file_metrics_differences_takes_priority_over_base_encoding(
    tmp_path, monkeypatch
):
    """When both are present for the same code, /Differences wins."""
    _mock_font_tools_with_winansi_glyph(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    # Differences remaps code 0x92 to "A" instead of WinAnsi's "quoteright".
    differences = [0x92, "/A"]
    res = patch_font_file_metrics(
        f, {"92": 700.0}, differences=differences, base_encoding="WinAnsiEncoding"
    )
    assert res == b"mock_saved_bytes"


def test_patch_font_file_metrics_unknown_base_encoding_falls_back_gracefully(
    tmp_path, monkeypatch
):
    """An unrecognized /BaseEncoding name doesn't crash; it simply yields no
    extra resolution, same as passing no base_encoding at all."""
    _mock_font_tools(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    res = patch_font_file_metrics(f, {"01": 999.0}, base_encoding="SomeFutureEncoding")
    assert res == b"mock_saved_bytes"


def _mock_font_tools_cid(monkeypatch):
    """
    Mocks fontTools with a CID-oriented font: a glyph order table indexed
    purely by GID, with no meaningful Unicode cmap at all -- matching a
    real CIDFontType2 font closely enough to exercise CID->GID glyph
    resolution independent of any cmap.
    """

    class DummyGlyph:
        def __init__(self):
            self.coordinates = [(100.0, 200.0)]
            self.components = []

    class DummyTTFont:
        def __init__(self, filepath=None):
            self.filepath = filepath
            self.head = type("Head", (), {"unitsPerEm": 1000})()
            self.glyph_order = [".notdef", "glyph_A", "glyph_B", "glyph_C"]
            self.hmtx = {
                ".notdef": [0.0, 0],
                "glyph_A": [250.0, 0],
                "glyph_B": [500.0, 0],
                "glyph_C": [750.0, 0],
            }
            self.glyf = {name: DummyGlyph() for name in self.glyph_order}

        def getBestCmap(self, cmapPreferences=None):
            return {}

        def getGlyphOrder(self):
            return self.glyph_order

        def __getitem__(self, key):
            if key == "hmtx":
                return type(
                    "Hmtx",
                    (),
                    {
                        "metrics": self.hmtx,
                        "__getitem__": lambda s, k: self.hmtx[k],
                        "__setitem__": lambda s, k, v: self.hmtx.update({k: v}),
                    },
                )()
            if key == "glyf":
                return self.glyf
            return getattr(self, key)

        def __contains__(self, key):
            return key in ("glyf", "hmtx")

        def save(self, path_or_stream):
            if hasattr(path_or_stream, "write"):
                path_or_stream.write(b"mock_saved_cid_bytes")

    monkeypatch.setattr("fontTools.ttLib.TTFont", DummyTTFont)


# ---------------------------------------------------------------------------
# get_font_widths_from_file: CID-keyed reading via /CIDToGIDMap
# ---------------------------------------------------------------------------


def test_get_font_widths_from_file_cid_identity(tmp_path, monkeypatch):
    """Under Identity, CID equals GID equals glyph-order index."""
    _mock_font_tools_cid(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    widths = get_font_widths_from_file(f, cid_to_gid_map="Identity")
    assert widths == {"0000": 0.0, "0001": 250.0, "0002": 500.0, "0003": 750.0}


def test_get_font_widths_from_file_cid_explicit_map(tmp_path, monkeypatch):
    """An explicit CID->GID dict resolves each CID to its mapped GID's
    glyph, independent of glyph-order position."""
    _mock_font_tools_cid(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    widths = get_font_widths_from_file(f, cid_to_gid_map={10: 2, 20: 3})
    assert widths == {"000A": 500.0, "0014": 750.0}


def test_get_font_widths_from_file_cid_gid_out_of_bounds_skipped(tmp_path, monkeypatch):
    """A CID mapped to a GID beyond the font's actual glyph set is skipped,
    not fatal."""
    _mock_font_tools_cid(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    widths = get_font_widths_from_file(f, cid_to_gid_map={5: 999})
    assert widths == {}


def test_get_font_widths_from_file_cid_glyph_missing_from_hmtx_skipped(tmp_path, monkeypatch):
    """A resolved glyph name absent from hmtx (a mismatched font/PDF pairing)
    is skipped rather than raising."""
    _mock_font_tools_cid(monkeypatch)

    class BrokenHmtxTTFont:
        def __init__(self, filepath=None):
            self.head = type("Head", (), {"unitsPerEm": 1000})()
            self.glyph_order = [".notdef", "glyph_A"]

        def getGlyphOrder(self):
            return self.glyph_order

        def __getitem__(self, key):
            if key == "hmtx":
                return {}  # no entries at all -> every lookup raises KeyError
            return getattr(self, key)

        def __contains__(self, key):
            return key == "hmtx"

    import fontTools.ttLib

    monkeypatch.setattr(fontTools.ttLib, "TTFont", BrokenHmtxTTFont)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    widths = get_font_widths_from_file(f, cid_to_gid_map="Identity")
    assert widths == {}


# ---------------------------------------------------------------------------
# patch_font_file_metrics: CID-keyed patching via /CIDToGIDMap
# ---------------------------------------------------------------------------


def test_patch_font_file_metrics_cid_identity_success(tmp_path, monkeypatch):
    _mock_font_tools_cid(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    res = patch_font_file_metrics(f, {"0001": 999.0}, cid_to_gid_map="Identity")
    assert res == b"mock_saved_cid_bytes"


def test_patch_font_file_metrics_cid_explicit_success(tmp_path, monkeypatch):
    _mock_font_tools_cid(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    res = patch_font_file_metrics(f, {"000A": 999.0}, cid_to_gid_map={10: 2})
    assert res == b"mock_saved_cid_bytes"


def test_patch_font_file_metrics_cid_unmatched_hex_returns_none(tmp_path, monkeypatch):
    """A pdf_widths key with no corresponding CID entry patches nothing."""
    _mock_font_tools_cid(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    res = patch_font_file_metrics(f, {"FFFF": 999.0}, cid_to_gid_map={10: 2})
    assert res is None


def test_patch_font_file_metrics_cid_explicit_gid_zero_skipped(tmp_path, monkeypatch):
    """A CID absent from an explicit /CIDToGIDMap (implicitly GID 0 /
    .notdef) is skipped rather than patched."""
    _mock_font_tools_cid(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    # CID 5 isn't a key in the explicit map at all -> resolves to GID 0 -> skip.
    res = patch_font_file_metrics(f, {"0005": 999.0}, cid_to_gid_map={10: 2})
    assert res is None


def test_patch_font_file_metrics_cid_bad_hex_key_skipped(tmp_path, monkeypatch):
    """A non-hex key in pdf_widths is skipped rather than raising."""
    _mock_font_tools_cid(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    res = patch_font_file_metrics(f, {"ZZZZ": 999.0, "0001": 888.0}, cid_to_gid_map="Identity")
    assert res == b"mock_saved_cid_bytes"


def test_patch_font_file_metrics_cid_gid_out_of_bounds_skipped(tmp_path, monkeypatch):
    _mock_font_tools_cid(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    res = patch_font_file_metrics(f, {"0005": 999.0}, cid_to_gid_map={5: 999})
    assert res is None


def test_patch_font_file_metrics_cid_differences_ignored(tmp_path, monkeypatch):
    """When cid_to_gid_map is given, differences/base_encoding are simply
    not consulted -- Type0 fonts never carry /Differences."""
    _mock_font_tools_cid(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    res = patch_font_file_metrics(
        f,
        {"0001": 999.0},
        differences=[1, "/SomeGlyph"],
        base_encoding="WinAnsiEncoding",
        cid_to_gid_map="Identity",
    )
    assert res == b"mock_saved_cid_bytes"


# ---------------------------------------------------------------------------
# squash_font_file_vectors: CID-keyed squashing via /CIDToGIDMap
# ---------------------------------------------------------------------------


def test_squash_font_file_vectors_cid_identity_success(tmp_path, monkeypatch):
    _mock_font_tools_cid(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    res = squash_font_file_vectors(f, {"0001": 999.0}, cid_to_gid_map="Identity")
    assert res == b"mock_saved_cid_bytes"


def test_squash_font_file_vectors_cid_explicit_success(tmp_path, monkeypatch):
    _mock_font_tools_cid(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    res = squash_font_file_vectors(f, {"000A": 999.0}, cid_to_gid_map={10: 2})
    assert res == b"mock_saved_cid_bytes"


def test_squash_font_file_vectors_cid_unmatched_hex_returns_none(tmp_path, monkeypatch):
    _mock_font_tools_cid(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    res = squash_font_file_vectors(f, {"FFFF": 999.0}, cid_to_gid_map={10: 2})
    assert res is None


def test_squash_font_file_vectors_cid_explicit_gid_zero_skipped(tmp_path, monkeypatch):
    _mock_font_tools_cid(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    res = squash_font_file_vectors(f, {"0005": 999.0}, cid_to_gid_map={10: 2})
    assert res is None


def test_squash_font_file_vectors_cid_gid_out_of_bounds_skipped(tmp_path, monkeypatch):
    _mock_font_tools_cid(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    res = squash_font_file_vectors(f, {"0005": 999.0}, cid_to_gid_map={5: 999})
    assert res is None


def test_squash_font_file_vectors_cid_glyph_missing_from_font_skipped(tmp_path, monkeypatch):
    """A resolved glyph name absent from hmtx/glyf is skipped rather than
    raising KeyError."""

    class BrokenGlyfTTFont:
        def __init__(self, filepath=None):
            self.head = type("Head", (), {"unitsPerEm": 1000})()
            self.glyph_order = [".notdef", "glyph_A"]

        def getBestCmap(self, cmapPreferences=None):
            return {}

        def getGlyphOrder(self):
            return self.glyph_order

        def __getitem__(self, key):
            if key == "hmtx":
                return {"glyph_A": [250.0, 0]}
            if key == "glyf":
                return {}  # glyph_A missing from glyf -> KeyError inside lookup
            return getattr(self, key)

        def __contains__(self, key):
            return key in ("hmtx", "glyf")

    import fontTools.ttLib

    monkeypatch.setattr(fontTools.ttLib, "TTFont", BrokenGlyfTTFont)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    res = squash_font_file_vectors(f, {"0001": 999.0}, cid_to_gid_map="Identity")
    assert res is None


def test_squash_font_file_vectors_cid_bad_hex_key_skipped(tmp_path, monkeypatch):
    """End-to-end via the public API: a non-hex key alongside a valid one
    doesn't abort the whole squash pass."""
    _mock_font_tools_cid(monkeypatch)
    f = tmp_path / "test.ttf"
    f.write_bytes(b"fake")
    res = squash_font_file_vectors(f, {"ZZZZ": 999.0, "0001": 999.0}, cid_to_gid_map="Identity")
    assert res == b"mock_saved_cid_bytes"


# ---------------------------------------------------------------------------
# _patch_single_cid_metric / _process_cid_glyph_squash: direct unit tests
# for branches unreachable via the public patch/squash entry points
# ---------------------------------------------------------------------------


def test_patch_single_cid_metric_hex_cid_not_in_pdf_widths_returns_false():
    """
    hex_cid is derived by re-formatting `cid` as 4-digit uppercase hex; if
    the caller's pdf_widths key for this CID isn't in that exact form
    (e.g. lowercase or non-padded, as could survive an unusual sidecar
    edit), the re-formatted lookup misses and the code is skipped rather
    than patched under a coincidentally-different key.
    """
    glyph_order = [".notdef", "A"]
    hmtx = {"A": (250, 0)}
    # Key present is "a" (lowercase, unpadded) -- cid=10 reformats to "000A",
    # which is NOT a key in pdf_widths.
    result = _patch_single_cid_metric(10, hmtx, glyph_order, {"a": 100.0}, 1.0, "Identity")
    assert result is False
    assert hmtx["A"] == (250, 0)  # untouched


def test_patch_single_cid_metric_glyph_missing_from_hmtx_returns_false():
    """A CID that resolves to a real, in-bounds GID/glyph name, but whose
    glyph name is absent from hmtx entirely (a mismatched font/PDF
    pairing), is skipped via the KeyError branch rather than raising."""
    glyph_order = [".notdef", "A"]
    hmtx = {}  # "A" deliberately absent
    result = _patch_single_cid_metric(1, hmtx, glyph_order, {"0001": 500.0}, 1.0, "Identity")
    assert result is False


def test_process_cid_glyph_squash_hex_cid_not_in_pdf_widths_returns_false():
    glyph_order = [".notdef", "A"]
    hmtx = {"A": (250, 0)}
    glyf = {"A": _FakeGlyph()}
    result = _process_cid_glyph_squash(
        10, hmtx, glyf, glyph_order, {"a": 500.0}, 1000.0, 1.0, "Identity"
    )
    assert result is False


def test_process_cid_glyph_squash_zero_font_width_returns_false():
    """font_w <= 0 (e.g. a zero-advance-width glyph, such as a combining
    mark) is a divide-by-zero guard, not a real ratio to apply."""
    glyph_order = [".notdef", "A"]
    hmtx = {"A": (0, 0)}  # font_w resolves to 0
    glyf = {"A": _FakeGlyph()}
    result = _process_cid_glyph_squash(
        1, hmtx, glyf, glyph_order, {"0001": 500.0}, 1000.0, 1.0, "Identity"
    )
    assert result is False


def test_process_cid_glyph_squash_zero_pdf_width_returns_false():
    """pdf_w <= 0 is likewise a guard against a degenerate zero-width target."""
    glyph_order = [".notdef", "A"]
    hmtx = {"A": (250, 0)}
    glyf = {"A": _FakeGlyph()}
    result = _process_cid_glyph_squash(
        1, hmtx, glyf, glyph_order, {"0001": 0.0}, 1000.0, 1.0, "Identity"
    )
    assert result is False


def test_process_cid_glyph_squash_ratio_close_to_one_returns_false():
    """A target width within 1e-3 of the font's own width isn't worth
    squashing; skip rather than apply a no-op transform."""
    glyph_order = [".notdef", "A"]
    hmtx = {"A": (250, 0)}  # font_w = 250 * (1000/1000) = 250
    glyf = {"A": _FakeGlyph()}
    result = _process_cid_glyph_squash(
        1, hmtx, glyf, glyph_order, {"0001": 250.1}, 1000.0, 1.0, "Identity"
    )
    assert result is False


def test_process_cid_glyph_squash_success_updates_hmtx_and_glyph():
    """Sanity check that a real, sufficiently-different ratio does apply,
    so the guards above are verified against a working baseline."""
    glyph_order = [".notdef", "A"]
    hmtx = {"A": (250, 0)}
    glyf = {"A": _FakeGlyph()}
    result = _process_cid_glyph_squash(
        1, hmtx, glyf, glyph_order, {"0001": 500.0}, 1000.0, 1.0, "Identity"
    )
    assert result is True
    assert hmtx["A"][0] == 500


def test_squash_cid_glyphs_bad_hex_key_skipped():
    """A non-hex key in pdf_widths hits the except ValueError: continue
    branch in the CID squash loop, rather than propagating."""

    class DummyTT:
        def getGlyphOrder(self):
            return [".notdef", "A"]

    hmtx = {"A": (250, 0)}
    glyf = {"A": _FakeGlyph()}
    result = _squash_cid_glyphs(
        DummyTT(), hmtx, glyf, {"ZZZZ": 999.0, "0001": 500.0}, 1000.0, 1.0, "Identity"
    )
    assert result is True
    assert hmtx["A"][0] == 500


# ---------------------------------------------------------------------------
# _resolve_cid_glyph_name: direct bounds check
# ---------------------------------------------------------------------------


def test_resolve_cid_glyph_name_bounds():
    glyph_order = [".notdef", "A", "B"]
    assert _resolve_cid_glyph_name(glyph_order, 1) == "A"
    assert _resolve_cid_glyph_name(glyph_order, 99) is None
    assert _resolve_cid_glyph_name(glyph_order, -1) is None


def test_patch_font_file_metrics_sfnt_dispatch(monkeypatch):
    """Verify that patch_font_file_metrics with sfnt format dispatches correctly (covers line 240)."""
    from pdftl.fonts.font_binary_utils import patch_font_file_metrics
    import pdftl.fonts.font_binary_utils as fbu

    called = []

    def fake_patch_via_ttfont(
        filepath, pdf_widths, differences=None, base_encoding=None, cid_to_gid_map=None
    ):
        called.append((filepath, pdf_widths))
        return b"mock_patched_sfnt"

    monkeypatch.setattr(fbu, "_patch_font_file_metrics_via_ttfont", fake_patch_via_ttfont)

    res = patch_font_file_metrics(Path("mock.ttf"), {"01": 120.0}, embedded_format="ttf")
    assert res == b"mock_patched_sfnt"
    assert called == [(Path("mock.ttf"), {"01": 120.0})]


def test_effective_cmap_code_symbol_fallback():
    from pdftl.fonts.font_binary_sfnt import _effective_cmap_code

    assert _effective_cmap_code(0xF041) == 0x41
    assert _effective_cmap_code(0x41) == 0x41


def test_get_font_widths_via_ttfont_no_usable_cmap(tmp_path, monkeypatch):
    """168-169: no usable cmap at all -> empty dict, not an exception."""
    from pdftl.fonts.font_binary_sfnt import get_font_widths_via_ttfont

    class NoCmapFont:
        def __init__(self, filepath=None):
            self.head = type("Head", (), {"unitsPerEm": 1000})()

        def getBestCmap(self, **kwargs):
            return None

        def __getitem__(self, key):
            if key == "hmtx":
                return {"A": [100, 0]}
            return getattr(self, key)

    monkeypatch.setattr("fontTools.ttLib.TTFont", NoCmapFont)
    f = tmp_path / "f.ttf"
    f.write_bytes(b"")
    assert get_font_widths_via_ttfont(f) == {}


def test_patch_cff_table_in_sfnt_guards():
    from pdftl.fonts.font_binary_sfnt import _patch_cff_table_in_sfnt

    assert _patch_cff_table_in_sfnt({}, {}, None, None, "Identity") is False
    assert _patch_cff_table_in_sfnt({}, {}, None, None, None) is False


def test_squash_cff_sfnt_falls_back_to_patch(tmp_path, monkeypatch):
    from pdftl.fonts.font_binary_utils import squash_font_file_vectors

    class CFFFont:
        def __init__(self, stream=None):
            self.head = type("Head", (), {"unitsPerEm": 1000})()

        def getBestCmap(self, **kwargs):
            return {1: "A"}

        def __getitem__(self, key):
            if key == "hmtx":
                return {"A": [250.0, 0]}
            if key == "CFF ":
                topdict = type("TopDict", (), {})()
                topdict.CharStrings = type("CharStrings", (), {"charStrings": {"A": 1}})()
                cff = type(
                    "CFF", (), {"fontNames": ["Font"], "__getitem__": lambda s, k: topdict}
                )()
                return type("CFFTable", (), {"cff": cff})()
            if key == "head":
                return self.head
            raise KeyError(key)

        def __contains__(self, key):
            return key in ("hmtx", "CFF ")

        def getGlyphOrder(self):
            return [".notdef", "A"]

        def save(self, stream):
            stream.write(b"saved_cff_sfnt")

    monkeypatch.setattr("fontTools.ttLib.TTFont", CFFFont)

    import pdftl.fonts.cff_binary_utils as cbu

    monkeypatch.setattr(cbu, "_patch_single_cff_width", lambda td, gn, w: True)

    f = tmp_path / "test.otf"
    f.write_bytes(b"")
    res = squash_font_file_vectors(f, {"01": 500.0})
    assert res == b"saved_cff_sfnt"


def test_rekey_hex_widths_to_names_malformed_hex():
    from pdftl.fonts.font_binary_utils import rekey_hex_widths_to_names

    assert rekey_hex_widths_to_names({"ZZ": 100.0}) == {}


def test_coerce_code_to_int_types():
    from pdftl.fonts.font_binary_utils import _coerce_code_to_int

    assert _coerce_code_to_int(65) == 65
    assert _coerce_code_to_int("41") == 65
    assert _coerce_code_to_int("ZZ") is None


def test_rekey_name_widths_to_hex_codes_maps():
    from pdftl.fonts.font_binary_utils import rekey_name_widths_to_hex_codes

    widths_by_name = {"A": 500.0, "B": 600.0, "C": 700.0}
    differences = [2, "/B"]
    res = rekey_name_widths_to_hex_codes(
        widths_by_name, differences=differences, base_encoding="WinAnsiEncoding"
    )
    assert res["41"] == 500.0
    assert res["02"] == 600.0
    assert "C" not in res.values()


def test_coerce_code_to_int_non_str_non_int_returns_none():
    from pdftl.fonts.font_binary_utils import _coerce_code_to_int

    assert _coerce_code_to_int(None) is None
    assert _coerce_code_to_int(3.5) is None


def test_rekey_name_widths_to_hex_codes_skips_unmapped_glyph():
    from pdftl.fonts.font_binary_utils import rekey_name_widths_to_hex_codes

    # "C" has no entry in differences/base_encoding -> dropped, not KeyError'd.
    res = rekey_name_widths_to_hex_codes({"C": 500.0}, differences=[1, "/A"])
    assert res == {}


def test_get_font_widths_no_usable_cmap_returns_empty(tmp_path, monkeypatch):
    from pdftl.fonts.font_binary_utils import get_font_widths_from_file

    class NoCmapFont:
        def __init__(self, filepath=None):
            self.head = type("Head", (), {"unitsPerEm": 1000})()

        def getBestCmap(self, **kwargs):
            return None

        def __getitem__(self, key):
            return {"A": [100, 0]}

    monkeypatch.setattr("fontTools.ttLib.TTFont", NoCmapFont)
    f = tmp_path / "f.ttf"
    f.write_bytes(b"")
    assert get_font_widths_from_file(f) == {}


def test_patch_cff_table_in_sfnt_skips_unresolved_and_unmatched_codes(monkeypatch):
    """217: resolved glyph not in CharStrings -> continue.
    221: resolved glyph found, but no matching pdf_widths entry -> continue."""
    from pdftl.fonts.font_binary_sfnt import _patch_cff_table_in_sfnt
    import pdftl.fonts.cff_binary_utils as cbu

    topdict = type("TopDict", (), {})()
    topdict.CharStrings = type("CharStrings", (), {"charStrings": {"A": 1, "C": 1}})()
    cff = type("CFF", (), {"fontNames": ["Font"], "__getitem__": lambda s, k: topdict})()

    class FakeTT:
        def __contains__(self, key):
            return key == "CFF "

        def __getitem__(self, key):
            if key == "CFF ":
                return type("CFFTable", (), {"cff": cff})()
            raise KeyError(key)

        def getBestCmap(self, **kwargs):
            # 1 -> "A": resolves, in CharStrings, matches pdf_widths -> patched
            # 2 -> "B": resolves, NOT in CharStrings -> line 217 continue
            # 3 -> "C": resolves, in CharStrings, but no pdf_widths entry -> line 221 continue
            return {1: "A", 2: "B", 3: "C"}

    patched_calls = []
    monkeypatch.setattr(
        cbu,
        "_patch_single_cff_width",
        lambda td, gn, w: (patched_calls.append((gn, w)), True)[1],
    )

    result = _patch_cff_table_in_sfnt(FakeTT(), {"01": 500.0}, None, None, None)

    assert result is True
    assert patched_calls == [("A", 500.0)]


def test_squash_internal_cff_sfnt_no_cff_table_returns_metrics_only(tmp_path, monkeypatch):
    """602: _patch_cff_table_in_sfnt returns False (no 'CFF ' table on the
    reopened bytes) -> fall through to returning the metrics-only patch
    as-is, rather than re-saving."""
    from pdftl.fonts.font_binary_utils import squash_font_file_vectors

    class NoCFFTableFont:
        def __init__(self, stream=None):
            self.head = type("Head", (), {"unitsPerEm": 1000})()

        def getBestCmap(self, **kwargs):
            return {1: "A"}

        def __getitem__(self, key):
            if key == "hmtx":
                return {"A": [250.0, 0]}
            if key == "head":
                return self.head
            raise KeyError(key)

        def __contains__(self, key):
            return key == "hmtx"  # no "glyf", no "CFF "

        def getGlyphOrder(self):
            return [".notdef", "A"]

        def save(self, stream):
            stream.write(b"saved_metrics_only")

    monkeypatch.setattr("fontTools.ttLib.TTFont", NoCFFTableFont)
    f = tmp_path / "test.otf"
    f.write_bytes(b"")
    res = squash_font_file_vectors(f, {"01": 500.0})
    assert res == b"saved_metrics_only"
