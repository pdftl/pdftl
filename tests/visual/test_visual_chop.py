import pikepdf

import pdftl.api


def test_visual_chop(pdf_factory, assert_pdf_match):
    base_pdf_path = pdf_factory(6)
    pdf = pikepdf.Pdf.open(base_pdf_path)
    pdf.pages[0].Rotate = 90
    pdf.pages[1].Rotate = 180
    pdf.pages[2].Rotate = 270
    pdf.pages[4].Rotate = 180
    pdf.pages[5].Rotate = 90
    result = pdftl.api.chop(pdf=pdf, operation_args=[])
    assert_pdf_match(result)

def test_visual_chop_rows(pdf_factory, assert_pdf_match):
    base_pdf_path = pdf_factory(6)
    pdf = pikepdf.Pdf.open(base_pdf_path)
    pdf.pages[0].Rotate = 90
    pdf.pages[1].Rotate = 180
    pdf.pages[2].Rotate = 270
    pdf.pages[4].Rotate = 180
    pdf.pages[5].Rotate = 90
    result = pdftl.api.chop(pdf=pdf, operation_args=["rows"])
    assert_pdf_match(result)

def test_visual_chop_rows_three(pdf_factory, assert_pdf_match):
    base_pdf_path = pdf_factory(6)
    pdf = pikepdf.Pdf.open(base_pdf_path)
    pdf.pages[0].Rotate = 90
    pdf.pages[1].Rotate = 180
    pdf.pages[2].Rotate = 270
    pdf.pages[4].Rotate = 180
    pdf.pages[5].Rotate = 90
    result = pdftl.api.chop(pdf=pdf, operation_args=["rows3"])
    assert_pdf_match(result)

def test_visual_chop_cols(pdf_factory, assert_pdf_match):
    base_pdf_path = pdf_factory(6)
    pdf = pikepdf.Pdf.open(base_pdf_path)
    pdf.pages[0].Rotate = 90
    pdf.pages[1].Rotate = 180
    pdf.pages[2].Rotate = 270
    pdf.pages[4].Rotate = 180
    pdf.pages[5].Rotate = 90
    result = pdftl.api.chop(pdf=pdf, operation_args=["cols"])
    assert_pdf_match(result)

