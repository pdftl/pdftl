import pikepdf
import pytest

from pdftl.operations.rebox import crop_or_clip_pages

# see also test_chop*


def test_calculate_new_box_rotation_180():
    """Tests margin un-rotation for 180 degree pages."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))
    pdf.pages[0]["/Rotate"] = 180
    result = crop_or_clip_pages(pdf, ["1-end(10pt)"])
    assert result.success
    # Margins should be applied symmetrically
    box = result.pdf.pages[0].mediabox
    assert float(box[0]) == pytest.approx(10.0)
    assert float(box[1]) == pytest.approx(10.0)


def test_calculate_new_box_rotation_270():
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))
    pdf.pages[0]["/Rotate"] = 270
    result = crop_or_clip_pages(pdf, ["1-end(10pt)"])
    assert result.success
