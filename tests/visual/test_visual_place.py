import pikepdf

import pdftl.api


def test_visual_place_rotated(six_page_rotated_pdf, two_page_pdf, assert_pdf_match):
    plan = {
        "1": "shift=100,200",
        "2": "spin=60",
        "3": "scale=0.7",
        "4": "scale=0.7;spin=60",
        "6": "shift=100,200;spin=-30",
    }
    op_args = [f"{a}({b})" for a, b in plan.items()]
    result = pdftl.api.place(pdf=six_page_rotated_pdf, operation_args=op_args)
    text_args = [f"{a}/{b}/(size=20)" for a, b in plan.items()]
    result = pdftl.api.add_text(pdf=result, operation_args=text_args)
    assert_pdf_match(result)


def test_visual_place_unrotated(six_page_pdf, two_page_pdf, assert_pdf_match):
    plan = {
        "1": "shift=100,200",
        "2": "spin=60",
        "3": "scale=0.7",
        "4": "scale=0.7;spin=60",
        "6": "shift=100,200;spin=-30",
    }
    op_args = [f"{a}({b})" for a, b in plan.items()]
    result = pdftl.api.place(pdf=six_page_pdf, operation_args=op_args)
    text_args = [f"{a}/{b}/(size=20)" for a, b in plan.items()]
    result = pdftl.api.add_text(pdf=result, operation_args=text_args)
    assert_pdf_match(result)


def test_visual_place_shift_rotated_page(six_page_rotated_pdf, assert_pdf_match):
    op_args = [
        "1-3(shift=50%,0)",
        "4-6(shift=0,50%)",
    ]
    result = pdftl.api.place(pdf=six_page_rotated_pdf, operation_args=op_args)
    assert_pdf_match(result, suffix="after")


def test_visual_place_noop(six_page_rotated_pdf, assert_pdf_match):
    assert_pdf_match(pdftl.api.place(pdf=six_page_rotated_pdf, operation_args=["(scale=1)"]))
