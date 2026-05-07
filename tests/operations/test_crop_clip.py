import pytest

from pdftl.operations.parsers.rebox_parser import parse_rebox_content


def test_parse_rebox_content_abs_valid():
    """
    Covers lines 79 and 96-100 in rebox_parser.py.
    Tests successful parsing of an absolute box specification.
    """
    # Assuming dim_str_to_pts translates raw string numbers to float/int points
    result = parse_rebox_content(
        "abs, 10, 20, 90, 80", page_width=100, page_height=100, operation="dummy_op"
    )

    assert result["type"] == "abs"
    assert result["values"] == (10, 20, 90, 80)


def test_parse_rebox_content_abs_invalid_length():
    """
    Covers lines 93-94 in rebox_parser.py.
    Tests the ValueError raised when 'abs' doesn't have exactly 4 values.
    """
    with pytest.raises(ValueError, match="Should have 4 comma-separated values following `abs`"):
        parse_rebox_content(
            "abs, 10, 20, 90", page_width=100, page_height=100, operation="dummy_op"
        )


import pikepdf

from pdftl.operations.rebox import _apply_or_preview, crop_or_clip_pages


def test_crop_with_abs_spec():
    """
    Covers lines 185-186 in crop.py.
    Ensures the crop operation correctly handles 'abs' specs from the parser.
    """
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(100, 100))

    # Passing the abs rule inside the page spec parentheses
    specs = ["1(abs, 10, 10, 90, 90)"]
    result = crop_or_clip_pages(pdf, specs, operation="crop")

    assert result.success is True
    # The mediabox should be updated to our absolute coordinates
    assert list(result.pdf.pages[0].mediabox) == [10, 10, 90, 90]


def test_clip_operation():
    """
    Covers lines 221-225 in crop.py.
    Exercises the 'clip' logic branch inside _apply_or_preview.
    """
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(100, 100))

    # Standard margin crop, but passed to the 'clip' operation
    specs = ["1(10, 10, 10, 10)"]
    result = crop_or_clip_pages(pdf, specs, operation="clip")

    assert result.success is True
    # In a clip operation, the mediabox isn't touched, but stream content is appended
    page = result.pdf.pages[0]
    page.contents_coalesce()
    assert b"q" in page.Contents.read_bytes()


def test_apply_or_preview_invalid_operation():
    """
    Covers lines 226-227 in crop.py.
    Hits the ultimate fallback ValueError for unrecognized operations.
    """
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(100, 100))

    with pytest.raises(ValueError, match="Internal error: invalid operation 'smash'"):
        _apply_or_preview(page=page, new_box=(10, 10, 90, 90), preview=False, operation="smash")
