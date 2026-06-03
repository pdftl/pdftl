# tests/operations/test_unpause.py

from unittest.mock import MagicMock, patch
import numpy as np
import pytest
from PIL import Image

from pdftl.core.core_types import OpResult
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.unpause import (
    _otsu_threshold,
    _parse_unpause_args,
    _is_consecutive,
    _find_pages_to_keep,
    unpause_pdf,
)


class TestUnpauseParams:
    def test_parse_unpause_args_defaults(self):
        dpi, ink, survival = _parse_unpause_args([])
        assert dpi == 72.0
        assert ink == "auto"
        assert survival == 0.98

    def test_parse_unpause_args_valid_overrides(self):
        dpi, ink, survival = _parse_unpause_args(["dpi=150.5", "ink=100", "survival=0.99"])
        assert dpi == 150.5
        assert ink == 100
        assert survival == 0.99

    def test_parse_unpause_args_ink_explicit_auto(self):
        dpi, ink, survival = _parse_unpause_args(["ink=auto"])
        assert ink == "auto"

    def test_parse_unpause_args_invalid_dpi(self):
        with pytest.raises(InvalidArgumentError, match="'unpause': invalid dpi"):
            _parse_unpause_args(["dpi=potato"])
        with pytest.raises(InvalidArgumentError, match="'unpause': invalid dpi"):
            _parse_unpause_args(["dpi=-10"])
        with pytest.raises(InvalidArgumentError, match="'unpause': invalid dpi"):
            _parse_unpause_args(["dpi=0"])

    def test_parse_unpause_args_invalid_ink(self):
        with pytest.raises(InvalidArgumentError, match="'unpause': invalid ink"):
            _parse_unpause_args(["ink=potato"])
        with pytest.raises(InvalidArgumentError, match="'unpause': invalid ink"):
            _parse_unpause_args(["ink=256"])
        with pytest.raises(InvalidArgumentError, match="'unpause': invalid ink"):
            _parse_unpause_args(["ink=0"])

    def test_parse_unpause_args_invalid_survival(self):
        with pytest.raises(InvalidArgumentError, match="'unpause': invalid survival"):
            _parse_unpause_args(["survival=potato"])
        with pytest.raises(InvalidArgumentError, match="'unpause': invalid survival"):
            _parse_unpause_args(["survival=0.0"])
        with pytest.raises(InvalidArgumentError, match="'unpause': invalid survival"):
            _parse_unpause_args(["survival=1.01"])


class TestUnpauseAnalysis:
    def test_otsu_threshold_solid_white(self):
        pixels = np.full((10, 10), 255, dtype=np.uint8)
        assert _otsu_threshold(pixels) == 128

    def test_otsu_threshold_solid_black(self):
        pixels = np.full((10, 10), 0, dtype=np.uint8)
        assert _otsu_threshold(pixels) == 128

    def test_otsu_threshold_half_black_half_white(self):
        pixels = np.zeros((10, 10), dtype=np.uint8)
        pixels[:, 5:] = 255
        assert _otsu_threshold(pixels) == 0

    def test_otsu_threshold_known_array(self):
        pixels = np.array([50] * 50 + [150] * 50, dtype=np.uint8)
        assert _otsu_threshold(pixels) == 50

    def test_is_consecutive_blank_previous_page(self):
        last_pixels = np.full((10, 10), 255, dtype=np.uint8)
        pixels = np.zeros((10, 10), dtype=np.uint8)
        assert _is_consecutive(last_pixels, pixels, last_threshold=100, survival_ratio=0.98)

    def test_is_consecutive_all_ink_survives(self):
        last_pixels = np.full((10, 10), 255, dtype=np.uint8)
        last_pixels[0, 0] = 0
        pixels = np.full((10, 10), 255, dtype=np.uint8)
        pixels[0, 0] = 0
        assert _is_consecutive(last_pixels, pixels, last_threshold=100, survival_ratio=0.98)

    def test_is_consecutive_no_ink_survives(self):
        last_pixels = np.full((10, 10), 255, dtype=np.uint8)
        last_pixels[0, 0] = 0
        pixels = np.full((10, 10), 255, dtype=np.uint8)
        assert not _is_consecutive(last_pixels, pixels, last_threshold=100, survival_ratio=0.98)

    def test_is_consecutive_boundary(self):
        last_pixels = np.full((10, 10), 255, dtype=np.uint8)
        last_pixels.ravel()[:100] = 0

        pixels_exact = np.full((10, 10), 255, dtype=np.uint8)
        pixels_exact.ravel()[:98] = 0
        assert _is_consecutive(last_pixels, pixels_exact, last_threshold=100, survival_ratio=0.98)

        pixels_below = np.full((10, 10), 255, dtype=np.uint8)
        pixels_below.ravel()[:97] = 0
        assert not _is_consecutive(
            last_pixels, pixels_below, last_threshold=100, survival_ratio=0.98
        )


class TestUnpauseOperation:
    @patch("pdftl.operations.unpause.iter_pages_as_pil")
    def test_find_pages_to_keep_empty_pdf(self, mock_iter):
        mock_pdf = MagicMock()
        mock_pdf.pages = []
        assert _find_pages_to_keep(mock_pdf, dpi=72, ink="auto", survival_ratio=0.98) == []

    @patch("pdftl.operations.unpause.iter_pages_as_pil")
    def test_find_pages_to_keep_single_page(self, mock_iter):
        mock_pdf = MagicMock()
        mock_pdf.pages = ["page0"]
        img0 = Image.new("L", (10, 10), 255)
        mock_iter.return_value = [(0, img0)]

        assert _find_pages_to_keep(mock_pdf, dpi=72, ink="auto", survival_ratio=0.98) == [0]

    @patch("pdftl.operations.unpause.iter_pages_as_pil")
    def test_find_pages_to_keep_all_consecutive(self, mock_iter):
        mock_pdf = MagicMock()
        mock_pdf.pages = ["page0", "page1", "page2"]

        img0 = Image.new("L", (10, 10), 255)
        img0.putpixel((0, 0), 0)
        img1 = Image.new("L", (10, 10), 255)
        img1.putpixel((0, 0), 0)
        img1.putpixel((0, 1), 0)
        img2 = Image.new("L", (10, 10), 255)
        img2.putpixel((0, 0), 0)
        img2.putpixel((0, 1), 0)
        img2.putpixel((0, 2), 0)

        mock_iter.return_value = [(0, img0), (1, img1), (2, img2)]

        assert _find_pages_to_keep(mock_pdf, dpi=72, ink=100, survival_ratio=0.98) == [2]

    @patch("pdftl.operations.unpause.iter_pages_as_pil")
    def test_find_pages_to_keep_no_consecutive(self, mock_iter):
        mock_pdf = MagicMock()
        mock_pdf.pages = ["page0", "page1"]

        img0 = Image.new("L", (10, 10), 255)
        img0.putpixel((0, 0), 0)
        img1 = Image.new("L", (10, 10), 255)
        img1.putpixel((5, 5), 0)

        mock_iter.return_value = [(0, img0), (1, img1)]

        assert _find_pages_to_keep(mock_pdf, dpi=72, ink=100, survival_ratio=0.98) == [0, 1]

    @patch("pdftl.operations.unpause.iter_pages_as_pil")
    def test_find_pages_to_keep_mixed(self, mock_iter):
        mock_pdf = MagicMock()
        mock_pdf.pages = ["page0", "page1", "page2", "page3"]

        img0 = Image.new("L", (10, 10), 255)
        img0.putpixel((0, 0), 0)

        img1 = Image.new("L", (10, 10), 255)
        img1.putpixel((0, 0), 0)
        img1.putpixel((0, 1), 0)

        img2 = Image.new("L", (10, 10), 255)
        img2.putpixel((5, 5), 0)

        img3 = Image.new("L", (10, 10), 255)
        img3.putpixel((5, 5), 0)
        img3.putpixel((5, 6), 0)

        mock_iter.return_value = [(0, img0), (1, img1), (2, img2), (3, img3)]

        assert _find_pages_to_keep(mock_pdf, dpi=72, ink=100, survival_ratio=0.98) == [1, 3]

    @patch("pdftl.operations.unpause.ensure_dependencies")
    @patch("pdftl.operations.unpause._find_pages_to_keep")
    @patch("pdftl.operations.delete.del_page")
    def test_unpause_pdf_integration(self, mock_del_page, mock_keep, mock_deps):
        mock_pdf = MagicMock()
        mock_pdf.pages = ["p1", "p2", "p3", "p4"]
        mock_keep.return_value = [1, 3]

        result = unpause_pdf(mock_pdf, ["dpi=72"])

        assert isinstance(result, OpResult)
        assert result.success is True
        assert mock_deps.called

        mock_del_page.assert_any_call(mock_pdf, 3)
        mock_del_page.assert_any_call(mock_pdf, 1)
        assert mock_del_page.call_count == 2

    def test_unpause_pdf_invalid_args_raise(self):
        mock_pdf = MagicMock()
        with pytest.raises(InvalidArgumentError):
            unpause_pdf(mock_pdf, ["survival=potato"])
