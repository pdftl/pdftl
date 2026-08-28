# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/info/test_toc.py

import contextlib
import logging
import signal
from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.exceptions import OperationError
from pdftl.info.toc import (
    _extract_action,
    _build_item,
    _extract_item,
    _from_python_types,
    _to_python_types,
    build_toc_tree,
    extract_toc_tree,
)
from pdftl.utils.pikepdf_compatibility_utils import set_outline_item_style_compat
from pdftl.utils.pikepdf_compatibility_utils import outline_item_has_style_properties


@pytest.fixture
def exotic_pdf():
    """Generates a PDF with a highly complex outline tree in memory."""
    pdf = pikepdf.Pdf.new()
    for _ in range(5):
        pdf.add_blank_page(page_size=(612, 792))

    # Setup a Named Destination
    pdf.Root.Names = pikepdf.Dictionary()
    dests_tree = pikepdf.NameTree.new(pdf)
    pdf.Root.Names.Dests = dests_tree.obj
    dests_tree["secret_appendix"] = pikepdf.Array([pdf.pages[3].obj, pikepdf.Name("/Fit")])

    with pdf.open_outline() as outline:
        # 1. Standard
        outline.root.append(pikepdf.OutlineItem("1. Standard", 0))

        # 2. Styled
        item2 = pikepdf.OutlineItem("2. Styled", 1)
        item2.to_dictionary_object(pdf)
        if outline_item_has_style_properties():
            item2.color = (1.0, 0.0, 0.0)
            item2.bold = True
            item2.italic = True  # Bold + Italic
        else:
            set_outline_item_style_compat(item2, color=(1.0, 0.0, 0.0), bold=True, italic=True)
        outline.root.append(item2)

        # 3. Explicit View
        item3 = pikepdf.OutlineItem("3. Zoomed", 2)
        item3.destination = pikepdf.Array([pdf.pages[2].obj, pikepdf.Name("/XYZ"), 0, 700, 2.5])
        outline.root.append(item3)

        # 4. Named Destination
        outline.root.append(pikepdf.OutlineItem("4. Named", "secret_appendix"))

        # 5. URI Action
        action = pikepdf.Dictionary(S=pikepdf.Name("/URI"), URI="https://example.com")
        outline.root.append(pikepdf.OutlineItem("5. URI", action=action))

        # 6. Nested
        parent = pikepdf.OutlineItem("6. Parent", 0)
        child = pikepdf.OutlineItem("6.1 Child", 1)
        child.destination = pikepdf.Array([pdf.pages[1].obj, pikepdf.Name("/FitH"), 500])
        parent.children.append(child)
        outline.root.append(parent)

        # 7. Generic Launch Action
        action_launch = pikepdf.Dictionary(S=pikepdf.Name("/Launch"), F=pikepdf.String("file.pdf"))
        outline.root.append(pikepdf.OutlineItem("7. Launch", action=action_launch))

        # 8. Named Action
        action_named = pikepdf.Dictionary(S=pikepdf.Name("/Named"), N=pikepdf.Name("/NextPage"))
        outline.root.append(pikepdf.OutlineItem("8. Named", action=action_named))

    return pdf


def test_extract_toc_tree_exotic(exotic_pdf):
    """Verifies all complex properties and ISO generic actions are extracted correctly."""
    data = extract_toc_tree(exotic_pdf)

    assert len(data) == 8
    assert data[0] == {"title": "1. Standard", "page": 1}

    assert data[1]["title"] == "2. Styled"
    assert data[1]["color"] == [1.0, 0.0, 0.0]
    assert data[1]["bold"] is True
    assert data[1]["italic"] is True

    assert data[2]["view"] == ["XYZ", 0, 700, 2.5]
    assert data[3]["dest"] == "secret_appendix"
    assert data[4]["uri"] == "https://example.com"

    assert data[5]["title"] == "6. Parent"
    assert data[5]["children"][0]["title"] == "6.1 Child"
    assert data[5]["children"][0]["view"] == ["FitH", 500]

    assert data[6]["title"] == "7. Launch"
    assert data[6]["action"]["S"] == {"__name__": "/Launch"}
    assert data[6]["action"]["F"] == "file.pdf"

    assert data[7]["title"] == "8. Named"
    assert data[7]["action"]["S"] == {"__name__": "/Named"}
    assert data[7]["action"]["N"] == {"__name__": "/NextPage"}


def test_build_toc_tree_roundtrip(exotic_pdf):
    """Verifies that injecting the extracted data back into a blank PDF faithfully restores it."""
    extracted_data = extract_toc_tree(exotic_pdf)

    # Create a fresh, blank 5-page PDF
    new_pdf = pikepdf.Pdf.new()
    for _ in range(5):
        new_pdf.add_blank_page()

    # Inject the data
    build_toc_tree(new_pdf, extracted_data)

    # Re-extract from the newly built PDF
    roundtrip_data = extract_toc_tree(new_pdf)

    # The data should be structurally identical, preserving exotic actions!
    assert roundtrip_data == extracted_data


def test_build_toc_out_of_bounds():
    """Verifies strict validation is applied to page boundaries."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()

    bad_toc = [{"title": "Out of Bounds", "page": 99}]

    with pytest.raises(ValueError, match="but the document only has 1 pages"):
        build_toc_tree(pdf, bad_toc)


def test_build_toc_empty_clears_outlines(exotic_pdf):
    """Verifies that passing an empty list cleanly deletes the Bookmarks pane."""
    assert "/Outlines" in exotic_pdf.Root

    build_toc_tree(exotic_pdf, [])

    assert "/Outlines" not in exotic_pdf.Root
    assert len(extract_toc_tree(exotic_pdf)) == 0


def test_build_toc_missing_target():
    """Covers the fallback when a YAML bookmark has no dest, uri, or page keys."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()
    pdf.add_blank_page()

    # Build with an empty node
    build_toc_tree(pdf, [{"title": "Nowhere"}])

    data = extract_toc_tree(pdf)
    assert len(data) == 1
    assert data[0]["title"] == "Nowhere"

    # The fallback automatically points to the first page
    assert data[0]["page"] == 1


def test_extract_toc_tree_view_edge_cases(exotic_pdf):
    """Covers unresolved destinations, null view arguments, and Decimal arguments."""
    import decimal

    import pikepdf

    with exotic_pdf.open_outline() as outline:
        # 1. Unresolved destination
        # Create a dummy object INSIDE exotic_pdf so we guarantee it gets
        # a unique objgen that won't accidentally match page 1!
        dummy_obj = pikepdf.Dictionary()

        bad_item = pikepdf.OutlineItem("Unresolved", 0)
        bad_item.to_dictionary_object(exotic_pdf)
        bad_item.destination = pikepdf.Array([dummy_obj, pikepdf.Name("/Fit")])
        outline.root.append(bad_item)

        # 2. Decimal and Null arguments
        # /XYZ arrays frequently use `null` to retain current zoom/pan state
        edge_item = pikepdf.OutlineItem("Edge Cases", 0)
        edge_item.to_dictionary_object(exotic_pdf)
        edge_item.destination = pikepdf.Array(
            [
                exotic_pdf.pages[0].obj,
                pikepdf.Name("/XYZ"),
                None,  # PDF null
                decimal.Decimal("123.45"),  # standard decimal.Decimal
                0,  # int type
            ]
        )
        outline.root.append(edge_item)

    data = extract_toc_tree(exotic_pdf)

    unresolved_node = data[-2]
    assert unresolved_node["title"] == "Unresolved"
    assert "page" not in unresolved_node  # Gracefully failed to resolve

    edge_node = data[-1]
    assert edge_node["title"] == "Edge Cases"
    assert edge_node["view"] == ["XYZ", None, 123.45, 0]


def test_extract_toc_tree_invalid_view_arg(exotic_pdf, caplog):
    """Covers the ValueError fallback for malformed view arguments."""
    with exotic_pdf.open_outline() as outline:
        bad_arg_item = pikepdf.OutlineItem("Bad Arg", 0)
        bad_arg_item.to_dictionary_object(exotic_pdf)

        # Create a destination array with a string that cannot be cast to int
        bad_arg_item.destination = pikepdf.Array(
            [
                exotic_pdf.pages[0].obj,
                pikepdf.Name("/XYZ"),
                pikepdf.String("not_a_number"),  # This triggers the ValueError
            ]
        )
        outline.root.append(bad_arg_item)

    # Run extraction while capturing WARNING logs
    with caplog.at_level(logging.WARNING):
        data = extract_toc_tree(exotic_pdf)

    # Verify the fallback worked and didn't crash
    bad_node = data[-1]
    assert bad_node["title"] == "Bad Arg"
    assert bad_node["view"] == ["XYZ"]  # The bad arg was ignored and not appended

    # Verify the logger actually caught it
    assert "Ignoring unknown destination argument" in caplog.text
    assert "not_a_number" in caplog.text


def test_extract_item_no_obj():
    # Force a mock item that has item.obj = None
    mock_item = MagicMock(spec=pikepdf.OutlineItem)
    mock_item.obj = None
    with pytest.raises(OperationError, match=r"Invalid item \(no obj\)"):
        _extract_item(mock_item, None, {}, {})


def test_build_item_fails_to_create_obj():
    """Covers line 213: raise OperationError("Invalid item (no obj)")"""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.pages = [MagicMock()]

    node = {"title": "Ghost Item", "page": 1}

    # Patching inside the namespace is often safer
    with patch("pikepdf.OutlineItem") as MockItem:
        instance = MockItem.return_value
        instance.obj = None

        with pytest.raises(OperationError, match=r"Invalid item \(no obj\)"):
            _build_item(node, mock_pdf)


def test_build_toc_validation_errors(caplog):
    """Covers validation logic via logging/skipping."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.Root = {}

    malformed_items = [
        "not a dict",
        {"page": 1},
        {"title": "Bad Child", "children": "not-a-list"},
    ]

    with caplog.at_level(logging.WARNING):
        # Prevent pikepdf C++ from parsing our structural mocks
        with patch("pikepdf.models.outlines.make_page_destination", return_value=[]):
            build_toc_tree(mock_pdf, malformed_items)

    assert "Ignoring invalid bookmark entry (not a dictionary)" in caplog.text
    assert "Ignoring invalid bookmark entry (missing 'title')" in caplog.text
    assert "Ignoring invalid 'children' (must be a list)" in caplog.text


def test_build_toc_strict_key_check():
    """Covers OperationError for unrecognized keys (typos)."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)

    # User made a typo 'pagee' instead of 'page'
    bad_toc = [{"title": "Intro", "pagee": 1}]

    with pytest.raises(OperationError, match="Invalid keys found in bookmark 'Intro': pagee"):
        build_toc_tree(mock_pdf, bad_toc)


def test_type_converters_edge_cases():
    """Covers edge cases like None, numbers, and nested Arrays."""
    import pikepdf

    # Test Null -> None
    assert _to_python_types(None) is None

    # Test primitives
    assert _to_python_types(42) == 42
    assert _to_python_types(3.14) == 3.14
    assert _to_python_types(True) is True

    class UnknownObj:
        def __str__(self):
            return "UnknownObj"

    assert _to_python_types(UnknownObj()) == "UnknownObj"

    # Test complex Array -> list
    arr = pikepdf.Array([pikepdf.Name("/Val"), pikepdf.String("text"), 100])
    py_arr = _to_python_types(arr)
    assert py_arr == [{"__name__": "/Val"}, "text", 100]

    # Test rebuilding
    mock_pdf = MagicMock()
    py_dict = {"mykey": py_arr, "num": 42, "bool": False}
    pdf_dict = _from_python_types(py_dict, mock_pdf)
    assert isinstance(pdf_dict, pikepdf.Dictionary)
    assert isinstance(pdf_dict["/mykey"], pikepdf.Array)
    assert isinstance(pdf_dict["/mykey"][0], pikepdf.Name)
    assert isinstance(pdf_dict["/mykey"][1], pikepdf.String)
    assert pdf_dict["/num"] == 42
    assert pdf_dict["/bool"] is False


def test_to_python_types_stream_fallback():
    """Covers line 66: a bare Stream object (outside the /JS action path)
    gets an explicit, honest marker instead of falling through to str(obj)
    (which previously produced an unhelpful "<pikepdf.Stream object at
    0x...>" placeholder with no signal that data was discarded).
    """
    pdf = pikepdf.Pdf.new()
    stream = pikepdf.Stream(pdf, b"irrelevant content")
    assert _to_python_types(stream) == {"__stream__": True}


def test_extract_action_js_stream_decodes(exotic_pdf):
    """Covers lines 140-143: a /JS action whose script is stored as a
    Stream (ISO 32000-2 12.6.4.16 permits string OR stream) rather than
    a text string. Should decode the script text rather than losing it.
    """
    with exotic_pdf.open_outline() as outline:
        js_stream = pikepdf.Stream(exotic_pdf, b"app.alert('hi');")
        action_js = pikepdf.Dictionary(S=pikepdf.Name("/JS"), JS=js_stream)
        outline.root.append(pikepdf.OutlineItem("9. JS Stream", action=action_js))

    data = extract_toc_tree(exotic_pdf)
    js_node = data[-1]

    assert js_node["title"] == "9. JS Stream"
    assert js_node["action"]["S"] == {"__name__": "/JS"}
    assert js_node["action"]["JS"] == "app.alert('hi');"
    assert "action_lossy" not in js_node


def test_extract_action_js_stream_undecodable(exotic_pdf, caplog):
    """Covers lines 144-148 and the 149->exit branch: a /JS stream whose
    bytes aren't valid UTF-8 (e.g. a broken/binary script). Should fall
    back to the generic dict conversion and flag the result as lossy,
    rather than raising or silently losing the fact that decoding failed.
    """
    with exotic_pdf.open_outline() as outline:
        # 0xff is not valid UTF-8 in this position
        bad_js_stream = pikepdf.Stream(exotic_pdf, b"\xff\xfe\x00garbage")
        action_js = pikepdf.Dictionary(S=pikepdf.Name("/JS"), JS=bad_js_stream)
        outline.root.append(pikepdf.OutlineItem("10. Bad JS", action=action_js))

    data = extract_toc_tree(exotic_pdf)
    bad_node = data[-1]

    assert bad_node["title"] == "10. Bad JS"
    assert bad_node.get("action_lossy") is True
    assert bad_node["action"]["S"] == {"__name__": "/JS"}
    # The stream payload itself is preserved only as the honest marker,
    # not silently stringified.
    assert bad_node["action"]["JS"] == {"__stream__": True}


def test_lossy_action_round_trip_does_not_fabricate_data(exotic_pdf):
    """A node with action_lossy=True (from an undecodable /JS stream)
    must not, on rebuild, write a fabricated placeholder dict back into
    the PDF as if it were the real action. It should be dropped with a
    warning, not silently propagated as bogus content.
    """
    with exotic_pdf.open_outline() as outline:
        bad_js_stream = pikepdf.Stream(exotic_pdf, b"\xff\xfe\x00garbage")
        action_js = pikepdf.Dictionary(S=pikepdf.Name("/JS"), JS=bad_js_stream)
        outline.root.append(pikepdf.OutlineItem("Lossy JS", action=action_js))

    extracted = extract_toc_tree(exotic_pdf)
    lossy_node = extracted[-1]  # our appended item is last
    assert lossy_node["action_lossy"] is True  # sanity check on the fixture

    new_pdf = pikepdf.Pdf.new()
    new_pdf.add_blank_page()

    # Must not raise (this is what the _ALLOWED_BOOKMARK_KEYS fix alone
    # would have covered) — but more importantly, must not fabricate data.
    # Only rebuild the one node under test — new_pdf is deliberately a
    # single blank page, not exotic_pdf's full 5-page/8-bookmark set.
    build_toc_tree(new_pdf, [lossy_node])

    rebuilt = extract_toc_tree(new_pdf)
    assert len(rebuilt) == 1
    assert "action" not in rebuilt[0]
    assert "action_lossy" not in rebuilt[0]


def test_extract_item_flags_italic_only():
    """Covers branch 114->118: with only the italic bit set (flags & 2
    is falsy), the bold check must be skipped rather than assumed.
    Existing fixtures only ever set both flags together, so this path
    (and the companion bold-only case below) was never exercised.
    """
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))

    with pdf.open_outline() as outline:
        item = pikepdf.OutlineItem("Italic Only", 0)
        item.to_dictionary_object(pdf)
        # Must go through the model properties (or the compat shim), same
        # as exotic_pdf's fixture does — OutlineItem re-serializes these
        # known fields on append/exit, so poking /F directly on item.obj
        # doesn't survive.
        if outline_item_has_style_properties():
            item.italic = True
        else:
            set_outline_item_style_compat(item, color=None, bold=False, italic=True)
        outline.root.append(item)

    data = extract_toc_tree(pdf)
    assert data[0]["italic"] is True
    assert "bold" not in data[0]


def test_extract_item_flags_bold_only():
    """Covers branch 112->114: with only the bold bit set (flags & 1 is
    falsy), the italic check must be skipped rather than assumed.
    """
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))

    with pdf.open_outline() as outline:
        item = pikepdf.OutlineItem("Bold Only", 0)
        item.to_dictionary_object(pdf)
        if outline_item_has_style_properties():
            item.bold = True
        else:
            set_outline_item_style_compat(item, color=None, bold=True, italic=False)
        outline.root.append(item)

    data = extract_toc_tree(pdf)
    assert data[0]["bold"] is True
    assert "italic" not in data[0]


def test_extract_action_js_non_stream():
    """Covers line 150: a /JS action whose script is a plain String (or
    any non-Stream type), not the Stream case handled above. Tested
    directly against _extract_action to avoid depending on how a full
    outline round-trip would represent it — this is purely about the
    isinstance(js, pikepdf.Stream) branch being False.
    """
    action_obj = pikepdf.Dictionary(S=pikepdf.Name("/JS"), JS=pikepdf.String("alert('hi')"))
    node = {}
    _extract_action(action_obj, node)
    assert node["action"]["JS"] == "alert('hi')"
    assert "action_lossy" not in node


def test_extract_action_goto_is_skipped():
    """Covers branch 151->exit: an explicit /GoTo action (S=/GoTo set
    directly on /A, rather than via the item.destination shorthand) must
    not populate node["action"] — GoTo is handled entirely by the
    separate destination-extraction path in _extract_item, not here.
    Tested directly against _extract_action, since resolving a hand-built
    /GoTo action through the full destination lookup isn't what this
    branch is actually about.
    """
    action_obj = pikepdf.Dictionary(S=pikepdf.Name("/GoTo"))
    node = {}
    _extract_action(action_obj, node)
    assert "action" not in node


def test_build_toc_tree_empty_with_no_prior_outlines():
    """Covers branch 205->207: calling build_toc_tree([]) on a PDF that
    never had an /Outlines entry to begin with. The existing empty-input
    test (test_build_toc_empty_clears_outlines) only covers the case
    where /Outlines was present and gets deleted — this covers the
    already-absent case, which must return cleanly without error.
    """
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page()
    assert "/Outlines" not in pdf.Root

    build_toc_tree(pdf, [])

    assert "/Outlines" not in pdf.Root


@contextlib.contextmanager
def _hang_guard(seconds=5):
    """Fails the test with a clear message instead of hanging forever.

    Uses SIGALRM rather than pytest-timeout so this doesn't depend on an
    optional plugin being installed. Unix-only; skip on platforms without
    signal.SIGALRM (e.g. native Windows) rather than false-passing.
    """
    if not hasattr(signal, "SIGALRM"):
        pytest.skip("SIGALRM not available on this platform")

    def _on_alarm(signum, frame):
        raise TimeoutError(
            f"extract_toc_tree did not return within {seconds}s — "
            "likely an infinite loop from an unguarded outline cycle."
        )

    old_handler = signal.signal(signal.SIGALRM, _on_alarm)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_handler)


def _make_cyclic_outline_pdf():
    """Builds a PDF whose outline tree contains a manufactured /Next cycle.

    pikepdf.OutlineItem/open_outline() won't let us construct a cycle
    through the normal API (it manages /First /Last /Next /Prev /Parent
    consistently), so this drops to raw dictionary construction to mimic
    a malformed/adversarial PDF: item B's /Next points back to item A,
    its own ancestor in traversal order, rather than forward or to None.
    """
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))

    item_a = pdf.make_indirect(pikepdf.Dictionary(Title=pikepdf.String("A")))
    item_b = pdf.make_indirect(pikepdf.Dictionary(Title=pikepdf.String("B")))

    outlines = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name("/Outlines"),
            First=item_a,
            Last=item_b,
            Count=2,
        )
    )

    item_a.Parent = outlines
    item_a.Next = item_b
    item_b.Parent = outlines
    item_b.Prev = item_a
    # The cycle: B's /Next points back to A instead of terminating (None).
    item_b.Next = item_a

    pdf.Root.Outlines = outlines
    return pdf


def test_extract_toc_tree_cyclic_outline_does_not_hang():
    """Regression test.
    A /Next pointer forming a cycle is spec-illegal but not something
    pikepdf itself rejects when constructed at the raw-dictionary level,
    so a malformed or adversarial PDF can present one. This test does not
    assert *how* pdftl should handle it (raise, truncate, warn) — only
    that it terminates instead of hanging. If this test currently times
    out, that confirms the hang is real, not theoretical, and narrows the
    fix to: add a visited-objgen set (or a depth cap, which incidentally
    also bounds this case) to whatever walk extract_toc_tree performs.
    """
    pdf = _make_cyclic_outline_pdf()

    with _hang_guard(seconds=5):
        # We don't assert on the return value's shape here — only that
        # calling this returns at all. A separate, follow-up test should
        # pin down the *desired* behavior (truncate vs raise vs warn)
        # once we know which failure mode we're actually seeing.
        extract_toc_tree(pdf)


def test_extract_toc_tree_deep_nesting_does_not_recursionerror():
    """Companion case to the cycle test above: a legal, non-cyclic but
    absurdly deep outline chain (each item's only child is the next
    item, 5000 levels deep). If extract_toc_tree recurses per level
    rather than iterating, this should surface as RecursionError rather
    than a hang — a distinct failure mode from the cycle case, and one
    a depth cap alone (without a visited-set) would actually fix.
    """
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))

    with pdf.open_outline() as outline:
        root_item = pikepdf.OutlineItem("Level 0", 0)
        outline.root.append(root_item)
        current = root_item
        for depth in range(1, 5000):
            child = pikepdf.OutlineItem(f"Level {depth}", 0)
            current.children.append(child)
            current = child

    with _hang_guard(seconds=10):
        try:
            extract_toc_tree(pdf)
        except RecursionError:
            pytest.fail(
                "extract_toc_tree hit RecursionError on a deep-but-legal "
                "outline tree — needs an iterative walk or an explicit, "
                "documented depth cap."
            )
