# src/pdftl/utils/icc_profiles.py

"""
Parse ICC profile metadata from raw bytes.

ICC.1:2010 references:
  s7.2   — profile header (128 bytes)
  s7.3   — tag table
  s10.6  — textDescriptionType (v2 'desc')
  s10.13 — multiLocalizedUnicodeType (v4 'mluc')
"""

import logging

logger = logging.getLogger(__name__)


def extract_icc_profile_name(raw: bytes) -> str:
    """
    Return the profile description string from raw ICC profile bytes, or "".

    Handles both ICC v2 (textDescriptionType) and v4 (multiLocalizedUnicodeType)
    desc tags. Returns "" on any parse failure rather than raising.
    """
    if len(raw) < 132:  # 128-byte header + 4-byte tag count minimum
        return ""

    tag_count = int.from_bytes(raw[128:132], "big")
    table_start = 132

    for i in range(tag_count):
        entry_start = table_start + i * 12
        if entry_start + 12 > len(raw):
            break

        tag_sig = raw[entry_start : entry_start + 4]
        if tag_sig != b"desc":
            continue

        data_offset = int.from_bytes(raw[entry_start + 4 : entry_start + 8], "big")
        data_size = int.from_bytes(raw[entry_start + 8 : entry_start + 12], "big")

        if data_offset + data_size > len(raw) or data_size < 8:
            return ""

        tag_data = raw[data_offset : data_offset + data_size]
        type_sig = tag_data[:4]

        if type_sig == b"mluc":
            return _read_mluc(tag_data)
        elif type_sig == b"desc":
            return _read_desc_v2(tag_data)
        else:
            return ""

    return ""


def _read_mluc(tag_data: bytes) -> str:
    """
    Read an ICC v4 multiLocalizedUnicodeType desc tag.

    Prefers the first en/* record; falls back to the first record of any language.
    """
    if len(tag_data) < 20:
        return ""

    record_count = int.from_bytes(tag_data[8:12], "big")
    best: str = ""

    for i in range(record_count):
        rec_start = 16 + i * 12
        if rec_start + 12 > len(tag_data):
            break

        language = tag_data[rec_start : rec_start + 2]
        str_length = int.from_bytes(tag_data[rec_start + 4 : rec_start + 8], "big")
        str_offset = int.from_bytes(tag_data[rec_start + 8 : rec_start + 12], "big")

        if str_offset + str_length > len(tag_data):
            continue

        name = (
            tag_data[str_offset : str_offset + str_length]
            .decode("utf-16-be", errors="ignore")
            .strip()
        )

        if not best:
            best = name
        if language == b"en":
            return name

    return best


def _read_desc_v2(tag_data: bytes) -> str:
    """
    Read an ICC v2 textDescriptionType desc tag (7-bit ASCII invariant subset).
    """
    if len(tag_data) < 12:
        return ""

    str_length = int.from_bytes(tag_data[8:12], "big")
    if str_length == 0 or 12 + str_length > len(tag_data):
        return ""

    candidate = tag_data[12 : 12 + str_length]
    null_idx = candidate.find(b"\x00")
    if null_idx != -1:
        candidate = candidate[:null_idx]

    return candidate.decode("ascii", errors="ignore").strip()
