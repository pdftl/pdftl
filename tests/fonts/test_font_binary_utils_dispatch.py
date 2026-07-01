# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/fonts/test_font_binary_utils_dispatch.py

"""
Tests for the format-dispatch surface of pdftl.fonts.font_binary_utils:
`classify_binary_format` and the bare_cff/type1/unknown branches of
`get_font_widths_from_file`, `patch_font_file_metrics`, and
`squash_font_file_vectors`.

These branches were previously untested from font_binary_utils.py's own
test suite: pdftl.fonts.cff_binary_utils and pdftl.fonts.type1_binary_utils
each have their own thorough test modules covering the *readers themselves*,
but nothing exercised the small amount of glue in font_binary_utils.py that
decides to call them in the first place. This module monkeypatches those
two submodules directly (via monkeypatch.setattr on the imported module
objects) so it stays a pure dispatch test -- it asserts *that* the right
function got called with the right arguments, not that cff/type1 reading
itself works (that's already covered elsewhere).

Also covers the squash_font_file_vectors "sfnt" branch explicitly to guard
against the dead-code duplication bug fixed during the font_binary_utils.py
/ font_binary_sfnt.py split: prior to that fix, the embedded_format="sfnt"
path and the embedded_format=None path used two independent, hand-copied
implementations of the same TTFont try/except/import logic
(`_squash_font_file_vectors_via_ttfont`, defined but never actually
called). Now both paths delegate to the exact same function.
"""

from __future__ import annotations

import pytest

from pdftl.fonts.font_binary_utils import (
    classify_binary_format,
    get_font_widths_from_file,
    patch_font_file_metrics,
    squash_font_file_vectors,
)


class TestClassifyBinaryFormat:
    @pytest.mark.parametrize("fmt", ["ttf", "otf", "cff2"])
    def test_sfnt_compatible_formats(self, fmt):
        assert classify_binary_format(fmt) == "sfnt"

    def test_bare_cff_format(self):
        assert classify_binary_format("cff") == "bare_cff"

    def test_type1_format(self):
        assert classify_binary_format("pfb") == "type1"

    def test_none_is_unknown(self):
        assert classify_binary_format(None) == "unknown"

    def test_empty_string_is_unknown(self):
        assert classify_binary_format("") == "unknown"

    def test_unrecognized_string_is_unknown(self):
        assert classify_binary_format("some_future_format") == "unknown"


class TestGetFontWidthsFromFileDispatch:
    def test_bare_cff_dispatches_to_cff_binary_utils(self, monkeypatch, tmp_path):
        import pdftl.fonts.cff_binary_utils as cbu

        captured = {}

        def fake_get_widths(filepath, cid_to_gid_map=None):
            captured["filepath"] = filepath
            captured["cid_to_gid_map"] = cid_to_gid_map
            return {"0001": 500.0}

        monkeypatch.setattr(cbu, "get_widths_from_cff", fake_get_widths)

        f = tmp_path / "font.cff"
        f.write_bytes(b"fake")
        result = get_font_widths_from_file(f, cid_to_gid_map="cff_native", embedded_format="cff")

        assert result == {"0001": 500.0}
        assert captured["filepath"] == f
        assert captured["cid_to_gid_map"] == "cff_native"

    def test_type1_dispatches_to_type1_binary_utils(self, monkeypatch, tmp_path):
        import pdftl.fonts.type1_binary_utils as t1u

        captured = {}

        def fake_get_widths(filepath):
            captured["filepath"] = filepath
            return {"A": 250.0}

        monkeypatch.setattr(t1u, "get_widths_from_type1", fake_get_widths)

        f = tmp_path / "font.pfb"
        f.write_bytes(b"fake")
        result = get_font_widths_from_file(f, embedded_format="pfb")

        assert result == {"A": 250.0}
        assert captured["filepath"] == f

    def test_unknown_format_returns_empty_dict(self, tmp_path):
        f = tmp_path / "font.xyz"
        f.write_bytes(b"fake")
        assert get_font_widths_from_file(f, embedded_format="some_future_format") == {}

    def test_sfnt_embedded_format_dispatches_same_as_omitted(self, monkeypatch, tmp_path):
        """embedded_format='ttf' and omitting embedded_format entirely both
        reach the same underlying sfnt reader.

        Patches the re-exported `fbu._get_font_widths_via_ttfont` name, not
        `font_binary_sfnt.get_font_widths_via_ttfont` directly: font_binary_utils
        imported that function by value at module-load time (`from ... import
        get_font_widths_via_ttfont as _get_font_widths_via_ttfont`), so it's
        `font_binary_utils`'s own bound reference that both call sites here
        actually use.
        """
        import pdftl.fonts.font_binary_utils as fbu

        calls = []

        def fake_via_ttfont(filepath, cid_to_gid_map=None):
            calls.append((filepath, cid_to_gid_map))
            return {"01": 111.0}

        monkeypatch.setattr(fbu, "_get_font_widths_via_ttfont", fake_via_ttfont)

        f = tmp_path / "font.ttf"
        f.write_bytes(b"fake")

        result_omitted = get_font_widths_from_file(f)
        result_explicit = get_font_widths_from_file(f, embedded_format="ttf")

        assert result_omitted == result_explicit == {"01": 111.0}
        assert len(calls) == 2


class TestPatchFontFileMetricsDispatch:
    def test_bare_cff_dispatches_to_cff_binary_utils(self, monkeypatch, tmp_path):
        import pdftl.fonts.cff_binary_utils as cbu

        captured = {}

        def fake_patch(filepath, pdf_widths, cid_to_gid_map=None):
            captured["pdf_widths"] = pdf_widths
            captured["cid_to_gid_map"] = cid_to_gid_map
            return b"patched_cff"

        monkeypatch.setattr(cbu, "patch_cff_widths", fake_patch)

        f = tmp_path / "font.cff"
        f.write_bytes(b"fake")
        result = patch_font_file_metrics(
            f, {"A": 999.0}, cid_to_gid_map="cff_native", embedded_format="cff"
        )

        assert result == b"patched_cff"
        assert captured["pdf_widths"] == {"A": 999.0}
        assert captured["cid_to_gid_map"] == "cff_native"

    def test_type1_dispatches_to_type1_binary_utils(self, monkeypatch, tmp_path):
        import pdftl.fonts.type1_binary_utils as t1u

        captured = {}

        def fake_patch(filepath, pdf_widths):
            captured["pdf_widths"] = pdf_widths
            return b"patched_type1"

        monkeypatch.setattr(t1u, "patch_type1_widths", fake_patch)

        f = tmp_path / "font.pfb"
        f.write_bytes(b"fake")
        # Type 1 is Simple-font-only (never CID-keyed), so
        # patch_font_file_metrics always rekeys pdf_widths from hex-PDF-code
        # keys to glyph-name keys before dispatching to patch_type1_widths --
        # see font_binary_utils.py's "Simple-font key-space translation"
        # section. A /Differences entry is required for code "0A" to
        # resolve to a real glyph name rather than being dropped.
        result = patch_font_file_metrics(
            f, {"0A": 500.0}, differences=[0x0A, "/A"], embedded_format="pfb"
        )

        assert result == b"patched_type1"
        assert captured["pdf_widths"] == {"A": 500.0}

    def test_unknown_format_returns_none(self, tmp_path):
        f = tmp_path / "font.xyz"
        f.write_bytes(b"fake")
        assert patch_font_file_metrics(f, {"A": 1.0}, embedded_format="some_future_format") is None


class TestSquashFontFileVectorsDispatch:
    def test_bare_cff_returns_none_with_warning(self, tmp_path, caplog):
        f = tmp_path / "font.cff"
        f.write_bytes(b"fake")
        with caplog.at_level("INFO"):
            result = squash_font_file_vectors(f, {"A": 1.0}, embedded_format="cff")
        assert result is None
        assert "not supported for CFF/Type1" in caplog.text

    def test_type1_returns_none_with_warning(self, tmp_path, caplog):
        f = tmp_path / "font.pfb"
        f.write_bytes(b"fake")
        with caplog.at_level("INFO"):
            result = squash_font_file_vectors(f, {"A": 1.0}, embedded_format="pfb")
        assert result is None
        assert "not supported for CFF/Type1" in caplog.text

    def test_unknown_format_returns_none_with_warning(self, tmp_path, caplog):
        f = tmp_path / "font.xyz"
        f.write_bytes(b"fake")
        with caplog.at_level("WARNING"):
            result = squash_font_file_vectors(f, {"A": 1.0}, embedded_format="some_future_format")
        assert result is None
        assert "Unrecognized font binary format" in caplog.text

    def test_sfnt_embedded_format_and_omitted_use_the_same_underlying_call(
        self, monkeypatch, tmp_path
    ):
        """
        Regression guard for the dead-code duplication bug: before the
        font_binary_sfnt.py split, embedded_format='ttf'/'otf'/'cff2' and
        embedded_format=None each ran their own independently-copied
        try/except/TTFont-import block. Confirms both now call through to
        the exact same function object exactly once each, rather than one
        of them being an unreachable/duplicated code path.
        """
        import pdftl.fonts.font_binary_utils as fbu

        calls = []

        def fake_via_ttfont(
            filepath, pdf_widths, differences=None, base_encoding=None, cid_to_gid_map=None
        ):
            calls.append(filepath)
            return b"squashed"

        monkeypatch.setattr(fbu, "_squash_font_file_vectors_via_ttfont", fake_via_ttfont)

        f = tmp_path / "font.ttf"
        f.write_bytes(b"fake")

        result_omitted = squash_font_file_vectors(f, {"A": 1.0})
        result_explicit = squash_font_file_vectors(f, {"A": 1.0}, embedded_format="ttf")

        assert result_omitted == b"squashed"
        assert result_explicit == b"squashed"
        assert calls == [f, f]
