from unittest.mock import MagicMock, patch

import pytest

from pdftl.operations.shuffle import shuffle_pdfs
from pdftl.exceptions import InvalidArgumentError


def test_shuffle_no_page_tuples():
    """
    Covers: raise InvalidArgumentError("Range specifications gave no pages")
    """
    mock_pdf = MagicMock()

    # We mock the helper to return empty list to trigger line 90
    with patch("pdftl.operations.shuffle._get_page_tuples_array", return_value=[]):
        with pytest.raises(InvalidArgumentError, match="Range specifications gave no pages"):
            shuffle_pdfs(inputs=["A"], specs=[], opened_pdfs=[mock_pdf])


def test_shuffle_no_opened_pdfs():
    """
    Checks that shuffle_pdfs raises a ValueError if opened_pdfs is empty.
    The pipeline manager should ensure this never happens from the CLI.
    """
    with pytest.raises(ValueError, match="opened_pdfs must contain something"):
        shuffle_pdfs(inputs=[], specs=[], opened_pdfs=[])
