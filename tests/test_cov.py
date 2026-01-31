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
