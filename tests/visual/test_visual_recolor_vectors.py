import pikepdf

import pdftl.api


def test_visual_recolor_vectors_simple(assert_pdf_match):
    """
    Verifies that basic RGB/CMYK vector fills and strokes on the main
    page canvas are successfully converted to grayscale.
    """
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    page = pdf.pages[0]

    # Draw a bright red square (rg) with a thick blue outline (RG)
    page.Contents = pdf.make_stream(b"1 0 0 rg 0 0 1 RG 5 w 50 50 100 100 re B")

    assert_pdf_match(pdf, suffix="before")

    # Run recolor pass (assuming 'recolor' or 'recolor_vectors' API command)
    result = pdftl.api.recolor_vectors(pdf=pdf, operation_args=[])

    assert_pdf_match(result, suffix="after")


def test_visual_recolor_tiling_pattern_regression(assert_pdf_match):
    """
    REGRESSION: Verifies that Type 1 Tiling Patterns are correctly crawled
    and recolored, and that the main page pattern selectors (/Pattern cs, /Pat1 scn)
    are safely ignored and passed through to the final stream.
    """
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))

    # 1. Define the exact green pattern from the bug report
    pattern_stream = pdf.make_stream(b"0 1 0 rg 0 0 10 10 re f")
    pattern_stream.update(
        {
            pikepdf.Name("/Type"): pikepdf.Name("/Pattern"),
            pikepdf.Name("/PatternType"): 1,
            pikepdf.Name("/PaintType"): 1,
            pikepdf.Name("/TilingType"): 1,
            pikepdf.Name("/BBox"): [0, 0, 10, 10],
            pikepdf.Name("/XStep"): 15,
            pikepdf.Name("/YStep"): 15,
            pikepdf.Name("/Resources"): pikepdf.Dictionary(),
        }
    )

    # 2. Embed pattern into the page's resource dictionary
    page = pdf.pages[0]
    page.Resources = pikepdf.Dictionary(Pattern=pikepdf.Dictionary(Pat1=pattern_stream))

    # 3. Paint a large square on the main canvas using the pattern
    # If the intercept bug returns, 'cs' and 'scn' will be stripped and this will paint black.
    page.Contents = pdf.make_stream(b"/Pattern cs /Pat1 scn 50 50 100 100 re f")

    # Baseline should show a vibrant green grid pattern
    assert_pdf_match(pdf, suffix="before")

    # Target logic: crawl resources and recolor
    result = pdftl.api.recolor_vectors(pdf=pdf, operation_args=[])

    # Baseline should show a perfect grayscale grid pattern
    assert_pdf_match(result, suffix="after")


def test_visual_recolor_form_xobject(assert_pdf_match):
    """
    Verifies that nested vector shapes inside a Form XObject (often
    used for grouped objects in Adobe Illustrator) are recolored.
    """
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))

    # 1. Create a Form XObject containing a yellow circle
    # (using a generic bezier curve approximation for visual testing)
    form_stream = pdf.make_stream(b"1 1 0 rg 100 100 m 100 150 150 150 150 100 c f")
    form_stream.update(
        {
            pikepdf.Name("/Type"): pikepdf.Name("/XObject"),
            pikepdf.Name("/Subtype"): pikepdf.Name("/Form"),
            pikepdf.Name("/BBox"): [0, 0, 200, 200],
            pikepdf.Name("/Resources"): pikepdf.Dictionary(),
        }
    )

    page = pdf.pages[0]
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Frm1=form_stream))

    # 2. Call the form from the main page
    page.Contents = pdf.make_stream(b"/Frm1 Do")

    # Baseline should show a bright yellow shape
    assert_pdf_match(pdf, suffix="before")

    result = pdftl.api.recolor_vectors(pdf=pdf, operation_args=[])

    # Baseline should show a light gray shape
    assert_pdf_match(result, suffix="after")
