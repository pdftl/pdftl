from unittest.mock import MagicMock

import pikepdf

from pdftl.utils.destinations import (
    ResolvedDest,
    _dest_from_outline_item,
    _find_page_index,
    get_named_destinations,
    get_page_map,
    resolve_dest_to_page_num,
)

# --- 1. RESTORE ORIGINAL INTEGRATION TESTS (Lines 23-27, 63-65, 88) ---


def test_resolve_named_dest_dictionary_path():
    """Hits Line 63: Named dest points to a Dict with a /D key."""
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        dest_dict = pikepdf.Dictionary(
            {"/D": pikepdf.Array([pdf.pages[0].obj, pikepdf.Name("/Fit")])}
        )
        named_dests = {"Complex": dest_dict}

        res = resolve_dest_to_page_num("Complex", pdf.pages, named_dests)
        assert res.dest_type == "Fit"


# --- 2. OUTLINE & ACTION FALLBACKS (Lines 47-53, 81) ---


def test_outline_item_action_fallback():
    """Hits Lines 47-53 & 81: OutlineItem -> Action -> Destination."""
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        page_obj = pdf.pages[0].obj

        # Create a mock OutlineItem that behaves like pikepdf's C-extension object
        mock_item = MagicMock(spec=pikepdf.OutlineItem)
        mock_item.destination = None
        # Mocking the .action.D attribute access
        mock_item.action = MagicMock()
        mock_item.action.D = pikepdf.Array([page_obj, pikepdf.Name("/XYZ")])

        # This hits line 81, then 47-53
        res = resolve_dest_to_page_num(mock_item, pdf.pages, None)
        assert res.page_num == 1


# --- 3. EDGE CASE FAILURES (Lines 33, 108) ---


def test_find_page_index_missing_objgen():
    """Hits Line 33: Object has no objgen attribute."""
    assert _find_page_index(object(), []) is None


def test_resolve_dest_to_page_num_catch_all():
    """Hits Line 108: Array is valid but contains invalid page data."""
    with pikepdf.new() as pdf:
        # Array with an integer instead of a Page Dictionary
        invalid_dest = pikepdf.Array([123, pikepdf.Name("/XYZ")])
        res = resolve_dest_to_page_num(invalid_dest, pdf.pages, None)
        assert res is None  # Hits line 108


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


def test_find_page_index_no_objgen():
    """
    Covers line 25: if not hasattr(page_obj, "objgen"): return None
    """
    # Pass a standard object that definitely doesn't have .objgen
    plain_obj = object()
    pages = [MagicMock()]

    assert _find_page_index(plain_obj, pages) is None


def test_dest_from_outline_item_action_fallback():
    """
    Covers lines 38-40:
      fallback to action if None
      ... if action is not None and hasattr(action, "D"): ...
    """
    # 1. Create a mock OutlineItem
    mock_item = MagicMock()

    # 2. Ensure primary destination is None (to enter the 'if' block)
    mock_item.destination = None

    # 3. Setup the Action object with a 'D' attribute
    mock_action = MagicMock()
    mock_action.D = "/MyFallbackDest"

    # 4. Attach action to item
    mock_item.action = mock_action

    # 5. Run function
    result = _dest_from_outline_item(mock_item)

    assert result == "/MyFallbackDest"


def test_find_page_index_fast_path_explicit():
    """
    Directly hits destinations.py:35
    The main resolver skips _find_page_index if pdf_pages is a dict,
    so we have to call the helper directly to cover this line.
    """
    from unittest.mock import MagicMock

    from pdftl.utils.destinations import _find_page_index

    mock_page = MagicMock()
    mock_page.objgen = (10, 0)

    # Passing a dict here forces Line 34 to be True and Line 35 to execute
    page_map = {(10, 0): 1}

    result = _find_page_index(mock_page, page_map)
    assert result == 1


def test_resolve_dest_full_integration(temp_pdf):
    from pikepdf import Array, Dictionary, Name, NameTree

    page2 = temp_pdf.add_blank_page()
    num_pages = len(temp_pdf.pages)
    page_map = get_page_map(temp_pdf.pages)

    # 1. Use the NameTree helper to create a valid /Dests tree
    dest_tree = NameTree.new(temp_pdf)

    # 2. Add the destination (the helper handles the tree logic)
    dest_tree["MyDest"] = Array([page2.obj, Name.XYZ])

    # 3. Attach it to the Root Catalog
    if "/Names" not in temp_pdf.Root:
        temp_pdf.Root.Names = Dictionary()

    temp_pdf.Root.Names.Dests = dest_tree.obj

    # 4. Now this should succeed
    named_dests = get_named_destinations(temp_pdf)
    assert "MyDest" in named_dests

    res = resolve_dest_to_page_num("MyDest", page_map, named_dests)
    assert res.page_num == num_pages
    assert res.dest_type == "XYZ"


def test_resolve_named_dest_dictionary_path(temp_pdf):
    """Hits Line 63: Named dest points to a Dict with a /D key."""
    page = temp_pdf.add_blank_page()
    dest_dict = pikepdf.Dictionary(D=pikepdf.Array([page.obj, pikepdf.Name("/Fit")]))
    named_dests = {"Complex": dest_dict}

    # Pass pages as a list to hit 'slow path' coverage in helper
    res = resolve_dest_to_page_num("Complex", temp_pdf.pages, named_dests)
    assert res.dest_type == "Fit"


def test_destinations_fast_paths_final(temp_pdf):
    """Hits lines 96-104 safely using real objects."""
    page = temp_pdf.add_blank_page()
    page_map = {page.objgen: 1}

    # Real Array prevents the 'isinstance' failure and instability
    fake_dest = pikepdf.Array([page.obj, pikepdf.Name("/XYZ"), 0, 0])

    res = resolve_dest_to_page_num(fake_dest, page_map, {})

    assert res.page_num == 1
    assert res.dest_type == "XYZ"
    assert res.args == [0, 0]


def test_resolve_dest_to_page_num_catch_all(temp_pdf):
    """Hits Line 108: Array is valid but contains invalid page data (int instead of dict)."""
    invalid_dest = pikepdf.Array([123, pikepdf.Name("/XYZ")])
    res = resolve_dest_to_page_num(invalid_dest, temp_pdf.pages, None)
    assert res is None


def test_find_page_index_missing_objgen():
    """Hits Line 33: Object has no objgen attribute (edge case)."""
    assert _find_page_index(object(), []) is None
