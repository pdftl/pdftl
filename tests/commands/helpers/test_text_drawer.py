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

import unittest
from unittest.mock import ANY, MagicMock, call, patch

import pytest


class TextDrawerTestMixin:
    """
    Mixin that provides fresh imports for every test run to avoid
    'Zombie Module' conflicts with parallel execution or reloading.
    """

    @property
    def module(self):
        """Helper to get the main module dynamically."""
        import pdftl.commands.helpers.text_drawer as m

        return m

    @property
    def TextDrawer(self):
        return self.module.TextDrawer

    @property
    def PageBox(self):
        return self.module._PageBox

    @property
    def get_base_coordinates(self):
        return self.module._get_base_coordinates

    @property
    def resolve_dimension(self):
        return self.module._resolve_dimension

    @property
    def DEFAULT_FONT_NAME(self):
        return self.module.DEFAULT_FONT_NAME

    @property
    def InvalidArgumentError(self):
        # Crucial: Must match the version raised by the fresh code
        try:
            from pdftl.exceptions import InvalidArgumentError

            return InvalidArgumentError
        except ImportError:
            return ValueError

    @property
    def MockPageBox(self):
        return self.PageBox


class TestTextDrawerLogic(TextDrawerTestMixin, unittest.TestCase):
    """
    Unit tests for the "pure" coordinate helper functions in text_drawer.py.
    """

    def setUp(self):
        self.page_box = self.MockPageBox(width=600.0, height=800.0)
        self.font_size = 10.0
        # Give a real text width to test alignment!
        self.text_width = 100.0

    def test_resolve_dimension(self):
        dim_rule_pt = {"type": "pt", "value": 50.0}
        self.assertEqual(self.resolve_dimension(dim_rule_pt, 800.0), 50.0)
        dim_rule_pct = {"type": "%", "value": 10.0}
        self.assertEqual(self.resolve_dimension(dim_rule_pct, 800.0), 80.0)
        self.assertEqual(self.resolve_dimension(20.0, 800.0), 20.0)
        self.assertEqual(self.resolve_dimension(None, 800.0), 0.0)

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
        x, y = self.get_base_coordinates(rule, self.page_box)
        self.assertEqual((x, y), (0.0, 800.0))  # Anchor: X=0, Y=800

        # Test 'top-center' anchor
        rule = {"position": "top-center", "align": "left"}
        x, y = self.get_base_coordinates(rule, self.page_box)
        # CORRECTED: The X anchor for "center" is 600 / 2 = 300.0
        self.assertEqual((x, y), (300.0, 800.0))

        # --- ALIGN CENTER (test 'top-left' anchor again) ---
        # 'align' is ignored, so the anchor is the same as the first test
        rule = {"position": "top-left", "align": "center"}
        x, y = self.get_base_coordinates(rule, self.page_box)
        # CORRECTED: The anchor is still (0.0, 800.0).
        self.assertEqual((x, y), (0.0, 800.0))

        # Test 'top-center' anchor again
        rule = {"position": "top-center", "align": "center"}
        x, y = self.get_base_coordinates(rule, self.page_box)
        # CORRECTED: The X anchor for "center" is 600 / 2 = 300.0
        self.assertEqual((x, y), (300.0, 800.0))

        # --- ALIGN RIGHT (test 'top-right' anchor) ---
        # This test was already correct
        rule = {"position": "top-right", "align": "right"}
        x, y = self.get_base_coordinates(rule, self.page_box)
        self.assertEqual((x, y), (600.0, 800.0))  # Anchor: X=600, Y=800

        # --- Test middle Y ---
        # This test was already correct
        rule = {"position": "mid-center"}
        x, y = self.get_base_coordinates(rule, self.page_box)
        # Anchor: X = 600 / 2 = 300.0, Y = 800 / 2 = 400.0
        self.assertEqual((x, y), (300.0, 400.0))


class TestTextDrawerClass(TextDrawerTestMixin):
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
        mock_page_box = self.MockPageBox(width=600, height=800)
        drawer = self.TextDrawer(mock_page_box)

        # 1. Test standard font: 'Helvetica'
        font_name = drawer.get_font_name("Helvetica")
        assert font_name == "Helvetica"
        mock_getFont.assert_not_called()

        # 2. Test another standard font, case-insensitive
        font_name = drawer.get_font_name("times-bold")
        assert font_name == "Times-Bold"
        mock_getFont.assert_not_called()

        from reportlab.pdfbase.pdfmetrics import FontNotFoundError

        # 3. Test bad font: 'Fake-Font-Name'
        mock_getFont.side_effect = FontNotFoundError("Font not found")
        with caplog.at_level("WARNING"):
            font_name = drawer.get_font_name("Fake-Font-Name")
            assert font_name == self.DEFAULT_FONT_NAME
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
        mock_page_box = self.MockPageBox(width=600, height=800)
        drawer = self.TextDrawer(mock_page_box)

        # Rule 1: Bad. The text lambda will fail.
        bad_rule = {"text": MagicMock(side_effect=TypeError("I am a bad rule!"))}
        context = {"page": 1}

        with caplog.at_level("WARNING"):
            drawer.draw_rule(bad_rule, context)
            assert len(caplog.records) == 1
            record = caplog.records[0]
            assert "Skipping one text rule" in record.message
            assert "I am a bad rule!" in str(record.args[0])

    # This helper method is now part of the pytest-style class
    def _run_draw_test(self, mock_canvas_instance, rule, expected_draw_x, expected_draw_y):
        """Helper to run a parameterized draw test."""

        mock_page_box = self.MockPageBox(width=600, height=800)  # Define box

        # Reset the mock's calls for this sub-test
        mock_canvas_instance.reset_mock()

        # Mock stringWidth to a known value
        mock_canvas_instance.stringWidth.return_value = 100.0  # text width

        drawer = self.TextDrawer(mock_page_box)  # Use box
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
        base_x, base_y = self.get_base_coordinates(full_rule, mock_page_box)  # Use box

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
    def test_draw_rule_geometry(self, position, align, expected_draw_x, expected_draw_y):
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
                self._run_draw_test(mock_canvas_instance, rule, expected_draw_x, expected_draw_y)


import subprocess
import sys

import pytest


class TestTextDrawerImportLogic:
    """
    Tests the module's behavior when dependencies are missing.
    Uses subprocesses to prevent state pollution in the main test suite.
    """

    def test_text_drawer_raises_error_without_reportlab(self):
        """
        Runs a separate python process that:
        1. Mocks reportlab as missing.
        2. Tries to instantiate TextDrawer.
        3. Asserts that it crashes with the correct error.
        """
        # Python script to run in the subprocess
        code = """
import sys
from unittest.mock import MagicMock

# 1. Poison sys.modules BEFORE importing the target
#    This simulates 'reportlab' being uninstalled.
sys.modules["reportlab"] = None
sys.modules["reportlab.pdfgen"] = None
sys.modules["reportlab.pdfgen.canvas"] = None
from pdftl.exceptions import UserCommandLineError
try:
    # 2. Import the module under test
    from pdftl.commands.helpers.text_drawer import TextDrawer, _PageBox
    
    # 3. Try to instantiate it
    page_box = _PageBox(width=100, height=100)
    TextDrawer(page_box)

except UserCommandLineError as e:
    # 4. Print the error so the parent process can read it
    print(f"CAUGHT: {e}")
    sys.exit(0) # Exit cleanly if we caught the expected error

# If we get here, it didn't raise!
print("DID NOT RAISE")
sys.exit(1)
        """

        # Run the subprocess
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)

        # Assertions
        assert result.returncode == 0, f"Subprocess crashed: {result.stderr}"
        assert "CAUGHT" in result.stdout
        # Verify your specific error message text here
        assert "pip install pdftl[add_text]" in result.stdout


if __name__ == "__main__":
    unittest.main()
