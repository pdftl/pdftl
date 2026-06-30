# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/helpers/test_tags_tree_walker.py

"""Tests for the logical structure tree walker utility."""

from __future__ import annotations

import sys
from unittest.mock import patch, MagicMock

import pikepdf

from pdftl.operations.helpers.tags_tree_walker import (
    _build_header_parts,
    _build_page_objgen_index,
    _build_role_map,
    _collect_tree_roots,
    _elem_tag,
    _elem_text_attrs,
    _format_role,
    _is_mcr,
    _is_struct_elem,
    _iter_k,
    _mcid_from_item,
    _reading_order_lines,
    _resolve_page_num,
    _resolve_tag,
    _run_tree,
    _stm_from_item,
    _tree_node,
)


# ---------------------------------------------------------------------------
# Core Utilities and Extraction Helpers
# ---------------------------------------------------------------------------


def test_build_page_objgen_index() -> None:
    """Verify builder correctly maps page index generations."""
    pdf = pikepdf.Pdf.new()
    p1 = pdf.add_blank_page()
    p2 = pdf.add_blank_page()

    index = _build_page_objgen_index(pdf)
    assert index == {p1.obj.objgen: 1, p2.obj.objgen: 2}


def test_build_role_map() -> None:
    """Verify role map extraction from StructTreeRoot."""
    pdf = pikepdf.Pdf.new()
    assert _build_role_map(pdf) == {}

    pdf.Root.StructTreeRoot = pikepdf.Dictionary()
    assert _build_role_map(pdf) == {}

    # Malformed root where RoleMap isn't a dict
    pdf.Root.StructTreeRoot.RoleMap = pikepdf.String("Malformed")
    assert _build_role_map(pdf) == {}

    # Valid RoleMap
    pdf.Root.StructTreeRoot.RoleMap = pikepdf.Dictionary(
        {"/custom": pikepdf.Name("/P"), "/chapter": pikepdf.Name("/H1")}
    )

    role_map = _build_role_map(pdf)
    assert role_map == {"custom": "P", "chapter": "H1"}


def test_build_role_map_exceptions() -> None:
    """Cover non-dict roots and exception handling during role map string coercion."""
    # Cover Line 47: root is not dict-like (lacks get attribute)
    pdf = pikepdf.Pdf.new()
    pdf.Root.StructTreeRoot = 42
    assert _build_role_map(pdf) == {}

    # Cover Lines 55-57: TypeError during role_map item string coercion
    class BadStr:
        def __str__(self) -> str:
            raise TypeError("force string cast failure")

    mock_pdf = MagicMock()
    mock_root = MagicMock()
    mock_root.get.return_value = {"/good": "/P", "/bad": BadStr()}
    mock_pdf.Root.StructTreeRoot = mock_root

    # This should extract "good" -> "P" and silently skip "bad"
    assert _build_role_map(mock_pdf) == {"good": "P"}


def test_format_role() -> None:
    """Test formatting of role attributes, quoting when necessary."""
    assert _format_role("text-unit") == "role=text-unit"
    assert _format_role("text unit") == 'role="text unit"'
    assert _format_role('speaker"one') == 'role="speaker"one"'


def test_resolve_tag() -> None:
    """Test standard, mapped, and unmapped tag resolution."""
    role_map = {"chapter": "H1", "widget": "Div"}

    # Standard tag passes through unchanged
    assert _resolve_tag("P", role_map) == ("P", None)
    assert _resolve_tag("H1", role_map) == ("H1", None)

    # Mapped custom tag
    assert _resolve_tag("chapter", role_map) == ("H1", "chapter")

    # Unmapped custom tag
    assert _resolve_tag("unknown-custom", role_map) == ("unknown", "unknown-custom")


def test_build_header_parts() -> None:
    """Verify header generation correctly interweaves standard tag and mapped role."""
    # 1. Standard tag, no role, no page
    assert _build_header_parts("P", None, None, {}, True, 0) == ["[P]"]

    # 2. Standard tag, mapped role, page, alt text, children
    res = _build_header_parts("H1", "chapter", 3, {"alt": "Intro"}, False, 2)
    assert res == ["[H1]", "page=3", "role=chapter", 'alt="Intro"', "(2 children)"]

    # 3. Quoted role
    res2 = _build_header_parts("unknown", "weird role", 1, {}, True, 0)
    assert res2 == ["[unknown]", "page=1", 'role="weird role"']


def test_resolve_page_num() -> None:
    """Test resolving structural references to page index numbers."""
    pdf = pikepdf.Pdf.new()
    p1 = pdf.add_blank_page()
    elem = pikepdf.Dictionary({"/Pg": p1.obj})

    index = {p1.obj.objgen: 1}
    assert _resolve_page_num(elem, index) == 1
    assert _resolve_page_num(pikepdf.Dictionary(), index) is None


def test_elem_tag() -> None:
    """Verify structural tag parsing cleans forward slashes."""
    elem = pikepdf.Dictionary({"/S": pikepdf.Name("/H1")})
    assert _elem_tag(elem) == "H1"
    assert _elem_tag(pikepdf.Dictionary()) == "(unknown)"


def test_elem_text_attrs_success() -> None:
    """Test text attribute retrieval converts strings safely."""
    elem = pikepdf.Dictionary({"/Alt": "Alternative Text", "/Lang": "en-US"})
    attrs = _elem_text_attrs(elem)

    assert attrs["alt"] == "Alternative Text"
    assert attrs["lang"] == "en-US"
    assert "actual_text" not in attrs


def test_elem_text_attrs_exceptions() -> None:
    """Test attribute retrieval silently catches un-castable and missing values."""
    # Integers and raw values lack the .get method, triggering silent exception handling.
    assert _elem_text_attrs(123) == {}


def test_iter_k_variations() -> None:
    """Verify /K values of all structural shapes normalize to flat lists."""
    # Case 1: None
    assert _iter_k(pikepdf.Dictionary()) == []

    # Case 2: Array
    arr = pikepdf.Array([1, 2])
    elem = pikepdf.Dictionary({"/K": arr})
    assert _iter_k(elem) == [1, 2]

    # Case 3: Single Item
    elem2 = pikepdf.Dictionary({"/K": 5})
    assert _iter_k(elem2) == [5]


def test_is_mcr() -> None:
    """Check MCR type evaluator verifies type labels and gracefully swallows errors."""
    assert _is_mcr(pikepdf.Dictionary({"/Type": pikepdf.Name("/MCR")})) is True
    assert _is_mcr(pikepdf.Dictionary({"/Type": pikepdf.Name("/StructElem")})) is False
    assert _is_mcr(123) is False  # Triggers TypeError


def test_mcid_from_item() -> None:
    """Test extracting integer MCID values from items, including type/import errors."""
    assert _mcid_from_item(15) == 15
    assert _mcid_from_item(pikepdf.Dictionary({"/MCID": 42})) == 42

    # Trigger ValueError handlers inside extraction
    assert _mcid_from_item(pikepdf.Dictionary({"/MCID": pikepdf.Name("/NotInt")})) is None
    assert _mcid_from_item(12.5) is None


def test_stm_from_item() -> None:
    """Verify stream identifier extraction from structural element maps."""
    assert _stm_from_item(12) is None

    pdf = pikepdf.Pdf.new()
    stm = pikepdf.Stream(pdf, b"")
    assert _stm_from_item(pikepdf.Dictionary({"/Stm": stm})) == stm

    assert _stm_from_item(123) is None  # Triggers TypeError


def test_is_struct_elem() -> None:
    """Verify type constraints confirm object counts as struct elements."""
    assert _is_struct_elem(pikepdf.Dictionary({"/Type": pikepdf.Name("/StructElem")})) is True
    assert _is_struct_elem(pikepdf.Dictionary({"/S": pikepdf.Name("/P")})) is True
    assert _is_struct_elem(pikepdf.Dictionary()) is False
    assert _is_struct_elem(123) is False  # Triggers TypeError


def test_collect_tree_roots() -> None:
    """Test tree root accumulation over missing and valid configurations."""
    pdf = pikepdf.Pdf.new()
    assert _collect_tree_roots(pdf) == []

    pdf.Root.StructTreeRoot = pikepdf.Dictionary()
    assert _collect_tree_roots(pdf) == []

    pdf.Root.StructTreeRoot.K = pikepdf.Array([pikepdf.Dictionary(), pikepdf.Dictionary()])
    assert len(_collect_tree_roots(pdf)) == 2

    pdf.Root.StructTreeRoot.K = pikepdf.Dictionary()
    assert len(_collect_tree_roots(pdf)) == 1


# ---------------------------------------------------------------------------
# Tree Builder Tests
# ---------------------------------------------------------------------------


def test_tree_node_variations() -> None:
    """Test JSON/YAML mapping covering circular refs, MCIDs, XObjects, and missing objgens."""
    pdf = pikepdf.Pdf.new()

    # Make the root dictionary indirect immediately to prevent inline cloning
    # and allow proper reference-cycle creation.
    root = pdf.make_indirect(pikepdf.Dictionary({"/S": pikepdf.Name("/Div")}))

    c1_mcid = 10
    c2_struct = pikepdf.Dictionary({"/S": pikepdf.Name("/Span")})
    c3_circ = root  # Circular reference to our indirect root object

    # Inline dictionaries not attached to a PDF lack an objgen property
    c4_no_obj = pikepdf.Dictionary({"/S": pikepdf.Name("/H1")})

    stm = pikepdf.Stream(pdf, b"")
    c5_xobj = pikepdf.Dictionary({"/MCID": 42, "/Stm": stm})

    root.K = pikepdf.Array([c1_mcid, c2_struct, c3_circ, c4_no_obj, c5_xobj])

    # Assigning to the document tree sets up the root structural tree element
    pdf.Root.StructTreeRoot = root

    node = _tree_node(root, {})

    assert node["tag"] == "Div"
    assert len(node["children"]) == 5
    assert node["children"][0] == {"mcid": 10}
    assert node["children"][1]["tag"] == "Span"
    assert node["children"][2] == {
        "type": "CircularReference",
        "id": f"{root.objgen[0]}:{root.objgen[1]}",
    }
    assert node["children"][3]["tag"] == "H1"
    assert node["children"][4] == {
        "mcid": 42,
        "xobject_objgen": f"{stm.objgen[0]}:{stm.objgen[1]}",
    }


def test_tree_node_mcr_pg_fallback() -> None:
    """Ensure tree node captures /Pg directly from the MCR dict if present."""
    pdf = pikepdf.Pdf.new()
    p1 = pdf.add_blank_page()
    page_index = {p1.obj.objgen: 1}

    # Parent without /Pg
    root = pdf.make_indirect(pikepdf.Dictionary({"/S": pikepdf.Name("/Div")}))
    # MCR child with its own /Pg
    mcr = pikepdf.Dictionary({"/MCID": 42, "/Pg": p1.obj})
    root.K = pikepdf.Array([mcr])

    node = _tree_node(root, page_index)
    assert "page" not in node  # Parent correctly evaluates to no page
    assert len(node["children"]) == 1
    assert node["children"][0]["mcid"] == 42
    assert node["children"][0]["page"] == 1  # Child extracted its own page successfully


def test_tree_node_inherited_pg() -> None:
    """Ensure tree node inherits /Pg from a parent element when omitted locally."""
    pdf = pikepdf.Pdf.new()
    p1 = pdf.add_blank_page()
    page_index = {p1.obj.objgen: 1}

    # Grandparent with /Pg
    root = pdf.make_indirect(pikepdf.Dictionary({"/S": pikepdf.Name("/Sect"), "/Pg": p1.obj}))

    # Parent without /Pg
    child = pikepdf.Dictionary({"/S": pikepdf.Name("/P")})

    # MCR without /Pg
    mcr = pikepdf.Dictionary({"/MCID": 50})

    child.K = pikepdf.Array([mcr])
    root.K = pikepdf.Array([child])

    node = _tree_node(root, page_index)

    assert node["page"] == 1
    assert node["children"][0]["tag"] == "P"
    assert node["children"][0]["page"] == 1
    assert node["children"][0]["children"][0]["mcid"] == 50
    assert node["children"][0]["children"][0]["page"] == 1


def test_tree_node_role() -> None:
    """Ensure role_map mappings are properly assigned inside tree node dictionaries."""
    # Line 340 coverage
    pdf = pikepdf.Pdf.new()
    root = pdf.make_indirect(pikepdf.Dictionary({"/S": pikepdf.Name("/custom")}))

    node = _tree_node(root, page_objgen_index={}, role_map={"custom": "P"})
    assert node["tag"] == "P"
    assert node["role"] == "custom"


def test_run_tree_integration() -> None:
    """Verify primary tree engine compiles root structures gracefully."""
    pdf = pikepdf.Pdf.new()
    pdf.Root.StructTreeRoot = pikepdf.Dictionary(
        {"/K": pikepdf.Dictionary({"/S": pikepdf.Name("/Document")})}
    )

    result = _run_tree(pdf, json_output=False, output_file=None)

    assert len(result) == 1
    assert result[0]["tag"] == "Document"


# ---------------------------------------------------------------------------
# Reading Order Iteration Tests
# ---------------------------------------------------------------------------


@patch("pdftl.operations.helpers.tags_tree_walker._build_mcid_stream_map")
def test_reading_order_lines_basic_and_streams(mock_build_map) -> None:
    """Test full reading line generator covering streams, metadata, and cache lookups."""
    pdf = pikepdf.Pdf.new()
    p1 = pdf.add_blank_page()

    span = pikepdf.Dictionary({"/S": pikepdf.Name("/Span")})
    p = pikepdf.Dictionary(
        {
            "/S": pikepdf.Name("/P"),
            "/Pg": p1.obj,
            "/Alt": "AltText",
            "/ActualText": "ActText",
            "/Lang": "EN",
            "/K": pikepdf.Array([1, 2, span]),
        }
    )
    pdf.Root.StructTreeRoot = pikepdf.Dictionary({"/K": p})

    mock_build_map.return_value = (
        {1: {"start_line": 10, "end_line": 11, "lines": ["stream text"]}},
        ["stream text"],
    )

    # Test 1: Full streaming
    lines = _reading_order_lines(pdf, {1}, annotate=False, show_streams=True)
    out = "\n".join(lines)

    # Cleaned up assertions to use single space characters matching production
    assert '[P] page=1 alt="AltText" actual="ActText" lang=EN (1 children)' in out
    assert "[MCID 1]  stream_lines=10-11\n    stream text" in out
    assert "[MCID 2]  (not found in page 1 stream — may be in an XObject)" in out
    assert "[Span]" in out

    # Test 2: Streams disabled fallback
    lines_no_streams = _reading_order_lines(pdf, {1}, annotate=False, show_streams=False)
    out_no_streams = "\n".join(lines_no_streams)

    assert "[MCID 1]  stream_lines=10-11" in out_no_streams
    assert "stream text" not in out_no_streams
    assert "[MCID 2]  (not in page stream)" in out_no_streams


@patch("pdftl.operations.helpers.tags_tree_walker._build_mcid_stream_map")
def test_reading_order_lines_rolemap_and_mcr_pg(mock_build_map) -> None:
    """Test reading order lines threading role map and handling MCR /Pg."""
    pdf = pikepdf.Pdf.new()
    p1 = pdf.add_blank_page()

    # RoleMap mapping 'chapter' -> 'H1'
    pdf.Root.StructTreeRoot = pikepdf.Dictionary(
        {"/RoleMap": pikepdf.Dictionary({"/chapter": pikepdf.Name("/H1")})}
    )

    # MCR with its own Pg
    mcr = pikepdf.Dictionary({"/MCID": 1, "/Pg": p1.obj})

    # Parent StructElem (custom tag 'chapter', no Pg)
    chapter_elem = pikepdf.Dictionary({"/S": pikepdf.Name("/chapter"), "/K": pikepdf.Array([mcr])})
    pdf.Root.StructTreeRoot.K = pikepdf.Array([chapter_elem])

    mock_build_map.return_value = (
        {1: {"start_line": 10, "end_line": 11, "lines": ["chapter stream"]}},
        ["chapter stream"],
    )

    lines = _reading_order_lines(pdf, {1}, annotate=False, show_streams=True)
    out = "\n".join(lines)

    # Ensure RoleMap resolved it to [H1] role=chapter
    assert "[H1] role=chapter" in out
    # Ensure MCR /Pg allowed it to find the stream content on page 1
    assert "[MCID 1]  stream_lines=10-11" in out


@patch("pdftl.operations.helpers.tags_tree_walker.parse_stream_bytes_for_mcids")
@patch("pdftl.operations.helpers.xobject_helpers.read_xobject_stream")
def test_reading_order_lines_xobj_processing(mock_read_xobj, mock_parse) -> None:
    """Cover Form XObject streaming pathways, disabled paths, and parsing exceptions."""
    pdf = pikepdf.Pdf.new()
    p1 = pdf.add_blank_page()

    stm_valid = pikepdf.Stream(pdf, b"valid")
    stm_invalid = pikepdf.Stream(pdf, b"invalid")

    mcr_valid = pikepdf.Dictionary({"/MCID": 42, "/Stm": stm_valid})
    mcr_invalid = pikepdf.Dictionary({"/MCID": 43, "/Stm": stm_invalid})

    fig = pikepdf.Dictionary(
        {
            "/S": pikepdf.Name("/Figure"),
            "/Pg": p1.obj,
            "/K": pikepdf.Array([mcr_valid, mcr_invalid]),
        }
    )
    pdf.Root.StructTreeRoot = pikepdf.Dictionary({"/K": fig})

    # Simulate parse_stream mapping succeeding for valid stream but failing for invalid
    mock_read_xobj.return_value = b"xobj bytes"
    mock_parse.side_effect = [
        ({42: {"start_line": 1, "end_line": 2, "lines": ["x_text"]}}, []),
        ValueError("Simulate un-parsable dictionary stream bytes"),
    ]

    # Test 1: Full XObject rendering
    lines = _reading_order_lines(pdf, {1}, annotate=False, show_streams=True)
    out = "\n".join(lines)

    valid_id = f"{stm_valid.objgen[0]}:{stm_valid.objgen[1]}"
    invalid_id = f"{stm_invalid.objgen[0]}:{stm_invalid.objgen[1]}"

    assert f"[MCID 42 in XObject {valid_id}]  stream_lines=1-2\n    x_text" in out
    assert f"[MCID 43 in XObject {invalid_id}]  (not found in XObject" in out

    # Test 2: Show Streams False logic branch for XObjects
    lines_no_streams = _reading_order_lines(pdf, {1}, annotate=False, show_streams=False)
    assert f"[MCID 42 in XObject {valid_id}]" in "\n".join(lines_no_streams)


@patch("pdftl.operations.helpers.tags_tree_walker.parse_stream_bytes_for_mcids")
@patch("pdftl.operations.helpers.stream_annotator.annotate_stream")
@patch("pdftl.operations.helpers.xobject_helpers.read_xobject_stream")
def test_reading_order_lines_xobj_annotation_failure(
    mock_read_xobj, mock_annotate, mock_parse
) -> None:
    """Verify that malformed XObject annotations securely fallback to bare bytes."""
    pdf = pikepdf.Pdf.new()
    p1 = pdf.add_blank_page()

    stm = pikepdf.Stream(pdf, b"bytes")
    mcr = pikepdf.Dictionary({"/MCID": 1, "/Stm": stm})

    div = pikepdf.Dictionary(
        {"/S": pikepdf.Name("/Div"), "/Pg": p1.obj, "/K": pikepdf.Array([mcr])}
    )
    pdf.Root.StructTreeRoot = pikepdf.Dictionary({"/K": div})

    mock_read_xobj.return_value = b"bytes"
    mock_annotate.side_effect = pikepdf.PdfError("Annotation malformed resources")
    mock_parse.return_value = ({1: {"start_line": 1, "end_line": 1, "lines": ["t"]}}, [])

    lines = _reading_order_lines(pdf, {1}, annotate=True, show_streams=True)
    out = "\n".join(lines)

    obj_id = f"{stm.objgen[0]}:{stm.objgen[1]}"
    assert f"[MCID 1 in XObject {obj_id}]  stream_lines=1-1" in out


def test_reading_order_lines_out_of_scope_and_unhashable() -> None:
    """Test dynamic page filtering avoids dumping parent roots while preserving their child paths."""
    pdf = pikepdf.Pdf.new()
    p1 = pdf.add_blank_page()
    p2 = pdf.add_blank_page()

    h1 = pikepdf.Dictionary({"/S": pikepdf.Name("/H1"), "/Pg": p1.obj})

    # Parent Document mapping to Page 2
    doc = pikepdf.Dictionary(
        {"/S": pikepdf.Name("/Document"), "/Pg": p2.obj, "/K": pikepdf.Array([h1])}
    )
    pdf.Root.StructTreeRoot = pikepdf.Dictionary({"/K": doc})

    # Request reading order exclusively for Page 1
    lines = _reading_order_lines(pdf, target_page_nums={1}, annotate=False, show_streams=False)
    out = "\n".join(lines)

    # Out of scope parent containers should not print headers natively
    assert "[Document]" not in out

    # But recursive tracking must traverse down into children appropriately
    assert "[H1]" in out


@patch("pdftl.operations.helpers.tags_tree_walker._build_mcid_stream_map")
def test_reading_order_lines_inherited_pg(mock_build_map) -> None:
    """Ensure reading order walk correctly inherits /Pg from ancestors."""
    pdf = pikepdf.Pdf.new()
    p1 = pdf.add_blank_page()

    # Grandparent with /Pg
    sect = pikepdf.Dictionary({"/S": pikepdf.Name("/Sect"), "/Pg": p1.obj})

    # Parent without /Pg
    p = pikepdf.Dictionary({"/S": pikepdf.Name("/P")})

    # MCR without /Pg
    mcr = pikepdf.Dictionary({"/MCID": 10})

    p.K = pikepdf.Array([mcr])
    sect.K = pikepdf.Array([p])
    pdf.Root.StructTreeRoot = pikepdf.Dictionary({"/K": sect})

    mock_build_map.return_value = (
        {10: {"start_line": 1, "end_line": 2, "lines": ["stream text"]}},
        ["stream text"],
    )

    lines = _reading_order_lines(pdf, {1}, annotate=False, show_streams=True)
    out = "\n".join(lines)

    # Both [Sect] and [P] should resolve as page=1
    assert "[Sect] page=1" in out
    assert "[P] page=1" in out
    assert "[MCID 10]  stream_lines=1-2" in out


def test_reading_order_lines_xobj_stm_no_objgen() -> None:
    """Verify that un-trackable XObject identifiers safely sidestep caching loops."""
    pdf = pikepdf.Pdf.new()
    p1 = pdf.add_blank_page()

    # Simulating a stream container with no true objgen identifier
    stm_inline = pikepdf.Dictionary()
    mcr = pikepdf.Dictionary({"/MCID": 1, "/Stm": stm_inline})

    div = pikepdf.Dictionary(
        {"/S": pikepdf.Name("/Div"), "/Pg": p1.obj, "/K": pikepdf.Array([mcr])}
    )
    pdf.Root.StructTreeRoot = pikepdf.Dictionary({"/K": div})

    lines = _reading_order_lines(pdf, {1}, annotate=False, show_streams=True)
    out = "\n".join(lines)

    # Fixed assertion to match the correct XObject formatting path
    assert "[MCID 1 in XObject unknown]  (not found in XObject unknown stream)" in out


# ---------------------------------------------------------------------------
# Uncovered Path Coverage Tests (Targeting 100% Coverage)
# ---------------------------------------------------------------------------


def test_resolve_page_num_edge_cases() -> None:
    """Test resolve page bounds and attribute boundaries."""
    # Line 42: No get attribute
    assert _resolve_page_num("not_dict", {}) is None

    # Lines 48-49: objgen access fails with AttributeError/ValueError on nested elements
    bad_elem = pikepdf.Dictionary({"/Pg": pikepdf.String("unhashable_value")})
    assert _resolve_page_num(bad_elem, {}) is None


def test_resolve_page_num_attribute_error_on_objgen() -> None:
    """Test _resolve_page_num raises AttributeError on pg.objgen."""

    # Specifically target Lines 48-49 with a custom object raising AttributeError
    class BadPg:
        @property
        def objgen(self):
            raise AttributeError("Accessing objgen fails")

    mock_elem = MagicMock()
    mock_elem.get.return_value = BadPg()
    assert _resolve_page_num(mock_elem, {}) is None


def test_elem_tag_edge_cases() -> None:
    """Test structural tag evaluation on objects lacking dictionary attributes."""
    # Line 54: No get attribute
    assert _elem_tag(99) == "(unknown)"


def test_elem_text_attrs_edge_cases() -> None:
    """Cover silent inner and outer exception limits in text attribute coercer."""

    class BadStr:
        def __str__(self) -> str:
            raise TypeError("Force inner string cast failure")

    # Lines 77-82: Inner exception handling within attribute key assignment loop
    mock_elem_inner = MagicMock()
    mock_elem_inner.get.side_effect = lambda key: BadStr() if key == "/Alt" else None
    assert _elem_text_attrs(mock_elem_inner) == {}

    # Lines 83-86: Outer exception handling when elem.get raises custom errors
    mock_elem_outer = MagicMock()
    mock_elem_outer.get.side_effect = ValueError("Outer method crash")
    assert _elem_text_attrs(mock_elem_outer) == {}


def test_iter_k_edge_cases() -> None:
    """Verify child list resolution skips direct object types lacking key/get interfaces."""
    # Line 103: No get attribute
    assert _iter_k(12345) == []


def test_mcid_from_item_pikepdf_integer() -> None:
    """Extract standard integer values from raw Integer proxies."""
    # Lines 130-132: Successful pikepdf Integer conversion path
    from pikepdf import Integer

    assert _mcid_from_item(Integer(100)) == 100

    # TypeError inside casting (Line 142)
    bad_mcid = pikepdf.Dictionary({"/MCID": pikepdf.Array()})
    assert _mcid_from_item(bad_mcid) is None


def test_mcid_from_item_import_error() -> None:
    """Test _mcid_from_item handles ImportError when importing Integer."""
    # Target Lines 130-132 block for ImportError coverage using sys.modules patching
    with patch.dict(sys.modules, {"pikepdf": None}):
        mock_item = MagicMock()
        mock_item.get.return_value = 42
        assert _mcid_from_item(mock_item) == 42


def test_mcid_from_item_pikepdf_integer_subclass_coverage() -> None:
    """Cover line 130 by mocking pikepdf.Integer as a non-int subclass."""

    class MockInteger:
        def __init__(self, val: int):
            self.val = val

        def __int__(self) -> int:
            return self.val

    # Patch the pikepdf.Integer class directly to verify type resolution during dynamic import
    with patch("pikepdf.Integer", MockInteger, create=True):
        item = MockInteger(100)
        # item is not a subclass of int, so isinstance(item, int) is False.
        # But isinstance(item, Integer) evaluates to True, hitting line 130.
        assert _mcid_from_item(item) == 100


def test_stm_from_item_exception_cases() -> None:
    """Test object reference retrieval handles missing property paths."""
    # Lines 155-158: Attribute exception pathway for objects lacking get operations
    assert _stm_from_item([]) is None


def test_collect_tree_roots_missing_get() -> None:
    """Ensure roots locator gracefully handles non-dictionary elements."""
    # Line 185: Root lacks a get attribute
    pdf = pikepdf.Pdf.new()
    pdf.Root.StructTreeRoot = 42
    assert _collect_tree_roots(pdf) == []


def test_tree_node_with_page_resolved() -> None:
    """Ensure structurally mapped page IDs correctly output into dictionary trees."""
    # Line 222: Page assignment path
    pdf = pikepdf.Pdf.new()
    p1 = pdf.add_blank_page()
    root = pdf.make_indirect(pikepdf.Dictionary({"/S": pikepdf.Name("/Div"), "/Pg": p1.obj}))

    page_index = {p1.obj.objgen: 1}
    node = _tree_node(root, page_index)
    assert node["page"] == 1


def test_tree_node_xobject_objgen_failure() -> None:
    """Ensure fallback path executes cleanly when stream lacks valid identity fields."""

    # Lines 233-236: Accessing objgen property throws AttributeError.
    class BadStream:
        @property
        def objgen(self):
            raise AttributeError("un-trackable stream")

    item = pikepdf.Dictionary({"/MCID": 50})
    root = pikepdf.Dictionary({"/S": pikepdf.Name("/Div"), "/K": pikepdf.Array([item])})

    with patch("pdftl.operations.helpers.tags_tree_walker._stm_from_item") as mock_stm_from_item:
        mock_stm_from_item.return_value = BadStream()
        node = _tree_node(root, {})

    assert len(node["children"]) == 1
    assert "xobject_objgen" not in node["children"][0]


def test_reading_order_lines_no_tree() -> None:
    """Ensure correct structural error output when parsing empty PDFs."""
    # Line 442: Empty root structures
    pdf = pikepdf.Pdf.new()
    lines = _reading_order_lines(pdf, {1}, annotate=False, show_streams=False)
    assert "(no structure tree found)" in lines


def test_reading_order_lines_objgen_and_attribute_exceptions() -> None:
    """Ensure stream lookup operations gracefully recover from un-trackable stream object identifiers."""

    # Lines 293-296 & 327-328: Accessing stm.objgen throws AttributeError inside reading order parser.
    class BadStream:
        @property
        def objgen(self):
            raise AttributeError("test stream error")

    mcr = pikepdf.Dictionary({"/MCID": 101})
    root = pikepdf.Dictionary({"/S": pikepdf.Name("/Div"), "/K": pikepdf.Array([mcr])})

    struct_tree_root = pikepdf.Dictionary({"/K": root})
    pdf = pikepdf.Pdf.new()
    pdf.Root.StructTreeRoot = struct_tree_root

    with patch("pdftl.operations.helpers.tags_tree_walker._stm_from_item") as mock_stm_from_item:
        mock_stm_from_item.return_value = BadStream()
        lines = _reading_order_lines(pdf, {1}, annotate=False, show_streams=True)

    assert any("(not found in XObject unknown stream)" in line for line in lines)


def test_reading_order_lines_walk_objgen_exceptions() -> None:
    """Test exception paths within structural recursion trees."""

    # Lines 339-346: Evaluating objgen on un-resolvable elements caught safely in walk.
    class BadElem:
        @property
        def objgen(self):
            raise ValueError("walk value error")

        def get(self, key):
            return pikepdf.Name("/Div") if key == "/S" else None

    pdf = pikepdf.Pdf.new()

    with patch("pdftl.operations.helpers.tags_tree_walker._collect_tree_roots") as mock_collect:
        mock_collect.return_value = [BadElem()]
        lines = _reading_order_lines(pdf, {1}, annotate=False, show_streams=False)

    assert any("[Div]" in line for line in lines)


def test_reading_order_lines_circular_reference() -> None:
    """Test reading order lines correctly identifies and outputs circular references."""
    # Covers Lines 339-342 (walk cycle detection)
    pdf = pikepdf.Pdf.new()

    root = pdf.make_indirect(pikepdf.Dictionary({"/S": pikepdf.Name("/Div")}))
    root.K = pikepdf.Array([root])
    pdf.Root.StructTreeRoot = root

    lines = _reading_order_lines(pdf, {1}, annotate=False, show_streams=False)
    out = "\n".join(lines)

    assert "[Div]" in out
    assert f"[CircularReference id={root.objgen[0]}:{root.objgen[1]}]" in out


def test_reading_order_lines_walk_seen_none_coverage(mocker) -> None:
    """Force coverage of `seen is None` inside _walk_node."""
    from pdftl.operations.helpers.tags_tree_walker import _walk_node
    import pikepdf

    class DummyElem:
        def get(self, key):
            return None

    # Call it directly with seen=None to hit the coverage branch
    output = []
    _walk_node(
        elem=DummyElem(),
        depth=0,
        seen=None,
        pdf=mocker.MagicMock(spec=pikepdf.Pdf),
        target_page_nums=set(),
        annotate=False,
        show_streams=False,
        page_objgen_index={},
        page_mcid_maps={},
        xobj_mcid_maps={},
        output=output,
    )

    # As long as it doesn't crash on `seen.add()` or similar, we're good.
    assert True


def test_reading_order_lines_mcid_page_unknown() -> None:
    """Verify that an MCID with no resolvable page falls back to (page unknown)."""
    # Cover Line 576
    pdf = pikepdf.Pdf.new()

    # MCR with an MCID but no /Pg, nested in a parent StructElem with no /Pg
    mcr = pikepdf.Dictionary({"/MCID": 99})
    div = pikepdf.Dictionary({"/S": pikepdf.Name("/Div"), "/K": pikepdf.Array([mcr])})
    pdf.Root.StructTreeRoot = pikepdf.Dictionary({"/K": div})

    lines = _reading_order_lines(pdf, {1}, annotate=False, show_streams=False)
    out = "\n".join(lines)

    assert "[MCID 99]  (page unknown)" in out


@patch("pdftl.operations.helpers.tags_tree_walker.parse_stream_bytes_for_mcids")
@patch("pdftl.operations.helpers.stream_annotator.annotate_stream")
@patch("pdftl.operations.helpers.xobject_helpers.read_xobject_stream")
def test_reading_order_lines_xobj_annotation_success(
    mock_read_xobj, mock_annotate, mock_parse
) -> None:
    """Verify XObject MCID blocks pick up annotated line content when annotation
    succeeds and line counts match the unannotated stream."""
    pdf = pikepdf.Pdf.new()
    p1 = pdf.add_blank_page()

    stm = pikepdf.Stream(pdf, b"bytes")
    mcr = pikepdf.Dictionary({"/MCID": 1, "/Stm": stm})

    div = pikepdf.Dictionary(
        {"/S": pikepdf.Name("/Div"), "/Pg": p1.obj, "/K": pikepdf.Array([mcr])}
    )
    pdf.Root.StructTreeRoot = pikepdf.Dictionary({"/K": div})

    mock_read_xobj.return_value = b"raw text"
    # Same line count as the unannotated parse below, so the annotated
    # version should be adopted and block["lines"] re-sliced from it.
    mock_annotate.return_value = b"raw text  % annotated comment"
    mock_parse.return_value = (
        {1: {"start_line": 1, "end_line": 1, "lines": ["raw text"]}},
        ["raw text"],
    )

    lines = _reading_order_lines(pdf, {1}, annotate=True, show_streams=True)
    out = "\n".join(lines)

    obj_id = f"{stm.objgen[0]}:{stm.objgen[1]}"
    assert f"[MCID 1 in XObject {obj_id}]  stream_lines=1-1" in out
    # The annotated line content (not the bare unannotated text) should
    # appear in the displayed stream lines.
    assert "% annotated comment" in out
