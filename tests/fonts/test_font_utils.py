from pathlib import Path
from unittest.mock import MagicMock, patch

from pdftl.fonts.font_utils import FontWrapper


@patch("fontTools.ttLib.TTFont")
def test_get_char_width_missing(ttfont_mock):
    # Create mock tables with required attributes
    head_mock = MagicMock(unitsPerEm=1000)
    hmtx_mock = {"A": (500, 0), ".notdef": (600, 0)}
    cmap_mock = {}

    ttfont_instance = MagicMock()
    ttfont_instance.__getitem__.side_effect = lambda key: (
        hmtx_mock if key == "hmtx" else head_mock
    )
    ttfont_instance.getBestCmap.return_value = cmap_mock

    ttfont_mock.return_value = ttfont_instance

    # Now constructing FontWrapper will work
    fm = FontWrapper("fake.ttf")
    width = fm.get_char_width("Z")  # char not in cmap
    assert width == 600  # fallback to .notdef


# tests/test_font_utils.py


# Use a system or bundled TTF for testing
TEST_FONT_PATH = Path("tests/files/fonts/Filepile.otf")


def test_fontmetrics_init_and_scale():
    fm = FontWrapper(TEST_FONT_PATH)
    assert fm.path == TEST_FONT_PATH
    assert fm.units_per_em > 0
    assert fm.scale_factor == 1000.0 / fm.units_per_em
    fm.close()


def test_get_char_width_existing_char():
    fm = FontWrapper(TEST_FONT_PATH)
    width = fm.get_char_width("A")
    assert width > 0
    fm.close()


def test_get_char_width_missing_char(caplog):
    fm = FontWrapper(TEST_FONT_PATH)
    # Use a rarely defined Unicode character to trigger .notdef
    char = "\uffff"
    with caplog.at_level("WARNING"):
        width = fm.get_char_width(char)
    assert width >= 0  # still returns a number
    assert any(".notdef" in rec.message for rec in caplog.records)
    fm.close()


def test_get_char_width_empty_string():
    fm = FontWrapper(TEST_FONT_PATH)
    assert fm.get_char_width("") == 0.0
    fm.close()


def test_close_multiple_times():
    fm = FontWrapper(TEST_FONT_PATH)
    fm.close()
    fm.close()  # should not raise


# --- Merged from test_font_utils_extra.py ---
# tests/test_font_utils_extra.py
from unittest.mock import MagicMock, patch

import pytest

from pdftl.fonts import font_utils as fu


@patch("fontTools.ttLib.TTFont")
def test_get_char_width_missing_and_notdef(ttfont_constructor):
    # Build TTFont instance mock with minimal tables hmtx, head and cmap
    tt_inst = MagicMock()
    # hmtx mapping contains only .notdef
    hmtx = {".notdef": (600, 0)}
    tt_inst.__getitem__.side_effect = lambda key: (
        hmtx if key == "hmtx" else MagicMock(unitsPerEm=1000) if key == "head" else None
    )
    tt_inst.getBestCmap.return_value = {}  # empty cmap, so glyph not found
    ttfont_constructor.return_value = tt_inst

    fm = fu.FontWrapper("fake.ttf")
    # char not present -> fallback to .notdef
    width = fm.get_char_width("Z")
    assert width == pytest.approx(600.0)
    fm.close()


@patch("fontTools.ttLib.TTFont")
def test_get_char_width_empty_string_returns_zero(ttfont_constructor):
    tt_inst = MagicMock()
    tt_inst.__getitem__.return_value = MagicMock(unitsPerEm=1000)
    tt_inst.getBestCmap.return_value = {}
    ttfont_constructor.return_value = tt_inst

    fm = fu.FontWrapper("fake.ttf")
    assert fm.get_char_width("") == 0.0
    fm.close()


def test_close_calls_ttfont_close(monkeypatch):
    # construct a FontWrapper object with a fake ttfont that has close called
    fm = fu.FontWrapper.__new__(fu.FontWrapper)
    fake_tt = MagicMock()
    fm.ttfont = fake_tt
    fm.close()
    fake_tt.close.assert_called_once()


# --- Merged from test_font_utils_gap.py ---
# tests/test_font_utils_gap.py
import logging
from unittest.mock import MagicMock, patch

import pytest

from pdftl.fonts.font_utils import FontWrapper


def test_get_char_width_robust_fallback(caplog):
    """
    Verifies that the wrapper robustly finds GID 0 for fallback,
    even if it is NOT named '.notdef'.
    """

    caplog.set_level(logging.WARNING)

    # 1. Mock TTFont
    mock_ttfont = MagicMock()

    # CRITICAL: Mock Glyph Order so GID 0 is "glyph0", NOT ".notdef"
    mock_ttfont.getGlyphOrder.return_value = ["glyph0", "glyphA"]

    # Mock CMAP (Only knows 'A')
    mock_ttfont.getBestCmap.return_value = {ord("A"): "glyphA"}

    # Mock metrics (hmtx)
    # We provide 'glyph0' but NOT '.notdef' to ensure code doesn't hardcode ".notdef"
    mock_metrics = {
        "glyphA": (500, 0),
        "glyph0": (888, 0),  # Distinct width to verify it was used
    }

    mock_ttfont.__getitem__.side_effect = lambda x: {
        "hmtx": mock_metrics,
        "head": MagicMock(unitsPerEm=1000),
    }[x]

    with patch("fontTools.ttLib.TTFont", return_value=mock_ttfont):
        wrapper = FontWrapper("weird_font.ttf")

        # 1. Verify it detected the correct fallback name
        assert wrapper.fallback_glyph_name == "glyph0"

        # 2. Test Success Case
        assert wrapper.get_char_width("A") == 500.0

        # 3. Test Fallback Case
        # Request 'X' (missing) -> Should use 'glyph0' (width 888)
        width = wrapper.get_char_width("X")

        assert width == 888.0
        assert "Using glyph0" in caplog.text


def test_font_wrapper_cleanup():
    """Ensures close() is called."""
    mock_ttfont = MagicMock()
    # Need to return a list for getGlyphOrder to pass __init__
    mock_ttfont.getGlyphOrder.return_value = [".notdef"]

    with patch("fontTools.ttLib.TTFont", return_value=mock_ttfont):
        wrapper = FontWrapper("dummy.ttf")
        wrapper.close()
        mock_ttfont.close.assert_called_once()


def test_font_wrapper_zero_units_per_em(tmp_path):
    """Covers font_utils.py line 39: fallback when units_per_em is 0."""
    from unittest.mock import MagicMock, patch

    from pdftl.fonts.font_utils import FontWrapper

    # We need to patch TTFont so it doesn't try to read a real file
    with patch("fontTools.ttLib.TTFont") as MockTTFont:
        mock_instance = MockTTFont.return_value
        mock_instance.getBestCmap.return_value = {}
        mock_instance.getGlyphOrder.return_value = [".notdef"]
        # Set unitsPerEm to 0 to trigger the fallback
        mock_instance.__getitem__.return_value.unitsPerEm = 0

        wrapper = FontWrapper("fake.ttf")
        assert wrapper.scale_factor == 1.0
