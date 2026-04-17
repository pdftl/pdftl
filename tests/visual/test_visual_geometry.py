import pikepdf

import pdftl.api


def test_visual_montage_rotated(six_page_rotated_pdf, assert_pdf_match):
    assert_pdf_match(pdftl.api.montage(pdf=six_page_rotated_pdf, operation_args=["grid=2x2"]))


def test_visual_montage_cropped(pdf_factory, assert_pdf_match):
    """
    Tests that the montage API correctly centers a cropped PDF (origin shift)
    into a 2x2 grid without pushing the visual content out of bounds.
    """
    base_pdf_path = pdf_factory(4)
    pdf = pikepdf.Pdf.open(base_pdf_path)
    pdf.pages[0].CropBox = [100, 100, 595, 842]
    assert_pdf_match(pdftl.api.montage(pdf=pdf, operation_args=["grid=2x2"]))


def test_visual_booklet_mixed_orientations(pdf_factory, assert_pdf_match):
    """
    Tests that the booklet layout engine gracefully handles a single PDF
    that mixes both normal and rotated pages.
    """
    rotated_pdf = pikepdf.Pdf.open(pdf_factory(4))
    rotated_pdf.pages[0].Rotate = 90
    rotated_pdf.pages[1].Rotate = 180
    rotated_pdf.pages[2].Rotate = -90
    assert_pdf_match(pdftl.api.booklet(pdf=rotated_pdf))


def test_visual_booklet_mixed_orientations2(pdf_factory, assert_pdf_match):
    """
    Tests that the booklet layout engine gracefully handles a single PDF
    that mixes both normal and rotated pages.
    """
    rotated_pdf = pikepdf.Pdf.open(pdf_factory(4))
    rotated_pdf.pages[0].Rotate = 90
    rotated_pdf.pages[1].Rotate = 180
    rotated_pdf.pages[2].Rotate = 270
    assert_pdf_match(pdftl.api.booklet(pdf=rotated_pdf))


def test_visual_booklet_rotated(pdf_factory, assert_pdf_match):
    """
    Tests that the booklet layout engine gracefully handles a single PDF
    that mixes both normal and rotated pages.
    """
    rotated_pdf = pikepdf.Pdf.open(pdf_factory(1))
    rotated_pdf.pages[0].Rotate = -90
    assert_pdf_match(pdftl.api.booklet(pdf=rotated_pdf))
