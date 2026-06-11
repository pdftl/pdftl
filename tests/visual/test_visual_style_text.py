import pdftl.api


def test_visual_style_text_stroke_and_fill(six_page_rotated_pdf, assert_pdf_match):
    """
    Verify that text styling cleanly overrides fills and outlines text.
    Applies a heavy red outline (stroke) with a distinct blue fill on pages 1-3.
    """
    op_args = [
        "1-3",
        "stroke=2.0",
        "stroke_color=1 0 0",  # Red outline
        "fill_color=0 0 1",  # Blue fill
    ]
    result = pdftl.api.style_text(pdf=six_page_rotated_pdf, operation_args=op_args)
    assert_pdf_match(result)


def test_visual_style_text_percentage_and_render_modes(six_page_rotated_pdf, assert_pdf_match):
    """
    Verify that relative percentage-based stroke widths scale dynamically
    with the target font sizes across differently rotated pages.
    """
    pdf = pdftl.api.add_text(
        pdf=six_page_rotated_pdf,
        operation_args=[
            "/Counter={global_count}/(position=mid-center,size=5, offset-y=200)",
            "/Counter={global_count}/(position=mid-center,size=10, offset-y=150)",
            "/Counter={global_count}/(position=mid-center,size=20, offset-y=100)",
            "/Counter={global_count}/(position=mid-center,size=40,offset-y=-100)",
            "/Counter={global_count}/(position=mid-center,size=80,offset-y=-200)",
        ],
    )
    assert_pdf_match(pdf, suffix="before")
    op_args = [
        "1-3",
        "stroke=3%",
        "stroke_color=0 1 0",  # Green outline
    ]
    result1 = pdftl.api.style_text(pdf=pdf, operation_args=op_args)
    op_args2 = [
        "4-6",
        "stroke=3",  # Absolute
        "stroke_color=0 1 1",  # Cyan outline
    ]
    result2 = pdftl.api.style_text(pdf=result1, operation_args=op_args2)
    assert_pdf_match(result2, suffix="after")
