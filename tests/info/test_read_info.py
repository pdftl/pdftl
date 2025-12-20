from unittest.mock import MagicMock, patch

import pikepdf
import pytest

# Import real pikepdf types for mocking
from pikepdf import Array, Dictionary, Name, OutlineItem, String

# Functions to test
from pdftl.info.read_info import (
    _get_destination_array,
    get_named_destinations,
    pdf_id_metadata_as_strings,
    resolve_page_number,
)

# --- Fixtures ---


@pytest.fixture
def mock_pdf():
    """Provides a MagicMock for a pikepdf.Pdf object."""
    return MagicMock(spec=["trailer", "Root"])


@pytest.fixture
def mock_named_dests():
    """Provides a MagicMock for a NameTree."""
    return MagicMock(spec=["get"])


@pytest.fixture
def mock_pdf_pages():
    """
    Provides a list of mock page objects using MagicMock
    to allow setting the .objgen attribute.
    """
    pages = []
    for i in range(3):
        # Use MagicMock to be able to set .objgen
        page = MagicMock(spec=pikepdf.Page)
        page.objgen = (i + 1, 0)
        pages.append(page)
    return pages


# --- Tests for pdf_id_metadata_as_strings ---


class TestPdfIdMetadataAsStrings:
    def test_with_id(self, mock_pdf):
        mock_pdf.trailer.ID = [b"\xde\xad\xbe\xef", b"\xca\xfe\xba\xbe"]
        result = pdf_id_metadata_as_strings(mock_pdf)
        assert result == ["deadbeef", "cafebabe"]

    def test_empty_id(self, mock_pdf):
        mock_pdf.trailer.ID = []
        assert pdf_id_metadata_as_strings(mock_pdf) == []

    def test_no_id_attribute(self, mock_pdf):
        mock_pdf.trailer.ID = None
        assert pdf_id_metadata_as_strings(mock_pdf) == []

    def test_no_trailer(self, mock_pdf):
        mock_pdf.trailer = None
        assert pdf_id_metadata_as_strings(mock_pdf) == []


# --- Tests for get_named_destinations ---


class TestGetNamedDestinations:
    def test_with_dests(self, mock_pdf):
        # Mock pikepdf object structure, which uses attribute access
        # for children and 'in' for key checks.
        mock_names = MagicMock()
        mock_names.__contains__.side_effect = lambda key: key == "/Dests"
        mock_names.Dests = "DestsObject"

        mock_pdf.Root = MagicMock()
        mock_pdf.Root.__contains__.side_effect = lambda key: key == "/Names"
        mock_pdf.Root.Names = mock_names

        # We patch NameTree to just return its input, to check it was called
        with patch("pikepdf.NameTree", lambda x: x) as mock_name_tree:
            result = get_named_destinations(mock_pdf)
            assert result == "DestsObject"

    def test_no_dests(self, mock_pdf):
        # Mock pikepdf object structure
        mock_names = MagicMock()
        mock_names.__contains__.side_effect = (
            lambda key: key != "/Dests"
        )  # Will be false for /Dests

        mock_pdf.Root = MagicMock()
        mock_pdf.Root.__contains__.side_effect = lambda key: key == "/Names"
        mock_pdf.Root.Names = mock_names
        assert get_named_destinations(mock_pdf) is None

    def test_no_names(self, mock_pdf):
        mock_pdf.Root = MagicMock()
        mock_pdf.Root.__contains__.side_effect = (
            lambda key: key != "/Names"
        )  # Will be false for /Names
        assert get_named_destinations(mock_pdf) is None

    def test_empty_root(self, mock_pdf):
        mock_pdf.Root = MagicMock()
        mock_pdf.Root.__contains__.return_value = False  # No keys
        assert get_named_destinations(mock_pdf) is None


# --- Tests for _get_destination_array ---


class TestGetDestinationArray:
    def test_direct_array_destination(self, mock_named_dests):
        # Mock item must be spec=OutlineItem to pass isinstance
        mock_item = MagicMock(spec=OutlineItem)
        expected_array = Array([Name("/Test")])
        mock_item.destination = expected_array

        result = _get_destination_array(mock_item, mock_named_dests)
        assert result == expected_array

    def test_action_d_destination(self, mock_named_dests):
        mock_item = MagicMock(spec=OutlineItem)
        expected_array = Array([Name("/Test")])
        mock_item.destination = None
        mock_item.action = MagicMock()  # Create the action attribute
        mock_item.action.D = expected_array

        result = _get_destination_array(mock_item, mock_named_dests)
        assert result == expected_array

    def test_named_dest_string_resolves_to_dict(self, mock_named_dests):
        mock_item = MagicMock(spec=OutlineItem)
        expected_array = Array([Name("/Test")])
        dest_name = String("MyDest")
        mock_item.destination = dest_name

        # The destination can be a dictionary containing the array
        dest_dict = Dictionary(D=expected_array)
        mock_named_dests.get.return_value = dest_dict

        result = _get_destination_array(mock_item, mock_named_dests)
        mock_named_dests.get.assert_called_with("MyDest")
        assert result == expected_array

    def test_named_dest_name_resolves_to_array(self, mock_named_dests):
        mock_item = MagicMock(spec=OutlineItem)
        expected_array = Array([Name("/Test")])
        dest_name = Name("/OtherDest")
        mock_item.destination = dest_name

        # The destination can be the array itself
        mock_named_dests.get.return_value = expected_array

        result = _get_destination_array(mock_item, mock_named_dests)
        mock_named_dests.get.assert_called_with("OtherDest")
        assert result == expected_array

    def test_named_dest_not_found(self, mock_named_dests):
        mock_item = MagicMock(spec=OutlineItem)
        mock_item.destination = String("MissingDest")
        mock_named_dests.get.return_value = None

        result = _get_destination_array(mock_item, mock_named_dests)
        assert result is None

    def test_invalid_item_type(self, mock_named_dests):
        assert _get_destination_array(123, mock_named_dests) is None
        assert _get_destination_array(None, mock_named_dests) is None
        assert _get_destination_array([], mock_named_dests) is None  # Fails isinstance

    def test_item_no_destination_or_action(self, mock_named_dests):
        mock_item = MagicMock(spec=OutlineItem)
        mock_item.destination = None
        mock_item.action = None
        assert _get_destination_array(mock_item, mock_named_dests) is None

    def test_item_with_action_but_no_d(self, mock_named_dests):
        mock_item = MagicMock(spec=OutlineItem)
        mock_item.destination = None
        mock_item.action = MagicMock()
        mock_item.action.D = None  # Has action, but no /D
        assert _get_destination_array(mock_item, mock_named_dests) is None


# --- Tests for resolve_page_number ---
@patch("pdftl.info.read_info._get_destination_array")
class TestResolvePageNumber:

    # new=lambda... does NOT inject an argument.
    # The signature must not include 'mock_is_page'.
    @patch("pdftl.info.read_info.is_page", new=lambda x: True)
    def test_resolves_correct_page(self, mock_get_dest_array, mock_pdf_pages, mock_named_dests):
        # We want to find the 2nd page
        target_page = mock_pdf_pages[1]  # This mock has .objgen == (2, 0)

        # Mock _get_destination_array to return an array pointing to this page.
        mock_dest_array = [target_page, Name("/XYZ"), 0, 0]
        mock_get_dest_array.return_value = mock_dest_array

        mock_item = MagicMock()
        result = resolve_page_number(mock_item, mock_pdf_pages, mock_named_dests)

        # Loop should check: (1,0) == (2,0) -> False
        # Loop should check: (2,0) == (2,0) -> True. return 1 + 1 = 2
        assert result == 2
        mock_get_dest_array.assert_called_with(mock_item, mock_named_dests)

    # new=lambda... does NOT inject an argument.
    # The signature must not include 'mock_is_page'.
    @patch("pdftl.info.read_info.is_page", new=lambda x: True)
    def test_page_not_in_list(self, mock_get_dest_array, mock_pdf_pages, mock_named_dests):
        other_page = MagicMock(spec=pikepdf.Page)
        other_page.objgen = (99, 0)  # This objgen is not in mock_pdf_pages

        mock_dest_array = [other_page, Name("/XYZ"), 0, 0]
        mock_get_dest_array.return_value = mock_dest_array

        mock_item = MagicMock()
        result = resolve_page_number(mock_item, mock_pdf_pages, mock_named_dests)
        assert result is None
        mock_get_dest_array.assert_called_with(mock_item, mock_named_dests)

    # Correct argument order. Method patch (mock_is_page) comes FIRST.
    @patch("pdftl.info.read_info.is_page", return_value=True)
    def test_no_destination_array(
        self, mock_is_page, mock_get_dest_array, mock_pdf_pages, mock_named_dests
    ):
        mock_get_dest_array.return_value = None  # Simulate failure
        mock_item = MagicMock()
        result = resolve_page_number(mock_item, mock_pdf_pages, mock_named_dests)

        assert result is None
        # The assertion is correct: if dest_array is None, is_page is never called.
        mock_is_page.assert_not_called()
        mock_get_dest_array.assert_called_with(mock_item, mock_named_dests)

    # Correct argument order. Method patch (mock_is_page) comes FIRST.
    @patch("pdftl.info.read_info.is_page", return_value=True)
    def test_empty_destination_array(
        self, mock_is_page, mock_get_dest_array, mock_pdf_pages, mock_named_dests
    ):
        mock_get_dest_array.return_value = Array([])  # Simulate empty array
        mock_item = MagicMock()
        result = resolve_page_number(mock_item, mock_pdf_pages, mock_named_dests)

        assert result is None
        # The assertion is correct: if dest_array is empty, is_page is never called.
        mock_is_page.assert_not_called()
        mock_get_dest_array.assert_called_with(mock_item, mock_named_dests)

    # Correct argument order. Method patch (mock_is_page_inner) comes FIRST.
    @patch("pdftl.info.read_info.is_page", return_value=False)
    def test_dest_array_not_a_page(
        self, mock_is_page_inner, mock_get_dest_array, mock_pdf_pages, mock_named_dests
    ):
        not_a_page = Name("/NotAPage")
        mock_get_dest_array.return_value = Array([not_a_page, Name("/XYZ"), 0, 0])

        mock_item = MagicMock()
        result = resolve_page_number(mock_item, mock_pdf_pages, mock_named_dests)

        assert result is None
        # The assertion is correct: is_page(not_a_page) should be called.
        mock_is_page_inner.assert_called_with(not_a_page)
        mock_get_dest_array.assert_called_with(mock_item, mock_named_dests)
