# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.core.executor import registry
from pdftl.fluent import PdfPipeline, pipeline


class TestFluentApi:
    @patch("pikepdf.open")
    def test_pipeline_open_variants(self, mock_open):
        """Hit lines 24-27: PdfPipeline.open with and without password."""
        mock_pdf = MagicMock(spec=pikepdf.Pdf)
        mock_open.return_value = mock_pdf

        # Test simple open
        pipe = PdfPipeline.open("test.pdf")
        assert isinstance(pipe, PdfPipeline)
        mock_open.assert_called_with("test.pdf")

        # Test open with password (line 26 branch)
        pipe_pw = PdfPipeline.open("protected.pdf", password="secret_pass")
        mock_open.assert_called_with("protected.pdf", password="secret_pass")
        assert pipe_pw._pdf == mock_pdf

    def test_pipeline_helper(self):
        """Hit line 64: The pipeline() helper function."""
        mock_pdf = MagicMock(spec=pikepdf.Pdf)
        pipe = pipeline(mock_pdf)
        assert isinstance(pipe, PdfPipeline)
        assert pipe.native == mock_pdf

    def test_fluent_properties_and_save(self):
        """Hit lines 30, 57, 60: save, native property, and get()."""
        mock_pdf = MagicMock(spec=pikepdf.Pdf)
        pipe = PdfPipeline(mock_pdf)

        # Test .native (line 57)
        assert pipe.native == mock_pdf
        # Test .get() (line 60)
        assert pipe.get() == mock_pdf

        # Test .save() (line 30)
        pipe.save("out.pdf", static=True)
        mock_pdf.save.assert_called_once_with("out.pdf", static=True)

    def test_getattr_attribute_error(self):
        """Hit line 53: AttributeError for unknown operations."""
        mock_pdf = MagicMock(spec=pikepdf.Pdf)
        pipe = PdfPipeline(mock_pdf)
        with pytest.raises(
            AttributeError, match="'PdfPipeline' object has no attribute 'non_existent_op'"
        ):
            _ = pipe.non_existent_op

    @patch("pdftl.api.call")
    def test_fluent_chaining_logic(self, mock_call):
        """Hit lines 45-48: Chaining vs Returning data."""
        mock_pdf_1 = MagicMock(spec=pikepdf.Pdf)
        mock_pdf_2 = MagicMock(spec=pikepdf.Pdf)
        pipe = PdfPipeline(mock_pdf_1)

        # Manually register the mock operation
        registry.operations["mock_op"] = MagicMock()

        # 1. Operation returns a PDF (Line 47: Chaining)
        mock_call.return_value = mock_pdf_2
        result = pipe.mock_op()
        assert result is pipe  # Chaining
        assert pipe.native is mock_pdf_2

        # 2. Operation returns non-PDF data (Line 48: Terminal)
        mock_call.return_value = {"page_count": 5}
        result = pipe.mock_op()
        assert result == {"page_count": 5}
        # PDF state remains mock_pdf_2 from previous step
        assert pipe.native is mock_pdf_2

    def test_fluent_method_naming(self):
        """Hit line 50: fluent_method.__name__ assignment."""
        mock_pdf = MagicMock(spec=pikepdf.Pdf)
        pipe = PdfPipeline(mock_pdf)

        # Manually register an op for this test
        registry.operations["name_check_op"] = MagicMock()

        # Accessing the method should give us a function with the right name
        method = pipe.name_check_op
        assert method.__name__ == "name_check_op"


from unittest.mock import patch

import pdftl.core.constants as c


def test_fluent_dir_discovery(mocker):
    # Coverage for lines 34-36
    # Mock registry to ensure there is at least one dynamic op
    mocker.patch("pdftl.fluent.registry.operations", {"mock_op": MagicMock()})
    p = pipeline(MagicMock())

    attrs = dir(p)
    assert "mock_op" in attrs
    assert "save" in attrs  # Ensure default attrs are still there


def test_map_fluent_args_edge_cases():
    mock_pdf = MagicMock()
    p = PdfPipeline(mock_pdf)

    # Coverage for line 69: Pass INPUTS as a single string, not a list
    op_data = MagicMock()
    op_data.args = ([], {})
    res = p._map_fluent_args(op_data, (), {c.INPUTS: "single_file.pdf"})
    assert res[c.INPUTS] == [mock_pdf, "single_file.pdf"]

    # Coverage for line 83: c.INPUTS is a positional target
    # Setup: op expects (INPUT_PDF, INPUTS)
    op_data_multi = MagicMock()
    op_data_multi.args = ([c.INPUT_PDF, c.INPUTS], {})

    # Calling p.cmd("extra.pdf")
    # self._pdf satisfies INPUT_PDF, so "extra.pdf" should hit line 83
    res = p._map_fluent_args(op_data_multi, ("extra.pdf",), {})
    assert res[c.INPUTS] == [mock_pdf, "extra.pdf"]


def test_apply_metadata_inconsistency(mocker):
    # Coverage for lines 100-102
    # We want a case where registry has 'missing_op' but api module does not
    mocker.patch("pdftl.fluent.registry.operations", {"missing_op": MagicMock()})
    p = pipeline(MagicMock())

    # Patch api to ensure 'missing_op' is missing
    with patch("pdftl.api.missing_op", side_effect=AttributeError):
        # Accessing the attribute triggers __getattr__ -> _apply_metadata
        func = p.missing_op
        assert func.__name__ == "missing_op"
        # Verify it didn't crash and returned the function even without docstrings
