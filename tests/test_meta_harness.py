# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl-tests.zip/tests/test_meta_harness.py

from pathlib import Path

from .comparison_helpers import compare_page_count, compare_visuals
from .create_pdf import create_custom_pdf
from .test_harness import run_test_case

# --- Test Definitions ---


def test_cat_operation_harness(runner, temp_dir, two_page_pdf):
    """Compares the 'cat' operation."""
    run_test_case(
        runner,
        temp_dir,
        input_pdf_generator=lambda: two_page_pdf,
        args_template='"{input}" cat 2 1 output "{output}"',
        comparison_fns=[compare_page_count, compare_visuals],
    )


def test_rotate_operation_harness(runner, temp_dir, two_page_pdf):
    """Compares the 'rotate' operation visually."""
    run_test_case(
        runner,
        temp_dir,
        input_pdf_generator=lambda: two_page_pdf,
        args_template='"{input}" rotate 1east 2west output "{output}"',
        comparison_fns=[compare_page_count, compare_visuals],
    )


def test_invalid_range_error_harness(runner, temp_dir, two_page_pdf):
    """Tests that both tools produce an error for an invalid page range."""
    run_test_case(
        runner,
        temp_dir,
        input_pdf_generator=lambda: two_page_pdf,
        args_template='"{input}" cat 99 output "{output}"',
        expected_stderrs=[
            "Invalid page",
            "Range start page number exceeds size of PDF",
        ],
    )


def test_crop_operation_harness(runner, temp_dir):
    """Tests the pdftl-exclusive 'crop' feature."""

    def generated_pdf():
        path = temp_dir / "generated_for_crop.pdf"
        create_custom_pdf(str(path), pages=3)
        return path

    def validate_crop(output_path: Path):
        import math

        import fitz

        with fitz.open(output_path) as doc:
            assert len(doc) == 3, "Cropped PDF should still have 3 pages"
            for page in doc:
                expected_width = 595 - 200  # 100 from each side
                assert math.isclose(page.rect.width, expected_width), "Page width is incorrect"

    run_test_case(
        runner,
        temp_dir,
        input_pdf_generator=generated_pdf,
        args_template='"{input}" crop 1-end(100pt,100pt,100pt,100pt) output "{output}"',
        validation_fn=validate_crop,
        commands=["pdftl"],
    )
