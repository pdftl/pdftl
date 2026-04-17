import pikepdf

import pdftl.api


def test_visual_stamp_simple(six_page_rotated_pdf, two_page_pdf, assert_pdf_match):
    result = pdftl.api.stamp(pdf=six_page_rotated_pdf, operation_args=[two_page_pdf])
    assert_pdf_match(result)


def test_visual_background_simple(six_page_rotated_pdf, two_page_pdf, assert_pdf_match):
    result = pdftl.api.background(pdf=six_page_rotated_pdf, operation_args=[two_page_pdf])
    assert_pdf_match(result)


def test_visual_multistamp_simple(six_page_rotated_pdf, two_page_pdf, assert_pdf_match):
    result = pdftl.api.multistamp(pdf=six_page_rotated_pdf, operation_args=[two_page_pdf])
    assert_pdf_match(result)


def test_visual_multibackground_simple(six_page_rotated_pdf, two_page_pdf, assert_pdf_match):
    result = pdftl.api.multibackground(pdf=six_page_rotated_pdf, operation_args=[two_page_pdf])
    assert_pdf_match(result)
