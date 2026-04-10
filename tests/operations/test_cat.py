import pikepdf

from pdftl.api import cat


def test_cat_step(twelve_page_pdf):
    in_pdf = pikepdf.open(twelve_page_pdf)
    assert len(in_pdf.pages) == 12

    out = cat(twelve_page_pdf, operation_args=["1-4"])
    assert len(out.pages) == 4
    out = cat(twelve_page_pdf, operation_args=["step3"])
    assert len(out.pages) == 4
    out = cat(twelve_page_pdf, operation_args=["step2"])
    assert len(out.pages) == 6
    out = cat(twelve_page_pdf, operation_args=["5-endstep2"])
    assert len(out.pages) == 4
