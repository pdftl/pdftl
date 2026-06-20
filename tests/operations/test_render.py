import logging
from unittest.mock import MagicMock, patch

import pytest

from pdftl.core.core_types import OpResult
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.render import _save_single_pdf, render_cli_hook, render_pdf


def test_render_cli_hook_jpg_extension():
    mock_img = MagicMock()
    data = [("page_1.jpg", mock_img)]
    result = OpResult(success=True, data=data)
    render_cli_hook(result, "render_stage", None)
    mock_img.save.assert_called_with("page_1.jpg", format="JPEG")


# --- Test Core Logic (render_pdf) ---


def test_render_pdf_invalid_args():
    mock_pdf = MagicMock()
    mock_pdf.pages = [MagicMock()] * 150
    # Test too many arguments
    with pytest.raises(InvalidArgumentError, match="[Ii]nvalid page spec modifier"):
        render_pdf(mock_pdf, ["150", "extra"])

    # Test invalid DPI
    with pytest.raises(InvalidArgumentError, match="invalid dpi"):
        render_pdf(mock_pdf, ["dpi=not-a-number"])

    with pytest.raises(InvalidArgumentError, match="positive number"):
        render_pdf(mock_pdf, ["dpi=-10"])


# --- Test CLI Hook (render_cli_hook) ---


def test_render_cli_hook_saving():
    """Test that the CLI hook iterates the generator and saves images."""
    mock_img_1 = MagicMock()
    mock_img_2 = MagicMock()

    # Data is a generator/list of (filename, image)
    data = [("page_1.png", mock_img_1), ("page_2", mock_img_2)]  # No extension, triggers fallback

    result = OpResult(success=True, data=data)

    render_cli_hook(result, "render_stage", None)

    # Assert saving
    mock_img_1.save.assert_called_with("page_1.png", format="PNG")
    mock_img_2.save.assert_called_with("page_2", format="PNG")


def test_render_cli_hook_error_handling():
    """Test exception handling in the save loop."""
    mock_img = MagicMock()
    mock_img.save.side_effect = ValueError("Bad format")

    result = OpResult(success=True, data=[("bad.file", mock_img)])

    with pytest.raises(InvalidArgumentError, match="Invalid render output template"):
        render_cli_hook(result, "render_stage", None)


def test_render_cli_hook_empty():
    """Covers Line 61: Return early if data is empty."""
    # Should not raise error or try to loop
    render_cli_hook(OpResult(success=True, data=[]), "stage", None)


@patch("pdftl.utils.dependencies.ensure_dependencies")
def test_render_pdf_generator_success(mock_ensure):
    input_pdf = MagicMock()
    # We need at least 2 pages in the "source" for our spec to work
    input_pdf.pages = [MagicMock(), MagicMock()]
    input_pdf.save = MagicMock()

    mock_pypdfium2 = MagicMock()
    mock_pypdfium2.__spec__ = MagicMock()

    with patch.dict("sys.modules", {"pypdfium2": mock_pypdfium2}):
        import pypdfium2

        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_bitmap = MagicMock()
        mock_pil_image = MagicMock()

        pypdfium2.PdfDocument.return_value = mock_doc

        mock_doc.__getitem__.return_value = mock_page
        mock_doc.__len__.return_value = 2

        mock_page.render.return_value = mock_bitmap
        mock_bitmap.to_pil.return_value = mock_pil_image

        result = render_pdf(input_pdf, ["1,2"], output_pattern="img_%d.png")

        assert result.success
        generated_items = list(result.data)

        assert len(generated_items) == 2
        assert generated_items[0][0] == "img_1.png"
        assert generated_items[1][0] == "img_2.png"


@patch("pdftl.utils.dependencies.ensure_dependencies")
def test_render_pdf_default_dpi_and_bad_pattern(mock_ensure):
    """
    Covers:
    - Default DPI when args is empty.
    - Fallback when pattern causes TypeError.
    """
    input_pdf = MagicMock()
    # Mock the input PDF to have 1 page so expand_specs_to_pages is happy
    input_pdf.pages = [MagicMock()]
    input_pdf.save = MagicMock()

    # Create a proper mock for the pypdfium2 module
    mock_pypdfium2 = MagicMock()
    mock_pypdfium2.__spec__ = MagicMock()

    with patch.dict("sys.modules", {"pypdfium2": mock_pypdfium2}):
        import pypdfium2

        # Setup the document and page mocks
        mock_doc = MagicMock(name="mock_doc")
        mock_page = MagicMock(name="mock_page")

        # Ensure it reports 1 page so range(len(ui_pdf)) works
        mock_doc.__len__.return_value = 1

        # This is the critical fix: Ensure indexing returns our mock_page
        mock_doc.__getitem__.return_value = mock_page

        # Ensure the PDF loader returns our mock_doc
        pypdfium2.PdfDocument.return_value = mock_doc

        # Execute with empty args (DPI should default to 150)
        # And a pattern with no %d (should trigger TypeError fallback)
        result = render_pdf(input_pdf, [], output_pattern="manual_name.png")

        # Consume the generator to trigger the rendering logic
        items = list(result.data)

        # 1. Check DPI default (150 DPI / 72 base = 2.0833...)
        # We check mock_page because ui_pdf[0] should now correctly return it
        mock_page.render.assert_called_with(scale=150.0 / 72.0)

        # 2. Check fallback filename logic
        # Since "manual_name.png" % 1 raises TypeError, it should return the raw string
        assert items[0][0] == "manual_name.png"


def test_save_single_pdf_logic(tmp_path):
    output_file = str(tmp_path / "test.pdf")
    mock_img1 = MagicMock()
    mock_img2 = MagicMock()

    # 1. Success Path (Lines 65, 70-71)
    gen = iter([("p1.png", mock_img1), ("p2.png", mock_img2)])
    count = _save_single_pdf(gen, output_file, 150.0)

    assert count == 2
    mock_img1.save.assert_called_once_with(
        output_file, "PDF", resolution=150.0, save_all=True, append_images=[mock_img2]
    )

    # 2. Empty Generator Path (Lines 66-67)
    assert _save_single_pdf(iter([]), output_file, 150.0) == 0


def test_save_single_pdf_error(tmp_path):
    # 3. Error Path (Lines 72-73)
    mock_img = MagicMock()
    mock_img.save.side_effect = OSError("Disk full")
    gen = iter([("p1.png", mock_img)])

    with pytest.raises(InvalidArgumentError, match="Failed to render single PDF"):
        _save_single_pdf(gen, "bad.pdf", 150.0)


def test_render_cli_hook_triggers_single_pdf(tmp_path, caplog):
    output_pdf = str(tmp_path / "merged.pdf")
    mock_img = MagicMock()

    # Create an OpResult that looks like a single-PDF render
    result = OpResult(
        success=True,
        pdf=MagicMock(),
        data=iter([("dummy.png", mock_img)]),
        meta={"output_pattern": output_pdf, "dpi": 300.0},
    )

    with caplog.at_level(logging.INFO):
        render_cli_hook(result, None, None)

    # Check lines 112-113 coverage
    assert "Rendered 1 pages into a single PDF" in caplog.text
    mock_img.save.assert_called_once()
