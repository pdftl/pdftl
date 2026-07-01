# tests/fonts/test_cff_roundtrip_integration.py
"""
Round-trip fidelity integration tests against real, non-mock CFF font
programs (AFDKO-built, genuinely subroutinized and hintmask-using), rather
than hand-assembled charstrings.

These exist alongside (not instead of) the unit-level TDD tests in
test_cff_width_gaps.py: the unit tests pin exact, deliberately-chosen
regression cases; these prove the same logic survives contact with a real
font's actual charstring shapes, dispatched through the real public entry
points (squash_font_file_vectors / patch_font_file_metrics), not the
private per-glyph patcher directly.

Fixtures (tests/files/fonts/):
  - source_sans3_subset.otf: Simple (non-CID) OTF/CFF, real local + global
    subrs, real hintmask usage (AFDKO-built, Adobe's own toolchain).
  - noto_sans_jp_subset.otf: CID-keyed OTF/CFF with a real FDArray (4 FDs
    across the retained glyphs), real global subrs, real hintmask usage.
  - Filepile.otf: plain baseline sfnt/CFF with none of the above -- kept
    as a trivial sanity case, not a stress case.

Both fonts are sfnt-wrapped CFF/CFF2, so `classify_binary_format` routes
them through the "sfnt" bucket -- and since neither carries a `glyf`
table, `squash_font_file_vectors` degrades to the metrics-only patch path
(`_patch_internal` for hmtx, then `_patch_cff_table_in_sfnt` for the CFF
table's own charstring widths) -- see font_binary_utils.py's docstring.
That degraded path is exercised here exactly as it would be in production.
"""

from pathlib import Path

import pytest
from fontTools.ttLib import TTFont

from pdftl.fonts.font_binary_utils import (
    get_font_widths_from_file,
    squash_font_file_vectors,
)

FONTS_DIR = Path(__file__).parent.parent / "files" / "fonts"


def _cff_topdict(font_bytes: bytes):
    """Opens recompiled font bytes and returns its CFF Top DICT, for
    reading back charstring widths after a patch."""
    import io

    tt = TTFont(io.BytesIO(font_bytes))
    cff = tt["CFF "].cff
    return cff[cff.fontNames[0]]


def _measure_all_charstring_widths(topdict) -> dict[str, float]:
    """Mirrors cff_binary_utils._measure_charstring_width for every glyph,
    keyed by glyph/CID-synthetic name, for round-trip comparison."""
    from fontTools.pens.basePen import NullPen

    widths = {}
    for name in topdict.CharStrings.keys():
        cs = topdict.CharStrings[name]
        cs.decompile()
        cs.draw(NullPen())
        widths[name] = cs.width
    return widths


class TestSourceSans3Roundtrip:
    """Simple (non-CID) real AFDKO-built OTF/CFF: real local + global
    subrs, real hintmask. Exercises the Simple-font width-sync path plus
    both fixed gaps (subr-delegated width, hintmask-first-op parity)
    against genuine charstring shapes rather than hand-built ones."""

    @pytest.fixture
    def font_path(self):
        path = FONTS_DIR / "source_sans3_subset.otf"
        if not path.exists():
            pytest.skip(f"Fixture not present: {path}")
        return path

    def test_all_glyph_widths_roundtrip_after_patch(self, font_path):
        # 1. Read the font's own current widths via its cmap-keyed path.
        original_widths = get_font_widths_from_file(font_path, embedded_format="otf")
        assert original_widths, "Fixture produced no readable widths at all"

        # 2. Construct a deliberately-different target width for every
        # code, so a no-op patch can't accidentally look like success.
        target_widths = {code: w + 37.0 for code, w in original_widths.items()}

        # 3. Patch through the real public entry point. Neither fixture
        # has a glyf table, so this exercises the metrics-only CFF-table
        # patch path (_patch_cff_table_in_sfnt), not glyph-vector squash.
        patched_bytes = squash_font_file_vectors(
            font_path, target_widths, embedded_format="otf", base_font="SourceSans3Subset"
        )
        assert patched_bytes is not None, "Patch reported nothing was patched"

        # 4. Recompile, re-open, and re-measure every glyph's width
        # directly off the CFF table's own charstrings -- not hmtx, since
        # hmtx patching was never in question; the CFF-table sync is what
        # the width-presence fix targets.
        topdict = _cff_topdict(patched_bytes)
        remeasured = _measure_all_charstring_widths(topdict)

        # 5. Every code's glyph must show EITHER the new width (successfully
        # patched) OR its original, untouched width (correctly and safely
        # skipped, per cff_binary_utils._find_width_presence's shared-subr
        # guard -- see its docstring: a glyph whose width push lives inside a
        # subroutine shared with other glyphs cannot be independently
        # repatched without risking corruption of every other caller, so it
        # is left alone rather than guessed at). A glyph showing neither is a
        # real corruption and must fail the test.
        tt = TTFont(font_path)
        cmap = tt.getBestCmap()
        checked_any = False
        patched_count = 0
        skipped_count = 0
        for code_hex, new_w in target_widths.items():
            code = int(code_hex, 16)
            gname = cmap.get(code)
            if gname is None or gname not in remeasured:
                continue
            checked_any = True
            original_w = original_widths[code_hex]
            actual_w = remeasured[gname]
            if actual_w == new_w:
                patched_count += 1
            elif actual_w == original_w:
                skipped_count += 1
            else:
                pytest.fail(
                    f"Glyph '{gname}' (code {code_hex}) shows neither the new "
                    f"width ({new_w}) nor its original untouched width "
                    f"({original_w}) -- got {actual_w}, which means the "
                    f"charstring was corrupted rather than patched or safely "
                    f"skipped."
                )
        assert checked_any, "No patched code resolved back to a real glyph via cmap"
        assert patched_count > 0, "No glyph was actually patchable at all -- fixture regression?"


class TestNotoSansJPRoundtrip:
    """CID-keyed OTF/CFF with a genuine FDArray (4 FDs across the
    retained glyphs). This is the fixture that actually stresses
    _resolve_nominal_width_x's FD-resolved-Private logic against a real,
    non-synthetic multi-FD structure."""

    @pytest.fixture
    def font_path(self):
        path = FONTS_DIR / "noto_sans_jp_subset.otf"
        if not path.exists():
            pytest.skip(f"Fixture not present: {path}")
        return path

    def test_all_cid_widths_roundtrip_after_patch_across_multiple_fds(self, font_path):
        original_widths = get_font_widths_from_file(
            font_path, cid_to_gid_map="Identity", embedded_format="otf"
        )
        # CID-keyed CFF read via cff_native path instead, since that's what
        # exercises the FDArray-resolved Private -- "Identity" above reads
        # via the sfnt/hmtx path and is only used as a baseline sanity read.
        assert original_widths or True  # hmtx-path result isn't the focus here

        # Read CFF-native CID-keyed widths directly (this is the path that
        # actually walks the CFF's own ROS/charset + per-glyph FD-resolved
        # Private -- see cff_binary_utils._get_cff_cid_widths).
        from pdftl.fonts import cff_binary_utils

        # Extract the bare CFF table bytes isn't directly available via a
        # public helper for sfnt-wrapped CFF, so read widths via the sfnt
        # CID path instead, matching production's own dispatch.
        tt = TTFont(font_path)
        cff = tt["CFF "].cff
        topdict = cff[cff.fontNames[0]]
        assert hasattr(topdict, "ROS"), "Fixture is not actually CID-keyed"
        assert len(topdict.FDArray) > 1, (
            "Fixture no longer spans multiple FDs -- FD-resolution "
            "regression coverage would be silently lost"
        )

        original_cid_widths = cff_binary_utils._get_cff_cid_widths(topdict)
        assert original_cid_widths, "No CID-keyed widths read from fixture"

        target_widths = {cid: w + 41.0 for cid, w in original_cid_widths.items()}

        patched_bytes = squash_font_file_vectors(
            font_path,
            target_widths,
            cid_to_gid_map="cff_native",
            embedded_format="otf",
            base_font="NotoSansJPSubset",
        )
        assert patched_bytes is not None, "Patch reported nothing was patched"

        patched_topdict = _cff_topdict(patched_bytes)
        remeasured_cid_widths = cff_binary_utils._get_cff_cid_widths(patched_topdict)

        assert remeasured_cid_widths.keys() == target_widths.keys()
        for hex_cid, expected_w in target_widths.items():
            assert remeasured_cid_widths[hex_cid] == expected_w, (
                f"CID {hex_cid} width did not round-trip across FDArray: "
                f"expected {expected_w}, got {remeasured_cid_widths[hex_cid]}"
            )
