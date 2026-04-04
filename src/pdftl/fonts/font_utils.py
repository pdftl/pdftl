# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/fonts/font_utils.py

# Copyright (c) 2025 The pdftl developers

# Much of this file is from font_utils.py in the swapfont
# project, available at https://github.com/qooxzuub/swapfont
# under the MPL-2.0, Copyright (c) 2025 qooxzuub

"""Font utilities"""

import logging
from typing import Any, cast

logger = logging.getLogger(__name__)


class FontWrapper:
    """
    Wrapper around fontTools.TTFont to provide simplified metric lookups.
    """

    def __init__(self, font_path: str):
        from fontTools.ttLib import TTFont  # type: ignore

        self.path = font_path
        self.ttfont = TTFont(font_path)
        self.cmap = self.ttfont.getBestCmap()
        self.hmtx = self.ttfont["hmtx"]
        self.head = self.ttfont["head"]

        # Dynamically determine the name of the missing glyph (GID 0)
        # Spec guarantees GID 0 exists, but doesn't guarantee it's named ".notdef"
        self.fallback_glyph_name = self.ttfont.getGlyphOrder()[0]

    @property
    def units_per_em(self) -> int:
        """Returns the unitsPerEm value from the head table."""
        return cast(Any, self.head).unitsPerEm

    @property
    def scale_factor(self) -> float:
        """
        Returns the scaling factor to convert font units to PDF units.
        PDF text space is usually normalized to 1000 units per em.
        """
        if self.units_per_em:
            return 1000.0 / self.units_per_em
        return 1.0

    def get_char_width(self, char: str) -> float:
        """
        Returns the width of a unicode character in PDF units (1/1000th of font size).
        """
        if not char:
            return 0.0

        # Get Glyph Name from Unicode
        ord_val = ord(char)
        glyph_name = self.cmap.get(ord_val) if self.cmap else None

        if not glyph_name:
            logger.warning(
                "Character '%s' (U+%04X) not found in %s. Using %s",
                char,
                ord_val,
                self.path,
                self.fallback_glyph_name,
            )
            glyph_name = self.fallback_glyph_name

        try:
            # Get Width from hmtx table
            raw_width, _ = self.hmtx[glyph_name]

            # Normalize to 1000 units per em using the property
            return raw_width * self.scale_factor

        except KeyError:
            logger.error("Metric lookup failed for glyph: %s", glyph_name)
            return 600.0  # Safe default width

    def close(self):
        """Closes the underlying TTFont resource."""
        self.ttfont.close()
