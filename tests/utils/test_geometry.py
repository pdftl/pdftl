import pikepdf
import pytest
from pikepdf import Matrix

from pdftl.utils.geometry import (
    get_visual_mapping_matrices,
    resolve_anchor,
    transform_quadpoints,
    transform_rect_bbox,
)


@pytest.fixture
def mock_page():
    """Provides a page with known MediaBox and TrimBox."""
    with pikepdf.new() as pdf:
        pdf.add_blank_page(page_size=(200, 300))
        page = pdf.pages[0]
        # Set a TrimBox smaller than MediaBox to test line 31
        page.TrimBox = [10, 10, 110, 110]  # x=10, y=10, w=100, h=100
        yield page


def test_resolve_anchor_variants():
    """Hits lines 105-113 and 120, 127 (the 'else' branches for center)."""
    x, y, w, h = 0, 0, 100, 100

    # Test "left-top" (swapped order logic in line 105)
    assert resolve_anchor("left-top", x, y, w, h) == (0, 100)

    # Test "right" only (line 113 & 127)
    assert resolve_anchor("right", x, y, w, h) == (100, 50)

    # Test "bottom" only (line 111 & 120)
    assert resolve_anchor("bottom", x, y, w, h) == (50, 0)

    # Test single part "left" (line 113)
    assert resolve_anchor("left", x, y, w, h) == (0, 50)


def test_transform_quadpoints():
    """Hits lines 75-79: Tests point-pair transformations."""
    # Quadpoints are usually sets of 8 (4 points), but the function
    # handles any even number of floats.
    quads = [0.0, 0.0, 10.0, 10.0]
    matrix = Matrix().translated(5, 5)

    new_quads = transform_quadpoints(quads, matrix)
    assert new_quads == [5.0, 5.0, 15.0, 15.0]


def test_transform_point_and_matrix_array():
    """Hits lines 86-87: Tests the internal point math."""
    # Moving (10, 10) by a translation matrix
    matrix = Matrix().translated(10, 10)
    # This triggers _transform_point which uses m.as_array()
    from pdftl.utils.geometry import _transform_point

    nx, ny = _transform_point(10, 10, matrix)

    assert nx == 20.0
    assert ny == 20.0


def test_resolve_anchor_top_center():
    """Hits lines 102-104: The vertical-first split branch."""
    x, y, w, h = 0, 0, 100, 100
    # This hits: if parts[0] in ["top", "bottom", "center"]
    # followed by: if len(parts) > 1: h_pos = parts[1]
    res = resolve_anchor("top-center", x, y, w, h)
    assert res == (50.0, 100.0)


def test_resolve_anchor_shorthand_vonly():
    """Hits line 102 but without a horizontal component."""
    x, y, w, h = 0, 0, 100, 100
    # Hits the logic where it's a split but only one part or no second part
    # though usually handled by the 'else' in line 120/127
    res = resolve_anchor("top", x, y, w, h)
    assert res == (50.0, 100.0)


def test_transform_rect_bbox():
    """Hits lines 56-68: Tests AABB calculation after rotation."""
    # A 10x10 square at origin [x1, y1, x2, y2]
    rect = [0, 0, 10, 10]

    # Rotate 45 degrees around the origin
    matrix = Matrix().rotated(45)

    bbox = transform_rect_bbox(rect, matrix)

    # Math check:
    # Corner (10,10) becomes (10cos45 - 10sin45, 10sin45 + 10cos45) = (0, 14.14)
    # Corner (10,0) becomes (7.07, 7.07)
    # Corner (0,10) becomes (-7.07, 7.07)
    # So the AABB is approx [-7.07, 0, 7.07, 14.14]

    assert bbox[0] == pytest.approx(-7.071, abs=1e-3)  # Min X
    assert bbox[1] == pytest.approx(0.0, abs=1e-3)  # Min Y
    assert bbox[2] == pytest.approx(7.071, abs=1e-3)  # Max X
    assert bbox[3] == pytest.approx(14.142, abs=1e-3)  # Max Y


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_get_visual_mapping_matrices_rotations(rotation):
    m_u_to_v, m_v_to_u = get_visual_mapping_matrices(0, 0, 200, 300, rotation)
    # Round-trip should give identity
    identity = m_u_to_v @ m_v_to_u
    arr = list(map(float, identity.as_array()))
    assert arr[0] == pytest.approx(1.0, abs=1e-6)
    assert arr[3] == pytest.approx(1.0, abs=1e-6)
    assert arr[4] == pytest.approx(0.0, abs=1e-6)
    assert arr[5] == pytest.approx(0.0, abs=1e-6)


def test_get_visual_mapping_matrices_270():
    from pdftl.utils.geometry import get_visual_mapping_matrices

    m_u_to_v, m_v_to_u = get_visual_mapping_matrices(0, 0, 200, 300, 270)
    identity = m_u_to_v @ m_v_to_u
    arr = list(map(float, identity.as_array()))
    assert arr[0] == pytest.approx(1.0, abs=1e-6)
    assert arr[3] == pytest.approx(1.0, abs=1e-6)


def test_get_visual_mapping_matrices_rotation_0():
    import pikepdf

    from pdftl.utils.geometry import get_visual_mapping_matrices

    m_u_to_v, m_v_to_u = get_visual_mapping_matrices(0, 0, 200, 300, 0)
    # Should return identity matrices
    identity = pikepdf.Matrix()
    assert list(map(float, m_u_to_v.as_array())) == list(map(float, identity.as_array()))
    assert list(map(float, m_v_to_u.as_array())) == list(map(float, identity.as_array()))
