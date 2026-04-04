from unittest.mock import MagicMock, patch

import pytest

from pdftl.exceptions import OperationError
from pdftl.operations.overlay import apply_overlay


def test_apply_overlay_empty_pdf():
    mock_input_pdf = MagicMock()

    # Create a mock for the overlay PDF that has an empty page list
    mock_overlay_pdf = MagicMock()
    mock_overlay_pdf.pages = []

    # ADD THIS LINE: Make the mock work properly as a context manager
    mock_overlay_pdf.__enter__.return_value = mock_overlay_pdf

    with patch("pikepdf.open", return_value=mock_overlay_pdf):
        with pytest.raises(OperationError, match="contains no pages"):
            # assuming your function call looks like this:
            apply_overlay(mock_input_pdf, "empty_overlay.pdf")
