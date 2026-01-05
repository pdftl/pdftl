# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/helpers/text_drawer.py

"""
A helper module that provides a 'TextDrawer' class.

This module conditionally imports 'reportlab'. If 'reportlab' is not
installed, it defines a 'dummy' TextDrawer that raises a helpful
error on instantiation. This isolates the optional dependency.
"""

import io
import logging
from collections import namedtuple
from typing import Any, Tuple

logger = logging.getLogger(__name__)

# --- Fix 1: Use inheritance, not assignment, for the fallback exception ---
try:
    from pdftl.exceptions import InvalidArgumentError
except ImportError:

    class InvalidArgumentError(ValueError):  # type: ignore[no-redef]
        pass


# The user-friendly error message, defined once.
_MISSING_DEPS_ERROR_MSG = (
    "The 'add_text' operation requires the 'reportlab' library.\n"
    "To install this optional dependency, run:\n\n"
    "    pip install pdftl[add_text]"
)

# A simple box structure for coordinate calculations
_PageBox = namedtuple("_PageBox", ["width", "height"])


# --- Coordinate helper functions ---


def _resolve_dimension(dim_rule: Any, page_dim: float) -> float:
    """
    Resolves a parsed dimension (e.g., {'type': '%', 'value': 50})
    into an absolute float value in points.
    """
    if dim_rule is None:
        return 0.0
    if isinstance(dim_rule, (int, float)):
        return float(dim_rule)
    if isinstance(dim_rule, dict):
        value = float(dim_rule.get("value", 0))
        if dim_rule.get("type") == "%":
            return (value / 100.0) * page_dim
        return value  # Default to 'pt'
    return 0.0


def _get_preset_x(pos: str, page_width: float) -> float:
    """Calculates the X coordinate of the anchor point based on a preset string."""
    if "left" in pos:
        return 0.0
    if "center" in pos:
        return page_width / 2
    if "right" in pos:
        return page_width
    return 0.0  # Default to left


def _get_preset_y(pos: str, page_height: float) -> float:
    """Calculates the Y coordinate of the anchor point based on a preset string."""
    if "top" in pos:
        return page_height
    if "mid" in pos:
        return page_height / 2
    if "bottom" in pos:
        return 0.0
    return 0.0  # Default to bottom


def _get_absolute_coordinates(rule: dict, page_box: _PageBox) -> Tuple[float, float]:
    """Calculates anchor X,Y based on absolute 'x'/'y' rules."""
    anchor_x = _resolve_dimension(rule.get("x"), page_box.width)
    anchor_y = _resolve_dimension(rule.get("y"), page_box.height)
    return anchor_x, anchor_y


def _get_base_coordinates(rule: dict, page_box: _PageBox) -> Tuple[float, float]:
    """
    Gets the (x, y) coordinates for the text anchor point.
    Dispatches to preset helper or absolute helper.
    """
    if "position" in rule:
        pos = rule["position"]
        anchor_x = _get_preset_x(pos, page_box.width)
        anchor_y = _get_preset_y(pos, page_box.height)
        return anchor_x, anchor_y

    return _get_absolute_coordinates(rule, page_box)


# --- Fix 2: Handle Optional Imports Cleanly ---
# We try the import first, set a flag, and then define the class based on the flag.
# This prevents the "Redefinition of TextDrawer" error.

_HAS_REPORTLAB = False
try:
    from reportlab.lib import colors
    from reportlab.pdfbase.pdfmetrics import getFont
    from reportlab.pdfgen import canvas as reportlab_canvas

    _HAS_REPORTLAB = True
except ImportError:
    pass


if _HAS_REPORTLAB:
    # --- Dependencies imported successfully ---

    # Define constants
    _DEFAULT_COLOR_OBJ = colors.black
    _STANDARD_T1_FONTS = {
        "Courier",
        "Courier-Bold",
        "Courier-Oblique",
        "Courier-BoldOblique",
        "Helvetica",
        "Helvetica-Bold",
        "Helvetica-Oblique",
        "Helvetica-BoldOblique",
        "Times-Roman",
        "Times-Bold",
        "Times-Italic",
        "Times-BoldItalic",
        "Symbol",
        "ZapfDingbats",
    }
    _FONT_NAME_MAP = {name.lower(): name for name in _STANDARD_T1_FONTS}
    DEFAULT_FONT_NAME = "Helvetica"
    DEFAULT_FONT_SIZE = 12.0
    DEFAULT_COLOR_TUPLE = (0, 0, 0)  # (r, g, b)

    class TextDrawer:
        """
        A class that encapsulates all reportlab drawing logic.
        This "real" class is used when reportlab is installed.
        """

        def __init__(self, page_box: Any):
            self.page_box = _PageBox(width=page_box.width, height=page_box.height)
            self.packet = io.BytesIO()
            self.canvas = reportlab_canvas.Canvas(
                self.packet, pagesize=(self.page_box.width, self.page_box.height)
            )
            self.font_cache: dict[str, str] = {}

        def get_font_name(self, font_name: str) -> str:
            """Validates a font name against reportlab's registry."""
            if not font_name:
                return DEFAULT_FONT_NAME

            if font_name in self.font_cache:
                return self.font_cache[font_name]

            lower_name = font_name.lower()
            if lower_name in _FONT_NAME_MAP:
                self.font_cache[font_name] = _FONT_NAME_MAP[lower_name]
                return self.font_cache[font_name]

            from reportlab.pdfbase.pdfmetrics import FontError, FontNotFoundError

            try:
                getFont(font_name)
                self.font_cache[font_name] = font_name
                return font_name
            except (FontError, FontNotFoundError, KeyError, AttributeError):
                logger.warning(
                    "Could not find or register font '%s'. Falling back to %s.",
                    font_name,
                    DEFAULT_FONT_NAME,
                )
                self.font_cache[font_name] = DEFAULT_FONT_NAME
                return DEFAULT_FONT_NAME

        def draw_rule(self, rule: dict, context: dict):
            """Draws a single text rule onto the internal canvas."""
            try:
                # 1. Get text and font properties
                # Note: 'rule["text"]' is expected to be a callable based on previous logic
                text = rule["text"](context)
                if not text:
                    return

                font_name = self.get_font_name(rule.get("font", DEFAULT_FONT_NAME))
                font_size = rule.get("size", DEFAULT_FONT_SIZE)

                # 2. Get anchor point
                anchor_x, anchor_y = _get_base_coordinates(rule, self.page_box)

                # 3. Get user-defined offsets
                offset_x = _resolve_dimension(rule.get("offset-x"), self.page_box.width)
                offset_y = _resolve_dimension(rule.get("offset-y"), self.page_box.height)

                final_anchor_x = anchor_x + offset_x
                final_anchor_y = anchor_y + offset_y

                # 5. Get graphical properties
                color_tuple = rule.get("color", DEFAULT_COLOR_TUPLE)
                rotate = rule.get("rotate", 0)

                # 6. Calculate drawing offsets based on text dimensions
                text_width = self.canvas.stringWidth(text, font_name, font_size)
                pos = rule.get("position", "")

                align = rule.get("align")
                if align is None:
                    if "right" in pos:
                        align = "right"
                    elif "center" in pos:
                        align = "center"
                    else:
                        align = "left"

                draw_x = 0.0
                if align == "center":
                    draw_x = -text_width / 2
                elif align == "right":
                    draw_x = -text_width

                draw_y = 0.0
                if "top" in pos:
                    draw_y = -font_size
                elif "mid" in pos:
                    draw_y = -font_size / 2

                # 7. Apply to canvas
                self.canvas.saveState()
                self.canvas.setFillColorRGB(*color_tuple)
                self.canvas.setFont(font_name, font_size)
                self.canvas.translate(final_anchor_x, final_anchor_y)
                self.canvas.rotate(rotate)
                self.canvas.drawString(draw_x, draw_y, text)
                self.canvas.restoreState()

            except (ValueError, TypeError, KeyError, AttributeError) as e:
                logger.warning("Skipping one text rule due to invalid data: %s", e)
                logger.debug("Detailed traceback:", exc_info=True)

        def save(self) -> bytes:
            self.canvas.save()
            self.packet.seek(0)
            return self.packet.read()

else:
    # --- Dependencies failed to import ---

    class TextDrawer:  # type: ignore[no-redef]
        """
        A "dummy" class that is used if 'reportlab' is not installed.
        """

        def __init__(self, page_box: Any):
            raise InvalidArgumentError(_MISSING_DEPS_ERROR_MSG)

        def draw_rule(self, rule: dict, context: dict):
            pass

        def save(self) -> bytes:
            return b""
