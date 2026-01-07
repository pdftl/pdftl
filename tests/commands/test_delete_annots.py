# tests/operations/test_delete_annots.py

import pikepdf

from pdftl.operations.delete_annots import delete_annots


def test_delete_annots_with_existing_annotations():
    """
    Covers line 54: page.Annots = pikepdf.Array([])
    Ensures that if annotations exist, they are cleared.
    """
    # 1. Create a PDF with one page and one annotation
    with pikepdf.new() as pdf:
        page = pdf.add_blank_page(page_size=(100, 100))

        # Create a dummy annotation
        annot = pikepdf.Dictionary(
            Type=pikepdf.Name.Annot,
            Subtype=pikepdf.Name.Text,
            Rect=[10, 10, 50, 50],
            Contents="Test Annotation",
        )

        # Assign it to the page (making it indirect is safer/standard)
        page.Annots = pdf.make_indirect(pikepdf.Array([annot]))

        assert len(page.Annots) == 1

        # 2. Run delete_annots on this page
        # Passing specs=["1"] explicitly matches page 1
        delete_annots(pdf, specs=["1"])

        # 3. Verify annotations are gone (empty array)
        assert len(page.Annots) == 0
        assert isinstance(page.Annots, pikepdf.Array)
