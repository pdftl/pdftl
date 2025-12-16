import pytest

from pdftl.commands.parsers.crop_parser import (
    _parse_single_margin_value,
    parse_crop_content,
    parse_smart_crop_spec,
    specs_to_page_rules,
)


class TestCropParserCoverage:

    # --- Covers lines 41-42 ---
    def test_specs_to_page_rules_validation_error(self):
        """
        Triggers the try/except block inside specs_to_page_rules (lines 39-44).
        We pass a syntactically valid outer spec '1(...)',
        but the inner content 'bad%' causes parse_crop_content to fail.
        """
        specs = ["1(bad%)"]

        # Verify it raises the specific ValueError wrapping the underlying exception
        with pytest.raises(ValueError, match="Error parsing crop content 'bad%'"):
            specs_to_page_rules(specs, total_pages=10)

    # --- Covers line 67 ---
    def test_parse_crop_content_returns_smart_crop(self):
        """
        Verifies line 67: if smart_crop is found, return it immediately.
        """
        result = parse_crop_content("fit", 100, 100)
        assert result["type"] == "fit"
        assert result["mode"] == "fit"

    # --- Covers lines 91-117 (Smart Crop Logic) ---
    def test_parse_smart_crop_spec_details(self):
        # 1. Basic 'fit' (Lines 91, 101 checks)
        res = parse_smart_crop_spec("fit", 100, 100)
        assert res["mode"] == "fit"
        assert res["source"] is None
        # Lines 110-112: Default padding
        assert res["padding"] == (0.0, 0.0, 0.0, 0.0)

        # 2. 'fit-group' with source (Lines 95-100)
        res = parse_smart_crop_spec("fit-group=1-5", 100, 100)
        assert res["mode"] == "fit-group"
        assert res["source"] == "1-5"

        # 3. 'fit-group' without source (Lines 95-96, skip 97)
        res = parse_smart_crop_spec("fit-group", 100, 100)
        assert res["mode"] == "fit-group"
        assert res["source"] is None

        # 4. Partial match that should fail (Lines 101-104)
        # "fitting" starts with "fit" but != "fit" and doesn't start with "fit-group"
        res = parse_smart_crop_spec("fitting", 100, 100)
        assert res is None

        # 5. Padding Logic (Lines 106-108, 113-115)
        # "fit, 10pt" -> splits into ["fit", "10pt"] -> padding_str="10pt"
        res = parse_smart_crop_spec("fit, 10pt", 100, 100)
        assert res["padding"] == (10.0, 10.0, 10.0, 10.0)

    # --- Covers lines 197-199 ---
    def test_parse_single_margin_value_bad_percentage(self):
        """
        Triggers the specific ValueError catch block for percentages.
        Input 'bad%' passes `.endswith('%')`, but `float('bad')` raises ValueError.
        The code catches it, passes, and falls through to the final float conversion.
        """
        # "bad%" -> triggers line 197 (ValueError) -> line 199 (pass)
        # -> line 207 (strip pt) -> line 208 float('bad%') -> raises ValueError again.
        with pytest.raises(ValueError):
            _parse_single_margin_value("bad%", 100)
