import pdftl.api


def test_visual_crop_fit(six_page_rotated_pdf, assert_pdf_match):
    assert_pdf_match(pdftl.api.crop(pdf=six_page_rotated_pdf, operation_args=["(fit)"]))


def test_visual_crop_to_a4(six_page_rotated_pdf, assert_pdf_match):
    assert_pdf_match(pdftl.api.crop(pdf=six_page_rotated_pdf, operation_args=["(a4)"]))


def test_visual_crop_to_a6(six_page_rotated_pdf, assert_pdf_match):
    assert_pdf_match(pdftl.api.crop(pdf=six_page_rotated_pdf, operation_args=["(a6)"]))


def test_visual_crop_to_a6_preview(six_page_rotated_pdf, assert_pdf_match):
    assert_pdf_match(pdftl.api.crop(pdf=six_page_rotated_pdf, operation_args=["(a6)", "preview"]))


def test_visual_crop_manual(six_page_rotated_pdf, assert_pdf_match):
    assert_pdf_match(pdftl.api.crop(pdf=six_page_rotated_pdf, operation_args=["(-10mm)"]))
