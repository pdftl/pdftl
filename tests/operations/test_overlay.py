from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.exceptions import OperationError
from pdftl.operations.overlay import _parse_operation_args, apply_overlay


def test_apply_overlay_empty_pdf():
    mock_input_pdf = MagicMock()
    mock_overlay_pdf = MagicMock()
    mock_overlay_pdf.pages = []
    mock_overlay_pdf.__enter__.return_value = mock_overlay_pdf

    with patch("pdftl.operations.overlay.smart_pikepdf_open", return_value=mock_overlay_pdf):
        # Updated regex to match the new error message
        fn = "empty_overlay.pdf"
        with pytest.raises(OperationError, match="has no pages"):
            apply_overlay(mock_input_pdf, fn, [fn])


def test_apply_overlay_missing_layer_name_value(two_page_pdf):
    with pytest.raises(OperationError, match="requires a value"):
        apply_overlay(pikepdf.open(two_page_pdf), "-", ["-", "layer_name"], on_top=True)


def test_apply_overlay_with_ocg_layer(two_page_pdf, tmp_path):
    # Create a real PDF file to use as the stamp
    stamp_path = tmp_path / "stamp.pdf"
    with pikepdf.new() as stamp:
        stamp.add_blank_page()
        stamp.save(stamp_path)

    result = apply_overlay(
        pikepdf.open(two_page_pdf),
        str(stamp_path),
        [str(stamp_path), "layer_name", "MyLayer"],
        on_top=True,
    )

    assert result.success
    # Verify OCG structure in the output PDF
    assert "/OCProperties" in result.pdf.Root

    # Check if the overlay XObject on page 1 was tagged with the OCG
    page = result.pdf.pages[0]
    xobjs = page.Resources.XObject
    found_oc = any("/OC" in x for x in xobjs.values())
    assert found_oc


def test_apply_overlay_underlay(two_page_pdf, tmp_path):
    """Covers overlay.py:158 — the add_underlay branch (on_top=False)."""
    bg_path = tmp_path / "bg.pdf"
    with pikepdf.new() as bg:
        bg.add_blank_page()
        bg.save(bg_path)

    result = apply_overlay(pikepdf.open(two_page_pdf), str(bg_path), [str(bg_path)], on_top=False)
    assert result.success


# --- OVERLAY/STAMP TESTS ---


@pytest.fixture
def stamp_pdf_path(tmp_path):
    """Creates a 1-page PDF to act as a stamp/overlay."""
    p = pikepdf.new()
    p.add_blank_page()
    output = tmp_path / "stamp.pdf"
    p.save(output)
    return str(output)


def test_overlay_stamp_basic(two_page_pdf, stamp_pdf_path):
    """Test applying a stamp (overlay)."""
    with pikepdf.open(two_page_pdf) as pdf:
        # apply_overlay(input_pdf, overlay_filename, ...)
        apply_overlay(pdf, stamp_pdf_path, [stamp_pdf_path], on_top=True)

        # We verify success by checking the file structure implicitly
        # (pikepdf handles the heavy lifting)
        assert len(pdf.pages) == 2


def test_overlay_background(two_page_pdf, stamp_pdf_path):
    """Test applying a background (underlay)."""
    with pikepdf.open(two_page_pdf) as pdf:
        apply_overlay(pdf, stamp_pdf_path, [stamp_pdf_path], on_top=False)
        assert len(pdf.pages) == 2


def test_overlay_missing_file_error(two_page_pdf):
    """Test error when overlay file doesn't exist."""
    with pikepdf.open(two_page_pdf) as pdf:
        with pytest.raises(OperationError):
            apply_overlay(pdf, "non_existent_file.pdf", ["non_existent_file.pdf"])


def test_apply_overlay_stdin():
    """
    Checks that source is set to None when overlay_filename is "-".
    """
    # 1. Setup Mocks
    input_pdf = MagicMock()
    page_mock = MagicMock()
    page_mock.trimbox = [0, 0, 612, 792]
    input_pdf.pages = [page_mock]

    with patch("pdftl.operations.overlay.smart_pikepdf_open") as mock_open:
        overlay_pdf = MagicMock()
        overlay_pdf.pages = [MagicMock()]
        mock_open.return_value = overlay_pdf

        # 2. Call with "-"
        apply_overlay(input_pdf, overlay_filename="-", operation_args=[])

        # 3. Assert
        mock_open.assert_called_with(None)


"""Tests for page-range filtering and argument parsing added to overlay.py."""


# ---------------------------------------------------------------------------
# _parse_operation_args — line 208 (page_specs.append) and surrounding logic
# ---------------------------------------------------------------------------


class TestParseOperationArgs:
    def test_empty_args(self):
        specs, layer = _parse_operation_args([])
        assert specs == []
        assert layer is None

    def test_only_page_specs(self):
        # Line 208: every token goes into page_specs when no layer_name present
        specs, layer = _parse_operation_args(["1-3", "odd"])
        assert specs == ["1-3", "odd"]
        assert layer is None

    def test_only_layer_name(self):
        specs, layer = _parse_operation_args(["layer_name", "Draft"])
        assert specs == []
        assert layer == "Draft"

    def test_page_specs_then_layer_name(self):
        # Line 208 fires for each spec token, then layer_name is consumed
        specs, layer = _parse_operation_args(["2-end", "even", "layer_name", "Watermark"])
        assert specs == ["2-end", "even"]
        assert layer == "Watermark"

    def test_single_page_spec_then_layer_name(self):
        specs, layer = _parse_operation_args(["odd", "layer_name", "Draft"])
        assert specs == ["odd"]
        assert layer == "Draft"

    def test_layer_name_missing_value_raises(self):
        with pytest.raises(OperationError, match="requires a value"):
            _parse_operation_args(["layer_name"])

    def test_layer_name_missing_value_after_specs_raises(self):
        with pytest.raises(OperationError, match="requires a value"):
            _parse_operation_args(["1-3", "layer_name"])


# ---------------------------------------------------------------------------
# apply_overlay page filtering — line 176 (the `continue` skip branch)
# ---------------------------------------------------------------------------


@pytest.fixture
def three_page_pdf(tmp_path):
    path = tmp_path / "three.pdf"
    with pikepdf.new() as pdf:
        for _ in range(3):
            pdf.add_blank_page()
        pdf.save(path)
    return str(path)


@pytest.fixture
def stamp_path(tmp_path):
    path = tmp_path / "stamp.pdf"
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        pdf.save(path)
    return str(path)


def _count_xobjects(pdf):
    """Return per-page XObject counts as a list."""
    counts = []
    for page in pdf.pages:
        if "/Resources" in page and "/XObject" in page.Resources:
            counts.append(len(page.Resources.XObject))
        else:
            counts.append(0)
    return counts


class TestApplyOverlayPageFiltering:
    def test_no_specs_stamps_all_pages(self, three_page_pdf, stamp_path):
        """Default (no specs): every page receives the stamp."""
        with pikepdf.open(three_page_pdf) as pdf:
            result = apply_overlay(pdf, stamp_path, [stamp_path])
        assert result.success
        counts = _count_xobjects(result.pdf)
        assert all(c > 0 for c in counts), f"Expected all pages stamped, got {counts}"

    def test_page_spec_skips_excluded_pages(self, three_page_pdf, stamp_path):
        """Line 176: pages outside the spec are skipped (no XObject added)."""
        with pikepdf.open(three_page_pdf) as pdf:
            result = apply_overlay(pdf, stamp_path, [stamp_path, "2"])  # only page 2
        counts = _count_xobjects(result.pdf)
        assert counts[0] == 0, "Page 1 should be untouched"
        assert counts[1] > 0, "Page 2 should be stamped"
        assert counts[2] == 0, "Page 3 should be untouched"

    def test_range_spec_stamps_subset(self, three_page_pdf, stamp_path):
        """Stamp pages 1-2 only; page 3 must be skipped (line 176)."""
        with pikepdf.open(three_page_pdf) as pdf:
            result = apply_overlay(pdf, stamp_path, [stamp_path, "1-2"])
        counts = _count_xobjects(result.pdf)
        assert counts[0] > 0, "Page 1 should be stamped"
        assert counts[1] > 0, "Page 2 should be stamped"
        assert counts[2] == 0, "Page 3 should be untouched"

    def test_odd_spec_stamps_odd_pages_only(self, three_page_pdf, stamp_path):
        """'odd' spec: pages 1 and 3 stamped, page 2 skipped (line 176)."""
        with pikepdf.open(three_page_pdf) as pdf:
            result = apply_overlay(pdf, stamp_path, [stamp_path, "odd"])
        counts = _count_xobjects(result.pdf)
        assert counts[0] > 0, "Page 1 (odd) should be stamped"
        assert counts[1] == 0, "Page 2 (even) should be untouched"
        assert counts[2] > 0, "Page 3 (odd) should be stamped"

    def test_even_spec_stamps_even_pages_only(self, three_page_pdf, stamp_path):
        """'even' spec: only page 2 stamped; pages 1 and 3 skipped (line 176)."""
        with pikepdf.open(three_page_pdf) as pdf:
            result = apply_overlay(pdf, stamp_path, [stamp_path, "even"])
        counts = _count_xobjects(result.pdf)
        assert counts[0] == 0, "Page 1 should be untouched"
        assert counts[1] > 0, "Page 2 (even) should be stamped"
        assert counts[2] == 0, "Page 3 should be untouched"

    def test_page_spec_with_layer_name(self, three_page_pdf, stamp_path):
        """Page spec and layer_name together: only specified pages get the OCG-tagged stamp."""
        with pikepdf.open(three_page_pdf) as pdf:
            result = apply_overlay(
                pdf, stamp_path, [stamp_path, "2", "layer_name", "MyLayer"], on_top=True
            )
        assert result.success
        assert "/OCProperties" in result.pdf.Root

        # Page 2 should have an OC-tagged XObject; pages 1 and 3 should not
        def has_oc_xobj(page):
            if "/Resources" not in page or "/XObject" not in page.Resources:
                return False
            return any("/OC" in x for x in page.Resources.XObject.values())

        assert not has_oc_xobj(result.pdf.pages[0]), "Page 1 should be untouched"
        assert has_oc_xobj(result.pdf.pages[1]), "Page 2 should have OC-tagged XObject"
        assert not has_oc_xobj(result.pdf.pages[2]), "Page 3 should be untouched"

    def test_last_page_only(self, three_page_pdf, stamp_path):
        """'end' spec targets only the last page; first two are skipped (line 176)."""
        with pikepdf.open(three_page_pdf) as pdf:
            result = apply_overlay(pdf, stamp_path, [stamp_path, "end"])
        counts = _count_xobjects(result.pdf)
        assert counts[0] == 0
        assert counts[1] == 0
        assert counts[2] > 0

    def test_process_page_not_called_for_skipped_pages(self, three_page_pdf, stamp_path):
        """Directly verify _process_page is never called for out-of-spec pages."""
        with patch("pdftl.operations.overlay._process_page") as mock_process:
            with pikepdf.open(three_page_pdf) as pdf:
                apply_overlay(pdf, stamp_path, [stamp_path, "2"])

            # args[0] is now `stamped_count`. Since it's the first stamp applied, it will be 0.
            called_indices = [c.args[0] for c in mock_process.call_args_list]
            assert called_indices == [0], (
                f"_process_page should pass stamped_count 0 for the first applied stamp, got {called_indices}"
            )


class TestApplyOverlayAdvancedSequencing:
    @pytest.fixture
    def mock_pdf_setup(self):
        """Fixture to set up a mock input PDF with 3 pages and a mock overlay."""
        input_pdf = MagicMock()
        # Create 3 distinct mock pages so we can verify the right ones are passed
        input_pdf.pages = [
            MagicMock(name="Page1"),
            MagicMock(name="Page2"),
            MagicMock(name="Page3"),
        ]

        overlay_pdf = MagicMock()
        overlay_pdf.pages = [MagicMock(name="Overlay1")]
        overlay_pdf.__enter__.return_value = overlay_pdf

        return input_pdf, overlay_pdf

    @patch("pdftl.operations.overlay.smart_pikepdf_open")
    @patch("pdftl.operations.overlay._process_page")
    @patch("pdftl.operations.overlay.page_numbers_matching_page_specs")
    def test_explicit_page_ordering(self, mock_matching, mock_process, mock_open, mock_pdf_setup):
        """Verify that apply_overlay respects the exact order of the target pages."""
        input_pdf, overlay_pdf = mock_pdf_setup
        mock_open.return_value = overlay_pdf

        # Simulate the user requesting page 3, then page 1
        mock_matching.return_value = [3, 1]

        apply_overlay(input_pdf, "stamp.pdf", ["stamp.pdf", "3,1"])

        # We expect 2 calls to _process_page.
        # Call 1: stamped_count=0, base_page=input_pdf.pages[2] (Page 3)
        # Call 2: stamped_count=1, base_page=input_pdf.pages[0] (Page 1)
        assert mock_process.call_count == 2

        # Verify first call
        args_call_1 = mock_process.call_args_list[0].args
        assert args_call_1[0] == 0  # stamped_count
        assert args_call_1[1] == input_pdf.pages[2]  # Actual page 3

        # Verify second call
        args_call_2 = mock_process.call_args_list[1].args
        assert args_call_2[0] == 1  # stamped_count
        assert args_call_2[1] == input_pdf.pages[0]  # Actual page 1

    @patch("pdftl.operations.overlay.smart_pikepdf_open")
    @patch("pdftl.operations.overlay._process_page")
    @patch("pdftl.operations.overlay.page_numbers_matching_page_specs")
    def test_duplicate_page_targeting(
        self, mock_matching, mock_process, mock_open, mock_pdf_setup
    ):
        """Verify that a user can stamp the same page multiple times."""
        input_pdf, overlay_pdf = mock_pdf_setup
        mock_open.return_value = overlay_pdf

        # Simulate the user requesting page 2 twice (e.g., applying two different background layers)
        mock_matching.return_value = [2, 2]

        apply_overlay(input_pdf, "stamp.pdf", ["stamp.pdf", "2,2"])

        assert mock_process.call_count == 2

        # Both calls should target input_pdf.pages[1] (Page 2),
        # but the stamped_count should increment.
        args_call_1 = mock_process.call_args_list[0].args
        assert args_call_1[0] == 0
        assert args_call_1[1] == input_pdf.pages[1]

        args_call_2 = mock_process.call_args_list[1].args
        assert args_call_2[0] == 1
        assert args_call_2[1] == input_pdf.pages[1]
