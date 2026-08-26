import pytest
from pikepdf import Array

import pdftl.operations.parsers.chop_parser as cp

# ---------------------------
# Fixtures and helpers
# ---------------------------


@pytest.fixture
def mock_page_rect():
    # A4-ish page rect: [x0, y0, x1, y1]
    return Array([0, 0, 600, 800])


# ---------------------------
# _parse_chop_spec_prep
# ---------------------------


def test_parse_chop_spec_prep_cols_default():
    content, direction, overlap_str = cp._parse_chop_spec_prep("cols")
    assert direction == "cols"
    assert content == "2"  # default to 2 pieces
    assert overlap_str is None


def test_parse_chop_spec_prep_rows_paren():
    content, direction, overlap_str = cp._parse_chop_spec_prep("rows(1:3)")
    assert direction == "rows"
    assert content == "1:3"
    assert overlap_str is None


def test_parse_chop_spec_prep_invalid_start():
    with pytest.raises(ValueError):
        cp._parse_chop_spec_prep("bad(3)")


def test_parse_chop_spec_prep_with_overlap_simple():
    content, direction, overlap_str = cp._parse_chop_spec_prep("cols3+10pt")
    assert direction == "cols"
    assert content == "3"
    assert overlap_str == "10pt"


def test_parse_chop_spec_prep_with_overlap_and_parens():
    content, direction, overlap_str = cp._parse_chop_spec_prep("rows(1:2)+5%")
    assert direction == "rows"
    assert content == "1:2"
    assert overlap_str == "5%"


def test_parse_chop_spec_prep_overlap_only_no_size():
    # No size before '+' -> falls back to default "2"
    content, direction, overlap_str = cp._parse_chop_spec_prep("cols+10pt")
    assert direction == "cols"
    assert content == "2"
    assert overlap_str == "10pt"


def test_parse_chop_spec_prep_unbalanced_parens_with_overlap():
    with pytest.raises(ValueError, match="Unbalanced parentheses"):
        cp._parse_chop_spec_prep("cols(1:2+10pt)")


def test_parse_chop_spec_prep_balanced_parens_with_overlap_ok():
    content, direction, overlap_str = cp._parse_chop_spec_prep("cols(1:2)+10pt")
    assert content == "1:2"
    assert overlap_str == "10pt"


# ---------------------------
# _parse_integer_spec
# ---------------------------


def test_parse_integer_spec_valid():
    sizes, deletes = cp._parse_integer_spec("3", 300)
    assert all(size == 100 for size in sizes)
    assert deletes == [False, False, False]


@pytest.mark.parametrize("val", ["0", "-2", "abc"])
def test_parse_integer_spec_invalid(val):
    with pytest.raises(ValueError):
        cp._parse_integer_spec(val, 300)


# ---------------------------
# _parse_ratio_spec
# ---------------------------


def test_parse_ratio_spec_valid():
    sizes, deletes = cp._parse_ratio_spec("1:2", 300)
    assert pytest.approx(sum(sizes)) == 300
    assert sizes[1] == pytest.approx(2 * sizes[0])
    assert deletes == [False, False]


def test_parse_ratio_spec_invalid():
    with pytest.raises(ValueError):
        cp._parse_ratio_spec("1:a", 200)


# ---------------------------
# _parse_overlap_value
# ---------------------------


def test_parse_overlap_value_none():
    assert cp._parse_overlap_value(None, 200) == 0.0


def test_parse_overlap_value_empty_string():
    assert cp._parse_overlap_value("", 200) == 0.0


def test_parse_overlap_value_percent():
    assert cp._parse_overlap_value("10%", 200) == pytest.approx(20.0)


def test_parse_overlap_value_percent_invalid():
    with pytest.raises(ValueError, match="Invalid overlap value"):
        cp._parse_overlap_value("abc%", 200)


def test_parse_overlap_value_unit(monkeypatch):
    monkeypatch.setattr(cp, "UNITS", {"pt": 1.0, "cm": 28.35})
    assert cp._parse_overlap_value("2cm", 200) == pytest.approx(56.7)


def test_parse_overlap_value_unit_invalid_number(monkeypatch):
    monkeypatch.setattr(cp, "UNITS", {"pt": 1.0, "cm": 28.35})
    with pytest.raises(ValueError, match="Invalid overlap value"):
        cp._parse_overlap_value("xcm", 200)


def test_parse_overlap_value_plain_number():
    assert cp._parse_overlap_value("15", 200) == 15.0


def test_parse_overlap_value_plain_invalid():
    with pytest.raises(ValueError, match="Invalid overlap value"):
        cp._parse_overlap_value("abc", 200)


def test_parse_overlap_value_negative_plain():
    with pytest.raises(ValueError, match="must be non-negative"):
        cp._parse_overlap_value("-5", 200)


def test_parse_overlap_value_negative_percent():
    with pytest.raises(ValueError, match="must be non-negative"):
        cp._parse_overlap_value("-10%", 200)


def test_parse_overlap_value_nan_plain():
    with pytest.raises(ValueError, match="must be a finite number"):
        cp._parse_overlap_value("nan", 200)


def test_parse_overlap_value_inf_plain():
    with pytest.raises(ValueError, match="must be a finite number"):
        cp._parse_overlap_value("inf", 200)


def test_parse_overlap_value_neg_inf_plain():
    # -inf is finite=False so it should hit the finiteness check,
    # not silently pass the (value < 0) check as a "valid negative".
    with pytest.raises(ValueError, match="must be a finite number"):
        cp._parse_overlap_value("-inf", 200)


def test_parse_overlap_value_nan_percent():
    with pytest.raises(ValueError, match="must be a finite number"):
        cp._parse_overlap_value("nan%", 200)


# ---------------------------
# _find_unit
# ---------------------------


def test_find_unit_matches_known_units(monkeypatch):
    monkeypatch.setattr(cp, "UNITS", {"pt": 1.0, "cm": 28.35})
    assert cp._find_unit("10cm") == "cm"
    assert cp._find_unit("15pt") == "pt"
    assert cp._find_unit("25") is None


# ---------------------------
# _parse_comma_spec_part_first_pass
# ---------------------------


def test_parse_comma_spec_part_first_pass_pt(monkeypatch):
    monkeypatch.setattr(cp, "UNITS", {"pt": 1.0})
    parsed, is_fill, delete = cp._parse_comma_spec_part_first_pass("10ptd")
    assert parsed["value"] == 10
    assert delete is True
    assert not is_fill


def test_parse_comma_spec_part_first_pass_percent():
    parsed, is_fill, delete = cp._parse_comma_spec_part_first_pass("25%")
    assert parsed["type"] == "%"
    assert parsed["value"] == 25
    assert not is_fill


def test_parse_comma_spec_part_first_pass_fill():
    parsed, is_fill, delete = cp._parse_comma_spec_part_first_pass("fill")
    assert is_fill
    assert parsed["type"] == "fill"
    assert not delete


def test_parse_comma_spec_part_first_pass_plain_number():
    parsed, is_fill, delete = cp._parse_comma_spec_part_first_pass("50")
    assert parsed == {"type": "pt", "value": 50.0}
    assert not is_fill
    assert not delete


def test_parse_comma_spec_part_first_pass_invalid():
    with pytest.raises(ValueError):
        cp._parse_comma_spec_part_first_pass("badX")


# ---------------------------
# _parse_comma_spec
# ---------------------------


def test_parse_comma_spec_mixed(monkeypatch):
    monkeypatch.setattr(cp, "UNITS", {"pt": 1.0})
    total_dim = 1000
    parts = ["10pt", "fill", "10%"]
    sizes, deletes = cp._parse_comma_spec(parts, total_dim)
    assert len(sizes) == 3
    assert pytest.approx(sum(sizes)) == total_dim
    assert deletes == [False, False, False]


def test_parse_comma_spec_fixed_exceeds(monkeypatch):
    monkeypatch.setattr(cp, "UNITS", {"pt": 1.0})
    parts = ["900pt", "200pt"]
    with pytest.raises(ValueError):
        cp._parse_comma_spec(parts, 1000)


# ---------------------------
# _build_rects
# ---------------------------


def test_build_rects_cols(mock_page_rect):
    final_sizes = [200, 400]
    delete_flags = [False, False]
    rects = cp._build_rects(final_sizes, delete_flags, "cols", 600, 800)
    assert all(isinstance(r, Array) for r in rects)
    assert rects[0][2] == 200
    assert rects[1][0] == 200


def test_build_rects_rows_with_delete(mock_page_rect):
    final_sizes = [200, 400]
    delete_flags = [True, False]
    rects = cp._build_rects(final_sizes, delete_flags, "rows", 600, 800)
    # only one rect (second, not deleted)
    assert len(rects) == 1
    assert rects[0][1] == 200  # y0 = page_height - offset - size


def test_build_rects_cols_with_overlap(mock_page_rect):
    final_sizes = [200, 400]
    delete_flags = [False, False]
    rects = cp._build_rects(final_sizes, delete_flags, "cols", 600, 800, overlap=20)
    # seam grows 10 into each side
    assert rects[0][2] == 210  # x1 of piece 0
    assert rects[1][0] == 190  # x0 of piece 1


def test_build_rects_rows_with_overlap(mock_page_rect):
    final_sizes = [400, 400]
    delete_flags = [False, False]
    rects = cp._build_rects(final_sizes, delete_flags, "rows", 600, 800, overlap=80)
    # piece 0 (top): y0 shrinks by 40 (grows downward)
    assert rects[0][1] == 360
    # piece 1 (bottom): y1 grows by 40 (grows upward)
    assert rects[1][3] == 440


def test_build_rects_overlap_skips_deleted_seam(mock_page_rect):
    # Middle piece deleted: first and third pieces are not adjacent,
    # so no overlap should be applied between them.
    final_sizes = [100, 100, 100]
    delete_flags = [False, True, False]
    rects = cp._build_rects(final_sizes, delete_flags, "cols", 300, 800, overlap=20)
    assert len(rects) == 2
    assert rects[0][2] == 100  # unchanged: neighbor (index 1) is deleted
    assert rects[1][0] == 200  # unchanged: neighbor (index 1) is deleted


def test_build_rects_overlap_no_outer_growth(mock_page_rect):
    final_sizes = [200, 400]
    delete_flags = [False, False]
    rects = cp._build_rects(final_sizes, delete_flags, "cols", 600, 800, overlap=50)
    assert rects[0][0] == 0  # left outer edge never grows
    assert rects[-1][2] == 600  # right outer edge never grows


def test_build_rects_default_overlap_is_zero(mock_page_rect):
    final_sizes = [200, 400]
    delete_flags = [False, False]
    rects = cp._build_rects(final_sizes, delete_flags, "cols", 600, 800)
    assert rects[0][2] == 200
    assert rects[1][0] == 200


# ---------------------------
# parse_chop_spec (overlap integration)
# ---------------------------


def test_parse_chop_spec_with_overlap_cols(mock_page_rect):
    rects = cp.parse_chop_spec("cols2+10pt", mock_page_rect)
    assert rects[0][2] == 305
    assert rects[1][0] == 295


def test_parse_chop_spec_with_overlap_percent_rows(mock_page_rect):
    rects = cp.parse_chop_spec("rows2+10%", mock_page_rect)
    assert rects[0][1] == 360
    assert rects[1][3] == 440


def test_parse_chop_spec_with_negative_overlap_raises(mock_page_rect):
    with pytest.raises(ValueError, match="must be non-negative"):
        cp.parse_chop_spec("cols2+-10", mock_page_rect)


# ---------------------------
# parse_chop_spec (main)
# ---------------------------


def test_parse_chop_spec_integer_cols(mock_page_rect):
    rects = cp.parse_chop_spec("cols3", mock_page_rect)
    assert len(rects) == 3


def test_parse_chop_spec_ratio_rows(mock_page_rect):
    rects = cp.parse_chop_spec("rows(1:2)", mock_page_rect)
    assert len(rects) == 2


def test_parse_chop_spec_comma(mock_page_rect):
    rects = cp.parse_chop_spec("cols(10%,fill,10%)", mock_page_rect)
    assert len(rects) == 3
    assert pytest.approx(sum(r[2] - r[0] for r in rects)) == 600


def test_parse_chop_spec_invalid(mock_page_rect):
    with pytest.raises(ValueError):
        cp.parse_chop_spec("cols(bad)", mock_page_rect)


# ---------------------------
# _split_spec_string
# ---------------------------


def test_split_spec_string_normal():
    page_range, chop = cp._split_spec_string("1-3cols2")
    assert page_range == "1-3"
    assert chop.startswith("cols")


def test_split_spec_string_no_match():
    with pytest.raises(ValueError):
        cp._split_spec_string("1-3abc")


# ---------------------------
# parse_chop_specs_to_rules
# ---------------------------


class DummyPageSpec:
    def __init__(self, start, end, qualifiers=None):
        self.start = start
        self.end = end
        self.step = 1
        self.qualifiers = qualifiers if qualifiers is not None else set()
        self.omissions = []


@pytest.fixture(autouse=True)
def mock_parse_sub_page_spec(monkeypatch):
    def dummy_parse(spec_str, total_pages):
        # simulate parse_sub_page_spec returning an object with start/end/qualifiers
        if spec_str == "1-3":
            return DummyPageSpec(1, 3, None)
        if spec_str == "4-6":
            return DummyPageSpec(4, 6, "even")
        return DummyPageSpec(1, total_pages, None)

    import pdftl.utils.page_specs

    monkeypatch.setattr(pdftl.utils.page_specs, "parse_sub_page_spec", dummy_parse)
    yield


def test_parse_chop_specs_to_rules_basic():
    specs = ["1-3cols2"]
    result = cp.parse_chop_specs_to_rules(specs, 10)
    # 3 pages, 0-based indices 0,1,2
    assert result == {0: "cols2", 1: "cols2", 2: "cols2"}


def test_parse_chop_specs_to_rules_with_range_qualifier(monkeypatch):
    specs = ["4-6cols3"]
    result = cp.parse_chop_specs_to_rules(specs, 10)
    # only even pages 4 and 6 from range qualifier
    assert set(result.keys()) == {3, 5}


def test_chop_parser_max_pieces_exceeded():
    """
    Covers line 201: raise ValueError(...larger than MAX_PIECES...)
    We call _parse_integer_spec directly to ensure we are testing the integer
    limit logic, not the fallback behavior of the main parser.
    """
    huge_number = cp.MAX_PIECES + 1
    total_dim = 1000

    with pytest.raises(ValueError) as exc:
        cp._parse_integer_spec(str(huge_number), total_dim)

    assert "Number of pieces is larger than MAX_PIECES" in str(exc.value.__cause__)


def test_chop_parser_comma_spec_excessive_size():
    """
    Ensures comma specs raise error if fixed sizes exceed page dimensions.
    We use explicit units ('pt') to force the parser to skip the integer strategy
    and use the comma/unit strategy.
    """
    page_rect = Array([0, 0, 100, 100])  # Height is 100

    # "rows2000pt" -> Explicit unit forces comma parsing. 2000 > 100.
    spec_str = "rows2000pt"

    with pytest.raises(ValueError) as exc:
        cp.parse_chop_spec(spec_str, page_rect)

    assert "Sum of fixed sizes in chop spec exceeds page dimensions" in str(exc.value)


def test_parse_chop_spec_full_page_overlap_at_100_percent(mock_page_rect):
    """+100% overlap on a 2-piece split should yield two full-page-sized pieces."""
    rects = cp.parse_chop_spec("rows2+100%", mock_page_rect)
    h0 = rects[0][3] - rects[0][1]
    h1 = rects[1][3] - rects[1][1]
    assert h0 == pytest.approx(800)
    assert h1 == pytest.approx(800)


def test_build_rects_middle_piece_grows_both_sides(mock_page_rect):
    final_sizes = [200, 200, 200]
    delete_flags = [False, False, False]
    rects = cp._build_rects(final_sizes, delete_flags, "cols", 600, 800, overlap=40)
    # middle piece grows by 20 on each side
    assert rects[1][0] == 180  # 200 - 20
    assert rects[1][2] == 420  # 400 + 20


def test_build_rects_kept_piece_flanked_by_deletions(mock_page_rect):
    final_sizes = [100, 100, 100]
    delete_flags = [True, False, True]
    rects = cp._build_rects(final_sizes, delete_flags, "cols", 300, 800, overlap=20)
    assert len(rects) == 1
    assert rects[0][0] == 100  # no growth, left neighbor deleted
    assert rects[0][2] == 200  # no growth, right neighbor deleted


def test_parse_chop_spec_single_piece_overlap_is_noop(mock_page_rect):
    rects = cp.parse_chop_spec("cols1+50pt", mock_page_rect)
    assert len(rects) == 1
    assert rects[0][0] == 0
    assert rects[0][2] == 600


def test_parse_chop_spec_trailing_empty_overlap(mock_page_rect):
    rects = cp.parse_chop_spec("cols3+", mock_page_rect)
    assert len(rects) == 3
    assert rects[0][2] == pytest.approx(200)  # no overlap applied


def test_build_rects_overlap_four_pieces_correct_seams(mock_page_rect):
    final_sizes = [100, 100, 100, 100]
    delete_flags = [False] * 4
    rects = cp._build_rects(final_sizes, delete_flags, "cols", 400, 800, overlap=20)
    # 3 internal seams, each piece except ends grows both sides by 10
    assert rects[0][2] == 110
    assert rects[1][0] == 90 and rects[1][2] == 210
    assert rects[2][0] == 190 and rects[2][2] == 310
    assert rects[3][0] == 290
