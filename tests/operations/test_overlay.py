from unittest.mock import MagicMock, patch

import pytest

from pdftl.exceptions import OperationError
from pdftl.operations.overlay import apply_overlay


import pikepdf


def test_apply_overlay_empty_pdf():
    mock_input_pdf = MagicMock()
    mock_overlay_pdf = MagicMock()
    mock_overlay_pdf.pages = []
    mock_overlay_pdf.__enter__.return_value = mock_overlay_pdf

    with patch("pdftl.operations.overlay.smart_pikepdf_open", return_value=mock_overlay_pdf):
        # Updated regex to match the new error message
        with pytest.raises(OperationError, match="is empty"):
            apply_overlay(mock_input_pdf, "empty_overlay.pdf", [])


def test_apply_overlay_missing_layer_name_value(two_page_pdf):
    with pytest.raises(OperationError, match="requires a value"):
        apply_overlay(pikepdf.open(two_page_pdf), "-", ["layer_name"], on_top=True)


def test_apply_overlay_with_ocg_layer(two_page_pdf, tmp_path):
    # Create a real PDF file to use as the stamp
    stamp_path = tmp_path / "stamp.pdf"
    with pikepdf.new() as stamp:
        stamp.add_blank_page()
        stamp.save(stamp_path)

    result = apply_overlay(
        pikepdf.open(two_page_pdf), str(stamp_path), ["layer_name", "MyLayer"], on_top=True
    )

    assert result.success
    # Verify OCG structure in the output PDF
    assert "/OCProperties" in result.pdf.Root

    # Check if the overlay XObject on page 1 was tagged with the OCG
    page = result.pdf.pages[0]
    xobjs = page.Resources.XObject
    found_oc = any("/OC" in x for x in xobjs.values())
    assert found_oc


def test_apply_overlay_underlay(two_page_pdf, tmp_path):
    """Covers overlay.py:158 — the add_underlay branch (on_top=False)."""
    bg_path = tmp_path / "bg.pdf"
    with pikepdf.new() as bg:
        bg.add_blank_page()
        bg.save(bg_path)

    result = apply_overlay(pikepdf.open(two_page_pdf), str(bg_path), [], on_top=False)
    assert result.success


# --- OVERLAY/STAMP TESTS ---


@pytest.fixture
def stamp_pdf_path(tmp_path):
    """Creates a 1-page PDF to act as a stamp/overlay."""
    p = pikepdf.new()
    p.add_blank_page()
    output = tmp_path / "stamp.pdf"
    p.save(output)
    return str(output)


def test_overlay_stamp_basic(two_page_pdf, stamp_pdf_path):
    """Test applying a stamp (overlay)."""
    with pikepdf.open(two_page_pdf) as pdf:
        # apply_overlay(input_pdf, overlay_filename, ...)
        apply_overlay(pdf, stamp_pdf_path, [], on_top=True)

        # We verify success by checking the file structure implicitly
        # (pikepdf handles the heavy lifting)
        assert len(pdf.pages) == 2


def test_overlay_background(two_page_pdf, stamp_pdf_path):
    """Test applying a background (underlay)."""
    with pikepdf.open(two_page_pdf) as pdf:
        apply_overlay(pdf, stamp_pdf_path, [], on_top=False)
        assert len(pdf.pages) == 2


def test_overlay_missing_file_error(two_page_pdf):
    """Test error when overlay file doesn't exist."""
    with pikepdf.open(two_page_pdf) as pdf:
        with pytest.raises(FileNotFoundError):
            apply_overlay(pdf, "non_existent_file.pdf", [])


def test_apply_overlay_stdin():
    """
    Checks that source is set to None when overlay_filename is "-".
    """
    # 1. Setup Mocks
    input_pdf = MagicMock()
    page_mock = MagicMock()
    # FIX: Provide actual coordinates so pikepdf.Rectangle(*map(float, ...)) works
    page_mock.trimbox = [0, 0, 612, 792]
    input_pdf.pages = [page_mock]

    with patch("pdftl.operations.overlay.smart_pikepdf_open") as mock_open:
        overlay_pdf = MagicMock()
        overlay_pdf.pages = [MagicMock()]
        mock_open.return_value = overlay_pdf

        # 2. Call with "-"
        apply_overlay(input_pdf, overlay_filename="-", operation_args=[])

        # 3. Assert
        mock_open.assert_called_with(None)
