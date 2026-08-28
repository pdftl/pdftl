# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""Tests for pdftl.operations.usage"""

import json
from io import StringIO
from unittest.mock import MagicMock, patch

import pikepdf

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.operations.usage import (
    _format_size,
    _get_pdf_source_bytes_and_pdf,
    _human_bytes,
    _write_table_output,
    usage,
    usage_cli_hook,
)

# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------


def _make_sample_usage_data():
    """Return a mock dictionary matching analyze_space_usage structure."""
    return {
        "file_size": 2048,
        "objects": 10,
        "unmeasured_objects": 0,
        "categories": [
            {
                "id": "images",
                "bytes": 1024,
                "objects": 2,
            },
            {
                "id": "fonts",
                "bytes": 0,
                "objects": 0,
            },
            {
                "id": "overhead",
                "bytes": 1024,
                "objects": 8,
                "residual": True,
                "detail": [
                    {"kind": "xref", "bytes": 512},
                    {"kind": "trailer", "bytes": 512},
                    {"kind": "unreferenced", "bytes": 0},
                ],
            },
        ],
    }


def _make_simple_pdf():
    """Return an in-memory pikepdf instance."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    return pdf


# ---------------------------------------------------------------------------
# _human_bytes
# ---------------------------------------------------------------------------


class TestHumanBytes:
    def test_bytes_under_1024(self):
        assert _human_bytes(500) == "500 B"
        assert _human_bytes(0) == "0 B"

    def test_kilobytes(self):
        assert _human_bytes(1024) == "1.0 KB"
        assert _human_bytes(1536) == "1.5 KB"

    def test_megabytes(self):
        assert _human_bytes(1048576) == "1.0 MB"
        assert _human_bytes(2597152) == "2.5 MB"

    def test_gigabytes(self):
        assert _human_bytes(1073741824) == "1.0 GB"


# ---------------------------------------------------------------------------
# _format_size
# ---------------------------------------------------------------------------


class TestFormatSize:
    def test_raw_bytes_when_human_false(self):
        assert _format_size(2048, human=False) == "2048"

    def test_human_formatted_when_human_true(self):
        assert _format_size(2048, human=True) == "2.0 KB"


# ---------------------------------------------------------------------------
# _get_pdf_source_bytes_and_pdf
# ---------------------------------------------------------------------------


class TestGetPdfSourceBytes:
    def test_reads_from_disk_when_valid_filename(self, tmp_path):
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 mock content")
        pdf = _make_simple_pdf()

        data, _ = _get_pdf_source_bytes_and_pdf(str(pdf_file), pdf)
        assert data == b"%PDF-1.4 mock content"

    def test_saves_in_memory_when_filename_is_underscore(self):
        pdf = _make_simple_pdf()
        data, _ = _get_pdf_source_bytes_and_pdf("_", pdf)
        assert data.startswith(b"%PDF")

    def test_saves_in_memory_when_filename_is_none(self):
        pdf = _make_simple_pdf()
        data, _ = _get_pdf_source_bytes_and_pdf(None, pdf)
        assert data.startswith(b"%PDF")


# ---------------------------------------------------------------------------
# _write_table_output
# ---------------------------------------------------------------------------


class TestWriteTableOutput:
    def test_human_readable_table(self):
        data = _make_sample_usage_data()
        out = StringIO()
        _write_table_output(data, out, human=True)
        text = out.getvalue()

        assert "CATEGORY" in text
        assert "images" in text
        assert "1.0 KB" in text
        assert "xref overhead" in text
        assert "total" in text
        assert "2.0 KB" in text

    def test_raw_bytes_table(self):
        data = _make_sample_usage_data()
        out = StringIO()
        _write_table_output(data, out, human=False)
        text = out.getvalue()

        assert "1024" in text
        assert "xref overhead" in text
        assert "total" in text
        assert "2048" in text

    def test_omits_zero_byte_categories(self):
        data = _make_sample_usage_data()
        out = StringIO()
        _write_table_output(data, out, human=True)
        text = out.getvalue()

        assert "images" in text
        assert "fonts" not in text

    def test_returns_early_if_no_active_categories(self):
        data = {"file_size": 0, "objects": 0, "categories": [{"id": "fonts", "bytes": 0}]}
        out = StringIO()
        _write_table_output(data, out, human=True)
        text = out.getvalue()

        assert text == ""


# ---------------------------------------------------------------------------
# usage_cli_hook
# ---------------------------------------------------------------------------


class TestUsageCliHook:
    def test_writes_table_output_to_stdout(self):
        usage_data = _make_sample_usage_data()
        result = OpResult(
            success=True,
            data=usage_data,
            meta={c.META_OUTPUT_FILE: None, c.META_JSON_OUTPUT: False, "human": True},
        )

        with patch("pdftl.operations.usage.smart_open_maybe_dash") as mock_open:
            mock_file = StringIO()
            mock_open.return_value.__enter__ = lambda s: mock_file
            mock_open.return_value.__exit__ = MagicMock(return_value=False)

            usage_cli_hook(result, None, None)
            output = mock_file.getvalue()

        assert "total" in output
        assert "images" in output

    def test_writes_json_output_to_stdout(self):
        usage_data = _make_sample_usage_data()
        result = OpResult(
            success=True,
            data=usage_data,
            meta={c.META_OUTPUT_FILE: None, c.META_JSON_OUTPUT: True},
        )

        with patch("pdftl.operations.usage.smart_open_maybe_dash") as mock_open:
            mock_file = StringIO()
            mock_open.return_value.__enter__ = lambda s: mock_file
            mock_open.return_value.__exit__ = MagicMock(return_value=False)

            usage_cli_hook(result, None, None)
            output = mock_file.getvalue()

        parsed = json.loads(output)
        assert "usage" in parsed
        assert parsed["usage"]["file_size"] == 2048

    def test_writes_output_to_file(self):
        usage_data = _make_sample_usage_data()
        result = OpResult(
            success=True,
            data=usage_data,
            meta={c.META_OUTPUT_FILE: "out.json", c.META_JSON_OUTPUT: True},
        )

        with patch("pdftl.operations.usage.smart_open_maybe_dash") as mock_open:
            mock_file = StringIO()
            mock_open.return_value.__enter__ = lambda s: mock_file
            mock_open.return_value.__exit__ = MagicMock(return_value=False)

            usage_cli_hook(result, None, None)
            mock_open.assert_called_once_with("out.json")


# ---------------------------------------------------------------------------
# usage (registered operation)
# ---------------------------------------------------------------------------


class TestUsage:
    @patch("pdftl.operations.usage.analyze_space_usage")
    def test_returns_op_result_success(self, mock_analyze):
        mock_analyze.return_value = _make_sample_usage_data()
        pdf = _make_simple_pdf()

        result = usage("usage", "_", pdf, [])

        assert isinstance(result, OpResult)
        assert result.success is True
        assert result.is_discardable is True
        assert isinstance(result.pdf, pikepdf.Pdf)

    @patch("pdftl.operations.usage.analyze_space_usage")
    def test_default_flags_in_meta(self, mock_analyze):
        mock_analyze.return_value = _make_sample_usage_data()
        pdf = _make_simple_pdf()

        result = usage("usage", "_", pdf, [])

        assert result.meta[c.META_JSON_OUTPUT] is False
        assert result.meta["human"] is True

    @patch("pdftl.operations.usage.analyze_space_usage")
    def test_bytes_flag_sets_human_false(self, mock_analyze):
        mock_analyze.return_value = _make_sample_usage_data()
        pdf = _make_simple_pdf()

        result = usage("usage", "_", pdf, ["bytes"])

        assert result.meta["human"] is False
        assert result.meta[c.META_JSON_OUTPUT] is False

    @patch("pdftl.operations.usage.get_json_flag", return_value=True)
    @patch("pdftl.operations.usage.analyze_space_usage")
    def test_json_flag_sets_meta_json_true(self, mock_analyze, mock_get_json):
        mock_analyze.return_value = _make_sample_usage_data()
        pdf = _make_simple_pdf()

        result = usage("usage", "_", pdf, ["json"])

        assert result.meta[c.META_JSON_OUTPUT] is True

    @patch("pdftl.operations.usage.analyze_space_usage")
    def test_stores_output_file_in_meta(self, mock_analyze):
        mock_analyze.return_value = _make_sample_usage_data()
        pdf = _make_simple_pdf()

        result = usage("usage", "_", pdf, [], output_file="usage.txt")

        assert result.meta[c.META_OUTPUT_FILE] == "usage.txt"
