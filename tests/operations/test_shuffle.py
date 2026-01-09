from unittest.mock import MagicMock, patch

import pytest

from pdftl.operations.shuffle import shuffle_pdfs


def test_shuffle_no_page_tuples():
    """
    Covers line 90: raise ValueError("Range specifications gave no pages")
    """
    mock_pdf = MagicMock()

    # We mock the helper to return empty list to trigger line 90
    with patch("pdftl.operations.shuffle._get_page_tuples_array", return_value=[]):
        with pytest.raises(ValueError, match="Range specifications gave no pages"):
            shuffle_pdfs(inputs=["A"], specs=[], opened_pdfs=[mock_pdf])
