# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/fonts/font_embedding.py

# Copyright (c) 2025 The pdftl developers

# Much of this file is from font_embedding.py in the swapfont
# project, available at https://github.com/qooxzuub/swapfont
# under the MPL-2.0, Copyright (c) 2025 qooxzuub

"""Font embedding"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pikepdf


logger = logging.getLogger(__name__)


@dataclass
class FontMetricsData:  # pylint: disable=too-many-instance-attributes
    """Dataclass for font metrics"""

    ascent: float = 0
    bbox: list = field(default_factory=list)
    cap_height: float = 0
    descent: float = 0
    flags: int = 0
    italic_angle: float = 0
    ps_name: str = ""
    scale: float = 1
    units_per_em: int = 1000


def embed_truetype_font(
    pdf: "pikepdf.Pdf",
    font_path: str,
    custom_encoding_map: dict[int, str] | None = None,
) -> "pikepdf.Object":
    """
    Embeds a TrueType font into the PDF document and returns the Font Object.

    This implementation creates a "Simple Font" (Type /TrueType) with
    /WinAnsiEncoding. This supports standard Western text.

    Args:
        pdf: The target pikepdf document (required to create streams).
        font_path: Filesystem path to the .ttf file.
        custom_encoding_map: Optional dict mapping {slot_index: unicode_char}.
                             Used to insert correct widths for remapped slots
                             (e.g. {128: 'fi', 129: 'Pi'}).

    Returns:
        A pikepdf.Object representing the /Font dictionary.
    """
    import pikepdf

    path = Path(font_path)
    if not path.exists():
        raise FileNotFoundError(f"Font file not found: {font_path}")

    from fontTools.ttLib import TTFont, TTLibError  # type: ignore

    logging.getLogger("fontTools.ttLib").setLevel(logging.ERROR)

    try:
        tt = TTFont(path)
    except TTLibError as e:
        logger.error("Could not parse font %s: %s", font_path, e)
        raise

    # 1. Extract Metrics from TTF Tables
    metrics = _extract_ttf_metrics(tt)

    # 2. Create the Widths Array
    # Pass custom mapping so we generate correct widths for overridden slots
    widths = _widths_array(tt, metrics, custom_encoding_map)

    # 3. Create FontFile2 Stream (The binary font data)
    with open(path, "rb") as f:
        font_data = f.read()

    font_stream = pdf.make_stream(font_data)
    font_stream.Length1 = len(font_data)

    # 4. Create FontDescriptor Dictionary

    font_descriptor = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/FontDescriptor"),
                "/FontName": pikepdf.Name(f"/{metrics.ps_name}"),
                "/Flags": metrics.flags,
                "/FontBBox": [float(x) for x in metrics.bbox],
                "/ItalicAngle": float(metrics.italic_angle),
                "/Ascent": float(metrics.ascent),
                "/Descent": float(metrics.descent),
                "/CapHeight": float(metrics.cap_height),
                "/StemV": 80,  # Standard approximation
                "/FontFile2": font_stream,
            }
        )
    )

    # 5. Create the Font Dictionary
    font_obj = pdf.make_indirect(
        pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Font"),
                "/Subtype": pikepdf.Name("/TrueType"),
                "/BaseFont": pikepdf.Name(f"/{metrics.ps_name}"),
                "/FirstChar": 0,
                "/LastChar": 255,
                "/Widths": widths,
                "/Encoding": pikepdf.Name("/WinAnsiEncoding"),
                "/FontDescriptor": font_descriptor,
            }
        )
    )

    logger.info("Embedded TrueType font: %s as %s", path.name, metrics.ps_name)
    return font_obj


def _extract_ttf_metrics(tt):
    head = tt["head"]
    hhea = tt["hhea"]
    os2 = tt["OS/2"]
    post = tt["post"]
    name_table = tt["name"]

    # Get PostScript name from 'name' table (ID 6)
    ps_name = name_table.getDebugName(6)
    if not ps_name:
        ps_name = "EmbeddedFont"

    # Sanitize name
    ps_name = ps_name.replace(" ", "")

    units_per_em = head.unitsPerEm
    scale = 1000.0 / units_per_em

    ascent = hhea.ascent * scale
    descent = hhea.descent * scale

    if hasattr(os2, "sCapHeight"):
        cap_height = os2.sCapHeight * scale
    else:
        cap_height = ascent

    italic_angle = post.italicAngle
    flags = 32
    if post.isFixedPitch:
        flags |= 1

    bbox = [head.xMin * scale, head.yMin * scale, head.xMax * scale, head.yMax * scale]

    return FontMetricsData(
        ascent=ascent,
        bbox=bbox,
        cap_height=cap_height,
        descent=descent,
        flags=flags,
        italic_angle=italic_angle,
        ps_name=ps_name,
        scale=scale,
        units_per_em=units_per_em,
    )


def _get_glyph_width(tt, metrics, gname):
    """Safely looks up glyph width from hmtx table."""
    if gname and gname in tt["hmtx"].metrics:
        return tt["hmtx"][gname][0] * metrics.scale
    return 0.0


def _calculate_slot_width(
    i: int,
    custom_map: dict[int, str] | None,
    cmap,
    tt,
    metrics,
    default_width: float,
) -> float:
    # pylint: disable=too-many-arguments, too-many-positional-arguments
    """Calculates the width for a specific character slot (0-255)."""
    width = 0.0

    # 1. Check for Custom Mapping Override
    if custom_map and i in custom_map:
        char_str = custom_map[i]
        # Look up glyph name for this custom char
        if len(char_str) == 1:
            gname = cmap.get(ord(char_str))
            if gname:
                width = _get_glyph_width(tt, metrics, gname)
            else:
                logger.warning(
                    "Custom char '%s' (slot %s) not found in font cmap.",
                    char_str,
                    i,
                )
                width = default_width
        else:
            # Fallback for complex mappings
            width = default_width

    # 2. Standard Lookup (if no override found or width still 0)
    if width == 0.0:
        # Heuristic: look up unicode code point directly for ASCII
        gname = cmap.get(i)
        if gname:
            width = _get_glyph_width(tt, metrics, gname)
        else:
            width = default_width

    return width


def _widths_array(tt, metrics, custom_map: dict[int, str] | None = None):
    """
    Return the widths array for a Simple Font (0..255).
    """
    widths = []
    cmap = tt.getBestCmap()
    default_width = _get_glyph_width(tt, metrics, ".notdef") or 600.0

    for i in range(0, 256):
        width = _calculate_slot_width(i, custom_map, cmap, tt, metrics, default_width)
        widths.append(width)

    return widths
