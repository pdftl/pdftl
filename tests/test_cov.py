def test_calculate_matrix_returns_none_when_no_dims(monkeypatch):
    from pdftl.operations.place import _calculate_transformation_matrix

    class DummyPage:
        pass

    # Force the edge case
    monkeypatch.setattr(
        "pdftl.operations.place.get_visible_page_dimensions",
        lambda page: None,
    )

    result = _calculate_transformation_matrix(DummyPage(), [])
    assert result is None


def test_calculate_transformation_matrix_returns_none_when_dims_missing(monkeypatch):
    from pdftl.operations.place import _calculate_transformation_matrix

    class DummyPage:
        pass

    # Force the edge case: no visible page dimensions
    monkeypatch.setattr(
        "pdftl.operations.place.get_visible_page_dimensions",
        lambda page: None,
    )

    result = _calculate_transformation_matrix(DummyPage(), [])

    assert result is None


from pdftl.operations.place import place_content


def test_place_content_skips_invalid_page_numbers(mocker):
    import pikepdf

    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()

    mocker.patch(
        "pdftl.operations.place.page_numbers_matching_page_spec",
        return_value=[0, 2],  # invalid for a 1-page PDF
    )

    result = place_content(pdf, ["1(shift=10,10)"])

    assert result.success is True


import pikepdf

from pdftl.api import _normalize_inputs


def test_normalize_inputs_user_opened_list():
    pdf = pikepdf.Pdf.new()

    inputs, opened = _normalize_inputs(
        user_inputs=None,
        user_opened=[pdf],
        password=None,
    )

    assert opened == {0: pdf}
    assert inputs == ["<obj-0>"]
