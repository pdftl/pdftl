# tests/utils/test_icc_profiles.py


from pdftl.utils.icc_profiles import (
    _read_desc_v2,
    _read_mluc,
    extract_icc_profile_name,
)


# ---------------------------------------------------------------------------
# Helpers to build minimal valid ICC byte structures
# ---------------------------------------------------------------------------


def _make_icc(tag_data: bytes, tag_sig: bytes = b"desc") -> bytes:
    """Wrap tag_data in a minimal ICC profile with a single tag table entry."""
    tag_data_offset = 128 + 4 + 12  # header + tag_count + one entry
    tag_table_entry = (
        tag_sig + tag_data_offset.to_bytes(4, "big") + len(tag_data).to_bytes(4, "big")
    )
    return b"\x00" * 128 + (1).to_bytes(4, "big") + tag_table_entry + tag_data


def _desc_v2(name: bytes) -> bytes:
    """Build a textDescriptionType (ICC v2) tag data block."""
    return b"desc" + b"\x00" * 4 + len(name).to_bytes(4, "big") + name


def _mluc(records: list[tuple[bytes, bytes]]) -> bytes:
    """
    Build a multiLocalizedUnicodeType (ICC v4) tag data block.
    records: list of (language_2bytes, utf16be_string_bytes)
    """
    record_count = len(records)
    record_size = 12
    # Each record: lang[2] country[2] length[4] offset[4]
    # String data starts immediately after the record table
    string_area_start = 16 + record_count * 12  # relative to tag_data start
    header = (
        b"mluc"
        + b"\x00" * 4  # reserved
        + record_count.to_bytes(4, "big")
        + record_size.to_bytes(4, "big")
    )
    record_table = b""
    string_area = b""
    offset = string_area_start
    for lang, utf16 in records:
        country = b"\x00\x00"
        record_table += lang + country + len(utf16).to_bytes(4, "big") + offset.to_bytes(4, "big")
        string_area += utf16
        offset += len(utf16)
    return header + record_table + string_area


# ---------------------------------------------------------------------------
# extract_icc_profile_name
# ---------------------------------------------------------------------------


def test_too_short():
    assert extract_icc_profile_name(b"\x00" * 131) == ""


def test_no_desc_tag():
    # Single tag with a non-desc signature — should return ""
    tag_data = b"\x00" * 20
    raw = _make_icc(tag_data, tag_sig=b"cprt")
    assert extract_icc_profile_name(raw) == ""


def test_desc_v2_via_extract():
    tag_data = _desc_v2(b"sRGB\x00")
    assert extract_icc_profile_name(_make_icc(tag_data)) == "sRGB"


def test_desc_mluc_via_extract():
    utf16 = "sRGB IEC61966-2.1".encode("utf-16-be")
    tag_data = _mluc([(b"en", utf16)])
    assert extract_icc_profile_name(_make_icc(tag_data)) == "sRGB IEC61966-2.1"


def test_unknown_type_sig_returns_empty():
    # tag table points to data with unrecognised type signature
    tag_data = b"unkn" + b"\x00" * 16
    assert extract_icc_profile_name(_make_icc(tag_data)) == ""


def test_data_offset_out_of_bounds():
    # Craft a tag table entry whose data_offset + data_size exceeds file length
    tag_data_offset = 128 + 4 + 12
    tag_table_entry = (
        b"desc"
        + (tag_data_offset + 9999).to_bytes(4, "big")  # offset way past EOF
        + (20).to_bytes(4, "big")
    )
    raw = b"\x00" * 128 + (1).to_bytes(4, "big") + tag_table_entry + b"\x00" * 20
    assert extract_icc_profile_name(raw) == ""


def test_data_size_too_small():
    # data_size < 8 triggers the guard
    tag_data_offset = 128 + 4 + 12
    tag_table_entry = (
        b"desc" + tag_data_offset.to_bytes(4, "big") + (4).to_bytes(4, "big")  # too small
    )
    raw = b"\x00" * 128 + (1).to_bytes(4, "big") + tag_table_entry + b"\x00" * 20
    assert extract_icc_profile_name(raw) == ""


def test_truncated_tag_table():
    # tag_count claims 5 entries but there's only room for 1 — should not raise
    tag_data = _desc_v2(b"Test\x00")
    tag_data_offset = 128 + 4 + 12
    tag_table_entry = (
        b"desc" + tag_data_offset.to_bytes(4, "big") + len(tag_data).to_bytes(4, "big")
    )
    raw = b"\x00" * 128 + (5).to_bytes(4, "big") + tag_table_entry + tag_data
    assert extract_icc_profile_name(raw) == "Test"


# ---------------------------------------------------------------------------
# _read_desc_v2
# ---------------------------------------------------------------------------


def test_desc_v2_normal():
    assert _read_desc_v2(_desc_v2(b"Adobe RGB (1998)\x00")) == "Adobe RGB (1998)"


def test_desc_v2_no_null_terminator():
    assert _read_desc_v2(_desc_v2(b"ProPhoto")) == "ProPhoto"


def test_desc_v2_too_short():
    assert _read_desc_v2(b"\x00" * 11) == ""


def test_desc_v2_zero_length():
    tag_data = b"desc" + b"\x00" * 4 + (0).to_bytes(4, "big")
    assert _read_desc_v2(tag_data) == ""


def test_desc_v2_length_exceeds_data():
    tag_data = b"desc" + b"\x00" * 4 + (999).to_bytes(4, "big") + b"hi"
    assert _read_desc_v2(tag_data) == ""


# ---------------------------------------------------------------------------
# _read_mluc
# ---------------------------------------------------------------------------


def test_mluc_single_english_record():
    utf16 = "sRGB".encode("utf-16-be")
    assert _read_mluc(_mluc([(b"en", utf16)])) == "sRGB"


def test_mluc_prefers_english_over_first():
    de_utf16 = "sRGB Deutsch".encode("utf-16-be")
    en_utf16 = "sRGB English".encode("utf-16-be")
    assert _read_mluc(_mluc([(b"de", de_utf16), (b"en", en_utf16)])) == "sRGB English"


def test_mluc_falls_back_to_first_when_no_english():
    fr_utf16 = "sRGB Français".encode("utf-16-be")
    ja_utf16 = "sRGB 日本語".encode("utf-16-be")
    result = _read_mluc(_mluc([(b"fr", fr_utf16), (b"ja", ja_utf16)]))
    assert result == "sRGB Français"


def test_mluc_too_short():
    assert _read_mluc(b"\x00" * 19) == ""


def test_mluc_record_offset_out_of_bounds():
    # Build a record whose str_offset points past the end of tag_data
    tag_data = _mluc([(b"en", "Test".encode("utf-16-be"))])
    # Corrupt the str_offset in the first record to point way past the end
    corrupted = bytearray(tag_data)
    corrupted[24:28] = (9999).to_bytes(4, "big")  # str_offset field
    assert _read_mluc(bytes(corrupted)) == ""


def test_mluc_zero_records():
    tag_data = _mluc([])
    assert _read_mluc(tag_data) == ""


def test_tag_table_entry_truncated():
    # tag_count claims 3 entries but raw ends after the first — hits break on line 36
    single_entry = b"cprt" + b"\x00" * 8  # one complete 12-byte non-desc entry
    raw = b"\x00" * 128 + (3).to_bytes(4, "big") + single_entry
    assert extract_icc_profile_name(raw) == ""


def test_mluc_record_table_truncated():
    # record_count claims 5 records but tag_data ends after the first record entry.
    # The first record's str_offset is corrupted to be out of bounds, so it hits
    # the continue on line 81. The second iteration then hits rec_start + 12 >
    # len(tag_data) and breaks with best == "", exercising line 74.
    utf16 = "Test".encode("utf-16-be")
    real = _mluc([(b"en", utf16)])
    corrupted = bytearray(real)
    corrupted[8:12] = (5).to_bytes(4, "big")  # claim 5 records
    corrupted[24:28] = (9999).to_bytes(4, "big")  # corrupt first record's str_offset
    assert _read_mluc(bytes(corrupted)) == ""
