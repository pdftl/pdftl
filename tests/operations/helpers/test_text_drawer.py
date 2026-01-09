# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
Final hardened test suite for text_drawer.
Uses localized patching and explicit call verification to prevent pollution.
"""

import subprocess
import sys
from unittest.mock import ANY, MagicMock, call

import pytest

from pdftl.operations.helpers import text_drawer

# --- Fixtures ---


@pytest.fixture(scope="module", autouse=True)
def clean_slate_imports():
    """
    Forcefully remove 'reportlab' and 'text_drawer' from sys.modules
    to recover from any 'poisoning' by previous tests (e.g. setting modules to None).
    """
    # 1. Scrub reportlab modules so they can be freshly imported
    # (Iterate over a copy of keys since we are deleting)
    for mod_name in list(sys.modules.keys()):
        if mod_name == "reportlab" or mod_name.startswith("reportlab."):
            # Delete if it's None (poisoned) or even if it exists, to be safe
            if sys.modules[mod_name] is None:
                del sys.modules[mod_name]

    # 2. Scrub the target module so it re-executes its import statements
    #    (which will now succeed because reportlab is scrubbed)
    target = "pdftl.operations.helpers.text_drawer"
    if target in sys.modules:
        del sys.modules[target]

    # 3. Import it fresh right now to ensure it's valid
    import importlib

    importlib.import_module(target)


@pytest.fixture
def page_box():
    """Provides a fresh PageBox for every test."""
    return text_drawer._PageBox(width=600.0, height=800.0)


@pytest.fixture
def drawer(page_box, monkeypatch):
    """Provides a TextDrawer instance with reportlab_canvas mocked."""
    # Create the mock canvas class and its instance
    mock_canvas_cls = MagicMock()
    mock_canvas_instance = MagicMock()
    mock_canvas_cls.return_value = mock_canvas_instance

    # Apply the patch using the standard monkeypatch fixture
    monkeypatch.setattr(
        "pdftl.operations.helpers.text_drawer.reportlab_canvas.Canvas", mock_canvas_cls
    )

    # Create the drawer
    instance = text_drawer.TextDrawer(page_box)

    # Yield both for the test to use
    yield instance, mock_canvas_instance


# --- Logic Tests ---


def test_resolve_dimension():
    """Tests pure coordinate resolution logic."""
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


# --- Class & Geometry Tests ---


def test_get_font_name_logic(drawer, monkeypatch, caplog):
    inst, _ = drawer
    # 1. Standard font
    assert inst.get_font_name("Helvetica") == "Helvetica"

    # 2. Case-insensitive standard font
    assert inst.get_font_name("times-bold") == "Times-Bold"

    # 3. Bad font fallback
    from reportlab.pdfbase.pdfmetrics import FontNotFoundError

    mock_get = MagicMock(side_effect=FontNotFoundError("Missing"))
    monkeypatch.setattr("pdftl.operations.helpers.text_drawer.getFont", mock_get)

    with caplog.at_level("WARNING"):
        caplog.clear()
        name = inst.get_font_name("FakeFont")
        assert name == text_drawer.DEFAULT_FONT_NAME
        assert "FakeFont" in caplog.text


def test_draw_rule_skips_bad_rule(drawer, caplog):
    inst, _ = drawer
    # Create a rule that will fail when evaluated
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
def test_draw_rule_geometry(drawer, monkeypatch, position, align, exp_x, exp_y, page_box):
    """Verifies precise call sequence to ensure geometry is correct."""
    inst, mock_canvas = drawer

    mock_canvas.stringWidth.return_value = 100.0

    rule = {
        "text": lambda ctx: "Hello",
        "font": "Helvetica",
        "size": 12.0,
        "position": position,
        "align": align,
        "color": (0, 0, 0),
        "offset-x": 0,
        "offset-y": 0,
        "rotate": 0,
    }

    base_x, base_y = text_drawer._get_base_coordinates(rule, page_box)

    mock_get = MagicMock()
    monkeypatch.setattr("pdftl.operations.helpers.text_drawer.getFont", mock_get)

    inst.draw_rule(rule, {})

    # Ensure calls happened in order to verify state management
    expected = [
        call.saveState(),
        call.setFillColorRGB(ANY, ANY, ANY),
        call.setFont("Helvetica", 12.0),
        call.translate(base_x, base_y),
        call.rotate(0),
        call.drawString(exp_x, exp_y, "Hello"),
        call.restoreState(),
    ]
    mock_canvas.assert_has_calls(expected, any_order=False)


# --- Isolation Tests ---


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
    assert "pip install pdftl[add_text]" in result.stdout
