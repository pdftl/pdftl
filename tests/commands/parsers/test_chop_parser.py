import pytest
from pikepdf import Array

import pdftl.commands.parsers.chop_parser as cp

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
    content, direction = cp._parse_chop_spec_prep("cols")
    assert direction == "cols"
    assert content == "2"  # default to 2 pieces


def test_parse_chop_spec_prep_rows_paren():
    content, direction = cp._parse_chop_spec_prep("rows(1:3)")
    assert direction == "rows"
    assert content == "1:3"


def test_parse_chop_spec_prep_invalid_start():
    with pytest.raises(ValueError):
        cp._parse_chop_spec_prep("bad(3)")


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
# _get_qualified_page_numbers
# ---------------------------


def test_get_qualified_page_numbers_even_odd():
    assert cp._get_qualified_page_numbers(1, 6, "even") == [2, 4, 6]
    assert cp._get_qualified_page_numbers(1, 6, "odd") == [1, 3, 5]
    assert cp._get_qualified_page_numbers(1, 3, None) == [1, 2, 3]
    # reversed range
    assert cp._get_qualified_page_numbers(5, 3, None) == [5, 4, 3]


# ---------------------------
# _group_specs_with_qualifiers
# ---------------------------


def test_group_specs_with_even_odd():
    specs = ["even", "1-3cols2", "odd", "4-5rows3", "6cols2"]
    grouped = cp._group_specs_with_qualifiers(specs)
    assert grouped == [
        ("1-3cols2", "even"),
        ("4-5rows3", "odd"),
        ("6cols2", None),
    ]


def test_group_specs_with_missing_next():
    with pytest.raises(ValueError):
        cp._group_specs_with_qualifiers(["even"])


# ---------------------------
# parse_chop_specs_to_rules
# ---------------------------


class DummyPageSpec:
    def __init__(self, start, end, qualifiers=None):
        self.start = start
        self.end = end
        self.qualifiers = qualifiers


@pytest.fixture(autouse=True)
def mock_parse_page_spec(monkeypatch):
    def dummy_parse(spec_str, total_pages):
        # simulate parse_page_spec returning an object with start/end/qualifiers
        if spec_str == "1-3":
            return DummyPageSpec(1, 3, None)
        if spec_str == "4-6":
            return DummyPageSpec(4, 6, "even")
        return DummyPageSpec(1, total_pages, None)

    monkeypatch.setattr(cp, "parse_page_spec", dummy_parse)
    yield


def test_parse_chop_specs_to_rules_basic():
    specs = ["1-3cols2"]
    result = cp.parse_chop_specs_to_rules(specs, 10)
    # 3 pages, 0-based indices 0,1,2
    assert result == {0: "cols2", 1: "cols2", 2: "cols2"}


def test_parse_chop_specs_to_rules_with_even(monkeypatch):
    specs = ["even", "4-6cols3"]
    result = cp.parse_chop_specs_to_rules(specs, 10)
    # 4,6 only
    assert result == {3: "cols3", 5: "cols3"}


def test_parse_chop_specs_to_rules_with_range_qualifier(monkeypatch):
    specs = ["4-6cols3"]
    result = cp.parse_chop_specs_to_rules(specs, 10)
    # only even pages 4 and 6 from range qualifier
    assert set(result.keys()) == {3, 5}
