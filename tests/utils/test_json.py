import decimal

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, Stream, String

# --- Import the function to test
from pdftl.utils.json import KEY_RESOLVED_DESTINATION, pdf_obj_to_json

# --- Import this helper, as it's needed to test a specific code path
# (Assuming it's importable from your test environment)
try:
    from pdftl.utils.whatisit import is_page
except ImportError:
    # Fallback mock if not easily importable
    def is_page(obj):
        return isinstance(obj, Dictionary) and obj.get("/Type") == "/Page"


def test_json_blank():
    pdf = pikepdf.new()
    assert pdf_obj_to_json(pdf.Root) == {
        "/Pages": {"/Count": 0, "/Kids": [], "/Type": "/Pages"},
        "/Type": "/Catalog",
    }
    pdf.add_blank_page()
    assert pdf_obj_to_json(pdf.Root.Pages.Kids) == [{"Page": '"Unknown"'}]
    assert pdf_obj_to_json(
        pdf.Root.Pages.Kids,
        page_object_to_num_map={pdf.Root.Pages.Kids[0].objgen: "1"},
    ) == [{"Page": "1"}]


# --- Added Tests for Primitives and Collections ---


def test_json_primitives():
    """Tests conversion of simple Python and PDF types."""
    assert pdf_obj_to_json(None) is None
    assert pdf_obj_to_json(123) == 123
    assert pdf_obj_to_json(12.3) == 12.3
    assert pdf_obj_to_json(True) is True
    assert pdf_obj_to_json("hello") == "hello"
    assert pdf_obj_to_json(Name("/TestName")) == "/TestName"
    assert pdf_obj_to_json(String("TestString")) == "TestString"


def test_json_collections():
    """Tests basic arrays and dictionaries."""
    # Test Array
    arr = Array([1, Name("/A"), String("B"), 3.14, None])
    expected_arr = [1, "/A", "B", "3.14", None]
    assert pdf_obj_to_json(arr) == expected_arr

    # Test Dictionary
    dct = Dictionary(
        {
            "/Int": 1,
            "/Name": Name("/A"),
            "/String": String("B"),
            "/Float": 3.14,
            "/SubArr": arr,
        }
    )
    expected_dct = {
        "/Int": 1,
        "/Name": "/A",
        "/String": "B",
        "/Float": "3.14",
        "/SubArr": expected_arr,
    }
    assert pdf_obj_to_json(dct) == expected_dct


def test_json_circular_reference():
    """
    Tests that the loop detection works.
    dict_a -> dict_b -> dict_a
    """
    dict_a = Dictionary()
    dict_b = Dictionary()

    dict_a["/B"] = dict_b
    dict_b["/A"] = dict_a

    # Test conversion starting from dict_a
    result_a = pdf_obj_to_json(dict_a)

    # Expected:
    # convert(dict_a, ancestors=[])
    #   -> new_ancestors = [dict_a]
    #   -> convert(dict_b, ancestors=[dict_a])
    #     -> new_ancestors = [dict_b, dict_a]
    #     -> convert(dict_a, ancestors=[dict_b, dict_a])
    #       -> LOOP detected! index of dict_a in ancestors is 1.
    #       -> returns "Go_Up(1)"
    expected_a = {"/B": {"/A": "Go_Up(1)"}}
    assert result_a == expected_a


def test_json_indirect_object_stream():
    """
    Tests the _handle_indirect_object path.
    This path is hit by objects that are `pikepdf.Object` but are not
    a Page, Array, Dictionary, String, or Name.
    A `pikepdf.Stream` is a perfect example.
    """
    pdf = pikepdf.new()
    pdf.add_blank_page()

    # A page's /Contents is a Stream object
    contents_stream = pdf.pages[0].Contents

    # Verify our assumptions about the object
    assert isinstance(contents_stream, Stream)
    assert isinstance(contents_stream, pikepdf.Object)
    assert hasattr(contents_stream, "objgen")
    assert not is_page(contents_stream)
    assert not isinstance(contents_stream, (Array, Dictionary, String, Name))

    # The handler should return a "Ref(obj, gen)" string
    objgen = contents_stream.objgen
    expected = f"Ref({objgen[0]}, {objgen[1]})"
    assert pdf_obj_to_json(contents_stream) == expected


def test_json_unknown_type_fallback():
    """
    Tests the _handle_unknown fallback, which should just call str().
    """
    # decimal.Decimal is not a basic JSON type
    dec = decimal.Decimal("10.123")
    assert pdf_obj_to_json(dec) == "10.123"

    # Test a custom class
    class MyObject:
        def __str__(self):
            return "MyCustomObjectString"

    my_obj = MyObject()
    assert pdf_obj_to_json(my_obj) == "MyCustomObjectString"


# --- Tests for GoTo Action Resolution ---


@pytest.fixture
def pdf_mocks():
    """Pytest fixture to create mock objects for destination testing."""
    pdf = pikepdf.new()
    pdf.add_blank_page()
    page_obj = pdf.pages[0].obj
    page_objgen = page_obj.objgen

    # Map: (obj, gen) -> page number
    page_object_map = {page_objgen: 5}

    # A destination array pointing to our page
    dest_array = Array([page_obj, Name("/XYZ"), 100, 200, 0])

    # Named destinations map: name -> dest object
    named_dests = {
        "MyDest": dest_array,
        "MyDestInDict": Dictionary({"/D": dest_array}),
    }

    yield {
        "page_map": page_object_map,
        "named_dests": named_dests,
        "page_obj": page_obj,
    }

    pdf.close()


def test_json_goto_action_resolved_simple(pdf_mocks):
    """
    Tests a standard /GoTo action with a named destination
    that is a simple Array.
    """
    action_dict = Dictionary(
        {
            "/S": Name("/GoTo"),
            "/D": String("MyDest"),  # Destination is by name (a string)
        }
    )

    result = pdf_obj_to_json(action_dict, pdf_mocks["page_map"], pdf_mocks["named_dests"])

    # Check that the original keys are preserved
    assert result["/S"] == "/GoTo"
    assert result["/D"] == "MyDest"

    # Check that the new resolved key was added
    assert KEY_RESOLVED_DESTINATION in result
    expected_resolved = {
        "TargetPage": 5,
        "DestinationDetails": ["/XYZ", 100, 200, 0],
    }
    assert result[KEY_RESOLVED_DESTINATION] == expected_resolved


def test_json_goto_action_resolved_in_dict(pdf_mocks):
    """
    Tests a /GoTo action where the named destination is a Dictionary
    containing the destination array under the /D key.
    """
    action_dict = Dictionary(
        {
            "/S": Name("/GoTo"),
            "/D": String("MyDestInDict"),
        }
    )

    result = pdf_obj_to_json(action_dict, pdf_mocks["page_map"], pdf_mocks["named_dests"])

    assert KEY_RESOLVED_DESTINATION in result
    expected_resolved = {
        "TargetPage": 5,
        "DestinationDetails": ["/XYZ", 100, 200, 0],
    }
    assert result[KEY_RESOLVED_DESTINATION] == expected_resolved


def test_json_goto_action_not_modified(pdf_mocks):
    """
    Tests cases where the destination resolution should NOT run
    and the dictionary should not be modified.
    """
    page_map = pdf_mocks["page_map"]
    named_dests = pdf_mocks["named_dests"]

    # Case 1: Not a GoTo action
    action_uri = Dictionary({"/S": Name("/URI"), "/URI": String("http://eg.com")})
    result_uri = pdf_obj_to_json(action_uri, page_map, named_dests)
    assert KEY_RESOLVED_DESTINATION not in result_uri

    # Case 2: GoTo, but not a *named* dest (e.g., dest is a direct array)
    action_direct = Dictionary(
        {
            "/S": Name("/GoTo"),
            "/D": Array([pdf_mocks["page_obj"], Name("/Fit")]),
        }
    )
    result_direct = pdf_obj_to_json(action_direct, page_map, named_dests)
    assert KEY_RESOLVED_DESTINATION not in result_direct

    # Case 3: GoTo, named dest, but name is missing from named_dests map
    action_missing = Dictionary({"/S": Name("/GoTo"), "/D": String("MissingDest")})
    result_missing = pdf_obj_to_json(action_missing, page_map, named_dests)
    assert KEY_RESOLVED_DESTINATION not in result_missing

    # Case 4: GoTo, named dest, but named_dests map itself is None
    action_no_dests = Dictionary({"/S": Name("/GoTo"), "/D": String("MyDest")})
    result_no_dests = pdf_obj_to_json(action_no_dests, page_map, named_dests=None)
    assert KEY_RESOLVED_DESTINATION not in result_no_dests
