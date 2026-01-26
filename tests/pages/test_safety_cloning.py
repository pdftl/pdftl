import pikepdf

from pdftl.pages.add_pages import add_pages
from pdftl.utils.page_specs import PageTransform


def test_safety_annotation_independence():
    """
    Verify that annotations are deep-copied (independent),
    but heavy content is shared (efficient).
    """
    # 1. Create Source PDF with an Annotation
    src = pikepdf.new()
    page = src.add_blank_page(page_size=(100, 100))

    # Add a dummy link annotation
    annot = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Link, Rect=[0, 0, 10, 10]
    )
    page.Annots = src.make_indirect([annot])

    # 2. Duplicate this page twice into a new PDF
    dest = pikepdf.new()
    transforms = [
        PageTransform(pdf=src, index=0, rotation=(0, False), scale=1.0),
        PageTransform(pdf=src, index=0, rotation=(0, False), scale=1.0),
    ]

    add_pages(dest, [src], transforms)

    p1 = dest.pages[0]
    p2 = dest.pages[1]

    # --- SAFETY CHECK: Annotations ---
    # The list of annotations must be different objects
    assert p1.Annots.objgen != p2.Annots.objgen or p1.Annots.objgen == (0, 0)

    # The actual annotation inside must ALSO be different objects
    annot1 = p1.Annots[0]
    annot2 = p2.Annots[0]
    assert annot1.objgen != annot2.objgen or annot1.objgen == (
        0,
        0,
    ), "Annotations are dangerously shared!"

    # --- EFFICIENCY CHECK: Content ---
    # The Contents stream should be the SAME object (Shared)
    # (Note: Blank pages might not have Contents, so we check Resources or implicit sharing)
    if "/Contents" in p1:
        assert (
            p1.Contents.objgen == p2.Contents.objgen
        ), "Content streams should be shared for speed"


def test_annotation_isolation_regression():
    """
    This test fails if p1 and p2 share the same annotation list.
    """
    src = pikepdf.new()
    page = src.add_blank_page(page_size=(100, 100))
    # Create an annotation
    annot = pikepdf.Dictionary(Type=pikepdf.Name.Annot, Rect=[0, 0, 10, 10])
    page.Annots = src.make_indirect([annot])

    dest = pikepdf.new()
    transforms = [
        PageTransform(pdf=src, index=0, rotation=(0, False), scale=1.0),
        PageTransform(pdf=src, index=0, rotation=(0, False), scale=1.0),
    ]

    # In the un-hardened version, this creates two pages sharing one /Annots list
    add_pages(dest, [src], transforms)

    # ACTION: Modify an annotation on the first page only
    dest.pages[0].Annots[0].Rect = [50, 50, 60, 60]

    # ASSERT: The second page should be UNTOUCHED
    # If this fails, the 'Dirty Master' or 'Shared Instance' bug is present.
    p2_rect = list(dest.pages[1].Annots[0].Rect)
    assert p2_rect == [0, 0, 10, 10], "REGRESSION: Mutation on Page 1 leaked to Page 2!"


def test_prove_hardening_is_required():
    """
    Structural Integrity Test:
    In a valid PDF, an annotation's /P (Parent) must point to the page
    it actually lives on.

    If instances are shared (unhardened), the last page processed
    'steals' the annotation from the previous pages.
    """
    src = pikepdf.new()
    page = src.add_blank_page(page_size=(100, 100))
    # We must use an indirect object for the annotation to trigger the shared reference
    annot = src.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Link, Rect=[0, 0, 10, 10])
    )
    page.Annots = src.make_indirect([annot])

    dest = pikepdf.new()
    # Duplicate the page twice
    transforms = [
        PageTransform(pdf=src, index=0, rotation=(0, False), scale=1.0),
        PageTransform(pdf=src, index=0, rotation=(0, False), scale=1.0),
    ]

    # Run the add_pages logic
    # Note: add_pages calls handle_page_widgets, which sets the /P key
    add_pages(dest, [src], transforms)

    p1 = dest.pages[0]
    p2 = dest.pages[1]

    # The /P key of the annotation on Page 1 must point to Page 1
    p1_annot_parent = p1.Annots[0].get("/P")

    assert p1_annot_parent is not None, "Annotation is missing Parent link"

    # UNHARDENED VERSION WILL FAIL HERE:
    # p1_annot_parent.objgen will be the ID of Page 2, not Page 1
    assert p1_annot_parent.objgen == p1.objgen, (
        f"Corruption detected! Page 1's annotation points to "
        f"Object {p1_annot_parent.objgen}, but Page 1 is Object {p1.objgen}"
    )


def test_widget_identity_collision():
    """
    Form widgets must be unique objects.
    If they are shared, checking a box on Page 1 will
    automatically check it on Page 2 (Mirroring).
    """
    src = pikepdf.new()
    page = src.add_blank_page(page_size=(100, 100))

    # Create a Form Field (Widget)
    # Fields are complex because they are linked to the global AcroForm tree
    widget = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Widget,
        FT=pikepdf.Name.Tx,  # Text field
        T="FirstName",  # Field Name
        Rect=[10, 10, 50, 20],
    )
    page.Annots = src.make_indirect([widget])

    dest = pikepdf.new()
    transforms = [
        PageTransform(pdf=src, index=0, rotation=(0, False), scale=1.0),
        PageTransform(pdf=src, index=0, rotation=(0, False), scale=1.0),
    ]

    add_pages(dest, [src], transforms)

    # In a hardened version, these should be distinct objects in the destination
    w1 = dest.pages[0].Annots[0]
    w2 = dest.pages[1].Annots[0]

    # ASSERTION 1: Physical Object Independence
    # If unhardened, these will likely have the same objgen because they
    # were pulled from the same Clean Master dictionary reference.
    assert w1.objgen != w2.objgen, "CRITICAL: Page 1 and Page 2 share the same Widget Object ID!"

    # ASSERTION 2: Structural Data Independence
    # Change the field name on Page 1. Page 2 should remain "FirstName".
    saved_w2_T = str(w2.T)
    w1.T = "ChangedName"
    assert str(w2.T) == saved_w2_T, "REGRESSION: Form field name 'bled' from Page 1 to Page 2"


def test_prove_physical_sharing_is_broken():
    """
    If this test fails, it proves the unhardened version is producing
    a structurally invalid PDF where two pages 'own' the same object.
    """
    src = pikepdf.new()
    page = src.add_blank_page(page_size=(100, 100))

    # Create an annotation as an indirect object
    annot = src.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Link, Rect=[0, 0, 10, 10])
    )
    page.Annots = src.make_indirect([annot])

    dest = pikepdf.new()
    transforms = [
        PageTransform(pdf=src, index=0, rotation=(0, False), scale=1.0),
        PageTransform(pdf=src, index=0, rotation=(0, False), scale=1.0),
    ]

    add_pages(dest, [src], transforms)

    # Get the raw object references from the PDF arrays
    # We use .objgen to get the (Number, Generation) of the underlying PDF object
    p1_annot_ref = dest.pages[0].Annots[0].objgen
    p2_annot_ref = dest.pages[1].Annots[0].objgen

    # IN THE UNHARDENED VERSION:
    # Both will point to the same object (e.g., 4 0 R).
    # THIS IS ILLEGAL in PDF if the annotation has a /P (Parent) key,
    # because the parent can't be both Page 1 and Page 2.
    assert p1_annot_ref != p2_annot_ref, (
        f"Structural Failure: Page 1 and Page 2 both point to PDF Object {p1_annot_ref}. "
        "Annotations must be unique instances."
    )


def test_clean_master_reuse_collision():
    """
    Targets the specific case: 'pdftl A.pdf A.pdf'
    Where the second instance is created via dict(clean_master).
    """
    src = pikepdf.new()
    page = src.add_blank_page(page_size=(100, 100))

    # Create an annotation with a unique ID
    annot = src.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Annot, Subtype=pikepdf.Name.Link, Rect=[0, 0, 10, 10])
    )
    page.Annots = src.make_indirect([annot])

    dest = pikepdf.new()
    # Same source page, twice.
    # Instance 0 -> copy_foreign (Clean Master created)
    # Instance 1 -> dict(clean_master) (Potential shared reference)
    transforms = [
        PageTransform(pdf=src, index=0, rotation=(0, False), scale=1.0),
        PageTransform(pdf=src, index=0, rotation=(0, False), scale=1.0),
    ]

    add_pages(dest, [src], transforms)

    p1 = dest.pages[0]
    p2 = dest.pages[1]

    # 1. Check for shared Object ID
    # If unhardened, Instance 1's Annots array likely points
    # to the exact same Object ID as Instance 0.
    assert p1.Annots[0].objgen != p2.Annots[0].objgen, (
        "Structural Failure: Page 1 and Page 2 share the same Annotation Object ID "
        "because of Clean Master reuse!"
    )

    # 2. Check for mutation bleed
    # Modify the URI on Page 1 (if it had one) or the Rect.
    p1.Annots[0].Rect = [100, 100, 110, 110]

    # If they are shared, Page 2's Rect will have changed too.
    assert list(p2.Annots[0].Rect) == [0, 0, 10, 10], (
        "Memory Failure: Page 2 annotation was mutated by Page 1 "
        "due to shared reference in clean_masters_map."
    )


def test_unhardened_deep_mutation_break():
    """
    If unhardened, Instance 2 will have its internal
    dictionary values changed by Instance 1.
    """
    src = pikepdf.new()
    page = src.add_blank_page(page_size=(100, 100))

    # Create an annotation with a dictionary inside (an Action)
    link = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Link,
        Rect=[0, 0, 10, 10],
        A=pikepdf.Dictionary(URI="https://original.com"),
    )
    page.Annots = src.make_indirect([link])

    dest = pikepdf.new()
    transforms = [
        PageTransform(pdf=src, index=0, rotation=(0, False), scale=1.0),
        PageTransform(pdf=src, index=0, rotation=(0, False), scale=1.0),
    ]

    # PASS 1: This uses the clean_master optimization
    add_pages(dest, [src], transforms)

    # Access the URI on both pages
    # Even if they are (0,0), they point to the same memory-resident dict
    u1 = dest.pages[0].Annots[0].A
    u2 = dest.pages[1].Annots[0].A

    # MUTATE Page 1
    u1.URI = "https://mutated.com"

    # ASSERT Page 2
    # In unhardened code, u2.URI will now be "https://mutated.com"
    # because 'clean_master' gave them the same pointer.
    assert str(u2.URI) == "https://original.com", "FAILURE: Deep mutation bleed!"


def test_clean_master_pollution():
    """
    Ensures that modifying an instance doesn't pollute the
    underlying master object for future instances.
    """
    src = pikepdf.new()
    page = src.add_blank_page(page_size=(100, 100))

    # Create a widget with a known name
    widget = pikepdf.Dictionary(
        Type=pikepdf.Name.Annot,
        Subtype=pikepdf.Name.Widget,
        FT=pikepdf.Name.Tx,
        T="TargetField",
        Rect=[0, 0, 10, 10],
    )
    page.Annots = src.make_indirect([widget])

    dest = pikepdf.new()
    # We need Instance 0 to be processed and 'wrangled'
    # before Instance 1 is finalized.
    transforms = [
        PageTransform(pdf=src, index=0, rotation=(0, False), scale=1.0),
        PageTransform(pdf=src, index=0, rotation=(0, False), scale=1.0),
    ]

    add_pages(dest, [src], transforms)

    # If the unhardened master was polluted:
    # Instance 0 was renamed to "TargetField" (or stayed same)
    # Instance 1 was renamed to "TargetField_1"
    # BUT, if they shared a master, the master itself now says "TargetField_1"

    # Check if a THIRD instance (new call) starts clean
    dest2 = pikepdf.new()
    add_pages(dest2, [src], [transforms[0]])

    final_val = str(dest2.pages[0].Annots[0].T)
    assert final_val == "TargetField", (
        f"Master Pollution: The source master was modified! "
        f"Expected 'TargetField', got '{final_val}'"
    )
