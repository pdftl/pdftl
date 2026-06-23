# tests/utils/test_fit_curves_jit.py

"""Tests targeting edge case branches in _fit_curves_jit.py using .py_func for coverage."""

import numpy as np

from pdftl.utils._fit_curves_jit import (
    chord_length_parameterize,
    generate_bezier,
    reparameterize,
    find_max_error,
)


class TestFitCurvesJitEdges:
    """Covers all edge case conditions and fallback branches in the JIT helpers."""

    def test_chord_length_parameterize_degenerate_all_points_same(self):
        """Forces total <= 1e-9 (Line 34) to execute the uniform parameterization loop."""
        pts = np.array([[5.0, 5.0], [5.0, 5.0], [5.0, 5.0], [5.0, 5.0]], dtype=np.float64)

        # Call the un-jitted pure Python function for coverage tracking
        u = chord_length_parameterize.py_func(pts)

        assert np.allclose(u, [0.0, 1.0 / 3.0, 2.0 / 3.0, 1.0])

    def test_generate_bezier_near_zero_determinant(self):
        """Forces abs(det) <= 1e-9 (Line 82) by providing parallel/collinear tangents."""
        pts = np.array([[0.0, 0.0], [5.0, 0.0], [10.0, 0.0]], dtype=np.float64)
        u = np.array([0.0, 0.5, 1.0], dtype=np.float64)
        ctrl_out = np.zeros((4, 2), dtype=np.float64)

        tl0, tl1 = 0.0, 0.0
        tr0, tr1 = 0.0, 0.0

        generate_bezier.py_func(pts, u, ctrl_out, tl0, tl1, tr0, tr1)
        assert np.allclose(ctrl_out[1], [0.0, 0.0])

    def test_reparameterize_near_zero_denominator(self):
        """Forces abs(den) <= 1e-9 (Line 155) inside Newton-Raphson iteration loop."""
        ctrl = np.array([[0.0, 0.0], [0.0, 0.0], [0.0, 0.0], [0.0, 0.0]], dtype=np.float64)
        pts = np.array([[5.0, 5.0]], dtype=np.float64)
        u = np.array([0.5], dtype=np.float64)

        new_u = reparameterize.py_func(ctrl, pts, u)
        assert new_u[0] == 0.5

    def test_reparameterize_clamping_bounds(self):
        """Forces parameter clamping to v = 0.0 (Line 158) and v = 1.0 (Line 160)."""
        ctrl = np.array([[0.0, 0.0], [3.33, 0.0], [6.66, 0.0], [10.0, 0.0]], dtype=np.float64)

        pts_left = np.array([[-5.0, 0.0]], dtype=np.float64)
        u_left = np.array([0.05], dtype=np.float64)
        res_left = reparameterize.py_func(ctrl, pts_left, u_left)
        assert res_left[0] == 0.0

        pts_right = np.array([[20.0, 0.0]], dtype=np.float64)
        u_right = np.array([0.95], dtype=np.float64)
        res_right = reparameterize.py_func(ctrl, pts_right, u_right)
        assert res_right[0] == 1.0

    def test_find_max_error_split_clamping_bounds(self):
        """Forces split clamping to lower bound (Line 198) and upper bound (Line 200)."""
        ctrl = np.array([[0.0, 0.0], [2.0, 0.0], [4.0, 0.0], [6.0, 0.0]], dtype=np.float64)
        u = np.array([0.0, 0.25, 0.5, 0.75, 1.0], dtype=np.float64)

        pts_left = np.array(
            [[0.0, 10.0], [1.5, 0.0], [3.0, 0.0], [4.5, 0.0], [6.0, 0.0]], dtype=np.float64
        )
        _, split_left = find_max_error.py_func(pts_left, ctrl, u)
        assert split_left == 1

        pts_right = np.array(
            [[0.0, 0.0], [1.5, 0.0], [3.0, 0.0], [4.5, 0.0], [6.0, 10.0]], dtype=np.float64
        )
        _, split_right = find_max_error.py_func(pts_right, ctrl, u)
        assert split_right == 3

    def test_chord_length_parameterize_happy_path(self):
        """Covers lines 31-33 by providing distinct points that calculate real chord lengths."""
        pts = np.array([[0.0, 0.0], [3.0, 4.0], [6.0, 8.0]], dtype=np.float64)
        u = chord_length_parameterize.py_func(pts)
        assert np.allclose(u, [0.0, 0.5, 1.0])

    def test_generate_bezier_happy_path(self):
        """Covers lines 80-81 and 101-108 by providing data that produces non-zero alphas."""
        # A simple curve layout with non-zero, distinct tangents
        pts = np.array([[0.0, 0.0], [5.0, 2.0], [10.0, 0.0]], dtype=np.float64)
        u = np.array([0.0, 0.5, 1.0], dtype=np.float64)
        ctrl_out = np.zeros((4, 2), dtype=np.float64)

        # Tangent vectors pointing outwards/forward
        tl0, tl1 = 1.0, 0.5
        tr0, tr1 = -1.0, 0.5

        generate_bezier.py_func(pts, u, ctrl_out, tl0, tl1, tr0, tr1)

        # Verify that control points 1 and 2 were populated via the happy path (lines 101-108)
        assert not np.allclose(ctrl_out[1], [0.0, 0.0])
        assert not np.allclose(ctrl_out[2], [0.0, 0.0])
