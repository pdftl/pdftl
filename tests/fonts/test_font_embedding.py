from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.fonts.font_embedding import (
    FontMetricsData,
    _extract_ttf_metrics,
    _get_glyph_width,
    _widths_array,
    embed_truetype_font,
)


def test_fontmetrics_dataclass():
    fm = FontMetricsData(ascent=10, bbox=[0, 0, 100, 100])
    assert fm.ascent == 10
    assert fm.bbox == [0, 0, 100, 100]


def test_extract_ttf_metrics(monkeypatch):
    tt = MagicMock()
    tt.__getitem__.side_effect = lambda key: (
        MagicMock(ascent=100, descent=-20)
        if key == "hhea"
        else (
            MagicMock(unitsPerEm=1000)
            if key == "head"
            else (
                MagicMock(sCapHeight=80)
                if key == "OS/2"
                else (
                    MagicMock(italicAngle=0, isFixedPitch=False)
                    if key == "post"
                    else MagicMock(getDebugName=lambda x: "TestFont")
                )
            )
        )
    )
    metrics = _extract_ttf_metrics(tt)
    assert metrics.ps_name == "TestFont"
    assert metrics.ascent > 0


def test_embed_truetype_font_file_not_found(tmp_path):
    pdf_mock = MagicMock()
    fake_path = tmp_path / "nofont.ttf"
    with pytest.raises(FileNotFoundError):
        embed_truetype_font(pdf_mock, str(fake_path))


def test_widths_array(monkeypatch):
    # 1. Create the main TTFont mock
    tt = MagicMock()
    tt.getBestCmap.return_value = {65: "A"}  # ASCII A

    # 2. Create a specific mock for the 'hmtx' table
    hmtx_table = MagicMock()
    hmtx_table.metrics = {"A": (500, 0), ".notdef": (600, 0)}
    hmtx_table.__getitem__.side_effect = lambda key: hmtx_table.metrics[key]

    # 3. Configure tt['hmtx'] to return our table mock
    # Using side_effect to handle specific key lookup safely
    def getitem_side_effect(key):
        if key == "hmtx":
            return hmtx_table
        return MagicMock()

    tt.__getitem__.side_effect = getitem_side_effect

    metrics = MagicMock(scale=1.0)
    widths = _widths_array(tt, metrics)

    # Standard PDF font has 256 slots
    assert len(widths) == 256
    # 'A' is at 65. width 500 * scale 1 = 500
    assert widths[65] == 500.0
    # .notdef is default
    assert widths[0] == 600.0


# --- Merged from test_font_embedding_extra.py ---
# tests/test_font_embedding_extra.py
from unittest.mock import MagicMock

import pikepdf
import pytest

from pdftl.fonts import font_embedding as fe
from pdftl.fonts.font_embedding import _extract_ttf_metrics


def test__extract_ttf_metrics_missing_sCapHeight_uses_ascent():
    tt = make_tt_mock(units_per_em=1000, hhea_ascent=800, hhea_descent=-200, os2_scap=None)
    # delete sCapHeight to trigger fallback path
    if hasattr(tt["OS/2"], "sCapHeight"):
        delattr(tt["OS/2"], "sCapHeight")

    metrics = fe._extract_ttf_metrics(tt)
    assert metrics.cap_height == pytest.approx(metrics.ascent)


def test_embed_truetype_font_file_not_found(tmp_path):
    pdf = pikepdf.new()
    missing = tmp_path / "does_not_exist.ttf"
    with pytest.raises(FileNotFoundError):
        fe.embed_truetype_font(pdf, str(missing))


def make_tt_mock(
    ps_name="Arial",
    units_per_em=1000,
    hhea_ascent=0,
    hhea_descent=0,
    os2_scap=0,
    post_italic=0.0,
    post_is_fixed=False,
    head_bbox=(0, 0, 0, 0),
):
    """
    Creates a MagicMock behaving like a fontTools TTFont object.
    Configured to support standard table access (tt['head'], tt['os/2'], etc.).
    """
    tt = MagicMock()

    # --- 1. Setup specific tables based on arguments ---

    # 'head' table (UnitsPerEm, Bounding Box)
    head = MagicMock()
    head.unitsPerEm = units_per_em
    head.xMin, head.yMin, head.xMax, head.yMax = head_bbox

    # 'hhea' table (Ascent, Descent)
    hhea = MagicMock()
    hhea.ascent = hhea_ascent
    hhea.descent = hhea_descent

    # 'OS/2' table (CapHeight)
    os2 = MagicMock()
    os2.sCapHeight = os2_scap

    # 'post' table (Italic Angle, Fixed Pitch)
    post = MagicMock()
    post.italicAngle = post_italic
    post.isFixedPitch = post_is_fixed

    # 'name' table
    name_table = MagicMock()
    name_table.getDebugName.return_value = ps_name

    # 'hmtx' table (Metrics) - preserving your existing logic
    hmtx_table = MagicMock()
    hmtx_table.metrics = {".notdef": (1000, 0)}
    # Allow dictionary-style access: table['A']
    hmtx_table.__getitem__.side_effect = lambda k: hmtx_table.metrics.get(k, (1000, 0))

    # --- 2. Wire up dictionary access for the main TTFont object ---

    table_map = {
        "head": head,
        "hhea": hhea,
        "OS/2": os2,
        "post": post,
        "name": name_table,
        "hmtx": hmtx_table,
    }

    def getitem(key):
        # Return specific table mock if mapped, otherwise a generic MagicMock
        return table_map.get(key, MagicMock())

    tt.__getitem__.side_effect = getitem
    tt.getBestCmap.return_value = {}

    return tt


# tests/test_font_embedding_extra.py


def test__extract_ttf_metrics_with_all_tables():
    """
    Verifies that _extract_ttf_metrics correctly pulls data from
    tables and normalizes them to PDF units (1/1000 em).
    """
    tt = make_tt_mock(
        units_per_em=2048,
        hhea_ascent=1536,
        hhea_descent=-512,
        os2_scap=1400,
        post_italic=12.5,
        post_is_fixed=True,
        head_bbox=(1, 2, 3, 4),
        ps_name="My PS Name",
    )

    metrics = _extract_ttf_metrics(tt)

    # 1. Verify raw units_per_em is preserved
    assert metrics.units_per_em == 2048

    # 2. Verify scaling logic
    # PDF standardizes font units to 1000.
    expected_scale = 1000.0 / 2048

    # Assert that metrics match the INPUT * SCALE
    assert metrics.ascent == 1536 * expected_scale
    assert metrics.descent == -512 * expected_scale
    assert metrics.cap_height == 1400 * expected_scale

    # BBox coordinates must also be scaled
    expected_bbox = [x * expected_scale for x in [1, 2, 3, 4]]
    assert metrics.bbox == expected_bbox

    # Italic angle is in degrees, so it is NOT scaled
    assert metrics.italic_angle == 12.5

    # Fixed pitch is encoded in the flags (bit 1)
    # Base flags logic in extract function is 32.
    # If isFixedPitch is True, it adds 1. Total = 33.
    assert metrics.flags == 33


def test__widths_array_with_notdef_and_missing_chars():
    tt = make_tt_mock()
    metrics = MagicMock(scale=1.0)

    widths = fe._widths_array(tt, metrics)
    # Just check first element is default
    assert widths[0] == 1000.0


def test_embed_truetype_font_success(tmp_path, monkeypatch):
    """
    Hybrid: use a real pikepdf.Pdf but patch TTFont and internal helpers so we don't need a real TTF.
    """
    pdf = pikepdf.new()
    fpath = tmp_path / "dummy.ttf"
    fpath.write_bytes(b"\x00\x01\x02\x03")

    # Patch TTFont so it doesn't try to parse the actual file
    tt_mock = make_tt_mock(ps_name="DropIn")
    monkeypatch.setattr(
        fe,
        "_extract_ttf_metrics",
        lambda tt: fe.FontMetricsData(
            ascent=100,
            bbox=[0, 0, 100, 100],
            cap_height=80,
            descent=-20,
            flags=32,
            italic_angle=0,
            ps_name="DropIn",
            scale=1.0,
        ),
    )
    # FIX: Added *args to accept the optional 3rd argument (custom_encoding_map)
    monkeypatch.setattr(fe, "_widths_array", lambda tt, metrics, *args: [600.0] * 256)
    monkeypatch.setattr("fontTools.ttLib.TTFont", lambda path: tt_mock)

    font_obj = fe.embed_truetype_font(pdf, str(fpath))

    assert font_obj["/Type"] == "/Font"
    assert font_obj["/Subtype"] == "/TrueType"
    assert font_obj["/BaseFont"] == "/DropIn"
    assert len(font_obj["/Widths"]) == 256


# --- Merged from test_font_embedding_extra2.py ---
# tests/test_font_embedding.py

import logging
from unittest.mock import MagicMock

import pytest

# Assuming _widths_array and FontMetricsData are imported or accessible
from pdftl.fonts.font_embedding import _widths_array


@pytest.fixture
def test_font_path():
    """Fixture providing a known path to a simple test TTF file."""
    # Placeholder: replace with actual path logic if needed
    return "fixtures/Roboto-Regular.ttf"


@pytest.mark.parametrize(
    "slot_index, custom_char_str, log_check",
    [
        # Case 1: Single character mapped successfully (Covers 183-186)
        (128, "A", None),
        # Case 2: Single character not found (Covers 187-191, logs warning)
        (129, "€", "not found in font cmap"),
        # Case 3: Complex string map (e.g., ligature) (Covers 192-194)
        (130, "fi", None),
    ],
)
def test_widths_array_custom_map_coverage(
    slot_index, custom_char_str, log_check, caplog, test_font_path
):
    """
    Tests the logic branches within _widths_array related to custom_encoding_map
    handling, specifically covering lines 181-194.

    This verifies handling for single found chars, single missing chars, and
    multi-character mappings (ligatures).
    """
    # 1. Mock TTFont object structure needed by _widths_array
    mock_tt = MagicMock()

    # Mock cmap: 'A' (65) exists, all other unicode points are missing.
    mock_tt.getBestCmap.return_value = {
        ord("A"): "A_glyph",
        # '€' (8364) is explicitly missing for the warning case (187-191).
    }

    # Mock hmtx table: provides metrics data (width)
    default_width_units = 600
    glyph_a_width_units = 500

    mock_tt["hmtx"].metrics = {
        "A_glyph": (glyph_a_width_units, 0),
        ".notdef": (default_width_units, 0),
    }

    # Ensure correct return when hmtx is accessed by glyph name
    def hmtx_lookup(gname):
        return mock_tt["hmtx"].metrics.get(gname, (0, 0))

    # Use side_effect to implement the lookup logic
    mock_tt["hmtx"].__getitem__.side_effect = hmtx_lookup

    # 2. Mock FontMetricsData (passed as 'metrics')
    metrics = MagicMock()
    metrics.scale = 0.5  # Use a non-1 scale factor to verify calculation: 600 * 0.5 = 300.0

    # 3. Create Custom Map
    custom_map = {slot_index: custom_char_str}

    # Calculate expected widths
    expected_default_width = default_width_units * metrics.scale
    expected_a_width = glyph_a_width_units * metrics.scale

    # --- Actual Function Call ---
    with caplog.at_level(logging.WARNING, logger="pdftl.fonts.font_embedding"):
        widths = _widths_array(mock_tt, metrics, custom_map)

    # --- Assertions ---

    # Verify the width for the custom slot index
    slot_width = widths[slot_index]

    if custom_char_str == "A":
        # Case 1: Single char found -> uses expected glyph width (Covers 183-186)
        assert slot_width == expected_a_width
        assert not caplog.text

    elif custom_char_str == "€":
        # Case 2: Single char NOT found -> uses default_width and logs warning (Covers 187-191)
        assert slot_width == expected_default_width
        assert log_check in caplog.text

    elif custom_char_str == "fi":
        # Case 3: Complex string (len > 1) -> uses default_width (Covers 192-194)
        assert slot_width == expected_default_width
        assert not caplog.text


def test_embed_truetype_font_invalid_file(tmp_path):
    """Covers font_embedding.py lines 64-66: TTLibError handling."""
    from fontTools.ttLib import TTLibError

    font_file = tmp_path / "broken.ttf"
    font_file.write_text("not a font")

    pdf = pikepdf.new()
    with patch("fontTools.ttLib.TTFont", side_effect=TTLibError("Corrupt")):
        with pytest.raises(TTLibError):
            embed_truetype_font(pdf, str(font_file))


def test_extract_ttf_metrics_no_ps_name():
    """Covers font_embedding.py line 131: Fallback PS name."""
    mock_tt = MagicMock()
    # Mock name table to return None for ID 6
    mock_tt["name"].getDebugName.return_value = None
    mock_tt["head"].unitsPerEm = 1000

    metrics = _extract_ttf_metrics(mock_tt)
    assert metrics.ps_name == "EmbeddedFont"


def test_get_glyph_width_missing_glyph():
    """Covers font_embedding.py line 171: Missing glyph width."""
    mock_tt = MagicMock()
    # Ensure the glyph name is NOT in metrics
    mock_tt["hmtx"].metrics = {}

    width = _get_glyph_width(mock_tt, MagicMock(scale=1.0), "nonexistent")
    assert width == 0.0
