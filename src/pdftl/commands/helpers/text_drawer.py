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

logger = logging.getLogger(__name__)

# Use a generic exception type from the core project if possible,
# otherwise fall back to a standard exception.
try:
    from pdftl.exceptions import InvalidArgumentError
except ImportError:
    InvalidArgumentError = ValueError


# The user-friendly error message, defined once.
# Assumes [project.optional-dependencies] is set up in pyproject.toml
_MISSING_DEPS_ERROR_MSG = (
    "The 'add_text' operation requires the 'reportlab' library.\n"
    "To install this optional dependency, run:\n\n"
    "    pip install pdftl[add_text]"
)

# A simple box structure for coordinate calculations
_PageBox = namedtuple("_PageBox", ["width", "height"])


# --- Coordinate helper functions ---
# These functions do not depend on reportlab, but are
# required by the TextDrawer logic. They are co-located
# here to keep add_text.py clean.


def _resolve_dimension(dim_rule, page_dim: float) -> float:
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
    """
    Calculates the X coordinate of the anchor point based on a preset string.

    Note: This is the anchor (e.g., page center), not the final
    left edge for drawing.
    """
    if "left" in pos:
        return 0.0
    if "center" in pos:
        return page_width / 2
    if "right" in pos:
        return page_width
    return 0.0  # Default to left


def _get_preset_y(pos: str, page_height: float) -> float:
    """
    Calculates the Y coordinate of the anchor point based on a preset string.

    Note: This is the anchor (e.g., page top), not the final text baseline.
    """
    if "top" in pos:
        return page_height
    if "mid" in pos:
        return page_height / 2
    if "bottom" in pos:
        return 0.0
    return 0.0  # Default to bottom


def _get_absolute_coordinates(rule: dict, page_box: _PageBox) -> tuple[float, float]:
    """
    Calculates anchor X,Y based on absolute 'x'/'y' rules.

    The (x, y) provided in the rule *is* the anchor point.
    """
    anchor_x = _resolve_dimension(rule.get("x"), page_box.width)
    anchor_y = _resolve_dimension(rule.get("y"), page_box.height)
    return anchor_x, anchor_y


def _get_base_coordinates(rule: dict, page_box: _PageBox) -> tuple[float, float]:
    """
    Gets the (x, y) coordinates for the text anchor point.

    This dispatches to either the preset helper (position-based) or
    the absolute helper (x/y-based).

    Args:
        rule: The text rule dictionary.
        page_box: The page dimensions.

    Returns:
        A tuple (anchor_x, anchor_y) for the text anchor.
    """
    if "position" in rule:
        pos = rule["position"]
        anchor_x = _get_preset_x(pos, page_box.width)
        anchor_y = _get_preset_y(pos, page_box.height)
        return anchor_x, anchor_y

    return _get_absolute_coordinates(rule, page_box)


try:
    # --- Try to import all required reportlab dependencies ---
    from reportlab.lib import colors
    from reportlab.pdfbase.pdfmetrics import getFont
    from reportlab.pdfgen import canvas as reportlab_canvas

    # from reportlab.lib.units import inch, mm, cm
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

        It is instantiated once per page and accumulates drawing
        commands.
        """

        def __init__(self, page_box):
            """
            Initializes a new drawing canvas for a page.

            Args:
                page_box: A pikepdf.Rectangle or compatible object
                          with .width and .height attributes.
            """
            self.page_box = _PageBox(width=page_box.width, height=page_box.height)
            self.packet = io.BytesIO()
            self.canvas = reportlab_canvas.Canvas(
                self.packet, pagesize=(self.page_box.width, self.page_box.height)
            )
            self.font_cache = {}

        def get_font_name(self, font_name: str) -> str:
            """Validates a font name against reportlab's registry."""
            if not font_name:
                return DEFAULT_FONT_NAME

            # Use a simple cache to avoid repeated getFont calls
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
            """
            Draws a single text rule onto the internal canvas.

            This method contains the core drawing logic and error handling
            to skip individual bad rules.
            """
            try:
                # 1. Get text and font properties
                text = rule["text"](context)
                if not text:
                    return  # Skip rules that render empty text

                font_name = self.get_font_name(rule.get("font", DEFAULT_FONT_NAME))
                font_size = rule.get("size", DEFAULT_FONT_SIZE)

                # 2. Get anchor point (independent of text width)
                # This is the (x,y) point the user is "pinning" the text to.
                anchor_x, anchor_y = _get_base_coordinates(rule, self.page_box)

                # 3. Get user-defined offsets
                offset_x = _resolve_dimension(rule.get("offset-x"), self.page_box.width)
                offset_y = _resolve_dimension(rule.get("offset-y"), self.page_box.height)

                # 4. Calculate final anchor point for translation
                final_anchor_x = anchor_x + offset_x
                final_anchor_y = anchor_y + offset_y

                # 5. Get graphical properties
                color_tuple = rule.get("color", DEFAULT_COLOR_TUPLE)
                rotate = rule.get("rotate", 0)

                # 6. Calculate drawing offsets based on text dimensions
                # These offsets determine where the text is drawn *relative*
                # to the anchor point, after translation and rotation.
                text_width = self.canvas.stringWidth(text, font_name, font_size)

                pos = rule.get("position", "")

                # Horizontal offset based on 'align'
                align = rule.get("align")  # Get user-specified alignment
                if align is None:
                    # No explicit alignment set; default based on position.
                    if "right" in pos:
                        align = "right"
                    elif "center" in pos:
                        align = "center"
                    else:
                        align = "left"  # Default for 'left' or absolute x/y

                if align == "center":
                    draw_x = -text_width / 2
                elif align == "right":
                    draw_x = -text_width
                else:
                    draw_x = 0.0  # align: "left"

                # Vertical offset based on 'position'
                # This determines the vertical anchor point of the text block.
                # reportlab.pdfgen.canvas.drawString(x, y) places the
                # *baseline* of the text at (x, y).

                if "top" in pos:
                    # Anchor is top. Draw text block *below* the anchor.
                    # We approximate ascent with font_size.
                    draw_y = -font_size
                elif "mid" in pos:
                    # Anchor is middle. Draw text block centered on the anchor.
                    draw_y = -font_size / 2
                else:
                    # Default: "bottom" in pos, or an absolute (x,y) anchor.
                    # Anchor is bottom/baseline. Draw text *at* the anchor.
                    draw_y = 0.0

                # 7. Apply to canvas
                self.canvas.saveState()
                self.canvas.setFillColorRGB(*color_tuple)
                self.canvas.setFont(font_name, font_size)

                # Apply transformations
                # Move the origin to the user's final anchor point
                self.canvas.translate(final_anchor_x, final_anchor_y)
                # Rotate the canvas around that new origin
                self.canvas.rotate(rotate)

                # Draw the string, offset from the new (0,0) origin
                self.canvas.drawString(draw_x, draw_y, text)

                self.canvas.restoreState()

            except (ValueError, TypeError, KeyError, AttributeError) as e:
                # These are likely due to bad user input in the 'rule' dict
                logger.warning("Skipping one text rule due to invalid data: %s", e)
                logger.debug("Detailed traceback for text rule failure:", exc_info=True)

        def save(self) -> bytes:
            """
            Finalizes the canvas and returns the PDF overlay as bytes.
            """
            self.canvas.save()
            self.packet.seek(0)
            return self.packet.read()

except ImportError:
    # --- Dependencies failed to import ---

    class TextDrawer:
        """
        A "dummy" class that is used if 'reportlab' is not installed.
        It does nothing except raise a helpful error when the user
        tries to use it (by instantiating it).
        """

        def __init__(self, page_box):
            # The "guard" is here. It raises a clear, helpful error
            # for the user.
            raise InvalidArgumentError(_MISSING_DEPS_ERROR_MSG)

        def draw_rule(self, rule: dict, context: dict):
            # This method will never be called, as __init__ fails first.
            pass

        def save(self) -> bytes:
            # This method will never be called.
            pass
