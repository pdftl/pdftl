def test_make_blank_page_with_boxes():
    import pikepdf

    from pdftl.utils.blank_page import make_blank_page

    pdf = pikepdf.new()
    page = make_blank_page(
        pdf, (0, 0, 200, 300), crop_box=(10, 10, 190, 290), trim_box=(5, 5, 195, 295)
    )
    assert list(page.MediaBox) == [0, 0, 200, 300]
    assert list(page.CropBox) == [10, 10, 190, 290]
    assert list(page.TrimBox) == [5, 5, 195, 295]
