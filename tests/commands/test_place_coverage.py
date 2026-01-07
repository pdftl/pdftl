import pikepdf
import pytest

from pdftl.operations.place import place_content


@pytest.fixture
def minimal_pdf():
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(100, 100))
    return pdf


def get_page_content_bytes(page):
    """Helper to safely read page content bytes regardless of structure."""
    if "/Contents" not in page:
        return b""
    contents = page["/Contents"]
    if isinstance(contents, pikepdf.Array):
        return b"".join(s.read_bytes() for s in contents)
    return contents.read_bytes()


# --- Basic Validation Tests ---


def test_place_invalid_page_is_ignored(minimal_pdf):
    # Tests line 89
    # Request page 5 on a 1-page PDF. Should silently continue (no crash).
    result = place_content(minimal_pdf, ["5(shift=10,10)"])
    assert result.success is True


def test_place_missing_mediabox_returns_none(minimal_pdf):
    # Tests line 108
    # Remove MediaBox to force get_visible_page_dimensions to return None
    del minimal_pdf.pages[0]["/MediaBox"]

    place_content(minimal_pdf, ["1(shift=10,10)"])

    # Logic should return early, meaning NO content should be added.
    # A blank page starts with no content (empty bytes).
    assert get_page_content_bytes(minimal_pdf.pages[0]) == b""


# --- Geometry & Anchor Tests ---


def test_place_spin_with_coord_anchor(minimal_pdf):
    # Tests lines 136-151 (spin) and 173-175 (coord anchor)
    place_content(minimal_pdf, ["1(spin=90:50,50)"])

    # We check for the 'cm' (current matrix) operator which indicates a transform was applied
    data = get_page_content_bytes(minimal_pdf.pages[0])
    assert b"cm" in data


def test_place_anchor_parsing_bottom_right(minimal_pdf):
    # Tests lines 185 (right) and 192 (bottom)
    place_content(minimal_pdf, ["1(scale=0.5:bottom-right)"])

    data = get_page_content_bytes(minimal_pdf.pages[0])
    assert b"cm" in data


# --- Annotation Update Tests ---


def test_place_updates_annotations(minimal_pdf):
    # Tests lines 262-280
    page = minimal_pdf.pages[0]

    annot = pikepdf.Dictionary(
        Type=pikepdf.Name("/Annot"),
        Subtype=pikepdf.Name("/Highlight"),
        Rect=[10, 10, 20, 20],
        QuadPoints=[10, 10, 20, 10, 20, 20, 10, 20],
        AP=pikepdf.Dictionary(N=pikepdf.Stream(minimal_pdf, b"dummy")),
    )

    if "/Annots" not in page:
        page["/Annots"] = pikepdf.Array()
    page["/Annots"].append(annot)

    place_content(minimal_pdf, ["1(shift=10,10)"])

    updated_annot = page["/Annots"][0]

    # Check Rect Updated
    rect = [float(x) for x in updated_annot["/Rect"]]
    assert rect == [20.0, 20.0, 30.0, 30.0]

    # Check QuadPoints Updated
    quads = [float(x) for x in updated_annot["/QuadPoints"]]
    assert quads[0] == 20.0

    # Check AP Deletion
    assert "/AP" not in updated_annot
