# tests/operations/test_bookmarks.py

import json
from unittest.mock import patch

import pikepdf
import pytest

from pdftl.core.types import OpResult
from pdftl.exceptions import InvalidArgumentError, MissingArgumentError, OperationError
from pdftl.operations.dump_bookmarks import dump_toc, dump_toc_cli_hook
from pdftl.operations.update_bookmarks import update_toc


def test_dump_bookmarks_json_flag():
    """Tests the dump_bookmarks operation structure and flag detection."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()
    with pdf.open_outline() as outline:
        outline.root.append(pikepdf.OutlineItem("Operation Test", 0))

    # Act
    res = dump_toc("dump_bookmarks", pdf, ["json"], output_file="/tmp/dummy.json")

    # Assert OpResult structure
    assert res.success is True
    assert res.is_discardable is True
    assert res.meta["json_output"] is True
    assert res.meta["output_file"] == "/tmp/dummy.json"

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
    assert saved_data[0]["title"] == "Test JSON"


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
        update_toc(pikepdf.open(six_page_pdf), [str(bad_yaml)])


# --- toc.py Coverage ---


def test_toc_filter_not_dict_and_missing_title(tmp_path, six_page_pdf, caplog):
    """Hits toc.py lines 135-136 (not a dict) and 139-140 (missing title)."""
    test_file = tmp_path / "test.yaml"
    # 1st item is a string, 2nd item is a dict but has no 'title'
    test_file.write_text("- Just a string\n- page: 1\n")

    update_toc(pikepdf.open(six_page_pdf), [str(test_file)])

    assert "not a dictionary" in caplog.text
    assert "missing 'title'" in caplog.text


def test_toc_filter_unrecognized_keys(tmp_path, six_page_pdf):
    """Hits toc.py line 147 (unrecognized keys OperationError)."""
    test_file = tmp_path / "test.yaml"
    test_file.write_text("- title: Valid Title\n  pagee: 5\n")  # Typo in 'page'

    with pytest.raises(
        OperationError, match="Invalid keys found in bookmark 'Valid Title': pagee"
    ):
        update_toc(pikepdf.open(six_page_pdf), [str(test_file)])


def test_toc_filter_invalid_children(tmp_path, six_page_pdf, caplog):
    """Hits toc.py lines 158-159 (children must be a list)."""
    test_file = tmp_path / "test.yaml"
    test_file.write_text("- title: Parent\n  children: Just a string instead of a list\n")

    update_toc(pikepdf.open(six_page_pdf), [str(test_file)])

    assert "Ignoring invalid 'children' (must be a list)" in caplog.text


import io


def test_update_bookmarks_no_args():
    """
    Covers lines 74-75: Error when no file argument is provided.
    """
    from pdftl.exceptions import MissingArgumentError

    pdf = pikepdf.Pdf.new()

    with pytest.raises(MissingArgumentError, match="requires a <bookmarks_file> argument"):
        update_toc(pdf, [])


def test_update_bookmarks_invalid_root_type(tmp_path):
    """
    Covers lines 103-107: Error when JSON/YAML is not a list.
    """
    from pdftl.exceptions import OperationError

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
