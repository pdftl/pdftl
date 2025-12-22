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
