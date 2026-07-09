# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/fonts/test_font_extraction_utils.py

"""Tests for pdftl.fonts.font_extraction_utils using real pikepdf objects."""

import pikepdf

from pdftl.fonts.font_extraction_utils import (
    extract_document_fonts,
    extract_resource_fonts,
    find_font_descriptor,
    get_encoding_name,
    get_font_properties,
    parse_font_flags,
    process_single_font,
)


class TestFontExtractionUtils:
    def test_parse_font_flags(self):
        """Test extraction of 32-bit font mask flags."""
        flags = parse_font_flags((1 << 0) | (1 << 5) | (1 << 16))
        assert flags["fixed_pitch"] is True
        assert flags["nonsymbolic"] is True
        assert flags["all_cap"] is True
        assert flags["italic"] is False
        assert flags["force_bold"] is False

    def test_find_font_descriptor_direct(self):
        """Test finding a direct FontDescriptor."""
        obj = pikepdf.Dictionary({"/FontDescriptor": pikepdf.Name("/DirectDescriptor")})
        assert find_font_descriptor(obj) == pikepdf.Name("/DirectDescriptor")

    def test_find_font_descriptor_descendant(self):
        """Test falling back to DescendantFonts."""
        desc_item = pikepdf.Dictionary({"/FontDescriptor": pikepdf.Name("/DescendantDescriptor")})
        obj = pikepdf.Dictionary({"/DescendantFonts": pikepdf.Array([desc_item])})
        assert find_font_descriptor(obj) == pikepdf.Name("/DescendantDescriptor")

    def test_find_font_descriptor_exceptions(self):
        """Test exception handling during descendant discovery."""
        obj = {"/DescendantFonts": True}
        assert find_font_descriptor(obj) is None

        # Empty array avoids IndexError traps natively
        obj2 = pikepdf.Dictionary({"/DescendantFonts": pikepdf.Array([])})
        assert find_font_descriptor(obj2) is None

    def test_get_font_properties_type3(self):
        """Type3 fonts should return basic hardcoded traits immediately."""
        obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type3")})
        is_emb, f_bytes, traits, metrics = get_font_properties(obj)
        assert is_emb is True
        assert f_bytes == 0
        assert traits == {}
        assert metrics == {}

    def test_get_font_properties_missing_descriptor(self):
        """Return defaults if no descriptor is found."""
        obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type1")})
        is_emb, f_bytes, traits, metrics = get_font_properties(obj)
        assert is_emb is False

    def test_get_font_properties_embedded_stream(self):
        """Test proper stream sizing with successful read_raw_bytes."""
        doc = pikepdf.Pdf.new()
        stream = doc.make_stream(b"12345")

        desc = pikepdf.Dictionary(
            {
                "/FontFile": stream,
                "/Flags": 32,
                "/StemV": 10.5,
                "/FontBBox": pikepdf.Array([1.0, 2.0, 3.0, 4.0]),
            }
        )
        obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type1"), "/FontDescriptor": desc})

        is_emb, f_bytes, traits, metrics = get_font_properties(obj)
        assert is_emb is True
        assert f_bytes == 5
        assert traits["nonsymbolic"] is True
        assert metrics["stem_v"] == 10.5
        assert metrics["bbox"] == [1.0, 2.0, 3.0, 4.0]

    def test_get_font_properties_fallback_length(self):
        """Test falling back to Length1 when object lacks read_raw_bytes."""

        class DummyFontFile:
            Length1 = 100

        class DummyFontObj(dict):
            @property
            def FontDescriptor(self):
                return self["/FontDescriptor"]

        # Use Python dicts to simulate the missing read_raw_bytes without triggering
        # pikepdf's strict C++ PdfError which occurs when attempting streams on dictionaries.
        desc = {"/FontFile2": DummyFontFile()}
        obj = DummyFontObj({"/Subtype": "/TrueType", "/FontDescriptor": desc})

        is_emb, f_bytes, traits, metrics = get_font_properties(obj)
        assert is_emb is True
        assert f_bytes == 100

    def test_get_font_properties_invalid_metrics(self):
        """Ensure resilient parsing over malformed float metrics and bbox Arrays."""
        desc = pikepdf.Dictionary(
            {
                "/Ascent": pikepdf.Name("/invalid"),
                "/FontBBox": pikepdf.Array([pikepdf.Name("/bad"), pikepdf.Name("/box")]),
            }
        )
        obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type1"), "/FontDescriptor": desc})

        is_emb, f_bytes, traits, metrics = get_font_properties(obj)
        assert "ascent" not in metrics
        assert "bbox" not in metrics

    def test_get_font_properties_fallback_length_invalid(self):
        """Test handling of Length1 conversion errors."""

        class DummyFontFile:
            Length1 = "/NotAnInt"

        class DummyFontObj(dict):
            @property
            def FontDescriptor(self):
                return self["/FontDescriptor"]

        desc = {"/FontFile2": DummyFontFile()}
        obj = DummyFontObj({"/Subtype": "/TrueType", "/FontDescriptor": desc})

        is_emb, f_bytes, traits, metrics = get_font_properties(obj)
        assert is_emb is True
        assert f_bytes == 0

    def test_extract_resource_fonts(self):
        """Test compiling resource maps."""
        assert extract_resource_fonts(None) == []

        empty_res = pikepdf.Dictionary()
        assert extract_resource_fonts(empty_res) == []

        doc = pikepdf.Pdf.new()
        font_obj = doc.make_indirect(
            pikepdf.Dictionary(
                {
                    "/BaseFont": pikepdf.Name("/AAAAAA+MyFont"),
                    "/Subtype": pikepdf.Name("/TrueType"),
                    "/Type": pikepdf.Name("/Font"),
                }
            )
        )

        # Second font is invalid (an integer) to test skip mechanics cleanly.
        # integers inherently lack .get(), whereas pikepdf.Array natively has it.
        res = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font_obj, "/F2": 123})})

        fonts = extract_resource_fonts(res)

        assert len(fonts) == 1
        f = fonts[0]
        assert f["name"] == "AAAAAA+MyFont"
        assert f["base_font"] == "MyFont"
        assert f["subtype"] == "TrueType"
        assert f["is_subset"] is True
        assert f["obj_id"] is not None


class TestGetEncodingName:
    def test_type3_returns_custom(self):
        obj = pikepdf.Dictionary()
        assert get_encoding_name(obj, subtype="Type 3") == "Custom"

    def test_type1_base14_returns_standard(self):
        obj = pikepdf.Dictionary()
        assert get_encoding_name(obj, subtype="Type 1", base_font="Helvetica") == "Standard"

    def test_truetype_no_encoding_returns_winAnsi(self):
        obj = pikepdf.Dictionary()
        assert get_encoding_name(obj, subtype="TrueType") == "WinAnsi"

    def test_pikepdf_name_encoding(self):
        obj = pikepdf.Dictionary({"/Encoding": pikepdf.Name("/WinAnsiEncoding")})
        assert get_encoding_name(obj) == "WinAnsi"

    def test_pikepdf_name_encoding_no_suffix(self):
        obj = pikepdf.Dictionary({"/Encoding": pikepdf.Name("/Identity-H")})
        assert get_encoding_name(obj) == "Identity-H"

    def test_dict_with_differences_returns_custom(self):
        obj = pikepdf.Dictionary(
            {"/Encoding": pikepdf.Dictionary({"/Differences": pikepdf.Array([])})}
        )
        assert get_encoding_name(obj) == "Custom"

    def test_dict_base_encoding(self):
        obj = pikepdf.Dictionary(
            {"/Encoding": pikepdf.Dictionary({"/BaseEncoding": pikepdf.Name("/WinAnsiEncoding")})}
        )
        assert get_encoding_name(obj) == "WinAnsi"

    def test_dict_no_base_encoding_returns_custom(self):
        obj = pikepdf.Dictionary({"/Encoding": pikepdf.Dictionary()})
        assert get_encoding_name(obj) == "Custom"

    def test_enc_str_pikepdf_dictionary_fallback(self):
        """Simulate an encoding value that coerces to an unknown string representation."""

        class MockFontDict(dict):
            @property
            def Encoding(self):
                return type("UnknownType", (), {"__str__": lambda s: "pikepdf.Dictionary"})()

        obj = MockFontDict({"/Encoding": True})
        assert get_encoding_name(obj) == "Custom"


class TestProcessSingleFont:
    def test_returns_none_if_no_get(self):
        assert process_single_font("/F1", []) is None

    def test_basic_font(self):
        obj = pikepdf.Dictionary(
            {"/BaseFont": pikepdf.Name("/Arial"), "/Subtype": pikepdf.Name("/Type1")}
        )
        result = process_single_font("/F1", obj)
        assert result["base_font"] == "Arial"
        assert result["subtype"] == "Type 1"
        assert result["resource_name"] == "F1"
        assert result["descriptor_font"] == ""

    def test_subset_font(self):
        obj = pikepdf.Dictionary(
            {"/BaseFont": pikepdf.Name("/ABCDEF+MyFont"), "/Subtype": pikepdf.Name("/TrueType")}
        )
        result = process_single_font("/F1", obj)
        assert result["is_subset"] is True
        assert result["base_font"] == "MyFont"
        assert result["name"] == "ABCDEF+MyFont"

    def test_basefont_fallback_to_parent(self):
        """When physical_obj has no /BaseFont, falls back to parent_obj."""
        physical_obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/CIDFontType2")})
        parent_obj = pikepdf.Dictionary(
            {
                "/Subtype": pikepdf.Name("/Type0"),
                "/BaseFont": pikepdf.Name("/FallbackFont"),
                "/DescendantFonts": pikepdf.Array([physical_obj]),
            }
        )
        result = process_single_font("/F1", parent_obj)
        assert result["base_font"] == "FallbackFont"

    def test_type1c_subtype_refinement(self):
        """FontFile3 with Type1C subtype refines subtype to 'Type 1C'."""
        doc = pikepdf.Pdf.new()
        ff3 = doc.make_stream(b"")
        ff3.Subtype = pikepdf.Name("/Type1C")

        desc = pikepdf.Dictionary({"/FontFile3": ff3, "/FontName": pikepdf.Name("/DescFont")})
        obj = pikepdf.Dictionary(
            {
                "/BaseFont": pikepdf.Name("/MyFont"),
                "/Subtype": pikepdf.Name("/Type1"),
                "/FontDescriptor": desc,
            }
        )
        result = process_single_font("/F1", obj)
        assert result["subtype"] == "Type 1C"
        assert result["descriptor_font"] == "DescFont"

    def test_no_basefont_returns_none_name(self):
        obj = pikepdf.Dictionary({"/Subtype": pikepdf.Name("/Type1")})
        result = process_single_font("/F1", obj)
        assert result["name"] == "[none]"

    def test_type0_descendant_exception_is_swallowed(self):
        """Exception accessing DescendantFonts falls back to parent_obj."""

        class DummyParentObj(dict):
            @property
            def DescendantFonts(self):
                raise AttributeError

        parent_obj = DummyParentObj(
            {"/Subtype": "/Type0", "/BaseFont": "/MyFont", "/DescendantFonts": True}
        )
        result = process_single_font("/F1", parent_obj)
        assert result is not None
        assert result["base_font"] == "MyFont"

    def test_cidfonttype0c_subtype_refinement(self):
        """FontFile3 with CIDFontType0C subtype."""
        doc = pikepdf.Pdf.new()
        ff3 = doc.make_stream(b"")
        ff3.Subtype = pikepdf.Name("/CIDFontType0C")

        desc = pikepdf.Dictionary({"/FontFile3": ff3})
        obj = pikepdf.Dictionary(
            {
                "/BaseFont": pikepdf.Name("/MyFont"),
                "/Subtype": pikepdf.Name("/CIDFontType0"),
                "/FontDescriptor": desc,
            }
        )
        result = process_single_font("/F1", obj)
        assert result["subtype"] == "CID Type 0C"

    def test_process_single_font_type3(self):
        """Exact mapping for Type 3 fonts."""
        obj = pikepdf.Dictionary(
            {"/BaseFont": pikepdf.Name("/MyType3Font"), "/Subtype": pikepdf.Name("/Type3")}
        )
        result = process_single_font("/F1", obj)
        assert result["subtype"] == "Type 3"

    def test_process_single_font_cidfonttype0(self):
        """Exact mapping for CIDFontType0 fonts."""
        obj = pikepdf.Dictionary(
            {
                "/BaseFont": pikepdf.Name("/MyCIDFont"),
                "/Subtype": pikepdf.Name("/CIDFontType0"),
                # By intentionally omitting a /FontDescriptor, it skips the Type 0C
                # refinement block and natively relies on the basic subtype parsing.
            }
        )
        result = process_single_font("/F1", obj)
        assert result["subtype"] == "CID Type 0"


class TestExtractDocumentFonts:
    def test_empty_pdf_returns_empty(self):
        doc = pikepdf.Pdf.new()
        assert extract_document_fonts(doc) == []

    def test_page_without_resources_skipped(self):
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        if "/Resources" in doc.pages[0]:
            del doc.pages[0].Resources
        assert extract_document_fonts(doc) == []

    def test_single_font_extracted(self):
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        font = doc.make_indirect(
            pikepdf.Dictionary(
                {"/BaseFont": pikepdf.Name("/Arial"), "/Subtype": pikepdf.Name("/Type1")}
            )
        )
        doc.pages[0].Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})

        result = extract_document_fonts(doc)
        assert len(result) == 1
        assert result[0]["base_font"] == "Arial"

    def test_deduplicates_same_font_across_pages(self):
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        doc.add_blank_page()

        font = doc.make_indirect(
            pikepdf.Dictionary(
                {"/BaseFont": pikepdf.Name("/Arial"), "/Subtype": pikepdf.Name("/Type1")}
            )
        )

        res_dict = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})
        doc.pages[0].Resources = res_dict
        doc.pages[1].Resources = res_dict

        result = extract_document_fonts(doc)
        assert len(result) == 1

    def test_page_indices_filters_pages(self):
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        result = extract_document_fonts(doc, page_indices=[0])
        assert result == []

    def test_xobject_font_crawled(self):
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        font = doc.make_indirect(
            pikepdf.Dictionary(
                {"/BaseFont": pikepdf.Name("/XObjFont"), "/Subtype": pikepdf.Name("/Type1")}
            )
        )

        xobj = doc.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/XObject"),
                    "/Resources": pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})}),
                }
            )
        )

        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/Im1": xobj})}
        )

        result = extract_document_fonts(doc)
        assert len(result) == 1
        assert result[0]["base_font"] == "XObjFont"

    def test_pattern_font_crawled(self):
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        font = doc.make_indirect(
            pikepdf.Dictionary(
                {"/BaseFont": pikepdf.Name("/PatternFont"), "/Subtype": pikepdf.Name("/Type1")}
            )
        )

        pat = doc.make_indirect(
            pikepdf.Dictionary(
                {
                    "/Type": pikepdf.Name("/Pattern"),
                    "/Resources": pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})}),
                }
            )
        )

        doc.pages[0].Resources = pikepdf.Dictionary({"/Pattern": pikepdf.Dictionary({"/P1": pat})})

        result = extract_document_fonts(doc)
        assert len(result) == 1
        assert result[0]["base_font"] == "PatternFont"

    def test_extgstate_font_crawled(self):
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        font = doc.make_indirect(
            pikepdf.Dictionary(
                {"/BaseFont": pikepdf.Name("/GSFont"), "/Subtype": pikepdf.Name("/Type1")}
            )
        )

        gs = doc.make_indirect(
            pikepdf.Dictionary(
                {"/Type": pikepdf.Name("/ExtGState"), "/Font": pikepdf.Array([font, 12.0])}
            )
        )

        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/ExtGState": pikepdf.Dictionary({"/GS1": gs})}
        )

        result = extract_document_fonts(doc)
        assert len(result) == 1
        assert result[0]["base_font"] == "GSFont"

    def test_page_index_out_of_bounds_skipped(self):
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        result = extract_document_fonts(doc, page_indices=[999])
        assert result == []

    def test_doc_pages_attribute_error(self):
        assert extract_document_fonts(pikepdf.Dictionary()) == []

    def test_annots_ap_resources_crawled(self):
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        font = doc.make_indirect(
            pikepdf.Dictionary(
                {"/BaseFont": pikepdf.Name("/AnnotFont"), "/Subtype": pikepdf.Name("/Type1")}
            )
        )

        ap_stream = doc.make_stream(b"")
        ap_stream.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})

        annot = pikepdf.Dictionary(
            {"/Type": pikepdf.Name("/Annot"), "/AP": pikepdf.Dictionary({"/N": ap_stream})}
        )

        doc.pages[0].Annots = pikepdf.Array([annot])

        result = extract_document_fonts(doc)
        assert len(result) == 1
        assert result[0]["base_font"] == "AnnotFont"

    def test_extgstate_font_dedup_by_obj_id(self):
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        font = doc.make_indirect(
            pikepdf.Dictionary(
                {"/BaseFont": pikepdf.Name("/GSFont"), "/Subtype": pikepdf.Name("/Type1")}
            )
        )

        gs1 = doc.make_indirect(pikepdf.Dictionary({"/Font": pikepdf.Array([font, 10.0])}))
        gs2 = doc.make_indirect(pikepdf.Dictionary({"/Font": pikepdf.Array([font, 12.0])}))

        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/ExtGState": pikepdf.Dictionary({"/GS1": gs1, "/GS2": gs2})}
        )

        result = extract_document_fonts(doc)
        assert len(result) == 1

    def test_annots_ap_substate_dict_crawled(self):
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        font = doc.make_indirect(
            pikepdf.Dictionary(
                {"/BaseFont": pikepdf.Name("/SubAPFont"), "/Subtype": pikepdf.Name("/Type1")}
            )
        )

        sub_ap = doc.make_stream(b"")
        sub_ap.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})

        ap_state = pikepdf.Dictionary({"/On": sub_ap})

        annot = pikepdf.Dictionary({"/AP": pikepdf.Dictionary({"/N": ap_state})})

        doc.pages[0].Annots = pikepdf.Array([annot])

        result = extract_document_fonts(doc)
        assert len(result) == 1
        assert result[0]["base_font"] == "SubAPFont"

    def test_malformed_resources_safely_ignored(self):
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        doc.pages[0].Resources = pikepdf.Dictionary(
            {
                "/ExtGState": pikepdf.Name("/NotADict"),
                "/Pattern": pikepdf.Name("/NotADict"),
                "/XObject": pikepdf.Name("/NotADict"),
            }
        )

        result = extract_document_fonts(doc)
        assert result == []

    # === Append to TestGetEncodingName ===

    def test_dict_base_encoding_bracket_access(self):
        """dictionary bracket fallback for BaseEncoding."""

        class MockEnc(dict):
            pass  # Lacks .BaseEncoding attribute naturally

        obj = pikepdf.Dictionary({"/Encoding": MockEnc({"/BaseEncoding": "/MacRomanEncoding"})})
        assert get_encoding_name(obj) == "MacRoman"

    def test_enc_str_encoding_suffix(self):
        """stripping 'Encoding' from string representation."""

        class MockFontDict(dict):
            @property
            def Encoding(self):
                return type("UnknownType", (), {"__str__": lambda s: "MacRomanEncoding"})()

        obj = MockFontDict({"/Encoding": True})
        assert get_encoding_name(obj) == "MacRoman"

    # === Append to TestExtractDocumentFonts ===

    def test_crawl_resources_none(self):
        """Return safely when resources resolve to None."""

        class PageWithNoneRes(dict):
            @property
            def Resources(self):
                return None

        class Doc:
            pages = [PageWithNoneRes({"/Resources": True})]

        assert extract_document_fonts(Doc()) == []

    def test_font_resources_nested_crawl(self):
        """/Resources contained strictly inside a /Font dict."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        # We need a font object that natively has a .Resources attribute to evaluate
        class NestedResFont(dict):
            @property
            def objgen(self):
                return (123, 0)

            @property
            def Resources(self):
                return None  # Triggers fallback base crawl exception recursively

        res_font = NestedResFont({"/BaseFont": "/Arial", "/Subtype": "/Type1", "/Resources": True})

        class PageRes(dict):
            @property
            def Font(self):
                return {"/F1": res_font}

        doc.pages[0].Resources = PageRes({"/Font": True})
        extract_document_fonts(doc)

    def test_extract_doc_fonts_seen_font_ids(self):
        """Deduping Font object_id that was previously crawled via ExtGState."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        font = doc.make_indirect(
            pikepdf.Dictionary(
                {"/BaseFont": pikepdf.Name("/Arial"), "/Subtype": pikepdf.Name("/Type1")}
            )
        )

        gs = doc.make_indirect(
            pikepdf.Dictionary(
                {"/Type": pikepdf.Name("/ExtGState"), "/Font": pikepdf.Array([font, 12.0])}
            )
        )

        # Putting it in both lists guarantees obj_id is evaluated at deduplication points for /Font
        # since it's an indirect object skipped by dictionary id logic.
        doc.pages[0].Resources = pikepdf.Dictionary(
            {
                "/ExtGState": pikepdf.Dictionary({"/GS1": gs}),
                "/Font": pikepdf.Dictionary({"/F1": font}),
            }
        )

        result = extract_document_fonts(doc)
        assert len(result) == 1

    def test_xobject_dedup(self):
        """Skipping previously seen XObjects dict IDs."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        xobj = doc.make_indirect(
            pikepdf.Dictionary(
                {"/Type": pikepdf.Name("/XObject"), "/Resources": pikepdf.Dictionary()}
            )
        )
        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/Im1": xobj, "/Im2": xobj})}
        )
        assert extract_document_fonts(doc) == []

    def test_pattern_dedup(self):
        """Skipping previously seen Pattern dict IDs."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        pat = doc.make_indirect(
            pikepdf.Dictionary(
                {"/Type": pikepdf.Name("/Pattern"), "/Resources": pikepdf.Dictionary()}
            )
        )
        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/Pattern": pikepdf.Dictionary({"/P1": pat, "/P2": pat})}
        )
        assert extract_document_fonts(doc) == []

    def test_extgstate_dedup(self):
        """Skipping previously seen ExtGState dict IDs."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        gs = doc.make_indirect(pikepdf.Dictionary({"/Type": pikepdf.Name("/ExtGState")}))
        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/ExtGState": pikepdf.Dictionary({"/GS1": gs, "/GS2": gs})}
        )
        assert extract_document_fonts(doc) == []

    def test_font_exceptions_lines(self):
        """Swallow errors processing malformed /Font items."""

        class Res(dict):
            @property
            def Font(self):
                class BadFontMap(dict):
                    def items(self):
                        yield "/F1", None  # Triggers AttributeError on .objgen

                return BadFontMap()

        class Page(dict):
            @property
            def Resources(self):
                return Res({"/Font": True})

        doc = type("Doc", (), {"pages": [Page({"/Resources": True})]})()
        assert extract_document_fonts(doc) == []

    def test_extgstate_font_exception(self):
        """Swallow errors processing malformed ExtGState Font Arrays."""

        class BadGS(dict):
            @property
            def objgen(self):
                return (1, 0)

            @property
            def Font(self):
                raise TypeError("GS Font Error")

        class Res(dict):
            @property
            def ExtGState(self):
                return {"/GS1": BadGS({"/Font": True})}

        class Page(dict):
            @property
            def Resources(self):
                return Res({"/ExtGState": True})

        doc = type("Doc", (), {"pages": [Page({"/Resources": True})]})()
        assert extract_document_fonts(doc) == []

    def test_page_exception(self):
        """Swallow errors processing unreadable Page data structure."""

        class BadPage(dict):
            @property
            def Resources(self):
                raise TypeError("Page Resource Error")

        doc = type("Doc", (), {"pages": [BadPage({"/Resources": True})]})()
        assert extract_document_fonts(doc) == []


class TestFontExtractionCoverage:
    def test_type1c_subtype_exception(self):
        """Swallow Type/Attribute exceptions accessing FontFile3 properties."""

        class MockFontDict(dict):
            @property
            def FontDescriptor(self):
                return self["/FontDescriptor"]

        # Using an integer for FontFile3 safely bypasses read_raw_bytes
        # but naturally triggers a TypeError on `"/Subtype" in ff3`
        desc = {"/FontFile3": 12345}
        obj = MockFontDict({"/BaseFont": "/MyFont", "/Subtype": "/Type1", "/FontDescriptor": desc})
        result = process_single_font("/F1", obj)
        assert result["subtype"] == "Type 1"

    def test_enc_exception(self):
        """Gracefully handling unreadable Encoding traits."""

        class BadStrEnc:
            def __str__(self):
                # Raising inside __str__ places the exception safely inside the try block
                raise ValueError("Unreadable Encoding")

        class MockFontDict(dict):
            @property
            def Encoding(self):
                return BadStrEnc()

        obj = MockFontDict({"/Encoding": True})
        assert get_encoding_name(obj) == "Unknown"

    def test_dict_base_encoding_bracket_access(self):
        """Dictionary bracket fallback for BaseEncoding."""

        class MockFontDict(dict):
            @property
            def Encoding(self):
                # Standard dict naturally lacks attribute access (e.g., .BaseEncoding)
                return {"/BaseEncoding": "/MacRomanEncoding"}

        obj = MockFontDict({"/Encoding": True})
        assert get_encoding_name(obj) == "MacRoman"

    def test_font_resources_nested_crawl(self):
        """/Resources contained strictly inside a /Font dict."""

        class NestedResFont(dict):
            @property
            def objgen(self):
                return (123, 0)

            @property
            def Resources(self):
                return None  # Triggers the base crawl exception recursively

        class PageRes(dict):
            @property
            def Font(self):
                return {"/F1": NestedResFont({"/Resources": True, "/BaseFont": "/Arial"})}

        class MockPage(dict):
            @property
            def Resources(self):
                return PageRes({"/Font": True})

        class MockDoc:
            pages = [MockPage({"/Resources": True})]

        extract_document_fonts(MockDoc())

    def test_extract_doc_fonts_seen_font_ids_via_pages(self):
        """Deduping Font object_id previously crawled via ExtGState on another page."""

        class MockFont(dict):
            @property
            def objgen(self):
                return (999, 0)

        shared_font = MockFont({"/Subtype": "/Type1", "/BaseFont": "/Arial"})

        class ExtGStateRes(dict):
            @property
            def ExtGState(self):
                class MockGS(dict):
                    @property
                    def objgen(self):
                        return (888, 0)

                    @property
                    def Font(self):
                        return [shared_font, 12.0]

                return {"/GS1": MockGS({"/Font": True})}

        class Page1(dict):
            @property
            def Resources(self):
                return ExtGStateRes({"/ExtGState": True})

        class FontRes(dict):
            @property
            def Font(self):
                return {"/F1": shared_font}

        class Page2(dict):
            @property
            def Resources(self):
                return FontRes({"/Font": True})

        class MockDoc:
            pages = [Page1({"/Resources": True}), Page2({"/Resources": True})]

        # Page 1 ExtGState logs object 999 into seen_font_ids
        # Page 2 /Font logs object 999 into seen_dict_ids, natively triggering the dedup skip
        result = extract_document_fonts(MockDoc())
        assert len(result) == 1

    def test_crawl_fonts_with_list_font_obj(self):
        """Font_obj lacks .get(), causing _process_and_store_font to return early."""

        class MockFontRes(dict):
            @property
            def Font(self):
                # Using a list evaluates `"/Resources" in []` as safely False,
                # passing it to _process_and_store_font which aborts because lists lack .get()
                return {"/F1": []}

        class MockPage(dict):
            @property
            def Resources(self):
                return MockFontRes({"/Font": True})

        class MockDoc:
            pages = [MockPage({"/Resources": True})]

        # Should silently ignore the invalid font object and return an empty list
        assert extract_document_fonts(MockDoc()) == []

    def test_crawl_annots_ap_state_without_get(self):
        """Ap_state lacks a .get() method, causing early return."""

        class MockAnnot(dict):
            @property
            def AP(self):
                # The state mapping under /N is a list, not a stream dictionary
                return {"/N": []}

        class MockPage(dict):
            @property
            def Annots(self):
                return [MockAnnot({"/AP": True})]

        class MockDoc:
            pages = [MockPage({"/Annots": True})]

        # Gracefully skips the annotation appearance loop
        assert extract_document_fonts(MockDoc()) == []

    def test_process_single_font_returns_none_when_unwrapped_is_invalid_type(self, monkeypatch):
        """Ensures process_single_font returns None if the unwrapped physical font
        object resolves to an invalid type such as a PDF Name instead of a dictionary or stream.
        """
        parent_obj = pikepdf.Dictionary()

        # Unwrap to an unsupported type to verify the defensive fallback pathway
        import pdftl.fonts.font_extraction_utils as feu

        monkeypatch.setattr(
            feu, "_unwrap_physical_font", lambda obj: pikepdf.Name("/InvalidPhysicalObjType")
        )

        result = process_single_font("F1", parent_obj)
        assert result is None
