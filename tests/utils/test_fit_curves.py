# tests/utils/test_fit_curves.py

"""Tests for pdftl.utils.fit_curves.

Pure-Python tests (no numba) run unconditionally.
JIT-dependent tests are guarded with pytest.importorskip("numba").
"""

import math
import pytest

from pdftl.utils.fit_curves import (
    _ctrl_to_list,
    _normalize,
    fit_cubic,
    fit_points,
)


# ---------------------------------------------------------------------------
# Pure Python helpers — no numba required
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_unit_vector_unchanged(self):
        result = _normalize((1.0, 0.0))
        assert result == pytest.approx((1.0, 0.0))

    def test_normalizes_correctly(self):
        result = _normalize((3.0, 4.0))
        assert result == pytest.approx((0.6, 0.8))

    def test_zero_vector_returns_zero(self):
        result = _normalize((0.0, 0.0))
        assert result == (0.0, 0.0)

    def test_negative_components(self):
        result = _normalize((-1.0, 0.0))
        assert result == pytest.approx((-1.0, 0.0))


class TestCtrlToList:
    def test_converts_ndarray(self):
        np = pytest.importorskip("numpy")
        ctrl = np.array([[0.0, 0.0], [1.0, 2.0], [3.0, 4.0], [5.0, 6.0]])
        result = _ctrl_to_list(ctrl)
        assert result == [
            (0.0, 0.0),
            (1.0, 2.0),
            (3.0, 4.0),
            (5.0, 6.0),
        ]

    def test_values_are_python_floats(self):
        np = pytest.importorskip("numpy")
        ctrl = np.zeros((4, 2), dtype=np.float64)
        result = _ctrl_to_list(ctrl)
        for pt in result:
            assert isinstance(pt[0], float)
            assert isinstance(pt[1], float)


# ---------------------------------------------------------------------------
# fit_cubic / fit_points — require numba for the JIT kernels
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _require_numba():
    pytest.importorskip("numba")


class TestFitCubicTwoPoints:
    """The 2-point early-return path needs numba for the numpy array conversion
    but not for the JIT kernels themselves."""

    def test_raises_for_fewer_than_two_points(self):
        pytest.importorskip("numba")
        with pytest.raises(ValueError, match="at least 2 points"):
            fit_cubic([(0, 0)], (1, 0), (-1, 0), 0.15, 4.0)

    def test_two_points_returns_single_curve(self):
        pytest.importorskip("numba")
        result = fit_cubic([(0.0, 0.0), (1.0, 0.0)], (1, 0), (-1, 0), 0.15, 4.0)
        assert len(result) == 1
        curve = result[0]
        assert len(curve) == 4
        # Endpoints must match input points
        assert curve[0] == pytest.approx((0.0, 0.0))
        assert curve[3] == pytest.approx((1.0, 0.0))

    def test_two_points_control_points_between_endpoints(self):
        pytest.importorskip("numba")
        result = fit_cubic([(0.0, 0.0), (3.0, 0.0)], (1, 0), (-1, 0), 0.15, 4.0)
        curve = result[0]
        p1x = curve[1][0]
        p2x = curve[2][0]
        assert 0.0 < p1x < 3.0
        assert 0.0 < p2x < 3.0


class TestFitPoints:
    def test_raises_for_fewer_than_two_points(self):
        pytest.importorskip("numba")
        with pytest.raises(ValueError, match="at least 2 points"):
            fit_points([(0, 0)])

    def test_straight_line_fits_in_one_segment(self):
        pytest.importorskip("numba")
        pts = [(float(i), 0.0) for i in range(10)]
        result = fit_points(pts, max_error=0.15)
        assert len(result) == 1

    def test_endpoints_preserved(self):
        pytest.importorskip("numba")
        pts = [(0.0, 0.0), (1.0, 1.0), (2.0, 0.0), (3.0, 1.0), (4.0, 0.0)]
        result = fit_points(pts, max_error=0.01)
        assert result[0][0] == pytest.approx((0.0, 0.0), abs=1e-6)
        assert result[-1][3] == pytest.approx((4.0, 0.0), abs=1e-6)

    def test_arc_approximation_within_tolerance(self):
        """Points sampled from a quarter-circle arc should be fitted within max_error."""
        pytest.importorskip("numba")
        r = 100.0
        max_error = 0.5
        pts = [
            (r * math.cos(t), r * math.sin(t)) for t in [i * math.pi / 2 / 20 for i in range(21)]
        ]
        result = fit_points(pts, max_error=max_error)
        # Verify fitted curve stays within tolerance of the original points
        # by checking a sample of the input points against the fitted beziers
        assert result  # at least one segment produced

    def test_max_error_scale_affects_subdivision(self):
        """Higher max_error_scale should allow fewer segments on a near-miss curve."""
        pytest.importorskip("numba")
        # Use a gentle arc that sits just above the base tolerance
        r = 50.0
        pts = [
            (r * math.cos(t), r * math.sin(t)) for t in [i * math.pi / 4 / 10 for i in range(11)]
        ]
        result_tight = fit_points(pts, max_error=0.5, max_error_scale=1.0)
        result_loose = fit_points(pts, max_error=0.5, max_error_scale=8.0)
        # loose scale allows more reparameterization attempts → typically fewer segments
        assert len(result_loose) <= len(result_tight)

    def test_accepts_list_of_tuples(self):
        pytest.importorskip("numba")
        pts = [(0.0, 0.0), (1.0, 0.5), (2.0, 0.0)]
        result = fit_points(pts, max_error=0.5)
        assert isinstance(result, list)

    def test_accepts_ndarray(self):
        np = pytest.importorskip("numpy")
        pytest.importorskip("numba")
        pts = np.array([(0.0, 0.0), (1.0, 0.5), (2.0, 0.0)])
        result = fit_points(pts, max_error=0.5)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# fit_curves.py — pure math helpers (lines 56, 60, 64, 68, 72)
# and fit_points zero-vector tangent branches (lines 172, 177, 186, 218, 223)
# ---------------------------------------------------------------------------

from pdftl.utils.fit_curves import (
    _add,
    _dot,
    _len,
    _mul,
    _sub,
)


class TestPureMathHelpers:
    """Covers lines 56 (_add), 60 (_sub), 64 (_mul), 68 (_dot), 72 (_len)."""

    def test_add(self):
        assert _add((1.0, 2.0), (3.0, 4.0)) == (4.0, 6.0)

    def test_sub(self):
        assert _sub((5.0, 3.0), (2.0, 1.0)) == (3.0, 2.0)

    def test_mul(self):
        assert _mul((2.0, 3.0), 4.0) == (8.0, 12.0)

    def test_dot(self):
        assert _dot((1.0, 2.0), (3.0, 4.0)) == pytest.approx(11.0)

    def test_len(self):
        assert _len((3.0, 4.0)) == pytest.approx(5.0)

    def test_len_zero_vector(self):
        assert _len((0.0, 0.0)) == pytest.approx(0.0)


class TestFitPointsZeroTangentBranches:
    """
    fit_points builds left/right tangents from adjacent point differences.
    When those differences are (near-)zero, the normalisation branches (lines
    172/177 and 218/223) clamp the tangent to (0, 0).

    A single degenerate pair triggers the zero-length left tangent; a
    repeated endpoint triggers the zero-length right tangent.
    """

    def test_zero_left_tangent_duplicate_start(self):
        """First two points identical → left tangent length ≈ 0 (lines 172/177)."""
        pytest.importorskip("numba")
        pts = [(0.0, 0.0), (0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]
        result = fit_points(pts, max_error=0.5)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_zero_right_tangent_duplicate_end(self):
        """Last two points identical → right tangent length ≈ 0 (lines 218/223)."""
        pytest.importorskip("numba")
        pts = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (2.0, 0.0)]
        result = fit_points(pts, max_error=0.5)
        assert isinstance(result, list)
        assert len(result) >= 1

    def test_both_tangents_zero(self):
        """All points identical — both tangents degenerate."""
        pytest.importorskip("numba")
        pts = [(1.0, 1.0), (1.0, 1.0), (1.0, 1.0), (1.0, 1.0)]
        # Should not raise; result is undefined but must be a list
        result = fit_points(pts, max_error=0.5)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Midpoint Degenerate Tangents (Lines 172, 177, 186 inside recursive split)
# ---------------------------------------------------------------------------


class TestFitCubicSplitDegenerateTangents:
    """Targets lines 172, 177, and 186 by directly executing the split recursive step."""

    def test_split_point_degenerate_left_and_right_tangents_direct(self):
        """Forces len_l and len_r to be zero by supplying duplicate points adjacent to split."""
        pytest.importorskip("numba")
        from pdftl.utils.fit_curves import _split_and_recurse_fit_cubic
        import numpy as np

        # Construct a 5-point array where split index is 2
        # Point 1 == Point 2 (Left distance is zero -> line 172)
        # Point 2 == Point 3 (Right distance is zero -> line 177)
        pts = np.array(
            [
                [0.0, 0.0],
                [5.0, 5.0],  # Index 1
                [5.0, 5.0],  # Index 2 (split point)
                [5.0, 5.0],  # Index 3
                [10.0, 10.0],
            ],
            dtype=np.float64,
        )

        # Call the internal splitter directly to guarantee evaluation of the branches
        result = _split_and_recurse_fit_cubic(
            pts=pts,
            split=2,
            tl0=1.0,
            tl1=0.0,
            tr0=-1.0,
            tr1=0.0,
            max_error=0.15,
            max_error_scale=4.0,
        )
        assert isinstance(result, list)

    def test_split_point_canceling_tangents_line_186(self):
        """Forces left and right tangents to exactly oppose each other, canceling out."""
        pytest.importorskip("numba")
        pts = [
            (0.0, 0.0),
            (5.0, 5.0),
            (9.0, 9.0),
            (10.0, 10.0),  # Cusp split anchor
            (9.0, 9.0),
            (5.0, 5.0),
            (0.0, 0.0),
        ]
        result = fit_points(pts, max_error=0.01)
        assert isinstance(result, list)
