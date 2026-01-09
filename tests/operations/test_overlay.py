from unittest.mock import MagicMock, patch

import pytest

from pdftl.operations.overlay import apply_overlay


def test_apply_overlay_empty_pdf():
    """
    Covers line 150: raise ValueError("Overlay PDF has no pages")
    """
    mock_input_pdf = MagicMock()

    # Create a mock for the overlay PDF that has an empty page list
    mock_overlay_pdf = MagicMock()
    mock_overlay_pdf.pages = []

    with patch("pikepdf.open", return_value=mock_overlay_pdf):
        with pytest.raises(ValueError, match="Overlay PDF has no pages"):
            apply_overlay(mock_input_pdf, "empty_overlay.pdf")
