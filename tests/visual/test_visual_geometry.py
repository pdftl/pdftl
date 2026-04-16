import pikepdf

import pdftl.api


def test_visual_montage_rotated(pdf_factory, assert_pdf_match):
    """
    Tests that the montage API correctly places a natively rotated PDF
    into a 2x2 grid without double-rotating or breaking bounds.
    """
    base_pdf_path = pdf_factory(6)
    pdf = pikepdf.Pdf.open(base_pdf_path)
    pdf.pages[0].Rotate = 90
    pdf.pages[1].Rotate = 180
    pdf.pages[2].Rotate = 270
    pdf.pages[4].Rotate = 180
    pdf.pages[5].Rotate = 90
    result = pdftl.api.montage(pdf=pdf, operation_args=["grid=2x2"])
    assert_pdf_match(result)


def test_visual_montage_cropped(pdf_factory, assert_pdf_match):
    """
    Tests that the montage API correctly centers a cropped PDF (origin shift)
    into a 2x2 grid without pushing the visual content out of bounds.
    """
    # 1. Arrange: Create the cropped input file
    base_pdf_path = pdf_factory(4)
    pdf = pikepdf.Pdf.open(base_pdf_path)
    # Crop 100pts off the bottom-left of the standard A4 (595x842)
    pdf.pages[0].CropBox = [100, 100, 595, 842]

    result = pdftl.api.montage(pdf=pdf, operation_args=["grid=2x2"])
    assert_pdf_match(result)


def test_visual_booklet_mixed_orientations(pdf_factory, assert_pdf_match):
    """
    Tests that the booklet layout engine gracefully handles a single PDF
    that mixes both normal and rotated pages.
    """
    rotated_pdf = pikepdf.Pdf.open(pdf_factory(4))
    rotated_pdf.pages[0].Rotate = 90
    rotated_pdf.pages[1].Rotate = 180
    rotated_pdf.pages[2].Rotate = -90
    result = pdftl.api.booklet(pdf=rotated_pdf)
    assert_pdf_match(result)


def test_visual_booklet_mixed_orientations2(pdf_factory, assert_pdf_match):
    """
    Tests that the booklet layout engine gracefully handles a single PDF
    that mixes both normal and rotated pages.
    """
    rotated_pdf = pikepdf.Pdf.open(pdf_factory(4))
    rotated_pdf.pages[0].Rotate = 90
    rotated_pdf.pages[1].Rotate = 180
    rotated_pdf.pages[2].Rotate = 270
    result = pdftl.api.booklet(pdf=rotated_pdf)
    assert_pdf_match(result)


def test_visual_booklet_rotated(pdf_factory, assert_pdf_match):
    """
    Tests that the booklet layout engine gracefully handles a single PDF
    that mixes both normal and rotated pages.
    """
    rotated_pdf = pikepdf.Pdf.open(pdf_factory(1))
    rotated_pdf.pages[0].Rotate = -90
    result = pdftl.api.booklet(pdf=rotated_pdf)
    assert_pdf_match(result)
