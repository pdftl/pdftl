# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/test_version.py

import re

from .test_harness import run_test_case

TARGET_PDFTK_VERSION = "3.3.3"


# Use the parametrize decorator to create a test for each spec
def test_version(runner, temp_dir):
    """Just test the pdftk version is OK"""
    results = run_test_case(
        runner,
        temp_dir,
        args_template="--version",
        input_pdf_generator=None,
        ignore_pdf_outputs=True,
        commands=["pdftk"],
    )
    assert re.search(r"\b3\.3\.[23]\b", results["pdftk"].stdout)
