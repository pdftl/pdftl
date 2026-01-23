from unittest.mock import MagicMock

from pdftl.operations.helpers.text_drawer import (
    TextDrawer,
    _get_preset_x,
    _get_preset_y,
    _resolve_dimension,
)


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
    rule = {"text": lambda ctx: "", "font": "Helvetica"}
    assert drawer.draw_rule(rule, {}) is None


def test_draw_rule_inferred_alignment():
    """Covers lines 196 and 198: Infer align from position."""
    drawer = TextDrawer(MagicMock(width=500, height=800))

    # Trigger line 196 (right)
    rule_right = {"text": lambda ctx: "test", "position": "right-top", "color": (0, 0, 0)}
    drawer.draw_rule(rule_right, {})  # Should set align to 'right' internally

    # Trigger line 198 (center)
    rule_center = {"text": lambda ctx: "test", "position": "center-mid", "color": (0, 0, 0)}
    drawer.draw_rule(rule_center, {})  # Should set align to 'center' internally


def test_draw_rule_invalid_data_warning(caplog):
    """Covers lines 223-224: Exception handling in draw_rule."""
    drawer = TextDrawer(MagicMock(width=500, height=800))
    # Pass something that causes a TypeError (e.g., text is not a callable)
    rule = {"text": "not-a-callable"}
    drawer.draw_rule(rule, {})
    assert "Skipping one text rule" in caplog.text


from unittest.mock import patch


def test_get_font_name_external_registry():
    """Covers lines 152-153: Font not in standard list but in ReportLab."""
    drawer = TextDrawer(MagicMock(width=500, height=800))

    # 'Vera' is a common font name that ReportLab logic might encounter.
    # We mock getFont to succeed for this specific name.
    with patch("reportlab.pdfbase.pdfmetrics.getFont") as mock_get_font:
        mock_get_font.return_value = True
        # "MyCustomFont" is not in _STANDARD_T1_FONTS
        result = drawer.get_font_name("MyCustomFont")

        assert result == "MyCustomFont"
        assert drawer.font_cache["MyCustomFont"] == "MyCustomFont"


def test_draw_rule_default_left_align():
    """Covers line 200: Default to 'left' if no align and no position keywords."""
    drawer = TextDrawer(MagicMock(width=500, height=800))

    # Position 'top' contains neither 'right' nor 'center'
    rule = {"text": lambda ctx: "hello", "position": "top", "font": "Helvetica", "size": 12}

    # We spy on drawString to see if draw_x remained 0.0 (left aligned)
    with patch.object(drawer.canvas, "drawString") as mock_draw:
        drawer.draw_rule(rule, {})
        # drawString(x, y, text) -> x should be 0.0
        args, _ = mock_draw.call_args
        assert args[0] == 0.0


def test_text_drawer_save():
    """Covers lines 228-230: Saving the canvas and returning bytes."""
    drawer = TextDrawer(MagicMock(width=500, height=800))

    # Draw something so the canvas isn't empty
    rule = {"text": lambda ctx: "test", "position": "top"}
    drawer.draw_rule(rule, {})

    content = drawer.save()

    assert isinstance(content, bytes)
    assert content.startswith(b"%PDF")  # ReportLab output is a PDF snippet
