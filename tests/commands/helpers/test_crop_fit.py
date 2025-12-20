import sys
from unittest import mock

import pytest

# Import the module under test
from pdftl.commands.helpers.crop_fit import FitCropContext, get_visible_bbox

# --- Mocks for Dependencies ---


class MockPikePdfPage:
    def __init__(self, cropbox=(0, 0, 595, 842)):
        # Simulate pikepdf.Page behavior regarding box access
        self.cropbox = cropbox
        self.mediabox = cropbox


class MockPdfiumPage:
    def __init__(self, width=100, height=100, bbox_px=None):
        self.width = width
        self.height = height
        self._bbox_px = bbox_px  # (left, top, right, bottom) in pixels
        self.rotation = 0

    def set_rotation(self, rot):
        self.rotation = rot

    def render(self, scale=1.0):
        # Create a mock that returns a PIL image
        mock_bitmap = mock.Mock()
        mock_pil = mock.Mock()

        # Determine image size based on scale
        w = int(self.width * scale)
        h = int(self.height * scale)
        mock_pil.size = (w, h)

        # Mock the inversion process:
        # In the source code: inverted.getbbox()
        # We need to mock ImageOps.invert(pil_image.convert("RGB")).getbbox()

        # Since we can't easily mock the chained result of ImageOps.invert...
        # ...we will mock the behavior inside get_visible_bbox by patching ImageOps.

        mock_bitmap.to_pil.return_value = mock_pil
        return mock_bitmap


# --- Tests ---


class TestFitCropContext:

    @pytest.fixture
    def mock_pikepdf_doc(self):
        doc = mock.Mock()
        doc.pages = [MockPikePdfPage() for _ in range(5)]  # 5 dummy pages
        return doc

    def test_lazy_initialization(self, mock_pikepdf_doc):
        """Test that pdfium is not loaded until .doc is accessed."""
        ctx = FitCropContext(mock_pikepdf_doc)
        assert ctx._pdfium_doc is None

        # Mock init logic to avoid real file I/O
        with mock.patch.object(ctx, "_init_pdfium_doc") as mock_init:
            _ = ctx.doc
            mock_init.assert_called_once()

    def test_init_raises_importerror_if_missing_deps(self, mock_pikepdf_doc):
        """Test that missing dependencies raise a helpful ImportError."""
        ctx = FitCropContext(mock_pikepdf_doc)

        # Simulate pypdfium2 missing from sys.modules
        with mock.patch.dict(sys.modules, {"pypdfium2": None}):
            with pytest.raises(ImportError, match="requires 'pypdfium2' and 'pillow'"):
                ctx._init_pdfium_doc()

    def test_init_pdfium_success(self, mock_pikepdf_doc):
        """Test the actual _init_pdfium_doc logic (mocking external libs)."""
        ctx = FitCropContext(mock_pikepdf_doc)

        with (
            mock.patch("pdftl.commands.helpers.crop_fit.io.BytesIO") as mock_io,
            mock.patch("pypdfium2.PdfDocument") as mock_pdfium_cls,
        ):

            ctx._init_pdfium_doc()

            # Should save pikepdf doc to buffer
            mock_pikepdf_doc.save.assert_called()
            # Should init pdfium from that buffer
            mock_pdfium_cls.assert_called()
            assert ctx._pdfium_doc is not None

    def test_calculate_rect_fit_simple(self, mock_pikepdf_doc):
        """Test basic 'fit' mode for a single page."""
        ctx = FitCropContext(mock_pikepdf_doc)
        ctx._pdfium_doc = [
            mock.Mock() for _ in range(5)
        ]  # Mock pdfium pages logic handled in helper test

        parsed = {"mode": "fit", "padding": (10, 20, 30, 40)}  # L, T, R, B

        # Mock the helper function
        with mock.patch("pdftl.commands.helpers.crop_fit.get_visible_bbox") as mock_get_bbox:
            # Helper returns (0, 0, 100, 100) (Left, Bottom, Right, Top)
            mock_get_bbox.return_value = (0, 0, 100, 100)

            # Padding is:
            # Left: -10
            # Bottom: -40 (parsed order is L, T, R, B -> dest returns L, B, R, T logic)
            # Wait, function uses: pad_l, pad_t, pad_r, pad_b = padding
            # return bbox[0] - pad_l, bbox[1] - pad_b, bbox[2] + pad_r, bbox[3] + pad_t

            result = ctx.calculate_rect(0, parsed, "fit", {})

            # Expected:
            # x1 = 0 - 10 = -10
            # y1 = 0 - 40 = -40
            # x2 = 100 + 30 = 130
            # y2 = 100 + 20 = 120
            assert result == (-10, -40, 130, 120)

            mock_get_bbox.assert_called_once()

    def test_calculate_rect_fit_group_explicit(self, mock_pikepdf_doc):
        """Test 'fit-group' with explicit range (e.g., '1-3')."""
        ctx = FitCropContext(mock_pikepdf_doc)
        ctx._pdfium_doc = [mock.Mock() for _ in range(5)]

        parsed = {"mode": "fit-group", "source": "1-2", "padding": (0, 0, 0, 0)}

        # Mock get_visible_bbox to return different boxes for pages 1 and 2 (indices 0 and 1)
        with mock.patch("pdftl.commands.helpers.crop_fit.get_visible_bbox") as mock_get_bbox:
            mock_get_bbox.side_effect = [
                (10, 10, 20, 20),  # Page 1 (tiny box)
                (0, 0, 100, 100),  # Page 2 (large box)
            ]

            # Should calculate union of these two
            result = ctx.calculate_rect(0, parsed, "fit-group=1-2", {})

            # Union should be min(10,0), min(10,0), max(20,100), max(20,100) -> (0,0,100,100)
            assert result == (0, 0, 100, 100)

            # Ensure it only called twice (once per page in group)
            assert mock_get_bbox.call_count == 2

    def test_calculate_rect_fit_group_caching(self, mock_pikepdf_doc):
        """Test that subsequent calls with same group spec use cache."""
        ctx = FitCropContext(mock_pikepdf_doc)
        ctx._pdfium_doc = [mock.Mock()]
        parsed = {"mode": "fit-group", "source": "1", "padding": (0, 0, 0, 0)}

        # Pre-seed cache
        ctx._group_cache["1"] = (55, 55, 66, 66)

        with mock.patch("pdftl.commands.helpers.crop_fit.get_visible_bbox") as mock_get_bbox:
            result = ctx.calculate_rect(0, parsed, "rule", {})
            assert result == (55, 55, 66, 66)
            mock_get_bbox.assert_not_called()

    def test_calculate_group_union_empty(self, mock_pikepdf_doc):
        """Test behavior when group yields no valid pages or empty boxes."""
        ctx = FitCropContext(mock_pikepdf_doc)
        ctx._pdfium_doc = []  # Empty doc

        # Source "1-5" on empty doc -> indices valid check inside loop
        # The loop iterates `indices`, but `if src_idx >= len(self.doc)` check skips them.
        res = ctx._calculate_group_union("1", "rule", {})
        assert res == (0.0, 0.0, 0.0, 0.0)

    def test_calculate_group_union_implicit(self, mock_pikepdf_doc):
        """Test 'fit-group' with implicit grouping (same rule string)."""
        ctx = FitCropContext(mock_pikepdf_doc)
        ctx._pdfium_doc = [mock.Mock() for _ in range(3)]

        # Setup: Page 0 and 2 share the same rule string. Page 1 is different.
        all_rules = {0: "fit-group", 1: "other", 2: "fit-group"}

        with mock.patch("pdftl.commands.helpers.crop_fit.get_visible_bbox") as mock_get_bbox:
            mock_get_bbox.side_effect = [
                (10, 10, 20, 20),  # Page 0
                (30, 30, 40, 40),  # Page 2
            ]

            res = ctx._calculate_group_union(None, "fit-group", all_rules)

            # Union of (10,10,20,20) and (30,30,40,40)
            # Min: 10, 10
            # Max: 40, 40
            assert res == (10, 10, 40, 40)


class TestGetVisibleBbox:

    # FIX: Patch the global PIL library, not the local module
    @mock.patch("PIL.ImageOps.invert")
    def test_get_visible_bbox_standard(self, mock_invert):
        """
        Test standard visible box calculation.
        Coordinate system: 100x100 page.
        Ink is in center: 25, 25 to 75, 75 (pixels/points).
        """
        # Setup Mock Page
        mock_pdfium_page = MockPdfiumPage(width=100, height=100)

        # Mock Inversion Result
        mock_inverted_img = mock.Mock()
        # getbbox returns (left, top, right, bottom) in PIXELS from Top-Left
        mock_inverted_img.getbbox.return_value = (25, 25, 75, 75)
        mock_invert.return_value = mock_inverted_img

        res = get_visible_bbox(mock_pdfium_page)

        # Logic matches previous expectation
        assert res == (25.0, 25.0, 75.0, 75.0)

    @mock.patch("PIL.ImageOps.invert")
    def test_get_visible_bbox_blank_page(self, mock_invert):
        """Test blank page handling (getbbox returns None)."""
        mock_pdfium_page = MockPdfiumPage(width=50, height=60)

        mock_inverted_img = mock.Mock()
        mock_inverted_img.getbbox.return_value = None  # Blank
        mock_invert.return_value = mock_inverted_img

        res = get_visible_bbox(mock_pdfium_page)

        # Should return full media box (0, 0, 50, 60)
        assert res == (0.0, 0.0, 50.0, 60.0)

    @mock.patch("PIL.ImageOps.invert")
    def test_get_visible_bbox_with_origin_offset(self, mock_invert):
        """
        Test calculation when PDF MediaBox/CropBox doesn't start at (0,0).
        """
        mock_pdfium_page = MockPdfiumPage(width=100, height=100)
        mock_pike_page = MockPikePdfPage(cropbox=(10, 10, 110, 110))

        mock_inverted_img = mock.Mock()
        # Mock ink in top-left corner relative to image
        mock_inverted_img.getbbox.return_value = (0, 0, 10, 10)
        mock_invert.return_value = mock_inverted_img

        res = get_visible_bbox(mock_pdfium_page, pikepdf_page=mock_pike_page)

        # Offset (10,10) is added to the calculated coordinates
        assert res == (10.0, 100.0, 20.0, 110.0)

    @mock.patch("PIL.ImageOps.invert")
    def test_get_visible_bbox_clamping_margin(self, mock_invert):
        """Test that margin expands the box but clamps to page boundaries."""
        mock_pdfium_page = MockPdfiumPage(width=100, height=100)

        mock_inverted_img = mock.Mock()
        mock_inverted_img.getbbox.return_value = (0, 0, 100, 100)
        mock_invert.return_value = mock_inverted_img

        res = get_visible_bbox(mock_pdfium_page, margin=10)

        assert res == (0.0, 0.0, 100.0, 100.0)

    @mock.patch("PIL.ImageOps.invert")
    def test_scaling_logic(self, mock_invert):
        """Test that scaling affects resolution but preserves coordinates."""
        scale = 2.0
        mock_pdfium_page = MockPdfiumPage(width=100, height=100)

        mock_inverted_img = mock.Mock()
        # Ink occupies top-left quadrant in pixels (relative to 200x200 image)
        mock_inverted_img.getbbox.return_value = (0, 0, 100, 100)
        mock_invert.return_value = mock_inverted_img

        res = get_visible_bbox(mock_pdfium_page, scale=scale)

        # (0, 50, 50, 100)
        assert res == (0.0, 50.0, 50.0, 100.0)
