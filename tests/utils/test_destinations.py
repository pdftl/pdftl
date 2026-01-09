import pikepdf

from pdftl.utils.destinations import ResolvedDest, get_named_destinations, resolve_dest_to_page_num


def test_resolve_dest_explicit_array():
    """Test resolving a direct destination array like [Page, /XYZ, 0, 0, 1]."""
    with pikepdf.new() as pdf:
        pdf.add_blank_page()  # Page 1 (index 0)
        page1 = pdf.pages[0]

        # [PageObject, /Type, args...]
        dest_array = pikepdf.Array([page1.obj, pikepdf.Name("/XYZ"), 0, 100, 1])

        result = resolve_dest_to_page_num(dest_array, pdf.pages, None)

        assert result is not None
        assert result.page_num == 1
        assert result.dest_type == "XYZ"
        assert result.args == [0, 100, 1]


def test_resolve_dest_defaults():
    """Test defaults when destination array is just [Page]."""
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        page1 = pdf.pages[0]

        # Minimal array: [PageObject] -> Should default to XYZ
        dest_array = pikepdf.Array([page1.obj])

        result = resolve_dest_to_page_num(dest_array, pdf.pages, None)

        assert result == ResolvedDest(1, "XYZ", [])


def test_resolve_dest_action_dictionary():
    """Test resolving an Action dictionary containing a /D entry."""
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        page1 = pdf.pages[0]

        dest_array = pikepdf.Array([page1.obj, pikepdf.Name("/Fit")])
        # Action Dictionary: { /S: /GoTo, /D: [Page, /Fit] }
        action_dict = pikepdf.Dictionary({"/S": pikepdf.Name("/GoTo"), "/D": dest_array})

        result = resolve_dest_to_page_num(action_dict, pdf.pages, None)

        assert result == ResolvedDest(1, "Fit", [])


def test_resolve_named_dest_simple():
    """Test resolving a named destination string looking up a direct Array."""
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        page1 = pdf.pages[0]

        dest_array = pikepdf.Array([page1.obj, pikepdf.Name("/FitH"), 500])

        # Mock named destinations (behaving like a dict/NameTree)
        named_dests = {"MyDest": dest_array}

        # Test with string input
        result = resolve_dest_to_page_num("MyDest", pdf.pages, named_dests)
        assert result == ResolvedDest(1, "FitH", [500])

        # Test with Name object input
        result_name = resolve_dest_to_page_num(pikepdf.Name("/MyDest"), pdf.pages, named_dests)
        assert result_name == ResolvedDest(1, "FitH", [500])


def test_resolve_named_dest_nested_dict():
    """Test resolving a named destination that points to a dictionary with /D."""
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        page1 = pdf.pages[0]

        dest_array = pikepdf.Array([page1.obj, pikepdf.Name("/FitV"), 200])
        dest_dict = pikepdf.Dictionary({"/D": dest_array})

        named_dests = {"ComplexDest": dest_dict}

        result = resolve_dest_to_page_num("ComplexDest", pdf.pages, named_dests)
        assert result == ResolvedDest(1, "FitV", [200])


def test_resolve_named_dest_not_found():
    """Test resolving a non-existent named destination."""
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        named_dests = {"Existing": pikepdf.Array([])}

        result = resolve_dest_to_page_num("Missing", pdf.pages, named_dests)
        assert result is None


def test_resolve_dest_page_matching_failure():
    """Test failure when the page object in destination doesn't exist in the PDF."""
    with pikepdf.new() as pdf:
        pdf.add_blank_page()

        # Create a page from a DIFFERENT PDF document
        other_pdf = pikepdf.new()

        # CRITICAL: We must ensure object IDs don't accidentally collide.
        # pikepdf.new() is deterministic. If both PDFs have 1 page,
        # page[0].objgen might be identical (e.g., (10, 0)).
        # We add dummy objects to other_pdf to shift the ID counter.
        other_pdf.make_indirect(pikepdf.Dictionary())
        other_pdf.make_indirect(pikepdf.Dictionary())

        other_pdf.add_blank_page()
        other_page = other_pdf.pages[0]

        dest_array = pikepdf.Array([other_page.obj, pikepdf.Name("/Fit")])

        # Should return None because page objgen won't match any page in `pdf.pages`
        result = resolve_dest_to_page_num(dest_array, pdf.pages, None)
        assert result is None


def test_get_named_destinations_structure():
    """Test the extraction of the NameTree root."""
    with pikepdf.new() as pdf:
        # Default empty PDF has no names
        assert get_named_destinations(pdf) is None

        # Create structure: /Root -> /Names -> /Dests
        pdf.Root.Names = pikepdf.Dictionary()
        assert get_named_destinations(pdf) is None

        # CRITICAL: NameTree root must be an INDIRECT object for pikepdf to accept it
        dests_dict = pikepdf.Dictionary({"/Names": pikepdf.Array([])})
        pdf.Root.Names.Dests = pdf.make_indirect(dests_dict)

        # Now it should return a NameTree wrapper
        names = get_named_destinations(pdf)
        assert names is not None
        # pikepdf.NameTree behaves like a mapping
        assert len(names) == 0


def test_resolve_dest_fallthrough():
    """Test falling through to the final return None (invalid/unrecognized input types)."""
    with pikepdf.new() as pdf:
        # 1. Empty Dictionary (no /D key) -> fallthrough
        result_dict = resolve_dest_to_page_num(pikepdf.Dictionary(), pdf.pages, None)
        assert result_dict is None

        # 2. Empty Array -> fallthrough
        result_arr = resolve_dest_to_page_num(pikepdf.Array([]), pdf.pages, None)
        assert result_arr is None

        # 3. None or simple types -> fallthrough
        result_none = resolve_dest_to_page_num(None, pdf.pages, None)
        assert result_none is None

        result_int = resolve_dest_to_page_num(123, pdf.pages, None)
        assert result_int is None
