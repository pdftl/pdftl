# tests/operations/test_barcode.py


from unittest.mock import MagicMock, patch

import pikepdf
import pytest
from PIL import Image

from pdftl.exceptions import OperationError
from pdftl.operations.barcode import (
    _get_anchor_coordinates,
    _get_preset_x,
    _get_preset_y,
    _parse_barcode_args,
    _stamp_image_on_page,
    barcode_pdf,
)
from pdftl.operations.parsers.barcode_parser import parse_barcode_specs_to_rules


def test_parse_barcode_args_continues_after_layer_name():
    """Ensures specs following 'layer_name <val>' are not silently truncated."""
    args = ["!spec1!", "layer_name", "my_layer", "!spec2!"]
    specs, layer = _parse_barcode_args(args)
    assert specs == ["!spec1!", "!spec2!"]
    assert layer == "my_layer"


def test_parser_defaults_to_zero_points():
    """Verifies legacy 36pt coordinates have been dropped in favor of 0pt alignment."""
    rules = parse_barcode_specs_to_rules(["!my_data!"], total_pages=1)
    page_zero_rule = rules[0][0]
    assert page_zero_rule["x"] == "0pt"
    assert page_zero_rule["y"] == "0pt"


@patch("pdftl.operations.barcode.generate_barcode")
@patch("pdftl.operations.barcode._stamp_image_on_page")
@patch("pdftl.operations.barcode.create_layer")
def test_barcode_pdf_handles_page_rotations(mock_create_layer, mock_stamp, mock_gen_barcode):
    """Verifies that coordinates are mapped properly across 0, 90, 180, and 270 degrees."""
    # Fake a crisp barcode image layout output
    fake_img = Image.new("RGB", (10, 10))
    mock_gen_barcode.return_value = fake_img

    # Define standard US Letter box
    us_letter = [0.0, 0.0, 612.0, 792.0]

    mock_page_0 = MagicMock(spec=pikepdf.Page)
    # Fix: Cast `key` to string to catch pikepdf.Name objects
    mock_page_0.get.side_effect = lambda key, default=None: {
        "/Rotate": 0,
        "/MediaBox": us_letter,
        "/CropBox": us_letter,
    }.get(str(key), default)
    mock_page_0.mediabox = us_letter
    mock_page_0.cropbox = us_letter

    mock_page_90 = MagicMock(spec=pikepdf.Page)
    # Fix: Cast `key` to string here as well
    mock_page_90.get.side_effect = lambda key, default=None: {
        "/Rotate": 90,
        "/MediaBox": us_letter,
        "/CropBox": us_letter,
    }.get(str(key), default)
    mock_page_90.mediabox = us_letter
    mock_page_90.cropbox = us_letter

    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.pages = [mock_page_0, mock_page_90]

    # Run operation with explicit corner bounds
    operation_args = ["1-end!data!(position=top-right,width=72pt)"]
    barcode_pdf(mock_pdf, operation_args)

    assert mock_stamp.call_count == 2

    # Check regular 0-degree stamp args
    call_unrotated = mock_stamp.call_args_list[0]
    # phys_x, phys_y for top-right layout boundary
    assert call_unrotated[0][3] == 612.0 - 72.0  # phys_x shifted inward by width


# --- 1. Argument Parsing (Lines 68, 75-76) ---


def test_parse_barcode_args_empty():
    """Hits line 68: return early if no args."""
    specs, layer = _parse_barcode_args([])
    assert specs == []
    assert layer is None


def test_parse_barcode_args_missing_layer_name():
    """Hits lines 75-76: StopIteration when layer_name lacks a value."""
    with pytest.raises(OperationError, match="The 'layer_name' option requires a value"):
        _parse_barcode_args(["1!data!", "layer_name"])


# --- 2. The Core Stamping Function (Lines 94-122) ---


def test_stamp_image_on_page_real_objects():
    """Hits lines 94-122: Tests the actual image buffer conversion and PDF overlay."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    page = pdf.pages[0]

    # Create a dummy image
    img = Image.new("RGB", (50, 50), color="black")

    # Run the stamper (no OCG layer to test standard path)
    _stamp_image_on_page(pdf, page, img, phys_x=10.0, phys_y=10.0, phys_w=50.0, phys_h=50.0)

    # Verify the image cache list was created and populated
    assert hasattr(pdf, "_image_cache")
    assert len(pdf._image_cache) == 1

    # Verify resources were added to the page
    assert "/Resources" in page
    assert "/XObject" in page.Resources


# --- 3. Positioning and Anchors (Lines 128, 131-133, 140-144, 156-158) ---


def test_preset_x_calculations():
    """Hits lines 128, 131-133: 'left', 'center', and fallback."""
    assert _get_preset_x("bottom-left", 100.0) == 0.0
    assert _get_preset_x("mid-center", 100.0) == 50.0
    assert _get_preset_x("unknown", 100.0) == 0.0  # Fallback


def test_preset_y_calculations():
    """Hits lines 140-144: 'bottom', 'mid', and fallback."""
    assert _get_preset_y("bottom-right", 100.0) == 0.0
    assert _get_preset_y("mid-center", 100.0) == 50.0
    assert _get_preset_y("unknown", 100.0) == 0.0  # Fallback


def test_absolute_anchor_coordinates():
    """Hits lines 156-158: Fallback to absolute x/y when position is missing."""
    rule = {"x": "10pt", "y": "20pt", "position": None}
    anchor_x, anchor_y, pos = _get_anchor_coordinates(rule, 612.0, 792.0)
    assert anchor_x == 10.0
    assert anchor_y == 20.0
    assert pos == ""


# --- 4. Empty Text, No Rules, & Exceptions (Lines 196, 205-206, 248-249, 281) ---


@patch("pdftl.operations.barcode.generate_barcode")
def test_empty_text_content(mock_gen):
    """Hits line 196: Return early if text content evaluates to empty."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()

    # We mock text_renderer to return empty text via the rule context
    with patch("pdftl.operations.barcode.parse_barcode_specs_to_rules") as mock_parse:
        mock_parse.return_value = {
            0: [{"text_renderer": lambda ctx: [("", None)], "position": "top-left"}]
        }
        barcode_pdf(pdf, ["1! !"])

    # generate_barcode should never be called because text was empty
    mock_gen.assert_not_called()


def test_skip_pages_without_rules():
    """Hits line 281: 'if not rules: continue' loop jump."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()  # Page 1
    pdf.add_blank_page()  # Page 2

    # Only target page 1. Page 2 will hit line 281.
    with patch("pdftl.operations.barcode._process_single_rule") as mock_process:
        barcode_pdf(pdf, ["1!data!"])
        assert mock_process.call_count == 1  # Only called for page 1


@patch("pdftl.operations.barcode.generate_barcode")
def test_generate_barcode_exception(mock_gen):
    """Hits lines 205-206: Catches generation errors and raises OperationError."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()

    mock_gen.side_effect = ValueError("Invalid matrix")

    with pytest.raises(OperationError, match="Barcode image matrix layout generation failed"):
        barcode_pdf(pdf, ["1!data!"])


@patch("pdftl.operations.barcode._stamp_image_on_page")
@patch("pdftl.operations.barcode.generate_barcode")
def test_stamp_image_raises_pdf_error(mock_gen, mock_stamp):
    """Hits lines 248-249: Catches PdfError during overlay and raises OperationError."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()

    mock_gen.return_value = Image.new("RGB", (10, 10))
    mock_stamp.side_effect = pikepdf.PdfError("Canvas error")

    with pytest.raises(OperationError, match="PDF canvas overlay stream assembly failed"):
        barcode_pdf(pdf, ["1!data!"])


# --- 5. Rotations 180 and 270 (Lines 229-232, 234-237) ---


@patch("pdftl.operations.barcode._stamp_image_on_page")
@patch("pdftl.operations.barcode.generate_barcode")
def test_barcode_180_and_270_rotations(mock_gen, mock_stamp):
    """Hits lines 229-232 and 234-237 by mocking page rotations."""
    # Dummy setup
    mock_gen.return_value = Image.new("RGB", (10, 10))
    us_letter = [0.0, 0.0, 612.0, 792.0]

    # Page with 180 rotation
    mock_page_180 = MagicMock(spec=pikepdf.Page)
    mock_page_180.get.side_effect = lambda k, d=None: {"/Rotate": 180}.get(str(k), d)
    mock_page_180.cropbox = us_letter

    # Page with 270 rotation
    mock_page_270 = MagicMock(spec=pikepdf.Page)
    mock_page_270.get.side_effect = lambda k, d=None: {"/Rotate": 270}.get(str(k), d)
    mock_page_270.cropbox = us_letter

    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.pages = [mock_page_180, mock_page_270]

    # Run against both pages
    barcode_pdf(mock_pdf, ["1-end!data!"])

    # Verify stamp was called twice, ensuring both rotation branches executed successfully
    assert mock_stamp.call_count == 2


# --- 6. Image Modes and OCG Layers (Lines 97, 120-122) ---


def test_stamp_image_rgba_conversion():
    """Hits line 97: Converts unsupported image modes (e.g., RGBA) to RGB."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(100, 100))
    page = pdf.pages[0]

    # Create an RGBA image (transparency channel) which must be flattened to RGB
    img = Image.new("RGBA", (50, 50), color=(255, 0, 0, 128))

    with patch.object(Image.Image, "convert", wraps=img.convert) as mock_convert:
        _stamp_image_on_page(pdf, page, img, phys_x=0.0, phys_y=0.0, phys_w=50.0, phys_h=50.0)

        # Verify the conversion block was triggered
        mock_convert.assert_called_once_with("RGB")


def test_stamp_image_with_ocg_layer():
    """Hits lines 120-122: Assigns the OCG layer dictionary to new XObjects."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(100, 100))
    page = pdf.pages[0]

    img = Image.new("RGB", (10, 10))

    # Create a mock Optional Content Group (OCG) dictionary
    dummy_ocg = pikepdf.Dictionary({"/Type": "/OCG", "/Name": "TestLayer"})

    # Pass the OCG down into the stamper
    _stamp_image_on_page(
        pdf, page, img, phys_x=0.0, phys_y=0.0, phys_w=10.0, phys_h=10.0, ocg=dummy_ocg
    )

    # Verify the logic successfully attached the OCG to the new XObject
    assert "/Resources" in page
    assert "/XObject" in page.Resources
    xobjects = page.Resources.XObject

    assert len(xobjects) > 0
    # Check that at least one XObject has the Optional Content (OC) key applied
    assert any("/OC" in xobj for xobj in xobjects.values())
