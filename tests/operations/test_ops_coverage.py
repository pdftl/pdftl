import io
from unittest.mock import MagicMock, patch

import pytest

import pdftl.core.constants as c
from pdftl.core.types import OpResult
from pdftl.exceptions import OperationError
from pdftl.operations.generate_fdf import generate_fdf_cli_hook
from pdftl.operations.overlay import apply_overlay
from pdftl.operations.update_info import update_info


class TestOpsCoverage:

    def test_generate_fdf_cli_hook_stdout(self):
        """
        Cover generate_fdf.py line 46:
        Checks that output_file is set to None when meta value is "-".
        """
        mock_data = io.BytesIO(b"fake fdf content")
        result = OpResult(success=True, data=mock_data, meta={c.META_OUTPUT_FILE: "-"})

        with patch("pdftl.operations.generate_fdf.smart_open") as mock_smart_open:
            mock_file_handle = MagicMock()
            mock_smart_open.return_value.__enter__.return_value = mock_file_handle

            generate_fdf_cli_hook(result, None)

            mock_smart_open.assert_called_with(None, mode="wb")

    def test_apply_overlay_stdin(self):
        """
        Cover overlay.py line 150:
        Checks that source is set to None when overlay_filename is "-".
        """
        # 1. Setup Mocks
        input_pdf = MagicMock()
        page_mock = MagicMock()
        # FIX: Provide actual coordinates so pikepdf.Rectangle(*map(float, ...)) works
        page_mock.trimbox = [0, 0, 612, 792]
        input_pdf.pages = [page_mock]

        with patch("pdftl.operations.overlay.smart_pikepdf_open") as mock_open:
            overlay_pdf = MagicMock()
            overlay_pdf.pages = [MagicMock()]
            mock_open.return_value = overlay_pdf

            # 2. Call with "-"
            apply_overlay(input_pdf, overlay_filename="-")

            # 3. Assert
            mock_open.assert_called_with(None)

    def test_update_info_value_error(self):
        """
        Cover update_info.py line 243:
        Checks that ValueError during resolution is caught and re-raised as OperationError.
        """
        mock_pdf = MagicMock()
        mock_args = ["metadata.txt"]
        mock_get_input = MagicMock()

        with patch("pdftl.operations.update_info.resolve_operation_spec") as mock_resolve:
            mock_resolve.side_effect = ValueError("Invalid metadata format")

            with pytest.raises(OperationError) as excinfo:
                update_info(mock_pdf, mock_args, mock_get_input)

            assert "Invalid metadata format" in str(excinfo.value)
