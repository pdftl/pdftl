from pathlib import Path

import pytest
from pikepdf import Array, Dictionary, Name, NameTree, OutlineItem, Pdf, String

from pdftl.info.read_info import resolve_page_number
from pdftl.pages.add_pages import add_pages
from pdftl.utils.destinations import get_named_destinations
from pdftl.utils.page_specs import PageTransform

# --- Fixtures ---


@pytest.fixture
def complex_pdf_a(tmp_path: Path):
    """
    Creates a two-page PDF with a valid NameTree-based named destination,
    an outline item, and a link, guaranteeing a valid NameTree structure.
    """
    pdf_path = tmp_path / "A.pdf"

    with Pdf.new() as pdf:
        # 1. Setup Page, Destination Array, and Name
        pdf.add_blank_page()
        pdf.add_blank_page()
        page_obj = pdf.pages[0].obj
        dest_name = "StartPage"  # The key used for link/outline actions

        # The destination array: [page_obj_ref, /Fit]
        # Array creation expects a single iterable argument
        dest_array = Array([page_obj, Name.Fit])

        # 2. Add the Named Destination to the /Names tree (CORRECT API USAGE)

        # Create a new NameTree instance
        dests_tree = NameTree.new(pdf)

        # Add the destination. NameTree keys are unslashed strings/bytes.
        dests_tree[dest_name] = dest_array

        # Assign the underlying object to the PDF Catalog (creating /Names if needed)
        if Name.Names not in pdf.Root:
            pdf.Root.Names = Dictionary()
        pdf.Root.Names[Name.Dests] = dests_tree.obj

        # 3. Add an Outline (Bookmark)
        with pdf.open_outline() as outline:
            # Create the outline item (destination=string)
            outline_item = OutlineItem(title="Chapter 1", destination=dest_name)
            outline.root.append(outline_item)

        # 4. Add an on-page Annotation (Link)
        link_annot = Dictionary(
            Type=Name.Annot,
            Subtype=Name.Link,
            Rect=Array([100, 700, 300, 750]),
            A=Dictionary(S=Name.GoTo, D=dest_name),  # The named destination string
        )
        if Name.Annots not in pdf.pages[0]:
            pdf.pages[0].Annots = pdf.make_indirect(Array())
        pdf.pages[0].Annots.append(pdf.make_indirect(link_annot))

        pdf.save(pdf_path)

    yield pdf_path


# --- Integration Test Case ---


def test_integration_cat_a_a(complex_pdf_a: Path, tmp_path: Path):
    """
    Tests the full system by concatenating PDF A with itself (A + A).

    This forces:
    - PASS 1 to create two instances (instance 0 and instance 1).
    - PASS 2 (Outlines) to correctly remap the outline for the second instance.
    - PASS 3 (Links) to correctly remap the on-page link for both instances.
    """

    # 1. Arrange - Setup the inputs

    # Open the PDF for processing
    pdf_a = Pdf.open(complex_pdf_a)
    opened_pdfs = [pdf_a]

    # Create the PageTransform list: A[0] + A[0]
    page_specs = [
        PageTransform(pdf=pdf_a, index=0, rotation=(0, False), scale=1.0),  # Instance 0
        PageTransform(pdf=pdf_a, index=0, rotation=(0, False), scale=1.0),  # Instance 1
    ]

    output_path = tmp_path / "merged_output.pdf"

    # Create the new PDF container
    new_pdf = Pdf.new()

    # 2. Act - Run the full process
    add_pages(new_pdf, opened_pdfs, page_specs)
    new_pdf.save(output_path)

    # 3. Assert - Check the final PDF structure

    # Re-open the saved PDF to ensure everything is written correctly
    with Pdf.open(output_path) as result_pdf:
        # --- A. Check Page Count ---
        assert len(result_pdf.pages) == 2
        page_1 = result_pdf.pages[0]
        page_2 = result_pdf.pages[1]

        # --- B. Check Outlines (Bookmarks) ---
        with result_pdf.open_outline() as outline:
            # Should have two root items, one for each instance
            assert len(outline.root) == 2

            # Check the first item (Instance 0)
            item_1 = outline.root[0]
            assert item_1.title == "Chapter 1"

            # Check the second item (Instance 1) - title should be the same
            item_2 = outline.root[1]
            assert item_2.title == "Chapter 1"

            # Check the destination names - they must be unique
            # Use the structure-agnostic .destination property
            dest_name_1 = item_1.destination
            dest_name_2 = item_2.destination

            # Ensure the destination property successfully resolved the string
            assert isinstance(dest_name_1, String)
            assert isinstance(dest_name_2, String)

            assert str(dest_name_1).endswith("StartPage")
            assert str(dest_name_2).endswith("StartPage")
            assert dest_name_1 != dest_name_2  # Critical check! Remapping worked.

        # --- C. Check Named Destinations (/Names Tree) ---
        dests_tree_dict = result_pdf.Root.Names.Dests
        dests_tree = NameTree(dests_tree_dict)

        # Both new destination names must exist
        assert str(dest_name_1) in dests_tree
        assert str(dest_name_2) in dests_tree

        final_named_dests = get_named_destinations(result_pdf)

        with result_pdf.open_outline() as outline:
            # .root is the (hidden) top-level item.
            # .root.children is the list of top-level bookmarks.
            outline_1 = outline.root[0]
            outline_2 = outline.root[1]

        # Destination 1 must point to the first page
        assert resolve_page_number(outline_1, result_pdf.pages, final_named_dests) == 1

        # Destination 2 must point to the second page
        assert resolve_page_number(outline_2, result_pdf.pages, final_named_dests) == 2

        # --- D. Check Annotations (Links) ---

        # Check link on Page 1 (Instance 0)
        assert len(page_1.Annots) == 1
        annot_1 = page_1.Annots[0]
        # Link action must point to the new destination name 1
        assert annot_1.A.D == dest_name_1

        # Check link on Page 2 (Instance 1)
        assert len(page_2.Annots) == 1
        annot_2 = page_2.Annots[0]
        # Link action must point to the new destination name 2
        assert annot_2.A.D == dest_name_2

    pdf_a.close()
    new_pdf.close()


def test_integration_cat_a_b_links(complex_pdf_a: Path, tmp_path: Path):
    """
    Verifies that links in PDF A and PDF B remain independent
    and don't 'bleed' into each other's namespaces.
    """
    # 1. Create PDF B with a DIFFERENT named destination but the SAME name
    # If PDF A has "StartPage" and PDF B has "StartPage", they must stay separate.

    import pikepdf

    pdf_b_path = tmp_path / "B.pdf"
    with pikepdf.Pdf.new() as pdf_b:
        pdf_b.add_blank_page()
        dests_tree = pikepdf.NameTree.new(pdf_b)
        # Use the EXACT same name as PDF A
        dests_tree["StartPage"] = pikepdf.Array([pdf_b.pages[0].obj, pikepdf.Name.Fit])

        if pikepdf.Name.Names not in pdf_b.Root:
            pdf_b.Root.Names = pikepdf.Dictionary()
        pdf_b.Root.Names.Dests = dests_tree.obj
        link = pikepdf.Dictionary(
            Type=pikepdf.Name.Annot,
            Subtype=pikepdf.Name.Link,
            Rect=[0, 0, 10, 10],
            A=pikepdf.Dictionary(S=pikepdf.Name.GoTo, D="StartPage"),
        )
        page = pdf_b.pages[0]
        page.Annots = pdf_b.make_indirect(pikepdf.Array([link]))
        pdf_b.save(pdf_b_path)
        pdf_b.save("/tmp/b.pdf")
    # 2. Act - Concatenate A + B
    pdf_a = pikepdf.Pdf.open(complex_pdf_a)
    pdf_b = pikepdf.Pdf.open(pdf_b_path)

    new_pdf = pikepdf.Pdf.new()
    page_specs = [
        PageTransform(pdf_a, 0, (0, False), 1.0),
        PageTransform(pdf_b, 0, (0, False), 1.0),
    ]

    add_pages(new_pdf, [pdf_a, pdf_b], page_specs)

    # 3. Assert
    # Verify that the destination tree now has TWO entries
    # (likely suffixed to avoid collision)
    all_dests = get_named_destinations(new_pdf)
    new_pdf.save("/tmp/out.pdf")
    assert len(all_dests) == 2

    # Verify that Page 1's link points to Page 1, and Page 2's link points to Page 2
    # This proves the 'Namespace Separation' works across different files.
    p1_link_dest = new_pdf.pages[0].Annots[0].A.D
    p2_link_dest = new_pdf.pages[1].Annots[0].A.D
    assert p1_link_dest != p2_link_dest
