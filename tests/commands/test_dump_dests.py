import io
import json
from unittest.mock import MagicMock, mock_open, patch

import pytest

# Import the functions and classes to test
from pdftl.commands.dump_dests import (
    _atomic_obj_to_json,
    _pdf_obj_to_json,
    _write_json_output,
    dump_dests,
)

# ================================================================
# ===== Mocks for pikepdf objects ================================
# ================================================================

# Global counter for mock object IDs
_objgen_counter = 100


class MockPdfObject:
    """A base class to mock the objgen attribute for cycle detection."""

    def __init__(self):
        global _objgen_counter
        self.objgen = (_objgen_counter, 0)
        _objgen_counter += 1


class MockDictionary(MockPdfObject, dict):
    """Mocks a pikepdf.Dictionary."""

    def __init__(self, *args, **kwargs):
        MockPdfObject.__init__(self)
        dict.__init__(self, *args, **kwargs)


class MockArray(MockPdfObject, list):
    """Mocks a pikepdf.Array."""

    def __init__(self, *args, **kwargs):
        MockPdfObject.__init__(self)
        list.__init__(self, *args, **kwargs)


class MockName(MockPdfObject):
    """Mocks a pikepdf.Name."""

    def __init__(self, text):
        super().__init__()
        self._text = text

    def __str__(self):
        return f"/{self._text}"

    def __hash__(self):
        return hash(self._text)

    def __eq__(self, other):
        return isinstance(other, MockName) and self._text == other._text

    def __repr__(self):
        return f"MockName('{self._text}')"


class MockString(MockPdfObject):
    """Mocks a pikepdf.String."""

    def __init__(self, text):
        super().__init__()
        self._text = text

    def __str__(self):
        return self._text


class MockStream(MockString):
    """Mocks a pikepdf.Stream, behavior is same as String for str()"""

    pass


# ================================================================
# ===== Pytest Fixtures ==========================================
# ================================================================

MODULE_PATH = "pdftl.commands.dump_dests"


@pytest.fixture(autouse=True)
def patch_pikepdf_types():
    """
    This fixture automatically patches the pikepdf types
    for *every test in this file*. This is crucial for
    `isinstance` checks in the SUT (System Under Test) to pass.

    NOTE: This also patches `NameTree`, which is used by the
    refactored `dump_dests` function.
    """
    # We use MagicMock for NameTree as we just need to mock its
    # instantiation and .items() method.
    with (
        patch("pikepdf.Dictionary", new=MockDictionary),
        patch("pikepdf.Array", new=MockArray),
        patch("pikepdf.Name", new=MockName),
        patch("pikepdf.String", new=MockString),
        patch("pikepdf.Stream", new=MockStream),
        patch("pikepdf.NameTree", new=MagicMock()) as mock_name_tree,
    ):

        # Configure the mock NameTree to be iterable by default
        mock_name_tree.return_value.items.return_value = []
        yield mock_name_tree


@pytest.fixture
def mock_stdout():
    """Patches sys.stdout with an in-memory buffer."""
    with patch("sys.stdout", new_callable=io.StringIO) as m:
        yield m


@pytest.fixture
def mock_pdf():
    """Creates a basic mock pikepdf.Pdf object."""
    pdf = MagicMock()
    pdf.pages = []
    pdf.Root = MagicMock()
    pdf.Root.Names = MagicMock()
    pdf.Root.Names.Dests = None  # Default: no dests tree
    return pdf


# ================================================================
# ===== Tests for dump_dests (Refactored) ========================
# ================================================================


def test_dump_dests_no_dests_tree(mock_pdf, mock_stdout):
    """
    Tests the behavior when `pdf.Root.Names.Dests` is None.
    """
    mock_pdf.Root.Names.Dests = None

    dump_dests(mock_pdf, output_file=None)

    result = json.loads(mock_stdout.getvalue())
    assert result["dests"] == []
    assert result["errors"] == []


def test_dump_dests_success(mock_pdf, mock_stdout, patch_pikepdf_types):
    """
    Tests the happy path: a Dests tree is found, instantiated,
    and iterated over successfully.
    """
    # 1. Setup mock page for page map
    page1 = MockPdfObject()
    mock_pdf.pages = [MagicMock(obj=page1)]  # Page map will be {page1.objgen: 1}

    # 2. Setup mock destination data
    dest_array = MockArray([page1, MockName("XYZ"), 1, 2, 3])
    mock_items = [("Dest1", dest_array)]

    # 3. Configure the patched NameTree mock
    mock_pdf.Root.Names.Dests = MagicMock()  # Just needs to be not-None
    patch_pikepdf_types.return_value.items.return_value = mock_items

    # 4. Run the function
    dump_dests(mock_pdf, output_file=None)

    # 5. Verify NameTree was called
    patch_pikepdf_types.assert_called_once_with(mock_pdf.Root.Names.Dests)

    # 6. Verify output
    result = json.loads(mock_stdout.getvalue())
    assert len(result["dests"]) == 1
    assert result["dests"][0]["name"] == "Dest1"
    assert result["dests"][0]["value"] == ["<<Page 1>>", "/XYZ", 1, 2, 3]
    assert result["errors"] == []


def test_dump_dests_nametree_init_fails(mock_pdf, mock_stdout, patch_pikepdf_types):
    """
    Tests that an exception during NameTree instantiation is caught.
    """
    mock_pdf.Root.Names.Dests = MagicMock()
    patch_pikepdf_types.side_effect = ValueError("Bad tree structure")

    dump_dests(mock_pdf, output_file=None)

    result = json.loads(mock_stdout.getvalue())
    assert result["dests"] == []
    assert len(result["errors"]) == 1
    assert "Failed to parse" in result["errors"][0]["error"]
    assert "Bad tree structure" in result["errors"][0]["details"]


def test_dump_dests_item_processing_fails(mock_pdf, mock_stdout, patch_pikepdf_types):
    """
    Tests that an exception during the processing of one item
    is caught, and processing continues (if there were more items).
    """
    mock_pdf.Root.Names.Dests = MagicMock()

    # This item will cause _pdf_obj_to_json to fail
    mock_items = [("BadDest", MockArray([1]))]  # Valid structure
    patch_pikepdf_types.return_value.items.return_value = mock_items

    # Patch the helper function to fail
    with patch(f"{MODULE_PATH}._pdf_obj_to_json", side_effect=ValueError("JSON fail")):
        dump_dests(mock_pdf, output_file=None)

    result = json.loads(mock_stdout.getvalue())
    assert result["dests"] == []
    assert len(result["errors"]) == 1
    assert "Failed to process" in result["errors"][0]["error"]
    assert "BadDest" in result["errors"][0]["error"]
    assert "JSON fail" in result["errors"][0]["details"]
    assert "raw_value" in result["errors"][0]


# ================================================================
# ===== Tests for Helper Functions ===============================
# ================================================================


def test_atomic_obj_to_json_primitives():
    assert _atomic_obj_to_json(123) == 123
    assert _atomic_obj_to_json(12.3) == 12.3
    assert _atomic_obj_to_json(True) is True
    assert _atomic_obj_to_json(None) is None
    assert _atomic_obj_to_json("hello") == "hello"


def test_atomic_obj_to_json_pdf_types():
    assert _atomic_obj_to_json(MockName("Test")) == "/Test"
    assert _atomic_obj_to_json(MockString("Test")) == "Test"
    assert _atomic_obj_to_json(MockStream("Test")) == "Test"


def test_pdf_obj_to_json_simple_dict_and_list():
    obj = MockDictionary({MockName("Key"): 123})
    assert _pdf_obj_to_json(obj, {}) == {"/Key": 123}

    obj = MockArray([1, True, MockString("hello")])
    assert _pdf_obj_to_json(obj, {}) == [1, True, "hello"]


def test_pdf_obj_to_json_page_mapping():
    page1 = MockPdfObject()
    page_map = {page1.objgen: 1}
    assert _pdf_obj_to_json(page1, page_map) == "<<Page 1>>"

    page2 = MockPdfObject()
    assert _pdf_obj_to_json(page2, page_map) == repr(page2)  # Fallback


def test_pdf_obj_to_json_circular_ref():
    obj = MockDictionary()
    obj[MockName("self")] = obj

    result = _pdf_obj_to_json(obj, {})
    assert result["/self"] == f"<<Circular Reference to {obj.objgen}>>"


def test_write_json_output_to_file():
    data = {"hello": "world"}
    # The regex compacts this to "{"hello": "world"}"
    compacted_expected = '{"hello": "world"}'
    test_filename = "test_output.json"

    m = mock_open()
    with patch("builtins.open", m):
        _write_json_output(data, test_filename)

    m.assert_called_once_with(test_filename, "w", encoding="utf-8")

    # We must check the calls to write() separately,
    # because print() calls write() twice (once for the
    # string, once for the newline).
    handle = m()
    write_calls = handle.write.call_args_list

    # Check that write was called twice
    assert len(write_calls) == 2

    # Check the first call was the string
    assert write_calls[0].args[0] == compacted_expected

    # Check the second call was the newline
    assert write_calls[1].args[0] == "\n"


def test_write_json_output_to_stdout(mock_stdout):
    data = {"a": 1}
    # The actual output is {"a": 1}\n, not { "a": 1 }\n
    compacted_expected = '{"a": 1}\n'  # Compacted + newline from print

    _write_json_output(data, output_file=None)
    assert mock_stdout.getvalue() == compacted_expected
