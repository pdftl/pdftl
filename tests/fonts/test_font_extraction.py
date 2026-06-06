"""Tests for pdftl.fonts.font_extraction_utils"""

from unittest.mock import MagicMock, PropertyMock

from pdftl.fonts.font_extraction_utils import (
    extract_resource_fonts,
    find_font_descriptor,
    get_encoding_name,
    get_font_properties,
    parse_font_flags,
)


class TestFontExtractionUtils:
    def test_parse_font_flags(self):
        """Test extraction of 32-bit font mask flags."""
        # Setup integer matching specific bits: 1(bit 1), 32(bit 6), 65536(bit 17)
        flags = parse_font_flags((1 << 0) | (1 << 5) | (1 << 16))
        assert flags["fixed_pitch"] is True
        assert flags["nonsymbolic"] is True
        assert flags["all_cap"] is True
        assert flags["italic"] is False
        assert flags["force_bold"] is False

    def test_find_font_descriptor_direct(self):
        """Test finding a direct FontDescriptor."""
        obj = MagicMock()
        obj.__contains__.side_effect = lambda k: k == "/FontDescriptor"
        obj.FontDescriptor = "DirectDescriptor"
        assert find_font_descriptor(obj) == "DirectDescriptor"

    def test_find_font_descriptor_descendant(self):
        """Test falling back to DescendantFonts."""
        obj = MagicMock()
        obj.__contains__.side_effect = lambda k: k == "/DescendantFonts"

        desc_item = MagicMock()
        desc_item.__contains__.side_effect = lambda k: k == "/FontDescriptor"
        desc_item.FontDescriptor = "DescendantDescriptor"

        obj.DescendantFonts = [desc_item]
        assert find_font_descriptor(obj) == "DescendantDescriptor"

    def test_find_font_descriptor_exceptions(self):
        """Test exception handling during descendant discovery."""
        obj = MagicMock()
        obj.__contains__.side_effect = lambda k: k == "/DescendantFonts"
        # Simulate failure when accessing DescendantFonts
        type(obj).DescendantFonts = PropertyMock(side_effect=AttributeError)
        assert find_font_descriptor(obj) is None

        # Return empty list simulating IndexError bounds trap avoidance
        obj2 = MagicMock()
        obj2.__contains__.side_effect = lambda k: k == "/DescendantFonts"
        obj2.DescendantFonts = []
        assert find_font_descriptor(obj2) is None

    def test_get_font_properties_type3(self):
        """Type3 fonts should return basic hardcoded traits immediately."""
        obj = MagicMock()
        obj.get.return_value = "/Type3"
        is_emb, f_bytes, traits, metrics = get_font_properties(obj)
        assert is_emb is True
        assert f_bytes == 0
        assert traits == {}
        assert metrics == {}

    def test_get_font_properties_missing_descriptor(self):
        """Return defaults if no descriptor is found."""
        obj = MagicMock()
        obj.get.return_value = "/Type1"
        obj.__contains__.return_value = False
        is_emb, f_bytes, traits, metrics = get_font_properties(obj)
        assert is_emb is False

    def test_get_font_properties_embedded_stream(self):
        """Test proper stream sizing with successful read_raw_bytes."""
        desc = MagicMock()
        # Mock dictionary keys
        desc.__contains__.side_effect = lambda k: k in (
            "/FontFile",
            "/Flags",
            "/StemV",
            "/FontBBox",
        )
        desc.get.side_effect = lambda k: "32" if k == "/Flags" else None

        ff = MagicMock()
        ff.read_raw_bytes.return_value = b"12345"
        desc.__getitem__.side_effect = (
            lambda k: ff
            if k == "/FontFile"
            else ("10.5" if k == "/StemV" else ["1", "2", "3", "4"])
        )

        obj = MagicMock()
        obj.get.return_value = "/Type1"
        obj.__contains__.side_effect = lambda k: k == "/FontDescriptor"
        obj.FontDescriptor = desc

        is_emb, f_bytes, traits, metrics = get_font_properties(obj)
        assert is_emb is True
        assert f_bytes == 5
        assert traits["nonsymbolic"] is True
        assert metrics["stem_v"] == 10.5
        assert metrics["bbox"] == [1.0, 2.0, 3.0, 4.0]

    def test_get_font_properties_fallback_length(self):
        """Test falling back to Length1 when read_raw_bytes throws."""
        desc = MagicMock()
        desc.__contains__.side_effect = lambda k: k == "/FontFile2"

        ff = MagicMock()
        ff.read_raw_bytes.side_effect = AttributeError
        ff.Length1 = "100"
        desc.__getitem__.side_effect = lambda k: ff

        obj = MagicMock()
        obj.get.return_value = "/TrueType"
        obj.__contains__.side_effect = lambda k: k == "/FontDescriptor"
        obj.FontDescriptor = desc

        is_emb, f_bytes, traits, metrics = get_font_properties(obj)
        assert is_emb is True
        assert f_bytes == 100

    def test_get_font_properties_invalid_metrics(self):
        """Ensure resilient parsing over malformed float metrics and bbox Arrays."""
        desc = MagicMock()
        desc.__contains__.side_effect = lambda k: k in ("/Ascent", "/FontBBox")
        desc.__getitem__.side_effect = lambda k: "invalid" if k == "/Ascent" else ["bad", "box"]

        obj = MagicMock()
        obj.get.return_value = "/Type1"
        obj.__contains__.side_effect = lambda k: k == "/FontDescriptor"
        obj.FontDescriptor = desc

        is_emb, f_bytes, traits, metrics = get_font_properties(obj)
        assert "ascent" not in metrics
        assert "bbox" not in metrics

    def test_get_encoding_name(self):
        """Test encoding string normalization mapping."""
        obj = MagicMock()
        obj.__contains__.return_value = False
        assert get_encoding_name(obj) == "Standard"

        obj.__contains__.return_value = True
        enc = MagicMock()
        enc.get.return_value = True
        enc.__contains__.return_value = True
        enc.BaseEncoding = "/WinAnsiEncoding"
        obj.Encoding = enc
        assert get_encoding_name(obj) == "WinAnsiEncoding"

        enc.__contains__.return_value = False
        enc.__str__.return_value = "/Identity-H"
        assert get_encoding_name(obj) == "Identity-H"

        # Safely trigger the exception block without polluting MagicMock globally
        class MalformedFont:
            def __contains__(self, item):
                return True

            @property
            def Encoding(self):
                class BadEncoding:
                    def __str__(self):
                        raise ValueError("Simulated encoding extraction failure")

                return BadEncoding()

        assert get_encoding_name(MalformedFont()) == "Unknown"

    def test_extract_resource_fonts(self):
        """Test compiling resource maps."""
        assert extract_resource_fonts(None) == []

        res = MagicMock()
        res.__contains__.return_value = False
        assert extract_resource_fonts(res) == []

        res.__contains__.return_value = True
        font_obj = MagicMock()
        font_obj.hasattr.return_value = True
        font_obj.get.side_effect = (
            lambda k, d=None: "/AAAAAA+MyFont" if k == "/BaseFont" else "/TrueType"
        )
        font_obj.objgen = (99, 0)
        font_obj.__contains__.return_value = False

        res.Font.items.return_value = [
            ("/F1", font_obj),
            ("/F2", MagicMock(spec=[])),
        ]  # F2 misses 'get'
        fonts = extract_resource_fonts(res)

        assert len(fonts) == 1
        f = fonts[0]
        assert f["name"] == "F1"
        assert f["base_font"] == "MyFont"
        assert f["subtype"] == "TrueType"
        assert f["is_subset"] is True
        assert f["obj_id"] == 99

    def test_find_font_descriptor_exceptions_caught(self):
        """Test lines 41-42: exceptions when accessing DescendantFonts are ignored."""

        class MalformedDescendants:
            def __contains__(self, item):
                return item == "/DescendantFonts"

            @property
            def DescendantFonts(self):
                raise KeyError("Descendants key missing despite __contains__ being True")

        assert find_font_descriptor(MalformedDescendants()) is None

    def test_get_font_properties_fallback_length_invalid(self):
        """Test lines 69-70: exception when converting Length1 to int falls back to 0."""
        desc = MagicMock()
        desc.__contains__.side_effect = lambda k: k == "/FontFile2"

        ff = MagicMock()
        # Force read_raw_bytes to fail so it drops into the Length1 fallback block
        ff.read_raw_bytes.side_effect = AttributeError

        # A non-integer string will trigger the ValueError on line 69
        ff.Length1 = "not_an_integer"
        desc.__getitem__.side_effect = lambda k: ff

        obj = MagicMock()
        obj.get.return_value = "/TrueType"
        obj.__contains__.side_effect = lambda k: k == "/FontDescriptor"
        obj.FontDescriptor = desc

        is_emb, f_bytes, traits, metrics = get_font_properties(obj)
        assert is_emb is True
        assert f_bytes == 0
