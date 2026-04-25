# test_geometry_bugs.py


import pikepdf
import pytest

import pdftl


def make_pdf_with_mediabox(mediabox: list, content: bytes = b"") -> pikepdf.Pdf:
    """Create single-page PDF with given MediaBox."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    page.MediaBox = pikepdf.Array(mediabox)
    if content:
        page.contents_add(content)
    return pdf


def make_rotated_pdf(size=(595, 842), rotation=90) -> pikepdf.Pdf:
    """Create single-page PDF with /Rotate set."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=size)
    page.Rotate = rotation
    return pdf


def get_mediaboxes(pdf: pikepdf.Pdf) -> list[list[float]]:
    return [[float(v) for v in page.MediaBox] for page in pdf.pages]


def get_prepended_cm_matrix(page) -> list[float] | None:
    """Extract the first 'cm' matrix from a page content stream."""
    ops = pikepdf.parse_content_stream(page)
    for operands, operator in ops:
        if str(operator) == "cm":
            return [float(x) for x in operands]
    return None


class TestChopNonZeroOrigin:
    """Bug: chop computes rects relative to 0, not mediabox origin."""

    def test_rows_nonzero_y_origin_boxes_stay_within_mediabox(self, tmp_path):
        src = make_pdf_with_mediabox([0, 200, 400, 600])
        infile = tmp_path / "in.pdf"
        src.save(infile)

        result = pdftl.api.chop(inputs=[str(infile)], operation_args=["rows2"])
        boxes = get_mediaboxes(result)

        assert len(boxes) == 2
        # Both output boxes must lie within the original [0,200,400,600]
        for box in boxes:
            assert box[1] >= 200, f"y0={box[1]} is below mediabox origin 200"
            assert box[3] <= 600, f"y1={box[3]} is above mediabox top 600"

    def test_rows_nonzero_y_origin_boxes_tile_exactly(self, tmp_path):
        src = make_pdf_with_mediabox([0, 200, 400, 600])
        infile = tmp_path / "in.pdf"
        src.save(infile)

        result = pdftl.api.chop(inputs=[str(infile)], operation_args=["rows2"])
        boxes = sorted(get_mediaboxes(result), key=lambda b: b[1])  # sort by y0

        assert len(boxes) == 2
        # Boxes should share a boundary and together cover [0,200,400,600]
        assert boxes[0][1] == pytest.approx(200)  # bottom of lower box = mediabox bottom
        assert boxes[1][3] == pytest.approx(600)  # top of upper box = mediabox top
        assert boxes[0][3] == pytest.approx(boxes[1][1])  # they meet in the middle

    def test_cols_nonzero_x_origin_boxes_tile_exactly(self, tmp_path):
        src = make_pdf_with_mediabox([100, 0, 500, 400])
        infile = tmp_path / "in.pdf"
        src.save(infile)

        result = pdftl.api.chop(inputs=[str(infile)], operation_args=["cols2"])
        boxes = sorted(get_mediaboxes(result), key=lambda b: b[0])

        assert len(boxes) == 2
        assert boxes[0][0] == pytest.approx(100)
        assert boxes[1][2] == pytest.approx(500)
        assert boxes[0][2] == pytest.approx(boxes[1][0])


def test_chop_rows_then_rotate_equals_rotate_then_chop_rows(tmp_path):
    """
    For an unrotated page, chop-then-rotate and rotate-then-chop should
    give the same visual result.
    """
    # A4 portrait: physical 595w × 842h. After rotate east: visual 842w × 595h.
    src = make_rotated_pdf(size=(595, 842), rotation=0)
    infile = tmp_path / "in.pdf"
    src.save(infile)

    # rotate east then chop
    rotated = pdftl.api.rotate(inputs=[str(infile)], operation_args=["east"])
    rotated_file = tmp_path / "rotated.pdf"
    rotated.save(rotated_file)

    chopped = pdftl.api.chop(inputs=[str(rotated_file)], operation_args=["rows2"])

    # Check that rotation is preserved
    assert int(chopped.pages[0].get("/Rotate", 0)) == 90

    boxes = get_mediaboxes(chopped)
    assert len(boxes) == 2

    # Since the page is rotated 90 degrees, physical width is visual height!
    # rows2 should split the visual height (595) into two 297.5 pieces.
    # Therefore, the physical width (b[2] - b[0]) should be 297.5.
    visual_heights = [b[2] - b[0] for b in boxes]
    for vh in visual_heights:
        assert vh == pytest.approx(595 / 2, abs=1), f"Expected visual height ~297.5, got {vh}"


def test_chop_rotated_nonzero_origin_boxes_map_axes_correctly(tmp_path):
    # 1. Create a PDF with non-zero origin and a 90-degree rotation
    # Physical bounds: X:[100, 600] (W=500), Y:[200, 900] (H=700)
    src = make_pdf_with_mediabox([100, 200, 600, 900])
    src.pages[0].Rotate = 90
    infile = tmp_path / "in_rotated.pdf"
    src.save(infile)

    # 2. Chop into columns (visually splits the 700 visual width into two 350 pieces)
    result = pdftl.api.chop(inputs=[str(infile)], operation_args=["cols2"])

    # Ensure rotation flag was preserved
    assert int(result.pages[0].get("/Rotate", 0)) == 90
    assert int(result.pages[1].get("/Rotate", 0)) == 90

    # Sort boxes by their physical Y origin since a 90-deg visual col-chop
    # cuts across the physical Y-axis.
    boxes = sorted(get_mediaboxes(result), key=lambda b: b[1])

    assert len(boxes) == 2

    # 3. Expected Bounding Boxes
    # Since rotation is PRESERVED, the physical X-axis (100 to 600) remains untouched.
    # The physical Y-axis (200 to 900) is what gets cut in half to create visual columns.

    # Expected Box 0: X stays 100-600. Y is cut from 200 to 550.
    expected_box0 = [100.0, 200.0, 600.0, 550.0]

    # Expected Box 1: X stays 100-600. Y is cut from 550 to 900.
    expected_box1 = [100.0, 550.0, 600.0, 900.0]

    assert boxes[0] == pytest.approx(expected_box0)
    assert boxes[1] == pytest.approx(expected_box1)


class TestPlaceShiftRotatedPage:
    """Bug: place shift percentage uses raw box dimensions, ignores /Rotate."""

    def test_shift_50pct_horizontal_on_rotated_page(self, tmp_path):
        """
        A 90°-rotated A4 page is visually 842 wide × 595 tall.
        shift=50%,0 should translate by 421pt (50% of 842), not 297.5pt (50% of 595).
        """
        src = make_rotated_pdf(size=(595, 842), rotation=90)
        infile = tmp_path / "in.pdf"
        src.save(infile)

        result = pdftl.api.place(inputs=[str(infile)], operation_args=["(shift=50%,0)"])
        page = result.pages[0]
        matrix = get_prepended_cm_matrix(page)

        assert matrix is not None
        # Matrix is [a, b, c, d, e, f] — e is x-translation
        # On a 90-degree rotated page, a visual horizontal shift is a physical vertical shift (ty)
        ty = matrix[5]
        assert ty == pytest.approx(842 / 2, abs=1), (
            f"Expected ty≈421 (50% of visual width 842 mapped to physical Y), got {ty}"
        )

    def test_shift_0_50pct_vertical_on_rotated_page(self, tmp_path):
        """
        A 90°-rotated A4 page is visually 842 wide × 595 tall.
        shift=0,50% should translate by 297.5pt (50% of 595), not 421pt (50% of 842).
        """
        src = make_rotated_pdf(size=(595, 842), rotation=90)
        infile = tmp_path / "in.pdf"
        src.save(infile)

        result = pdftl.api.place(inputs=[str(infile)], operation_args=["(shift=0,50%)"])
        page = result.pages[0]
        matrix = get_prepended_cm_matrix(page)

        assert matrix is not None
        # On a 90-degree rotated page, a visual vertical shift is a physical horizontal shift (tx)
        tx = matrix[4]
        assert abs(tx) == pytest.approx(595 / 2, abs=1), (
            f"Expected |tx|≈297.5 (50% of visual height 595 mapped to physical X), got {tx}"
        )


class TestBookletRotatedPages:
    """Bug: booklet canvas sizing ignores /Rotate."""

    def test_booklet_canvas_size_with_rotated_pages(self, tmp_path):
        """
        4 landscape pages (via /Rotate 90 on portrait A4).
        Visual size: 842w × 595h. Booklet should create canvas ≈ 1684 × 595.
        Currently creates ≈ 1190 × 842 because it reads raw box dimensions.
        """
        src = pikepdf.new()
        for _ in range(4):
            page = src.add_blank_page(page_size=(595, 842))
            page.Rotate = 90
        infile = tmp_path / "in.pdf"
        src.save(infile)

        result = pdftl.api.booklet(pdf=str(infile))
        canvas_box = [float(v) for v in result.pages[0].MediaBox]
        canvas_w = canvas_box[2] - canvas_box[0]
        canvas_h = canvas_box[3] - canvas_box[1]

        # Visual width of rotated page = 842, so 2-up canvas should be ~1684 wide
        assert canvas_w == pytest.approx(842 * 2, abs=2), (
            f"Expected canvas width≈1684, got {canvas_w}"
        )
        assert canvas_h == pytest.approx(595, abs=2), f"Expected canvas height≈595, got {canvas_h}"


##################################################


def test_chop_rotation_180_and_270(tmp_path):
    # --- Test 180 Degrees ---
    # Physical: [0, 0, 400, 600]. Visual (upside down): 400x600.
    src_180 = make_pdf_with_mediabox([0, 0, 400, 600])
    src_180.pages[0].Rotate = 180
    in_180 = tmp_path / "180.pdf"
    src_180.save(in_180)

    # cols2 visually splits X (0-400) into [0, 200] and [200, 400]
    # At 180 deg, visual left maps to physical right.
    res_180 = pdftl.api.chop(inputs=[str(in_180)], operation_args=["cols2"])
    boxes_180 = sorted(get_mediaboxes(res_180), key=lambda b: b[0])
    assert len(boxes_180) == 2
    assert boxes_180[0] == pytest.approx([0.0, 0.0, 200.0, 600.0])
    assert boxes_180[1] == pytest.approx([200.0, 0.0, 400.0, 600.0])

    # --- Test 270 Degrees ---
    # Physical: [0, 0, 400, 600]. Visual (rotated 90 CCW): 600x400.
    src_270 = make_pdf_with_mediabox([0, 0, 400, 600])
    src_270.pages[0].Rotate = 270
    in_270 = tmp_path / "270.pdf"
    src_270.save(in_270)

    # cols2 visually splits X (0-600) into [0, 300] and [300, 600]
    # At 270 deg, visual X maps to physical Y.
    res_270 = pdftl.api.chop(inputs=[str(in_270)], operation_args=["cols2"])
    boxes_270 = sorted(get_mediaboxes(res_270), key=lambda b: b[1])
    assert len(boxes_270) == 2
    assert boxes_270[0] == pytest.approx([0.0, 0.0, 400.0, 300.0])
    assert boxes_270[1] == pytest.approx([0.0, 300.0, 400.0, 600.0])


def test_chop_cleans_up_other_bounding_boxes(tmp_path):
    src = make_pdf_with_mediabox([0, 0, 400, 600])

    # Inject extra bounding boxes to trigger the cleanup loop
    src.pages[0].CropBox = [10, 10, 390, 590]
    src.pages[0].BleedBox = [5, 5, 395, 595]

    infile = tmp_path / "extra_boxes.pdf"
    src.save(infile)

    # Chop the page
    res = pdftl.api.chop(inputs=[str(infile)], operation_args=["rows2"])

    # Assert that the new chopped pages no longer have the extra boxes
    for page in res.pages:
        assert "/CropBox" not in page
        assert "/BleedBox" not in page
        assert "/MediaBox" in page  # MediaBox must survive


def content_stream_has_90deg_rotation(page: pikepdf.Page) -> bool:
    """
    Checks if the page content stream contains a 'cm' (Current Matrix) operator
    that applies a +/- 90 degree rotation (possibly with scaling).
    In a matrix [a b c d e f], a 90-degree rotation means a ≈ 0, d ≈ 0,
    and b, c are non-zero (specifically b = -c).
    """
    ops = pikepdf.parse_content_stream(page)
    for operands, operator in ops:
        if str(operator) == "cm" and len(operands) == 6:
            a, b, c, d, e, f = [float(x) for x in operands]
            # Check for a 90/270 rotation matrix signature
            if abs(a) < 0.001 and abs(d) < 0.001 and abs(b) > 0.001 and abs(c) > 0.001:
                return True
    return False
