"""Tests for pdftl.utils.pdf_resources"""

import io
import logging
from unittest.mock import MagicMock

import pikepdf

from pdftl.utils.pdf_resources import (
    _next_xobject_match,
    _iter_appearance_streams,
    unshare_resources_key,
    find_resource_recursive,
    get_all_fonts_recursive,
    walk_content_streams,
    walk_content_streams_deduped,
    StreamContext,
    ensure_page_resources,
    _yield_immediate_fonts,
)


class TestUnshareResourcesKey:
    """Tests for unshare_resources_key's create-vs-copy branches."""

    def test_creates_empty_dict_when_key_absent(self):
        """Test the key-not-present branch creates a fresh empty Dictionary."""
        resources = pikepdf.Dictionary({})
        result = unshare_resources_key(resources, "/XObject")

        assert "/XObject" in resources
        assert isinstance(result, pikepdf.Dictionary)
        assert len(result.keys()) == 0

    def test_copies_existing_dict_when_key_present(self):
        """Test the key-present branch forks a private copy rather than
        aliasing the original shared dictionary."""
        shared = pikepdf.Dictionary({"/F1": pikepdf.Dictionary({"/Type": "/Font"})})
        resources = pikepdf.Dictionary({"/Font": shared})

        result = unshare_resources_key(resources, "/Font")

        assert "/F1" in result
        assert result is not shared


class TestPdfResources:
    # --- find_resource_recursive & helpers ---

    def test_find_resource_no_resources(self):
        """Test returning None when no /Resources dict is present on page."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        if "/Resources" in doc.pages[0]:
            del doc.pages[0].Resources
        assert find_resource_recursive(doc, "/Font", "/F1") is None

    # --- annotation appearance stream fonts ---

    def test_get_all_fonts_annotation_appearance_stream(self):
        """Test extracting fonts used only inside an annotation's /AP /N form,
        not present anywhere in the page's own /Resources/Font dict."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        font = pikepdf.Dictionary({"/Type": "/Font", "/BaseFont": "/Arimo"})

        ap_form = doc.make_stream(b"")
        ap_form.Subtype = pikepdf.Name("/Form")
        ap_form.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F2": font})})

        annot = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Annot"),
                "/Subtype": pikepdf.Name("/FreeText"),
                "/AP": pikepdf.Dictionary({"/N": ap_form}),
            }
        )

        doc.pages[0].Annots = pikepdf.Array([annot])
        # Page resources deliberately have no /Font at all.

        fonts = list(get_all_fonts_recursive(doc))
        assert len(fonts) == 1
        assert fonts[0][0] == "/F2"
        assert fonts[0][2] == 1

    def test_get_all_fonts_annotation_appearance_state_dict(self):
        """Test /AP /N as a sub-dictionary keyed by appearance state (e.g. /Off, /On)."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        font = pikepdf.Dictionary({"/Type": "/Font", "/BaseFont": "/Helv"})

        on_form = doc.make_stream(b"")
        on_form.Subtype = pikepdf.Name("/Form")
        on_form.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})

        annot = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Annot"),
                "/Subtype": pikepdf.Name("/Widget"),
                "/AP": pikepdf.Dictionary({"/N": pikepdf.Dictionary({"/On": on_form})}),
            }
        )

        doc.pages[0].Annots = pikepdf.Array([annot])

        fonts = list(get_all_fonts_recursive(doc))
        assert len(fonts) == 1
        assert fonts[0][0] == "/F1"

    def test_get_all_fonts_annotation_no_annots(self):
        """Test pages with no /Annots key are unaffected."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        fonts = list(get_all_fonts_recursive(doc))
        assert len(fonts) == 0

    # --- _iter_appearance_streams direct unit tests ---

    def test_iter_appearance_streams_non_object_entry(self):
        """Test a non-pikepdf.Object ap_entry (e.g. a plain str) yields nothing."""
        assert list(_iter_appearance_streams("just_a_string")) == []

    def test_iter_appearance_streams_skips_non_object_state_values(self):
        """Test dict-of-states entries whose values aren't pikepdf.Object are
        skipped without yielding, and the loop continues to completion."""
        fake_entry = MagicMock(spec=pikepdf.Object)
        fake_entry.get.return_value = None  # not /Form -- falls into state-dict branch
        fake_entry.items.return_value = [("/Off", "not_a_pikepdf_object")]

        assert list(_iter_appearance_streams(fake_entry)) == []

    def test_iter_appearance_streams_direct_stream_yields_none_state(self):
        """Test a direct-stream /AP entry (no state sub-dictionary) yields
        (None, stream) -- state_name is None to distinguish from a real
        state key like '/On'."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        form = doc.make_stream(b"")
        form.Subtype = pikepdf.Name("/Form")

        results = list(_iter_appearance_streams(form))
        assert results == [(None, form)]

    def test_iter_appearance_streams_state_dict_yields_state_key(self):
        """Test a state-dictionary /AP entry yields (state_key, stream)
        pairs with the actual state name, not None."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        on_form = doc.make_stream(b"")
        on_form.Subtype = pikepdf.Name("/Form")
        state_dict = pikepdf.Dictionary({"/On": on_form})

        results = list(_iter_appearance_streams(state_dict))
        assert results == [("/On", on_form)]

    def test_get_all_fonts_annotation_missing_ap(self):
        """Test annotations lacking /AP entirely are skipped without error."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        annot = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Annot"),
                "/Subtype": pikepdf.Name("/FreeText"),
                # Deliberately no /AP key
            }
        )
        doc.pages[0].Annots = pikepdf.Array([annot])

        fonts = list(get_all_fonts_recursive(doc))
        assert len(fonts) == 0

    def test_get_all_fonts_annotation_non_object_entry(self):
        """Test /Annots arrays containing non-pikepdf-object entries are skipped."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        # A raw string in the Annots array can't be isinstance-checked as a
        # pikepdf.Object with /AP, so it must be safely skipped.
        doc.pages[0].Annots = pikepdf.Array(["not_an_annotation"])

        fonts = list(get_all_fonts_recursive(doc))
        assert len(fonts) == 0

    def test_get_all_fonts_annotation_dedup_shared_appearance(self):
        """Test two annotations referencing the *same* AP form only yield fonts once."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        font = pikepdf.Dictionary({"/Type": "/Font", "/BaseFont": "/Helv"})

        shared_form = doc.make_stream(b"")
        shared_form.Subtype = pikepdf.Name("/Form")
        shared_form.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})

        annot1 = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Annot"),
                "/Subtype": pikepdf.Name("/Widget"),
                "/AP": pikepdf.Dictionary({"/N": shared_form}),
            }
        )
        annot2 = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Annot"),
                "/Subtype": pikepdf.Name("/Widget"),
                "/AP": pikepdf.Dictionary({"/N": shared_form, "/D": shared_form}),
            }
        )

        doc.pages[0].Annots = pikepdf.Array([annot1, annot2])

        fonts = list(get_all_fonts_recursive(doc))
        # Same underlying stream objgen visited only once, despite three references.
        assert len(fonts) == 1
        assert fonts[0][0] == "/F1"

    def test_get_all_fonts_annotation_iteration_exception(self, caplog):
        """Test malformed /Annots (non-iterable) is caught by the suppress block
        and doesn't blow up font extraction for the rest of the page."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        font = pikepdf.Dictionary({"/Type": "/Font", "/BaseFont": "/Helv"})
        doc.pages[0].Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})
        # /Annots present but not an array -- iterating raises inside the
        # suppress(...) block in _yield_annotation_fonts.
        doc.pages[0].Annots = pikepdf.Dictionary({"/Bogus": True})

        fonts = list(get_all_fonts_recursive(doc))
        # Page-level font extraction still succeeds; annotation walk fails silently.
        assert len(fonts) == 1
        assert fonts[0][0] == "/F1"

    def test_next_xobject_match_all_non_form_exhausts_loop(self):
        """Test the loop iterates past every non-/Form XObject and falls through
        to return None, exercising the false branch of the /Subtype check on
        every iteration (no early return via a matching /Form entry)."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        img1 = doc.make_stream(b"")
        img1.Subtype = pikepdf.Name("/Image")

        img2 = doc.make_stream(b"")
        img2.Subtype = pikepdf.Name("/Image")

        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/Img1": img1, "/Img2": img2})}
        )

        assert find_resource_recursive(doc, "/Font", "/F1") is None

    def test_next_xobject_match_skips_non_form_then_matches(self):
        """Test the XObject loop continues past a non-/Form entry to find a later match."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        font = pikepdf.Dictionary({"/Type": "/Font"})

        not_form = doc.make_stream(b"")
        not_form.Subtype = pikepdf.Name("/Image")  # not /Form -- loop must skip and continue

        form = doc.make_stream(b"")
        form.Subtype = pikepdf.Name("/Form")
        form.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})

        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/Img": not_form, "/Frm": form})}
        )

        assert find_resource_recursive(doc, "/Font", "/F1") is not None

    def test_get_all_fonts_xobject_skips_non_form_then_matches(self):
        """Test _yield_xobject_fonts continues past a non-/Form XObject to a later Form one."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        font = pikepdf.Dictionary({"/Type": "/Font"})

        not_form = doc.make_stream(b"")
        not_form.Subtype = pikepdf.Name("/Image")

        form = doc.make_stream(b"")
        form.Subtype = pikepdf.Name("/Form")
        form.Resources = pikepdf.Dictionary({"/Font": pikepdf.Dictionary({"/F1": font})})

        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/Img": not_form, "/Frm": form})}
        )

        fonts = list(get_all_fonts_recursive(doc))
        assert len(fonts) == 1
        assert fonts[0][0] == "/F1"

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


class TestWalkContentStreams:
    """Tests for the canonical walk_content_streams recursive walker."""

    def _make_form(self, doc, content=b"", resources=None):
        form = doc.make_stream(content)
        form.Subtype = pikepdf.Name("/Form")
        if resources is not None:
            form.Resources = resources
        return form

    # --- basic page-level ---

    def test_walk_page_contents_only(self):
        """Test a bare page with a content stream and no nested resources
        yields exactly the page stream, kind='page', depth=0."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        doc.pages[0].Contents = doc.make_stream(b"1 0 0 RG")

        results = list(walk_content_streams(doc))
        assert len(results) == 1
        stream, ctx = results[0]
        assert ctx.kind == "page"
        assert ctx.depth == 0
        assert ctx.page_num == 1

    def test_walk_no_contents_no_resources(self):
        """Test a page with neither /Contents nor /Resources yields nothing."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        if "/Contents" in doc.pages[0]:
            del doc.pages[0].Contents
        if "/Resources" in doc.pages[0]:
            del doc.pages[0].Resources

        results = list(walk_content_streams(doc))
        assert [ctx.kind for _, ctx in results] == []

    # --- Form XObject recursion ---

    def test_walk_form_xobject_recursive(self):
        """Test a Form XObject is yielded with kind='form', depth=1, and its
        own nested Form (depth 2) is also yielded."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        inner = self._make_form(doc, b"inner")
        outer_res = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/In": inner})})
        outer = self._make_form(doc, b"outer", resources=outer_res)

        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/Out": outer})}
        )

        results = list(walk_content_streams(doc))
        kinds_depths = sorted((ctx.kind, ctx.depth) for _, ctx in results)
        assert kinds_depths == [("form", 1), ("form", 2), ("page", 0)]

    def test_walk_form_skips_non_form_xobject(self):
        """Test Image XObjects are not yielded as content streams."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        img = doc.make_stream(b"\xff")
        img.Subtype = pikepdf.Name("/Image")

        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/Img": img})}
        )

        results = list(walk_content_streams(doc))
        assert [ctx.kind for _, ctx in results] == ["page"]

    def test_walk_form_dedup_shared_xobject(self):
        """Test the same Form referenced twice on one page is yielded once."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        shared = self._make_form(doc, b"shared")
        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/A": shared, "/B": shared})}
        )

        results = list(walk_content_streams(doc))
        assert len([r for r in results if r[1].kind == "form"]) == 1

    def test_walk_form_self_reference_no_infinite_loop(self):
        """Test a self-referencing Form XObject terminates via the visited set."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        form = self._make_form(doc, b"self")
        form.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/Self": form})})

        doc.pages[0].Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/F": form})})

        results = list(walk_content_streams(doc))
        assert len([r for r in results if r[1].kind == "form"]) == 1

    # --- Pattern recursion ---

    def test_walk_tiling_pattern_yielded(self):
        """Test a type-1 tiling Pattern's content stream is yielded, kind='pattern'."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        pat = doc.make_stream(b"pattern content")
        pat.PatternType = 1

        doc.pages[0].Resources = pikepdf.Dictionary({"/Pattern": pikepdf.Dictionary({"/P1": pat})})

        results = list(walk_content_streams(doc))
        kinds = sorted(ctx.kind for _, ctx in results)
        assert kinds == ["page", "pattern"]
        pattern_ctx = next(ctx for _, ctx in results if ctx.kind == "pattern")
        assert pattern_ctx.owner_key == "/P1"
        assert pattern_ctx.owner_resources is not None

    def test_walk_shading_pattern_skipped(self):
        """Test a type-2 shading Pattern (no content stream) is not yielded."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        pat = doc.make_stream(b"")
        pat.PatternType = 2

        doc.pages[0].Resources = pikepdf.Dictionary({"/Pattern": pikepdf.Dictionary({"/P1": pat})})

        results = list(walk_content_streams(doc))
        assert [ctx.kind for _, ctx in results] == ["page"]

    def test_walk_pattern_malformed_type_skipped(self):
        """Test a Pattern with a non-integer /PatternType is safely skipped."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        pat = doc.make_stream(b"")
        pat.PatternType = pikepdf.Name("/Bogus")

        doc.pages[0].Resources = pikepdf.Dictionary({"/Pattern": pikepdf.Dictionary({"/P1": pat})})

        results = list(walk_content_streams(doc))
        assert [ctx.kind for _, ctx in results] == ["page"]

    # --- ExtGState /SMask recursion ---

    def test_walk_smask_group_yielded(self):
        """Test an ExtGState /SMask /G Form group is recursed into, kind='smask'."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        group = self._make_form(doc, b"mask content")
        group.Group = pikepdf.Dictionary({"/S": pikepdf.Name("/Transparency")})
        smask = pikepdf.Dictionary({"/Type": pikepdf.Name("/Mask"), "/G": group})
        gs = pikepdf.Dictionary({"/Type": pikepdf.Name("/ExtGState"), "/SMask": smask})

        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/ExtGState": pikepdf.Dictionary({"/GS1": gs})}
        )

        results = list(walk_content_streams(doc))
        kinds = sorted(ctx.kind for _, ctx in results)
        assert kinds == ["page", "smask"]
        smask_ctx = next(ctx for _, ctx in results if ctx.kind == "smask")
        assert smask_ctx.owner_key == "/GS1"
        assert smask_ctx.owner_resources is None

    def test_walk_smask_none_skipped(self):
        """Test /SMask == /None (the common no-op case) is skipped without error."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        gs = pikepdf.Dictionary(
            {"/Type": pikepdf.Name("/ExtGState"), "/SMask": pikepdf.Name("/None")}
        )
        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/ExtGState": pikepdf.Dictionary({"/GS1": gs})}
        )

        results = list(walk_content_streams(doc))
        assert [ctx.kind for _, ctx in results] == ["page"]

    def test_walk_extgstate_no_smask_key(self):
        """Test ExtGState dicts without /SMask at all are handled cleanly."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        gs = pikepdf.Dictionary({"/Type": pikepdf.Name("/ExtGState")})
        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/ExtGState": pikepdf.Dictionary({"/GS1": gs})}
        )

        results = list(walk_content_streams(doc))
        assert [ctx.kind for _, ctx in results] == ["page"]

    # --- Annotation /AP recursion ---

    def test_walk_annotation_appearance_stream(self):
        """Test an annotation's /AP /N stream is yielded, kind='annotation'."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        ap_form = self._make_form(doc, b"annot content")
        annot = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Annot"),
                "/Subtype": pikepdf.Name("/FreeText"),
                "/AP": pikepdf.Dictionary({"/N": ap_form}),
            }
        )
        doc.pages[0].Annots = pikepdf.Array([annot])

        results = list(walk_content_streams(doc))
        kinds = sorted(ctx.kind for _, ctx in results)
        assert kinds == ["annotation", "page"]
        annot_ctx = next(ctx for _, ctx in results if ctx.kind == "annotation")
        assert annot_ctx.annot_index == 0
        assert annot_ctx.ap_key == "/N"
        assert annot_ctx.ap_state is None

    def test_walk_annotation_nested_form_recursion(self):
        """Test a Form nested inside an annotation's AP stream resources is
        also recursed into (depth increments past the annotation stream)."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        nested = self._make_form(doc, b"nested")
        ap_res = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/N1": nested})})
        ap_form = self._make_form(doc, b"annot content", resources=ap_res)

        annot = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Annot"),
                "/Subtype": pikepdf.Name("/FreeText"),
                "/AP": pikepdf.Dictionary({"/N": ap_form}),
            }
        )
        doc.pages[0].Annots = pikepdf.Array([annot])

        results = list(walk_content_streams(doc))
        kinds = sorted(ctx.kind for _, ctx in results)
        assert kinds == ["annotation", "form", "page"]

    def test_walk_annotation_no_annots(self):
        """Test pages with no /Annots key are unaffected."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        results = list(walk_content_streams(doc))
        assert [ctx.kind for _, ctx in results] == ["page"]

    def test_walk_annotation_missing_ap(self):
        """Test annotations lacking /AP entirely are skipped without error."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        annot = pikepdf.Dictionary(
            {"/Type": pikepdf.Name("/Annot"), "/Subtype": pikepdf.Name("/FreeText")}
        )
        doc.pages[0].Annots = pikepdf.Array([annot])

        results = list(walk_content_streams(doc))
        assert [ctx.kind for _, ctx in results] == ["page"]

    def test_walk_annotation_non_object_entry(self):
        """Test /Annots arrays containing non-object entries are skipped."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        doc.pages[0].Annots = pikepdf.Array(["not_an_annotation"])

        results = list(walk_content_streams(doc))
        assert [ctx.kind for _, ctx in results] == ["page"]

    def test_walk_annotation_malformed_annots_suppressed(self):
        """Test a malformed (non-iterable) /Annots value is caught by the
        suppress block and doesn't blow up the rest of the walk."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        doc.pages[0].Contents = doc.make_stream(b"page content")
        doc.pages[0].Annots = pikepdf.Dictionary({"/Bogus": True})

        results = list(walk_content_streams(doc))
        assert len(results) == 1
        assert results[0][1].kind == "page"

    # --- combined / cross-cutting ---

    def test_walk_page_indices_filter(self):
        """Test only the requested page_indices are walked."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        doc.add_blank_page()
        doc.pages[0].Contents = doc.make_stream(b"page1")
        doc.pages[1].Contents = doc.make_stream(b"page2")

        results = list(walk_content_streams(doc, page_indices=[2]))
        assert len(results) == 1
        assert results[0][1].page_num == 2

    def test_walk_page_out_of_range_logged(self, caplog):
        """Test an out-of-range page index is logged and skipped, not raised."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        with caplog.at_level(logging.WARNING):
            results = list(walk_content_streams(doc, page_indices=[999]))

        assert results == []
        assert "walk_content_streams: page 999 out of range" in caplog.text

    def test_walk_combined_all_kinds_one_page(self):
        """Test a page combining a Form, a Pattern, an SMask group, and an
        annotation AP stream all in one Resources dict yields all four kinds
        with correct dedup and no cross-contamination."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        doc.pages[0].Contents = doc.make_stream(b"page content")

        form = self._make_form(doc, b"form content")

        pat = doc.make_stream(b"pattern content")
        pat.PatternType = 1

        smask_group = self._make_form(doc, b"mask content")
        smask = pikepdf.Dictionary({"/Type": pikepdf.Name("/Mask"), "/G": smask_group})
        gs = pikepdf.Dictionary({"/Type": pikepdf.Name("/ExtGState"), "/SMask": smask})

        doc.pages[0].Resources = pikepdf.Dictionary(
            {
                "/XObject": pikepdf.Dictionary({"/F1": form}),
                "/Pattern": pikepdf.Dictionary({"/P1": pat}),
                "/ExtGState": pikepdf.Dictionary({"/GS1": gs}),
            }
        )

        ap_form = self._make_form(doc, b"annot content")
        annot = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Annot"),
                "/Subtype": pikepdf.Name("/FreeText"),
                "/AP": pikepdf.Dictionary({"/N": ap_form}),
            }
        )
        doc.pages[0].Annots = pikepdf.Array([annot])

        results = list(walk_content_streams(doc))
        kinds = sorted(ctx.kind for _, ctx in results)
        assert kinds == ["annotation", "form", "page", "pattern", "smask"]

    def test_stream_context_is_dataclass_with_expected_fields(self):
        """Test StreamContext exposes the expected attribute surface."""
        ctx = StreamContext(page_num=1, depth=0, kind="page", resources=None)
        assert ctx.page_num == 1
        assert ctx.depth == 0
        assert ctx.kind == "page"
        assert ctx.resources is None

    def test_walk_pattern_dedup_shared_pattern(self):
        """Test the same Pattern referenced under two names is only yielded
        once, covering the already-visited continue branch."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        pat = doc.make_stream(b"pattern content")
        pat.PatternType = 1

        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/Pattern": pikepdf.Dictionary({"/P1": pat, "/P2": pat})}
        )

        results = list(walk_content_streams(doc))
        assert len([r for r in results if r[1].kind == "pattern"]) == 1

    def test_walk_pattern_with_own_resources_recurses(self):
        """Test a Pattern with its own /Resources dict recurses into a
        nested Form XObject, covering the pattern-resources recursion call."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        nested = self._make_form(doc, b"nested in pattern")
        pat_res = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/N1": nested})})
        pat = doc.make_stream(b"pattern content")
        pat.PatternType = 1
        pat.Resources = pat_res

        doc.pages[0].Resources = pikepdf.Dictionary({"/Pattern": pikepdf.Dictionary({"/P1": pat})})

        results = list(walk_content_streams(doc))
        kinds = sorted(ctx.kind for _, ctx in results)
        assert kinds == ["form", "page", "pattern"]

    def test_walk_extgstate_dedup_shared_gs(self):
        """Test the same ExtGState dict referenced under two names is only
        processed once, covering the already-visited continue branch."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        group = self._make_form(doc, b"mask content")
        smask = pikepdf.Dictionary({"/Type": pikepdf.Name("/Mask"), "/G": group})
        gs = pikepdf.Dictionary({"/Type": pikepdf.Name("/ExtGState"), "/SMask": smask})

        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/ExtGState": pikepdf.Dictionary({"/GS1": gs, "/GS2": gs})}
        )

        results = list(walk_content_streams(doc))
        assert len([r for r in results if r[1].kind == "smask"]) == 1

    def test_walk_smask_group_dedup_shared_group(self):
        """Test two distinct ExtGStates whose /SMask /G points to the same
        Form group only yield the smask group once, covering the
        already-visited continue on the group objgen check."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        group = self._make_form(doc, b"shared mask content")
        smask1 = pikepdf.Dictionary({"/Type": pikepdf.Name("/Mask"), "/G": group})
        smask2 = pikepdf.Dictionary({"/Type": pikepdf.Name("/Mask"), "/G": group})
        gs1 = pikepdf.Dictionary({"/Type": pikepdf.Name("/ExtGState"), "/SMask": smask1})
        gs2 = pikepdf.Dictionary({"/Type": pikepdf.Name("/ExtGState"), "/SMask": smask2})

        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/ExtGState": pikepdf.Dictionary({"/GS1": gs1, "/GS2": gs2})}
        )

        results = list(walk_content_streams(doc))
        assert len([r for r in results if r[1].kind == "smask"]) == 1

    def test_walk_smask_group_not_pikepdf_object(self):
        """Test /SMask /G that isn't a valid pikepdf.Object (malformed) is
        skipped without error, covering the isinstance-false half of the
        compound continue condition (as opposed to the already-visited half)."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        smask = pikepdf.Dictionary({"/Type": pikepdf.Name("/Mask"), "/G": 123})
        gs = pikepdf.Dictionary({"/Type": pikepdf.Name("/ExtGState"), "/SMask": smask})

        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/ExtGState": pikepdf.Dictionary({"/GS1": gs})}
        )

        results = list(walk_content_streams(doc))
        assert [ctx.kind for _, ctx in results] == ["page"]

    def test_walk_smask_group_with_own_resources_recurses(self):
        """Test an SMask group with its own /Resources dict recurses into a
        nested Form XObject, covering the smask-resources recursion call."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        nested = self._make_form(doc, b"nested in smask")
        group_res = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/N1": nested})})
        group = self._make_form(doc, b"mask content", resources=group_res)
        smask = pikepdf.Dictionary({"/Type": pikepdf.Name("/Mask"), "/G": group})
        gs = pikepdf.Dictionary({"/Type": pikepdf.Name("/ExtGState"), "/SMask": smask})

        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/ExtGState": pikepdf.Dictionary({"/GS1": gs})}
        )

        results = list(walk_content_streams(doc))
        kinds = sorted(ctx.kind for _, ctx in results)
        assert kinds == ["form", "page", "smask"]

    def test_walk_annotation_dedup_shared_appearance_stream(self):
        """Test the same appearance stream referenced under both /N and /D
        on one annotation is only yielded once, covering the already-visited
        continue branch in _walk_annotation_streams."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        shared_form = self._make_form(doc, b"shared annot content")
        annot = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Annot"),
                "/Subtype": pikepdf.Name("/Widget"),
                "/AP": pikepdf.Dictionary({"/N": shared_form, "/D": shared_form}),
            }
        )
        doc.pages[0].Annots = pikepdf.Array([annot])

        results = list(walk_content_streams(doc))
        assert len([r for r in results if r[1].kind == "annotation"]) == 1

    def test_walk_annotation_appearance_state_dict_records_ap_state(self):
        """Test an /AP /N entry that's a state sub-dictionary (e.g. /Off,
        /On) records the state key in ctx.ap_state, distinguishing it from
        the direct-stream case where ap_state is None."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        on_form = self._make_form(doc, b"on content")
        annot = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Annot"),
                "/Subtype": pikepdf.Name("/Widget"),
                "/AP": pikepdf.Dictionary({"/N": pikepdf.Dictionary({"/On": on_form})}),
            }
        )
        doc.pages[0].Annots = pikepdf.Array([annot])

        results = list(walk_content_streams(doc))
        annot_ctx = next(ctx for _, ctx in results if ctx.kind == "annotation")
        assert annot_ctx.ap_state == "/On"
        assert annot_ctx.ap_key == "/N"

    def test_walk_annotation_index_tracks_position_in_annots_array(self):
        """Test the second annotation in /Annots records annot_index=1,
        not 0, confirming index tracking survives skipped (non-AP)
        annotations before it."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        no_ap_annot = pikepdf.Dictionary(
            {"/Type": pikepdf.Name("/Annot"), "/Subtype": pikepdf.Name("/FreeText")}
        )
        ap_form = self._make_form(doc, b"second annot content")
        second_annot = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Annot"),
                "/Subtype": pikepdf.Name("/FreeText"),
                "/AP": pikepdf.Dictionary({"/N": ap_form}),
            }
        )
        doc.pages[0].Annots = pikepdf.Array([no_ap_annot, second_annot])

        results = list(walk_content_streams(doc))
        annot_ctx = next(ctx for _, ctx in results if ctx.kind == "annotation")
        assert annot_ctx.annot_index == 1

    def test_walk_form_owner_key_and_resources_populated(self):
        """Test kind='form' StreamContext carries the /Resources dict that
        holds the /XObject entry and the entry's key name, needed for
        breadcrumb-building and in-place unsharing by other callers."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        form = self._make_form(doc, b"form content")
        page_resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/Fm7": form})})
        doc.pages[0].Resources = page_resources

        results = list(walk_content_streams(doc))
        form_ctx = next(ctx for _, ctx in results if ctx.kind == "form")
        assert form_ctx.owner_key == "/Fm7"
        assert form_ctx.owner_resources is not None

    def test_walk_xobject_forms_skips_non_pikepdf_object(self):
        """Test the isinstance-false branch in _walk_xobject_forms."""
        from pdftl.utils.pdf_resources import _walk_xobject_forms

        doc = pikepdf.Pdf.new()
        good = self._make_form(doc, b"good content")

        class MockResources(dict):
            @property
            def XObject(self):
                return {"/Bad": "not_a_pikepdf_object", "/Good": good}

        mock_res = MockResources({"/XObject": True})
        results = list(_walk_xobject_forms(mock_res, page_num=1, depth=1, visited=set()))

        assert len(results) == 1
        assert results[0][1].kind == "form"

    def test_walk_tiling_patterns_skips_non_pikepdf_object(self):
        """Test the isinstance-false branch in _walk_tiling_patterns."""
        from pdftl.utils.pdf_resources import _walk_tiling_patterns

        doc = pikepdf.Pdf.new()
        good_pat = doc.make_stream(b"pattern content")
        good_pat.PatternType = 1

        class MockResources(dict):
            @property
            def Pattern(self):
                return {"/Bad": "not_a_pikepdf_object", "/Good": good_pat}

        mock_res = MockResources({"/Pattern": True})
        results = list(_walk_tiling_patterns(mock_res, page_num=1, depth=1, visited=set()))

        assert len(results) == 1
        assert results[0][1].kind == "pattern"

    def test_walk_extgstate_smasks_skips_non_pikepdf_object(self):
        """Test the isinstance-false branch in _walk_extgstate_smasks."""
        from pdftl.utils.pdf_resources import _walk_extgstate_smasks

        doc = pikepdf.Pdf.new()
        group = self._make_form(doc, b"mask content")
        smask = pikepdf.Dictionary({"/Type": pikepdf.Name("/Mask"), "/G": group})
        good_gs = pikepdf.Dictionary({"/Type": pikepdf.Name("/ExtGState"), "/SMask": smask})

        class MockResources(dict):
            @property
            def ExtGState(self):
                return {"/Bad": "not_a_pikepdf_object", "/Good": good_gs}

        mock_res = MockResources({"/ExtGState": True})
        results = list(_walk_extgstate_smasks(mock_res, page_num=1, depth=1, visited=set()))

        assert len(results) == 1
        assert results[0][1].kind == "smask"

    def test_walk_yield_duplicates_form_referenced_twice(self):
        """Test a shared Form referenced under two names yields twice with
        yield_duplicates=True, once (default) with yield_duplicates=False."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        shared = self._make_form(doc, b"shared")
        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/A": shared, "/B": shared})}
        )

        default_results = list(walk_content_streams(doc))
        dup_results = list(walk_content_streams(doc, yield_duplicates=True))

        assert len([r for r in default_results if r[1].kind == "form"]) == 1
        assert len([r for r in dup_results if r[1].kind == "form"]) == 2

    def test_walk_yield_duplicates_pattern_referenced_twice(self):
        """Test a shared Pattern referenced twice yields twice with
        yield_duplicates=True."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        pat = doc.make_stream(b"pattern content")
        pat.PatternType = 1
        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/Pattern": pikepdf.Dictionary({"/P1": pat, "/P2": pat})}
        )

        results = list(walk_content_streams(doc, yield_duplicates=True))
        assert len([r for r in results if r[1].kind == "pattern"]) == 2

    def test_walk_yield_duplicates_smask_shared_group_referenced_twice(self):
        """Test two ExtGStates whose /SMask /G points to the same group
        both yield the group with yield_duplicates=True."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        group = self._make_form(doc, b"shared mask content")
        smask1 = pikepdf.Dictionary({"/Type": pikepdf.Name("/Mask"), "/G": group})
        smask2 = pikepdf.Dictionary({"/Type": pikepdf.Name("/Mask"), "/G": group})
        gs1 = pikepdf.Dictionary({"/Type": pikepdf.Name("/ExtGState"), "/SMask": smask1})
        gs2 = pikepdf.Dictionary({"/Type": pikepdf.Name("/ExtGState"), "/SMask": smask2})

        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/ExtGState": pikepdf.Dictionary({"/GS1": gs1, "/GS2": gs2})}
        )

        results = list(walk_content_streams(doc, yield_duplicates=True))
        assert len([r for r in results if r[1].kind == "smask"]) == 2

    def test_walk_yield_duplicates_annotation_shared_ap_referenced_twice(self):
        """Test the same appearance stream under /N and /D on one
        annotation yields twice with yield_duplicates=True."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        shared_form = self._make_form(doc, b"shared annot content")
        annot = pikepdf.Dictionary(
            {
                "/Type": pikepdf.Name("/Annot"),
                "/Subtype": pikepdf.Name("/Widget"),
                "/AP": pikepdf.Dictionary({"/N": shared_form, "/D": shared_form}),
            }
        )
        doc.pages[0].Annots = pikepdf.Array([annot])

        results = list(walk_content_streams(doc, yield_duplicates=True))
        assert len([r for r in results if r[1].kind == "annotation"]) == 2

    def test_walk_yield_duplicates_self_referencing_form_terminates(self):
        """Test a self-referencing Form still terminates (doesn't infinite
        loop) even with yield_duplicates=True -- it may be yielded twice
        (once from the page, once again as its own unresolved self-ref)
        but must not recurse a third time."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        form = self._make_form(doc, b"self")
        form.Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/Self": form})})
        doc.pages[0].Resources = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/F": form})})

        results = list(walk_content_streams(doc, yield_duplicates=True))
        form_results = [r for r in results if r[1].kind == "form"]
        assert 1 <= len(form_results) <= 2

    def test_walk_yield_duplicates_does_not_recurse_into_second_occurrence(self):
        """Test that a shared Form's own nested Form is only ever yielded
        once, even when the outer Form itself is yielded twice under
        yield_duplicates=True -- confirms the second occurrence doesn't
        re-descend and duplicate the child."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()

        inner = self._make_form(doc, b"inner")
        outer_res = pikepdf.Dictionary({"/XObject": pikepdf.Dictionary({"/In": inner})})
        outer = self._make_form(doc, b"outer", resources=outer_res)

        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/A": outer, "/B": outer})}
        )

        results = list(walk_content_streams(doc, yield_duplicates=True))
        outer_hits = [r for r in results if r[1].kind == "form" and r[0].objgen == outer.objgen]
        inner_hits = [r for r in results if r[1].kind == "form" and r[0].objgen == inner.objgen]
        assert len(outer_hits) == 2
        assert len(inner_hits) == 1


class TestWalkContentStreamsDeduped:
    """Tests for the walk_content_streams_deduped cross-call dedup wrapper."""

    def _make_form(self, doc, content=b"", resources=None):
        form = doc.make_stream(content)
        form.Subtype = pikepdf.Name("/Form")
        if resources is not None:
            form.Resources = resources
        return form

    def test_fresh_seen_set_matches_undeduped_walk(self):
        """Test that with an empty `seen` set, results match a plain
        walk_content_streams call one-for-one (no cross-call state yet)."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        doc.pages[0].Contents = doc.make_stream(b"page content")

        form = self._make_form(doc, b"form content")
        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/F1": form})}
        )

        plain = list(walk_content_streams(doc))
        deduped = list(walk_content_streams_deduped(doc, None, set()))

        assert [ctx.kind for _, ctx in deduped] == [ctx.kind for _, ctx in plain]

    def test_seen_set_populated_after_call(self):
        """Test every yielded stream's objgen ends up in `seen`."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        doc.pages[0].Contents = doc.make_stream(b"page content")

        form = self._make_form(doc, b"form content")
        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/F1": form})}
        )

        seen = set()
        results = list(walk_content_streams_deduped(doc, None, seen))

        assert seen == {stream.objgen for stream, _ in results}

    def test_preseeded_objgen_is_skipped(self):
        """Test a stream whose objgen is already in `seen` before the call
        is never yielded, simulating a shared Form already processed on a
        prior page."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        doc.pages[0].Contents = doc.make_stream(b"page content")

        form = self._make_form(doc, b"form content")
        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/F1": form})}
        )

        seen = {form.objgen}
        results = list(walk_content_streams_deduped(doc, None, seen))

        assert [ctx.kind for _, ctx in results] == ["page"]

    def test_shared_form_across_two_pages_yielded_once(self):
        """Test the primary use case: a Form XObject shared by two pages is
        only yielded on the first page when the same `seen` set is reused
        across two separate calls (one per page)."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        doc.add_blank_page()
        doc.pages[0].Contents = doc.make_stream(b"page1 content")
        doc.pages[1].Contents = doc.make_stream(b"page2 content")

        shared_form = self._make_form(doc, b"shared form content")
        shared_resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/F1": shared_form})}
        )
        doc.pages[0].Resources = shared_resources
        doc.pages[1].Resources = shared_resources

        seen = set()
        page1_results = list(walk_content_streams_deduped(doc, [1], seen))
        page2_results = list(walk_content_streams_deduped(doc, [2], seen))

        assert len([r for r in page1_results if r[1].kind == "form"]) == 1
        assert len([r for r in page2_results if r[1].kind == "form"]) == 0
        assert [ctx.kind for _, ctx in page2_results] == ["page"]

    def test_independent_seen_sets_do_not_interfere(self):
        """Test two separate `seen` sets passed to two separate calls don't
        leak state into each other (sanity check that dedup state lives in
        the caller-supplied set, not anywhere global)."""
        doc = pikepdf.Pdf.new()
        doc.add_blank_page()
        doc.pages[0].Contents = doc.make_stream(b"page content")

        form = self._make_form(doc, b"form content")
        doc.pages[0].Resources = pikepdf.Dictionary(
            {"/XObject": pikepdf.Dictionary({"/F1": form})}
        )

        seen_a = set()
        seen_b = set()
        results_a = list(walk_content_streams_deduped(doc, [1], seen_a))
        results_b = list(walk_content_streams_deduped(doc, [1], seen_b))

        assert len([r for r in results_a if r[1].kind == "form"]) == 1
        assert len([r for r in results_b if r[1].kind == "form"]) == 1


def test_ensure_page_resources_creates_empty_dict():
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    if "/Resources" in page:
        del page["/Resources"]

    res = ensure_page_resources(page)
    assert "/Resources" in page.obj
    assert isinstance(res, pikepdf.Dictionary)


def test_ensure_page_resources_forks_inherited_dict():
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    del page["/Resources"]
    pdf.Root.Pages["/Resources"] = pikepdf.Dictionary(
        {"/Font": pikepdf.Dictionary({"/F1": pikepdf.Dictionary()})}
    )

    # Save and re-open to instantiate parent tree references
    buf = io.BytesIO()
    pdf.save(buf)
    buf.seek(0)

    pdf_reopened = pikepdf.open(buf, inherit_page_attributes=False)
    page_reopened = pdf_reopened.pages[0]

    assert "/Resources" not in page_reopened.obj
    res = ensure_page_resources(page_reopened)

    assert "/Resources" in page_reopened.obj
    assert "/Font" in res


def test_ensure_page_resources_already_present():
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    page.obj["/Resources"] = pikepdf.Dictionary({"/Font": pikepdf.Dictionary()})

    res = ensure_page_resources(page)
    assert res == page.obj["/Resources"]


def test_yield_immediate_fonts_skips_non_pikepdf_objects():
    class MockResources(dict):
        @property
        def Font(self):
            return {"/F1": "not_a_pikepdf_object"}

    mock_res = MockResources({"/Font": True})
    fonts = list(_yield_immediate_fonts(mock_res, page_num=1))
    assert fonts == []
