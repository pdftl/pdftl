from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from PIL import Image

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.delete_blank import (
    _BlankSpec,
    _compute_ink_coverage,
    _compute_stddev,
    _extract_params,
    _find_blank_pages_for_spec,
    _page_is_blank,
    _parse_float,
    _parse_spec,
    _resolve_params,
    delete_blank,
)


class TestDeleteBlankParams:
    def test_parse_float_valid(self):
        assert _parse_float("1.5", "test") == 1.5
        assert _parse_float("2", "test") == 2.0

    def test_parse_float_invalid_format(self):
        with pytest.raises(InvalidArgumentError, match="expected a number"):
            _parse_float("potato", "test")

    def test_parse_float_min_val(self):
        with pytest.raises(InvalidArgumentError, match="must be >= 0.0"):
            _parse_float("-1", "test", min_val=0.0)

    def test_parse_float_max_val(self):
        with pytest.raises(InvalidArgumentError, match="must be <= 1.0"):
            _parse_float("1.5", "test", max_val=1.0)

    def test_extract_params_no_parens(self):
        assert _extract_params("odd") == ("odd", {})
        assert _extract_params("odd(missing_closing") == ("odd(missing_closing", {})

    def test_extract_params_valid(self):
        spec = "even(threshold=0.01, dpi=72, no_equals, mode=rgb)"
        selector, params = _extract_params(spec)
        assert selector == "even"
        assert params == {"threshold": "0.01", "dpi": "72", "mode": "rgb"}

    def test_extract_params_invalid_key(self):
        with pytest.raises(InvalidArgumentError, match="unknown parameter 'potato'"):
            _extract_params("1-5(potato=5)")

    def test_resolve_params_defaults(self):
        mode, dpi, threshold, stddev, use_thresh, use_std = _resolve_params({})
        assert mode == "grey"
        assert dpi == 30.0
        assert threshold == 0.005
        assert stddev == 5.0
        assert use_thresh is True
        assert use_std is True

    def test_resolve_params_explicit(self):
        params = {"mode": "rgb", "dpi": "72", "threshold": "0.1"}
        mode, dpi, threshold, stddev, use_thresh, use_std = _resolve_params(params)
        assert mode == "rgb"
        assert dpi == 72.0
        assert threshold == 0.1
        assert stddev == 5.0
        assert use_thresh is True
        assert use_std is False  # Explicit threshold, so stddev drops out unless specified

    def test_resolve_params_invalid_mode(self):
        with pytest.raises(InvalidArgumentError, match="must be 'grey' or 'rgb'"):
            _resolve_params({"mode": "cmyk"})

    @patch("pdftl.operations.delete_blank.page_numbers_matching_page_spec")
    def test_parse_spec(self, mock_page_match):
        mock_page_match.return_value = [1, 3]

        # Test default selector "-"
        spec1 = _parse_spec("-", total_pages=3)
        assert spec1.candidate_indices == {0, 1, 2}

        # Test explicit selector
        spec2 = _parse_spec("odd(dpi=100)", total_pages=3)
        assert spec2.candidate_indices == {0, 2}
        assert spec2.dpi == 100.0


class TestDeleteBlankAnalysis:
    def test_compute_ink_coverage(self):
        # Empty image
        img_empty = Image.new("L", (0, 0))
        assert _compute_ink_coverage(img_empty) == 0.0

        # Solid white
        img_white = Image.new("L", (10, 10), 255)
        assert _compute_ink_coverage(img_white) == 0.0

        # Solid black
        img_black = Image.new("L", (10, 10), 0)
        assert _compute_ink_coverage(img_black) == 1.0

        # Half white, half black
        arr = np.zeros((10, 10), dtype=np.uint8)
        arr[:, 5:] = 255
        img_half = Image.fromarray(arr, "L")
        assert _compute_ink_coverage(img_half) == 0.5

    def test_compute_stddev(self):
        # Solid color RGB
        img_solid_rgb = Image.new("RGB", (10, 10), (100, 150, 200))
        assert _compute_stddev(img_solid_rgb, "rgb") == 0.0

        # Mixed Grey
        arr = np.zeros((10, 10), dtype=np.uint8)
        arr[:, 5:] = 255
        img_mixed_grey = Image.fromarray(arr, "L")
        assert _compute_stddev(img_mixed_grey, "grey") > 0.0

        # Mixed RGB
        arr_rgb = np.zeros((10, 10, 3), dtype=np.uint8)
        arr_rgb[:, 5:] = [255, 255, 255]
        img_mixed_rgb = Image.fromarray(arr_rgb, "RGB")
        assert _compute_stddev(img_mixed_rgb, "rgb") > 0.0

    @patch("pdftl.operations.delete_blank._compute_ink_coverage")
    @patch("pdftl.operations.delete_blank._compute_stddev")
    def test_page_is_blank(self, mock_stddev, mock_ink):
        spec = _BlankSpec(set(), 30.0, "grey", 0.05, 5.0, True, True)

        # Passes both
        mock_ink.return_value = 0.01
        mock_stddev.return_value = 1.0
        assert _page_is_blank(None, spec) is True

        # Fails threshold
        mock_ink.return_value = 0.1
        assert _page_is_blank(None, spec) is False

        # Fails stddev
        mock_ink.return_value = 0.01
        mock_stddev.return_value = 10.0
        assert _page_is_blank(None, spec) is False


class _FakePage:
    """Minimal page stand-in: supports .keys()/del like a real pikepdf.Page,
    but is still distinguishable/comparable like the old bare-string mocks."""

    def __init__(self, name):
        self.name = name

    def keys(self):
        return []

    def __eq__(self, other):
        return isinstance(other, _FakePage) and self.name == other.name

    def __repr__(self):
        return f"_FakePage({self.name!r})"


class TestDeleteBlankOperation:
    @patch("pdftl.utils.page_images.iter_pages_as_pil")
    @patch("pdftl.operations.delete_blank._page_is_blank")
    def test_find_blank_pages_for_spec(self, mock_is_blank, mock_iter):
        spec = _BlankSpec({0, 1}, 30.0, "grey", 0.05, 5.0, True, True)

        mock_iter.return_value = [(0, "img0"), (1, "img1")]
        # Page 0 is blank, Page 1 is not
        mock_is_blank.side_effect = lambda img, sp: img == "img0"

        blank_pages = _find_blank_pages_for_spec("pdf", spec)
        assert blank_pages == {1}  # 1-based index

    @patch("pdftl.utils.dependencies.ensure_dependencies")
    @patch("pdftl.operations.delete_blank._find_blank_pages_for_spec")
    def test_delete_blank_empty_specs(self, mock_find, mock_deps):
        mock_pdf = MagicMock()
        mock_pdf.pages = [_FakePage("page1"), _FakePage("page2")]
        mock_find.return_value = {2}

        result = delete_blank(mock_pdf, [])
        assert mock_pdf.pages == [_FakePage("page1")]
        assert result.success is True

    @patch("pdftl.utils.dependencies.ensure_dependencies")
    @patch("pdftl.operations.delete_blank._find_blank_pages_for_spec")
    def test_delete_blank_main(self, mock_find, mock_deps):
        # Setup mock PDF with 3 pages
        mock_pdf = MagicMock()
        mock_pdf.pages = [_FakePage("page1"), _FakePage("page2"), _FakePage("page3")]

        # Spec 1 finds page 1, Spec 2 finds page 3
        mock_find.side_effect = [{1}, {3}]

        # Pass valid page spec strings (like "1" and "3") instead of "spec1"
        result = delete_blank(mock_pdf, ["1", "3"])

        assert result.success is True
        assert mock_deps.called

        # Deletions must happen in reverse order (index 2, then index 0)
        assert mock_pdf.pages == [_FakePage("page2")]
