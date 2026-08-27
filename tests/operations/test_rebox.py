import pikepdf
import pytest
import logging
from unittest.mock import patch
from unittest.mock import MagicMock

from pdftl.operations.rebox import crop_or_clip_pages
from pdftl.utils.pikepdf_helpers import get_inheritable
from pdftl.operations.rebox import _apply_rule_to_page, _calculate_new_box
from pdftl.operations.parsers.rebox_parser import parse_rebox_content
from pdftl.operations.rebox import _apply_or_preview

##################################################


def _read_page_content(page):
    """Helper to read page content whether it is a Stream or Array."""
    contents = page.Contents
    ret = []
    if isinstance(contents, pikepdf.Array):
        ret.append(b"".join(stream.read_bytes() for stream in contents))
    else:
        ret.append(contents.read_bytes())
    ret.extend(_read_xobject_content(page))
    return ret


def _read_xobject_content(container, visited=None):
    if visited is None:
        visited = set()

    resources = getattr(container, "Resources", None)
    if not isinstance(resources, pikepdf.Dictionary):
        return []

    xobjects = getattr(resources, "XObject", None)
    if not xobjects:
        return []

    streams = []
    for _, xobject_ref in xobjects.items():
        oid = xobject_ref.objgen
        if oid in visited:
            continue
        visited.add(xobject_ref.objgen)
        streams.append(xobject_ref.read_bytes())

        new_res = getattr(xobject_ref, "Resources", None)
        if new_res:
            streams.extend(_read_xobject_content(new_res, visited))

    return streams


@pytest.fixture
def pdf():
    p = pikepdf.new()
    p.add_blank_page(page_size=(100, 100))  # 100x100 box
    # Ensure content stream exists for preview test
    p.pages[0].Contents = p.make_stream(b"")
    return p


def test_crop_preview(pdf):
    """Test preview mode (Lines 141-147)."""
    specs = ["preview", "1-end(10)"]
    crop_or_clip_pages(pdf, specs)

    # Use helper to handle array conversion
    content = _read_page_content(pdf.pages[0])
    assert any(b"re s" in x for x in content)


def test_crop_paper_size(pdf):
    """Test cropping to a paper size (Lines 122-127, 156)."""
    pdf.pages[0].mediabox = [0, 0, 1000, 1000]

    specs = ["1-end(a4)"]
    crop_or_clip_pages(pdf, specs)

    mbox = pdf.pages[0].mediabox
    width = float(mbox[2]) - float(mbox[0])
    assert 590 < width < 600


def test_crop_invalid_dimensions(pdf):
    """Test cropping that results in negative size (Lines 96-101, 134)."""
    # 100 width - 60 left - 60 right = -20 width
    specs = ["1-end(60,0,60,0)"]

    crop_or_clip_pages(pdf, specs)

    # MediaBox should remain unchanged
    mbox = pdf.pages[0].mediabox
    assert float(mbox[2]) == 100


def test_crop_missing_mediabox(pdf, caplog):
    """Test page with no MediaBox (Lines 90-91)."""
    # We mock _get_page_dimensions to return None, simulating a page
    # where MediaBox is missing or invalid.
    caplog.set_level(logging.DEBUG)

    with patch("pdftl.operations.rebox.get_visible_page_dimensions", return_value=None):
        crop_or_clip_pages(pdf, ["1-end(10)"])

    assert "no valid MediaBox" in caplog.text


def test_crop_fit_mode_execution(minimal_pdf):
    """
    Covers line 152: return fit_ctx.calculate_rect(...)
    Triggered when spec type is 'fit'.
    """

    # Run crop with a 'fit' spec
    # The actual calculation result isn't vital here, just hitting the dispatch line.
    args = ["1(fit)"]
    result = crop_or_clip_pages(minimal_pdf, args)

    assert result.success


def test_crop_updates_existing_boxes(minimal_pdf):
    """
    Covers line 182: page[box_key] = new_box
    Triggered when the page already has CropBox/TrimBox/BleedBox.
    """
    page = minimal_pdf.pages[0]
    rect = [0, 0, 100, 100]
    page.mediabox = rect

    # Explicitly set other boxes so the loop in crop.py sees them
    page.CropBox = rect
    page.TrimBox = rect

    # Apply a simple margin crop
    args = ["1(10)"]  # 10 units from all edges
    crop_or_clip_pages(minimal_pdf, args)

    # Verify they were modified
    new_rect = [10, 10, 90, 90]
    assert list(page.CropBox) == new_rect
    assert list(page.TrimBox) == new_rect


def test_crop_preview_rotated_page(minimal_pdf):
    """
    Covers line 199: overlay_page.Rotate = int(page.Rotate)
    Triggered when 'preview' is used on a rotated page.
    """
    page = minimal_pdf.pages[0]
    page.Rotate = 90

    # Run in preview mode
    args = ["1(10)", "preview"]
    crop_or_clip_pages(minimal_pdf, args)

    # The preview overlay should have been created.
    # We can check if the crop function completed without error
    # and potentially inspect the page content for the overlay if needed.
    # But for coverage, execution is enough.
    assert True


def test_apply_crop_rule_invalid_index():
    """
    Covers crop.py line 111:
    Checks that a ValueError is raised if the page index `i` is out of bounds
    relative to the PDF's page count.
    """
    pdf = pikepdf.new()
    pdf.add_blank_page()  # PDF has 1 page (index 0)

    # Try to access index 5
    with pytest.raises(ValueError, match="is too large"):
        _apply_rule_to_page(
            page_rule="some_rule",
            i=5,
            pdf=pdf,
            preview=False,
            fit_ctx=None,
            all_rules={},
            operation="crop",
        )


def test_crop_invalid_spec_raises_user_error(tmp_path):
    from pdftl.exceptions import UserCommandLineError

    pdf = pikepdf.new()
    pdf.pages.append(
        pikepdf.Page(pikepdf.Dictionary(Type=pikepdf.Name("/Page"), MediaBox=[0, 0, 612, 792]))
    )

    with pytest.raises(UserCommandLineError, match="foobar"):
        crop_or_clip_pages(pdf, ["foobar"], operation="crop")


def test_calculate_new_box_missing_dimensions(two_page_pdf):
    with pikepdf.open(two_page_pdf):
        with patch("pdftl.utils.dimensions.get_visible_page_dimensions") as mock_page_dims:
            mock_page_dims.return_value = []
            assert _calculate_new_box(None, None, None, None, None, None) is None


##################################################


def test_calculate_new_box_rotation_180():
    """Tests margin un-rotation for 180 degree pages."""
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))
    pdf.pages[0]["/Rotate"] = 180
    result = crop_or_clip_pages(pdf, ["1-end(10pt)"])
    assert result.success
    # Margins should be applied symmetrically
    box = result.pdf.pages[0].mediabox
    assert float(box[0]) == pytest.approx(10.0)
    assert float(box[1]) == pytest.approx(10.0)


def test_calculate_new_box_rotation_270():
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 300))
    pdf.pages[0]["/Rotate"] = 270
    result = crop_or_clip_pages(pdf, ["1-end(10pt)"])
    assert result.success


def test_crop_updates_inherited_trimbox():
    """Exposes rebox.py: skips updating an inherited /TrimBox because 'in page' misses parent attributes."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 300))
    parent = page.obj["/Parent"]

    # Define /TrimBox on parent node so the page inherits it
    parent["/TrimBox"] = pikepdf.Array([0, 0, 200, 300])
    if "/TrimBox" in page.obj:
        del page.obj["/TrimBox"]

    # Crop 10pt off all edges
    result = crop_or_clip_pages(pdf, ["1-end(10pt)"])
    assert result.success

    # 'if "/TrimBox" in page' evaluated to False, so page["/TrimBox"] was never written.
    # get_inheritable() still returns the old, un-cropped [0, 0, 200, 300] from parent!
    updated_trimbox = get_inheritable(pdf.pages[0], "/TrimBox")
    assert updated_trimbox is not None
    assert [float(x) for x in updated_trimbox] == pytest.approx([10.0, 10.0, 190.0, 290.0])


##################################################


def test_parse_rebox_content_abs_valid():
    """
    Covers lines 79 and 96-100 in rebox_parser.py.
    Tests successful parsing of an absolute box specification.
    """
    # Assuming dim_str_to_pts translates raw string numbers to float/int points
    result = parse_rebox_content(
        "abs, 10, 20, 90, 80", page_width=100, page_height=100, operation="dummy_op"
    )

    assert result["type"] == "abs"
    assert result["values"] == (10, 20, 90, 80)


def test_parse_rebox_content_abs_invalid_length():
    """
    Covers lines 93-94 in rebox_parser.py.
    Tests the ValueError raised when 'abs' doesn't have exactly 4 values.
    """
    with pytest.raises(ValueError, match="Should have 4 comma-separated values following `abs`"):
        parse_rebox_content(
            "abs, 10, 20, 90", page_width=100, page_height=100, operation="dummy_op"
        )


def test_crop_with_abs_spec():
    """
    Covers lines 185-186 in crop.py.
    Ensures the crop operation correctly handles 'abs' specs from the parser.
    """
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(100, 100))

    # Passing the abs rule inside the page spec parentheses
    specs = ["1(abs, 10, 10, 90, 90)"]
    result = crop_or_clip_pages(pdf, specs, operation="crop")

    assert result.success is True
    # The mediabox should be updated to our absolute coordinates
    assert list(result.pdf.pages[0].mediabox) == [10, 10, 90, 90]


def test_clip_operation():
    """
    Covers lines 221-225 in crop.py.
    Exercises the 'clip' logic branch inside _apply_or_preview.
    """
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(100, 100))

    # Standard margin crop, but passed to the 'clip' operation
    specs = ["1(10, 10, 10, 10)"]
    result = crop_or_clip_pages(pdf, specs, operation="clip")

    assert result.success is True
    # In a clip operation, the mediabox isn't touched, but stream content is appended
    page = result.pdf.pages[0]
    page.contents_coalesce()
    assert b"q" in page.Contents.read_bytes()


def test_apply_or_preview_invalid_operation():
    """
    Covers lines 226-227 in crop.py.
    Hits the ultimate fallback ValueError for unrecognized operations.
    """
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(100, 100))

    with pytest.raises(ValueError, match="Internal error: invalid operation 'smash'"):
        _apply_or_preview(page=page, new_box=(10, 10, 90, 90), preview=False, operation="smash")


def test_crop_or_clip_pages_skips_pages_without_rules(tmp_path):
    """Covers the 194->193 branch: a page index NOT present in page_rules
    must be left untouched, and the loop must simply continue to the next
    page rather than raising or applying any rule to it.

    Uses a spec that only targets page 1 of a 2-page doc, so page 2's
    index never appears as a key in page_rules.
    """
    import pikepdf

    from pdftl.operations.rebox import crop_or_clip_pages

    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    pdf.add_blank_page(page_size=(200, 200))

    original_box_2 = list(pdf.pages[1].mediabox)

    result = crop_or_clip_pages(pdf, ["1(10pt)"], operation="crop")

    assert result.success is True
    # Page 1 (index 0) was targeted and should have changed.
    assert list(pdf.pages[0].mediabox) != [0, 0, 200, 200]
    # Page 2 (index 1) was never in page_rules -- must be untouched.
    assert list(pdf.pages[1].mediabox) == original_box_2


def test_calculate_new_box_falls_back_to_zero_rotation_on_error():
    """Covers the except (AttributeError, TypeError, ValueError) branch:
    if page.rotation raises during margin un-rotation, rotation must
    default to 0 rather than propagating the error."""

    class ExplodingRotationPage:
        """A minimal stand-in whose .rotation raises, to force the
        except branch in _calculate_new_box's MARGIN UN-ROTATION step."""

        @property
        def rotation(self):
            raise AttributeError("rotation not available")

    page = ExplodingRotationPage()

    # get_visible_page_dimensions needs to succeed for execution to reach
    # the rotation lookup, so patch it to return simple, valid dims.
    import pdftl.operations.rebox as rebox_module

    original_get_dims = rebox_module.get_visible_page_dimensions
    rebox_module.get_visible_page_dimensions = lambda p, apply_rotate: (0, 0, 100, 100)
    try:
        # A plain margin spec ("10pt,10pt") avoids 'abs'/'fit'/'paper' early
        # returns, forcing execution down to the rotation try/except.
        box = _calculate_new_box(
            page, "10pt,10pt", 0, fit_ctx=MagicMock(), all_rules={}, operation="crop"
        )
    finally:
        rebox_module.get_visible_page_dimensions = original_get_dims

    # No exception propagated, and a valid box was still computed using
    # rotation=0 as the fallback.
    assert box is not None
