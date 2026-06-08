"""Tests for pdftl.utils.pdf_resources"""

import logging

import pikepdf

from pdftl.utils.pdf_resources import (
    _next_xobject_match,
    find_resource_recursive,
    get_all_fonts_recursive,
)


class TestPdfResources:
    # --- find_resource_recursive & helpers ---

    def test_find_resource_no_resources(self):
        """Test returning None when no /Resources dict is present on page."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        if "/Resources" in doc.pages[0]:
            del doc.pages[0].Resources
        assert find_resource_recursive(doc, "/Font", "/F1") is None

    def test_find_resource_immediate(self):
        """Test locating a resource directly on the page level."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        font = pikepdf.Dictionary({"/Type": "/Font"})
        doc.pages[0].Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})

        # Exact target name prefix
        res = find_resource_recursive(doc, "/Font", "/F1")
        assert res is not None

        # Unprefixed target name (hits normalization logic)
        res2 = find_resource_recursive(doc, "/Font", "F1")
        assert res2 is not None

        # Missing target name
        res3 = find_resource_recursive(doc, "/Font", "/F2")
        assert res3 is None

    def test_find_resource_xobject(self):
        """Test finding a resource deeply nested inside a Form XObject."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        font = pikepdf.Dictionary({"/Type": "/Font"})

        xobj = doc.make_stream(b"")
        xobj.Subtype = pikepdf.Name("/Form")
        xobj.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})

        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/X1": xobj})}
        )

        assert find_resource_recursive(doc, "/Font", "/F1") is not None

    def test_find_resource_xobject_visited_skip(self):
        """Test preventing infinite recursion loops from self-referencing XObjects."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        xobj = doc.make_stream(b"")
        xobj.Subtype = pikepdf.Name("/Form")
        # Self-reference creates infinite loop normally
        xobj.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/X1": xobj})})

        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/X1": xobj})}
        )

        # Bypasses loop via visited set check, cleanly returning None
        assert find_resource_recursive(doc, "/Font", "/F1") is None

    def test_next_xobject_match_not_pikepdf_object(self):
        """Test safely skipping items inside XObject maps that aren't valid PDF objects."""
        resources = {"/XObject": {"/X1": "not_an_object"}}
        visited = set()
        assert _next_xobject_match("/Font", resources, "/F1", visited) is None

    # --- get_all_fonts_recursive ---

    def test_get_all_fonts_immediate(self):
        """Test extracting fonts directly mapped in Page Resources."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        font = pikepdf.Dictionary({"/Type": "/Font"})
        doc.pages[0].Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})

        fonts = list(get_all_fonts_recursive(doc))
        assert len(fonts) == 1
        assert fonts[0][0] == "/F1"
        assert fonts[0][2] == 1  # Page 1

    def test_get_all_fonts_xobject(self):
        """Test recursively extracting fonts inside Form XObjects."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        font = pikepdf.Dictionary({"/Type": "/Font"})

        xobj = doc.make_stream(b"")
        xobj.Subtype = pikepdf.Name("/Form")
        xobj.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})

        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/X1": xobj})}
        )

        fonts = list(get_all_fonts_recursive(doc))
        assert len(fonts) == 1

    def test_get_all_fonts_pattern(self):
        """Test recursively extracting fonts mapped inside visual Pattern layouts."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        font = pikepdf.Dictionary({"/Type": "/Font"})

        pat = doc.make_stream(b"")
        pat.Type = pikepdf.Name("/Pattern")
        pat.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/PFont": font})})

        doc.pages[0].Resources = pikepdf.Dictionary({"/Pattern": pikepdf.Dictionary({"/P1": pat})})

        fonts = list(get_all_fonts_recursive(doc))
        assert len(fonts) == 1
        assert fonts[0][0] == "/PFont"

    def test_get_all_fonts_extgstate(self):
        """Test extracting font array tuples stored inside ExtGState descriptors."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        font = pikepdf.Dictionary({"/Type": "/Font"})

        gs = pikepdf.Dictionary(
            {"/Type": pikepdf.Name("/ExtGState"), "/Font": pikepdf.Array([font, 12.0])}
        )

        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/ExtGState": pikepdf.Dictionary({"/GS1": gs})}
        )

        fonts = list(get_all_fonts_recursive(doc))
        assert len(fonts) == 1
        assert fonts[0][0] == "/GS1_ExtGState"

    def test_get_all_fonts_extgstate_exception(self):
        """Test graceful degradation handling malformed arrays in ExtGState Font references."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        gs1 = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/ExtGState"),
                "/Font": 123,  # Int fails len() and array indexing, natively triggering Exception
            }
        )

        gs2 = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/ExtGState"),
                "/Font": pikepdf.Array([]),  # Fails len() > 0 boolean check silently
            }
        )

        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/ExtGState": pikepdf.Dictionary({"/GS1": gs1, "/GS2": gs2})}
        )

        fonts = list(get_all_fonts_recursive(doc))
        assert len(fonts) == 0

    def test_get_all_fonts_visited_loops(self):
        """Test duplicate/circular reference protection across all Resource subtypes."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        xobj = doc.make_stream(b"")
        xobj.Subtype = pikepdf.Name("/Form")
        xobj.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/X1": xobj})})

        pat = doc.make_stream(b"")
        pat.Type = pikepdf.Name("/Pattern")
        pat.Resources = pikepdf.Dictionary({"/Pattern": pikepdf.Dictionary({"/P1": pat})})

        gs = pikepdf.Dictionary({"/Type": pikepdf.Name("/ExtGState")})

        # Intentionally passing identical ExtGState objects natively tests dedup skips
        doc.pages[0].Resources = pikepdf.Dictionary(
            {
                "/XObject": pikepdf.Dictionary({"/X1": xobj}),
                "/Pattern": pikepdf.Dictionary({"/P1": pat}),
                "/ExtGState": pikepdf.Dictionary({"/GS1": gs, "/GS2": gs}),
            }
        )

        fonts = list(get_all_fonts_recursive(doc))
        assert len(fonts) == 0

    def test_get_all_fonts_page_indices(self):
        """Test specific explicit page indexing arguments."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        doc.add_blank_page()

        font = pikepdf.Dictionary({"/Type": "/Font"})
        doc.pages[1].Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F2": font})})

        # `page_indices` is expected to be 1-indexed according to standard PDF parsing logic
        fonts = list(get_all_fonts_recursive(doc, page_indices=[2]))
        assert len(fonts) == 1
        assert fonts[0][2] == 2

    def test_get_all_fonts_page_error(self, caplog):
        """Test safely logging and continuing when page index traversal throws."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        with caplog.at_level(logging.WARNING):
            # 999 natively forces IndexError inside the page traversal loop
            fonts = list(get_all_fonts_recursive(doc, page_indices=[999]))

        assert len(fonts) == 0
        assert "Error traversing page 999" in caplog.text

    def test_get_all_fonts_skip_non_pikepdf_objects(self):
        """Test robust failure handling for strictly malformed non-pikepdf data within resource blocks."""

        class Res(dict):
            @property
            def Font(self):
                return {"/F1": "string"}

            @property
            def XObject(self):
                return {"/X1": "string"}

            @property
            def Pattern(self):
                return {"/P1": "string"}

            @property
            def ExtGState(self):
                return {"/GS1": "string"}

        class Page(dict):
            @property
            def Resources(self):
                return Res({"/Font": True, "/XObject": True, "/Pattern": True, "/ExtGState": True})

        class MockDoc:
            pages = [Page({"/Resources": True})]

        # Natively triggers `isinstance(..., pikepdf.Object)` skips across all 4 container checks
        fonts = list(get_all_fonts_recursive(MockDoc()))
        assert len(fonts) == 0

    def test_walk_container_missing_resources(self):
        """Test fast-exit skip logic when the given container entirely lacks resource definitions."""

        class BadPage:
            pass  # Lacks .get() and .Resources natively

        class MockDoc:
            pages = [BadPage()]

        fonts = list(get_all_fonts_recursive(MockDoc()))
        assert len(fonts) == 0
