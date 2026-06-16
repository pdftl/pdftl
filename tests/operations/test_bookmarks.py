# tests/operations/test_bookmarks.py

import io
import json
from unittest.mock import patch

import pikepdf
import pytest
from pdftl.exceptions import OperationError

from pdftl.core.core_types import OpResult
from pdftl.exceptions import InvalidArgumentError, MissingArgumentError
from pdftl.operations.dump_bookmarks import dump_toc, dump_toc_cli_hook
from pdftl.operations.update_bookmarks import update_toc


@pytest.fixture
def bookmarked_pdf(tmp_path, six_page_pdf):
    """Generates a test PDF with a complex, nested bookmark hierarchy."""
    bookmarked_path = tmp_path / "bookmarked_6_page.pdf"

    # A complex structure testing nesting, standard pages, and URIs
    initial_structure = """
    - title: Chapter 1
      page: 1
      children:
        - title: Section 1.1
          page: 2
        - title: Section 1.2
          page: 3
    - title: External Reference
      uri: https://example.com
    """
    yaml_path = tmp_path / "initial_bookmarks.yaml"
    yaml_path.write_text(initial_structure)

    # Use our working function to build the test asset
    from pdftl.operations.update_bookmarks import update_toc
    import pikepdf

    with pikepdf.open(six_page_pdf) as pdf:
        update_toc(pdf, [str(yaml_path)])
        pdf.save(str(bookmarked_path))

    return bookmarked_path


def test_dump_bookmarks_json_flag(tmp_path):
    """Tests the dump_bookmarks operation structure and flag detection."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()
    with pdf.open_outline() as outline:
        outline.root.append(pikepdf.OutlineItem("Operation Test", 0))

    output_file = tmp_path / "dummy.json"

    # Act
    res = dump_toc("dump_bookmarks", pdf, ["json"], output_file=str(output_file))

    # Assert OpResult structure
    assert res.success is True
    assert res.is_discardable is True
    assert res.meta["json_output"] is True
    assert res.meta["output_file"] == str(output_file)

    # Assert data payload
    assert len(res.data) == 1
    assert res.data[0]["title"] == "Operation Test"


def test_update_bookmarks_missing_argument():
    """Tests that missing the required input file raises the appropriate error."""
    pdf = pikepdf.Pdf.new()
    with pytest.raises(MissingArgumentError, match="requires a <bookmarks_file>"):
        update_toc(pdf, [])


def test_update_bookmarks_invalid_json_format(tmp_path):
    """Tests that a JSON object (dict) instead of an Array (list) is rejected."""
    pdf = pikepdf.Pdf.new()
    bad_file = tmp_path / "bad.json"

    # Writing a dict instead of a list
    bad_file.write_text('{"title": "Not a list"}')

    with pytest.raises(OperationError, match="Root element must be a list"):
        update_toc(pdf, [str(bad_file)])


def test_update_bookmarks_translates_value_error(tmp_path):
    """Tests that out-of-bounds ValueErrors are caught and re-raised as OperationErrors."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()

    bad_file = tmp_path / "oob.json"
    bad_file.write_text(json.dumps([{"title": "Bad Page", "page": 99}]))

    with pytest.raises(OperationError, match="Validation Error"):
        update_toc(pdf, [str(bad_file)])


def test_update_bookmarks_success_json(tmp_path):
    """Tests a full, successful CLI run parsing a JSON file."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()
    pdf.add_blank_page()

    good_file = tmp_path / "good.json"
    good_file.write_text(json.dumps([{"title": "Valid JSON Injection", "page": 2, "bold": True}]))

    res = update_toc(pdf, [str(good_file)])
    assert res.success is True

    # Re-open the outline and verify it actually applied to the pikepdf object
    with pdf.open_outline() as outline:
        items = list(outline.root)
        assert len(items) == 1
        assert items[0].title == "Valid JSON Injection"
        assert int(items[0].obj.get("/F", 0)) & 2  # Verifies bold flag was applied


def test_dump_toc_cli_hook_no_yaml_installed(tmp_path):
    """Verifies that missing pyyaml throws the standard dependency error."""
    out_file = tmp_path / "out.yaml"
    result = OpResult(
        success=True,
        pdf=None,
        data=[{"title": "Test"}],
        meta={"output_file": str(out_file), "json_output": False},
    )

    with patch.dict("sys.modules", {"yaml": None}):
        with pytest.raises(InvalidArgumentError):
            dump_toc_cli_hook(result, stage="post", _pipeline=None)


def test_update_toc_no_yaml_installed(tmp_path, two_page_pdf):
    """Verifies that missing pyyaml throws the standard dependency error when loading."""
    yaml_file = tmp_path / "toc.yaml"
    yaml_file.write_text("- title: YAML Node\n  page: 1\n")

    with patch.dict("sys.modules", {"yaml": None}):
        with pytest.raises(InvalidArgumentError):
            update_toc(two_page_pdf, [str(yaml_file)])


# --- dump_bookmarks.py coverage ---


def test_dump_toc_cli_hook_no_meta():
    """Covers line 34: Missing metadata raises AttributeError."""
    bad_result = OpResult(success=True, pdf=None, data=[])
    with pytest.raises(AttributeError, match="No result metadata"):
        dump_toc_cli_hook(bad_result, stage="post", _pipeline=None)


def test_dump_toc_cli_hook_json_output(tmp_path):
    """Covers lines 41-42: Successful JSON output."""
    out_file = tmp_path / "out.json"
    result = OpResult(
        success=True,
        pdf=None,
        data=[{"title": "Test JSON"}],
        meta={"output_file": str(out_file), "json_output": True},
    )

    dump_toc_cli_hook(result, stage="post", _pipeline=None)

    saved_data = json.loads(out_file.read_text())
    assert saved_data["bookmarks"][0]["title"] == "Test JSON"


def test_dump_toc_cli_hook_yaml_output(tmp_path):
    """Covers lines 49-51: Successful YAML output."""
    out_file = tmp_path / "out.yaml"
    result = OpResult(
        success=True,
        pdf=None,
        data=[{"title": "Test YAML"}],
        meta={"output_file": str(out_file), "json_output": False},
    )

    # This will succeed as long as pyyaml is actually installed in your test env
    dump_toc_cli_hook(result, stage="post", _pipeline=None)

    assert "title: Test YAML" in out_file.read_text()


# --- update_bookmarks.py coverage ---


def test_update_toc_yaml_loading(tmp_path):
    """Covers lines 53-55: Successful YAML loading."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()

    yaml_file = tmp_path / "toc.yaml"
    yaml_file.write_text("- title: YAML Node\n  page: 1\n")

    # This will succeed and hit the `import yaml` block
    result = update_toc(pdf, [str(yaml_file)])
    assert result.success is True


# --- update_bookmarks.py Coverage ---


def test_update_bookmarks_json_decode_error(tmp_path, six_page_pdf):
    """Hits update_bookmarks.py line 58 (JSONDecodeError)."""
    bad_json = tmp_path / "bad.json"
    bad_json.write_text('{"title": "missing quote}')

    with pytest.raises(OperationError, match="Failed to parse JSON file"):
        update_toc(pikepdf.open(six_page_pdf), [str(bad_json)])


def test_update_bookmarks_yaml_decode_error(tmp_path, six_page_pdf):
    """Hits update_bookmarks.py line 63 (YAMLError)."""
    bad_yaml = tmp_path / "bad.yaml"
    # Tabs are strictly forbidden in YAML and will trigger a ScannerError
    bad_yaml.write_text("- title: broken\n\tbad_indent: true")

    with pytest.raises(OperationError, match="Failed to parse YAML file"):
        with pikepdf.open(six_page_pdf) as pdf:
            update_toc(pdf, [str(bad_yaml)])


# --- toc.py Coverage ---


def test_toc_filter_not_dict_and_missing_title(tmp_path, six_page_pdf):
    """Ensures that passing a non-dictionary item raises a strict validation error."""
    test_file = tmp_path / "test.yaml"
    # 1st item is a string, 2nd item is a dict but has no 'title'
    test_file.write_text("- Just a string\n- page: 1\n")

    with pikepdf.open(six_page_pdf) as pdf:
        with pytest.raises(OperationError, match="Expected a dictionary for bookmark, got: str"):
            update_toc(pdf, [str(test_file)])


def test_toc_filter_unrecognized_keys(tmp_path, six_page_pdf):
    """Hits toc.py line 147 (unrecognized keys OperationError)."""
    test_file = tmp_path / "test.yaml"
    test_file.write_text("- title: Valid Title\n  pagee: 5\n")  # Typo in 'page'

    with pikepdf.open(six_page_pdf) as pdf:
        with pytest.raises(
            OperationError, match="Invalid keys found in bookmark 'Valid Title': pagee"
        ):
            update_toc(pdf, [str(test_file)])


def test_toc_filter_invalid_children(tmp_path, six_page_pdf):
    """Ensures that invalid children types raise a strict validation error."""
    test_file = tmp_path / "test.yaml"
    test_file.write_text("- title: Parent\n  children: Just a string instead of a list\n")

    with pikepdf.open(six_page_pdf) as pdf:
        with pytest.raises(OperationError, match="'children' must be a list"):
            update_toc(pdf, [str(test_file)])


def test_update_bookmarks_no_args():
    """
    Covers lines 74-75: Error when no file argument is provided.
    """
    pdf = pikepdf.Pdf.new()

    with pytest.raises(MissingArgumentError, match="requires a <bookmarks_file> argument"):
        update_toc(pdf, [])


def test_update_bookmarks_invalid_root_type(tmp_path):
    """
    Covers lines 103-107: Error when JSON/YAML is not a list.
    """
    pdf = pikepdf.Pdf.new()
    json_file = tmp_path / "invalid.json"
    json_file.write_text(json.dumps({"not_a": "list"}))

    with pytest.raises(OperationError, match="Root element must be a list"):
        update_toc(pdf, [str(json_file)])


def test_update_bookmarks_from_stdin(tmp_path):
    """
    Covers line 79: Handles '-' by reading from stdin.
    """
    # 1. Setup a dummy PDF
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()

    # 2. Prepare mock JSON data
    mock_bookmarks = [{"title": "Chapter 1", "page": 1}]
    mock_input = json.dumps(mock_bookmarks)

    # 3. Patch stdin and call update_toc
    # smart_open(None) reads from sys.stdin
    with patch("sys.stdin", io.StringIO(mock_input)):
        op_args = ["-"]
        result = update_toc(pdf, op_args)

    # 4. Verify results
    assert result.success is True

    # Correct way to check the outline in pikepdf:
    # We use the 'open_outline' context manager to ensure the tree is synced
    with pdf.open_outline() as outline:
        root_items = list(outline.root)
        assert len(root_items) == 1
        assert root_items[0].title == "Chapter 1"


# --- Additional Coverage for Edge Cases and Stdin Pipelines ---


def test_update_bookmarks_stdin_malformed_json_with_yaml(tmp_path):
    """Hits lines 85, 141, 153, 166, 172, 188-191.

    Pipes bad JSON through stdin while YAML is installed. It must attempt
    both parsers, log both failures, and raise a combined error report.
    """
    pdf = pikepdf.Pdf.new()
    bad_stream_content = '{"title": "Unclosed Object'

    with patch("sys.stdin", io.StringIO(bad_stream_content)):
        with pytest.raises(
            OperationError, match="Could not parse bookmarks input. Parsers tried:"
        ):
            update_toc(pdf, ["-"])


def test_update_bookmarks_stdin_malformed_json_no_yaml():
    """Hits lines 124, 180-184.

    Pipes bad JSON through stdin when pyyaml is missing. It identifies
    the structural JSON intent ('{') and extracts a helpful JSON-specific error message.
    """
    pdf = pikepdf.Pdf.new()
    bad_stream_content = '{"missing_bracket": '

    with patch.dict("sys.modules", {"yaml": None}):
        with patch("sys.stdin", io.StringIO(bad_stream_content)):
            with pytest.raises(OperationError, match="Invalid JSON data provided on stdin:"):
                update_toc(pdf, ["-"])


def test_update_bookmarks_stdin_raw_text_no_yaml():
    """Hits line 186.

    Pipes flat, ambiguous text through stdin with pyyaml missing. Since it doesn't
    look like JSON, it safely assumes YAML intent and suggests installing pyyaml.
    """
    pdf = pikepdf.Pdf.new()
    ambiguous_content = "ch1: page 1\n  ch2: page 2"

    with patch.dict("sys.modules", {"yaml": None}):
        with patch("sys.stdin", io.StringIO(ambiguous_content)):
            # extra_tag="yaml" triggers InvalidArgumentError in pdftl dependency helper
            with pytest.raises(InvalidArgumentError):
                update_toc(pdf, ["-"])


def test_try_parse_yaml_direct_error_handling():
    """Ensures that unexpected third-party syntax failures are caught
    and returned cleanly rather than causing an unhandled crash.
    """
    from pdftl.operations.update_bookmarks import _try_parse_yaml
    import yaml

    # Passing raw text with forbidden tab characters forces a YAMLError variant
    content = "-\tbad_tab_indentation"
    result, error = _try_parse_yaml(content, is_yaml_ext=False, yaml_module=yaml)

    assert result is None
    assert error is not None


# --- New Coverage for Precedence Logic (update_bookmarks.py 110-124, 127) ---


def test_enforce_precedence_and_warn(caplog):
    """Covers Precedence resolution, recursive traversal, and singular warnings."""
    from pdftl.operations.update_bookmarks import _enforce_precedence_and_warn
    import logging

    nodes = [
        {
            "title": "Root",
            "dest": "chapter1",
            "page": 1,
            "view": ["FitH", 800],
            "children": [{"title": "Child", "dest": "section1", "page": 2, "view": ["XYZ"]}],
        },
        {"title": "Sibling", "dest": "chapter2", "page": 3},
    ]

    with caplog.at_level(logging.DEBUG):
        _enforce_precedence_and_warn(nodes, {"emitted": False})

    # Assert derived keys were stripped completely to protect the backend
    assert "page" not in nodes[0]
    assert "view" not in nodes[0]
    assert "page" not in nodes[0]["children"][0]
    assert "view" not in nodes[0]["children"][0]
    assert "page" not in nodes[1]

    # Assert warning emitted ONLY once, despite 3 violations
    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "The 'dest' key takes precedence" in warnings[0]

    # Assert debug log emitted for EVERY violation
    debugs = [
        r.message
        for r in caplog.records
        if r.levelname == "DEBUG" and "Precedence enforced" in r.message
    ]
    assert len(debugs) == 3


# --- New Coverage for Resolution Logic (dump_bookmarks.py 106-126, 137-139) ---


def test_dump_bookmarks_resolve_dest_success():
    """Covers Successful resolution mapping, data cleaning, and tree traversal child-popping."""
    from pdftl.operations.dump_bookmarks import _resolve_bookmarks_tree

    class MockResolved:
        def __init__(self, page_num, dest_type, args):
            self.page_num = page_num
            self.dest_type = dest_type
            self.args = args

    nodes = [
        {
            "title": "Root",
            "dest": "target_root",
            "children": [{"title": "Child", "dest": "target_child"}],
        }
    ]

    with patch("pdftl.operations.dump_bookmarks.resolve_dest_to_page_num") as mock_resolve:
        mock_resolve.side_effect = [
            MockResolved(1, "XYZ", [0.0, 700.5, 0]),
            MockResolved(2, "FitH", [500]),
        ]

        _resolve_bookmarks_tree(nodes, {}, {})

    # Root Node Checks
    assert nodes[0]["page"] == 1
    assert nodes[0]["view"] == ["XYZ", 0.0, 700.5, 0.0]  # Ints with __float__ become floats

    # Children Checks (verifies lines 137-139 popped and restored them cleanly)
    assert "children" in nodes[0]
    assert nodes[0]["children"][0]["page"] == 2
    assert nodes[0]["children"][0]["view"] == ["FitH", 500.0]


def test_dump_bookmarks_resolve_dest_failure_and_none(caplog):
    """Covers missing resolution mappings and unexpected internal errors."""
    from pdftl.operations.dump_bookmarks import _resolve_single_node_dest

    # 1. Test None return (line 108-109)
    node_none = {"title": "None Dest", "dest": "missing1"}
    with patch("pdftl.operations.dump_bookmarks.resolve_dest_to_page_num", return_value=None):
        _resolve_single_node_dest(node_none, {}, {})
    assert "page" not in node_none

    # 2. Test Exception block (line 125-126)
    node_err = {"title": "Err Dest", "dest": "missing2"}
    with patch(
        "pdftl.operations.dump_bookmarks.resolve_dest_to_page_num", side_effect=KeyError("Broken")
    ):
        _resolve_single_node_dest(node_err, {}, {})

    assert "page" not in node_err
    assert "Failed to resolve destination 'missing2'" in caplog.text


# --- Strict Structure Validation Tests (0-Indexing & Collisions) ---


def test_update_bookmarks_validation_uri_collision(tmp_path, six_page_pdf):
    """Ensures that passing conflicting routing keys (uri vs page) raises an OperationError."""
    test_file = tmp_path / "test.yaml"
    # A bookmark cannot jump to a page AND open a web link simultaneously
    test_file.write_text("- title: Collision\n  page: 1\n  uri: https://example.com\n")

    with pikepdf.open(six_page_pdf) as pdf:
        with pytest.raises(OperationError):
            update_toc(pdf, [str(test_file)])


def test_update_bookmarks_validation_zero_indexing(tmp_path, six_page_pdf):
    """Ensures that page: 0 traps are caught since PDF pages are strictly 1-indexed."""
    test_file = tmp_path / "test.yaml"
    test_file.write_text("- title: Zero Index Trap\n  page: 0\n")

    with pikepdf.open(six_page_pdf) as pdf:
        with pytest.raises(OperationError):
            update_toc(pdf, [str(test_file)])


@pytest.mark.parametrize(
    "yaml_content, expected_error",
    [
        # Missing title entirely
        ("- page: 1\n", "Missing required 'title'"),
        # Invalid title type (int instead of string)
        ("- title: 123\n  page: 1\n", "'title' must be a string"),
        # Invalid page type (string instead of int)
        ("- title: Intro\n  page: one\n", "'page' must be an integer"),
        # Invalid color (not enough items)
        ("- title: Intro\n  color: [1.0, 0.0]\n", "'color' must be a list of 3"),
        # Invalid color (wrong type in array)
        ("- title: Intro\n  color: [1, 2, red]\n", "'color' must be a list of 3"),
        # Invalid view type (string instead of list)
        ("- title: Intro\n  page: 1\n  view: FitH\n", "'view' must be a list"),
        # View without page
        ("- title: Intro\n  view: ['FitH', 800]\n", "requires a target 'page'"),
        # Routing Collision (URI + Page)
        (
            "- title: Intro\n  page: 1\n  uri: https://example.com\n",
            "Cannot contain both a web link",
        ),
        # Children type mismatch (string instead of list)
        ("- title: Intro\n  children: chapter 1\n", "'children' must be a list"),
        # Nested child validation failure (page is string instead of int)
        (
            "- title: Parent\n  children:\n    - title: Child\n      page: two\n",
            "'page' must be an integer",
        ),
        # Root is not a list (it's a dict)
        ("title: Intro\npage: 1\n", "Root element must be a list"),
    ],
    ids=[
        "missing_title",
        "invalid_title_type",
        "invalid_page_type",
        "color_array_too_short",
        "color_array_invalid_type",
        "invalid_view_type",
        "view_without_page",
        "uri_page_collision",
        "invalid_children_type",
        "nested_invalid_child",
        "root_not_list",
    ],
)
def test_update_bookmarks_schema_validation(tmp_path, six_page_pdf, yaml_content, expected_error):
    """Systematically tests all schema edge cases and routing logic."""
    test_file = tmp_path / "test.yaml"
    test_file.write_text(yaml_content)

    with pikepdf.open(six_page_pdf) as pdf:
        with pytest.raises(OperationError, match=expected_error):
            # Replace 'update_bookmarks' with whatever your entrypoint function is
            update_toc(pdf, [str(test_file)])


def test_bookmarks_round_trip_integration(tmp_path, bookmarked_pdf):
    """Verifies the end-to-end dump -> edit -> update workflow with nested data."""
    import pdftl

    bookmarks = pdftl.api.dump_bookmarks(bookmarked_pdf)
    assert bookmarks[0]["children"][0]["title"] == "Section 1.1"

    bookmarks[0]["title"] = "Chapter 1 (Edited)"
    bookmarks.append({"title": "Chapter 2", "page": 4})
    edited_pdf = pdftl.api.update_bookmarks(bookmarked_pdf, bookmarks=bookmarks)
    final_bookmarks = pdftl.api.dump_bookmarks(edited_pdf)

    # Assertions
    assert final_bookmarks[0]["title"] == "Chapter 1 (Edited)"  # Edit survived
    assert (
        final_bookmarks[0]["children"][1]["title"] == "Section 1.2"
    )  # Unedited siblings survived
    assert final_bookmarks[-1]["title"] == "Chapter 2"  # New addition survived
    assert final_bookmarks[-1]["page"] == 4


def test_bookmarks_named_dests_integration(tmp_path, get_pdf_path):
    """Verifies round-trip integration for bookmarks utilizing named destinations."""
    import pdftl

    test_filename = "issue123.pdf"
    bookmarked_pdf = get_pdf_path(test_filename)
    bookmarks = pdftl.api.dump_bookmarks(bookmarked_pdf)

    # Helper function to find and modify the first named destination
    def find_and_edit_dest(nodes, new_title="Edited Named Dest Title"):
        for node in nodes:
            if "dest" in node:
                original_dest = node["dest"]
                node["title"] = new_title
                return original_dest
            if "children" in node and isinstance(node["children"], list):
                found = find_and_edit_dest(node["children"], new_title)
                if found:
                    return found
        return None

    # 2. EDIT: Locate the named dest and change its title
    original_dest_value = find_and_edit_dest(bookmarks)
    if not original_dest_value:
        pytest.fail(f"{test_filename} does not contain any named destinations ('dest') to test.")

    edited_pdf = pdftl.api.update_bookmarks(bookmarked_pdf, bookmarks=bookmarks)
    final_bookmarks = pdftl.api.dump_bookmarks(edited_pdf)

    # Re-run our search on the final data to ensure the edit and the target persisted
    def find_dest_by_title(nodes, target_title):
        for node in nodes:
            if node.get("title") == target_title:
                return node
            if "children" in node and isinstance(node["children"], list):
                found = find_dest_by_title(node["children"], target_title)
                if found:
                    return found
        return None

    verified_node = find_dest_by_title(final_bookmarks, "Edited Named Dest Title")

    # Assertions
    assert verified_node is not None, "The edited bookmark was lost during the update."
    assert verified_node.get("dest") == original_dest_value, (
        "The named destination string was corrupted or dropped."
    )


def test_round_trip_uri_survives(tmp_path, bookmarked_pdf):
    """Verifies that URI bookmarks survive the dump -> update round trip."""
    import pdftl

    bookmarks = pdftl.api.dump_bookmarks(bookmarked_pdf)

    edited_pdf = pdftl.api.update_bookmarks(bookmarked_pdf, bookmarks=bookmarks)
    final_bookmarks = pdftl.api.dump_bookmarks(edited_pdf)

    uri_nodes = [b for b in final_bookmarks if "uri" in b]
    assert len(uri_nodes) == 1
    assert uri_nodes[0]["title"] == "External Reference"
    assert uri_nodes[0]["uri"] == "https://example.com"


def test_dump_bookmarks_no_resolve_preserves_dest(get_pdf_path):
    """Verifies that no_resolve keeps dest intact and suppresses page/view injection."""
    import pdftl

    bookmarked_pdf = get_pdf_path("issue123.pdf")
    bookmarks = pdftl.api.dump_bookmarks(bookmarked_pdf, operation_args=["no_resolve"])

    def find_dest_node(nodes):
        for node in nodes:
            if "dest" in node:
                return node
            if "children" in node:
                found = find_dest_node(node["children"])
                if found:
                    return found
        return None

    dest_node = find_dest_node(bookmarks)
    assert dest_node is not None, "issue123.pdf should contain at least one named destination"
    assert "dest" in dest_node
    assert "page" not in dest_node
    assert "view" not in dest_node


def test_update_bookmarks_zero_page_rejected(tmp_path, six_page_pdf):
    """Verifies that page: 0 is caught by build_toc_tree boundary checking, not schema validation."""
    import pdftl
    import pikepdf

    with pikepdf.open(six_page_pdf) as pdf:
        with pytest.raises(Exception, match="(?i)page|bound|invalid"):
            pdftl.api.update_bookmarks(pdf, bookmarks=[{"title": "Zero", "page": 0}])


def test_update_bookmarks_stdin_yaml(tmp_path):
    """Verifies YAML data piped through stdin is correctly applied."""
    import io
    from unittest.mock import patch
    import pdftl
    import pikepdf

    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()
    pdf.add_blank_page()

    yaml_content = "- title: Stdin YAML Chapter\n  page: 1\n- title: Chapter 2\n  page: 2\n"

    with patch("sys.stdin", io.StringIO(yaml_content)):
        result_pdf = pdftl.api.update_bookmarks(pdf, operation_args=["-"])

    final_bookmarks = pdftl.api.dump_bookmarks(result_pdf)
    assert len(final_bookmarks) == 2
    assert final_bookmarks[0]["title"] == "Stdin YAML Chapter"
    assert final_bookmarks[1]["title"] == "Chapter 2"
