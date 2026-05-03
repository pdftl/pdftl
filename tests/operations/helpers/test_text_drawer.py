# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
Test suite for text_drawer.
"""

import subprocess
import sys
from unittest.mock import MagicMock

import pytest

from pdftl.operations.helpers import text_drawer


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


def test_get_font_name_logic(drawer, monkeypatch, caplog):
    inst, _ = drawer
    assert inst.get_font_name("Helvetica") == "Helvetica"
    assert inst.get_font_name("times-bold") == "Times-Bold"

    from reportlab.pdfbase.pdfmetrics import FontNotFoundError

    mock_get = MagicMock(side_effect=FontNotFoundError("Missing"))
    monkeypatch.setattr("reportlab.pdfbase.pdfmetrics.getFont", mock_get)

    with caplog.at_level("WARNING"):
        caplog.clear()
        name = inst.get_font_name("FakeFont")
        assert name == text_drawer.DEFAULT_FONT_NAME
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
