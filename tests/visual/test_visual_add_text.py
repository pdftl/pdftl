import itertools

import pdftl.api


def test_visual_add_text_positions(six_page_rotated_pdf, assert_pdf_match):
    def docmd(a, b):
        return f"/{a}-{b}/(position={a}-{b},color=.5 .5 0, size=30)"

    vpos = ["bottom", "mid", "top"]
    hpos = ["left", "center", "right"]

    op_args = [docmd(a, b) for a, b in itertools.product(vpos, hpos)]
    result = pdftl.api.add_text(pdf=six_page_rotated_pdf, operation_args=op_args)
    assert_pdf_match(result, suffix="after")
