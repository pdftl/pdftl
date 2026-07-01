# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/helpers/font_ops_shared.py

"""
Small shared helpers used by both the font export and font import logic.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from pdftl.utils.page_specs import page_numbers_matching_page_specs

logger = logging.getLogger(__name__)


def file_hash(filepath: Path) -> str:
    """Calculates the MD5 hash of a file to track modifications."""
    hasher = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            buf = f.read()
            hasher.update(buf)
    except OSError as exc:
        logger.debug("Failed to calculate hash for %s: %s", filepath, exc)
        return ""
    return hasher.hexdigest()


def sanitize_name(name: str) -> str:
    """Cleans up a PostScript font name to form a safe, readable filename."""
    return re.sub(r"[^a-zA-Z0-9_\-\+\.]", "", name)


def get_target_pages(pdf, page_specs: list[str]) -> list[int]:
    """Resolves target page numbers from raw page specifications."""
    num_pages = len(pdf.pages)
    if page_specs:
        return sorted(list(page_numbers_matching_page_specs(page_specs, num_pages)))
    return list(range(1, num_pages + 1))


_FLAG_BITS = {
    "FixedPitch": 1,
    "Serif": 2,
    "Symbolic": 3,
    "Script": 4,
    "Nonsymbolic": 6,
    "Italic": 7,
    "AllCap": 17,
    "SmallCap": 18,
    "ForceBold": 19,
}


def decode_font_flags(flags_int: int) -> dict[str, bool]:
    """Decodes a 32-bit PDF font Flags integer into a friendly boolean dict."""
    return {name: bool(flags_int & (1 << (bit - 1))) for name, bit in _FLAG_BITS.items()}


def encode_font_flags(flags_dict: dict[str, bool]) -> int:
    """Encodes a friendly boolean dict into a 32-bit PDF font Flags integer."""
    flags_int = 0
    symbolic = flags_dict.get("Symbolic", False)
    nonsymbolic = flags_dict.get("Nonsymbolic", not symbolic)

    if symbolic == nonsymbolic:
        nonsymbolic = not symbolic

    resolved_flags = dict(flags_dict)
    resolved_flags["Symbolic"] = symbolic
    resolved_flags["Nonsymbolic"] = nonsymbolic

    for name, bit in _FLAG_BITS.items():
        if resolved_flags.get(name, False):
            flags_int |= 1 << (bit - 1)
    return flags_int
