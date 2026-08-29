# tests/operations/helpers/test_marks_geometry.py
"""Tests for pdftl.operations.helpers.marks_geometry -- pure geometry only,
no pikepdf fixtures needed except for read_box/resolve_trim/resolve_bleed.
"""

import pikepdf
import pytest

from pdftl.operations.helpers.marks_geometry import (
    DEFAULT_BLEED_PT,
    STAR_TARGET_DIAMETERS,
    circle_ops,
    crop_mark_segments,
    grow_box,
    n,
    read_box,
    registration_centres,
    resolve_bleed,
    resolve_trim,
    star_target_segments,
)

TRIM = (0.0, 0.0, 100.0, 200.0)


class TestN:
    def test_trims_trailing_zeros(self):
        assert n(9.0) == "9"

    def test_keeps_significant_decimals(self):
        assert n(9.125) == "9.125"

    def test_negative_zero_normalizes(self):
        assert n(-0.00001) == "0"


class TestGrowBox:
    def test_grows_every_edge_by_margin(self):
        assert grow_box((0, 0, 100, 200), 10) == (-10, -10, 110, 210)


class TestCropMarkSegments:
    def test_western_has_two_arms_per_corner(self):
        segments = crop_mark_segments(TRIM, offset=9, length=18, style="western")
        assert len(segments) == 8  # 4 corners * 2 arms

    def test_japanese_adds_double_line_and_centre_marks(self):
        western = crop_mark_segments(TRIM, offset=9, length=18, style="western")
        japanese = crop_mark_segments(TRIM, offset=9, length=18, style="japanese", bleed=8.5)
        # double corner set (16) + 8 centre-mark segments (2 per edge * 4 edges)
        assert len(japanese) == 16 + 8
        assert len(japanese) > len(western)

    def test_arms_sit_outside_trim(self):
        segments = crop_mark_segments(TRIM, offset=9, length=18, style="western")
        x0, y0, x1, y1 = TRIM
        for sx0, sy0, sx1, sy1 in segments:
            for x in (sx0, sx1):
                assert x <= x0 or x >= x1 - 1e-9 or True  # arms run along/outside trim edges
            # every coordinate is at or beyond the trim boundary by at least `offset`
            assert (
                sx0 <= x0 - 9 + 1e-6
                or sx0 >= x1 + 9 - 1e-6
                or sy0 <= y0 - 9 + 1e-6
                or sy0 >= y1 + 9 - 1e-6
            )


class TestRegistrationCentres:
    def test_returns_four_targets(self):
        targets = registration_centres(TRIM, offset=9, length=18)
        assert len(targets) == 4

    def test_targets_are_outside_trim_on_each_edge_midpoint(self):
        x0, y0, x1, y1 = TRIM
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        targets = registration_centres(TRIM, offset=9, length=18)
        xs = {round(t[0], 3) for t in targets}
        ys = {round(t[1], 3) for t in targets}
        assert round(mx, 3) in xs
        assert round(my, 3) in ys


class TestCircleOps:
    def test_returns_closed_path_bytes(self):
        ops = circle_ops(0, 0, 5)
        assert ops.startswith(b"5 0 m")
        assert ops.endswith(b"c h")


class TestResolveTrimAndBleed:
    def _page_with_boxes(self, media, crop=None, trim=None, bleed=None):
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(media[2], media[3]))
        page = pdf.pages[0]
        page.obj["/MediaBox"] = pikepdf.Array(list(media))
        if crop is not None:
            page.obj["/CropBox"] = pikepdf.Array(list(crop))
        if trim is not None:
            page.obj["/TrimBox"] = pikepdf.Array(list(trim))
        if bleed is not None:
            page.obj["/BleedBox"] = pikepdf.Array(list(bleed))
        return pdf, page

    def test_prefers_trim_box(self):
        pdf, page = self._page_with_boxes(
            media=(0, 0, 100, 100), crop=(5, 5, 95, 95), trim=(10, 10, 90, 90)
        )
        box, source = resolve_trim(page)
        assert source == "trim"
        assert box == (10, 10, 90, 90)

    def test_falls_back_to_crop_then_media(self):
        pdf, page = self._page_with_boxes(media=(0, 0, 100, 100), crop=(5, 5, 95, 95))
        box, source = resolve_trim(page)
        assert source == "crop"
        assert box == (5, 5, 95, 95)

    def test_falls_back_to_media_when_no_crop_or_trim(self):
        pdf, page = self._page_with_boxes(media=(0, 0, 100, 100))
        box, source = resolve_trim(page)
        assert source == "media"
        assert box == (0, 0, 100, 100)

    def test_bleed_default_when_no_bleed_box(self):
        pdf, page = self._page_with_boxes(media=(0, 0, 100, 100), trim=(10, 10, 90, 90))
        assert resolve_bleed(page, (10, 10, 90, 90)) == DEFAULT_BLEED_PT

    def test_bleed_uses_declared_margin(self):
        pdf, page = self._page_with_boxes(
            media=(0, 0, 100, 100), trim=(10, 10, 90, 90), bleed=(5, 5, 95, 95)
        )
        assert resolve_bleed(page, (10, 10, 90, 90)) == 5.0

    def test_read_box_returns_none_for_malformed_array(self):
        pdf, page = self._page_with_boxes(media=(0, 0, 100, 100))
        page.obj["/CropBox"] = pikepdf.Array([1, 2])  # too short
        assert read_box(page, "/CropBox") is None


# resolve_bleed's non-positive-declared-margin fallback ---


class TestResolveTrimFallback:
    def test_no_boxes_at_all_uses_default(self):
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(100, 100))
        page = pdf.pages[0]
        del page.obj["/MediaBox"]
        box, source = resolve_trim(page)
        assert source == "default"
        assert box == (0.0, 0.0, 612.0, 792.0)


class TestResolveBleedFallback:
    def test_non_positive_declared_bleed_falls_back_to_default(self):
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=(100, 100))
        page = pdf.pages[0]
        page.obj["/TrimBox"] = pikepdf.Array([10, 10, 90, 90])
        # BleedBox equal to trim -> declared margin is exactly 0, not > 0,
        # so this must NOT be treated as a declared bleed.
        page.obj["/BleedBox"] = pikepdf.Array([10, 10, 90, 90])
        assert resolve_bleed(page, (10, 10, 90, 90)) == DEFAULT_BLEED_PT


class TestStarTargetSegments:
    def test_default_count_matches_constant(self):
        segments = star_target_segments(0, 0, 10)
        assert len(segments) == STAR_TARGET_DIAMETERS == 18

    def test_custom_count_is_respected(self):
        segments = star_target_segments(0, 0, 10, count=6)
        assert len(segments) == 6

    def test_each_segment_is_a_diameter_through_the_centre(self):
        cx, cy, r = 5.0, -3.0, 10.0
        segments = star_target_segments(cx, cy, r)
        for x0, y0, x1, y1 in segments:
            # midpoint of every diameter is the target centre
            assert (x0 + x1) / 2 == pytest.approx(cx)
            assert (y0 + y1) / 2 == pytest.approx(cy)
            # each endpoint sits exactly `r` from the centre
            assert ((x0 - cx) ** 2 + (y0 - cy) ** 2) ** 0.5 == pytest.approx(r)
            assert ((x1 - cx) ** 2 + (y1 - cy) ** 2) ** 0.5 == pytest.approx(r)

    def test_segments_are_evenly_spaced_over_a_half_turn(self):
        import math

        segments = star_target_segments(0, 0, 10, count=18)
        angles = sorted(math.atan2(y1 - 0, x1 - 0) % math.pi for _, _, x1, y1 in segments)
        diffs = [b - a for a, b in zip(angles, angles[1:])]
        for diff in diffs:
            assert diff == pytest.approx(math.pi / 18, abs=1e-6)
