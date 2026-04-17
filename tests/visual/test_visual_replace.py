import pdftl.api


def test_visual_replace_simple(six_page_rotated_pdf, assert_pdf_match):
    assert_pdf_match(
        pdftl.api.replace(
            pdf=six_page_rotated_pdf,
            operation_args=["/([0-9.]+) ([0-9.]+) ([0-9.]+) (RG|rg)/\\2 \\3 \\1 \\4/"],
        )
    )
