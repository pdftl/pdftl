# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/test_font_fidelity_gaps.py

"""
High-level fidelity tests for export_fonts -> user edit -> import_fonts,
targeting known gaps in bare CFF (Type1C / CIDFontType0C) width sync and
the unused encoding_cmap import path.

These are written against the *real* operations end-to-end (no mocking of
fontTools or pdftl internals) so they exercise the actual key-space and
FDArray/Private issues rather than a simplified stand-in.

IMPORTANT: export_fonts() itself does NOT write manifest.json to disk --
that only happens via export_fonts_cli_hook, which the CLI wires up as a
post-operation hook. Every test here must call export_fonts_cli_hook
explicitly after export_fonts(), via the _export() helper below, or
import_fonts() will fail immediately with "Manifest file not found"
before ever reaching the code path under test.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pikepdf

from pdftl.operations.export_import_fonts import (
    export_fonts,
    export_fonts_cli_hook,
    import_fonts,
)

sys.path.insert(0, str(Path(__file__).parent.parent / "fonts" / "fixtures"))
from font_fixture_builder import (  # noqa: E402
    SQUARE_500,
    TRIANGLE_300,
    _MinimalOTFontStub,
    _build_charstrings,
    build_bare_cff_bytes,
    build_cid_keyed_cff_bytes,
)


def _export(pdf, export_dir) -> None:
    """
    Runs export_fonts AND actually writes manifest.json to disk, mirroring
    how the CLI invokes it. export_fonts() alone only returns the manifest
    as in-memory `data` -- it does not write manifest.json to
    `export_dir`. That write only happens via export_fonts_cli_hook
    (see export_fonts_cli_hook in pdftl/operations/export_import_fonts.py).
    Every test below must go through this helper, not export_fonts()
    directly, or import_fonts() will fail before reaching the code under
    test.
    """
    res = export_fonts(pdf, [str(export_dir)])
    assert res.success
    export_fonts_cli_hook(res, None, None)


def _build_cid_keyed_cff_without_global_private(
    cid_glyphs: dict, font_name: str = "TestCIDFontNoGlobalPrivate"
) -> bytes:
    """
    Like font_fixture_builder.build_cid_keyed_cff_bytes, but deliberately
    omits topdict.Private -- matching what real CID-keyed CFF-producing
    tools (FontForge, real Adobe-originated CJK fonts, etc.) actually
    emit: nominalWidthX/defaultWidthX live ONLY on each FD's own Private
    dict, resolved via FDSelect, never on the Top DICT itself.

    font_fixture_builder.build_cid_keyed_cff_bytes sets
    `top_dict.Private = private` in addition to assigning that same
    PrivateDict instance into the FDArray's single FD -- which masks the
    real-world case this fixture exists to reproduce, since
    `topdict.Private.nominalWidthX` then resolves fine. This builder
    leaves `top_dict.Private` unset entirely.
    """
    from fontTools.cffLib import (
        CFFFontSet,
        FDArrayIndex,
        FDSelect,
        GlobalSubrsIndex,
        PrivateDict,
        TopDict,
        TopDictIndex,
    )

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

    # This Private belongs ONLY to the FD -- it is never assigned to
    # top_dict.Private, unlike font_fixture_builder's version.
    fd_private = PrivateDict()

    top_dict = TopDict()
    top_dict.charset = order
    top_dict.GlobalSubrs = global_subrs
    top_dict.FontName = font_name
    top_dict.FontMatrix = [0.001, 0, 0, 0.001, 0, 0]
    top_dict.ROS = ("Adobe", "Identity", 0)
    top_dict.CIDCount = len(order)
    # Deliberately NOT setting top_dict.Private here.

    fd_array = FDArrayIndex()
    fd = TopDict()
    fd.Private = fd_private
    fd.FontName = f"{font_name}-FD0"
    fd_array.append(fd)
    top_dict.FDArray = fd_array

    fd_select = FDSelect()
    fd_select.format = 0
    fd_select.gidArray = [0] * len(order)
    top_dict.FDSelect = fd_select

    top_dict.CharStrings = _build_charstrings(
        order, glyphs, fd_private, global_subrs, fd_select, fd_array
    )

    font_set.topDictIndex.append(top_dict)

    buf = io.BytesIO()
    font_set.compile(buf, otFont=_MinimalOTFontStub())
    return buf.getvalue()


def _make_pdf_with_type1c_font(cff_bytes: bytes):
    """A Simple font (/Type1) whose /FontFile3 /Type1C program has glyphs
    'A' (width 500) and 'B' (width 300), reachable via /Differences at
    codes 0x41 and 0x42 respectively -- mirroring a real Distiller/LaTeX
    PDF where a bare CFF Simple font is embedded."""
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
    """A Type0/CIDFontType0 font backed by a genuine, FDArray-based
    CID-keyed CFF program (CID 1 -> square/500, CID 2 -> triangle/300),
    with Identity /CIDToGIDMap semantics for the CID space.

    Pass `cff_bytes` to control which CFF fixture is embedded; defaults to
    font_fixture_builder.build_cid_keyed_cff_bytes if omitted.
    """
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
            # Correct /W sequence form is [c_first, [w1, w2, ...]] (two
            # elements) -- NOT [c_first, c_last, w] (three elements), which
            # is the *range* form for a single repeated width. Getting this
            # wrong makes _extract_range_widths try float() on a
            # pikepdf.Array, silently swallowed by its own except clause,
            # producing zero width entries.
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
# Gap 1: bare CFF Simple font (Type1C) -- key-space mismatch breaks
# auto/patch_font_metrics/squash_font_vectors width sync entirely.
# ---------------------------------------------------------------------------


class TestBareCffSimpleFontWidthFidelity:
    def test_auto_mode_picks_up_binary_only_width_edit(self, tmp_path):
        """
        If the user edits ONLY the exported .cff binary (e.g. via
        FontForge) and leaves the JSON sidecar's width_sync_mode at the
        default 'auto', import_fonts should read the new width back out of
        the font program and write it into /Widths -- exactly like it does
        for TrueType. Today, get_widths_from_cff returns glyph-name keys
        ("A") that update_font_widths can't parse as hex, so the edit is
        silently dropped and /Widths stays unchanged.
        """
        cff_bytes = build_bare_cff_bytes({"A": SQUARE_500, "B": TRIANGLE_300})
        pdf, font_obj = _make_pdf_with_type1c_font(cff_bytes)

        export_dir = tmp_path / "fonts"
        _export(pdf, export_dir)

        cff_files = list(export_dir.glob("font_*.cff"))
        assert len(cff_files) == 1

        # Simulate an external editor changing glyph "A"'s width to 650.
        edited_bytes = build_bare_cff_bytes({"A": (650, SQUARE_500[1]), "B": TRIANGLE_300})
        cff_files[0].write_bytes(edited_bytes)

        res_import = import_fonts(pdf, [str(export_dir)])
        assert res_import.success

        assert float(font_obj.Widths[0]) == 650.0, (
            "auto width sync did not pick up the binary-only CFF edit -- "
            "get_widths_from_cff's glyph-name keys are being silently "
            "dropped by update_font_widths's hex-key normalization"
        )

    def test_patch_font_metrics_mode_actually_patches_the_binary(self, tmp_path):
        """
        With width_sync_mode='patch_font_metrics' and a manual width.pdf
        edit in the sidecar, the embedded CFF binary itself should be
        rewritten so the font program's own width matches /Widths. Today
        patch_font_file_metrics never receives `differences`/`base_encoding`
        for the 'cff' dispatch branch, and pdf_widths (hex codes) never
        matches the CFF's own glyph-name keys, so the binary is left
        untouched even though /Widths is updated via the manual fallback.
        """
        cff_bytes = build_bare_cff_bytes({"A": SQUARE_500, "B": TRIANGLE_300})
        pdf, font_obj = _make_pdf_with_type1c_font(cff_bytes)

        export_dir = tmp_path / "fonts"
        _export(pdf, export_dir)

        sidecar_files = list(export_dir.glob("font_*.json"))
        assert len(sidecar_files) == 1
        sidecar = json.loads(sidecar_files[0].read_text())
        sidecar["width_sync_mode"] = "patch_font_metrics"
        # Code 0x41 == 'A', requesting a new PDF width of 777.
        assert "41" in sidecar["mappings"], sidecar["mappings"].keys()
        sidecar["mappings"]["41"]["width"]["pdf"] = 777.0
        sidecar_files[0].write_text(json.dumps(sidecar))

        res_import = import_fonts(pdf, [str(export_dir)])
        assert res_import.success
        assert float(font_obj.Widths[0]) == 777.0  # /Widths itself is fine either way

        # The real check: did the embedded font binary get patched to match?
        from pdftl.fonts.cff_binary_utils import get_widths_from_cff

        descriptor = font_obj.FontDescriptor
        patched_stream_bytes = descriptor.FontFile3.read_bytes()
        reread_path = tmp_path / "reread.cff"
        reread_path.write_bytes(patched_stream_bytes)
        widths = get_widths_from_cff(reread_path)

        assert widths.get("A") == 777.0, (
            "patch_font_metrics mode did not actually patch the embedded "
            "CFF binary's own glyph width -- /Widths and the font program "
            "now disagree, which is exactly the fidelity failure this "
            "mode exists to prevent"
        )


# ---------------------------------------------------------------------------
# Gap 2: CID-keyed CFF (CIDFontType0C) with a real FDArray/Private
# structure -- patch_font_metrics must not crash.
# ---------------------------------------------------------------------------


class TestCidKeyedCffWithFdArrayDoesNotCrash:
    def test_patch_font_metrics_on_real_fdarray_cff_does_not_raise(self, tmp_path):
        """
        The default build_cid_keyed_cff_bytes fixture sets a global
        top_dict.Private in addition to the per-FD Private, which masks a
        real bug: an ordinary CID-keyed CFF (built the way real
        font-authoring tools actually produce one) has NO top-level
        Private dict at all -- every glyph's nominalWidthX lives on its
        FD's own Private via FDSelect.
        _patch_single_cff_width's unguarded
        `topdict.Private.nominalWidthX` should not raise AttributeError
        for such a font; at minimum it must degrade to the documented
        "skip this glyph, don't crash the whole import" contract that
        every other patch path in this codebase honors.

        This test uses _build_cid_keyed_cff_without_global_private (see
        above), which omits top_dict.Private entirely, to reproduce that
        real-world shape rather than the masked fixture.
        """
        cff_bytes = _build_cid_keyed_cff_without_global_private({1: SQUARE_500, 2: TRIANGLE_300})
        pdf, font_obj, cid_font = _make_pdf_with_cid_keyed_cff_font(cff_bytes=cff_bytes)

        # Confirm the fixture actually reproduces the real-world shape
        # before trusting the rest of the test.
        from pdftl.fonts.cff_binary_utils import _decompile_bare_cff

        raw_cff_bytes = cid_font.FontDescriptor.FontFile3.read_bytes()
        _, topdict = _decompile_bare_cff(raw_cff_bytes)
        # fontTools' TopDict.__getattr__ RAISES AttributeError for an unset
        # attribute rather than returning None -- `topdict.Private is None`
        # itself raises here, so it can't be used as the guard. Checking
        # membership in the underlying rawDict is the non-raising way to
        # confirm no top-level Private was ever set.
        has_top_level_private = "Private" in topdict.rawDict
        assert not has_top_level_private, (
            "test setup assumption violated -- fixture unexpectedly "
            "carries a usable top-level Private dict; adjust "
            "_build_cid_keyed_cff_without_global_private"
        )

        export_dir = tmp_path / "fonts"
        _export(pdf, export_dir)

        sidecar_files = list(export_dir.glob("font_*.json"))
        assert len(sidecar_files) == 1
        sidecar = json.loads(sidecar_files[0].read_text())
        sidecar["width_sync_mode"] = "patch_font_metrics"
        # CID 1 -> hex "0001"
        assert "0001" in sidecar["mappings"], sidecar["mappings"].keys()
        sidecar["mappings"]["0001"]["width"]["pdf"] = 900.0
        sidecar_files[0].write_text(json.dumps(sidecar))

        # This must not raise AttributeError: 'NoneType' object has no
        # attribute 'nominalWidthX' (or similar) -- whether the exception
        # would come from _patch_single_cff_width itself or be swallowed
        # somewhere upstream, import_fonts as a whole must not blow up.
        res_import = import_fonts(pdf, [str(export_dir)])
        assert res_import.success

        # Not raising isn't the same as fidelity: confirm the embedded CFF
        # binary's own CID 1 width was actually updated to 900, not just
        # silently left alone by a graceful-but-inert fallback.
        from pdftl.fonts.cff_binary_utils import get_widths_from_cff

        patched_stream_bytes = cid_font.FontDescriptor.FontFile3.read_bytes()
        reread_path = tmp_path / "reread_cid.cff"
        reread_path.write_bytes(patched_stream_bytes)
        widths = get_widths_from_cff(reread_path, cid_to_gid_map="cff_native")

        assert widths.get("0001") == 900.0, (
            "import_fonts did not raise, but the embedded CID-keyed CFF "
            "binary's own width for CID 1 was not actually patched to "
            "900.0 -- patch_font_metrics mode is silently a no-op here "
            "rather than achieving byte-for-byte fidelity"
        )


# ---------------------------------------------------------------------------
# Gap 3: encoding_cmap sidecar edits (Identity-H <-> Identity-V) are
# silently ignored because import_encoding_cmap is never called.
# ---------------------------------------------------------------------------


class TestEncodingCmapEditIsApplied:
    def test_identity_h_to_identity_v_edit_round_trips(self, tmp_path):
        """
        The manifest records `encoding_cmap: "Identity-H"` for a Type0
        font using the predefined Identity-H CMap, and
        import_encoding_cmap exists specifically to let a user flip this
        to Identity-V. But _process_single_font_import never calls it, so
        editing the manifest has no effect on the resulting PDF.
        """
        pdf, font_obj, cid_font = _make_pdf_with_cid_keyed_cff_font()
        assert font_obj.Encoding == pikepdf.Name("/Identity-H")

        export_dir = tmp_path / "fonts"
        _export(pdf, export_dir)

        manifest_path = export_dir / "manifest.json"
        assert manifest_path.is_file()
        manifest = json.loads(manifest_path.read_text())
        font_key = next(iter(manifest["fonts"].keys()))
        assert manifest["fonts"][font_key]["encoding_cmap"] == "Identity-H"

        manifest["fonts"][font_key]["encoding_cmap"] = "Identity-V"
        manifest_path.write_text(json.dumps(manifest))

        res_import = import_fonts(pdf, [str(export_dir)])
        assert res_import.success

        assert font_obj.Encoding == pikepdf.Name("/Identity-V"), (
            "editing 'encoding_cmap' in the manifest from Identity-H to "
            "Identity-V had no effect -- import_encoding_cmap is defined "
            "and unit-tested but never invoked from "
            "_process_single_font_import"
        )
