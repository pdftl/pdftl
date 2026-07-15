# tests/operations/helpers/test_del_pages.py

from unittest.mock import MagicMock, patch

import pikepdf
import pytest

from pdftl.operations.helpers.del_pages import del_pages, _del_page


def make_pdf(n_pages):
    """Build a fresh in-memory pdf with n_pages blank pages, each carrying a
    marker key so we can tell pages apart after deletion/reordering."""
    pdf = pikepdf.new()
    for i in range(n_pages):
        pdf.add_blank_page()
        pdf.pages[i].Marker = pikepdf.String(f"page-{i}")
    return pdf


def markers(pdf):
    return [str(p.Marker) for p in pdf.pages]


# ---------------------------------------------------------------------------
# del_pages - integration behaviour on real pikepdf documents
# ---------------------------------------------------------------------------


class TestDelPagesIntegration:
    def test_deletes_single_page(self):
        pdf = make_pdf(3)
        del_pages(pdf, [2])  # 1-based: delete "page-1"
        assert markers(pdf) == ["page-0", "page-2"]

    def test_deletes_multiple_pages_regardless_of_input_order(self):
        pdf = make_pdf(5)
        # unsorted, deliberately not reverse-sorted by the caller
        del_pages(pdf, [2, 5])
        assert markers(pdf) == ["page-0", "page-2", "page-3"]

    def test_deletes_all_pages(self):
        pdf = make_pdf(2)
        del_pages(pdf, [1, 2])
        assert len(pdf.pages) == 0

    def test_delete_no_pages_is_noop(self):
        pdf = make_pdf(3)
        del_pages(pdf, [])
        assert markers(pdf) == ["page-0", "page-1", "page-2"]

    def test_stripped_page_keys_are_removed_before_deletion(self):
        # Regression guard for pikepdf#196: verify the page dict is actually
        # emptied (not just removed from pdf.pages) by capturing the object
        # reference before deletion.
        pdf = make_pdf(2)
        page_obj = pdf.pages[0].obj
        assert "/Marker" in page_obj
        del_pages(pdf, [1])
        assert len(list(page_obj.keys())) == 0


# ---------------------------------------------------------------------------
# del_pages - page label preservation toggle
# ---------------------------------------------------------------------------


class TestDelPagesLabelPreservation:
    @patch("pdftl.operations.helpers.del_pages.remap_page_labels")
    def test_preserve_labels_true_calls_remap(self, mock_remap):
        pdf = make_pdf(4)
        del_pages(pdf, [2, 4], preserve_page_labels=True)
        mock_remap.assert_called_once_with(pdf, pdf, [0, 2])

    @patch("pdftl.operations.helpers.del_pages.remap_page_labels")
    def test_preserve_labels_false_skips_remap(self, mock_remap):
        pdf = make_pdf(4)
        del_pages(pdf, [2, 4], preserve_page_labels=False)
        mock_remap.assert_not_called()

    @patch("pdftl.operations.helpers.del_pages.remap_page_labels")
    def test_preserve_labels_defaults_to_true(self, mock_remap):
        pdf = make_pdf(2)
        del_pages(pdf, [1])
        mock_remap.assert_called_once()

    @patch("pdftl.operations.helpers.del_pages.remap_page_labels")
    def test_remap_called_before_any_page_is_deleted(self, mock_remap):
        """remap_page_labels must run against the pre-deletion page count -
        assert the page count was still full at the moment it was called."""
        pdf = make_pdf(3)

        def check_pages_still_intact(*args, **kwargs):
            assert len(pdf.pages) == 3

        mock_remap.side_effect = check_pages_still_intact
        del_pages(pdf, [2])
        assert len(pdf.pages) == 2  # deletion did happen, just after remap

    def test_new_pages_list_excludes_deleted_1_based_numbers(self):
        pdf = make_pdf(5)
        captured = {}

        def capture(src, dst, new_to_old):
            captured["new_to_old"] = new_to_old

        with patch(
            "pdftl.operations.helpers.del_pages.remap_page_labels",
            side_effect=capture,
        ):
            del_pages(pdf, [2, 4])  # delete 1-based pages 2 and 4

        # surviving 0-based old indices: 0, 2, 4
        assert captured["new_to_old"] == [0, 2, 4]


# ---------------------------------------------------------------------------
# _del_page
# ---------------------------------------------------------------------------


class TestDelPage:
    def test_deletes_correct_1_based_page(self):
        pdf = make_pdf(3)
        _del_page(pdf, 2)
        assert markers(pdf) == ["page-0", "page-2"]

    def test_strips_keys_when_page_has_keys_method(self):
        pdf = make_pdf(1)
        page_obj = pdf.pages[0].obj
        _del_page(pdf, 1)
        assert len(list(page_obj.keys())) == 0

    def test_raises_if_page_lacks_keys(self):
        """Real pikepdf.Page objects always have .keys(); the hasattr guard
        that used to tolerate objects without it was removed as dead code
        (unreachable for any real page). This documents that _del_page now
        assumes .keys() is present rather than silently tolerating its
        absence."""

        class NoKeysPage:
            pass

        mock_pdf = MagicMock()
        fake_page = NoKeysPage()
        mock_pdf.pages = MagicMock()
        mock_pdf.pages.__getitem__.return_value = fake_page

        with pytest.raises(AttributeError, match="keys"):
            _del_page(mock_pdf, 1)

    def test_uses_1_based_indexing(self):
        mock_pdf = MagicMock()
        mock_page = MagicMock()
        mock_page.keys.return_value = ["/Contents"]
        mock_pdf.pages.__getitem__.return_value = mock_page

        _del_page(mock_pdf, 5)

        mock_pdf.pages.__getitem__.assert_called_with(4)
        mock_pdf.pages.__delitem__.assert_called_once_with(4)
        mock_page.__delitem__.assert_called_once_with("/Contents")
