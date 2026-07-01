# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/helpers/test_font_import_helpers.py

"""
Unit tests for pdftl.operations.helpers.font_import_helpers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pikepdf
import pytest

from pdftl.operations.helpers.font_import_helpers import (
    _auto_sync_widths_from_font,
    import_cid_to_gid_map,
    import_differences,
    import_single_font_binary,
    _import_tounicode_json,
    _import_tounicode_ps,
    import_widths,
    _inject_font_bytes,
    import_type3_font,
    import_encoding_cmap,
)
from pdftl.operations.helpers.font_ops_shared import file_hash


@pytest.fixture
def sample_pdf_with_type1_font():
    pdf = pikepdf.new()
    page = pdf.add_blank_page()

    font_descriptor = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name("/MyType1Font"),
                "/Flags": 32,
                "/FontFile": pdf.make_stream(b"Fake Type1 PFB Bytes"),
            }
        )
    )

    font_obj = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/MyType1Font"),
                "/FirstChar": 1,
                "/LastChar": 1,
                "/Widths": pikepdf.Array([250.0]),
                "/FontDescriptor": font_descriptor,
            }
        )
    )

    page.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_obj})})

    return pdf, font_obj


@pytest.fixture
def sample_pdf_with_fonts():
    pdf = pikepdf.new()
    page = pdf.add_blank_page()

    font_descriptor = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name("/MyFont"),
                "/Flags": 32,
                "/FontFile2": pdf.make_stream(b"Fake TrueType Font Bytes"),
            }
        )
    )

    to_unicode = pdf.make_stream(
        b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap 1 begincodespacerange <00> <FF> endcodespacerange 1 beginbfchar <01> <0041> endbfchar endcmap CMapName currentdict /CMap defineresource pop end end"
    )

    font_obj = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
                "/BaseFont": pikepdf.Name("/MyFont"),
                "/FirstChar": 1,
                "/LastChar": 2,
                "/Widths": pikepdf.Array([250.0, 500.0]),
                "/FontDescriptor": font_descriptor,
                "/ToUnicode": to_unicode,
                "/Encoding": pikepdf.Dictionary(
                    {
                        "/Type": pikepdf.Name("/Encoding"),
                        "/Differences": pikepdf.Array(
                            [1, pikepdf.Name("/A"), 2, pikepdf.Name("/B")]
                        ),
                    }
                ),
            }
        )
    )

    page.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_obj})})

    return pdf, font_obj


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
            pass

    monkeypatch.setattr("fontTools.ttLib.TTFont", DummyTTFont)


# ---------------------------------------------------------------------------
# _inject_font_bytes
# ---------------------------------------------------------------------------


def test_inject_font_bytes_no_descriptor_key():
    font_obj = pikepdf.Dictionary()
    assert _inject_font_bytes(font_obj, {}, b"data") is False


def test_inject_font_bytes_missing_descriptor(sample_pdf_with_fonts):
    pdf, font_obj = sample_pdf_with_fonts
    del font_obj["/FontDescriptor"]
    font_entry = {"descriptor_key": "FontFile2"}
    assert _inject_font_bytes(font_obj, font_entry, b"data") is False


# ---------------------------------------------------------------------------
# import_single_font_binary
# ---------------------------------------------------------------------------


def test_font_binary_missing_descriptor(tmp_path, sample_pdf_with_fonts):
    pdf, font_obj = sample_pdf_with_fonts
    del font_obj["/FontDescriptor"]

    font_entry = {
        "embedded_file": "test.ttf",
        "descriptor_key": "FontFile2",
        "binary_md5": "different",
    }

    test_ttf = tmp_path / "test.ttf"
    test_ttf.write_bytes(b"test")

    # Should safely return False
    assert import_single_font_binary(font_obj, font_entry, tmp_path) is False


def test_import_single_font_binary_oserror(monkeypatch, tmp_path, sample_pdf_with_type1_font):
    pdf, font_obj = sample_pdf_with_type1_font
    f = tmp_path / "font.pfb"
    f.write_bytes(b"binary data")

    font_entry = {
        "embedded_file": "font.pfb",
        "descriptor_key": "FontFile",
        "binary_md5": "different-hash-forces-update",
    }

    real_open = open

    def mock_open(path, mode="r", *a, **kw):
        if str(path).endswith(".pfb") and "rb" in mode:
            raise OSError("read error")
        return real_open(path, mode, *a, **kw)

    monkeypatch.setattr("builtins.open", mock_open)
    result = import_single_font_binary(font_obj, font_entry, tmp_path)
    assert result is False


# ---------------------------------------------------------------------------
# _import_tounicode_json / _import_tounicode_ps
# ---------------------------------------------------------------------------


def test_import_tounicode_json_bad_file(tmp_path, sample_pdf_with_fonts):
    pdf, font_obj = sample_pdf_with_fonts

    json_path = tmp_path / "bad.json"
    json_path.write_text("{bad json")

    font_entry = {"base_font": "Test"}

    # Should safely return empty dict and log warning
    res = _import_tounicode_json(font_obj, json_path, font_entry, pikepdf, pdf)
    assert res == {}


def test_import_tounicode_json_creates_new_stream(tmp_path):
    pdf = pikepdf.new()
    font_obj = pikepdf.Dictionary({})
    json_path = tmp_path / "sidecar.json"
    json_path.write_text(json.dumps({"mappings": {"01": {"unicode": "A"}}}))
    result = _import_tounicode_json(font_obj, json_path, {"base_font": "Test"}, pikepdf, pdf)
    assert result
    assert "/ToUnicode" in font_obj


def test_import_tounicode_json_missing_pdf_context_raises_value_error(tmp_path):
    font_obj = pikepdf.Dictionary({})
    json_path = tmp_path / "sidecar.json"
    json_path.write_text(json.dumps({"mappings": {"01": {"unicode": "A"}}}))
    with pytest.raises(ValueError, match="valid pikepdf.Pdf context must be provided"):
        _import_tounicode_json(font_obj, json_path, {"base_font": "Test"}, pikepdf, pdf=None)


def test_import_tounicode_ps_creates_new_stream(tmp_path):
    pdf = pikepdf.new()
    font_obj = pikepdf.Dictionary({})
    ps_path = tmp_path / "cmap.ps"
    ps_path.write_bytes(b"PSDATA")
    _import_tounicode_ps(font_obj, ps_path, {}, pikepdf, pdf)
    assert "/ToUnicode" in font_obj


def test_import_tounicode_ps_missing_pdf_context_raises_value_error(tmp_path):
    font_obj = pikepdf.Dictionary({})
    ps_path = tmp_path / "cmap.ps"
    ps_path.write_bytes(b"PSDATA")
    with pytest.raises(ValueError, match="valid pikepdf.Pdf context must be provided"):
        _import_tounicode_ps(font_obj, ps_path, {}, pikepdf, pdf=None)


def test_import_tounicode_ps_oserror(tmp_path):
    pdf = pikepdf.new()
    font_obj = pikepdf.Dictionary({})
    missing = tmp_path / "missing.ps"
    _import_tounicode_ps(font_obj, missing, {}, pikepdf, pdf)  # must not raise
    assert "/ToUnicode" not in font_obj


# ---------------------------------------------------------------------------
# import_differences: empty list, and a bare (non-"/"-prefixed, non-numeric)
# string that falls through to the ValueError branch
# ---------------------------------------------------------------------------


def test_import_differences_empty_list():
    font_obj = pikepdf.Dictionary({})
    import_differences(font_obj, {"differences": []}, pikepdf)
    assert "/Encoding" not in font_obj


def test_import_differences_non_numeric_bare_string():
    font_obj = pikepdf.Dictionary({})
    import_differences(font_obj, {"differences": ["glyphname"]}, pikepdf)
    assert font_obj["/Encoding"]["/Differences"][0] == pikepdf.Name("/glyphname")


# ---------------------------------------------------------------------------
# import_encoding_cmap
# ---------------------------------------------------------------------------


def test_import_encoding_cmap_no_mode():
    font_obj = pikepdf.Dictionary({})
    assert import_encoding_cmap(font_obj, {}, pikepdf) is False


def test_import_encoding_cmap_invalid_mode(monkeypatch):
    import pdftl.operations.helpers.font_import_helpers as fih

    monkeypatch.setattr(fih, "_PREDEFINED_IDENTITY_CMAPS", ["Identity-H", "Identity-V"])

    font_obj = pikepdf.Dictionary({})
    font_entry = {"encoding_cmap": "UniGB-UCS2-H"}

    assert import_encoding_cmap(font_obj, font_entry, pikepdf) is False
    assert "/Encoding" not in font_obj


def test_import_encoding_cmap_valid_mode(monkeypatch):
    import pdftl.operations.helpers.font_import_helpers as fih

    monkeypatch.setattr(fih, "_PREDEFINED_IDENTITY_CMAPS", ["Identity-H", "Identity-V"])

    font_obj = pikepdf.Dictionary({})
    font_entry = {"encoding_cmap": "Identity-H"}

    assert import_encoding_cmap(font_obj, font_entry, pikepdf) is True
    assert font_obj["/Encoding"] == pikepdf.Name("/Identity-H")


# ---------------------------------------------------------------------------
# _auto_sync_widths_from_font: unchanged-file short-circuit, empty widths_map,
# import-error branch, and the broad except clause catching a downstream error
# ---------------------------------------------------------------------------


def test_auto_sync_widths_unchanged_file_short_circuits(tmp_path):
    f = tmp_path / "font.ttf"
    f.write_bytes(b"same-bytes")
    h = file_hash(f)
    result = _auto_sync_widths_from_font(
        None, {"embedded_file": "font.ttf", "binary_md5": h}, tmp_path, pikepdf
    )
    assert result is False


def test_auto_sync_widths_import_error(monkeypatch, tmp_path):
    import sys

    monkeypatch.setitem(sys.modules, "fontTools.ttLib", None)
    result = _auto_sync_widths_from_font(None, {"embedded_file": None}, tmp_path, pikepdf)
    assert result is False


def test_auto_sync_widths_empty_widths_map(monkeypatch, tmp_path):
    import sys

    f = tmp_path / "font.ttf"
    f.write_bytes(b"changed-bytes")
    monkeypatch.setitem(sys.modules, "fontTools.ttLib", None)
    result = _auto_sync_widths_from_font(
        None, {"embedded_file": "font.ttf", "binary_md5": "different"}, tmp_path, pikepdf
    )
    assert result is False


def test_auto_sync_widths_exception_in_update(monkeypatch, tmp_path):
    _mock_font_tools(monkeypatch)
    f = tmp_path / "font.ttf"
    f.write_bytes(b"changed-bytes")
    # font_obj=None -> update_font_widths(None, ...) raises AttributeError
    # inside the try block -> caught by the broad except clause.
    result = _auto_sync_widths_from_font(
        None,
        {"embedded_file": "font.ttf", "binary_md5": "different", "base_font": "X"},
        tmp_path,
        pikepdf,
    )
    assert result is False


def test_auto_sync_widths_success(tmp_path, monkeypatch):
    """Successfully syncs widths from font, covering the logger.info and return True branch."""
    _mock_font_tools(monkeypatch)
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
    font_entry = {"embedded_file": "test.ttf", "binary_md5": "old_hash", "base_font": "TestFont"}

    f = tmp_path / "test.ttf"
    f.write_bytes(b"dummy_bytes")  # different hash than 'old_hash'

    result = _auto_sync_widths_from_font(font_obj, font_entry, tmp_path, pikepdf)
    assert result is True
    assert "/Widths" in font_obj


# ---------------------------------------------------------------------------
# import_widths: invalid hex key filtered out, and the "auto sync failed,
# fall back to manual update" path
# ---------------------------------------------------------------------------


def test_import_widths_invalid_hex_key_skipped(tmp_path):
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
    mappings = {"ZZ": {"width": {"pdf": 100.0}}}
    import_widths(font_obj, {}, tmp_path, mappings, pikepdf)
    assert "/Widths" not in font_obj


def test_import_widths_auto_fallback_to_manual_update(tmp_path):
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
    font_entry = {}  # no embedded_file -> auto-sync short-circuits to False fast
    mappings = {"41": {"width": {"pdf": 500.0}}}
    import_widths(font_obj, font_entry, tmp_path, mappings, pikepdf)
    assert float(font_obj["/Widths"][0]) == 500.0


# ---------------------------------------------------------------------------
# import_widths: In-Memory / Specific Sync Modes
# ---------------------------------------------------------------------------


def test_import_widths_patch_mode_success(tmp_path, monkeypatch):
    _mock_font_tools(monkeypatch)
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
    font_entry = {"embedded_file": "test.ttf", "descriptor_key": "FontFile2", "base_font": "Test"}

    f = tmp_path / "test.ttf"
    f.write_bytes(b"dummy")

    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps({"width_sync_mode": "patch_font_metrics"}))
    font_entry["sidecar_json_file"] = "sidecar.json"

    mappings = {"01": {"width": {"pdf": 999.0}}}

    import pdftl.operations.helpers.font_import_helpers as fih

    monkeypatch.setattr(fih, "_inject_font_bytes", lambda *a: True)

    res = import_widths(font_obj, font_entry, tmp_path, mappings, pikepdf)
    assert res is True
    assert float(font_obj["/Widths"][0]) == 999.0


def test_import_widths_patch_mode_no_bytes(tmp_path, monkeypatch):
    _mock_font_tools(monkeypatch)
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
    font_entry = {"embedded_file": "test.ttf", "descriptor_key": "FontFile2", "base_font": "Test"}

    f = tmp_path / "test.ttf"
    f.write_bytes(b"dummy")

    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps({"width_sync_mode": "patch_font_metrics"}))
    font_entry["sidecar_json_file"] = "sidecar.json"

    mappings = {"99": {"width": {"pdf": 999.0}}}  # Unmatched hex key -> patch returns None

    res = import_widths(font_obj, font_entry, tmp_path, mappings, pikepdf)
    assert res is False


def test_import_widths_squash_mode_success(tmp_path, monkeypatch):
    _mock_font_tools(monkeypatch)
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
    font_entry = {"embedded_file": "test.ttf", "descriptor_key": "FontFile2", "base_font": "Test"}

    f = tmp_path / "test.ttf"
    f.write_bytes(b"dummy")

    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps({"width_sync_mode": "squash_font_vectors"}))
    font_entry["sidecar_json_file"] = "sidecar.json"

    mappings = {"01": {"width": {"pdf": 999.0}}}

    import pdftl.operations.helpers.font_import_helpers as fih

    monkeypatch.setattr(fih, "_inject_font_bytes", lambda *a: True)

    res = import_widths(font_obj, font_entry, tmp_path, mappings, pikepdf)
    assert res is True
    assert float(font_obj["/Widths"][0]) == 999.0


def test_import_widths_squash_mode_no_bytes(tmp_path, monkeypatch):
    _mock_font_tools(monkeypatch)
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
    font_entry = {"embedded_file": "test.ttf", "descriptor_key": "FontFile2", "base_font": "Test"}

    f = tmp_path / "test.ttf"
    f.write_bytes(b"dummy")

    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps({"width_sync_mode": "squash_font_vectors"}))
    font_entry["sidecar_json_file"] = "sidecar.json"

    mappings = {"99": {"width": {"pdf": 999.0}}}  # Unmatched hex key -> squash returns None

    res = import_widths(font_obj, font_entry, tmp_path, mappings, pikepdf)
    assert res is False


def test_import_widths_manual_mode(tmp_path):
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
    font_entry = {}
    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps({"width_sync_mode": "manual"}))
    font_entry["sidecar_json_file"] = "sidecar.json"
    mappings = {"41": {"width": {"pdf": 500.0}}}

    res = import_widths(font_obj, font_entry, tmp_path, mappings, pikepdf)
    assert res is False
    assert float(font_obj["/Widths"][0]) == 500.0


def test_import_widths_unknown_mode_fallback(tmp_path, monkeypatch):
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
    font_entry = {}
    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps({"width_sync_mode": "invalid_mode"}))
    font_entry["sidecar_json_file"] = "sidecar.json"
    mappings = {"41": {"width": {"pdf": 600.0}}}

    res = import_widths(font_obj, font_entry, tmp_path, mappings, pikepdf)
    assert res is False
    assert float(font_obj["/Widths"][0]) == 600.0


def test_import_widths_json_read_error(tmp_path):
    """Triggers the exception handler during JSON read, falling back to 'auto'."""
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
    font_entry = {}
    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text("{bad json")
    font_entry["sidecar_json_file"] = "sidecar.json"
    mappings = {"41": {"width": {"pdf": 700.0}}}

    res = import_widths(font_obj, font_entry, tmp_path, mappings, pikepdf)
    assert res is False
    assert float(font_obj["/Widths"][0]) == 700.0


# ---------------------------------------------------------------------------
# import_widths: /Differences threading into patch_font_metrics / squash_font_vectors
# ---------------------------------------------------------------------------


def test_import_widths_patch_mode_passes_differences_through(tmp_path, monkeypatch):
    """Confirms font_entry['differences'] reaches patch_font_file_metrics as the
    `differences` kwarg, so a /Differences-remapped code can be resolved
    correctly against the real font glyph rather than the font's own cmap guess."""
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
    font_entry = {
        "embedded_file": "test.ttf",
        "descriptor_key": "FontFile2",
        "base_font": "Test",
        "differences": [1, "/B"],
    }

    f = tmp_path / "test.ttf"
    f.write_bytes(b"dummy")

    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps({"width_sync_mode": "patch_font_metrics"}))
    font_entry["sidecar_json_file"] = "sidecar.json"

    mappings = {"01": {"width": {"pdf": 999.0}}}

    import pdftl.operations.helpers.font_import_helpers as fih

    captured = {}

    def fake_patch(
        filepath,
        pdf_widths,
        differences=None,
        base_encoding=None,
        cid_to_gid_map=None,
        embedded_format=None,
        base_font="",
    ):
        captured["differences"] = differences  # or "base_encoding", per test
        return b"patched"

    monkeypatch.setattr(fih, "patch_font_file_metrics", fake_patch)
    monkeypatch.setattr(fih, "_inject_font_bytes", lambda *a: True)

    res = import_widths(font_obj, font_entry, tmp_path, mappings, pikepdf)
    assert res is True
    assert captured["differences"] == [1, "/B"]


def test_import_widths_patch_mode_no_differences_passes_none(tmp_path, monkeypatch):
    """When a font_entry carries no 'differences' key, None is passed through
    rather than a missing keyword argument or a stale value."""
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
    font_entry = {"embedded_file": "test.ttf", "descriptor_key": "FontFile2", "base_font": "Test"}

    f = tmp_path / "test.ttf"
    f.write_bytes(b"dummy")

    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps({"width_sync_mode": "patch_font_metrics"}))
    font_entry["sidecar_json_file"] = "sidecar.json"

    mappings = {"01": {"width": {"pdf": 999.0}}}

    import pdftl.operations.helpers.font_import_helpers as fih

    captured = {}

    def fake_patch(
        filepath,
        pdf_widths,
        differences=None,
        base_encoding=None,
        cid_to_gid_map=None,
        embedded_format=None,
        base_font="",
    ):
        captured["differences"] = differences
        return b"patched"

    monkeypatch.setattr(fih, "patch_font_file_metrics", fake_patch)
    monkeypatch.setattr(fih, "_inject_font_bytes", lambda *a: True)

    import_widths(font_obj, font_entry, tmp_path, mappings, pikepdf)
    assert captured["differences"] is None


def test_import_widths_squash_mode_passes_differences_through(tmp_path, monkeypatch):
    """Confirms font_entry['differences'] reaches squash_font_file_vectors as
    the `differences` kwarg."""
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
    font_entry = {
        "embedded_file": "test.ttf",
        "descriptor_key": "FontFile2",
        "base_font": "Test",
        "differences": [3, "/C"],
    }

    f = tmp_path / "test.ttf"
    f.write_bytes(b"dummy")

    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps({"width_sync_mode": "squash_font_vectors"}))
    font_entry["sidecar_json_file"] = "sidecar.json"

    mappings = {"03": {"width": {"pdf": 900.0}}}

    import pdftl.operations.helpers.font_import_helpers as fih

    captured = {}

    def fake_squash(
        filepath,
        pdf_widths,
        differences=None,
        base_encoding=None,
        cid_to_gid_map=None,
        embedded_format=None,
        base_font="",
    ):
        captured["differences"] = differences
        return b"squashed"

    monkeypatch.setattr(fih, "squash_font_file_vectors", fake_squash)
    monkeypatch.setattr(fih, "_inject_font_bytes", lambda *a: True)

    res = import_widths(font_obj, font_entry, tmp_path, mappings, pikepdf)
    assert res is True
    assert captured["differences"] == [3, "/C"]


def test_import_widths_patch_mode_passes_base_encoding_through(tmp_path, monkeypatch):
    """Confirms font_entry['base_encoding'] reaches patch_font_file_metrics as
    the `base_encoding` kwarg, so codes only resolvable via a declared
    /BaseEncoding (e.g. WinAnsiEncoding) are patched correctly."""
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
    font_entry = {
        "embedded_file": "test.ttf",
        "descriptor_key": "FontFile2",
        "base_font": "Test",
        "base_encoding": "WinAnsiEncoding",
    }

    f = tmp_path / "test.ttf"
    f.write_bytes(b"dummy")

    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps({"width_sync_mode": "patch_font_metrics"}))
    font_entry["sidecar_json_file"] = "sidecar.json"

    mappings = {"92": {"width": {"pdf": 400.0}}}

    import pdftl.operations.helpers.font_import_helpers as fih

    captured = {}

    def fake_patch(
        filepath,
        pdf_widths,
        differences=None,
        base_encoding=None,
        cid_to_gid_map=None,
        embedded_format=None,
        base_font="",
    ):
        captured["base_encoding"] = base_encoding
        return b"patched"

    monkeypatch.setattr(fih, "patch_font_file_metrics", fake_patch)
    monkeypatch.setattr(fih, "_inject_font_bytes", lambda *a: True)

    res = import_widths(font_obj, font_entry, tmp_path, mappings, pikepdf)
    assert res is True
    assert captured["base_encoding"] == "WinAnsiEncoding"


def test_import_widths_patch_mode_no_base_encoding_passes_none(tmp_path, monkeypatch):
    """When a font_entry carries no 'base_encoding' key, None is passed
    through rather than a missing keyword argument or a stale value."""
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
    font_entry = {"embedded_file": "test.ttf", "descriptor_key": "FontFile2", "base_font": "Test"}

    f = tmp_path / "test.ttf"
    f.write_bytes(b"dummy")

    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps({"width_sync_mode": "patch_font_metrics"}))
    font_entry["sidecar_json_file"] = "sidecar.json"

    mappings = {"01": {"width": {"pdf": 999.0}}}

    import pdftl.operations.helpers.font_import_helpers as fih

    captured = {}

    def fake_patch(
        filepath,
        pdf_widths,
        differences=None,
        base_encoding=None,
        cid_to_gid_map=None,
        embedded_format=None,
        base_font="",
    ):
        captured["base_encoding"] = base_encoding
        return b"patched"

    monkeypatch.setattr(fih, "patch_font_file_metrics", fake_patch)
    monkeypatch.setattr(fih, "_inject_font_bytes", lambda *a: True)

    import_widths(font_obj, font_entry, tmp_path, mappings, pikepdf)
    assert captured["base_encoding"] is None


def test_import_widths_squash_mode_passes_base_encoding_through(tmp_path, monkeypatch):
    """Confirms font_entry['base_encoding'] reaches squash_font_file_vectors
    as the `base_encoding` kwarg."""
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
    font_entry = {
        "embedded_file": "test.ttf",
        "descriptor_key": "FontFile2",
        "base_font": "Test",
        "base_encoding": "MacRomanEncoding",
    }

    f = tmp_path / "test.ttf"
    f.write_bytes(b"dummy")

    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps({"width_sync_mode": "squash_font_vectors"}))
    font_entry["sidecar_json_file"] = "sidecar.json"

    mappings = {"41": {"width": {"pdf": 900.0}}}

    import pdftl.operations.helpers.font_import_helpers as fih

    captured = {}

    def fake_squash(
        filepath,
        pdf_widths,
        differences=None,
        base_encoding=None,
        cid_to_gid_map=None,
        embedded_format=None,
        base_font="",
    ):
        captured["base_encoding"] = base_encoding
        return b"squashed"

    monkeypatch.setattr(fih, "squash_font_file_vectors", fake_squash)
    monkeypatch.setattr(fih, "_inject_font_bytes", lambda *a: True)

    res = import_widths(font_obj, font_entry, tmp_path, mappings, pikepdf)
    assert res is True
    assert captured["base_encoding"] == "MacRomanEncoding"


# ---------------------------------------------------------------------------
# Type 3 Font Import Unit Testing
# ---------------------------------------------------------------------------


def test_import_type3_font_missing_and_empty_guards(tmp_path):
    """Verifies safe early returns when importing with missing structural records."""
    font_dict = pikepdf.Dictionary()

    # Missing filename
    import_type3_font(font_dict, {}, tmp_path)

    # Missing physical sidecar file
    import_type3_font(font_dict, {"charprocs_file": "nonexistent.charprocs"}, tmp_path)

    # Empty font procedures
    (tmp_path / "empty.charprocs").write_text("")
    import_type3_font(font_dict, {"charprocs_file": "empty.charprocs"}, tmp_path)

    # Missing CharProcs in PDF dict
    (tmp_path / "valid.charprocs").write_text("=== Font X / CharProcs /A\n=====\ntext")
    import_type3_font(font_dict, {"charprocs_file": "valid.charprocs"}, tmp_path)


def test_import_cid_to_gid_map_no_mode():
    pdf = pikepdf.new()
    font = pikepdf.Dictionary()
    assert import_cid_to_gid_map(font, {}, Path("."), pikepdf, pdf) is False


def test_import_cid_to_gid_map_identity():
    pdf = pikepdf.new()
    cid_font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/CIDFontType2")})
    font = pikepdf.Dictionary(
        {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
    )
    entry = {"cid_to_gid_map": "Identity"}
    assert import_cid_to_gid_map(font, entry, Path("."), pikepdf, pdf) is True
    assert cid_font["/CIDToGIDMap"] == pikepdf.Name("/Identity")


def test_import_cid_to_gid_map_missing_filename():
    pdf = pikepdf.new()
    font = pikepdf.Dictionary()
    entry = {"cid_to_gid_map": "explicit"}
    assert import_cid_to_gid_map(font, entry, Path("."), pikepdf, pdf) is False


def test_import_cid_to_gid_map_file_not_found(tmp_path):
    pdf = pikepdf.new()
    font = pikepdf.Dictionary()
    entry = {"cid_to_gid_map": "explicit", "cid_to_gid_map_file": "missing.json"}
    assert import_cid_to_gid_map(font, entry, tmp_path, pikepdf, pdf) is False


def test_import_cid_to_gid_map_corrupt_file(tmp_path):
    pdf = pikepdf.new()
    font = pikepdf.Dictionary()
    entry = {"cid_to_gid_map": "explicit", "cid_to_gid_map_file": "cid2gid.json"}
    (tmp_path / "cid2gid.json").write_text("{corrupt")
    assert import_cid_to_gid_map(font, entry, tmp_path, pikepdf, pdf) is False


def test_import_cid_to_gid_map_success(tmp_path):
    pdf = pikepdf.new()
    cid_font = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/CIDFontType2")})
    font = pdf.make_indirect(
        pikepdf.Dictionary(
            {"/Subtype": pikepdf.Name("/Type0"), "/DescendantFonts": pikepdf.Array([cid_font])}
        )
    )
    entry = {"cid_to_gid_map": "explicit", "cid_to_gid_map_file": "cid2gid.json"}
    (tmp_path / "cid2gid.json").write_text(json.dumps({"cid_to_gid": {"0000": "0005"}}))
    assert import_cid_to_gid_map(font, entry, tmp_path, pikepdf, pdf) is True
    resolved_cid_font = font.DescendantFonts[0]
    assert resolved_cid_font["/CIDToGIDMap"].read_bytes() == b"\x00\x05"


def test_import_widths_patch_mode_no_embedded_file_falls_back_to_manual(tmp_path):
    """When patch_font_metrics is requested but the font has no embedded
    binary at all (e.g. an unembedded Core 14 font), the sidecar's pdf
    widths are still written manually rather than silently dropped."""
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type1")})
    font_entry = {
        "base_font": "Helvetica",
        "is_embedded": False,
        "embedded_file": None,
        "embedded_format": None,
    }

    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps({"width_sync_mode": "patch_font_metrics"}))
    font_entry["sidecar_json_file"] = "sidecar.json"

    mappings = {"20": {"width": {"pdf": 278.0}}}

    res = import_widths(font_obj, font_entry, tmp_path, mappings, pikepdf)
    assert res is False
    assert float(font_obj["/Widths"][0]) == 278.0


def test_import_widths_squash_mode_no_embedded_file_falls_back_to_manual(tmp_path):
    """Same fallback guarantee as patch_font_metrics, exercised for
    squash_font_vectors."""
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type1")})
    font_entry = {
        "base_font": "Helvetica",
        "is_embedded": False,
        "embedded_file": None,
        "embedded_format": None,
    }

    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps({"width_sync_mode": "squash_font_vectors"}))
    font_entry["sidecar_json_file"] = "sidecar.json"

    mappings = {"20": {"width": {"pdf": 300.0}}}

    res = import_widths(font_obj, font_entry, tmp_path, mappings, pikepdf)
    assert res is False
    assert float(font_obj["/Widths"][0]) == 300.0


def test_import_widths_patch_mode_no_embedded_file_and_no_pdf_widths_noop(tmp_path):
    """If there are no sidecar pdf widths at all, the graceful fallback for
    an unembedded font does nothing rather than writing an empty /Widths
    array."""
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type1")})
    font_entry = {"base_font": "Helvetica", "is_embedded": False, "embedded_file": None}

    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps({"width_sync_mode": "patch_font_metrics"}))
    font_entry["sidecar_json_file"] = "sidecar.json"

    res = import_widths(font_obj, font_entry, tmp_path, {}, pikepdf)
    assert res is False
    assert "/Widths" not in font_obj


# ---------------------------------------------------------------------------
# _find_type1_segment_lengths / _update_type1_length_fields
# ---------------------------------------------------------------------------


def _build_synthetic_type1_bytes() -> tuple[bytes, int, int, int]:
    """Builds a structurally valid (if minimal) raw Type 1 program with real
    eexec/cleartomark boundary markers, and returns (bytes, length1, length2,
    length3) so tests can assert against the exact expected split."""
    segment1 = b"%!FontType1-1.0: Fake\ncurrentfile eexec\n"
    segment2 = b"ENCRYPTEDPAYLOADEND"  # deliberately doesn't end in '0'/whitespace
    zeros_line = b"0" * 64 + b"\n"
    trailer = zeros_line + b"cleartomark\n"

    font_bytes = segment1 + segment2 + trailer
    return font_bytes, len(segment1), len(segment2), len(trailer)


def test_find_type1_segment_lengths_well_formed_program():
    from pdftl.operations.helpers.font_import_helpers import _find_type1_segment_lengths

    font_bytes, expected_l1, expected_l2, expected_l3 = _build_synthetic_type1_bytes()
    result = _find_type1_segment_lengths(font_bytes)
    assert result == (expected_l1, expected_l2, expected_l3)


def test_find_type1_segment_lengths_crlf_after_eexec():
    from pdftl.operations.helpers.font_import_helpers import _find_type1_segment_lengths

    segment1 = b"%!FontType1\ncurrentfile eexec\r\n"
    segment2 = b"PAYLOADEND"
    trailer = b"0" * 64 + b"\ncleartomark\n"
    font_bytes = segment1 + segment2 + trailer

    result = _find_type1_segment_lengths(font_bytes)
    assert result is not None
    length1, length2, length3 = result
    assert length1 == len(segment1)
    assert length2 == len(segment2)
    assert length3 == len(trailer)


def test_find_type1_segment_lengths_no_eexec_marker_returns_none():
    from pdftl.operations.helpers.font_import_helpers import _find_type1_segment_lengths

    assert _find_type1_segment_lengths(b"not a type1 program at all") is None


def test_find_type1_segment_lengths_no_cleartomark_marker_returns_none():
    from pdftl.operations.helpers.font_import_helpers import _find_type1_segment_lengths

    font_bytes = b"%!FontType1\ncurrentfile eexec\nPAYLOAD_NO_TRAILER"
    assert _find_type1_segment_lengths(font_bytes) is None


def test_find_type1_segment_lengths_cleartomark_before_eexec_returns_none():
    """A 'cleartomark' string appearing (implausibly) before the eexec marker
    can't be a valid trailer; treated as unlocatable rather than producing a
    negative-length split."""
    from pdftl.operations.helpers.font_import_helpers import _find_type1_segment_lengths

    font_bytes = b"cleartomark currentfile eexec\nPAYLOAD"
    assert _find_type1_segment_lengths(font_bytes) is None


def test_update_type1_length_fields_writes_lengths():
    import pikepdf
    from pdftl.operations.helpers.font_import_helpers import _update_type1_length_fields

    font_bytes, expected_l1, expected_l2, expected_l3 = _build_synthetic_type1_bytes()

    pdf = pikepdf.new()
    stream = pdf.make_stream(b"placeholder")
    _update_type1_length_fields(stream, font_bytes)

    assert int(stream.Length1) == expected_l1
    assert int(stream.Length2) == expected_l2
    assert int(stream.Length3) == expected_l3


def test_update_type1_length_fields_unrecognized_program_leaves_lengths_untouched():
    """When boundary markers can't be found, existing Length* fields (if any)
    are left exactly as they were rather than being overwritten with a guess."""
    import pikepdf
    from pdftl.operations.helpers.font_import_helpers import _update_type1_length_fields

    pdf = pikepdf.new()
    stream = pdf.make_stream(b"placeholder")
    stream.Length1 = 111
    stream.Length2 = 222
    stream.Length3 = 333

    _update_type1_length_fields(stream, b"not a real type1 program")

    assert int(stream.Length1) == 111
    assert int(stream.Length2) == 222
    assert int(stream.Length3) == 333


def test_inject_font_bytes_type1_updates_all_three_length_fields(tmp_path):
    """End-to-end via _inject_font_bytes: a Type 1 /FontFile re-injection
    recomputes Length1/2/3 from the new bytes, not just Length1 the way
    /FontFile2 does."""
    import pikepdf
    from pdftl.operations.helpers.font_import_helpers import _inject_font_bytes

    font_bytes, expected_l1, expected_l2, expected_l3 = _build_synthetic_type1_bytes()

    pdf = pikepdf.new()
    descriptor = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontFile": pdf.make_stream(b"old placeholder bytes"),
            }
        )
    )
    font_obj = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/FontDescriptor": descriptor,
            }
        )
    )
    font_entry = {"descriptor_key": "FontFile"}

    result = _inject_font_bytes(font_obj, font_entry, font_bytes)
    assert result is True

    resolved_stream = descriptor["/FontFile"]
    assert resolved_stream.read_bytes() == font_bytes
    assert int(resolved_stream.Length1) == expected_l1
    assert int(resolved_stream.Length2) == expected_l2
    assert int(resolved_stream.Length3) == expected_l3


def test_inject_font_bytes_type1_no_markers_leaves_lengths_and_still_writes_bytes(tmp_path):
    """Even when segment boundaries can't be recomputed, the stream bytes
    themselves are still written -- only the Length* metadata is skipped."""
    import pikepdf
    from pdftl.operations.helpers.font_import_helpers import _inject_font_bytes

    pdf = pikepdf.new()
    stream = pdf.make_stream(b"old bytes")
    stream.Length1 = 5
    descriptor = pdf.make_indirect(
        pikepdf.Dictionary({"/Type": pikepdf.Name("/FontDescriptor"), "/FontFile": stream})
    )
    font_obj = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/FontDescriptor": descriptor,
            }
        )
    )
    font_entry = {"descriptor_key": "FontFile"}

    new_bytes = b"totally unrelated binary payload with no PostScript structure at all"
    result = _inject_font_bytes(font_obj, font_entry, new_bytes)
    assert result is True

    resolved_stream = descriptor["/FontFile"]
    assert resolved_stream.read_bytes() == new_bytes
    assert int(resolved_stream.Length1) == 5  # unchanged, not overwritten with a guess


def test_find_type1_segment_lengths_no_eol_after_eexec():
    """When 'eexec' is not immediately followed by any EOL sequence at all
    (e.g. malformed or unusually generated Type 1 data), length1 ends right
    at the end of 'eexec' itself, exercising the eol_len == 0 branch."""
    from pdftl.operations.helpers.font_import_helpers import _find_type1_segment_lengths

    segment1 = b"%!FontType1-1.0: Fake\ncurrentfile eexec"  # no trailing EOL
    segment2 = b"ENCRYPTEDPAYLOADEND"
    zeros_line = b"0" * 64 + b"\n"
    trailer = zeros_line + b"cleartomark\n"

    font_bytes = segment1 + segment2 + trailer
    result = _find_type1_segment_lengths(font_bytes)

    assert result is not None
    length1, length2, length3 = result
    assert length1 == len(segment1)  # after_eexec with eol_len == 0
    assert length2 == len(segment2)
    assert length3 == len(trailer)


from pdftl.operations.helpers.font_import_helpers import (
    _resolve_cid_to_gid_for_sync,
)


# ---------------------------------------------------------------------------
# _resolve_cid_to_gid_for_sync
# ---------------------------------------------------------------------------


def test_resolve_cid_to_gid_for_sync_no_mode_is_identity(tmp_path):
    assert _resolve_cid_to_gid_for_sync(None, {}, tmp_path) == "Identity"


def test_resolve_cid_to_gid_for_sync_explicit_identity(tmp_path):
    assert (
        _resolve_cid_to_gid_for_sync(None, {"cid_to_gid_map": "Identity"}, tmp_path) == "Identity"
    )


def test_resolve_cid_to_gid_for_sync_explicit_no_filename(tmp_path):
    entry = {"cid_to_gid_map": "explicit"}
    assert _resolve_cid_to_gid_for_sync(None, entry, tmp_path) is None


def test_resolve_cid_to_gid_for_sync_file_not_found(tmp_path):
    entry = {"cid_to_gid_map": "explicit", "cid_to_gid_map_file": "missing.json"}
    assert _resolve_cid_to_gid_for_sync(None, entry, tmp_path) is None


def test_resolve_cid_to_gid_for_sync_corrupt_file(tmp_path):
    entry = {"cid_to_gid_map": "explicit", "cid_to_gid_map_file": "c2g.json"}
    (tmp_path / "c2g.json").write_text("{corrupt")
    assert _resolve_cid_to_gid_for_sync(None, entry, tmp_path) is None


def test_resolve_cid_to_gid_for_sync_success(tmp_path):
    entry = {"cid_to_gid_map": "explicit", "cid_to_gid_map_file": "c2g.json"}
    (tmp_path / "c2g.json").write_text(json.dumps({"cid_to_gid": {"000A": "0002"}}))
    assert _resolve_cid_to_gid_for_sync(None, entry, tmp_path) == {10: 2}


# ---------------------------------------------------------------------------
# _auto_sync_widths_from_font: Type0 CID-aware branch
# ---------------------------------------------------------------------------


def test_auto_sync_widths_type0_identity_passes_through(tmp_path, monkeypatch):
    """A Type0 font with an Identity CIDToGIDMap resolves to 'Identity' and
    passes it through to get_font_widths_from_file."""
    import pdftl.operations.helpers.font_import_helpers as fih

    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type0")})
    font_entry = {
        "embedded_file": "test.ttf",
        "binary_md5": "old_hash",
        "base_font": "CIDFont",
        "cid_to_gid_map": "Identity",
    }
    f = tmp_path / "test.ttf"
    f.write_bytes(b"changed_bytes")

    captured = {}

    def fake_get_widths(
        filepath,
        cid_to_gid_map=None,
        embedded_format=None,
        base_font="",
    ):
        captured["cid_to_gid_map"] = cid_to_gid_map
        return {"0001": 500.0}

    monkeypatch.setattr(fih, "get_font_widths_from_file", fake_get_widths)

    result = _auto_sync_widths_from_font(font_obj, font_entry, tmp_path, pikepdf)
    assert result is True
    assert captured["cid_to_gid_map"] == "Identity"


def test_auto_sync_widths_type0_resolution_failure_skips_sync(tmp_path, monkeypatch):
    """When the CID->GID mapping can't be resolved (missing sidecar),
    automatic width sync is skipped entirely rather than guessing."""
    import pdftl.operations.helpers.font_import_helpers as fih

    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type0")})
    font_entry = {
        "embedded_file": "test.ttf",
        "binary_md5": "old_hash",
        "base_font": "CIDFont",
        "cid_to_gid_map": "explicit",
        "cid_to_gid_map_file": "missing.json",
    }
    f = tmp_path / "test.ttf"
    f.write_bytes(b"changed_bytes")

    called = {"flag": False}

    def fake_get_widths(*a, **k):
        called["flag"] = True
        return {}

    monkeypatch.setattr(fih, "get_font_widths_from_file", fake_get_widths)

    result = _auto_sync_widths_from_font(font_obj, font_entry, tmp_path, pikepdf)
    assert result is False
    assert called["flag"] is False


# ---------------------------------------------------------------------------
# import_widths -> _apply_in_memory_patch / _apply_in_memory_squash:
# Type0 CID-aware branches
# ---------------------------------------------------------------------------


def test_import_widths_patch_mode_type0_resolution_failure_returns_false(tmp_path):
    """A Type0 font requesting patch_font_metrics with an unresolvable
    CID->GID mapping is skipped rather than falling through to cmap-based
    patching."""
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type0")})
    font_entry = {
        "embedded_file": "test.ttf",
        "descriptor_key": "FontFile2",
        "base_font": "CIDFont",
        "cid_to_gid_map": "explicit",
        "cid_to_gid_map_file": "missing.json",
    }
    f = tmp_path / "test.ttf"
    f.write_bytes(b"dummy")

    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps({"width_sync_mode": "patch_font_metrics"}))
    font_entry["sidecar_json_file"] = "sidecar.json"

    mappings = {"0001": {"width": {"pdf": 999.0}}}
    res = import_widths(font_obj, font_entry, tmp_path, mappings, pikepdf)
    assert res is False


def test_import_widths_patch_mode_type0_passes_cid_to_gid_map_through(tmp_path, monkeypatch):
    """A Type0 font with a resolvable CID->GID mapping passes it to
    patch_font_file_metrics, and passes differences/base_encoding as None
    since /Differences never applies to Type0 fonts."""
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type0")})
    font_entry = {
        "embedded_file": "test.ttf",
        "descriptor_key": "FontFile2",
        "base_font": "CIDFont",
        "cid_to_gid_map": "Identity",
    }
    f = tmp_path / "test.ttf"
    f.write_bytes(b"dummy")

    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps({"width_sync_mode": "patch_font_metrics"}))
    font_entry["sidecar_json_file"] = "sidecar.json"

    mappings = {"0001": {"width": {"pdf": 999.0}}}

    import pdftl.operations.helpers.font_import_helpers as fih

    captured = {}

    def fake_patch(
        filepath,
        pdf_widths,
        differences=None,
        base_encoding=None,
        cid_to_gid_map=None,
        embedded_format=None,
        base_font="",
    ):
        captured["differences"] = differences
        captured["base_encoding"] = base_encoding
        captured["cid_to_gid_map"] = cid_to_gid_map
        return b"patched"

    monkeypatch.setattr(fih, "patch_font_file_metrics", fake_patch)
    monkeypatch.setattr(fih, "_inject_font_bytes", lambda *a: True)

    res = import_widths(font_obj, font_entry, tmp_path, mappings, pikepdf)
    assert res is True
    assert captured["cid_to_gid_map"] == "Identity"
    assert captured["differences"] is None
    assert captured["base_encoding"] is None


def test_import_widths_squash_mode_type0_resolution_failure_returns_false(tmp_path):
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type0")})
    font_entry = {
        "embedded_file": "test.ttf",
        "descriptor_key": "FontFile2",
        "base_font": "CIDFont",
        "cid_to_gid_map": "explicit",
        "cid_to_gid_map_file": "missing.json",
    }
    f = tmp_path / "test.ttf"
    f.write_bytes(b"dummy")

    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps({"width_sync_mode": "squash_font_vectors"}))
    font_entry["sidecar_json_file"] = "sidecar.json"

    mappings = {"0001": {"width": {"pdf": 999.0}}}
    res = import_widths(font_obj, font_entry, tmp_path, mappings, pikepdf)
    assert res is False


def test_import_widths_squash_mode_type0_passes_cid_to_gid_map_through(tmp_path, monkeypatch):
    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type0")})
    font_entry = {
        "embedded_file": "test.ttf",
        "descriptor_key": "FontFile2",
        "base_font": "CIDFont",
        "cid_to_gid_map": "explicit",
        "cid_to_gid_map_file": "c2g.json",
    }
    (tmp_path / "c2g.json").write_text(json.dumps({"cid_to_gid": {"0001": "0002"}}))

    f = tmp_path / "test.ttf"
    f.write_bytes(b"dummy")

    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps({"width_sync_mode": "squash_font_vectors"}))
    font_entry["sidecar_json_file"] = "sidecar.json"

    mappings = {"0001": {"width": {"pdf": 999.0}}}

    import pdftl.operations.helpers.font_import_helpers as fih

    captured = {}

    def fake_squash(
        filepath,
        pdf_widths,
        differences=None,
        base_encoding=None,
        cid_to_gid_map=None,
        embedded_format=None,
        base_font="",
    ):
        captured["cid_to_gid_map"] = cid_to_gid_map
        return b"squashed"

    monkeypatch.setattr(fih, "squash_font_file_vectors", fake_squash)
    monkeypatch.setattr(fih, "_inject_font_bytes", lambda *a: True)

    res = import_widths(font_obj, font_entry, tmp_path, mappings, pikepdf)
    assert res is True
    assert captured["cid_to_gid_map"] == {1: 2}


def test_read_descriptor_sidecar_outcomes(tmp_path):
    from pdftl.operations.helpers.font_import_helpers import _read_descriptor_sidecar

    font_entry = {"sidecar_json_file": "sidecar.json"}
    sidecar = tmp_path / "sidecar.json"

    assert _read_descriptor_sidecar(font_entry, tmp_path) is None

    sidecar.write_text("{bad json format")
    assert _read_descriptor_sidecar(font_entry, tmp_path) is None

    sidecar.write_text(json.dumps({"descriptor": {"Ascent": 100}}))
    assert _read_descriptor_sidecar(font_entry, tmp_path) == {"Ascent": 100}


def test_apply_descriptor_edit_no_change():
    from pdftl.operations.helpers.font_import_helpers import _apply_descriptor_edit

    desc = {"/Ascent": 100}
    assert _apply_descriptor_edit(desc, "/Ascent", 100) is False


def test_import_descriptor_empty_desc_data(tmp_path):
    from pdftl.operations.helpers.font_import_helpers import import_descriptor
    import pikepdf
    import json

    font_entry = {"sidecar_json_file": "sidecar.json"}
    sidecar = tmp_path / "sidecar.json"
    sidecar.write_text(json.dumps({"descriptor": {}}))
    font_obj = pikepdf.Dictionary({"/FontDescriptor": pikepdf.Dictionary()})
    assert import_descriptor(font_obj, font_entry, tmp_path, pikepdf) is False


def test_import_type3_font_reconstruction_success(tmp_path):
    from pdftl.operations.helpers.font_import_helpers import import_type3_font
    from PIL import Image
    import pikepdf
    import json

    img = Image.new("L", (2, 2))
    img_path = tmp_path / "img0.tiff"
    img.save(img_path)

    charprocs_file = tmp_path / "font.charprocs"
    charprocs_file.write_text(
        "========================================================================\n"
        "=== Font 1_0_Test / CharProcs /A\n"
        "========================================================================\n"
        "100 0 d1\n"
        "%BEGIN_INLINE_IMAGE%\n"
        f"%META: {json.dumps({'Width': 2, 'Height': 2, 'ColorSpace': '/DeviceGray', 'BitsPerComponent': 8})}\n"
        "%REF: img0.tiff\n"
        "%END_INLINE_IMAGE%\n"
        "Q\n"
    )
    font_entry = {"charprocs_file": "font.charprocs", "inline_images": {}}
    pdf = pikepdf.new()
    charprocs_dict = pikepdf.Dictionary({"/A": pdf.make_stream(b"old_data")})
    font_obj = pikepdf.Dictionary({"/CharProcs": charprocs_dict})

    import_type3_font(font_obj, font_entry, tmp_path)

    rebuilt = charprocs_dict["/A"].read_bytes()
    assert b"BI " in rebuilt
    assert b"ID\n" in rebuilt
    assert b"EI" in rebuilt
    assert b"100 0 d1" in rebuilt


def test_inject_font_bytes_fontfile_type1(tmp_path, monkeypatch):
    from pdftl.operations.helpers.font_import_helpers import _inject_font_bytes
    import pdftl.operations.helpers.font_import_helpers as fih
    import pikepdf

    called = {}
    monkeypatch.setattr(
        fih, "_update_type1_length_fields", lambda s, b: called.setdefault("ok", True)
    )

    pdf = pikepdf.new()
    desc = pikepdf.Dictionary({"/FontFile": pdf.make_stream(b"old")})
    font_obj = pikepdf.Dictionary({"/FontDescriptor": pdf.make_indirect(desc)})
    font_entry = {"descriptor_key": "FontFile"}

    assert _inject_font_bytes(font_obj, font_entry, b"new") is True
    assert called["ok"] is True


def test_import_single_font_binary_missing_filename(tmp_path):
    from pdftl.operations.helpers.font_import_helpers import import_single_font_binary

    assert import_single_font_binary({}, {}, tmp_path) is False


def test_import_single_font_binary_hash_match(tmp_path):
    from pdftl.operations.helpers.font_import_helpers import import_single_font_binary
    from pdftl.operations.helpers.font_ops_shared import file_hash

    f = tmp_path / "font.ttf"
    f.write_bytes(b"data")
    font_entry = {"embedded_file": "font.ttf", "binary_md5": file_hash(f)}
    assert import_single_font_binary({}, font_entry, tmp_path) is False


def test_import_single_font_binary_success(tmp_path, monkeypatch):
    from pdftl.operations.helpers.font_import_helpers import import_single_font_binary
    import pdftl.operations.helpers.font_import_helpers as fih

    f = tmp_path / "font.ttf"
    f.write_bytes(b"new_data")
    font_entry = {
        "embedded_file": "font.ttf",
        "binary_md5": "old_hash",
        "descriptor_key": "FontFile2",
        "base_font": "F",
    }
    monkeypatch.setattr(fih, "_inject_font_bytes", lambda *a: True)
    assert import_single_font_binary({}, font_entry, tmp_path) is True


def test_resolve_sidecar_clash_and_files(tmp_path):
    from pdftl.operations.helpers.font_import_helpers import _resolve_sidecar_clash_and_files
    from pdftl.exceptions import UserCommandLineError
    import pytest

    j = tmp_path / "a.json"
    p = tmp_path / "a.ps"
    j.write_text("{}")
    p.write_text("ps")
    font_entry = {"sidecar_json_file": "a.json", "tounicode_ps_file": "a.ps"}

    with pytest.raises(UserCommandLineError, match="Ambiguous ToUnicode source"):
        _resolve_sidecar_clash_and_files(font_entry, tmp_path)


def test_import_tounicode_json_success(tmp_path):
    from pdftl.operations.helpers.font_import_helpers import _import_tounicode_json
    import pikepdf
    import json

    pdf = pikepdf.new()
    f = tmp_path / "u.json"
    f.write_text(json.dumps({"mappings": {"01": {"unicode": "A"}}}))
    font_obj = pikepdf.Dictionary()

    res = _import_tounicode_json(font_obj, f, {"base_font": "F"}, pikepdf, pdf)
    assert res == {"01": {"unicode": "A"}}
    assert "/ToUnicode" in font_obj


def test_import_tounicode_ps_success(tmp_path):
    from pdftl.operations.helpers.font_import_helpers import _import_tounicode_ps
    import pikepdf

    pdf = pikepdf.new()
    f = tmp_path / "u.ps"
    f.write_bytes(b"PS")
    font_obj = pikepdf.Dictionary()

    _import_tounicode_ps(font_obj, f, {}, pikepdf, pdf)
    assert "/ToUnicode" in font_obj
    assert font_obj["/ToUnicode"].read_bytes() == b"PS"


def test_import_tounicode_and_get_mappings_dispatch(tmp_path, monkeypatch):
    from pdftl.operations.helpers.font_import_helpers import import_tounicode_and_get_mappings
    import pdftl.operations.helpers.font_import_helpers as fih
    import pikepdf

    font_entry = {"sidecar_json_file": "a.json"}
    j = tmp_path / "a.json"
    j.write_text("{}")
    monkeypatch.setattr(fih, "_import_tounicode_json", lambda *a, **k: {"mock": 1})

    res = import_tounicode_and_get_mappings({}, font_entry, tmp_path, pikepdf)
    assert res == {"mock": 1}


def test_import_differences_success():
    from pdftl.operations.helpers.font_import_helpers import import_differences
    import pikepdf

    font_obj = pikepdf.Dictionary()
    font_entry = {"differences": [1, "/A", 2, "B"]}

    import_differences(font_obj, font_entry, pikepdf)
    arr = font_obj["/Encoding"]["/Differences"]
    assert len(arr) == 4
    assert int(arr[0]) == 1
    assert str(arr[1]) == "/A"
    assert int(arr[2]) == 2
    assert str(arr[3]) == "/B"


def test_resolve_cid_to_gid_for_sync_bare_cff_format(tmp_path):
    from pdftl.operations.helpers.font_import_helpers import _resolve_cid_to_gid_for_sync

    assert _resolve_cid_to_gid_for_sync({}, {"embedded_format": "cff"}, tmp_path) == "cff_native"


def test_rekey_simple_font_widths_if_needed_cff_rekey():
    from pdftl.operations.helpers.font_import_helpers import _rekey_simple_font_widths_if_needed

    assert _rekey_simple_font_widths_if_needed({"A": 100}, {"embedded_format": "ttf"}) == {
        "A": 100
    }
    font_entry = {"embedded_format": "cff", "differences": [0x41, "/A"]}
    res = _rekey_simple_font_widths_if_needed({"A": 100}, font_entry)
    assert res == {"41": 100.0}


def test_auto_sync_widths_from_font_hash_match(tmp_path):
    from pdftl.operations.helpers.font_import_helpers import _auto_sync_widths_from_font
    from pdftl.operations.helpers.font_ops_shared import file_hash
    import pikepdf

    f = tmp_path / "font.ttf"
    f.write_bytes(b"data")
    assert (
        _auto_sync_widths_from_font(
            {}, {"embedded_file": "font.ttf", "binary_md5": file_hash(f)}, tmp_path, pikepdf
        )
        is False
    )


def test_execute_widths_sync_manual_mode(tmp_path, monkeypatch):
    from pdftl.operations.helpers.font_import_helpers import _execute_widths_sync
    import pdftl.operations.helpers.font_import_helpers as fih
    import pikepdf

    called = {}
    monkeypatch.setattr(fih, "update_font_widths", lambda *a: called.setdefault("updated", True))
    _execute_widths_sync({}, {}, tmp_path, "manual", {"01": 100}, None, pikepdf)
    assert called["updated"] is True


def test_read_descriptor_sidecar_no_key_returns_none(tmp_path):
    from pdftl.operations.helpers.font_import_helpers import _read_descriptor_sidecar

    assert _read_descriptor_sidecar({}, tmp_path) is None


def test_import_differences_missing_key_is_noop():
    from pdftl.operations.helpers.font_import_helpers import import_differences
    import pikepdf

    font_obj = pikepdf.Dictionary()
    import_differences(font_obj, {}, pikepdf)
    assert "/Encoding" not in font_obj


def test_import_single_font_binary_file_not_on_disk(tmp_path):
    from pdftl.operations.helpers.font_import_helpers import import_single_font_binary

    font_entry = {"embedded_file": "missing.ttf", "binary_md5": "x"}
    assert import_single_font_binary({}, font_entry, tmp_path) is False


def test_import_tounicode_and_get_mappings_ps_branch_and_neither(tmp_path):
    from pdftl.operations.helpers.font_import_helpers import import_tounicode_and_get_mappings
    import pikepdf

    pdf = pikepdf.new()
    font_obj = pikepdf.Dictionary()

    # neither json nor ps present -> {}
    assert import_tounicode_and_get_mappings(font_obj, {}, tmp_path, pikepdf, pdf) == {}

    # ps present -> writes /ToUnicode, returns {}
    ps = tmp_path / "u.ps"
    ps.write_bytes(b"PS")
    res = import_tounicode_and_get_mappings(
        font_obj, {"tounicode_ps_file": "u.ps"}, tmp_path, pikepdf, pdf
    )
    assert res == {}
    assert "/ToUnicode" in font_obj


def test_inject_font_bytes_fontfile2_updates_length1(tmp_path):
    """440: /FontFile2 branch sets Length1 to the new byte count."""
    from pdftl.operations.helpers.font_import_helpers import _inject_font_bytes
    import pikepdf

    pdf = pikepdf.new()
    desc = pikepdf.Dictionary({"/FontFile2": pdf.make_stream(b"old")})
    font_obj = pikepdf.Dictionary({"/FontDescriptor": pdf.make_indirect(desc)})
    font_entry = {"descriptor_key": "FontFile2"}

    new_bytes = b"new_font_bytes_1234"
    assert _inject_font_bytes(font_obj, font_entry, new_bytes) is True
    resolved = desc["/FontFile2"]
    assert resolved.read_bytes() == new_bytes
    assert int(resolved.Length1) == len(new_bytes)


def test_import_tounicode_json_updates_existing_stream(tmp_path):
    """527: existing /ToUnicode is overwritten via .write(), not replaced."""
    from pdftl.operations.helpers.font_import_helpers import _import_tounicode_json
    import pikepdf
    import json

    pdf = pikepdf.new()
    f = tmp_path / "u.json"
    f.write_text(json.dumps({"mappings": {"01": {"unicode": "A"}}}))

    font_obj = pikepdf.Dictionary({"/ToUnicode": pdf.make_stream(b"old")})
    res = _import_tounicode_json(font_obj, f, {"base_font": "F"}, pikepdf, pdf)
    assert res == {"01": {"unicode": "A"}}
    assert font_obj["/ToUnicode"].read_bytes() != b"old"


def test_import_tounicode_ps_updates_existing_stream(tmp_path):
    """552: same, for the PS sidecar path."""
    from pdftl.operations.helpers.font_import_helpers import _import_tounicode_ps
    import pikepdf

    pdf = pikepdf.new()
    f = tmp_path / "u.ps"
    f.write_bytes(b"NEWPS")

    font_obj = pikepdf.Dictionary({"/ToUnicode": pdf.make_stream(b"old")})
    _import_tounicode_ps(font_obj, f, {}, pikepdf, pdf)
    assert font_obj["/ToUnicode"].read_bytes() == b"NEWPS"


def test_auto_sync_widths_from_font_file_missing_returns_false(tmp_path):
    """804: embedded_file named in the entry, but not actually on disk."""
    from pdftl.operations.helpers.font_import_helpers import _auto_sync_widths_from_font
    import pikepdf

    font_entry = {"embedded_file": "does_not_exist.ttf", "binary_md5": "whatever"}
    assert _auto_sync_widths_from_font({}, font_entry, tmp_path, pikepdf) is False


def test_auto_sync_widths_bare_cff_rekey_empties_returns_false(tmp_path, monkeypatch):
    """837: a bare-CFF font whose name-keyed widths rekey to nothing (no
    /Differences or /BaseEncoding to translate any glyph name) -> False."""
    from pdftl.operations.helpers.font_import_helpers import _auto_sync_widths_from_font
    import pdftl.operations.helpers.font_import_helpers as fih
    import pikepdf

    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type1")})
    font_entry = {
        "embedded_file": "test.cff",
        "binary_md5": "old_hash",
        "base_font": "TestFont",
        "embedded_format": "cff",
    }
    f = tmp_path / "test.cff"
    f.write_bytes(b"changed_bytes")

    monkeypatch.setattr(fih, "get_font_widths_from_file", lambda *a, **k: {"A": 500.0})

    assert _auto_sync_widths_from_font(font_obj, font_entry, tmp_path, pikepdf) is False


def test_import_widths_preserve_mode_returns_false(tmp_path):
    """1091: sync_mode == 'preserve' -> no writes at all."""
    from pdftl.operations.helpers.font_import_helpers import import_widths
    import pikepdf
    import json

    font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/TrueType")})
    font_entry = {"sidecar_json_file": "sidecar.json"}
    (tmp_path / "sidecar.json").write_text(json.dumps({"width_sync_mode": "preserve"}))

    mappings = {"41": {"width": {"pdf": 500.0}}}
    res = import_widths(font_obj, font_entry, tmp_path, mappings, pikepdf)
    assert res is False
    assert "/Widths" not in font_obj
