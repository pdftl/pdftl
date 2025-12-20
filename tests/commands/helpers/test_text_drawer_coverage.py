import io
import sys
import unittest
from unittest import mock

# --- Module Name for explicit reloads ---
module_name = "pdftl.commands.helpers.text_drawer"

# Mock structure for reportlab dependencies, used for patching.
mock_colors = mock.MagicMock()
mock_colors.black = (0, 0, 0)

mock_reportlab = {
    # The top-level package mock is essential to prevent ImportError on 'import reportlab'
    "reportlab": mock.MagicMock(),
    "reportlab.lib.colors": mock_colors,
    # pdfmetrics must exist and have getFont method for the real TextDrawer to be defined
    "reportlab.pdfbase.pdfmetrics": mock.MagicMock(getFont=mock.Mock()),
    "reportlab.pdfgen.canvas": mock.MagicMock(),
    # Also ensure pdftl.exceptions is mocked for the InvalidArgumentError alias check
    "pdftl.exceptions": mock.MagicMock(),
}


class MockPageBox:
    """A minimal mock object matching the interface required by TextDrawer.__init__."""

    def __init__(self, width, height):
        self.width = width
        self.height = height


class MockCanvasWithState:
    """A mock canvas that allows us to check internal drawing values."""

    def __init__(self, *args, **kwargs):
        self.packet = io.BytesIO(b"pdf_content")
        self.draw_x = None
        self.draw_y = None
        self.save_called = False
        self.seek_called = False
        self.read_called = False

    def stringWidth(self, text, font, size):
        return 100.0  # Fixed width for easy calculation

    def saveState(self):
        pass

    def setFillColorRGB(self, *args):
        pass

    def setFont(self, *args):
        pass

    def translate(self, *args):
        pass

    def rotate(self, *args):
        pass

    def drawString(self, x, y, text):
        # Captures the final calculated draw coordinates
        self.draw_x = x
        self.draw_y = y

    def save(self):
        self.save_called = True

    def seek(self, pos):
        if pos == 0:
            self.seek_called = True

    def read(self):
        self.read_called = True
        return b"test_output_bytes"

    def restoreState(self):
        pass


class TestTextDrawerCoverage(unittest.TestCase):

    def setUp(self):
        # Default mock page dimensions
        self.page_box_mock = MockPageBox(width=500, height=700)

    def tearDown(self):
        # Clean up sys.modules after tests that manipulated it
        # Note: This is crucial, but individual tests also pop it for clean re-import
        sys.modules.pop(module_name, None)

    def _get_real_text_drawer_module(self):
        """
        Forces a clean import of the production module under the reportlab mock,
        ensuring the real TextDrawer class is defined.
        """
        # Ensure the module is removed from sys.modules to force re-import
        sys.modules.pop(module_name, None)

        try:
            # Patch sys.modules with the reportlab mocks needed to pass the try/except block
            with mock.patch.dict("sys.modules", mock_reportlab):
                # Import the module. It should now successfully define the REAL TextDrawer.
                import pdftl.commands.helpers.text_drawer as _td_mod

                return _td_mod
        except Exception as e:
            self.fail(f"Failed to re-import production module under mock context: {e}")

    # --- Tests for Coordinate Helper Functions ---
    # These do not depend on TextDrawer or reportlab, so a simple import is fine.

    def test_resolve_dimension_invalid_type_l59(self):
        """Covers L59: Fallback return 0.0 for unknown dim_rule types."""
        import pdftl.commands.helpers.text_drawer as _td_mod

        # Non-dict, non-int, non-float, non-None
        self.assertEqual(_td_mod._resolve_dimension("invalid_string", 500), 0.0)
        self.assertEqual(_td_mod._resolve_dimension([1, 2, 3], 500), 0.0)

    def test_get_preset_x_default_l75(self):
        """Covers L75: Default return 0.0 (left) for unknown position strings."""
        import pdftl.commands.helpers.text_drawer as _td_mod

        self.assertEqual(_td_mod._get_preset_x("unknown-position", 500), 0.0)
        self.assertEqual(_td_mod._get_preset_x("midtop", 500), 0.0)

    def test_get_preset_y_coverage_l85_l87_l89_l90(self):
        """Covers L85, L87, L89, L90: Checks calculations for top, mid, bottom, and default/unknown positions."""
        import pdftl.commands.helpers.text_drawer as _td_mod

        # Covers L90: Default return (unknown position)
        self.assertEqual(_td_mod._get_preset_y("unknown-position", 700), 0.0)

        # Covers L87: "mid" in pos check (Corrected assertion for mid position)
        self.assertEqual(_td_mod._get_preset_y("midleft", 700), 350.0)

        # Covers L85: "top" in pos check
        self.assertEqual(_td_mod._get_preset_y("topright", 700), 700.0)

        # Covers L89: "bottom" in pos check
        self.assertEqual(_td_mod._get_preset_y("bottomcenter", 700), 0.0)

    # --- Tests for TextDrawer Class (Reportlab installed path) ---

    @mock.patch("reportlab.pdfgen.canvas.Canvas", new=MockCanvasWithState)
    def test_get_font_name_empty_or_none_l185(self):
        """Covers L185: Returns default font if input is None or empty string."""
        _td_mod = self._get_real_text_drawer_module()

        drawer = _td_mod.TextDrawer(self.page_box_mock)
        self.assertEqual(drawer.get_font_name(""), _td_mod.DEFAULT_FONT_NAME)
        self.assertEqual(drawer.get_font_name(None), _td_mod.DEFAULT_FONT_NAME)

    @mock.patch("reportlab.pdfgen.canvas.Canvas", new=MockCanvasWithState)
    def test_get_font_name_cache_hit_l189(self):
        """Covers L189: Checks if the font cache is used."""
        _td_mod = self._get_real_text_drawer_module()

        # Access the patched getFont mock object via the module's exposed alias
        mock_getFont = _td_mod.getFont
        mock_getFont.side_effect = None
        mock_getFont.reset_mock()

        drawer = _td_mod.TextDrawer(self.page_box_mock)

        # Use a non-standard font name to force registration check (L197) and miss the font map
        TEST_FONT_NAME = "CustomFont_Test"

        # 1. First call forces registration check (L197) and populates cache.
        first_font = drawer.get_font_name(TEST_FONT_NAME)
        self.assertEqual(
            first_font, TEST_FONT_NAME
        )  # Should return the same name if found/registered

        # 2. Mock getFont to raise an error if called again (ensuring cache is used)
        mock_getFont.side_effect = Exception("Should not be called")

        # 3. Second call should hit the cache (L188-L189)
        second_font = drawer.get_font_name(TEST_FONT_NAME)
        self.assertEqual(second_font, TEST_FONT_NAME)

        # Assert called once in total (first call only)
        mock_getFont.assert_called_once_with(TEST_FONT_NAME)

    @mock.patch("reportlab.pdfgen.canvas.Canvas", new=MockCanvasWithState)
    def test_draw_rule_empty_text_l220(self):
        """Covers L220: Skips drawing if text lambda returns empty string."""
        _td_mod = self._get_real_text_drawer_module()

        drawer = _td_mod.TextDrawer(self.page_box_mock)
        drawer.canvas.drawString = mock.Mock()

        rule = {"text": lambda c: "", "position": "top-left"}

        drawer.draw_rule(rule, {})

        drawer.canvas.drawString.assert_not_called()

    @mock.patch("reportlab.pdfgen.canvas.Canvas", new=MockCanvasWithState)
    def test_draw_rule_default_alignments_l254_l259(self):
        """
        Covers L254, L256, L259: Implicit alignment based on 'position'
        when 'align' is not specified. draw_x calculation determines coverage.
        """
        # 1. Get the real module and TextDrawer class
        _td_mod = self._get_real_text_drawer_module()

        drawer = _td_mod.TextDrawer(self.page_box_mock)

        # --- L254: 'right' in pos -> align='right' -> draw_x = -text_width
        rule_right = {"text": lambda c: "Test", "position": "top-right"}
        drawer.draw_rule(rule_right, {})
        # text_width is mocked to 100.0, so draw_x should be -100.0
        self.assertEqual(drawer.canvas.draw_x, -100.0)

        # --- L256: 'center' in pos -> align='center' -> draw_x = -text_width / 2
        rule_center = {"text": lambda c: "Test", "position": "mid-center"}
        drawer.draw_rule(rule_center, {})
        # Expected draw_x: -50.0
        self.assertEqual(drawer.canvas.draw_x, -50.0)

        # --- L259: Default (no 'align', no 'right', no 'center') -> align='left' -> draw_x = 0.0
        # 1. Using 'left' explicitly
        rule_left = {"text": lambda c: "Test", "position": "top-left"}
        drawer.draw_rule(rule_left, {})
        self.assertEqual(drawer.canvas.draw_x, 0.0)

        # 2. Using absolute x/y (no 'position')
        rule_absolute = {"text": lambda c: "Test", "x": 100, "y": 100}
        drawer.draw_rule(rule_absolute, {})
        self.assertEqual(drawer.canvas.draw_x, 0.0)

    @mock.patch("reportlab.pdfgen.canvas.Canvas", new=MockCanvasWithState)
    def test_save_method_l310_312(self):
        """Covers L310-312: Finalizes canvas, seeks packet, and reads bytes."""
        _td_mod = self._get_real_text_drawer_module()

        drawer = _td_mod.TextDrawer(self.page_box_mock)

        # Replace the BytesIO with our mock object which also has seek/read/save methods
        drawer.packet = drawer.canvas

        result = drawer.save()

        self.assertTrue(drawer.canvas.save_called)  # L310
        self.assertTrue(drawer.canvas.seek_called)  # L311
        self.assertTrue(drawer.canvas.read_called)  # L312
        self.assertEqual(result, b"test_output_bytes")

    # --- Tests for Exception Handling Paths ---

    @mock.patch("pdftl.commands.helpers.text_drawer.InvalidArgumentError")
    def test_import_error_fallback_valueerror_l23_24(self, mock_exception_alias):
        """
        Covers L23-24: Tests the case where pdftl.exceptions is NOT available
        and the fallback alias to ValueError is used.
        """
        # 1. Set up the environment to fail the pdftl.exceptions import
        sys.modules.pop(module_name, None)

        with mock.patch.dict("sys.modules", {"pdftl.exceptions": None}):
            # 2. Force a clean re-import of the module
            import pdftl.commands.helpers.text_drawer as _td_mod

            # Check the resulting alias on the module itself.
            self.assertIs(
                _td_mod.InvalidArgumentError,
                ValueError,
                "InvalidArgumentError should fall back to ValueError (L24)",
            )

    def test_dummy_textdrawer_methods_l331_335(self):
        """
        Covers L331 and L335: Tests that the dummy class methods exist (pass)
        if reportlab fails to import.
        """
        # 1. Remove the module from sys.modules
        sys.modules.pop(module_name, None)

        # 2. Set up the environment to fail the required reportlab imports.
        mocked_modules_none = {
            "reportlab.lib.colors": None,
            "reportlab.pdfbase.pdfmetrics": None,
            "reportlab.pdfgen.canvas": None,
            "reportlab": None,  # Top-level package
        }

        try:
            with mock.patch.dict("sys.modules", mocked_modules_none):
                # 3. Perform a fresh import. This will execute the code and hit
                # the 'except ImportError' block (L314) due to the patch.
                import pdftl.commands.helpers.text_drawer as _td_mod

                # Get the dummy class that was defined in the except block
                DummyTextDrawer = _td_mod.TextDrawer
        except Exception as e:
            self.fail(f"Fresh import failed unexpectedly when forcing ImportError path: {e}")
        finally:
            # Clean up the module from sys.modules again to avoid polluting subsequent tests
            sys.modules.pop(module_name, None)

        # --- Test the dummy class methods ---
        # Instantiate without calling __init__ (which raises the error)
        drawer = DummyTextDrawer.__new__(DummyTextDrawer)

        # Test draw_rule (L331) - should not raise
        try:
            drawer.draw_rule({}, {})
        except Exception as e:
            self.fail(f"draw_rule in dummy class should not raise: {e}")

        # Test save (L335) - should not raise
        try:
            drawer.save()
        except Exception as e:
            self.fail(f"save in dummy class should not raise: {e}")
