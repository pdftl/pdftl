# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/test_suite_1.py

from pathlib import Path

import pytest

from .comparison_helpers import compare_page_count, compare_visuals
from .test_harness import run_test_case

CATR_SPECS = ["", "A1 B3-end", "B A3-end", "A A", "B Beast"]
FILENAME_ARGS = [
    '"A=pdfs/6_page.pdf" "B=pdfs/6_page.pdf"',
    '"pdfs/6_page.pdf" "pdfs/6_page.pdf"',
]


@pytest.mark.parametrize("spec", CATR_SPECS)
@pytest.mark.parametrize("filenames", FILENAME_ARGS)
def test_cat_repeats(runner, temp_dir, six_page_pdf, filenames, spec):
    """Compares the 'cat' operation for various page specifications."""
    # The 'spec' argument is passed in by pytest
    run_test_case(
        runner,
        temp_dir,
        input_pdf_generator=lambda: six_page_pdf,  # Path("pdfs/1.pdf"),
        args_template=f'{filenames} cat {spec} output "{{output}}"',
        comparison_fns=[compare_page_count, compare_visuals],
        commands=["pdftl", "pdftk"],
    )


CAT_SPECS = [
    "",
    "2east 1west",
    "r1down",
    "1-2left",
    "-end",
    "1-",
    "1-endright",
    "end-1south",
    "~1",
    "~end",
    "1-2~end",
    "1-end~2",
    "~1~4-r1",
    "-east",
    "r-",
    "-r",
    "2-",
    "-2",
    "r2-",
    "-r2",
    "2r",
    "1-end 1-end ~5even ~5odd",
    "northeveneven",
    "evensouthwestodd",
    "~even",
    "A",
    "Z",
    "- - - - - - - - - -",
    "4north 4east 4south 4west 4left 4right 4down 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4 4",
    ### "~", # expected incompatibility. We accept this as all pages.
]


@pytest.mark.slow
@pytest.mark.parametrize("spec", CAT_SPECS)
def test_cat(runner, temp_dir, six_page_pdf, spec):
    """Compares the 'cat' operation for various page specifications."""
    # The 'spec' argument is passed in by pytest
    run_test_case(
        runner,
        temp_dir,
        input_pdf_generator=lambda: six_page_pdf,  # Path("pdfs/1.pdf"),
        args_template=f'"{{input}}" cat {spec} output "{{output}}"',
        comparison_fns=[compare_page_count, compare_visuals],
        commands=["pdftl", "pdftk"],
    )


CAT_SPECS2 = ["x2", "x.5", "z1", "z0", "x1", "z-1", "x-1", "1-3z1", "1-3z-1"]
CAT_SPECS3 = [x + y for x in CAT_SPECS if len(x) < 20 for y in CAT_SPECS2]

# @pytest.mark.parametrize("spec", CAT_SPECS3)
# def test_cat_scaling(runner, temp_dir, six_page_pdf, spec):
#     """Compares the 'cat' operation for various page specifications"""
#     """involving py-specific scaling."""
#     run_test_case(
#         runner, temp_dir,
#         input_pdf_generator=lambda: Path("pdfs/1.pdf"),
#         args_template=f'"{{input}}" cat {spec} output "{{output}}"',
#         comparison_fns=[compare_page_count, compare_visuals],
#         commands=["pdftl"]
#     )

SHUFFLE_SPECS = [
    "",
    "A B",
    "B A",
    "A2 B3",
    "A1-3 Br1",
    "B~2 A1 B",
    "A B1",
    "B1 A",
]


@pytest.mark.parametrize("spec", SHUFFLE_SPECS)
def test_shuffle(runner, temp_dir, spec):
    """Compares the 'shuffle' operation for various page specifications"""
    run_test_case(
        runner,
        temp_dir,
        input_pdf_generator=lambda: Path("pdfs/6_page.pdf"),
        args_template=f'A=pdfs/1.pdf B=pdfs/6_page.pdf shuffle {spec} output "{{output}}"',
        comparison_fns=[compare_page_count, compare_visuals],
        commands=["pdftl", "pdftk"],
    )
