# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
Test suite for the text_drawer helper module.

This file contains:
1.  TestTextDrawerLogic: Unit tests for pure helper functions (coordinates).
2.  TestTextDrawerClass: Unit tests for the TextDrawer class,
    mocking reportlab.
3.  TestTextDrawerHypothesis: Property-based tests for coordinate logic.
"""

import math
import unittest
from importlib import reload
from unittest.mock import ANY, MagicMock, call, patch

import pytest

# --- Third-Party Imports ---
from hypothesis import given, settings
from hypothesis import strategies as st

# --- Local Application Imports ---
# Import the functions and class to be tested
from pdftl.commands.helpers.text_drawer import _PageBox  # Import the helper tuple
from pdftl.commands.helpers.text_drawer import (
    DEFAULT_FONT_NAME,
    TextDrawer,
    _get_base_coordinates,
    _resolve_dimension,
)

# Import custom exception for testing
try:
    from pdftl.exceptions import InvalidArgumentError
except ImportError:
    InvalidArgumentError = ValueError

# We use the internal _PageBox for mocks
MockPageBox = _PageBox


class TestTextDrawerLogic(unittest.TestCase):
    """
    Unit tests for the "pure" coordinate helper functions in text_drawer.py.
    """

    def setUp(self):
        self.page_box = MockPageBox(width=600.0, height=800.0)
        self.font_size = 10.0
        # Give a real text width to test alignment!
        self.text_width = 100.0

    def test_resolve_dimension(self):
        dim_rule_pt = {"type": "pt", "value": 50.0}
        self.assertEqual(_resolve_dimension(dim_rule_pt, 800.0), 50.0)
        dim_rule_pct = {"type": "%", "value": 10.0}
        self.assertEqual(_resolve_dimension(dim_rule_pct, 800.0), 80.0)
        self.assertEqual(_resolve_dimension(20.0, 800.0), 20.0)
        self.assertEqual(_resolve_dimension(None, 800.0), 0.0)

    def test_get_base_coordinates_presets(self):
        """
        Tests preset anchor coordinate calculations.
        Assumes self.page_box is (width=600, height=800).
        Note: _get_base_coordinates *only* reads 'position' and
        correctly ignores 'align'.
        """
        # --- ALIGN LEFT (test 'top-left' anchor) ---
        # 'align' is ignored by _get_base_coordinates
        rule = {"position": "top-left", "align": "left"}
        x, y = _get_base_coordinates(rule, self.page_box)
        self.assertEqual((x, y), (0.0, 800.0))  # Anchor: X=0, Y=800

        # Test 'top-center' anchor
        rule = {"position": "top-center", "align": "left"}
        x, y = _get_base_coordinates(rule, self.page_box)
        # CORRECTED: The X anchor for "center" is 600 / 2 = 300.0
        self.assertEqual((x, y), (300.0, 800.0))

        # --- ALIGN CENTER (test 'top-left' anchor again) ---
        # 'align' is ignored, so the anchor is the same as the first test
        rule = {"position": "top-left", "align": "center"}
        x, y = _get_base_coordinates(rule, self.page_box)
        # CORRECTED: The anchor is still (0.0, 800.0).
        self.assertEqual((x, y), (0.0, 800.0))

        # Test 'top-center' anchor again
        rule = {"position": "top-center", "align": "center"}
        x, y = _get_base_coordinates(rule, self.page_box)
        # CORRECTED: The X anchor for "center" is 600 / 2 = 300.0
        self.assertEqual((x, y), (300.0, 800.0))

        # --- ALIGN RIGHT (test 'top-right' anchor) ---
        # This test was already correct
        rule = {"position": "top-right", "align": "right"}
        x, y = _get_base_coordinates(rule, self.page_box)
        self.assertEqual((x, y), (600.0, 800.0))  # Anchor: X=600, Y=800

        # --- Test middle Y ---
        # This test was already correct
        rule = {"position": "mid-center"}
        x, y = _get_base_coordinates(rule, self.page_box)
        # Anchor: X = 600 / 2 = 300.0, Y = 800 / 2 = 400.0
        self.assertEqual((x, y), (300.0, 400.0))


class TestTextDrawerClass:
    """
    Unit tests for the TextDrawer class. (pytest-style)
    These tests mock the reportlab dependency.
    """

    # We no longer use setUp, we'll create the mock_page_box
    # inside each test that needs it.

    @patch("pdftl.commands.helpers.text_drawer.getFont")
    @patch("pdftl.commands.helpers.text_drawer.reportlab_canvas")
    def test_get_font_name_logic(self, mock_canvas, mock_getFont, caplog):
        """Tests all logic paths for font validation and fallbacks."""
        mock_page_box = MockPageBox(width=600, height=800)
        drawer = TextDrawer(mock_page_box)

        # 1. Test standard font: 'Helvetica'
        font_name = drawer.get_font_name("Helvetica")
        assert font_name == "Helvetica"
        mock_getFont.assert_not_called()

        # 2. Test another standard font, case-insensitive
        font_name = drawer.get_font_name("times-bold")
        assert font_name == "Times-Bold"
        mock_getFont.assert_not_called()

        # 3. Test bad font: 'Fake-Font-Name'
        mock_getFont.side_effect = Exception("Font not found")
        with caplog.at_level("WARNING"):
            font_name = drawer.get_font_name("Fake-Font-Name")
            assert font_name == DEFAULT_FONT_NAME
            mock_getFont.assert_called_with("Fake-Font-Name")
            assert len(caplog.records) == 1
            # Corrected assertion
            record = caplog.records[0]
            assert record.args[0] == "Fake-Font-Name"

        mock_getFont.reset_mock()

        # 4. Test a *registered* custom font
        mock_getFont.side_effect = None  # Clear the side effect
        font_name = drawer.get_font_name("My-Custom-TTF-Font")
        assert font_name == "My-Custom-TTF-Font"
        mock_getFont.assert_called_with("My-Custom-TTF-Font")

    @patch("pdftl.commands.helpers.text_drawer.getFont")
    @patch("pdftl.commands.helpers.text_drawer.reportlab_canvas")
    def test_draw_rule_skips_bad_rule(self, mock_canvas, mock_getFont, caplog):
        """Tests that one bad rule doesn't stop others (via logging)."""
        mock_page_box = MockPageBox(width=600, height=800)
        drawer = TextDrawer(mock_page_box)

        # Rule 1: Bad. The text lambda will fail.
        bad_rule = {"text": MagicMock(side_effect=Exception("I am a bad rule!"))}
        context = {"page": 1}

        with caplog.at_level("WARNING"):
            drawer.draw_rule(bad_rule, context)
            assert len(caplog.records) == 1
            record = caplog.records[0]
            assert "Skipping one text rule" in record.message
            assert "I am a bad rule!" in str(record.args[0])

    # This helper method is now part of the pytest-style class
    def _run_draw_test(
        self, mock_canvas_instance, rule, expected_draw_x, expected_draw_y
    ):
        """Helper to run a parameterized draw test."""

        mock_page_box = MockPageBox(width=600, height=800)  # Define box

        # Reset the mock's calls for this sub-test
        mock_canvas_instance.reset_mock()

        # Mock stringWidth to a known value
        mock_canvas_instance.stringWidth.return_value = 100.0  # text width

        drawer = TextDrawer(mock_page_box)  # Use box
        context = {}

        # Set defaults that can be overridden by the 'rule' param
        full_rule = {
            "text": lambda ctx: "Hello",
            "font": "Helvetica",
            "size": 12.0,
            "color": (0, 0, 0),
            "offset-x": 0,
            "offset-y": 0,
            "rotate": 0,
        }
        full_rule.update(rule)

        # Get final anchor from the rule (e.g., 300, 400 for mid-center)
        # Note: _get_base_coordinates ignores 'align'
        base_x, base_y = _get_base_coordinates(full_rule, mock_page_box)  # Use box

        # Execute
        drawer.draw_rule(full_rule, context)

        # Verify
        expected_calls = [
            call.saveState(),
            call.setFillColorRGB(ANY, ANY, ANY),  # We use 'ANY' here
            call.setFont(full_rule["font"], full_rule["size"]),
            call.translate(base_x, base_y),  # Base anchor point
            call.rotate(0),
            call.drawString(expected_draw_x, expected_draw_y, "Hello"),
            call.restoreState(),
        ]
        mock_canvas_instance.assert_has_calls(expected_calls)

    @pytest.mark.parametrize(
        "position, align, expected_draw_x, expected_draw_y",
        [
            # ... (parameters are all correct) ...
            ("top-left", "left", 0.0, -12.0),
            ("mid-left", "left", 0.0, -6.0),
            ("bottom-left", "left", 0.0, 0.0),
            ("top-center", "center", -50.0, -12.0),
            ("mid-center", "center", -50.0, -6.0),
            ("bottom-center", "center", -50.0, 0.0),
            ("top-right", "right", -100.0, -12.0),
            ("mid-right", "right", -100.0, -6.0),
            ("bottom-right", "right", -100.0, 0.0),
        ],
    )
    def test_draw_rule_geometry(
        self, position, align, expected_draw_x, expected_draw_y
    ):
        """
        Tests all 9 combinations of position/alignment geometry.
        Assumes text_width=100.0 and font_size=12.0.
        """
        # Patches are inside the function, which is correct
        with patch("pdftl.commands.helpers.text_drawer.getFont", MagicMock()):
            with patch(
                "pdftl.commands.helpers.text_drawer.reportlab_canvas"
            ) as mock_reportlab_canvas:

                mock_canvas_instance = mock_reportlab_canvas.Canvas.return_value
                rule = {"position": position, "align": align, "size": 12.0}

                # Call the helper method using self
                self._run_draw_test(
                    mock_canvas_instance, rule, expected_draw_x, expected_draw_y
                )

    def test_text_drawer_dummy_class_raises_error(self):
        """
        Tests that instantiating TextDrawer raises an error if reportlab
        is not available (i.e., that the dummy class is working).
        """
        mock_page_box = MockPageBox(width=600, height=800)

        # We "poison" the sys.modules cache to make 'reportlab'
        # dependencies unavailable, forcing an ImportError.
        # We patch all the specific imports from the 'try' block.
        poisoned_modules = {
            "reportlab.pdfgen.canvas": None,
            "reportlab.lib.units": None,
            "reportlab.lib.colors": None,
            "reportlab.pdfbase.pdfmetrics": None,
            "reportlab.pdfbase.ttfonts": None,
            "reportlab.pdfbase": None,  # Also poison the base
        }

        # Use patch.dict as a context manager
        with patch.dict("sys.modules", poisoned_modules):

            # We must re-import the module *after* our patches are in place
            import pdftl.commands.helpers.text_drawer

            # Reload the module, which will now fail the imports
            reload(pdftl.commands.helpers.text_drawer)

            # Now, get the dummy class from the reloaded module
            DummyTextDrawer = pdftl.commands.helpers.text_drawer.TextDrawer

            # Check that instantiating it raises the expected error
            with pytest.raises(
                InvalidArgumentError, match="pip install pdftl\\[add_text\\]"
            ):
                DummyTextDrawer(mock_page_box)

        # --- Cleanup ---
        # We must reload the module *again* outside the patch
        # to restore the *real* TextDrawer for any subsequent tests.
        reload(pdftl.commands.helpers.text_drawer)


# --- Hypothesis Property-Based Tests ---

# --- Strategies for generating valid inputs ---

st_floats = st.floats(
    min_value=0, max_value=10000, allow_nan=False, allow_infinity=False
)

st_dim_rule = st.one_of(
    st.builds(lambda v: {"type": "pt", "value": v}, st_floats),
    st.builds(
        lambda v: {"type": "%", "value": v}, st.floats(min_value=0, max_value=100)
    ),
    st_floats,  # Test raw floats
    st.just(None),
)

st_page_box_hypothesis = st.builds(
    MockPageBox,
    width=st.floats(min_value=1, max_value=2000),
    height=st.floats(min_value=1, max_value=2000),
)

st_align = st.one_of(
    st.just("left"), st.just("center"), st.just("right"), st.just(None)
)

st_position_preset = st.sampled_from(
    [
        "top-left",
        "top-center",
        "top-right",
        "mid-left",
        "mid-center",
        "mid-right",
        "bottom-left",
        "bottom-center",
        "bottom-right",
    ]
)

st_position_rule = st.one_of(
    st.builds(lambda p: {"position": p}, st_position_preset),
    st.builds(lambda x, y: {"x": x, "y": y}, st_dim_rule, st_dim_rule),
)


@st.composite
def st_full_rule(draw):
    """Generates a rule dict for testing coordinates and matrices."""
    rule = draw(st_position_rule)
    rule["align"] = draw(st_align)
    rule["offset-x"] = draw(st_dim_rule)
    rule["offset-y"] = draw(st_dim_rule)
    rule["rotate"] = draw(st.floats(min_value=-360, max_value=360))
    return rule


class TestTextDrawerHypothesis(unittest.TestCase):
    """Property-based tests for the coordinate logic functions."""

    @given(dim_rule=st_dim_rule, page_dim=st_floats)
    @settings(max_examples=200)
    def test_resolve_dimension_hypothesis(self, dim_rule, page_dim):
        """Test that _resolve_dimension always returns a finite float."""
        result = _resolve_dimension(dim_rule, page_dim)
        self.assertIsInstance(result, float)
        self.assertTrue(math.isfinite(result))

    @given(
        rule=st_full_rule(),
        page_box=st_page_box_hypothesis,
    )
    @settings(max_examples=500, deadline=None)
    def test_get_base_coordinates_hypothesis(self, rule, page_box):
        """Test that _get_base_coordinates always returns valid coords."""
        x, y = _get_base_coordinates(rule, page_box)
        self.assertTrue(math.isfinite(x))
        self.assertTrue(math.isfinite(y))


if __name__ == "__main__":
    unittest.main()
