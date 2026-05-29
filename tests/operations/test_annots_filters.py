# tests/operations/test_annots_filters_coverage.py

import logging

import pikepdf
import pytest

# ---------------------------------------------------------------------------
# Helpers / internal imports
# ---------------------------------------------------------------------------
from pdftl.operations.annots_filters import (
    _annot_matches_filters,
    _annot_passes_rule,
    _data_item_to_string_helper,
    _delete_annots_in_page,
    _get_all_annots_data,
    _lines_from_datum,
    _values_equal,
    dump_data_annots,
    dump_data_annots_cli_hook,
)
from pdftl.core.core_types import OpResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def simple_pdf():
    """One-page PDF with a Text and a Highlight annotation."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page()

    text_annot = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Text,
        Rect=[0, 0, 50, 50],
        Contents=pikepdf.String("Hello"),
        Border=[0, 0, 1],
    )
    highlight_annot = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Highlight,
        Rect=[10, 10, 60, 60],
        C=pikepdf.Array([1, 1, 0]),
    )
    page.Annots = pdf.make_indirect(pikepdf.Array([text_annot, highlight_annot]))
    return pdf


# ---------------------------------------------------------------------------
# Line 179 – _get_all_annots_data with compat=True AND rules
# ---------------------------------------------------------------------------


def test_get_all_annots_data_compat_true_with_rules(simple_pdf):
    """
    _get_all_annots_data is normally called either with compat=True (no rules)
    or compat=False (with rules).  Calling it with both exercises line 179 where
    `included_pages` is built from rules while compat=True is also set.
    """
    from pdftl.operations.parsers.modify_annots_parser import specs_to_selection_rules

    rules = specs_to_selection_rules(["1/Text"], len(simple_pdf.pages))
    data = _get_all_annots_data(simple_pdf, compat=True, rules=rules)

    # Only the /Text annotation should survive the filter
    assert len(data) == 1
    assert data[0]["Properties"]["/Subtype"] == "/Text"


# ---------------------------------------------------------------------------
# Lines 242-243 – _lines_from_datum compat=False with a short key (/C)
# ---------------------------------------------------------------------------


def test_lines_from_datum_compat_false_includes_short_key_and_index():
    """
    With compat=False every property key is processed (including short ones
    that would normally be skipped).  The /C key (len == 2) is short and is
    handled by _key_value_lines returning [] – this exercises the branch at
    line ~242 where compat=False allows the loop to run even for short keys
    (they are then silently dropped inside _key_value_lines).
    Also asserts that AnnotIndexInPage is emitted (line 243).
    """
    datum = {
        "Page": 2,
        "AnnotationIndex": 7,
        "Properties": {
            "/Subtype": "/Text",
            "/C": [1, 0, 0],  # Short key – will be filtered inside _key_value_lines
            "/Contents": "Test",
        },
    }
    lines = _lines_from_datum(datum, str, compat=False)

    # IndexInPage line must be present (line 243)
    assert any("AnnotIndexInPage: 7" in l for l in lines)


# ---------------------------------------------------------------------------
# Lines 298-301 – dump_data_annots_cli_hook early return when data is falsy
# ---------------------------------------------------------------------------


def test_dump_data_annots_cli_hook_no_data(caplog):
    """
    When result.data is None/empty the hook should log a warning and return
    early without crashing (lines 298-301).
    """
    result = OpResult(success=True, data=None, meta={})

    with caplog.at_level(logging.WARNING):
        dump_data_annots_cli_hook(result, None, None)

    assert "No data available" in caplog.text


# ---------------------------------------------------------------------------
# Lines 312, 314 – _generate_pdftk_annots_report without PdfUriBase
# ---------------------------------------------------------------------------


def test_dump_data_annots_no_uri_base(simple_pdf, capsys):
    """
    When the PDF has no Root.URI.Base, the report must not contain
    'PdfUriBase' (line 312 – empty uri_line branch, line 314 – joining).
    """
    result = dump_data_annots(simple_pdf, output_file=None)
    assert result.success
    assert "PdfUriBase" not in result.data

    dump_data_annots_cli_hook(result, None, None)
    out = capsys.readouterr().out
    assert "PdfUriBase" not in out
    assert "NumberOfPages: 1" in out


# ---------------------------------------------------------------------------
# Lines 326-335 – _delete_annots_in_page: fast-path wipe
# ---------------------------------------------------------------------------


def test_delete_annots_in_page_fast_path_wipe(simple_pdf):
    """
    A rule with no type_selector and no value_selectors triggers the fast-path
    that sets page.Annots = [] directly (lines 326-330).
    """
    from unittest.mock import MagicMock

    page = simple_pdf.pages[0]
    assert len(page.Annots) == 2

    # Build a minimal rule that matches everything (no type, no value selectors)
    rule = MagicMock()
    rule.type_selector = None
    rule.value_selectors = []
    rule.page_numbers = [1]

    page_object_to_num_map = {p.obj.objgen: i + 1 for i, p in enumerate(simple_pdf.pages)}
    _delete_annots_in_page(1, page, [rule], page_object_to_num_map)

    assert len(page.Annots) == 0


def test_delete_annots_in_page_selective_deletion(simple_pdf):
    """
    A rule with a type_selector deletes only matching annotations, exercising
    the per-annotation loop (lines 331-335).
    """
    from unittest.mock import MagicMock

    page = simple_pdf.pages[0]
    assert len(page.Annots) == 2  # Text + Highlight

    rule = MagicMock()
    rule.type_selector = "/Text"
    rule.value_selectors = []
    rule.page_numbers = [1]

    page_object_to_num_map = {p.obj.objgen: i + 1 for i, p in enumerate(simple_pdf.pages)}
    _delete_annots_in_page(1, page, [rule], page_object_to_num_map)

    # Only the Highlight should remain
    assert len(page.Annots) == 1
    assert str(page.Annots[0].Subtype) == "/Highlight"


# ---------------------------------------------------------------------------
# Lines 343-353 – _annot_matches_filters
# ---------------------------------------------------------------------------


def test_annot_matches_filters_key_missing():
    """Key not present in props → returns False (line 353)."""
    result = _annot_matches_filters({"/Subtype": "/Text"}, [("Rect", "[0 0 10 10]")])
    assert result is False


def test_annot_matches_filters_value_mismatch():
    """Key present but value differs → returns False."""
    props = {"/Subtype": "/Text", "/Rect": [0, 0, 10, 10]}
    result = _annot_matches_filters(props, [("Rect", "[0 0 99 99]")])
    assert result is False


def test_annot_matches_filters_invalid_value_string():
    """
    _parse_value_to_python raises ValueError for a malformed value
    → _annot_matches_filters returns False (line 348).
    """
    props = {"/Subtype": "/Text", "/Rect": [0, 0, 10, 10]}
    result = _annot_matches_filters(props, [("Rect", "[0 0 1")])  # Malformed array
    assert result is False


def test_annot_matches_filters_match():
    """All K=V pairs match → returns True."""
    props = {"/Subtype": "/Text", "/Contents": "Hello"}
    result = _annot_matches_filters(props, [("Contents", "(Hello)")])
    assert result is True


# ---------------------------------------------------------------------------
# Lines 358-368 – _values_equal
# ---------------------------------------------------------------------------


def test_values_equal_list_length_mismatch():
    """Lists of different lengths → False (line 360)."""
    assert _values_equal([1, 2], [1, 2, 3]) is False


def test_values_equal_list_same():
    """Lists with equal elements → True."""
    assert _values_equal([1.0, 0.0], [1, 0]) is True


def test_values_equal_numeric_cross_type():
    """int vs float comparison (line 364)."""
    assert _values_equal(1, 1.0) is True
    assert _values_equal(1, 2.0) is False


def test_values_equal_name_with_slash():
    """Name strings are compared after stripping leading slash (line 367)."""
    assert _values_equal("/Text", "Text") is True
    assert _values_equal("Text", "/Text") is True
    assert _values_equal("/Text", "/Link") is False


def test_values_equal_plain_strings():
    """Non-name strings use direct equality (line 368)."""
    assert _values_equal("hello", "hello") is True
    assert _values_equal("hello", "world") is False


def test_values_equal_boolean():
    """Booleans fall through to the final equality check."""
    assert _values_equal(True, True) is True
    assert _values_equal(True, False) is False


# ---------------------------------------------------------------------------
# Lines 373-378 – _annot_passes_rule
# ---------------------------------------------------------------------------


def _make_rule(type_selector=None, value_selectors=None, page_numbers=None):
    from unittest.mock import MagicMock

    r = MagicMock()
    r.type_selector = type_selector
    r.value_selectors = value_selectors or []
    r.page_numbers = page_numbers or [1]
    return r


def test_annot_passes_rule_type_mismatch():
    """Rule type_selector doesn't match annotation Subtype → False (line 374)."""
    annot = {"Page": 1, "AnnotationIndex": 1, "Properties": {"/Subtype": "/Text"}}
    rule = _make_rule(type_selector="/Link")
    assert _annot_passes_rule(annot, rule) is False


def test_annot_passes_rule_value_selectors_false():
    """Rule has value_selectors that don't match → False (line 377)."""
    annot = {"Page": 1, "AnnotationIndex": 1, "Properties": {"/Subtype": "/Text"}}
    rule = _make_rule(type_selector="/Text", value_selectors=[("Contents", "(Nope)")])
    assert _annot_passes_rule(annot, rule) is False


def test_annot_passes_rule_no_selectors():
    """No type or value selectors → True for any annotation (line 378)."""
    annot = {"Page": 1, "AnnotationIndex": 1, "Properties": {"/Subtype": "/Text"}}
    rule = _make_rule()
    assert _annot_passes_rule(annot, rule) is True


# ---------------------------------------------------------------------------
# Lines 395, 400-401 – _get_all_annots_data with rules (page + rule filtering)
# ---------------------------------------------------------------------------


def test_get_all_annots_data_with_rules_filters_pages(simple_pdf):
    """
    When rules restrict to specific pages, only those pages are visited
    (line 395 – `included_pages` check) and only matching annotations are
    returned (lines 400-401 – list comprehension filter).
    """
    from pdftl.operations.parsers.modify_annots_parser import specs_to_selection_rules

    # Add a second page with a Link annotation
    page2 = simple_pdf.add_blank_page()
    link_annot = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Link,
        Rect=[0, 0, 20, 20],
    )
    page2.Annots = simple_pdf.make_indirect(pikepdf.Array([link_annot]))

    # Rule: only /Text annotations on page 1
    rules = specs_to_selection_rules(["1/Text"], len(simple_pdf.pages))
    data = _get_all_annots_data(simple_pdf, compat=False, rules=rules)

    assert len(data) == 1
    assert data[0]["Page"] == 1
    assert data[0]["Properties"]["/Subtype"] == "/Text"


def test_get_all_annots_data_rules_exclude_non_matching(simple_pdf):
    """
    A page is included but the type_selector doesn't match any annotation →
    the per-page filter (lines 400-401) returns an empty list for that page.
    """
    from pdftl.operations.parsers.modify_annots_parser import specs_to_selection_rules

    rules = specs_to_selection_rules(["1/Link"], len(simple_pdf.pages))
    data = _get_all_annots_data(simple_pdf, compat=False, rules=rules)

    # simple_pdf page 1 has Text + Highlight, no Link → empty result
    assert data == []


# ---------------------------------------------------------------------------
# Lines 500-501 – _data_item_to_string_helper with string_convert_maybe=None
# ---------------------------------------------------------------------------


def test_data_item_to_string_helper_no_string_convert():
    """
    Passing string_convert_maybe=None should use an identity function
    internally (lines 500-501) and not crash.
    """
    result = _data_item_to_string_helper("/Contents", "Hello World", "Annot", None)
    assert result == "AnnotContents: Hello World"


def test_data_item_to_string_helper_no_string_convert_name_value():
    """Same None path but with a Name-style value (leading slash stripped)."""
    result = _data_item_to_string_helper("/Subtype", "/Text", "Annot", None)
    assert result == "AnnotSubtype: Text"


import pytest
from unittest.mock import MagicMock, patch
from pdftl.core.core_types import OpResult
from pdftl.operations.annots_filters import dump_annots, delete_annots, _values_equal


# Minimal structural mocks to simulate pikepdf structures
class MockObj:
    def __init__(self, objgen):
        self.objgen = objgen


class MockPage:
    def __init__(self, objgen, annots=None):
        self.obj = MockObj(objgen)
        if annots is not None:
            self.Annots = annots


class MockPdf:
    def __init__(self, pages):
        self.pages = pages


# === 1. Covers Line 179 ===
@patch("pdftl.operations.parsers.modify_annots_parser.specs_to_selection_rules")
@patch("pdftl.operations.annots_filters._get_all_annots_data")
def test_dump_annots_with_specs(mock_get_all, mock_specs_to_rules):
    """Triggers line 179 by providing the optional `specs` parameter."""
    mock_pdf = MockPdf([MockPage((1, 0))])
    mock_specs_to_rules.return_value = []
    mock_get_all.return_value = []

    res = dump_annots(mock_pdf, specs=["1-5/Link"])

    mock_specs_to_rules.assert_called_once_with(["1-5/Link"], 1)
    assert res.success is True


# === 2. Covers Lines 298-301 ===
def test_delete_annots_no_specs_wipe_all():
    """Triggers lines 298-301 by providing an empty/None specs argument."""
    page1 = MockPage((1, 0), annots=[1, 2])
    page2 = MockPage((2, 0))  # Test page without an Annots attribute
    mock_pdf = MockPdf([page1, page2])

    res = delete_annots(mock_pdf, specs=None)

    assert res.success is True
    assert page1.Annots == []
    assert not hasattr(page2, "Annots")


# === 3. Covers Lines 312 and 314 ===
@patch("pdftl.operations.parsers.modify_annots_parser.specs_to_selection_rules")
def test_delete_annots_with_filters_skips(mock_specs_to_rules):
    """Triggers lines 312 (page exclusion) and 314 (missing Annots attribute)."""

    class MockRule:
        def __init__(self, page_numbers):
            self.page_numbers = page_numbers
            self.type_selector = None
            self.value_selectors = None

    # Filter rule target only pages 2 and 3
    rule = MockRule(page_numbers={2, 3})
    mock_specs_to_rules.return_value = [rule]

    page1 = MockPage((1, 0), annots=[1])  # Page 1: Not in included_pages -> triggers Line 312
    page2 = MockPage((2, 0))  # Page 2: Included but lacks Annots -> triggers Line 314
    page3 = MockPage((3, 0), annots=[2])  # Page 3: Included with Annots -> fully processed

    mock_pdf = MockPdf([page1, page2, page3])

    res = delete_annots(mock_pdf, specs=["some_filter"])
    assert res.success is True
    assert page3.Annots == []  # Fast path wipes page 3


# === 4. Covers Line 368 ===
def test_values_equal_fallback():
    """Triggers line 368 fallback comparison for non-string/numeric/list values."""
    # Mismatched types fall straight to line 368
    assert _values_equal("abc", 123) is False

    # None types fall straight to line 368
    assert _values_equal(None, None) is True
