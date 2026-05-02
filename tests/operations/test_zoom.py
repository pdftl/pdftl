# tests/operations/test_zoom.py

import pytest
import pikepdf
from pdftl.operations.zoom import zoom_pages, _calculate_zoom_factor


def test_zoom_basic_fit_a4():
    # Create a 100x100 square page
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(100, 100))

    # Scale to A4 (595 pts)
    # Since it is square, ratio should be 5.9527
    spec = ["1(A4)"]
    result = zoom_pages(pdf, spec)

    # MediaBox should be scaled
    new_box = [float(x) for x in result.pdf.pages[0].MediaBox]
    assert new_box[2] == pytest.approx(595, abs=0.01)
    assert new_box[3] == pytest.approx(595, abs=0.01)


def test_zoom_explicit_width():
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(100, 200))  # Aspect 1:2

    # Only target width=200. Aspect preserved, so height becomes 400.
    spec = ["1(width=200)"]
    result = zoom_pages(pdf, spec)

    new_box = [float(x) for x in result.pdf.pages[0].MediaBox]
    assert new_box[2] == 200.0
    assert new_box[3] == 400.0


def test_zoom_explicit_height():
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(100, 100))

    spec = ["1(height=50)"]
    result = zoom_pages(pdf, spec)

    new_box = [float(x) for x in result.pdf.pages[0].MediaBox]
    assert new_box[2] == 50.0
    assert new_box[3] == 50.0


def test_zoom_box_fit_rectangular():
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(100, 100))

    # Fit inside 200x400 envelope.
    # Limiting factor is width (200/100=2.0) vs height (400/100=4.0)
    # Should scale by 2.0 to fit inside.
    spec = ["1(200,400)"]
    result = zoom_pages(pdf, spec)

    new_box = [float(x) for x in result.pdf.pages[0].MediaBox]
    assert new_box[2] == 200.0
    assert new_box[3] == 200.0


def test_zoom_constraints_shrink():
    # Page is 100x100, target is 200x200
    # Normally factor is 2.0, but 'shrink' should force it to 1.0
    assert _calculate_zoom_factor(100, 100, {}, ["200", "shrink"]) == 1.0

    # If page is 200, target is 100, shrink should allow it (0.5)
    assert _calculate_zoom_factor(200, 200, {}, ["100", "shrink"]) == 0.5


def test_zoom_constraints_grow():
    # Page is 200x200, target is 100x100
    # Normally factor is 0.5, but 'grow' should force it to 1.0
    assert _calculate_zoom_factor(200, 200, {}, ["100", "grow"]) == 1.0

    # If page is 50, target 100, grow should allow it (2.0)
    assert _calculate_zoom_factor(50, 50, {}, ["100", "grow"]) == 2.0


def test_zoom_no_target_defaults_to_identity():
    # If no dimensions are provided in parens, factor should be 1.0
    assert _calculate_zoom_factor(100, 100, {}, []) == 1.0


def test_zoom_unrecognized_bare_tokens_ignored():
    # 'random_text' should be filtered out from dimension calculation
    # Only '100' should be used as target width/height
    factor = _calculate_zoom_factor(50, 50, {}, ["100", "random_text"])
    assert factor == 2.0


def test_zoom_handles_zero_dimensions():
    # Page 100x100 -> Target width 0
    # Should result in a scale factor of 0.0, not 1.0
    factor = _calculate_zoom_factor(100, 100, {"width": "0"}, [])
    assert factor == 0.0


def test_zoom_handles_missing_dimensions(mocker):
    mocker.patch("pdftl.operations.zoom.get_visible_page_dimensions", return_value=None)
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(100, 100))

    result = zoom_pages(pdf, ["1(A4)"])
    # In-place means the page is still there!
    assert len(result.pdf.pages) == 1
    # And it shouldn't have changed size because factor calculation was skipped
    assert float(result.pdf.pages[0].MediaBox[2]) == 100.0


def test_zoom_invalid_format_raises():
    pdf = pikepdf.new()
    pdf.add_blank_page()

    # Update to catch the custom exception and matching message
    from pdftl.exceptions import InvalidArgumentError

    with pytest.raises(InvalidArgumentError, match="Invalid zoom spec"):
        zoom_pages(pdf, ["1-3_no_parens"])
