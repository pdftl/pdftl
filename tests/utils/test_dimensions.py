import pytest

from pdftl.utils.dimensions import dim_str_to_pts

# ---------------------------
# Tests for _parse_single_margin_value (internal helper)
# ---------------------------

cm_pt = 28.346456692


@pytest.mark.parametrize(
    "val_str, total_dimension, expected_pts",
    [
        ("50", 800, 50.0),  # Default unit is pt
        ("50pt", 800, 50.0),  # Explicit pt
        ("0", 800, 0.0),  # Zero value
        ("", 800, 0.0),  # Empty value
        ("10%", 800, 80.0),  # Percentage
        ("1in", 100, 72.0),  # Inch
        ("2in", 100, 144.0),  # Multiple inches
        ("1cm", 100, cm_pt),  # Centimeter
        ("10mm", 100, cm_pt),  # Millimeter (10mm = 1cm)
    ],
)
def test_dim_str_to_pts(val_str, total_dimension, expected_pts):
    result = dim_str_to_pts(val_str, total_dimension)
    assert pytest.approx(result) == expected_pts


def test_dim_str_to_pts_invalid():
    with pytest.raises(ValueError):
        dim_str_to_pts("foo", 800)


# --- Covers lines 197-199 ---
def test_dim_str_to_pts_bad_percentage():
    """
    Triggers the specific ValueError catch block for percentages.
    Input 'bad%' passes `.endswith('%')`, but `float('bad')` raises ValueError.
    The code catches it, passes, and falls through to the final float conversion.
    """
    # "bad%" -> triggers line 197 (ValueError) -> line 199 (pass)
    # -> line 207 (strip pt) -> line 208 float('bad%') -> raises ValueError again.
    with pytest.raises(ValueError):
        dim_str_to_pts("bad%", 100)
