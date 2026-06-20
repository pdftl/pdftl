# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
Test suite for text_drawer.
"""

import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from pdftl.operations.helpers import text_drawer
from pdftl.operations.helpers.text_drawer import (
    TextDrawer,
    _get_preset_x,
    _get_preset_y,
    _PageBox,
    _resolve_dimension,
)
import io
from pikepdf import Pdf, Rectangle


@pytest.fixture(scope="module", autouse=True)
def clean_slate_imports():
    """
    Forcefully remove 'reportlab' and 'text_drawer' from sys.modules
    to recover from any 'poisoning' by previous tests.
    """
    for mod_name in list(sys.modules.keys()):
        if mod_name == "reportlab" or mod_name.startswith("reportlab."):
            if sys.modules[mod_name] is None:
                del sys.modules[mod_name]

    target = "pdftl.operations.helpers.text_drawer"
    if target in sys.modules:
        del sys.modules[target]

    import importlib

    importlib.import_module(target)


@pytest.fixture
def page_box():
    return text_drawer._PageBox(width=600.0, height=800.0)


@pytest.fixture
def drawer(page_box, monkeypatch):
    mock_canvas_cls = MagicMock()
    mock_canvas_instance = MagicMock()
    mock_canvas_cls.return_value = mock_canvas_instance
    monkeypatch.setattr("reportlab.pdfgen.canvas.Canvas", mock_canvas_cls)
    instance = text_drawer.TextDrawer(page_box)
    yield instance, mock_canvas_instance


# --- Pure function tests ---


def test_resolve_dimension():
    resolve = text_drawer._resolve_dimension
    assert resolve({"type": "pt", "value": 50.0}, 800.0) == 50.0
    assert resolve({"type": "%", "value": 10.0}, 800.0) == 80.0
    assert resolve(20.0, 800.0) == 20.0
    assert resolve(None, 800.0) == 0.0


@pytest.mark.parametrize(
    "rule, expected",
    [
        ({"position": "top-left", "align": "left"}, (0.0, 800.0)),
        ({"position": "top-center", "align": "left"}, (300.0, 800.0)),
        ({"position": "top-right", "align": "right"}, (600.0, 800.0)),
        ({"position": "mid-center"}, (300.0, 400.0)),
    ],
)
def test_get_base_coordinates(rule, expected, page_box):
    assert text_drawer._get_base_coordinates(rule, page_box) == expected


# --- TextDrawer tests ---

import pdftl.operations.helpers.text_drawer as td  # need this for fast test apparently
import pdftl.fonts.file_locator as fl  # and this


def test_get_font_name_logic(drawer, monkeypatch, caplog):
    inst, _ = drawer
    inst.font_cache = {}

    # Hammer down EVERY possible namespace reference to the system resolver
    # to guarantee it NEVER touches the disk scan loop.
    monkeypatch.setattr(fl, "resolve_system_font_path", lambda _: None)
    monkeypatch.setattr(td, "resolve_system_font_path", lambda _: None)

    assert inst.get_font_name("Helvetica") == "Helvetica"
    assert inst.get_font_name("times-bold") == "Times-Bold"

    from reportlab.pdfbase.pdfmetrics import FontNotFoundError

    # Target the absolute module level properties directly
    import reportlab.pdfbase.pdfmetrics as pdfm

    mock_get = MagicMock(side_effect=FontNotFoundError("Missing"))

    monkeypatch.setattr(pdfm, "getFont", mock_get)
    monkeypatch.setattr("reportlab.pdfbase.pdfmetrics.getFont", mock_get)

    with caplog.at_level("WARNING"):
        caplog.clear()
        name = inst.get_font_name("FakeFont")
        assert name == td.DEFAULT_FONT_NAME
        assert "FakeFont" in caplog.text


def test_draw_rule_skips_bad_rule(drawer, caplog):
    inst, _ = drawer
    bad_rule = {"text": MagicMock(side_effect=TypeError("Logic Error"))}
    with caplog.at_level("WARNING"):
        caplog.clear()
        inst.draw_rule(bad_rule, {"page": 1})
        assert "Skipping one text rule" in caplog.text


@pytest.mark.parametrize(
    "position, align, exp_x, exp_y",
    [
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
def test_draw_rule_geometry(drawer, monkeypatch, position, align, exp_x, exp_y):
    """Verifies drawString is called with the correct coordinates."""
    inst, mock_canvas = drawer
    mock_canvas.stringWidth.return_value = 100.0

    rule = {
        "text": lambda ctx: [("Hello", None)],
        "font": "Helvetica",
        "size": 12.0,
        "position": position,
        "align": align,
        "color": (0, 0, 0),
        "offset-x": 0,
        "offset-y": 0,
        "rotate": 0,
    }

    monkeypatch.setattr("reportlab.pdfbase.pdfmetrics.getFont", MagicMock())
    inst.draw_rule(rule, {})

    mock_canvas.drawString.assert_called_once_with(exp_x, exp_y, "Hello")


# --- Isolation test ---


def test_text_drawer_raises_error_without_reportlab():
    code = """
import sys
sys.modules["reportlab"] = None
sys.modules["reportlab.pdfgen"] = None
from pdftl.exceptions import UserCommandLineError
try:
    from pdftl.operations.helpers.text_drawer import TextDrawer, _PageBox
    TextDrawer(_PageBox(100, 100))
except UserCommandLineError as e:
    print(f"CAUGHT: {e}")
    sys.exit(0)
sys.exit(1)
    """
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0
    assert "pip install pdftl[add-text]" in result.stdout


def test_draw_rule_with_link(drawer, monkeypatch):
    """Verifies link creation and coordinate transformation for URLs."""
    inst, mock_canvas = drawer

    # 1. Setup Mocks
    # Mock absolutePosition to simulate a simple translation (e.g., identity)
    mock_canvas.absolutePosition.side_effect = lambda x, y: (x, y)
    mock_canvas.stringWidth.return_value = 50.0

    # Mock getAscentDescent which is imported inside _draw_run
    mock_gad = MagicMock(return_value=(10.0, -2.0))  # ascent, descent
    monkeypatch.setattr("reportlab.pdfbase.pdfmetrics.getAscentDescent", mock_gad)
    # Ensure font lookup doesn't fail
    monkeypatch.setattr("reportlab.pdfbase.pdfmetrics.getFont", MagicMock())

    # 2. Define a rule with a URL
    rule = {
        "text": lambda ctx: [("Click Me", "https://example.com")],
        "font": "Helvetica",
        "size": 12.0,
        "position": "bottom-left",
        "linkcolor": (0, 0, 1),  # Blue
    }

    # 3. Execute
    inst.draw_rule(rule, {})

    # 4. Assertions
    # Verify fill color was set to linkcolor
    mock_canvas.setFillColorRGB.assert_called_with(0, 0, 1)

    # Verify linkURL was called with the correct URL and calculated Rect
    # Rect = (min_x, min_y, max_x, max_y)
    # x=0, y=0, w=50, descent=-2, ascent=10 -> (0, -2, 50, 10)
    mock_canvas.linkURL.assert_called_once_with(
        "https://example.com", (0.0, -2.0, 50.0, 10.0), relative=0, thickness=0
    )


def test_get_font_name_cache(drawer):
    """Verifies that font names are cached after the first lookup."""
    inst, _ = drawer

    # First call - populates cache
    name1 = inst.get_font_name("Helvetica")
    assert "Helvetica" in inst.font_cache

    # Second call - should return from cache
    # We can verify this by checking if the cache dictionary was accessed
    name2 = inst.get_font_name("Helvetica")
    assert name1 == name2 == "Helvetica"


# tests/operations/helpers/test_text_drawer.py


@pytest.fixture
def mock_drawer():
    """Returns a TextDrawer with a mocked canvas to intercept draw calls."""
    page_box = _PageBox(width=500, height=500)
    drawer = TextDrawer(page_box)
    drawer.canvas = MagicMock()
    return drawer


@patch("reportlab.pdfbase.pdfmetrics.getAscentDescent")
def test_drawer_draws_background_rect(mock_metrics, mock_drawer):
    """Test that explicit bgcolor and padding trigger the correct rect drawing."""
    # Mock font metrics: Ascent 10, Descent -2
    mock_metrics.return_value = (10.0, -2.0)

    # Mock string width to be 50 points
    mock_drawer.canvas.stringWidth.return_value = 50.0

    rule = {
        "text": lambda ctx: [("Hello", None)],
        "bgcolor": (1.0, 0.0, 0.0, 0.5),  # Red, 50% opacity
        "padding": 10.0,
        "x": 100,
        "y": 100,
        "font": "Helvetica",
        "size": 12.0,
        "align": "left",
    }

    mock_drawer.draw_rule(rule, {})

    # 1. Assert color and alpha were set correctly for the background
    mock_drawer.canvas.setFillColorRGB.assert_any_call(1.0, 0.0, 0.0)
    mock_drawer.canvas.setFillAlpha.assert_any_call(0.5)

    # 2. Assert the rectangle math is correct
    # draw_x = 0 (left align), draw_y = 0
    # bg_x = 0 - 10(padding) = -10
    # bg_y = 0 + (-2)(descent) - 10(padding) = -12
    # bg_w = 50(width) + 20(padding*2) = 70
    # bg_h = (10 - -2)(ascent-descent) + 20(padding*2) = 32
    mock_drawer.canvas.rect.assert_called_with(-10.0, -12.0, 70.0, 32.0, fill=1, stroke=0)

    # 3. Assert alpha was reset to 1.0 for the text
    mock_drawer.canvas.setFillAlpha.assert_called_with(1.0)


@patch("reportlab.pdfbase.pdfmetrics.getAscentDescent")
def test_drawer_default_white_background(mock_metrics, mock_drawer):
    """Test that providing padding without a bgcolor defaults to opaque white."""
    mock_metrics.return_value = (10.0, -2.0)
    mock_drawer.canvas.stringWidth.return_value = 50.0

    rule = {
        "text": lambda ctx: [("Hello", None)],
        "padding": 5.0,
        "align": "left",
        # Notice: No bgcolor provided
    }

    mock_drawer.draw_rule(rule, {})

    # It should default to [1, 1, 1] (White)
    mock_drawer.canvas.setFillColorRGB.assert_any_call(1, 1, 1)

    # Rect math for 5pt padding:
    # bg_x = -5, bg_y = -7, bg_w = 60, bg_h = 22
    mock_drawer.canvas.rect.assert_called_with(-5.0, -7.0, 60.0, 22.0, fill=1, stroke=0)


@patch("reportlab.pdfbase.pdfmetrics.getAscentDescent")
def test_drawer_zero_padding_triggers_minimal_background(mock_metrics, mock_drawer):
    """Test that explicit padding=0 triggers the default white background tightly around text."""
    mock_metrics.return_value = (10.0, -2.0)
    mock_drawer.canvas.stringWidth.return_value = 50.0

    rule = {
        "text": lambda ctx: [("Hello", None)],
        "padding": 0.0,  # Explicitly providing 0
        "align": "left",
        # Notice: No bgcolor provided
    }

    mock_drawer.draw_rule(rule, {})

    # It should default to [1, 1, 1] (White)
    mock_drawer.canvas.setFillColorRGB.assert_any_call(1, 1, 1)

    # Rect math for 0pt padding:
    # bg_x = 0, bg_y = 0 + (-2) = -2
    # bg_w = 50, bg_h = (10 - -2) = 12
    mock_drawer.canvas.rect.assert_called_with(0.0, -2.0, 50.0, 12.0, fill=1, stroke=0)


def test_drawer_text_with_alpha_channel(mock_drawer):
    """Test that providing a 4-tuple for text color sets the alpha channel."""
    rule = {
        "text": lambda ctx: [("Transparent Text", None)],
        "color": (0.0, 0.0, 1.0, 0.5),  # Blue, 50% opacity
        "align": "left",
    }

    mock_drawer.canvas.stringWidth.return_value = 100.0
    mock_drawer.draw_rule(rule, {})

    # Assert that the RGB colors were set for the text run
    mock_drawer.canvas.setFillColorRGB.assert_any_call(0.0, 0.0, 1.0)

    # Assert that line 289 was hit to set the text alpha
    mock_drawer.canvas.setFillAlpha.assert_called_with(0.5)


# --- merged from test_text_drawer_coverage.py ---


def test_resolve_dimension_unsupported_type():
    """Covers line 45: Returns 0.0 for unsupported types."""
    assert _resolve_dimension([10], 100) == 0.0


def test_preset_coordinate_fallbacks():
    """Covers lines 56 and 67: Default to 0.0 for unknown position strings."""
    assert _get_preset_x("unknown", 100) == 0.0
    assert _get_preset_y("unknown", 100) == 0.0


def test_get_font_name_empty_and_cache():
    """Covers lines 138, 141, 152-153: Font logic."""
    drawer = TextDrawer(MagicMock(width=500, height=800))

    # 138: Empty font name
    assert drawer.get_font_name("") == "Helvetica"

    # 152-153: Valid non-standard font (ReportLab usually has 'Courier')
    # Even if not standard in our list, getFont will find it.
    assert drawer.get_font_name("Courier") == "Courier"

    # 141: Trigger Cache hit
    assert drawer.get_font_name("Courier") == "Courier"


def test_draw_rule_empty_text():
    """Covers line 170: Early return if text is empty."""
    drawer = TextDrawer(MagicMock(width=500, height=800))
    # Rule with a lambda that returns empty string
    rule = {"text": lambda ctx: [], "font": "Helvetica"}
    assert drawer.draw_rule(rule, {}) is None


def test_draw_rule_inferred_alignment():
    """Covers lines 196 and 198: Infer align from position."""
    drawer = TextDrawer(MagicMock(width=500, height=800))

    # Trigger line 196 (right)
    rule_right = {
        "text": lambda ctx: [("test", None)],
        "position": "right-top",
        "color": (0, 0, 0),
    }
    drawer.draw_rule(rule_right, {})  # Should set align to 'right' internally

    # Trigger line 198 (center)
    rule_center = {
        "text": lambda ctx: [("test", None)],
        "position": "center-mid",
        "color": (0, 0, 0),
    }
    drawer.draw_rule(rule_center, {})  # Should set align to 'center' internally


def test_draw_rule_invalid_data_warning(caplog):
    """Covers lines 223-224: Exception handling in draw_rule."""
    drawer = TextDrawer(MagicMock(width=500, height=800))
    # Pass something that causes a TypeError (e.g., text is not a callable)
    rule = {"text": "not-a-callable"}
    drawer.draw_rule(rule, {})
    assert "Skipping one text rule" in caplog.text


def test_get_font_name_external_registry(monkeypatch):
    drawer = TextDrawer.__new__(TextDrawer)
    drawer.font_cache = {}

    monkeypatch.setattr(
        text_drawer,
        "resolve_system_font_path",
        lambda _: None,
    )

    with patch("reportlab.pdfbase.pdfmetrics.getFont") as mock_get_font:
        mock_get_font.return_value = True
        assert drawer.get_font_name("MyCustomFont") == "MyCustomFont"


def test_draw_rule_default_left_align():
    """Covers line 200: Default to 'left' if no align and no position keywords."""
    drawer = TextDrawer(MagicMock(width=500, height=800))

    rule = {
        "text": lambda ctx: [("hello", None)],
        "position": "top",
        "font": "Helvetica",
        "size": 12,
    }

    # Force canvas initialization with a harmless draw first
    drawer.draw_rule({"text": lambda ctx: [("init", None)]}, {})

    # Now canvas exists and we can patch drawString on it
    with patch.object(drawer.canvas, "drawString") as mock_draw:
        drawer.draw_rule(rule, {})
        args, _ = mock_draw.call_args
        assert args[0] == 0.0


def test_text_drawer_save():
    """Covers lines 228-230: Saving the canvas and returning bytes."""
    drawer = TextDrawer(MagicMock(width=500, height=800))

    # Draw something so the canvas isn't empty
    rule = {"text": lambda ctx: [("test", None)], "position": "top"}
    drawer.draw_rule(rule, {})

    content = drawer.save()

    assert isinstance(content, bytes)
    assert content.startswith(b"%PDF")  # ReportLab output is a PDF snippet


# Append to tests/operations/helpers/test_text_drawer.py

# --- Targeted additions to push coverage to 100% ---


@patch("reportlab.pdfbase.pdfmetrics.registerFont")
def test_register_external_font_success(mock_register, mock_drawer):
    """Covers line 147: Checks caching flows when external font registrations execute smoothly."""
    mock_drawer.font_cache.clear()
    with patch("reportlab.pdfbase.ttfonts.TTFont", return_value=MagicMock()):
        res = mock_drawer._register_external_font("CustomOSFont", "/dir/CustomOSFont.ttf")
        assert res == "CustomOSFont"
        assert mock_drawer.font_cache["CustomOSFont"] == "CustomOSFont"


def test_register_external_font_exception_fallback(mock_drawer, caplog):
    """Covers lines 148-155: Catches registration errors, logging an error and choosing Helvetica."""
    mock_drawer.font_cache.clear()
    with patch(
        "reportlab.pdfbase.pdfmetrics.registerFont", side_effect=ValueError("Bad structural units")
    ):
        with caplog.at_level("WARNING"):
            res = mock_drawer._register_external_font("BrokenFont", "/dir/broken.ttf")
            assert res == "Helvetica"
            assert mock_drawer.font_cache["BrokenFont"] == "Helvetica"
            assert "Failed to register resolved font" in caplog.text


def test_get_font_name_as_literal_file_path(mock_drawer):
    """Covers line 172: Directly intercepts strings that point straight to a local font file."""
    mock_drawer.font_cache.clear()
    with (
        patch("os.path.isfile", return_value=True),
        patch.object(
            mock_drawer, "_register_external_font", return_value="RegisteredLiteral"
        ) as mock_reg,
    ):
        res = mock_drawer.get_font_name("/assets/fonts/MyFont.ttf")
        assert res == "RegisteredLiteral"
        mock_reg.assert_called_once_with("/assets/fonts/MyFont.ttf", "/assets/fonts/MyFont.ttf")


@patch("reportlab.pdfbase.pdfmetrics.getAscentDescent", side_effect=KeyError)
def test_draw_background_metrics_lookup_fallback(mock_metrics, mock_drawer):
    """Covers line 215-216: Falls back to font_size as ascent and 0.0 as descent upon missing metrics."""
    # Providing padding ensures background rectangle code is processed
    rule = {
        "text": lambda ctx: [("Hello Fallback", None)],
        "padding": 5.0,
        "bgcolor": (1, 1, 1),
    }
    mock_drawer.canvas.stringWidth.return_value = 40.0

    # We trace calls to rect() to confirm if fallback coordinates used math matching (ascent=fontSize)
    # bg_h = (font_size - 0.0) + (padding * 2) = 12.0 + 10.0 = 22.0
    mock_drawer.draw_rule(rule, {})
    mock_drawer.canvas.rect.assert_called_with(-5.0, -5.0, 50.0, 22.0, fill=1, stroke=0)


@patch(
    "reportlab.pdfbase.pdfmetrics.getFont",
    side_effect=TypeError("String Width computation failure"),
)
def test_draw_rule_string_width_calculation_error(mock_get_font, mock_drawer, caplog):
    """Covers lines 252-254: Catches stringWidth computation evaluation errors and safely skips processing."""
    rule = {
        "text": lambda ctx: [("Crash width calculation", None)],
    }
    # Forcing stringWidth to crash out
    mock_drawer.canvas.stringWidth.side_effect = TypeError("invalid format inputs")
    with caplog.at_level("WARNING"):
        mock_drawer.draw_rule(rule, {})
        assert "Failed to calculate text width for rule" in caplog.text


def test_draw_run_invalid_color_format_warning(mock_drawer, caplog):
    """Covers lines 340-341: Intercepts formatting errors inside individual text run coloring assignments."""
    rule = {
        "text": lambda ctx: [("Bad color run", None)],
        "color": 42,  # Integer is not unpackable with *operator
    }
    mock_drawer.canvas.stringWidth.return_value = 10.0
    with caplog.at_level("WARNING"):
        mock_drawer.draw_rule(rule, {})
        assert "Invalid text color format" in caplog.text


def test_draw_run_url_annotation_exception(mock_drawer, caplog):
    """Covers lines 353-354: Protects rendering loop if URL linkURL annotations trigger runtime errors."""
    rule = {
        "text": lambda ctx: [("Crash annotation link", "http://crash.me")],
    }
    mock_drawer.canvas.stringWidth.return_value = 10.0
    # Force linkURL call to raise an entry error
    mock_drawer.canvas.linkURL.side_effect = ValueError("Corrupt link mapping context")
    with caplog.at_level("WARNING"):
        mock_drawer.draw_rule(rule, {})
        assert "Failed to render URL annotation" in caplog.text


def test_draw_run_final_string_width_exception(mock_drawer):
    """Covers lines 358-359: Validates fallback return width of 0.0 if stringWidth throws an exception at end of run."""
    rule = {
        "text": lambda ctx: [("Crash ending width call", None)],
    }
    # First call in draw_rule succeeds to evaluate geometric boundaries, second inside loop breaks
    mock_drawer.canvas.stringWidth.side_effect = [50.0, TypeError("Forced error down low")]

    # Executing must not raise an error up the stack
    mock_drawer.draw_rule(rule, {})


def test_draw_background_invalid_format_exception(mock_drawer, caplog):
    """Covers lines 229-230: Captures packing errors if bgcolor contains un-unpackable structures."""
    rule = {
        "text": lambda ctx: [("Hello Exception", None)],
        "padding": 2.0,
        "bgcolor": 12345,  # Numeric scalars completely break unpacking sequences, forcing the exception
    }
    mock_drawer.canvas.stringWidth.return_value = 50.0
    with caplog.at_level("WARNING"):
        mock_drawer.draw_rule(rule, {})
        assert "Failed to draw background color due to invalid format" in caplog.text


def test_get_font_name_resolves_system_font(monkeypatch):
    drawer = TextDrawer.__new__(TextDrawer)
    drawer.font_cache = {}

    monkeypatch.setattr(
        text_drawer,
        "resolve_system_font_path",
        lambda _: "/fake/fonts/MyFont.ttf",
    )

    monkeypatch.setattr(
        TextDrawer,
        "_register_external_font",
        lambda self, font_name, path: "RegisteredFont",
    )

    assert drawer.get_font_name("MyFont") == "RegisteredFont"


def test_reset_page_box_with_existing_canvas_and_content():
    """Covers lines 145-148: showPage is called when canvas exists and has content."""
    drawer = TextDrawer(MagicMock(width=500, height=800))
    # Force canvas into existence with content
    drawer.draw_rule({"text": lambda ctx: [("hello", None)]}, {})
    assert drawer.canvas is not None

    with (
        patch.object(drawer.canvas, "showPage") as mock_show_page,
        patch.object(drawer.canvas, "setPageSize") as mock_set_size,
    ):
        from pikepdf import Rectangle

        drawer.reset_page_box(Rectangle(0, 0, 300, 400))
        mock_show_page.assert_called_once()
        mock_set_size.assert_called_once_with((300.0, 400.0))


def test_register_external_font_initializes_canvas_if_none():
    """Covers line 159: canvas is initialized inside _register_external_font if None."""
    drawer = TextDrawer(MagicMock(width=500, height=800))
    assert drawer.canvas is None

    with (
        patch("reportlab.pdfbase.pdfmetrics.registerFont"),
        patch("reportlab.pdfbase.ttfonts.TTFont", return_value=MagicMock()),
    ):
        drawer._register_external_font("TestFont", "/fake/TestFont.ttf")

    assert drawer.canvas is not None


def test_save_returns_empty_bytes_when_canvas_is_none():
    """Covers line 332: save() returns b'' when nothing has been drawn."""
    drawer = TextDrawer(MagicMock(width=500, height=800))
    assert drawer.canvas is None
    result = drawer.save()
    assert result == b""


def test_text_drawer_preserves_page_boundaries_when_a_page_draws_nothing():
    """A page whose rule(s) produce no visible runs must still get its own
    overlay page — otherwise its content silently merges into the next
    page's canvas page and overlay_page_indices desyncs from the real
    page boundaries (regression for the add_text page-shift bug)."""
    drawer = TextDrawer(page_box=Rectangle(0, 0, 200, 200))

    # Page 1: real content
    drawer.reset_page_box(Rectangle(0, 0, 200, 200))
    drawer.draw_rule({"text": lambda ctx: [("Page One", None)]}, {})

    # Page 2: rule evaluates to no runs (e.g. a conditional/filtered rule)
    drawer.reset_page_box(Rectangle(0, 0, 200, 200))
    drawer.draw_rule({"text": lambda ctx: []}, {})

    # Page 3: real content again
    drawer.reset_page_box(Rectangle(0, 0, 200, 200))
    drawer.draw_rule({"text": lambda ctx: [("Page Three", None)]}, {})

    overlay_bytes = drawer.save()
    with Pdf.open(io.BytesIO(overlay_bytes)) as overlay_pdf:
        # Must be 3 distinct pages — page 2's empty output must not get
        # folded into page 3's canvas page.
        assert len(overlay_pdf.pages) == 3
