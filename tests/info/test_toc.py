# tests/info/test_toc.py

import logging
from unittest.mock import MagicMock

import pikepdf
import pytest

from pdftl.exceptions import OperationError
from pdftl.info.toc import _extract_item, build_toc_tree, extract_toc_tree


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
        item2.obj.C = pikepdf.Array([1.0, 0.0, 0.0])
        item2.obj.F = 3  # Bold + Italic
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

    return pdf


def test_extract_toc_tree_exotic(exotic_pdf):
    """Verifies all complex properties are extracted correctly."""
    data = extract_toc_tree(exotic_pdf)

    assert len(data) == 6
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

    # The data should be structurally identical
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


from unittest.mock import patch

import pytest

from pdftl.info.toc import _build_item


def test_build_item_fails_to_create_obj():
    """
    Covers line 213: raise OperationError("Invalid item (no obj)")
    """
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    # FIX: Mock the pages list so len(pdf.pages) >= 1
    mock_pdf.pages = [MagicMock()]

    node = {"title": "Ghost Item", "page": 1}

    # Patching inside the 'pdftl.info.toc' namespace is often safer
    # if it's already imported there.
    with patch("pikepdf.OutlineItem") as MockItem:
        instance = MockItem.return_value
        instance.obj = None

        with pytest.raises(OperationError, match=r"Invalid item \(no obj\)"):
            _build_item(node, mock_pdf)


def test_build_toc_validation_errors(caplog):
    """Covers lines 136-137, 140-141, and 160-164 via logging/skipping."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)
    mock_pdf.Root = {}

    malformed_items = [
        "not a dict",
        {"page": 1},
        {"title": "Bad Child", "children": "not-a-list"},
    ]

    with caplog.at_level(logging.WARNING):
        # Prevent the deep pikepdf C++ layout from parsing our structural Page mocks
        with patch("pikepdf.models.outlines.make_page_destination", return_value=[]):
            build_toc_tree(mock_pdf, malformed_items)

    assert "Ignoring invalid bookmark entry (not a dictionary)" in caplog.text
    assert "Ignoring invalid bookmark entry (missing 'title')" in caplog.text
    assert "Ignoring invalid 'children' (must be a list)" in caplog.text


def test_build_toc_strict_key_check():
    """Covers line 148: OperationError for unrecognized keys (typos)."""
    mock_pdf = MagicMock(spec=pikepdf.Pdf)

    # User made a typo 'pagee' instead of 'page'
    bad_toc = [{"title": "Intro", "pagee": 1}]

    with pytest.raises(OperationError, match="Invalid keys found in bookmark 'Intro': pagee"):
        build_toc_tree(mock_pdf, bad_toc)
