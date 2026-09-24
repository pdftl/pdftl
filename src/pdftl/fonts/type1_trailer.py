# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/fonts/type1_trailer.py

"""
Detects a PDF-embedded Type 1 (/FontFile) program whose fixed-content
trailer was omitted, and completes it before parsing.

ISO 32000-2:2020 9.9.1 Table 125: /Length3 0 lets a producer drop the 512
zeros + `cleartomark` that follow the eexec-encrypted section, and the
reader is expected to supply them. LaTeX/TeX-produced PDFs routinely do
this. fontTools.t1Lib locates the end of the encrypted section by scanning
for exactly that zero run (`t1Lib.EEXECEND`, 512 zeros with arbitrary
whitespace between them; `cleartomark` itself is not required), and fails
with "can't find end of eexec part" without it.

The decision is made from the bytes, not by parsing and retrying on that
error: one parse instead of two for the common trailerless case, and a
program that has a trailer but is corrupt elsewhere is never retried.

Shared by type1_binary_utils.py (width read/patch) and type1_to_cff.py
(subsetting) so both complete exactly the same programs.
"""

from __future__ import annotations

_EEXEC_MARKER = b"currentfile eexec"
# The whitespace set t1Lib.EEXECEND tolerates between zeros.
_WHITESPACE = b" \t\r\n"
_ZERO_RUN = b"0" * 512
_ZERO_BLOCK = b"0" * 16

# t1Lib's EEXECEND scan is quadratic in the length of each short (<512)
# zero run it has to try and abandon, so a crafted program made of many
# long-but-short-of-512 runs is refused before fontTools ever sees it.
# Counted in 16-zero blocks outside whole 512-zero runs: real ciphertext
# has essentially none (16 ASCII zeros in a row is ~2^-64 likely), and a
# complete trailer contributes none. Some real fonts ship a short trailer
# (448 zeros, 28 blocks, is the most seen across 7,491 real programs).
MAX_SHORT_ZERO_BLOCKS = 64

# No leading separator: fontTools matches the zero run wherever it starts,
# so a program truncated partway into its own zero run is completed
# seamlessly.
TRAILER = (b"0" * 64 + b"\n") * 8 + b"cleartomark\n"


class ZeroRunTooIrregular(Exception):
    """The post-eexec section holds more short zero runs than any real
    encrypted section or trailer does. Fixed, disclosure-free message."""


def needs_completion(raw: bytes) -> bool:
    """
    True if `raw` is a plain-form Type 1 program with no full 512-zero run
    after `currentfile eexec` (the /Length3 0 shape, or a trailer truncated
    partway through).

    False for a PFB-segmented program (0x80 first byte: segment headers
    bound the encrypted section, nothing to complete) and for a program
    with no `currentfile eexec` marker at all.

    Raises ZeroRunTooIrregular (see MAX_SHORT_ZERO_BLOCKS); callers treat
    that as an unparseable font.
    """
    if raw[:1] == b"\x80":
        return False
    marker = raw.find(_EEXEC_MARKER)
    if marker < 0:
        return False
    # +1 skips the single separator byte after the marker, as t1Lib does.
    section = raw[marker + len(_EEXEC_MARKER) + 1 :].translate(None, _WHITESPACE)
    blocks_per_run = len(_ZERO_RUN) // len(_ZERO_BLOCK)
    short_blocks = section.count(_ZERO_BLOCK) - blocks_per_run * section.count(_ZERO_RUN)
    if short_blocks > MAX_SHORT_ZERO_BLOCKS:
        raise ZeroRunTooIrregular(
            "Type 1 program's encrypted section holds zero runs no encrypted section has"
        )
    return _ZERO_RUN not in section


def completed(raw: bytes) -> bytes:
    """`raw` with the fixed trailer appended."""
    return raw + TRAILER


def completed_if_needed(raw: bytes) -> bytes:
    """`raw`, completed when `needs_completion` says so. Propagates
    ZeroRunTooIrregular."""
    return completed(raw) if needs_completion(raw) else raw
