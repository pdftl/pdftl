import pikepdf
import pytest
from pikepdf import Dictionary, Name

from pdftl.pages.links import RebuildLinksPartialContext
from pdftl.pages.outlines import rebuild_outlines


def test_outline_named_destination_preservation(tmp_path):
    # 1. Create a source PDF with a named destination
    src_path = tmp_path / "src.pdf"
    with pikepdf.new() as src:
        # Add a blank page
        src.add_blank_page()
        target_page = src.pages[0]

        # Create a Named Destination in the source
        # This is the "G11.1696097" equivalent
        dest_name = "TargetDest"
        dest_array = [target_page.obj, Name.XYZ, 0, 0, None]

        # Build the NameTree
        names_tree = pikepdf.NameTree.new(src)
        names_tree[dest_name] = dest_array
        src.Root.Names = src.make_indirect(Dictionary(Dests=names_tree.obj))

        # Add an Outline item pointing to that name
        with src.open_outline() as outline:
            outline.root.append(pikepdf.OutlineItem("TOC Link", dest_name))

        src.save(src_path)

    # 2. Run the remapping logic
    out_path = tmp_path / "out.pdf"
    with pikepdf.open(src_path) as src_pdf, pikepdf.new() as dst_pdf:
        # Simulate 'cat' by copying the page
        dst_pdf.pages.append(src_pdf.pages[0])

        # Mock the context and remapper
        # (Assuming you have a factory or setup for these in your project)
        from pdftl.pages.link_remapper import create_link_remapper

        page_map = {(id(src_pdf), 0, 0): dst_pdf.pages[0]}
        processed_info = [(src_pdf, 0, 0)]

        remapper = create_link_remapper(
            page_map=page_map,
            page_transforms={},
            processed_page_info=processed_info,
            unique_source_pdfs={src_pdf},
            pdf_to_input_index={id(src_pdf): 0},
        )

        context = RebuildLinksPartialContext(processed_page_info=processed_info)

        # THE CALL UNDER TEST
        new_dests = rebuild_outlines(dst_pdf, [], context, remapper)

        # The main pipeline usually does this next:
        from pdftl.pages.links import write_named_dests

        write_named_dests(dst_pdf, new_dests)

        dst_pdf.save(out_path)

    # 3. VERIFICATION
    with pikepdf.open(out_path) as result:
        # BUG CHECK: The old code would fail here because Root.Names wouldn't exist
        assert Name.Names in result.Root, "Root.Names missing - Named destinations not preserved!"

        # Verify the specific destination exists
        dests = pikepdf.NameTree(result.Root.Names.Dests)
        assert dest_name in dests, f"Destination '{dest_name}' was lost during outline rebuilding"
