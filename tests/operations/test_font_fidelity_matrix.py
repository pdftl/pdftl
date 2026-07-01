# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/test_font_fidelity_matrix.py

"""
Follow-up tests closing the lacunae identified after
test_font_fidelity_gaps.py: that file only demonstrated the
`embedded_format` dispatch bug for `patch_font_metrics` mode against two
CFF flavors. This file extends coverage to:

  1. `squash_font_vectors` mode -- same dispatch bug, untested there.
  2. Type 1 (`/FontFile`, .pfb) fonts -- same dispatch bug, untested at all.
     (Genuine Type 1 byte fixtures are not buildable from scratch in this
     codebase's own fixture module -- see font_fixture_builder.py's
     docstring -- so these use a kwargs-capture/monkeypatch strategy
     instead of full round-trip bytes, which decouples the assertion from
     that blocker entirely.)
  3. Whether a genuinely FDArray-based, multi-FD CID-keyed CFF (distinct
     nominalWidthX per FD) patches correctly once cff_binary_utils is
     actually reached -- tested by calling cff_binary_utils directly,
     bypassing the outer dispatch bug so this isn't gated on it being
     fixed first.
  4. Whether `auto` mode's read path (`get_font_widths_from_file`) has the
     same missing `embedded_format` threading as the patch/squash paths.
  5. Type 3 inline-image extraction's `BI...ID...EI` regex misparsing a
     binary payload that happens to contain a whitespace-bounded literal
     "ID" or "EI" byte sequence.

CAVEAT: written without access to a live pdftl checkout/environment.
Syntax-checked only (`python -m py_compile`), not executed against the
real package. The multi-FD CFF builder in particular uses lower-level
fontTools.cffLib API (CharStrings() with a None container-level `private`
and per-charstring `.private` overrides for FD-select resolution) that I
was not able to verify by running; if it doesn't compile/decompile
cleanly on first try, that construction is the most likely place to need
adjustment, not the assertions built on top of it.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pikepdf
import pytest

from pdftl.operations.export_import_fonts import (
    export_fonts,
    export_fonts_cli_hook,
    import_fonts,
)
from pdftl.operations.helpers.font_import_helpers import import_widths

sys.path.insert(0, str(Path(__file__).parent.parent / "fonts" / "fixtures"))
from font_fixture_builder import (  # noqa: E402
    SQUARE_500,
    TRIANGLE_300,
    _MinimalOTFontStub,
    build_bare_cff_bytes,
    build_cid_keyed_cff_bytes,
)


def _export(pdf, export_dir) -> None:
    """export_fonts() alone does not write manifest.json -- see
    test_font_fidelity_gaps.py's _export() for the full explanation."""
    res = export_fonts(pdf, [str(export_dir)])
    assert res.success
    export_fonts_cli_hook(res, None, None)


def _make_pdf_with_type1c_font(cff_bytes: bytes):
    pdf = pikepdf.new()
    page = pdf.add_blank_page()

    stream = pdf.make_stream(cff_bytes)
    stream.Subtype = pikepdf.Name("/Type1C")

    descriptor = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name("/TestType1C"),
                "/Flags": 32,
                "/FontFile3": stream,
            }
        )
    )

    font_obj = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/BaseFont": pikepdf.Name("/TestType1C"),
                "/FirstChar": 0x41,
                "/LastChar": 0x42,
                "/Widths": pikepdf.Array([500.0, 300.0]),
                "/FontDescriptor": descriptor,
                "/Encoding": pikepdf.Dictionary(
                    {"/Differences": pikepdf.Array([0x41, pikepdf.Name("/A"), pikepdf.Name("/B")])}
                ),
            }
        )
    )
    page.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_obj})})
    return pdf, font_obj


def _make_pdf_with_cid_keyed_cff_font(cff_bytes: bytes | None = None):
    pdf = pikepdf.new()
    page = pdf.add_blank_page()

    if cff_bytes is None:
        cff_bytes = build_cid_keyed_cff_bytes({1: SQUARE_500, 2: TRIANGLE_300})

    stream = pdf.make_stream(cff_bytes)
    stream.Subtype = pikepdf.Name("/CIDFontType0C")

    descriptor = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name("/TestCIDCFF"),
                "/Flags": 32,
                "/FontFile3": stream,
            }
        )
    )

    cid_font = pikepdf.Dictionary(
        {
            "/Type": pikepdf.Name("/Font"),
            "/Subtype": pikepdf.Name("/CIDFontType0"),
            "/BaseFont": pikepdf.Name("/TestCIDCFF"),
            "/CIDSystemInfo": pikepdf.Dictionary(
                {
                    "/Registry": pikepdf.String("Adobe"),
                    "/Ordering": pikepdf.String("Identity"),
                    "/Supplement": 0,
                }
            ),
            "/FontDescriptor": descriptor,
            "/W": pikepdf.Array([1, pikepdf.Array([500.0, 300.0])]),
        }
    )

    font_obj = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/Type0"),
                "/BaseFont": pikepdf.Name("/TestCIDCFF"),
                "/Encoding": pikepdf.Name("/Identity-H"),
                "/DescendantFonts": pikepdf.Array([cid_font]),
            }
        )
    )
    page.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_obj})})
    return pdf, font_obj, cid_font


# ---------------------------------------------------------------------------
# 1. squash_font_vectors mode -- same embedded_format dispatch bug as
#    patch_font_metrics, for both CFF flavors.
# ---------------------------------------------------------------------------


class TestSquashFontVectorsDispatchForCff:
    def test_squash_mode_actually_touches_simple_cff_binary(self, tmp_path):
        """
        squash_font_vectors mode should rescale the embedded CFF's glyph
        outline in-memory to visually match the requested PDF width, and
        write the modified binary back. If (as with patch_font_metrics)
        `embedded_format` is never threaded through to
        squash_font_file_vectors, the sfnt/TTFont path is tried, fails to
        open a bare CFF, and the whole thing silently falls back to a
        manual /Widths write -- so the embedded font's own width is left
        unchanged.
        """
        cff_bytes = build_bare_cff_bytes({"A": SQUARE_500, "B": TRIANGLE_300})
        pdf, font_obj = _make_pdf_with_type1c_font(cff_bytes)

        export_dir = tmp_path / "fonts"
        _export(pdf, export_dir)

        sidecar_files = list(export_dir.glob("font_*.json"))
        assert len(sidecar_files) == 1
        sidecar = json.loads(sidecar_files[0].read_text())
        sidecar["width_sync_mode"] = "squash_font_vectors"
        assert "41" in sidecar["mappings"], sidecar["mappings"].keys()
        sidecar["mappings"]["41"]["width"]["pdf"] = 900.0
        sidecar_files[0].write_text(json.dumps(sidecar))

        res_import = import_fonts(pdf, [str(export_dir)])
        assert res_import.success

        from pdftl.fonts.cff_binary_utils import get_widths_from_cff

        patched_bytes = font_obj.FontDescriptor.FontFile3.read_bytes()
        reread_path = tmp_path / "reread.cff"
        reread_path.write_bytes(patched_bytes)
        widths = get_widths_from_cff(reread_path)

        assert widths.get("A") == 900.0, (
            "squash_font_vectors mode did not actually touch the embedded "
            "bare CFF binary -- same embedded_format dispatch gap as "
            "patch_font_metrics mode, just on a different call site"
        )

    def test_squash_mode_actually_touches_cid_keyed_cff_binary(self, tmp_path):
        """Same as above, for a CIDFontType0C descendant."""
        pdf, font_obj, cid_font = _make_pdf_with_cid_keyed_cff_font()

        export_dir = tmp_path / "fonts"
        _export(pdf, export_dir)

        sidecar_files = list(export_dir.glob("font_*.json"))
        assert len(sidecar_files) == 1
        sidecar = json.loads(sidecar_files[0].read_text())
        sidecar["width_sync_mode"] = "squash_font_vectors"
        assert "0001" in sidecar["mappings"], sidecar["mappings"].keys()
        sidecar["mappings"]["0001"]["width"]["pdf"] = 900.0
        sidecar_files[0].write_text(json.dumps(sidecar))

        res_import = import_fonts(pdf, [str(export_dir)])
        assert res_import.success

        from pdftl.fonts.cff_binary_utils import get_widths_from_cff

        patched_bytes = cid_font.FontDescriptor.FontFile3.read_bytes()
        reread_path = tmp_path / "reread_cid.cff"
        reread_path.write_bytes(patched_bytes)
        widths = get_widths_from_cff(reread_path, cid_to_gid_map="cff_native")

        assert widths.get("0001") == 900.0, (
            "squash_font_vectors mode did not actually touch the embedded CID-keyed CFF binary"
        )


# ---------------------------------------------------------------------------
# 2. Type 1 (/FontFile, .pfb): same dispatch bug, verified via a
#    kwargs-capture strategy since genuine Type 1 byte fixtures aren't
#    buildable in this codebase yet (see font_fixture_builder.py).
# ---------------------------------------------------------------------------


class TestEmbeddedFormatThreadingAcrossNonSfntFormats:
    """
    Directly checks whether import_widths's patch_font_metrics/
    squash_font_vectors code paths actually pass `embedded_format` through
    to patch_font_file_metrics/squash_font_file_vectors, for both
    non-sfnt formats pdftl claims to support ("cff" and "pfb").

    This is a unit-level test (calling import_widths directly, not the
    full export_fonts/import_fonts pipeline) specifically so it doesn't
    depend on being able to construct genuine bytes for the format under
    test -- the fake patch/squash functions never actually touch the
    bytes, they just record what they were called with.
    """

    @pytest.mark.parametrize("embedded_format", ["cff", "pfb"])
    def test_patch_font_metrics_passes_embedded_format(
        self, tmp_path, monkeypatch, embedded_format
    ):
        import pdftl.operations.helpers.font_import_helpers as fih

        font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type1")})
        font_entry = {
            "embedded_file": "font.bin",
            "descriptor_key": "FontFile" if embedded_format == "pfb" else "FontFile3",
            "base_font": "Test",
            "embedded_format": embedded_format,
        }
        (tmp_path / "font.bin").write_bytes(b"dummy")

        sidecar = tmp_path / "sidecar.json"
        sidecar.write_text(json.dumps({"width_sync_mode": "patch_font_metrics"}))
        font_entry["sidecar_json_file"] = "sidecar.json"

        mappings = {"41": {"width": {"pdf": 500.0}}}

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
            captured["embedded_format"] = embedded_format
            return None  # force manual fallback; we only care what was passed

        monkeypatch.setattr(fih, "patch_font_file_metrics", fake_patch)

        import_widths(font_obj, font_entry, tmp_path, mappings, pikepdf)

        assert captured.get("embedded_format") == embedded_format, (
            f"patch_font_metrics mode did not pass embedded_format="
            f"{embedded_format!r} through to patch_font_file_metrics -- "
            f"it dispatches via the sfnt/TTFont path regardless of the "
            f"font's real format, for every non-sfnt font type"
        )

    @pytest.mark.parametrize("embedded_format", ["cff", "pfb"])
    def test_squash_font_vectors_passes_embedded_format(
        self, tmp_path, monkeypatch, embedded_format
    ):
        import pdftl.operations.helpers.font_import_helpers as fih

        font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type1")})
        font_entry = {
            "embedded_file": "font.bin",
            "descriptor_key": "FontFile" if embedded_format == "pfb" else "FontFile3",
            "base_font": "Test",
            "embedded_format": embedded_format,
        }
        (tmp_path / "font.bin").write_bytes(b"dummy")

        sidecar = tmp_path / "sidecar.json"
        sidecar.write_text(json.dumps({"width_sync_mode": "squash_font_vectors"}))
        font_entry["sidecar_json_file"] = "sidecar.json"

        mappings = {"41": {"width": {"pdf": 500.0}}}

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
            captured["embedded_format"] = embedded_format
            return None

        monkeypatch.setattr(fih, "squash_font_file_vectors", fake_squash)

        import_widths(font_obj, font_entry, tmp_path, mappings, pikepdf)

        assert captured.get("embedded_format") == embedded_format, (
            f"squash_font_vectors mode did not pass embedded_format="
            f"{embedded_format!r} through to squash_font_file_vectors"
        )


# ---------------------------------------------------------------------------
# 3. auto mode: does get_font_widths_from_file also lack embedded_format
#    threading, the same way the patch/squash paths do?
# ---------------------------------------------------------------------------


class TestAutoModeEmbeddedFormatThreading:
    @pytest.mark.parametrize("embedded_format", ["cff", "pfb"])
    def test_auto_mode_passes_embedded_format_to_reader(
        self, tmp_path, monkeypatch, embedded_format
    ):
        """
        _auto_sync_widths_from_font is the function auto mode calls to
        read the (possibly user-edited) font binary's own widths back
        out. If it calls get_font_widths_from_file without
        embedded_format, it has exactly the same defect as the
        patch/squash call sites -- for a non-sfnt font, TTFont() will
        never successfully open the file, and the binary-only edit
        scenario (test_auto_mode_picks_up_binary_only_width_edit in
        test_font_fidelity_gaps.py) will always silently fail regardless
        of what fix lands for patch/squash mode.
        """
        import pdftl.operations.helpers.font_import_helpers as fih

        font_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type1")})
        font_entry = {
            "embedded_file": "font.bin",
            "base_font": "Test",
            "embedded_format": embedded_format,
            # binary_md5 deliberately absent/mismatched so the "file
            # changed" branch is taken rather than the unchanged-file
            # short-circuit.
            "binary_md5": "not-the-real-hash",
        }
        (tmp_path / "font.bin").write_bytes(b"dummy-changed-bytes")

        captured = {}

        def fake_get_widths(filepath, cid_to_gid_map=None, embedded_format=None, base_font=""):
            captured["embedded_format"] = embedded_format
            return {}

        monkeypatch.setattr(fih, "get_font_widths_from_file", fake_get_widths)

        fih._auto_sync_widths_from_font(font_obj, font_entry, tmp_path, pikepdf)

        assert captured.get("embedded_format") == embedded_format, (
            f"auto mode's font-binary read path does not pass "
            f"embedded_format={embedded_format!r} through to "
            f"get_font_widths_from_file -- the same dispatch gap as "
            f"patch/squash mode, on a third call site"
        )


# ---------------------------------------------------------------------------
# 4. Multi-FD CID-keyed CFF correctness, tested directly against
#    cff_binary_utils (bypassing the outer dispatch bug entirely) so this
#    isn't gated on that bug being fixed first.
# ---------------------------------------------------------------------------


def _build_cid_keyed_cff_multi_fd(cid_glyphs: dict, font_name: str = "TestMultiFD") -> bytes:
    """
    Builds a genuine CID-keyed CFF with TWO FDs carrying different
    nominalWidthX/defaultWidthX values, and assigns each glyph to a
    specific FD via FDSelect -- unlike every other CID-keyed fixture in
    this codebase, which uses exactly one FD, and therefore never
    exercises whether width patching correctly resolves nominalWidthX
    per-glyph via FDSelect rather than a single, shared value.

    CID 1 -> FD0 (nominalWidthX=0), CID 2 -> FD1 (nominalWidthX=200).

    CAVEAT: constructed via lower-level fontTools.cffLib API not
    otherwise used in this codebase's fixtures (CharStrings() with
    per-charstring `.private` overrides instead of a single shared
    Private). Not verified by execution -- if CFFFontSet.compile() or
    the CharStrings() constructor signature rejects this, that's the
    most likely place to need adjustment.
    """
    from fontTools.cffLib import (
        CFFFontSet,
        CharStrings,
        FDArrayIndex,
        FDSelect,
        GlobalSubrsIndex,
        PrivateDict,
        TopDict,
        TopDictIndex,
    )
    from fontTools.pens.t2CharStringPen import T2CharStringPen

    order = [".notdef"] + [f"cid{cid:05d}" for cid in sorted(cid_glyphs)]
    glyphs: dict = {".notdef": (0, [])}
    for cid, spec in cid_glyphs.items():
        glyphs[f"cid{cid:05d}"] = spec

    font_set = CFFFontSet()
    font_set.major = 1
    font_set.minor = 0
    font_set.fontNames = [font_name]
    font_set.topDictIndex = TopDictIndex()

    global_subrs = GlobalSubrsIndex()
    font_set.GlobalSubrs = global_subrs

    private_fd0 = PrivateDict()
    private_fd0.nominalWidthX = 0
    private_fd0.defaultWidthX = 0

    private_fd1 = PrivateDict()
    private_fd1.nominalWidthX = 200
    private_fd1.defaultWidthX = 200

    top_dict = TopDict()
    top_dict.charset = order
    top_dict.GlobalSubrs = global_subrs
    top_dict.FontName = font_name
    top_dict.FontMatrix = [0.001, 0, 0, 0.001, 0, 0]
    top_dict.ROS = ("Adobe", "Identity", 0)
    top_dict.CIDCount = len(order)

    fd_array = FDArrayIndex()
    fd0 = TopDict()
    fd0.Private = private_fd0
    fd0.FontName = f"{font_name}-FD0"
    fd1 = TopDict()
    fd1.Private = private_fd1
    fd1.FontName = f"{font_name}-FD1"
    fd_array.append(fd0)
    fd_array.append(fd1)
    top_dict.FDArray = fd_array

    # order = [".notdef", "cid00001", "cid00002"] -> gids [0, 1, 2].
    # .notdef and CID 1 use FD0; CID 2 uses FD1.
    fd_select = FDSelect()
    fd_select.format = 0
    fd_select.gidArray = [0, 0, 1]
    top_dict.FDSelect = fd_select

    charstrings = CharStrings(None, order, global_subrs, None, fd_select, fd_array)
    for gid, name in enumerate(order):
        width, commands = glyphs[name]
        private = private_fd0 if fd_select.gidArray[gid] == 0 else private_fd1
        pen = T2CharStringPen(width - private.nominalWidthX, {})
        for method_name, args in commands:
            getattr(pen, method_name)(*args)
        cs = pen.getCharString(private=private, globalSubrs=global_subrs)
        charstrings[name] = cs
    top_dict.CharStrings = charstrings

    font_set.topDictIndex.append(top_dict)

    buf = io.BytesIO()
    font_set.compile(buf, otFont=_MinimalOTFontStub())
    return buf.getvalue()


class TestMultiFdCidKeyedCffCorrectness:
    def test_patch_resolves_nominal_width_x_per_fd(self, tmp_path):
        """
        Patches CID 2, which lives on FD1 (nominalWidthX=200) -- distinct
        from FD0 (nominalWidthX=0), which every other CID-keyed CFF
        fixture in this codebase uses exclusively. If
        _patch_single_cff_width reads nominalWidthX from anywhere other
        than the specific glyph's own resolved FD (e.g. a single
        top-level or first-FD value), this will patch CID 2 to the wrong
        absolute width even though it "succeeds" without raising.

        Called directly against cff_binary_utils, bypassing
        import_fonts/patch_font_file_metrics entirely, so this is not
        gated on the embedded_format dispatch bug being fixed first.
        """
        from pdftl.fonts.cff_binary_utils import get_widths_from_cff, patch_cff_widths

        cff_bytes = _build_cid_keyed_cff_multi_fd({1: SQUARE_500, 2: TRIANGLE_300})
        cff_path = tmp_path / "multi_fd.cff"
        cff_path.write_bytes(cff_bytes)

        # Sanity check: the fixture actually reads back correctly before
        # patching, i.e. FD-aware reading itself works.
        original_widths = get_widths_from_cff(cff_path, cid_to_gid_map="cff_native")
        assert original_widths.get("0001") == 500.0
        assert original_widths.get("0002") == 300.0

        patched_bytes = patch_cff_widths(cff_path, {"0002": 950.0}, cid_to_gid_map="cff_native")
        assert patched_bytes is not None

        patched_path = tmp_path / "multi_fd_patched.cff"
        patched_path.write_bytes(patched_bytes)
        reread = get_widths_from_cff(patched_path, cid_to_gid_map="cff_native")

        assert reread.get("0002") == 950.0, (
            "patching CID 2 (on FD1, nominalWidthX=200) did not produce "
            "the correct absolute width -- nominalWidthX resolution is "
            "not correctly per-FD"
        )
        assert reread.get("0001") == 500.0, (
            "patching CID 2 unexpectedly altered CID 1 (on FD0) as a side effect"
        )

    def test_no_top_level_private_does_not_crash(self, tmp_path):
        """
        The multi-FD fixture above has no top-level Private at all (only
        per-FD Private dicts) -- confirms patch_cff_widths does not raise
        AttributeError on `topdict.Private.nominalWidthX` for a font
        shaped exactly like real CID-keyed CFF-producing tools emit.
        """
        from pdftl.fonts.cff_binary_utils import _decompile_bare_cff, patch_cff_widths

        cff_bytes = _build_cid_keyed_cff_multi_fd({1: SQUARE_500, 2: TRIANGLE_300})
        cff_path = tmp_path / "multi_fd.cff"
        cff_path.write_bytes(cff_bytes)

        _, topdict = _decompile_bare_cff(cff_bytes)
        assert "Private" not in topdict.rawDict, (
            "test setup assumption violated -- top-level Private is "
            "present; adjust _build_cid_keyed_cff_multi_fd"
        )

        # Must not raise.
        patch_cff_widths(cff_path, {"0001": 700.0}, cid_to_gid_map="cff_native")


# ---------------------------------------------------------------------------
# 5. Type 3 inline-image extraction: BI...ID...EI regex misparsing a
#    binary payload containing a whitespace-bounded literal "ID"/"EI".
# ---------------------------------------------------------------------------


class TestType3InlineImageRegexFragility:
    def test_binary_payload_containing_literal_id_marker_is_not_truncated(self, tmp_path):
        """
        _process_inline_images_on_export scans a Type 3 glyph's raw
        content stream bytes with a non-greedy regex,
        `BI\\s+(.*?)\\s+ID\\s+(.*?)\\s+EI`, to locate inline images. Both
        capture groups are non-greedy, so the regex stops at the FIRST
        `\\s+ID\\s+` (ending the dict group) and the FIRST subsequent
        `\\s+EI` (ending the data group) it finds.

        If the raw pixel data itself happens to contain a
        whitespace-bounded byte sequence spelling "ID" or "EI" --
        entirely plausible for 1-bit or 8-bit bitmap data -- the regex
        will treat that coincidental byte pattern as the real delimiter
        and truncate the extracted image data well before the genuine
        terminator, silently corrupting the round-trip.

        This test builds pixel data that deliberately embeds a
        whitespace + "EI" byte sequence partway through, well before the
        real terminating `EI`, and confirms the extracted image is NOT
        truncated at that false positive.
        """
        from pdftl.operations.helpers.type3_extraction_helpers import (
            _process_inline_images_on_export,
        )

        width, height, bpc = 4, 4, 8
        expected_len = width * height  # 16 raw gray bytes, no filter

        # Deliberately embed a literal b" EI" (space + 'E' + 'I') inside
        # the raw pixel payload, 6 bytes before the genuine terminator.
        # A correct parser must still read the FULL 16 bytes of pixel
        # data and only stop at the real trailing "\nEI" below.
        payload = bytearray(b"\x10" * expected_len)
        false_positive_offset = expected_len - 6
        payload[false_positive_offset : false_positive_offset + 3] = b" EI"
        payload = bytes(payload)
        assert len(payload) == expected_len

        stream_bytes = (
            f"BI /W {width} /H {height} /BPC {bpc} /CS /DeviceGray ID\n".encode("latin-1")
            + payload
            + b"\nEI"
        )

        img_registry: dict = {}
        bitmaps_dir = tmp_path / "bitmaps"
        _process_inline_images_on_export(
            stream_bytes, "1_0_TestFont", "A", bitmaps_dir, img_registry
        )

        assert "A_0" in img_registry, "no image was extracted at all"
        tiff_path = bitmaps_dir / img_registry["A_0"]["filename"].split("/")[-1]
        assert tiff_path.is_file()

        from PIL import Image

        img = Image.open(tiff_path)
        extracted_bytes = img.tobytes()

        assert len(extracted_bytes) == expected_len, (
            f"extracted image data was truncated to {len(extracted_bytes)} "
            f"bytes instead of the expected {expected_len} -- the "
            f"BI...ID...EI regex stopped early at a coincidental "
            f"whitespace+'EI' byte sequence inside the pixel data rather "
            f"than the genuine terminator"
        )
        assert extracted_bytes == payload, (
            "extracted image bytes do not match the original payload -- "
            "round-trip fidelity lost due to the regex misparse"
        )
