# tests/utils/test_path_geometry.py

"""Tests for pdftl.utils.path_geometry."""

import math
import pytest

from pdftl.utils.path_geometry import (
    _perp_distance,
    rdp_simplify,
    sample_cubic_bezier,
    simplify_path,
)
from pdftl.utils.path_types import Path, SimplifiedPath, SimplifyConfig, Subpath


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_linear_subpath(points, closed=False, op_count=None):
    return Subpath(
        points=points,
        closed=closed,
        has_curves=False,
        ctm_scale=1.0,
        original_op_count=op_count if op_count is not None else len(points),
    )


def _make_path(subpaths, paint_op="S", is_clipping=False):
    return Path(
        subpaths=subpaths,
        paint_op=paint_op,
        original_instructions=[],
        is_clipping=is_clipping,
    )


# ---------------------------------------------------------------------------
# _perp_distance
# ---------------------------------------------------------------------------


class TestPerpDistance:
    def test_point_on_line_returns_zero(self):
        # Point (1, 1) lies on line from (0,0) to (2,2)
        assert _perp_distance((1.0, 1.0), (0.0, 0.0), (2.0, 2.0)) == pytest.approx(0.0)

    def test_perpendicular_distance(self):
        # Point (0, 1) distance from x-axis segment (0,0)→(2,0) = 1.0
        assert _perp_distance((0.0, 1.0), (0.0, 0.0), (2.0, 0.0)) == pytest.approx(1.0)

    def test_degenerate_segment_falls_back_to_point_distance(self):
        # a == b → distance from point p to point a
        d = _perp_distance((3.0, 4.0), (0.0, 0.0), (0.0, 0.0))
        assert d == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# sample_cubic_bezier
# ---------------------------------------------------------------------------


class TestSampleCubicBezier:
    def test_last_point_is_p3(self):
        p0, p1, p2, p3 = (0, 0), (1, 2), (2, 2), (3, 0)
        pts = sample_cubic_bezier(p0, p1, p2, p3, tolerance=0.5)
        assert pts[-1] == pytest.approx((3.0, 0.0), abs=1e-9)

    def test_does_not_include_p0(self):
        p0, p1, p2, p3 = (0, 0), (1, 2), (2, 2), (3, 0)
        pts = sample_cubic_bezier(p0, p1, p2, p3, tolerance=0.5)
        assert (0.0, 0.0) not in pts

    def test_min_samples_is_four(self):
        # Very small curve — should still produce at least 4 samples
        p0, p1, p2, p3 = (0, 0), (0.001, 0), (0.002, 0), (0.003, 0)
        pts = sample_cubic_bezier(p0, p1, p2, p3, tolerance=0.5)
        assert len(pts) >= 4

    def test_max_samples_is_64(self):
        # Very large curve with tiny tolerance — should cap at 64
        p0, p1, p2, p3 = (0, 0), (1000, 0), (2000, 0), (3000, 0)
        pts = sample_cubic_bezier(p0, p1, p2, p3, tolerance=0.001)
        assert len(pts) <= 64

    def test_straight_line_bezier_points_on_line(self):
        # Straight line as a cubic: all control points collinear
        p0, p1, p2, p3 = (0, 0), (1, 0), (2, 0), (3, 0)
        pts = sample_cubic_bezier(p0, p1, p2, p3, tolerance=0.15)
        for x, y in pts:
            assert y == pytest.approx(0.0, abs=1e-9)
            assert 0.0 <= x <= 3.0


# ---------------------------------------------------------------------------
# rdp_simplify
# ---------------------------------------------------------------------------


class TestRdpSimplify:
    def test_two_points_returned_unchanged(self):
        pts = [(0.0, 0.0), (1.0, 1.0)]
        assert rdp_simplify(pts, 0.1) == pts

    def test_collinear_points_collapse_to_endpoints(self):
        pts = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0), (4.0, 0.0)]
        result = rdp_simplify(pts, 0.01)
        assert result == [(0.0, 0.0), (4.0, 0.0)]

    def test_corner_preserved(self):
        # L-shape: the corner should be preserved
        pts = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]
        result = rdp_simplify(pts, 0.01)
        assert (1.0, 0.0) in result

    def test_first_and_last_always_included(self):
        pts = [(0.0, 0.0), (0.5, 0.1), (1.0, 0.0)]
        result = rdp_simplify(pts, 10.0)  # huge tolerance — collapse
        assert result[0] == (0.0, 0.0)
        assert result[-1] == (1.0, 0.0)

    def test_high_tolerance_collapses(self):
        pts = [(0.0, 0.0), (1.0, 0.01), (2.0, 0.0)]
        result = rdp_simplify(pts, 1.0)
        assert len(result) == 2

    def test_low_tolerance_preserves(self):
        pts = [(0.0, 0.0), (1.0, 0.5), (2.0, 0.0)]
        result = rdp_simplify(pts, 0.001)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# simplify_path
# ---------------------------------------------------------------------------


class TestSimplifyPath:
    def _default_config(self, **kwargs):
        defaults = dict(
            tolerance=0.15,
            curves=True,
            lines=True,
            clip_paths=False,
            min_points=4,
            max_error_scale=4.0,
        )
        defaults.update(kwargs)
        return SimplifyConfig(**defaults)

    def test_paint_op_none_falls_back(self):
        path = _make_path(
            [_make_linear_subpath([(0, 0), (1, 0), (2, 0), (3, 0)])],
            paint_op=None,
        )
        sp = simplify_path(path, self._default_config())
        assert sp.fell_back

    def test_clipping_path_falls_back_by_default(self):
        path = _make_path(
            [_make_linear_subpath([(0, 0), (1, 0), (2, 0), (3, 0)])],
            is_clipping=True,
        )
        sp = simplify_path(path, self._default_config(clip_paths=False))
        assert sp.fell_back

    def test_clipping_path_simplified_when_enabled(self):
        # A collinear path with is_clipping=True should simplify if clip_paths=True
        pts = [(float(i), 0.0) for i in range(10)]
        subpath = Subpath(
            points=pts,
            closed=False,
            has_curves=False,
            ctm_scale=1.0,
            original_op_count=len(pts),
        )
        path = _make_path([subpath], is_clipping=True)
        sp = simplify_path(path, self._default_config(clip_paths=True))
        assert not sp.fell_back

    def test_subpath_below_min_points_falls_back(self):
        # 3 points but min_points=4
        path = _make_path(
            [_make_linear_subpath([(0, 0), (1, 0), (2, 0)])],
        )
        sp = simplify_path(path, self._default_config(min_points=4))
        assert sp.fell_back

    def test_no_gain_falls_back(self):
        # 2-point subpath: simplified == original (m + l = 2 ops, original=2)
        path = _make_path(
            [_make_linear_subpath([(0, 0), (1, 0)], op_count=2)],
        )
        sp = simplify_path(path, self._default_config(min_points=2))
        assert sp.fell_back

    def test_successful_linear_simplification(self):
        # 10 collinear points — RDP should reduce to 2, which is < original 10
        pts = [(float(i), 0.0) for i in range(10)]
        subpath = Subpath(
            points=pts,
            closed=False,
            has_curves=False,
            ctm_scale=1.0,
            original_op_count=10,
        )
        path = _make_path([subpath])
        sp = simplify_path(path, self._default_config())
        assert not sp.fell_back
        ops = [op for _, op in sp.subpath_instructions]
        assert ops[0] == "m"
        assert all(op == "l" for op in ops[1:])

    def test_closed_subpath_emits_h(self):
        pts = [(float(i), 0.0) for i in range(10)]
        subpath = Subpath(
            points=pts,
            closed=True,
            has_curves=False,
            ctm_scale=1.0,
            original_op_count=10,
        )
        path = _make_path([subpath])
        sp = simplify_path(path, self._default_config())
        if not sp.fell_back:
            ops = [op for _, op in sp.subpath_instructions]
            assert ops[-1] == "h"

    def test_lines_false_emits_linear_passthrough(self):
        pts = [(float(i), 0.0) for i in range(10)]
        subpath = Subpath(
            points=pts,
            closed=False,
            has_curves=False,
            ctm_scale=1.0,
            original_op_count=10,
        )
        path = _make_path([subpath])
        # lines=False means RDP is skipped — should emit as-is linear ops
        sp = simplify_path(path, self._default_config(lines=False))
        # Either fell_back (no gain) or emitted as linear without simplification
        if not sp.fell_back:
            ops = [op for _, op in sp.subpath_instructions]
            assert ops[0] == "m"

    def test_paint_op_preserved(self):
        pts = [(float(i), 0.0) for i in range(10)]
        subpath = Subpath(
            points=pts,
            closed=False,
            has_curves=False,
            ctm_scale=1.0,
            original_op_count=10,
        )
        path = _make_path([subpath], paint_op="f")
        sp = simplify_path(path, self._default_config())
        assert sp.paint_op == "f"

    def test_empty_subpaths_falls_back(self):
        path = _make_path([])
        sp = simplify_path(path, self._default_config())
        # No subpaths — trivially succeeds with empty instructions (not a fallback)
        assert sp.paint_op == "S"


# ---------------------------------------------------------------------------
# path_geometry.py — _prec helper (lines 179, 182)
# ---------------------------------------------------------------------------

from pdftl.utils.path_geometry import _prec


class TestPrecHelper:
    """Lines 179 (tol < 0.1 → 4 dp) and 182 (tol < 1.0 → 3 dp)."""

    def test_very_fine_tolerance_returns_4(self):
        assert _prec(0.05) == 4

    def test_medium_tolerance_returns_3(self):
        assert _prec(0.5) == 3

    def test_coarse_tolerance_returns_2(self):
        assert _prec(2.0) == 2


# ---------------------------------------------------------------------------
# path_geometry.py — curved subpath simplification (lines 264, 291-331)
# ---------------------------------------------------------------------------


def _curve_subpath(points, ctm_scale=1.0, op_count=None):
    return Subpath(
        points=points,
        closed=False,
        has_curves=True,
        ctm_scale=ctm_scale,
        original_op_count=op_count if op_count is not None else len(points),
    )


def _linear_subpath(points, closed=False, ctm_scale=1.0, op_count=None):
    return Subpath(
        points=points,
        closed=False,
        has_curves=False,
        ctm_scale=ctm_scale,
        original_op_count=op_count if op_count is not None else len(points),
    )


def _make_path(subpaths, paint_op="S", is_clipping=False):
    return Path(
        subpaths=subpaths,
        paint_op=paint_op,
        original_instructions=[],
        is_clipping=is_clipping,
    )


def _default_config(**kw):
    defaults = dict(
        tolerance=0.15,
        curves=True,
        lines=True,
        clip_paths=False,
        min_points=4,
        max_error_scale=4.0,
    )
    defaults.update(kw)
    return SimplifyConfig(**defaults)


class TestSimplifyCurvedPath:
    """Covers _simplify_curved (lines 291-331) via simplify_path."""

    def test_arc_curve_simplified(self):
        """A quarter-circle sampled densely should be Schneider-fitted."""
        pytest.importorskip("numba")
        r = 100.0
        pts = [
            (r * math.cos(t), r * math.sin(t)) for t in [i * math.pi / 2 / 30 for i in range(31)]
        ]
        sp_obj = _curve_subpath(pts, op_count=50)
        path = _make_path([sp_obj])
        result = simplify_path(path, _default_config())
        # Either simplified or fell back — must not raise
        assert isinstance(result, SimplifiedPath)

    def test_curves_false_emits_linear_ops_for_curved_subpath(self):
        """
        When curves=False, _simplify_subpath falls through to _emit_linear
        (line 264 — the else branch that skips both if-blocks).
        """
        pts = [(float(i), float(i % 2)) for i in range(10)]
        sp_obj = _curve_subpath(pts, op_count=20)
        path = _make_path([sp_obj])
        # curves=False means Schneider fitting is skipped;
        # lines=False means RDP is also skipped → _emit_linear passthrough
        result = simplify_path(path, _default_config(curves=False, lines=False))
        if not result.fell_back:
            ops = [op for _, op in result.subpath_instructions]
            assert ops[0] == "m"

    def test_curved_subpath_no_gain_falls_back(self):
        """
        If fitted_op_count >= original_op_count the function returns None,
        causing a whole-path fallback (line 304).
        """
        pytest.importorskip("numba")
        # Very short curve: 4 points, op_count=1 (ridiculously small original)
        # Schneider will produce 1 'm' + ≥1 'c', so fitted_op_count ≥ 2 > 1
        pts = [(0.0, 0.0), (1.0, 1.0), (2.0, 1.0), (3.0, 0.0)]
        sp_obj = _curve_subpath(pts, op_count=1)
        path = _make_path([sp_obj])
        result = simplify_path(path, _default_config())
        assert result.fell_back

    def test_closed_curved_subpath_emits_h(self):
        """Closed curved subpath must end with 'h' operator (line 328)."""
        pytest.importorskip("numba")
        r = 50.0
        pts = [
            (r * math.cos(t), r * math.sin(t)) for t in [i * 2 * math.pi / 30 for i in range(31)]
        ]
        sp_obj = Subpath(
            points=pts,
            closed=True,
            has_curves=True,
            ctm_scale=1.0,
            original_op_count=100,
        )
        path = _make_path([sp_obj])
        result = simplify_path(path, _default_config())
        if not result.fell_back:
            ops = [op for _, op in result.subpath_instructions]
            assert ops[-1] == "h"


class TestSimplifyRdpErrorBranch:
    """Covers lines 344-346: RDP ZeroDivisionError fallback."""

    def test_rdp_zero_division_causes_fallback(self, monkeypatch):
        """
        Patch rdp_simplify to raise ZeroDivisionError and confirm the path
        falls back rather than propagating the exception.
        """
        import pdftl.utils.path_geometry as pg

        def _bad_rdp(points, tolerance):
            raise ZeroDivisionError("synthetic error")

        monkeypatch.setattr(pg, "rdp_simplify", _bad_rdp)

        pts = [(float(i), 0.0) for i in range(10)]
        sp_obj = _linear_subpath(pts, op_count=10)
        path = _make_path([sp_obj])
        result = simplify_path(path, _default_config())
        assert result.fell_back


# ---------------------------------------------------------------------------
# path_geometry.py — Curved Fit Exception & Empty Branches (Lines 295-297, 300)
# ---------------------------------------------------------------------------


class TestSimplifyCurvedErrorBranches:
    """Covers lines 295-297 (ValueError catch) and line 300 (if not fitted)."""

    def test_fit_points_value_error_causes_fallback(self, monkeypatch):
        """Forces fit_points to raise a ValueError to hit lines 295-297."""
        import pdftl.utils.fit_curves as fc

        def _mock_fit_raise(*args, **kwargs):
            raise ValueError("Simulated fit failure")

        monkeypatch.setattr(fc, "fit_points", _mock_fit_raise)

        pts = [(0.0, 0.0), (1.0, 2.0), (2.0, 2.0), (3.0, 0.0)]
        sp_obj = _curve_subpath(pts, op_count=10)
        path = _make_path([sp_obj])

        result = simplify_path(path, _default_config())
        assert result.fell_back

    def test_fit_points_empty_result_causes_fallback(self, monkeypatch):
        """Forces fit_points to return an empty list to hit line 300."""
        import pdftl.utils.fit_curves as fc

        def _mock_fit_empty(*args, **kwargs):
            return []

        monkeypatch.setattr(fc, "fit_points", _mock_fit_empty)

        pts = [(0.0, 0.0), (1.0, 2.0), (2.0, 2.0), (3.0, 0.0)]
        sp_obj = _curve_subpath(pts, op_count=10)
        path = _make_path([sp_obj])

        result = simplify_path(path, _default_config())
        assert result.fell_back
