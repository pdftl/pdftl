from unittest.mock import MagicMock, patch

import pytest

from pdftl.core.types import OpResult
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.render import render_cli_hook, render_pdf

# --- Test Core Logic (render_pdf) ---


def test_render_pdf_invalid_args():
    mock_pdf = MagicMock()
    # Test too many arguments
    with pytest.raises(InvalidArgumentError, match="takes at most one argument"):
        render_pdf(mock_pdf, ["150", "extra"])

    # Test invalid DPI
    with pytest.raises(InvalidArgumentError, match="invalid dpi"):
        render_pdf(mock_pdf, ["not-a-number"])

    with pytest.raises(InvalidArgumentError, match="positive number"):
        render_pdf(mock_pdf, ["-10"])


@patch("pdftl.operations.render.ensure_dependencies")
def test_render_pdf_generator_success(mock_ensure):
    """
    Mocks pypdfium2 to verify the generator yields images
    and preserves the original input PDF.
    """
    input_pdf = MagicMock()
    input_pdf.save = MagicMock()  # Mock saving to buffer

    # Mock the pypdfium2 library structure
    with patch.dict("sys.modules", {"pypdfium2": MagicMock()}):
        import pypdfium2

        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_bitmap = MagicMock()
        mock_pil_image = MagicMock()

        # FIXED: PdfDocument is instantiated directly, not as a context manager
        pypdfium2.PdfDocument.return_value = mock_doc

        # Setup iterator (2 pages)
        mock_doc.__iter__.return_value = iter([mock_page, mock_page])

        # Setup render chain
        mock_page.render.return_value = mock_bitmap
        mock_bitmap.to_pil.return_value = mock_pil_image

        # Execute
        result = render_pdf(input_pdf, ["72"], output_pattern="img_%d.png")

        assert result.success
        assert result.pdf is input_pdf  # Ensure pipeline continuity

        # Consume the generator
        generated_items = list(result.data)

        # Verifications
        assert len(generated_items) == 2
        assert generated_items[0] == ("img_1.png", mock_pil_image)

        # Check dpi calculation (72 input / 72 base = 1.0 scale)
        mock_page.render.assert_called_with(scale=1.0)

        # Ensure temporary buffer was used (input_pdf.save called)
        input_pdf.save.assert_called()


# --- Test CLI Hook (render_cli_hook) ---


def test_render_cli_hook_saving():
    """Test that the CLI hook iterates the generator and saves images."""
    mock_img_1 = MagicMock()
    mock_img_2 = MagicMock()

    # Data is a generator/list of (filename, image)
    data = [("page_1.png", mock_img_1), ("page_2", mock_img_2)]  # No extension, triggers fallback

    result = OpResult(success=True, data=data)

    render_cli_hook(result, "render_stage")

    # Assert saving
    mock_img_1.save.assert_called_with("page_1.png")
    mock_img_2.save.assert_called_with("page_2", format="png")


def test_render_cli_hook_error_handling():
    """Test exception handling in the save loop."""
    mock_img = MagicMock()
    mock_img.save.side_effect = ValueError("Bad format")

    result = OpResult(success=True, data=[("bad.file", mock_img)])

    with pytest.raises(InvalidArgumentError, match="Invalid render output template"):
        render_cli_hook(result, "render_stage")


from unittest.mock import patch

# --- Test Core Logic (render_pdf) ---


def test_render_pdf_invalid_args():
    mock_pdf = MagicMock()
    # Test too many arguments
    with pytest.raises(InvalidArgumentError, match="takes at most one argument"):
        render_pdf(mock_pdf, ["150", "extra"])

    # Test invalid DPI
    with pytest.raises(InvalidArgumentError, match="invalid dpi"):
        render_pdf(mock_pdf, ["not-a-number"])

    with pytest.raises(InvalidArgumentError, match="positive number"):
        render_pdf(mock_pdf, ["-10"])


@patch("pdftl.operations.render.ensure_dependencies")
def test_render_pdf_generator_success(mock_ensure):
    """
    Mocks pypdfium2 to verify the generator yields images
    and preserves the original input PDF.
    """
    input_pdf = MagicMock()
    input_pdf.save = MagicMock()  # Mock saving to buffer

    # Mock the pypdfium2 library structure
    with patch.dict("sys.modules", {"pypdfium2": MagicMock()}):
        import pypdfium2

        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_bitmap = MagicMock()
        mock_pil_image = MagicMock()

        # FIXED: PdfDocument is instantiated directly, not as a context manager
        pypdfium2.PdfDocument.return_value = mock_doc

        # Setup iterator (2 pages)
        mock_doc.__iter__.return_value = iter([mock_page, mock_page])

        # Setup render chain
        mock_page.render.return_value = mock_bitmap
        mock_bitmap.to_pil.return_value = mock_pil_image

        # Execute
        result = render_pdf(input_pdf, ["72"], output_pattern="img_%d.png")

        assert result.success
        assert result.pdf is input_pdf  # Ensure pipeline continuity

        # Consume the generator
        generated_items = list(result.data)

        # Verifications
        assert len(generated_items) == 2
        assert generated_items[0] == ("img_1.png", mock_pil_image)

        # Check dpi calculation (72 input / 72 base = 1.0 scale)
        mock_page.render.assert_called_with(scale=1.0)

        # Ensure temporary buffer was used (input_pdf.save called)
        input_pdf.save.assert_called()


@patch("pdftl.operations.render.ensure_dependencies")
def test_render_pdf_default_dpi_and_bad_pattern(mock_ensure):
    """
    Covers:
    - Line 103: Default DPI when args is empty.
    - Lines 138-140: Fallback when pattern causes TypeError.
    """
    input_pdf = MagicMock()
    input_pdf.save = MagicMock()

    with patch.dict("sys.modules", {"pypdfium2": MagicMock()}):
        import pypdfium2

        mock_doc = MagicMock()
        mock_page = MagicMock()
        # Ensure iterator works
        mock_doc.__iter__.return_value = iter([mock_page])
        pypdfium2.PdfDocument.return_value = mock_doc

        # 1. Test Default DPI (Empty Args)
        # --------------------------------
        # Pass a pattern that causes TypeError to trigger the fallback logic
        # "bad_pattern" % 1 -> TypeError in Python
        result = render_pdf(input_pdf, [], output_pattern="bad_pattern_no_format")

        # Consume generator to trigger logic
        items = list(result.data)

        # Check DPI default (150 / 72 = 2.08333...)
        mock_page.render.assert_called_with(scale=150.0 / 72.0)

        # Check Fallback Filename logic
        # Since pattern failed, it should use "page_1.png"
        assert items[0][0] == "page_1.png"


# --- Test CLI Hook (render_cli_hook) ---


def test_render_cli_hook_saving():
    """Test that the CLI hook iterates the generator and saves images."""
    mock_img_1 = MagicMock()
    mock_img_2 = MagicMock()

    # Data is a generator/list of (filename, image)
    data = [("page_1.png", mock_img_1), ("page_2", mock_img_2)]  # No extension, triggers fallback

    result = OpResult(success=True, data=data)

    render_cli_hook(result, "render_stage")

    # Assert saving
    mock_img_1.save.assert_called_with("page_1.png")
    mock_img_2.save.assert_called_with("page_2", format="png")


def test_render_cli_hook_error_handling():
    """Test exception handling in the save loop."""
    mock_img = MagicMock()
    mock_img.save.side_effect = ValueError("Bad format")

    result = OpResult(success=True, data=[("bad.file", mock_img)])

    with pytest.raises(InvalidArgumentError, match="Invalid render output template"):
        render_cli_hook(result, "render_stage")


def test_render_cli_hook_empty():
    """Covers Line 61: Return early if data is empty."""
    # Should not raise error or try to loop
    render_cli_hook(OpResult(success=True, data=[]), "stage")
