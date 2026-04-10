import pikepdf

from pdftl.api import cat


def test_cat_step(twelve_page_pdf):
    in_pdf = pikepdf.open(twelve_page_pdf)
    assert len(in_pdf.pages) == 12

    out = cat(twelve_page_pdf, operation_args=["1-4"])
    assert len(out.pages) == 4
    for output_index in range(4):
        assert (
            out.pages[output_index].Contents.read_bytes()
            == in_pdf.pages[output_index].Contents.read_bytes()
        )

    out = cat(twelve_page_pdf, operation_args=["step3"])
    assert len(out.pages) == 4
    for output_index, input_page_number in enumerate([1, 4, 7, 10]):
        assert (
            out.pages[output_index].Contents.read_bytes()
            == in_pdf.pages[input_page_number - 1].Contents.read_bytes()
        )

    out = cat(twelve_page_pdf, operation_args=["step2"])
    assert len(out.pages) == 6
    for output_index, input_page_number in enumerate([1, 3, 5, 7, 9, 11]):
        assert (
            out.pages[output_index].Contents.read_bytes()
            == in_pdf.pages[input_page_number - 1].Contents.read_bytes()
        )

    out = cat(twelve_page_pdf, operation_args=["5-endstep2"])
    assert len(out.pages) == 4
    for output_index, input_page_number in enumerate([5, 7, 9, 11]):
        assert (
            out.pages[output_index].Contents.read_bytes()
            == in_pdf.pages[input_page_number - 1].Contents.read_bytes()
        )
