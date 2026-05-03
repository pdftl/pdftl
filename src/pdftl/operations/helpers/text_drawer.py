# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/helpers/text_drawer.py

"""
A helper module that provides a 'TextDrawer' class.

This module conditionally imports 'reportlab'. If 'reportlab' is not
installed, it defines a 'dummy' TextDrawer that raises a helpful
error on instantiation. This isolates the optional dependency.
"""

import io
import logging
from collections import namedtuple
from typing import Any

from pdftl.utils.dependencies import ensure_dependencies

logger = logging.getLogger(__name__)

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


def _get_absolute_coordinates(rule: dict, page_box: _PageBox) -> tuple[float, float]:
    """Calculates anchor X,Y based on absolute 'x'/'y' rules."""
    anchor_x = _resolve_dimension(rule.get("x"), page_box.width)
    anchor_y = _resolve_dimension(rule.get("y"), page_box.height)
    return anchor_x, anchor_y


def _get_base_coordinates(rule: dict, page_box: _PageBox) -> tuple[float, float]:
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


# Define constants
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
        ensure_dependencies(
            feature_name="add-text", dependencies={"reportlab": "reportlab"}, extra_tag="add-text"
        )

        # Local imports to avoid top-level failures
        from reportlab.pdfgen import canvas as reportlab_canvas

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

        from reportlab.pdfbase.pdfmetrics import FontError, FontNotFoundError, getFont

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
            runs = rule["text"](context)
            if not runs:
                return

            font_name = self.get_font_name(rule.get("font", DEFAULT_FONT_NAME))
            font_size = rule.get("size", DEFAULT_FONT_SIZE)
            color = rule.get("color", DEFAULT_COLOR_TUPLE)
            link_color = rule.get("linkcolor", color)
            rotate = rule.get("rotate", 0)

            full_text = "".join(text for text, _ in runs)
            text_width = self.canvas.stringWidth(full_text, font_name, font_size)
            pos = rule.get("position", "")
            draw_x, draw_y = self._get_draw_position(rule.get("align"), pos, text_width, font_size)

            anchor_x, anchor_y = _get_base_coordinates(rule, self.page_box)
            offset_x = _resolve_dimension(rule.get("offset-x"), self.page_box.width)
            offset_y = _resolve_dimension(rule.get("offset-y"), self.page_box.height)

            self.canvas.saveState()
            self.canvas.setFont(font_name, font_size)
            self.canvas.translate(anchor_x + offset_x, anchor_y + offset_y)
            self.canvas.rotate(rotate)

            cursor_x = draw_x
            for text, url in runs:
                cursor_x += _draw_run(
                    self.canvas,
                    text,
                    url,
                    cursor_x,
                    draw_y,
                    font_name,
                    font_size,
                    color,
                    link_color,
                )

            self.canvas.restoreState()

        except (ValueError, TypeError, KeyError, AttributeError) as e:
            logger.warning("Skipping one text rule due to invalid data: %s", e)
            logger.debug("Detailed traceback:", exc_info=True)

    def _get_draw_position(self, align, pos, text_width, font_size):
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

        return draw_x, draw_y

    def save(self) -> bytes:
        self.canvas.save()
        self.packet.seek(0)
        return self.packet.read()


def _transform_rect(canvas, x1, y1, x2, y2):
    corners = [
        canvas.absolutePosition(x1, y1),
        canvas.absolutePosition(x2, y1),
        canvas.absolutePosition(x1, y2),
        canvas.absolutePosition(x2, y2),
    ]
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def _draw_run(canvas, text, url, x, y, font_name, font_size, color, link_color):
    """Draws a single text run and optionally adds a URI annotation."""
    run_color = link_color if url else color
    canvas.setFillColorRGB(*run_color[:3])
    canvas.drawString(x, y, text)
    if url:
        from reportlab.pdfbase.pdfmetrics import getAscentDescent

        w = canvas.stringWidth(text, font_name, font_size)
        ascent, descent = getAscentDescent(font_name, font_size)
        rect = _transform_rect(canvas, x, y + descent, x + w, y + ascent)
        canvas.linkURL(url, rect, relative=0, thickness=0)
    return canvas.stringWidth(text, font_name, font_size)
