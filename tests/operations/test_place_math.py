# tests/operations/test_place_math.py
import pytest

from pdftl.operations.place import _multiply_matrices


def apply_matrix(point, matrix):
    x, y = point
    a, b, c, d, e, f = matrix
    # PDF Coords: [x y 1] * [a b 0; c d 0; e f 1]
    # x' = ax + cy + e
    # y' = bx + dy + f
    nx = x * a + y * c + e
    ny = x * b + y * d + f
    return nx, ny


def test_scale_around_center_invariant():
    """
    If we scale 0.5 around the center (100, 100),
    the point (100, 100) must remain at (100, 100).
    """
    # 1. Translate center (100,100) to origin (-100, -100)
    m1 = [1, 0, 0, 1, -100, -100]
    # 2. Scale 0.5
    m2 = [0.5, 0, 0, 0.5, 0, 0]
    # 3. Translate back (+100, +100)
    m3 = [1, 0, 0, 1, 100, 100]

    # Combine m1 * m2 * m3
    combined = _multiply_matrices(m1, _multiply_matrices(m2, m3))

    # Apply to the anchor point itself
    p_out = apply_matrix((100, 100), combined)

    assert p_out[0] == 100.0
    assert p_out[1] == 100.0


def test_scale_point_movement():
    """
    If we scale 0.5 around (0,0), point (100,100) should move to (50,50).
    """
    m1 = [1, 0, 0, 1, 0, 0]  # No translation needed for 0,0 anchor
    m2 = [0.5, 0, 0, 0.5, 0, 0]
    m3 = [1, 0, 0, 1, 0, 0]

    combined = _multiply_matrices(m1, _multiply_matrices(m2, m3))

    p_out = apply_matrix((100, 100), combined)
    assert p_out == (50.0, 50.0)


def test_spin_around_center():
    """
    Rotate 90 degrees around (100,100).
    Point (200, 100) [Right of center] should become (100, 200) [Top of center]
    """
    # m2 for 90 deg: cos=0, sin=1 => [0, 1, -1, 0, 0, 0]
    m1 = [1, 0, 0, 1, -100, -100]
    m2 = [0, 1, -1, 0, 0, 0]
    m3 = [1, 0, 0, 1, 100, 100]

    combined = _multiply_matrices(m1, _multiply_matrices(m2, m3))

    # Input: 200, 100
    p_out = apply_matrix((200, 100), combined)

    assert p_out[0] == pytest.approx(100.0)
    assert p_out[1] == pytest.approx(200.0)
