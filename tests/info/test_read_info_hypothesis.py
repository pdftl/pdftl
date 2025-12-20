import string
from unittest.mock import MagicMock, Mock, patch

import pikepdf
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# Import real pikepdf types for mocking
from pikepdf import Array, Dictionary, Name, OutlineItem, String

# Functions to test
from pdftl.info.read_info import _get_destination_array, resolve_page_number

# --- Reusable Mocks ---


def create_mock_page(obj_num):
    """
    Creates a mock page object using MagicMock
    to allow setting the .objgen attribute.
    """
    # Use MagicMock to be able to set .objgen, which is read-only
    # on real Dictionary objects. This mimics a real pikepdf.Page.
    page = MagicMock(spec=pikepdf.Page)
    page.objgen = (obj_num, 0)  # .objgen is a tuple
    return page


class MockNameTree:
    """A mock NameTree that can be configured by hypothesis."""

    def __init__(self, mapping):
        self._mapping = mapping
        # Create a mock 'get' that we can also spy on
        self.get = Mock(side_effect=self._internal_get)

    def _internal_get(self, key):
        return self._mapping.get(key)


# --- Hypothesis Strategies ---


# Strategy to create a dictionary of named destinations
# Keys are strings, values are either a Array or a Dictionary
# wrapping a Array
@st.composite
def named_dest_map_strategy(draw):
    page_nums = st.integers(min_value=1, max_value=100)

    # Strategy for the destination array
    dest_array_strategy = st.lists(
        st.integers(min_value=-10000, max_value=10000), min_size=1, max_size=5
    ).map(Array)

    # Strategy for the value in the NameTree
    value_strategy = st.one_of(
        dest_array_strategy,
        dest_array_strategy.map(lambda arr: Dictionary({"/D": arr})),
    )

    # Create the map
    mapping = draw(
        st.dictionaries(
            keys=st.text(min_size=1, max_size=10, alphabet=string.ascii_letters),
            values=value_strategy,
            min_size=0,
            max_size=10,
        )
    )
    return mapping


@st.composite
def mock_item_strategy(draw, named_dests_map):
    """
    Generates a mock item and the expected destination array.
    Uses the provided named_dests_map to ensure lookups can succeed.
    """
    mock_item = MagicMock(spec=OutlineItem)

    # Choose a type of destination
    dest_type = draw(st.integers(min_value=1, max_value=4))

    if dest_type == 1:
        # 1. Direct Array destination
        expected_dest = draw(
            st.lists(st.integers(min_value=-10000, max_value=10000), min_size=1, max_size=5).map(
                Array
            )
        )
        mock_item.destination = expected_dest
        mock_item.action = None

    elif dest_type == 2:
        # 2. Action with /D destination
        expected_dest = draw(
            st.lists(st.integers(min_value=-10000, max_value=10000), min_size=1, max_size=5).map(
                Array
            )
        )
        mock_item.destination = None
        mock_item.action = MagicMock()
        mock_item.action.D = expected_dest

    elif dest_type == 3:
        # 3. Named destination (String or Name)

        # Decide if we *must* create a key
        map_keys = list(named_dests_map.keys())
        if not map_keys:
            # Can't test this case if map is empty,
            # so just return a simple array dest
            expected_dest = Array([1, 2, 3])
            mock_item.destination = expected_dest
            mock_item.action = None
            return mock_item, expected_dest

        # Pick a key that exists in the map
        dest_name_str = draw(st.sampled_from(map_keys))

        # Get the expected result from the map
        expected_obj = named_dests_map[dest_name_str]
        if isinstance(expected_obj, Dictionary):
            expected_dest = expected_obj.D
        else:
            expected_dest = expected_obj

        # Randomly choose to represent it as a String or Name
        if draw(st.booleans()):
            mock_item.destination = String(dest_name_str)
        else:
            # Name objects MUST start with a /
            dest_name = Name("/" + dest_name_str)
            mock_item.destination = dest_name

        mock_item.action = None

    else:  # dest_type == 4
        # 4. No destination
        mock_item.destination = None
        mock_item.action = None
        expected_dest = None

    return mock_item, expected_dest


# --- Tests ---


@given(named_dests_map=named_dest_map_strategy(), data=st.data())
@settings(max_examples=100, deadline=None)
def test_get_destination_array_hypothesis(named_dests_map, data):

    mock_dests = MockNameTree(named_dests_map)
    mock_item, expected_result = data.draw(mock_item_strategy(named_dests_map))

    try:
        result = _get_destination_array(mock_item, mock_dests)
    except Exception:
        assert False, "_get_destination_array crashed"

    assert result == expected_result

    # Check if a named destination was used and looked up
    if isinstance(mock_item.destination, (String, Name)) and mock_item.action is None:
        expected_key = str(mock_item.destination)
        # If it was a Name, the key is the string *without* the /
        if isinstance(mock_item.destination, Name):
            expected_key = expected_key[1:]

        mock_dests.get.assert_called_with(expected_key)


@pytest.mark.slow
@given(num_pages=st.integers(min_value=0, max_value=20), data=st.data())
@settings(max_examples=100, deadline=None)
# Patch _get_destination_array to isolate the logic
@patch("pdftl.info.read_info._get_destination_array")
# Patch is_page to check for our MagicMock pages
@patch("pdftl.info.read_info.is_page", new=lambda x: isinstance(x, MagicMock))
def test_resolve_page_number_hypothesis(mock_get_dest_array, num_pages, data):

    # 1. create_mock_page *must* be fixed to use MagicMock and tuple
    mock_pages = [create_mock_page(i + 1) for i in range(num_pages)]

    if num_pages == 0:
        target_page_idx = -1  # No page to target
    else:
        # Target a page index, or -1 to signal no target
        target_page_idx = data.draw(st.integers(min_value=-1, max_value=num_pages - 1))

    mock_item = MagicMock()
    mock_dests = MagicMock()

    if target_page_idx == -1:
        # Create an item that will fail to resolve

        # Draw which failure case to test
        fail_case = data.draw(st.integers(min_value=1, max_value=3))
        if fail_case == 1:
            mock_get_dest_array.return_value = None  # No dest
        elif fail_case == 2:
            mock_get_dest_array.return_value = []  # Empty dest array
        else:
            # Make a mock non-page (is_page patch will return False)
            not_a_page = Name("/NotAPage")
            mock_get_dest_array.return_value = [not_a_page]

        expected_page_num = None
    else:
        # Create an item that resolves to the target page
        target_page = mock_pages[target_page_idx]

        # Return a Python list, not a pikepdf.Array,
        # since we can't store a MagicMock in a real Array.
        dest_array_list = [target_page, Name("/XYZ"), 0, 100]
        mock_get_dest_array.return_value = dest_array_list

        expected_page_num = target_page_idx + 1  # 1-indexed

    try:
        result = resolve_page_number(mock_item, mock_pages, mock_dests)
    except Exception:
        assert False, "resolve_page_number crashed"

    assert result == expected_page_num
