# tests/operations/test_place.py

import pikepdf
import pytest

from pdftl.operations.place import place_content


@pytest.fixture
def two_page_pdf(tmp_path):
    # Create a simple 2-page PDF (Letter size 612x792)
    pdf_path = tmp_path / "2_page.pdf"
    with pikepdf.new() as pdf:
        pdf.add_blank_page(page_size=(612, 792))
        pdf.add_blank_page(page_size=(612, 792))
        pdf.save(pdf_path)
    return pdf_path


def test_place_shift_absolute(two_page_pdf):
    """
    Test shifting content by a fixed amount.
    Input: shift=100,200
    Expected Matrix: [1, 0, 0, 1, 100, 200]
    """
    with pikepdf.open(two_page_pdf) as pdf:
        place_content(pdf, ["1(shift=100,200)"])

        # Inspect Page 1
        p1 = pdf.pages[0]
        # DIRECT CALL: No helper needed
        instructions = list(pikepdf.parse_content_stream(p1))

        # instructions[0] is ('q',)
        # instructions[1] is ([1,0,0,1,100,200], 'cm')

        # Verify wrapper start
        assert str(instructions[0][1]) == "q"

        # Verify matrix
        operands = instructions[1][0]
        op_name = str(instructions[1][1])
        assert op_name == "cm"
        assert float(operands[4]) == 100.0
        assert float(operands[5]) == 200.0


def test_place_scale_center(two_page_pdf):
    with pikepdf.open(two_page_pdf) as pdf:
        # Scale 0.5 center
        place_content(pdf, ["1(scale=0.5)"])
        # Verify no crash and modification occurred
        instructions = list(pikepdf.parse_content_stream(pdf.pages[0]))
        assert len(instructions) > 0


def test_place_relative_math(two_page_pdf):
    """
    Test logic: shift=50%,0
    If page width is 612 (Letter), dx should be 306.
    """
    with pikepdf.open(two_page_pdf) as pdf:
        page = pdf.pages[0]
        # Ensure we read the dimension correctly for expectation
        width = float(page.MediaBox[2])

        place_content(pdf, ["1(shift=50%,0)"])

        instructions = list(pikepdf.parse_content_stream(page))
        operands = instructions[1][0]  # The 'cm' operands

        dx = float(operands[4])

        assert dx == pytest.approx(width * 0.5, 0.01)


def test_named_anchors_logic(two_page_pdf):
    """Ensure named anchors don't crash and produce matrices."""
    with pikepdf.open(two_page_pdf) as pdf:
        place_content(pdf, ["1(scale=0.5:top-left)"])
        instructions = list(pikepdf.parse_content_stream(pdf.pages[0]))
        assert len(instructions) > 0


def test_compound_coordinates_integration(two_page_pdf):
    """
    Verify full stack: Parser -> Evaluator -> PDF
    Input: shift=50%+10, 10
    """
    with pikepdf.open(two_page_pdf) as pdf:
        width = float(pdf.pages[0].MediaBox[2])
        expected_dx = (width * 0.5) + 10.0

        # Parser handles spaces: "shift=50%+10, 10"
        place_content(pdf, ["1(shift=50%+10, 10)"])

        instructions = list(pikepdf.parse_content_stream(pdf.pages[0]))
        operands = instructions[1][0]
        dx = float(operands[4])

        assert dx == pytest.approx(expected_dx, 0.01)
