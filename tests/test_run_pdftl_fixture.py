# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/test_run_pdftl_fixture.py

import pikepdf
import pytest


def test_run_pdftl_raises_when_the_command_fails(run_pdftl, tmp_path):
    with pytest.raises(RuntimeError, match="exit code 1"):
        run_pdftl([str(tmp_path / "missing.pdf"), "cat", "output", str(tmp_path / "o.pdf")])


def test_run_pdftl_runs_a_command(run_pdftl, tmp_path):
    src, out = tmp_path / "in.pdf", tmp_path / "out.pdf"
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        pdf.add_blank_page()
        pdf.save(src)
    run_pdftl([str(src), "cat", "2", "output", str(out)])
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 1
