# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/fonts/cmap_utils.py

"""
Utilities for parsing and compiling /ToUnicode CMap streams in PDFs.

Provides stateless, robust parsing of CMap files to flat JSON dictionaries
and optimized compilation of flat mapping dictionaries back to PostScript CMaps.

Also provides detection of a Type0 font's predefined /Encoding CMap (as
distinct from /ToUnicode, which maps codes to Unicode, and /CIDToGIDMap in
widths_utils.py, which maps CIDs to TrueType GIDs). /Encoding determines how
raw character codes in content streams are split into CIDs in the first
place. When it names one of the two predefined identity CMaps, that fact is
useful to preserve on export even when no /ToUnicode is present to resolve a
human-readable meaning for those CIDs, since it tells any downstream
consumer that CID equals code and no further CMap resolution is possible or
necessary.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# The two predefined identity CMaps a Type0 font's /Encoding may name
# directly (ISO 32000-2, 9.7.5.2). Under either, a character code maps to a
# CID of the same numeric value; -H and -V differ only in writing mode
# (horizontal vs vertical), not in the code-to-CID mapping itself.
_PREDEFINED_IDENTITY_CMAPS = frozenset({"Identity-H", "Identity-V"})


def _parse_hex(hex_str: str) -> str:
    """Converts a PDF hex string (e.g. '0041' or '00660066') into a Python unicode string."""
    clean = re.sub(r"[^a-fA-F0-9]", "", hex_str)
    if len(clean) % 4 != 0:
        return ""
    try:
        # Decode 16-bit big-endian characters (UTF-16BE) with strict error handling
        # to prevent silent introduction of corrupted replacement characters (\ufffd).
        return bytes.fromhex(clean).decode("utf-16-be", errors="strict")
    except (UnicodeDecodeError, ValueError) as e:
        logger.warning("Skipping corrupted UTF-16BE hex mapping %r: %s", hex_str, e)
        return ""


def _to_hex_str(char_str: str) -> str:
    """Encodes a Python unicode string to a big-endian UTF-16 hex string (e.g. '0041')."""
    return char_str.encode("utf-16-be").hex().upper()


def detect_predefined_identity_encoding(font_obj: Any) -> str | None:
    """
    Detects whether a Type0 font's /Encoding names one of the predefined
    identity CMaps, /Identity-H or /Identity-V.

    Returns "Identity-H" or "Identity-V" if so, or None if `font_obj` is not
    a Type0 font, has no /Encoding, or /Encoding is something else (a
    different predefined CMap name, or an embedded CMap program Stream,
    neither of which this helper attempts to resolve).

    This is a distinct concern from /ToUnicode (semantic Unicode mapping)
    and /CIDToGIDMap (CID-to-GID mapping, handled in widths_utils.py):
    /Encoding governs how raw content-stream byte codes are split into CIDs
    in the first place. Recording this on export lets the manifest stay
    meaningful for a font that has neither /ToUnicode nor a non-default
    /W array to populate its mappings from.
    """
    if str(font_obj.get("/Subtype", "")) != "/Type0":
        return None
    if "/Encoding" not in font_obj:
        return None

    enc = font_obj["/Encoding"]
    # /Encoding for a Type0 font is either a Name (a predefined CMap, the
    # only case handled here) or a Stream (an embedded CMap program). A
    # Stream's str() representation will never match a known CMap name, so
    # it safely falls through to None without needing a separate type check.
    name = str(enc).lstrip("/")
    return name if name in _PREDEFINED_IDENTITY_CMAPS else None


def parse_to_unicode_cmap(cmap_bytes: bytes) -> dict[str, str]:
    """
    Parses a /ToUnicode CMap stream into a flat dictionary.
    Maps character code (hex string, e.g. "01") -> Unicode value (string).
    """
    mappings: dict[str, str] = {}
    content = cmap_bytes.decode("latin-1", errors="replace")

    # Clean up comments and unify whitespace
    lines = []
    for line in content.splitlines():
        line_clean = re.sub(r"%.*$", "", line).strip()
        if line_clean:
            lines.append(line_clean)

    # Tokenize the entire stream to avoid line-break dependency
    tokens = []
    for line in lines:
        tokens.extend(re.findall(r"<[^>]+>|\[|\]|\w+|[^\s<>\[\]]+", line))

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "beginbfchar":
            i = _parse_bfchar_block(tokens, i + 1, mappings)
        elif token == "beginbfrange":
            i = _parse_bfrange_block(tokens, i + 1, mappings)
        else:
            i += 1

    return mappings


def _try_parse_bfchar_item(src_token: str, dst_token: str, mappings: dict[str, str]) -> None:
    """Parses a single source-to-destination hex character map pair if valid."""
    if not (src_token.startswith("<") and src_token.endswith(">")):
        return
    if not (dst_token.startswith("<") and dst_token.endswith(">")):
        return
    src_hex = src_token[1:-1].upper()
    try:
        int(src_hex, 16)
        val = _parse_hex(dst_token)
        if val:  # Only assign if decoding was successful and non-empty
            mappings[src_hex] = val
    except ValueError:
        pass


def _parse_bfchar_block(tokens: list[str], start_idx: int, mappings: dict[str, str]) -> int:
    idx = start_idx
    while idx < len(tokens):
        token = tokens[idx]
        if token == "endbfchar":
            return idx + 1
        if token.startswith("<") and token.endswith(">") and idx + 1 < len(tokens):
            _try_parse_bfchar_item(token, tokens[idx + 1], mappings)
            idx += 2
        else:
            idx += 1
    return idx


def _parse_bfrange_block(tokens: list[str], start_idx: int, mappings: dict[str, str]) -> int:
    idx = start_idx
    while idx < len(tokens):
        token = tokens[idx]
        if token == "endbfrange":
            return idx + 1

        if token.startswith("<") and token.endswith(">") and idx + 2 < len(tokens):
            src_start_hex = token[1:-1].upper()
            src_end_hex = tokens[idx + 1][1:-1].upper()
            dst_token = tokens[idx + 2]

            try:
                start_code = int(src_start_hex, 16)
                end_code = int(src_end_hex, 16)
                code_len = len(src_start_hex)
            except ValueError:
                idx += 1
                continue

            if dst_token == "[":
                idx = _parse_bfrange_array(
                    tokens, idx + 3, start_code, end_code, code_len, mappings
                )
            elif dst_token.startswith("<") and dst_token.endswith(">"):
                _parse_bfrange_sequential(
                    src_start_hex, dst_token[1:-1], start_code, end_code, code_len, mappings
                )
                idx += 3
            else:
                idx += 1
        else:
            idx += 1
    return idx


def _parse_bfrange_array(
    tokens: list[str],
    start_idx: int,
    start_code: int,
    end_code: int,
    code_len: int,
    mappings: dict[str, str],
) -> int:
    idx = start_idx
    curr_code = start_code
    while idx < len(tokens):
        token = tokens[idx]
        if token == "]":
            return idx + 1
        if token.startswith("<") and token.endswith(">"):
            if curr_code <= end_code:
                key_hex = f"{curr_code:0{code_len}X}"
                val = _parse_hex(token)
                if val:
                    mappings[key_hex] = val
                curr_code += 1
            idx += 1
        else:
            idx += 1
    return idx


def _parse_bfrange_sequential(
    src_start_hex: str,
    dst_start_hex: str,
    start_code: int,
    end_code: int,
    code_len: int,
    mappings: dict[str, str],
) -> None:
    try:
        dst_start_code = int(dst_start_hex, 16)
        dst_len = len(dst_start_hex)
    except ValueError:
        return

    for code in range(start_code, end_code + 1):
        offset = code - start_code
        key_hex = f"{code:0{code_len}X}"
        val_hex = f"{(dst_start_code + offset):0{dst_len}X}"
        val = _parse_hex(val_hex)
        if val:
            mappings[key_hex] = val


def compile_to_unicode_cmap(mappings: dict[str, str], name: str = "Custom") -> bytes:
    """
    Compiles a flat mapping dictionary back to an optimized /ToUnicode PostScript CMap.
    Automatically identifies and compresses sequential runs into `beginbfrange` blocks.
    """
    if not mappings:
        return b""

    # Sort keys numerically to easily detect contiguous ranges
    def _sort_key(k: str) -> tuple[int, int]:
        return len(k), int(k, 16)

    sorted_keys = sorted(mappings.keys(), key=_sort_key)
    code_len = len(sorted_keys[0])

    bfchar_entries: list[tuple[str, str]] = []
    bfrange_entries: list[tuple[str, str, str]] = []

    # Detect sequential runs
    idx = 0
    while idx < len(sorted_keys):
        start_key = sorted_keys[idx]
        start_code = int(start_key, 16)
        start_val = mappings[start_key]

        run_len = 1
        while idx + run_len < len(sorted_keys):
            next_key = sorted_keys[idx + run_len]
            next_code = int(next_key, 16)
            next_val = mappings[next_key]

            if (
                next_code == start_code + run_len
                and len(start_val) == 1
                and len(next_val) == 1
                and ord(next_val) == ord(start_val) + run_len
            ):
                run_len += 1
            else:
                break

        if run_len >= 3:
            end_key = sorted_keys[idx + run_len - 1]
            bfrange_entries.append((start_key, end_key, _to_hex_str(start_val)))
            idx += run_len
        else:
            for offset in range(run_len):
                k = sorted_keys[idx + offset]
                bfchar_entries.append((k, _to_hex_str(mappings[k])))
            idx += run_len

    lines = [
        "/CIDInit /ProcSet findresource begin",
        "12 dict begin",
        "begincmap",
        "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def",
        f"/CMapName /ToUnicode-{name} def",
        "/CMapType 2 def",
        "1 begincodespacerange",
        f"  <{0:0{code_len}X}> <{'F' * code_len}>",
        "endcodespacerange",
    ]

    _write_bfchar_chunks(bfchar_entries, lines)
    _write_bfrange_chunks(bfrange_entries, lines)

    lines.extend(
        [
            "endcmap",
            "CMapName currentdict /CMap defineresource pop",
            "end",
            "end",
        ]
    )

    return "\n".join(lines).encode("latin-1")


def _write_bfchar_chunks(entries: list[tuple[str, str]], lines: list[str]) -> None:
    for i in range(0, len(entries), 100):
        chunk = entries[i : i + 100]
        lines.append(f"{len(chunk)} beginbfchar")
        for k, v in chunk:
            lines.append(f"  <{k}> <{v}>")
        lines.append("endbfchar")


def _write_bfrange_chunks(entries: list[tuple[str, str, str]], lines: list[str]) -> None:
    for i in range(0, len(entries), 100):
        chunk = entries[i : i + 100]
        lines.append(f"{len(chunk)} beginbfrange")
        for start, end, target in chunk:
            lines.append(f"  <{start}> <{end}> <{target}>")
        lines.append("endbfrange")
