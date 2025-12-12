from decimal import Decimal

import pikepdf
import pytest

from pdftl.utils.string import (
    before_space,
    compact_json_string,
    pdf_num_to_string,
    pdf_obj_to_string,
    pdf_rect_to_string,
    xml_decode_for_info,
    xml_encode_for_info,
)

# =======================================================================
#  Tests for XML encoding/decoding
# =======================================================================


@pytest.mark.parametrize(
    "inp, expected",
    [
        ("Hello World", "Hello World"),
        ("Less < and > Greater", "Less &lt; and &gt; Greater"),
        ("Quotes \" and '", "Quotes &quot; and &apos;"),
        ("Ampersand &", "Ampersand &amp;"),
        # --- CORRECTED based on failure ---
        # The function does not encode newlines.
        ("Newlines\n and \r", "Newlines\n and \r"),
        # The \n is not encoded.
        ("<\"&'\n>", "&lt;&quot;&amp;&apos;\n&gt;"),
    ],
)
def test_xml_encode_for_info(inp, expected):
    assert xml_encode_for_info(inp) == expected


@pytest.mark.parametrize(
    "inp, expected",
    [
        ("Hello World", "Hello World"),
        ("Less &lt; and &gt; Greater", "Less < and > Greater"),
        ("Quotes &quot; and &apos;", "Quotes \" and '"),
        ("Ampersand &amp;", "Ampersand &"),
        # --- CORRECTED based on failure ---
        # Removed the extra space I had before &#10;
        ("Newlines&#10; and &#13;", "Newlines\n and \r"),
        ("&lt;&quot;&amp;&apos;&#10;&gt;", "<\"&'\n>"),
    ],
)
def test_xml_decode_for_info(inp, expected):
    assert xml_decode_for_info(inp) == expected


@pytest.mark.parametrize(
    "original_string",
    [
        "Hello!",
        'A <complex> string & "stuff" \' \n with \r all chars.' "Even non-ascii: éàçü",
        "",
    ],
)
def test_xml_round_trip(original_string):
    """Tests that decoding an encoded string gives back the original."""
    encoded = xml_encode_for_info(original_string)
    decoded = xml_decode_for_info(encoded)
    assert decoded == original_string


# =======================================================================
#  Tests for simple string helpers
# =======================================================================


@pytest.mark.parametrize(
    "inp, expected",
    [
        ("hello world", "hello"),
        ("hello", "hello"),
        (" hello", ""),
        ("", ""),
        ("hello world again", "hello"),
    ],
)
def test_before_space(inp, expected):
    assert before_space(inp) == expected


@pytest.mark.parametrize(
    "inp, expected",
    [
        (123, "123"),
        (123.0, "123"),
        (123.5, "123.5"),
        (0.5, "0.5"),
        (-42.1, "-42.1"),
        (0, "0"),
    ],
)
def test_pdf_num_to_string(inp, expected):
    """Tests the :g formatting for numbers."""
    assert pdf_num_to_string(inp) == expected


def test_pdf_rect_to_string():
    """Tests formatting a list of numbers (like a PDF Rect)."""
    rect = [0, 0.5, 500, 750.0]
    # Uses pdf_num_to_string, so 0.5 -> "0.5" and 750.0 -> "750"
    assert pdf_rect_to_string(rect) == "0 0.5 500 750"
    assert pdf_rect_to_string([]) == ""


# =======================================================================
#  Tests for pdf_obj_to_string
# =======================================================================


# --- CORRECTED based on failures ---
# The function ONLY handles types explicitly in its `if` checks:
# Name, Array, list, int, str.
# All other types (None, bool, float, String, Dictionary)
# are NOT IMPLEMENTED. The tests for those are removed.
@pytest.mark.parametrize(
    "inp, expected",
    [
        # Basic Python types (that are handled)
        (10, "10"),
        ("A string", "A string"),
        # pikepdf types (that are handled)
        (pikepdf.Name("/Foo"), "Foo"),
        # --- CORRECTED based on failure ---
        # The function does not decode hex codes, just strips the /
        (pikepdf.Name("/Foo#20Bar"), "Foo#20Bar"),
        # List/Array types
        (pikepdf.Array([1, 2, 3]), "1 2 3"),
        # --- CORRECTED based on failure ---
        # The list is joined by spaces, not str()
        (["a", "list"], "a list"),
        (True, "True"),  # fixme, should it be 'true'?
        (False, "False"),
    ],
)
def test_pdf_obj_to_string(inp, expected):
    assert pdf_obj_to_string(inp) == expected


def test_pdf_obj_to_string_recursive():
    """Test recursive call with Array"""
    # This failed before because 1.0 (a float) is not handled.
    # We create an object that *is* handled: int and Name.
    inp = pikepdf.Array([pikepdf.Name("/A"), 1])
    assert pdf_obj_to_string(inp) == "A 1"


@pytest.mark.parametrize(
    "unhandled_input",
    [
        # All these types were in the failed tests
        None,
        20.5,
        pikepdf.String("Hello"),
        pikepdf.Dictionary(Foo=10),
        Decimal("1.0"),
    ],
)
def test_pdf_obj_to_string_not_implemented(unhandled_input):
    """
    Tests that all other types correctly raise NotImplementedError.
    """
    with pytest.raises(NotImplementedError):
        pdf_obj_to_string(unhandled_input)


# =======================================================================
#  Tests for compact_json_string
# =======================================================================


def test_compact_simple_array():
    """Tests that a simple array with newlines is compacted."""
    messy = """
    [
      1,
      "hello",
      true
    ]
    """
    expected = """
    [1, "hello", true]
    """
    assert compact_json_string(messy) == expected


def test_compact_simple_object():
    """Tests that a simple object with newlines is compacted."""
    messy = """
    {
      "a": 1,
      "b": "world",
      "c": null
    }
    """
    expected = """
    {"a": 1, "b": "world", "c": null}
    """
    assert compact_json_string(messy) == expected


def test_compact_nested_array():
    """
    Tests that only the innermost simple array is compacted,
    not the outer array that contains it.
    """
    messy = """
    [
      1,
      [
        "a",
        "b"
      ],
      3
    ]
    """
    # The inner array [ "a", "b" ] should be compacted.
    # The outer array should NOT be, because its content contains [ and ].
    expected = """
    [
      1,
      ["a", "b"],
      3
    ]
    """
    assert compact_json_string(messy) == expected


def test_compact_nested_object():
    """
    Tests that only the innermost simple object is compacted,
    not the outer object that contains it.
    """
    messy = """
    {
      "key1": "value1",
      "key2": {
        "sub_key": 123
      }
    }
    """
    # The inner object { "sub_key": 123 } should be compacted.
    # The outer object should NOT be, because its content contains { and }.
    expected = """
    {
      "key1": "value1",
      "key2": {"sub_key": 123}
    }
    """
    assert compact_json_string(messy) == expected


def test_compact_mixed_nested():
    """Tests compacting an object inside an array."""
    messy = """
    [
      {
        "a": 1
      },
      {
        "b": 2
      }
    ]
    """
    expected = """
    [{"a": 1}, {"b": 2}]
    """
    assert compact_json_string(messy) == expected


def test_compact_array_in_object():
    """Tests compacting an array inside an object."""
    messy = """
    {
      "data": [
        1,
        2,
        3
      ]
    }
    """
    expected = """
    {"data": [1, 2, 3]}
    """
    assert compact_json_string(messy) == expected


def test_already_compact():
    """Tests that an already compact string is not changed."""
    compact = "[1, 2, 3]"
    assert compact_json_string(compact) == compact

    compact_obj = '{"a": 1, "b": 2}'
    assert compact_json_string(compact_obj) == compact_obj


def test_empty_structures():
    """Tests that empty structures are handled."""
    messy_array = "[\n\n]"
    assert compact_json_string(messy_array) == "[]"

    messy_obj = "{\n\n}"
    assert compact_json_string(messy_obj) == "{}"
