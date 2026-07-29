import pikepdf
import pytest
from pikepdf import Matrix

from pdftl.utils.geometry import (
    get_visual_mapping_matrices,
    resolve_anchor,
    rects_overlap,
    transform_quadpoints,
    transform_rect_bbox,
    wrap_visual_matrix,
    update_annotations_for_matrix,
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


def test_resolve_anchor_unrecognized_hyphenated_first_part():
    """Hits branch 107->117: first segment before the hyphen matches
    neither the top/bottom/center nor left/right groups, so both
    if/elif at 103/107 fall through and defaults (center, center) apply."""
    x, y, w, h = 0, 0, 100, 100
    assert resolve_anchor("foo-bar", x, y, w, h) == (50.0, 50.0)


def test_resolve_anchor_unrecognized_no_hyphen():
    """Hits branch 114->117: no hyphen present, and the whole string
    matches neither the vertical nor horizontal keyword sets, so both
    if/elif at 112/114 fall through and defaults (center, center) apply."""
    x, y, w, h = 0, 0, 100, 100
    assert resolve_anchor("foobar", x, y, w, h) == (50.0, 50.0)


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


class TestRectsOverlap:
    def test_clear_overlap(self):
        """Test two rects with substantial genuine overlap."""
        a = [0, 0, 10, 10]
        b = [5, 5, 15, 15]
        assert rects_overlap(a, b) is True

    def test_no_overlap_separated_horizontally(self):
        """Test rects entirely apart on the x-axis."""
        a = [0, 0, 10, 10]
        b = [20, 0, 30, 10]
        assert rects_overlap(a, b) is False

    def test_no_overlap_separated_vertically(self):
        """Test rects entirely apart on the y-axis."""
        a = [0, 0, 10, 10]
        b = [0, 20, 10, 30]
        assert rects_overlap(a, b) is False

    def test_no_overlap_diagonal_separation(self):
        """Test rects apart on both axes simultaneously."""
        a = [0, 0, 10, 10]
        b = [20, 20, 30, 30]
        assert rects_overlap(a, b) is False

    def test_touching_edge_not_overlapping(self):
        """Test rects that share exactly one edge (zero-width intersection)
        are NOT considered overlapping -- a shared boundary has no area."""
        a = [0, 0, 10, 10]
        b = [10, 0, 20, 10]
        assert rects_overlap(a, b) is False

    def test_touching_corner_not_overlapping(self):
        """Test rects that share exactly one corner point."""
        a = [0, 0, 10, 10]
        b = [10, 10, 20, 20]
        assert rects_overlap(a, b) is False

    def test_tiny_genuine_overlap_still_true(self):
        """Test that even a sliver of real overlap (not just touching) is
        detected -- this is the core no-threshold guarantee that
        distinguishes this from pdf_text/bboxes.py's containment heuristic."""
        a = [0, 0, 10, 10]
        b = [9.99, 0, 20, 10]
        assert rects_overlap(a, b) is True

    def test_fully_contained_rect(self):
        """Test a small rect entirely inside a larger one."""
        a = [0, 0, 100, 100]
        b = [10, 10, 20, 20]
        assert rects_overlap(a, b) is True

    def test_identical_rects(self):
        """Test two identical rects overlap."""
        a = [0, 0, 10, 10]
        b = [0, 0, 10, 10]
        assert rects_overlap(a, b) is True

    def test_overlap_is_symmetric(self):
        """Test rects_overlap(a, b) == rects_overlap(b, a) across several cases."""
        cases = [
            ([0, 0, 10, 10], [5, 5, 15, 15]),
            ([0, 0, 10, 10], [20, 0, 30, 10]),
            ([0, 0, 10, 10], [10, 0, 20, 10]),
        ]
        for a, b in cases:
            assert rects_overlap(a, b) == rects_overlap(b, a)

    def test_zero_area_rect_touching_is_not_overlap(self):
        """Test a degenerate zero-width rect that only touches another rect's
        edge is not treated as overlapping (consistent with the edge-touch rule)."""
        a = [0, 0, 10, 10]
        b = [10, 0, 10, 10]  # zero-width sliver at x=10
        assert rects_overlap(a, b) is False

    def test_negative_coordinates(self):
        """Test overlap detection works correctly with negative coordinate
        space (e.g. content translated below/left of the origin)."""
        a = [-10, -10, 0, 0]
        b = [-5, -5, 5, 5]
        assert rects_overlap(a, b) is True

    def test_negative_coordinates_no_overlap(self):
        """Test negative-space rects that don't overlap."""
        a = [-10, -10, -5, -5]
        b = [0, 0, 10, 10]
        assert rects_overlap(a, b) is False


class TestTransformRectBboxTupleCtm:
    def test_identity_tuple_ctm_is_noop(self):
        rect = [1.0, 2.0, 3.0, 4.0]
        identity = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        assert transform_rect_bbox(rect, identity) == pytest.approx(rect)

    def test_translation_tuple_ctm(self):
        rect = [0.0, 0.0, 10.0, 10.0]
        translate = (1.0, 0.0, 0.0, 1.0, 5.0, -2.0)
        assert transform_rect_bbox(rect, translate) == pytest.approx([5.0, -2.0, 15.0, 8.0])

    def test_scale_tuple_ctm(self):
        rect = [0.0, 0.0, 1.0, 1.0]
        scale = (2.0, 0.0, 0.0, 3.0, 0.0, 0.0)
        assert transform_rect_bbox(rect, scale) == pytest.approx([0.0, 0.0, 2.0, 3.0])

    def test_tuple_and_matrix_ctm_agree(self):
        import pikepdf

        m = pikepdf.Matrix(1, 0, 0, 1, 5, -2).rotated(30)
        as_tuple = tuple(float(x) for x in m.as_array())
        rect = [0.0, 0.0, 10.0, 4.0]
        assert transform_rect_bbox(rect, as_tuple) == pytest.approx(transform_rect_bbox(rect, m))


class TestWrapVisualMatrix:
    """Covers geometry.py's wrap_visual_matrix (lines ~170-184), previously
    flagged as a pre-existing coverage gap with no test at all."""

    def _make_page(self, rotation=0, size=(200, 300)):
        pdf = pikepdf.new()
        pdf.add_blank_page(page_size=size)
        page = pdf.pages[0]
        if rotation:
            page.Rotate = rotation
        return pdf, page

    def test_returns_none_when_unrotated_dims_missing(self, monkeypatch):
        """Hits the first `if unrot_dims is None: return None` branch."""
        _, page = self._make_page()

        def fake_dims(pg, apply_rotate=False):
            return None if not apply_rotate else (0, 0, 200, 300)

        monkeypatch.setattr("pdftl.utils.dimensions.get_visible_page_dimensions", fake_dims)
        result = wrap_visual_matrix(page, Matrix())
        assert result is None

    def test_returns_none_when_visual_dims_missing(self, monkeypatch):
        """Hits the second `if vis_dims is None: return None` branch --
        unrotated dims resolve fine but the rotated/visible dims don't."""
        _, page = self._make_page()

        def fake_dims(pg, apply_rotate=False):
            return (0, 0, 200, 300) if not apply_rotate else None

        monkeypatch.setattr("pdftl.utils.dimensions.get_visible_page_dimensions", fake_dims)
        result = wrap_visual_matrix(page, Matrix())
        assert result is None

    @pytest.mark.parametrize("rotation", [0, 90, 180, 270])
    def test_wraps_matrix_for_each_rotation(self, rotation):
        """Normal path: dims resolve fine, matrix composition succeeds and
        returns a pikepdf.Matrix for every supported rotation angle."""
        _, page = self._make_page(rotation=rotation)
        result = wrap_visual_matrix(page, Matrix())
        assert result is not None
        # Should be composable / behave like a Matrix (has as_array)
        assert hasattr(result, "as_array")

    def test_wrapped_matrix_applies_visual_translation(self):
        """Sanity check the composed matrix actually does something sane:
        wrapping identity should round-trip close to identity when there's
        no rotation."""
        _, page = self._make_page(rotation=0)
        result = wrap_visual_matrix(page, Matrix())
        arr = [float(x) for x in result.as_array()]
        assert arr[0] == pytest.approx(1.0, abs=1e-6)
        assert arr[3] == pytest.approx(1.0, abs=1e-6)


class TestUpdateAnnotationsForMatrix:
    """Covers geometry.py's update_annotations_for_matrix (lines ~189-198),
    previously flagged as a pre-existing coverage gap with no test at all."""

    def _make_page_with_annots(self, pdf):
        pdf.add_blank_page(page_size=(200, 300))
        page = pdf.pages[0]
        return page

    def test_no_annots_key_is_noop(self):
        """Hits the early `if "/Annots" not in page: return` branch."""
        pdf = pikepdf.new()
        page = self._make_page_with_annots(pdf)
        assert "/Annots" not in page
        # Should not raise
        update_annotations_for_matrix(page, Matrix())

    def test_quadpoints_rect_and_ap_all_updated(self):
        """Hits all three inner branches: QuadPoints transform, Rect
        transform, and AP deletion, on a single synthetic annotation."""
        pdf = pikepdf.new()
        page = self._make_page_with_annots(pdf)

        annot = pdf.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Annot"),
                    "/Subtype": pikepdf.Name("/Highlight"),
                    "/QuadPoints": pikepdf.Array([0, 0, 10, 0, 10, 10, 0, 10]),
                    "/Rect": pikepdf.Array([0, 0, 10, 10]),
                    "/AP": pikepdf.Dictionary({"/N": pdf.make_stream(b"")}),
                }
            )
        )
        page.Annots = pikepdf.Array([annot])

        translate = Matrix().translated(5, 5)
        update_annotations_for_matrix(page, translate)

        assert list(map(float, page.Annots[0].QuadPoints)) == [
            5.0,
            5.0,
            15.0,
            5.0,
            15.0,
            15.0,
            5.0,
            15.0,
        ]
        assert list(map(float, page.Annots[0].Rect)) == [5.0, 5.0, 15.0, 15.0]
        assert "/AP" not in page.Annots[0]

    def test_annot_missing_optional_keys_skips_branches(self):
        """An annotation with none of QuadPoints/Rect/AP should just be
        left alone -- covers the "all three ifs are False" path."""
        pdf = pikepdf.new()
        page = self._make_page_with_annots(pdf)

        annot = pdf.make_indirect(
            pikepdf.Dictionary(
                {"/Type": pikepdf.Name("/Annot"), "/Subtype": pikepdf.Name("/Popup")}
            )
        )
        page.Annots = pikepdf.Array([annot])

        # Should not raise, and should leave the annot untouched
        update_annotations_for_matrix(page, Matrix())
        assert "/QuadPoints" not in page.Annots[0]
        assert "/Rect" not in page.Annots[0]
