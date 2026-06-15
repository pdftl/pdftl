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
import os
from collections import namedtuple
from typing import Any

from pdftl.fonts.file_locator import resolve_system_font_path
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


# --- Constants ---

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

        self.page_box = _PageBox(width=page_box.width, height=page_box.height)
        self.packet = io.BytesIO()
        self.canvas = None
        self.font_cache: dict[str, str] = {}
        self._has_content = False

    def reset_page_box(self, new_box: Any) -> None:
        """Dynamically transitions the target layout viewport tracking geometries."""
        from reportlab.pdfgen import canvas as reportlab_canvas

        self.page_box = _PageBox(width=new_box.width, height=new_box.height)

        if self.canvas is None:
            self.canvas = reportlab_canvas.Canvas(
                self.packet, pagesize=(self.page_box.width, self.page_box.height)
            )
        else:
            if self._has_content:
                self.canvas.showPage()
                self._has_content = False
            self.canvas.setPageSize((self.page_box.width, self.page_box.height))

    def _register_external_font(self, font_name: str, target_path: str) -> str:
        """Helper to register an absolute TTF path into ReportLab."""
        internal_name = os.path.splitext(os.path.basename(target_path))[0]
        try:
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont, TTFError

            # Use the lazy initialization safeguard to avoid crashing on check calls
            if self.canvas is None:
                self.reset_page_box(self.page_box)

            pdfmetrics.registerFont(TTFont(internal_name, target_path))
            self.font_cache[font_name] = internal_name
            return internal_name
        except (OSError, ValueError, RuntimeError, TypeError, TTFError) as e:
            logger.warning(
                "Failed to register resolved font '%s': %s. Falling back to default font",
                target_path,
                e,
            )
            self.font_cache[font_name] = DEFAULT_FONT_NAME
            return DEFAULT_FONT_NAME

    def get_font_name(self, font_name: str) -> str:
        """Validates a font name or file path against reportlab's registry."""
        if not font_name:
            return DEFAULT_FONT_NAME

        if font_name in self.font_cache:
            return self.font_cache[font_name]

        lower_name = font_name.lower()
        if lower_name in _FONT_NAME_MAP:
            self.font_cache[font_name] = _FONT_NAME_MAP[lower_name]
            return self.font_cache[font_name]

        # Check if the string is a literal file path
        if font_name.lower().endswith((".ttf", ".otf")) and os.path.isfile(font_name):
            return self._register_external_font(font_name, font_name)

        # Attempt OS-level system resolution
        target_path = resolve_system_font_path(font_name)
        if target_path:
            return self._register_external_font(font_name, target_path)

        # Fallback to pure ReportLab lookup
        from reportlab.pdfbase.pdfmetrics import FontError, FontNotFoundError, getFont

        try:
            getFont(font_name)
            self.font_cache[font_name] = font_name
            return font_name
        except (FontError, FontNotFoundError, KeyError, AttributeError):
            logger.warning(
                "Could not resolve font '%s'. Falling back to %s.", font_name, DEFAULT_FONT_NAME
            )
            self.font_cache[font_name] = DEFAULT_FONT_NAME
            return DEFAULT_FONT_NAME

    def _draw_background(
        self,
        rule: dict,
        draw_x: float,
        draw_y: float,
        text_width: float,
        font_name: str,
        font_size: float,
    ):
        """Calculates and draws the background rectangle for text if required."""
        bgcolor = rule.get("bgcolor")
        if not bgcolor and "padding" not in rule:
            return

        if bgcolor is None:
            bgcolor = [1, 1, 1]

        padding = _resolve_dimension(rule.get("padding", 0), self.page_box.width)
        from reportlab.pdfbase.pdfmetrics import getAscentDescent

        try:
            ascent, descent = getAscentDescent(font_name, font_size)
        except KeyError:
            ascent, descent = font_size, 0.0

        bg_x = draw_x - padding
        bg_y = draw_y + descent - padding
        bg_w = text_width + (padding * 2)
        bg_h = (ascent - descent) + (padding * 2)

        try:
            self.canvas.setFillColorRGB(*bgcolor[:3])
            if len(bgcolor) == 4:
                self.canvas.setFillAlpha(float(bgcolor[3]))
            self.canvas.rect(bg_x, bg_y, bg_w, bg_h, fill=1, stroke=0)
            self.canvas.setFillAlpha(1.0)
        except (TypeError, ValueError, IndexError) as e:
            logger.warning("Failed to draw background color due to invalid format: %s", e)

    def draw_rule(self, rule: dict, context: dict):
        """Draws a single text rule onto the internal canvas."""
        try:
            runs = rule["text"](context)
        except (TypeError, KeyError, ValueError) as e:
            logger.warning("Skipping one text rule due to parsing error: %s", e)
            return

        if not runs:
            return

        if self.canvas is None:
            self.reset_page_box(self.page_box)

        self._has_content = True

        font_name = self.get_font_name(rule.get("font", DEFAULT_FONT_NAME))
        font_size = float(rule.get("size", DEFAULT_FONT_SIZE))
        color = rule.get("color", DEFAULT_COLOR_TUPLE)
        link_color = rule.get("linkcolor", color)
        rotate = float(rule.get("rotate", 0))

        full_text = "".join(str(text) for text, _ in runs)
        try:
            text_width = self.canvas.stringWidth(full_text, font_name, font_size)
        except (KeyError, TypeError, ValueError) as e:
            logger.warning("Failed to calculate text width for rule: %s", e)
            return

        pos = rule.get("position", "")
        draw_x, draw_y = self._get_draw_position(rule.get("align"), pos, text_width, font_size)

        anchor_x, anchor_y = _get_base_coordinates(rule, self.page_box)
        offset_x = _resolve_dimension(rule.get("offset-x"), self.page_box.width)
        offset_y = _resolve_dimension(rule.get("offset-y"), self.page_box.height)

        self.canvas.saveState()
        self.canvas.setFont(font_name, font_size)
        self.canvas.translate(anchor_x + offset_x, anchor_y + offset_y)
        self.canvas.rotate(rotate)

        self._draw_background(rule, draw_x, draw_y, text_width, font_name, font_size)

        cursor_x = draw_x
        for text, url in runs:
            cursor_x += _draw_run(
                self.canvas,
                str(text),
                url,
                cursor_x,
                draw_y,
                font_name,
                font_size,
                color,
                link_color,
            )

        self.canvas.restoreState()

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
        if self.canvas is None:
            return b""
        self.canvas.save()
        self.packet.seek(0)
        return self.packet.read()


# --- Drawing Helpers ---


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

    try:
        canvas.setFillColorRGB(*run_color[:3])
        if len(run_color) == 4:
            canvas.setFillAlpha(float(run_color[3]))
        else:
            canvas.setFillAlpha(1.0)
    except (TypeError, ValueError, IndexError) as e:
        logger.warning("Invalid text color format: %s", e)

    canvas.drawString(x, y, text)

    if url:
        from reportlab.pdfbase.pdfmetrics import getAscentDescent

        try:
            w = canvas.stringWidth(text, font_name, font_size)
            ascent, descent = getAscentDescent(font_name, font_size)
            rect = _transform_rect(canvas, x, y + descent, x + w, y + ascent)
            canvas.linkURL(url, rect, relative=0, thickness=0)
        except (KeyError, ValueError, TypeError) as e:
            logger.warning("Failed to render URL annotation: %s", e)

    try:
        return canvas.stringWidth(text, font_name, font_size)
    except (KeyError, ValueError, TypeError):
        return 0.0
