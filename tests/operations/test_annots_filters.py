import logging
from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.core.core_types import OpResult
from pdftl.operations.annots_filters import (
    _annot_matches_filters,
    _annot_passes_rule,
    _data_item_to_string_helper,
    _delete_annots_in_page,
    _get_all_annots_data,
    _get_nested_prop,
    _key_value_lines,
    _lines_from_datum,
    _values_equal,
    delete_annots,
    dump_annots,
    dump_annots_cli_hook,
    dump_data_annots,
    dump_data_annots_cli_hook,
)

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
# _get_all_annots_data
# ---------------------------------------------------------------------------


def test_get_all_annots_data_compat_true_with_rules(simple_pdf):
    """
    _get_all_annots_data is normally called either with compat=True (no rules)
    or compat=False (with rules). Calling it with both exercises the branch
    where `included_pages` is built from rules while compat=True is also set.
    """
    from pdftl.operations.parsers.modify_annots_parser import specs_to_selection_rules

    rules = specs_to_selection_rules(["1/Text"], len(simple_pdf.pages))
    data = _get_all_annots_data(simple_pdf, compat=True, rules=rules)

    # Only the /Text annotation should survive the filter
    assert len(data) == 1
    assert data[0]["Properties"]["/Subtype"] == "/Text"


def test_get_all_annots_data_with_rules_filters_pages(simple_pdf):
    """
    When rules restrict to specific pages, only those pages are visited,
    and only matching annotations are returned.
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
    A page is included but the type_selector doesn't match any annotation ->
    the per-page filter returns an empty list for that page.
    """
    from pdftl.operations.parsers.modify_annots_parser import specs_to_selection_rules

    rules = specs_to_selection_rules(["1/Link"], len(simple_pdf.pages))
    data = _get_all_annots_data(simple_pdf, compat=False, rules=rules)

    # simple_pdf page 1 has Text + Highlight, no Link -> empty result
    assert data == []


def test_get_all_annots_data_empty_rules_list(simple_pdf):
    """rules=[] is falsy, so included_pages stays None (no filtering at all)."""
    data = _get_all_annots_data(simple_pdf, compat=False, rules=[])
    # No rules means no page/annot filtering — both annots on page 1 come back
    assert len(data) == 2


def test_get_all_annots_data_with_named_dests(simple_pdf):
    """Covers the branch where the PDF has a /Names/Dests tree."""
    from pikepdf import Array, Dictionary, Name, String

    pdf = simple_pdf
    dest_array = Array([pdf.pages[0].obj, Name.Fit])
    names_array = Array([String("MyDest"), dest_array])
    dests_dict = pdf.make_indirect(Dictionary(Names=names_array))
    pdf.Root.Names = pdf.make_indirect(Dictionary(Dests=dests_dict))

    # Just needs to not crash and still return annotation data
    data = _get_all_annots_data(pdf, compat=False)
    assert len(data) == 2


# ---------------------------------------------------------------------------
# _lines_from_datum / _key_value_lines / _data_item_to_string_helper
# ---------------------------------------------------------------------------


def test_lines_from_datum_compat_false_includes_short_key_and_index():
    """
    With compat=False every property key is processed (including short ones
    that would normally be skipped). The /C key (len == 2) is short and is
    handled by _key_value_lines returning [] — this exercises the branch
    where compat=False allows the loop to run even for short keys (they are
    then silently dropped inside _key_value_lines).
    Also asserts that AnnotIndexInPage is emitted.
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

    assert any("AnnotIndexInPage: 7" in l for l in lines)


def test_lines_from_datum_no_subtype_key():
    datum = {"Page": 1, "AnnotationIndex": 1, "Properties": {"/Rect": [0, 0, 1, 1]}}
    assert _lines_from_datum(datum, str) == []


def test_lines_from_datum_skips_javascript_action():
    datum = {
        "Page": 1,
        "AnnotationIndex": 1,
        "Properties": {
            "/Subtype": "/Link",
            "/A": {"/S": "/JavaScript", "/JS": "app.alert('hi')"},
        },
    }
    assert _lines_from_datum(datum, str) == []


def test_key_value_lines_action_dict():
    """'/A' values are expanded into one line per action key."""
    lines = _key_value_lines("/A", {"/S": "/URI", "/URI": "http://example.com"}, "Annot", str)
    assert "AnnotActionSubtype: URI" in lines
    assert "AnnotActionURI: http://example.com" in lines


def test_key_value_lines_handles_not_implemented_error(caplog):
    """A string_convert that raises NotImplementedError is caught and
    logged, yielding no lines for that key."""

    def bad_string_convert(_x):
        raise NotImplementedError("unsupported")

    with caplog.at_level(logging.WARNING):
        lines = _key_value_lines("/LongKey", "value", "Annot", bad_string_convert, compat=False)

    assert lines == []
    assert "Skipping unsupported annotation key" in caplog.text


def test_data_item_to_string_helper_no_string_convert():
    """
    Passing string_convert_maybe=None should use an identity function
    internally and not crash.
    """
    result = _data_item_to_string_helper("/Contents", "Hello World", "Annot", None)
    assert result == "AnnotContents: Hello World"


def test_data_item_to_string_helper_no_string_convert_name_value():
    """Same None path but with a Name-style value (leading slash stripped)."""
    result = _data_item_to_string_helper("/Subtype", "/Text", "Annot", None)
    assert result == "AnnotSubtype: Text"


def test_data_item_to_string_helper_key_s_becomes_subtype():
    """Key 'S' is renamed to 'Subtype'."""
    result = _data_item_to_string_helper("S", "/URI", "AnnotAction", str)
    assert result == "AnnotActionSubtype: URI"


def test_data_item_to_string_helper_renames_s_key():
    """Same rename, but via the string_convert_maybe=None path."""
    result = _data_item_to_string_helper("/S", "/URI", "AnnotAction", None)
    assert result == "AnnotActionSubtype: URI"


# ---------------------------------------------------------------------------
# dump_data_annots / dump_data_annots_cli_hook / _generate_pdftk_annots_report
# ---------------------------------------------------------------------------


def test_dump_data_annots_cli_hook_no_data(caplog):
    """
    When result.data is None/empty the hook should log a warning and return
    early without crashing.
    """
    result = OpResult(success=True, data=None, meta={})

    with caplog.at_level(logging.WARNING):
        dump_data_annots_cli_hook(result, None, None)

    assert "No data available" in caplog.text


def test_dump_data_annots_no_uri_base(simple_pdf, capsys):
    """
    When the PDF has no Root.URI.Base, the report must not contain
    'PdfUriBase'.
    """
    result = dump_data_annots(simple_pdf, output_file=None)
    assert result.success
    assert "PdfUriBase" not in result.data

    dump_data_annots_cli_hook(result, None, None)
    out = capsys.readouterr().out
    assert "PdfUriBase" not in out
    assert "NumberOfPages: 1" in out


def test_dump_data_annots_with_uri_base(simple_pdf):
    """dump_data_annots includes PdfUriBase when present on the PDF Root."""
    simple_pdf.Root.URI = pikepdf.Dictionary(Base=pikepdf.String("http://example.com/"))
    result = dump_data_annots(simple_pdf)
    assert result.data["PdfUriBase"] == "http://example.com/"


def test_generate_pdftk_annots_report_with_uri_base():
    """PdfUriBase, when present, is included in the first block of the
    pdftk-style report."""
    from pdftl.operations.annots_filters import _generate_pdftk_annots_report

    data = {
        "NumberOfPages": 1,
        "PdfUriBase": "http://example.com/",
        "Annotations": [],
    }
    report = _generate_pdftk_annots_report(data)
    assert "PdfUriBase: http://example.com/" in report


# ---------------------------------------------------------------------------
# dump_annots / dump_annots_cli_hook
# ---------------------------------------------------------------------------


def test_dump_annots_cli_hook_writes_json(simple_pdf, tmp_path):
    """dump_annots_cli_hook serializes result.data to a compacted JSON
    string and writes it with a trailing newline."""
    output_file = tmp_path / "annots.json"
    result = dump_annots(simple_pdf, output_file=str(output_file))
    dump_annots_cli_hook(result, None, None)

    text = output_file.read_text()
    assert '"annotations"' in text
    assert text.endswith("\n")


def test_dump_annots_no_specs(simple_pdf):
    """dump_annots without specs skips rule building and returns all
    annotations, unfiltered."""
    result = dump_annots(simple_pdf)
    assert result.success is True
    assert len(result.data) == 2


def test_dump_annots_no_specs_skips_rule_parsing():
    """Covers the False branch of 'if specs:' — specs=None."""
    pdf = pikepdf.new()
    pdf.add_blank_page()
    result = dump_annots(pdf, specs=None)
    assert result.success is True


def test_dump_annots_invalid_spec_raises_invalid_argument_error():
    """Covers the except ValueError -> InvalidArgumentError branch."""
    from pdftl.exceptions import InvalidArgumentError

    pdf = pikepdf.new()
    pdf.add_blank_page()
    with pytest.raises(InvalidArgumentError):
        dump_annots(pdf, specs=["/Link(Border=null"])  # unclosed paren -> ValueError


# ---------------------------------------------------------------------------
# delete_annots / _delete_annots_in_page
# ---------------------------------------------------------------------------


def test_delete_annots_in_page_fast_path_wipe(simple_pdf):
    """
    A rule with no type_selector and no value_selectors triggers the
    fast-path that sets page.Annots = [] directly.
    """
    page = simple_pdf.pages[0]
    assert len(page.Annots) == 2

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
    the per-annotation loop.
    """
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


def test_delete_annots_invalid_spec_raises_invalid_argument_error():
    from pdftl.exceptions import InvalidArgumentError

    pdf = pikepdf.new()
    pdf.add_blank_page()
    with pytest.raises(InvalidArgumentError):
        delete_annots(pdf, specs=["/Link(Border=null"])  # unclosed paren -> ValueError


# ---------------------------------------------------------------------------
# _annot_matches_filters
# ---------------------------------------------------------------------------


def test_annot_matches_filters_key_missing():
    """Key not present in props -> returns False."""
    result = _annot_matches_filters({"/Subtype": "/Text"}, [("Rect", "[0 0 10 10]")])
    assert result is False


def test_annot_matches_filters_value_mismatch():
    """Key present but value differs -> returns False."""
    props = {"/Subtype": "/Text", "/Rect": [0, 0, 10, 10]}
    result = _annot_matches_filters(props, [("Rect", "[0 0 99 99]")])
    assert result is False


def test_annot_matches_filters_invalid_value_string():
    """
    _parse_value_to_python raises ValueError for a malformed value
    -> _annot_matches_filters returns False.
    """
    props = {"/Subtype": "/Text", "/Rect": [0, 0, 10, 10]}
    result = _annot_matches_filters(props, [("Rect", "[0 0 1")])  # Malformed array
    assert result is False


def test_annot_matches_filters_match():
    """All K=V pairs match -> returns True."""
    props = {"/Subtype": "/Text", "/Contents": "Hello"}
    result = _annot_matches_filters(props, [("Contents", "(Hello)")])
    assert result is True


def test_annot_matches_filters_nested_key_match():
    props = {"/Subtype": "/Link", "/A": {"/S": "/URI"}}
    assert _annot_matches_filters(props, [("A/S", "/URI")]) is True


def test_annot_matches_filters_nested_key_no_match():
    props = {"/Subtype": "/Link", "/A": {"/S": "/GoTo"}}
    assert _annot_matches_filters(props, [("A/S", "/URI")]) is False


def test_annot_matches_filters_exists_present():
    from pdftl.operations.parsers.modify_annots_parser import EXISTS

    props = {"/Subtype": "/Link", "/A": {"/S": "/URI"}}
    assert _annot_matches_filters(props, [("A", EXISTS)]) is True


def test_annot_matches_filters_exists_absent():
    from pdftl.operations.parsers.modify_annots_parser import EXISTS

    props = {"/Subtype": "/Link"}
    assert _annot_matches_filters(props, [("A", EXISTS)]) is False


def test_annot_matches_filters_exists_nested():
    from pdftl.operations.parsers.modify_annots_parser import EXISTS

    props = {"/Subtype": "/Link", "/A": {"/S": "/URI"}}
    assert _annot_matches_filters(props, [("A/S", EXISTS)]) is True
    assert _annot_matches_filters(props, [("A/URI", EXISTS)]) is False


# ---------------------------------------------------------------------------
# _get_nested_prop
# ---------------------------------------------------------------------------


def test_get_nested_prop_simple_key():
    assert _get_nested_prop({"/Rect": [0, 0, 1, 1]}, "Rect") == [0, 0, 1, 1]


def test_get_nested_prop_nested_key():
    props = {"/A": {"/S": "/URI", "/URI": "https://example.com"}}
    assert _get_nested_prop(props, "A/S") == "/URI"
    assert _get_nested_prop(props, "A/URI") == "https://example.com"


def test_get_nested_prop_missing_intermediate():
    assert _get_nested_prop({"/Subtype": "/Text"}, "A/S") is None


def test_get_nested_prop_list_index():
    props = {"/C": [1, 0, 0]}
    assert _get_nested_prop(props, "C/0") == 1
    assert _get_nested_prop(props, "C/9") is None  # out of range


def test_get_nested_prop_non_dict_intermediate():
    props = {"/Subtype": "/Text"}
    assert _get_nested_prop(props, "Subtype/S") is None  # "/Text" is a str, not indexable


# ---------------------------------------------------------------------------
# _values_equal
# ---------------------------------------------------------------------------


def test_values_equal_list_length_mismatch():
    """Lists of different lengths -> False."""
    assert _values_equal([1, 2], [1, 2, 3]) is False


def test_values_equal_list_same():
    """Lists with equal elements -> True."""
    assert _values_equal([1.0, 0.0], [1, 0]) is True


def test_values_equal_numeric_cross_type():
    """int vs float comparison."""
    assert _values_equal(1, 1.0) is True
    assert _values_equal(1, 2.0) is False


def test_values_equal_name_with_slash():
    """Name strings are compared after stripping leading slash."""
    assert _values_equal("/Text", "Text") is True
    assert _values_equal("Text", "/Text") is True
    assert _values_equal("/Text", "/Link") is False


def test_values_equal_plain_strings():
    """Non-name strings use direct equality."""
    assert _values_equal("hello", "hello") is True
    assert _values_equal("hello", "world") is False


def test_values_equal_boolean():
    """Booleans fall through to the final equality check."""
    assert _values_equal(True, True) is True
    assert _values_equal(True, False) is False


def test_values_equal_fallback():
    """Triggers the fallback comparison for non-string/numeric/list values."""
    # Mismatched types fall straight through to plain equality
    assert _values_equal("abc", 123) is False

    # None types fall straight through to plain equality
    assert _values_equal(None, None) is True


# ---------------------------------------------------------------------------
# _annot_passes_rule
# ---------------------------------------------------------------------------


def _make_rule(type_selector=None, value_selectors=None, page_numbers=None):
    r = MagicMock()
    r.type_selector = type_selector
    r.value_selectors = value_selectors or []
    r.page_numbers = page_numbers or [1]
    return r


def test_annot_passes_rule_type_mismatch():
    """Rule type_selector doesn't match annotation Subtype -> False."""
    annot = {"Page": 1, "AnnotationIndex": 1, "Properties": {"/Subtype": "/Text"}}
    rule = _make_rule(type_selector="/Link")
    assert _annot_passes_rule(annot, rule) is False


def test_annot_passes_rule_value_selectors_false():
    """Rule has value_selectors that don't match -> False."""
    annot = {"Page": 1, "AnnotationIndex": 1, "Properties": {"/Subtype": "/Text"}}
    rule = _make_rule(type_selector="/Text", value_selectors=[("Contents", "(Nope)")])
    assert _annot_passes_rule(annot, rule) is False


def test_annot_passes_rule_no_selectors():
    """No type or value selectors -> True for any annotation."""
    annot = {"Page": 1, "AnnotationIndex": 1, "Properties": {"/Subtype": "/Text"}}
    rule = _make_rule()
    assert _annot_passes_rule(annot, rule) is True


# ---------------------------------------------------------------------------
# Mocked-pdf tests
# ---------------------------------------------------------------------------


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


@patch("pdftl.operations.annots_filters.specs_to_selection_rules")
@patch("pdftl.operations.annots_filters._get_all_annots_data")
def test_dump_annots_with_specs(mock_get_all, mock_specs_to_rules):
    """Providing the optional `specs` parameter triggers rule parsing."""
    mock_pdf = MockPdf([MockPage((1, 0))])
    mock_specs_to_rules.return_value = []
    mock_get_all.return_value = []

    res = dump_annots(mock_pdf, specs=["1-5/Link"])

    mock_specs_to_rules.assert_called_once_with(["1-5/Link"], 1)
    assert res.success is True


def test_delete_annots_no_specs_wipe_all():
    """Providing an empty/None specs argument wipes all annotations."""
    page1 = MockPage((1, 0), annots=[1, 2])
    page2 = MockPage((2, 0))  # Test page without an Annots attribute
    mock_pdf = MockPdf([page1, page2])

    res = delete_annots(mock_pdf, specs=None)

    assert res.success is True
    assert page1.Annots == []
    assert not hasattr(page2, "Annots")


@patch("pdftl.operations.annots_filters.specs_to_selection_rules")
def test_delete_annots_with_filters_skips(mock_specs_to_rules):
    """Triggers page exclusion and the missing-Annots-attribute branch."""

    class MockRule:
        def __init__(self, page_numbers):
            self.page_numbers = page_numbers
            self.type_selector = None
            self.value_selectors = None

    # Filter rule target only pages 2 and 3
    rule = MockRule(page_numbers={2, 3})
    mock_specs_to_rules.return_value = [rule]

    page1 = MockPage((1, 0), annots=[1])  # Page 1: not in included_pages
    page2 = MockPage((2, 0))  # Page 2: included but lacks Annots
    page3 = MockPage((3, 0), annots=[2])  # Page 3: included with Annots

    mock_pdf = MockPdf([page1, page2, page3])

    res = delete_annots(mock_pdf, specs=["some_filter"])
    assert res.success is True
    assert page3.Annots == []  # Fast path wipes page 3
