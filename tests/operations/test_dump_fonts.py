"""Tests for pdftl.operations.dump_fonts"""

import json
from io import StringIO
from unittest.mock import MagicMock, patch

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.operations.dump_fonts import (
    _extract_font_info,
    dump_fonts,
    dump_fonts_cli_hook,
)


class TestDumpFontsOperation:
    def test_extract_font_info(self):
        """Test cross-page font aggregation logic."""
        pdf = MagicMock()

        # Page 1: Has No Fonts
        page1 = MagicMock()
        page1.get.return_value = None

        # Page 2: Has One Font
        page2 = MagicMock()
        res = MagicMock()
        res.__contains__.return_value = True
        f_obj = MagicMock()
        f_obj.hasattr.return_value = True
        f_obj.get.side_effect = lambda k, d=None: "/Arial" if k == "/BaseFont" else "/Type1"
        f_obj.objgen = (42, 0)
        f_obj.__contains__.return_value = False  # Bypass Descriptor
        res.Font.items.return_value = [("/F2", f_obj)]
        page2.get.return_value = res

        pdf.pages = [page1, page2]

        # Extract across all pages
        result = _extract_font_info(pdf)
        assert len(result["fonts"]) == 1
        font = result["fonts"][0]
        assert font["obj_id"] == 42
        assert font["usages"] == {"F2": [2]}

        # Test filtering via specs array
        result_specs = _extract_font_info(pdf, specs=["1"])
        assert len(result_specs["fonts"]) == 0

    def test_extract_font_info_fallback_id_and_exceptions(self):
        """Test inline ID synthetic generation and gracefully continuing over faulty pages."""
        pdf = MagicMock()
        page1 = MagicMock()
        res = MagicMock()
        res.__contains__.return_value = True
        f_obj = MagicMock()
        f_obj.hasattr.return_value = True
        f_obj.get.side_effect = lambda k, d=None: "/ComicSans" if k == "/BaseFont" else "/Type1"
        f_obj.objgen = None  # Force fallback ID assignment
        f_obj.__contains__.return_value = False
        res.Font.items.return_value = [("/F3", f_obj)]
        page1.get.return_value = res

        # Introduce erroring page
        page2 = MagicMock()
        page2.get.side_effect = AttributeError("Malformed Page Dict Error")

        pdf.pages = [page1, page2]

        result = _extract_font_info(pdf)
        assert len(result["fonts"]) == 1
        font = result["fonts"][0]
        assert font["base_font"] == "ComicSans"
        assert list(font["usages"].keys()) == ["F3"]

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
        parsed = json.loads(output)
        assert parsed == test_data
        mock_open.assert_called_once_with("out.json")

    def test_dump_fonts(self):
        """Test end-to-end wrapper returning an OpResult."""
        pdf = MagicMock()
        pdf.pages = []
        result = dump_fonts(pdf, specs=[], output_file="test_out.json")

        assert isinstance(result, OpResult)
        assert result.success is True
        assert result.data == {"fonts": []}
        assert result.meta[c.META_OUTPUT_FILE] == "test_out.json"
