import pikepdf

from pdftl.pages.add_pages import add_pages
from pdftl.utils.page_specs import PageTransform


def test_regression_independent_page_rotation():
    """
    Regression Test: Ensure transformations on one instance of a page
    do not 'poison' the master copy used for subsequent instances.

    Scenario:
    1. Add Page 1 with 90-degree rotation.
    2. Add Page 1 again with 0-degree rotation.

    Failure Mode: Both pages end up rotated 90 degrees because instance #2
    cloned the already-rotated instance #1.
    """
    # 1. Create a simple source PDF in memory
    src = pikepdf.new()
    src.add_blank_page(page_size=(100, 100))

    # 2. Define the transforms: [Rotate 90, No Rotate]
    # We use the exact same source page (src, index=0) for both.
    transforms = [
        PageTransform(pdf=src, index=0, rotation=(90, False), scale=1.0),
        PageTransform(pdf=src, index=0, rotation=(0, False), scale=1.0),
    ]

    dest = pikepdf.new()

    # 3. Run the function under test
    add_pages(dest, [src], transforms)

    # 4. Assertions
    p1 = dest.pages[0]
    p2 = dest.pages[1]

    # Page 1 must be rotated
    assert p1.get("/Rotate") == 90, "First page should be rotated 90 degrees"

    # Page 2 must NOT be rotated (This is where the regression fails)
    # It might be 0 or simply missing the /Rotate key.
    p2_rot = p2.get("/Rotate", 0)
    assert p2_rot == 0, f"Regression detected: Second page inherited rotation {p2_rot}"
