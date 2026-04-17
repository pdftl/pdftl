import pdftl.api


def test_visual_chop(six_page_rotated_pdf, assert_pdf_match):
    assert_pdf_match(pdftl.api.chop(pdf=six_page_rotated_pdf, operation_args=[]))


def test_visual_chop_rows(six_page_rotated_pdf, assert_pdf_match):
    assert_pdf_match(pdftl.api.chop(pdf=six_page_rotated_pdf, operation_args=["rows"]))


def test_visual_chop_rows_three(six_page_rotated_pdf, assert_pdf_match):
    assert_pdf_match(pdftl.api.chop(pdf=six_page_rotated_pdf, operation_args=["rows3"]))


def test_visual_chop_cols(six_page_rotated_pdf, assert_pdf_match):
    assert_pdf_match(pdftl.api.chop(pdf=six_page_rotated_pdf, operation_args=["cols"]))
