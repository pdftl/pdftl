import io
import json
import re

# Import patch and the *real* pikepdf types for our mocks to use
from unittest.mock import patch

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

# Import the internal functions to test
from pdftl.commands.dump_dests import (
    _atomic_obj_to_json,
    _pdf_obj_to_json,
    _write_json_output,
)

# A strategy that generates JSON-serializable "atomic" values
st_atomic_values = st.one_of(
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.booleans(),
    st.none(),
    st.text(),
)


@given(value=st_atomic_values)
def test_atomic_obj_to_json_handles_python_primitives(value):
    """
    Tests that basic Python types that are also valid PDF
    object values are passed through correctly.
    """
    # We pass an empty page_map and visited set for this simple test.
    if isinstance(value, str):
        # The real function uses str(obj) which works for String/Stream
        # but here we just test that a plain string doesn't error
        assert isinstance(_atomic_obj_to_json(value), str)
    else:
        assert _atomic_obj_to_json(value) == value


# A strategy to generate nested Python objects (dicts, lists, atoms)
# This will be used to test the JSON compaction logic.
st_json_serializable = st.recursive(
    st_atomic_values,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(st.text(min_size=1, max_size=10), children, max_size=5),
    ),
    max_leaves=10,
)


@given(data=st_json_serializable)
def test_write_json_output_compaction_does_not_crash(data):
    """
    Tests that the regex-based JSON compaction in _write_json_output
    runs without errors on a wide variety of valid JSON structures.
    """
    # We patch "sys.stdout" with an in-memory buffer (StringIO)
    # for *each run* of the Hypothesis test. This prevents
    # pytest from crashing on a closed stdout, which was the
    # bug we saw earlier.
    with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        # NOTE: This function is stateful (it prints to stdout)
        # We are testing that it doesn't crash and produces valid JSON.
        _write_json_output(data, output_file=None)

        output_string = mock_stdout.getvalue()

        # Assert that something was actually printed
        assert output_string

        # Assert that the output is still valid JSON
        # This proves the regex didn't break the structure.
        try:
            json.loads(output_string)
        except json.JSONDecodeError as e:
            pytest.fail(
                f"Regex compaction produced invalid JSON: {e}\nOutput was:\n{output_string}"
            )


@given(
    simple_list=st.lists(st_atomic_values, min_size=1, max_size=5).filter(
        # A "simple" list contains no nested objects OR CHARACTERS
        # that would break compaction.
        lambda l: not any(
            "{" in str(i) or "[" in str(i) or "}" in str(i) or "]" in str(i) for i in l
        )
    )
)
def test_write_json_output_compaction_compacts_simple_lists(simple_list):
    """
    Tests that a "simple" list (no nested dicts/lists)
    IS compacted onto a single line.
    """
    assume(simple_list)  # Not an empty list
    data = {"my_key": simple_list}

    with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        _write_json_output(data, output_file=None)
        output_string = mock_stdout.getvalue()

        # Find the string representation of the list
        list_str_match = re.search(r"(\[.*?\])", output_string, re.DOTALL)
        assert list_str_match is not None

        # Assert that the list itself contains no newlines
        list_str = list_str_match.group(1)
        assert "\n" not in list_str, f"Simple list was not compacted:\n{output_string}"


@given(
    complex_list=st.lists(
        st.one_of(st.dictionaries(st.text(min_size=1), st.integers())),
        min_size=1,
        max_size=3,
    )
)
def test_write_json_output_compaction_skips_complex_lists(complex_list):
    """
    Tests that a "complex" list (one that contains dicts)
    IS NOT compacted onto a single line.
    """
    data = {"my_key": complex_list}

    with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
        _write_json_output(data, output_file=None)
        output_string = mock_stdout.getvalue()

        # Find the string representation of the list
        list_str_match = re.search(r"(\[.*?\])", output_string, re.DOTALL)
        assert list_str_match is not None

        # Assert that the list *does* contain newlines
        list_str = list_str_match.group(1)
        assert "\n" in list_str, f"Complex list was incorrectly compacted:\n{output_string}"


# --- Mocks for pikepdf objects ---

_objgen_counter = 100  # Start high to avoid (0, 0)


class MockPdfObject:
    """A base class to mock the objgen attribute for cycle detection."""

    def __init__(self):
        global _objgen_counter
        # Create a unique object ID (objgen) for each mock
        self.objgen = (_objgen_counter, 0)
        _objgen_counter += 1


class MockDictionary(MockPdfObject, dict):
    """Mocks a pikepdf.Dictionary."""

    def __init__(self, *args, **kwargs):
        MockPdfObject.__init__(self)
        # Initialize the dict part of this object
        dict.__init__(self, *args, **kwargs)


class MockArray(MockPdfObject, list):
    """Mocks a pikepdf.Array."""

    def __init__(self, *args, **kwargs):
        MockPdfObject.__init__(self)
        # Initialize the list part of this object
        # This handles MockArray([1, 2, 3]) from hypothesis.map()
        list.__init__(self, *args, **kwargs)


class MockName(MockPdfObject):
    """Mocks a pikepdf.Name."""

    def __init__(self, text):
        super().__init__()
        self._text = text

    def __str__(self):
        # The str() representation of a Name includes the leading /
        return f"/{self._text}"

    def __hash__(self):
        return hash(self._text)

    def __eq__(self, other):
        return isinstance(other, MockName) and self._text == other._text


class MockString(MockPdfObject):
    """Mocks a pikepdf.String."""

    def __init__(self, text):
        super().__init__()
        self._text = text

    def __str__(self):
        # The str() representation of a String is just its content
        return self._text


# Mock for pikepdf.Stream, which is also checked in _atomic_obj_to_json
class MockStream(MockString):
    """Mocks a pikepdf.Stream, behavior is same as String for str()"""

    pass


# --- Patches for the module under test ---
# These replace the real pikepdf types *inside the dump_dests module*
# with our mock classes, so that `isinstance` checks will pass.
MODULE_PATH = "pdftl.commands.dump_dests"


@patch("pikepdf.Dictionary", new=MockDictionary)
@patch("pikepdf.Array", new=MockArray)
@patch("pikepdf.Name", new=MockName)
@patch("pikepdf.String", new=MockString)
@patch("pikepdf.Stream", new=MockStream)
def _run_patched_test(test_function, *args):
    """
    A helper to run a test function with all patches active.
    This ensures `isinstance` checks inside the imported module
    work correctly against our mock objects.
    """
    test_function(*args)


# --- Start of Patched Tests ---


@given(s=st.text(min_size=1, max_size=20))
def test_atomic_obj_to_json_handles_mock_pdf_name(s):
    """Tests that pikepdf.Name objects are stringified."""

    def test_logic(s):
        mock_name = MockName(s)
        # The function calls str(obj)
        assert _atomic_obj_to_json(mock_name) == str(mock_name)

    _run_patched_test(test_logic, s)


@given(s=st.text(min_size=1, max_size=20))
def test_atomic_obj_to_json_handles_mock_pdf_string(s):
    """Tests that pikepdf.String objects are stringified."""

    def test_logic(s):
        mock_string = MockString(s)
        # The function calls str(obj)
        assert _atomic_obj_to_json(mock_string) == str(mock_string)

    _run_patched_test(test_logic, s)


@given(data=st.binary())
def test_atomic_obj_to_json_uses_repr_for_unknown_types(data):
    """Tests that unknown types fall back to repr()."""

    def test_logic(data):
        # 'bytes' is not one of the explicitly handled types
        # It will fall through to `repr(obj)`
        assert _atomic_obj_to_json(data) == repr(data)

    _run_patched_test(test_logic, data)


@given(page_num=st.integers(min_value=1, max_value=1000))
def test_pdf_obj_to_json_maps_pages_hypothesis(page_num):
    """Tests that objects in the page_map are replaced by <<Page ...>>."""

    def test_logic(page_num):
        mock_page = MockPdfObject()  # Doesn't need to be a specific type
        page_map = {mock_page.objgen: page_num}

        result = _pdf_obj_to_json(mock_page, page_map)

        assert result == f"<<Page {page_num}>>"

    _run_patched_test(test_logic, page_num)


def test_pdf_obj_to_json_detects_simple_circular_reference():
    """Tests cycle detection in a dictionary."""

    def test_logic():
        obj = MockDictionary()
        # A dictionary that contains itself
        obj[MockName("self")] = obj

        # Run the conversion
        result = _pdf_obj_to_json(obj, page_object_to_num_map={})

        # Check the result
        assert isinstance(result, dict)
        # The key /self should be stringified
        assert "/self" in result
        # The value should be the circular reference string
        assert result["/self"] == f"<<Circular Reference to {obj.objgen}>>"

    _run_patched_test(test_logic)


def test_pdf_obj_to_json_detects_deeper_circular_reference():
    """Tests cycle detection in a nested array."""

    def test_logic():
        # A contains B, B contains A
        obj_a = MockDictionary()
        obj_b = MockArray()

        obj_a[MockName("b_array")] = obj_b
        obj_b.append(obj_a)  # The array now contains the dict

        # Run the conversion starting from the top
        result = _pdf_obj_to_json(obj_a, page_object_to_num_map={})

        # Unwind the result to check
        assert isinstance(result, dict)
        assert "/b_array" in result
        assert isinstance(result["/b_array"], list)
        assert len(result["/b_array"]) == 1
        # The item in the list should be the circular ref
        assert result["/b_array"][0] == f"<<Circular Reference to {obj_a.objgen}>>"

    _run_patched_test(test_logic)


# --- Recursive strategy for testing _pdf_obj_to_json ---

# Strategy for atomic PDF values
st_pdf_atoms = st.one_of(
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    st.booleans(),
    st.none(),
    # Add mock Names and Strings as atoms
    st.builds(
        MockName,
        st.text(
            min_size=1,
            max_size=10,
            alphabet=st.characters(min_codepoint=65, max_codepoint=90),
        ),
    ),
    st.builds(MockString, st.text(min_size=1, max_size=10)),
)

# A recursive strategy to build nested structures of mock PDF objects
st_pdf_object = st.recursive(
    st_pdf_atoms,
    lambda children: st.one_of(
        # Strategy for MockArray:
        # Create a list of children, then map it to the MockArray constructor
        st.lists(children, max_size=3).map(MockArray),
        # Strategy for MockDictionary:
        # Create a dict, then map it to the MockDictionary constructor
        st.dictionaries(
            # Keys must be MockName
            st.builds(MockName, st.text(min_size=1, max_size=5, alphabet="abcde")),
            children,
            max_size=3,
        ).map(MockDictionary),
    ),
    max_leaves=10,
)


@given(obj=st_pdf_object)
def test_pdf_obj_to_json_handles_nested_structures(obj):
    """
    Tests that the main conversion function can handle
    randomly generated, deeply nested PDF object structures
    without crashing and always produces JSON-serializable output.
    """

    def test_logic(obj):
        # Use a blank page map and visited set for each run
        page_map = {}
        visited = set()

        try:
            # Run the conversion
            result = _pdf_obj_to_json(obj, page_map, visited)

            # The result must be serializable by the standard json library
            json_string = json.dumps(result)

            # And it should be loadable
            assert json.loads(json_string) == result

        except Exception as e:
            pytest.fail(f"Conversion failed for object {obj} (repr: {repr(obj)}): {e}")

    _run_patched_test(test_logic, obj)
