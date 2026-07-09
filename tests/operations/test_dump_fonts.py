# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/test_dump_fonts.py

import json
from io import StringIO
from unittest.mock import MagicMock, patch

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.operations.dump_fonts import _extract_font_info, dump_fonts_cli_hook


def make_font(resource_name, base_font, obj_id):
    """Returns a minimal font dict matching process_single_font's output schema."""
    return {
        "name": f"/{base_font}",
        "resource_name": resource_name,
        "base_font": base_font,
        "descriptor_font": "",
        "subtype": "Type 1",
        "is_embedded": False,
        "font_bytes": 0,
        "is_subset": False,
        "encoding": "WinAnsi",
        "has_to_unicode": False,
        "traits": {},
        "metrics": {},
        "obj_id": obj_id,
    }


class TestDumpFontsOperation:
    @patch("pdftl.operations.dump_fonts.process_single_font")
    @patch("pdftl.operations.dump_fonts.get_all_fonts_recursive")
    def test_extract_font_info_single_font(self, mock_crawl, mock_process):
        """Single font on one page is returned correctly."""
        f_obj = MagicMock()
        mock_crawl.return_value = [("/F2", f_obj, 1)]
        mock_process.return_value = make_font("F2", "Arial", 42)

        pdf = MagicMock()
        pdf.pages = [MagicMock()]

        result = _extract_font_info(pdf)

        assert len(result["fonts"]) == 1
        font = result["fonts"][0]
        assert font["base_font"] == "Arial"
        assert font["obj_id"] == 42
        assert font["usages"] == {"/F2": [1]}

    @patch("pdftl.operations.dump_fonts.process_single_font")
    @patch("pdftl.operations.dump_fonts.get_all_fonts_recursive")
    def test_extract_font_info_deduplicates_across_pages(self, mock_crawl, mock_process):
        """Same font appearing on two pages is merged into one entry with both pages in usages."""
        f_obj = MagicMock()
        mock_crawl.return_value = [("/F1", f_obj, 1), ("/F1", f_obj, 2)]
        mock_process.return_value = make_font("/F1", "Helvetica", 7)

        pdf = MagicMock()
        pdf.pages = [MagicMock(), MagicMock()]

        result = _extract_font_info(pdf)

        assert len(result["fonts"]) == 1
        assert result["fonts"][0]["usages"] == {"/F1": [1, 2]}

    @patch("pdftl.operations.dump_fonts.process_single_font")
    @patch("pdftl.operations.dump_fonts.get_all_fonts_recursive")
    def test_extract_font_info_fallback_id(self, mock_crawl, mock_process):
        """Fonts with obj_id=None get a synthetic key and are still returned."""
        f_obj = MagicMock()
        mock_crawl.return_value = [("/F3", f_obj, 1)]
        mock_process.return_value = make_font("/F3", "ComicSans", None)

        pdf = MagicMock()
        pdf.pages = [MagicMock()]

        result = _extract_font_info(pdf)

        assert len(result["fonts"]) == 1
        assert result["fonts"][0]["base_font"] == "ComicSans"
        assert result["fonts"][0]["usages"] == {"/F3": [1]}

    @patch("pdftl.operations.dump_fonts.process_single_font")
    @patch("pdftl.operations.dump_fonts.get_all_fonts_recursive")
    def test_extract_font_info_skips_unparseable_fonts(self, mock_crawl, mock_process):
        """Fonts where process_single_font returns None are silently skipped."""
        f_obj = MagicMock()
        mock_crawl.return_value = [("/F1", f_obj, 1)]
        mock_process.return_value = None

        pdf = MagicMock()
        pdf.pages = [MagicMock()]

        result = _extract_font_info(pdf)

        assert result["fonts"] == []

    @patch("pdftl.operations.dump_fonts.process_single_font")
    @patch("pdftl.operations.dump_fonts.get_all_fonts_recursive")
    def test_extract_font_info_multiple_distinct_fonts(self, mock_crawl, mock_process):
        """Two fonts with different obj_ids produce two separate entries."""
        f1, f2 = MagicMock(), MagicMock()
        mock_crawl.return_value = [("/F1", f1, 1), ("/F2", f2, 1)]
        mock_process.side_effect = [
            make_font("F1", "Arial", 10),
            make_font("F2", "Times-Roman", 20),
        ]

        pdf = MagicMock()
        pdf.pages = [MagicMock()]

        result = _extract_font_info(pdf)

        assert len(result["fonts"]) == 2
        base_fonts = {f["base_font"] for f in result["fonts"]}
        assert base_fonts == {"Arial", "Times-Roman"}

    @patch("pdftl.operations.dump_fonts.smart_open_maybe_dash")
    def test_dump_fonts_cli_hook(self, mock_open):
        """Test stdout json flushing via dump_fonts_cli_hook."""
        mock_file = StringIO()
        mock_open.return_value.__enter__.return_value = mock_file
        test_data = {"fonts": [{"base_font": "Times"}]}
        res = OpResult(success=True, data=test_data, meta={c.META_OUTPUT_FILE: "out.json"})

        dump_fonts_cli_hook(res, None, None)

        output = mock_file.getvalue()
        assert "Times" in output
        assert json.loads(output) == test_data
        mock_open.assert_called_once_with("out.json")

    @patch("pdftl.operations.dump_fonts.process_single_font")
    @patch("pdftl.operations.dump_fonts.get_all_fonts_recursive")
    def test_extract_font_info_with_specs(self, mock_crawl, mock_process):
        """Page specs narrow the target pages passed to get_all_fonts_recursive."""
        mock_crawl.return_value = []

        pdf = MagicMock()
        pdf.pages = [MagicMock()] * 5

        _extract_font_info(pdf, specs=["1", "3"])

        mock_crawl.assert_called_once()
        target_pages = mock_crawl.call_args[0][1]
        assert target_pages == [1, 3]

    @patch("pdftl.operations.dump_fonts.process_single_font")
    @patch("pdftl.operations.dump_fonts.get_all_fonts_recursive")
    def test_extract_font_info_skips_font_parse_exceptions(self, mock_crawl, mock_process):
        """Exceptions raised by process_single_font are caught and the font is skipped."""
        f_obj = MagicMock()
        mock_crawl.return_value = [("/F1", f_obj, 1)]
        mock_process.side_effect = AttributeError("Corrupt font object")

        pdf = MagicMock()
        pdf.pages = [MagicMock()]

        result = _extract_font_info(pdf)

        assert result["fonts"] == []

    @patch("pdftl.operations.dump_fonts._extract_font_info")
    def test_dump_fonts_returns_op_result(self, mock_extract):
        """dump_fonts wraps extracted data in a successful OpResult."""
        from pdftl.operations.dump_fonts import dump_fonts

        mock_extract.return_value = {"fonts": [{"base_font": "Arial"}]}

        pdf = MagicMock()
        result = dump_fonts(pdf, specs=[], output_file="out.json")

        assert result.success is True
        assert result.data == {"fonts": [{"base_font": "Arial"}]}
        assert result.meta[c.META_OUTPUT_FILE] == "out.json"
