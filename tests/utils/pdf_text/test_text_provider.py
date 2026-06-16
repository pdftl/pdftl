# tests/utils/test_text_provider.py
"""
Tests for src/pdftl/utils/pdf_text/text_provider.py.

All PDFium I/O is mocked — no real PDF file is required.
"""

from __future__ import annotations

import ctypes
from unittest.mock import MagicMock, patch

# codeql[py/import-and-import-from]
import pdftl.utils.pdf_text.text_provider as text_provider_mod
from pdftl.utils.pdf_text.text_provider import (
    TextProvider,
    _detect_bold,
    _detect_bold_tex_font,
    _detect_italic,
    _detect_italic_tex_font,
    _fetch_char_size,
    _fetch_font_name_and_flags,
    _fetch_font_weight,
    _normalize_tex_font_name,
    _sample_pdf_indices,
)

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------


def _make_textpage(text: str, num_chars: int | None = None):
    """Return a mock textpage whose get_text_range returns *text*."""
    tp = MagicMock()
    tp.count_chars.return_value = num_chars if num_chars is not None else len(text)
    tp.get_text_range.return_value = text
    return tp


def _make_page(textpage):
    page = MagicMock()
    page.get_textpage.return_value = textpage
    return page


def _make_pdf(pages: list):
    pdf = MagicMock()
    pdf.__len__ = MagicMock(return_value=len(pages))
    pdf.__getitem__ = MagicMock(side_effect=lambda i: pages[i])
    return pdf


# ===========================================================================
# _sample_pdf_indices
# ===========================================================================


class TestSamplePdfIndices:
    def test_empty(self):
        assert _sample_pdf_indices([]) == []

    def test_fewer_than_max(self):
        assert _sample_pdf_indices([0, 1, 2]) == [0, 1, 2]

    def test_exactly_max(self):
        result = _sample_pdf_indices(list(range(5)))
        assert len(result) == 5

    def test_more_than_max_returns_at_most_5(self):
        result = _sample_pdf_indices(list(range(100)))
        assert len(result) <= 5

    def test_custom_max_samples(self):
        result = _sample_pdf_indices(list(range(20)), max_samples=3)
        assert len(result) <= 3


# ===========================================================================
# _fetch_char_size
# ===========================================================================


class TestFetchCharSize:
    def test_positive_size(self):
        mock_c = MagicMock()
        mock_c.FPDFText_GetFontSize.return_value = 12.0
        assert _fetch_char_size(MagicMock(), 0, mock_c) == 12.0

    def test_zero_size_returns_zero(self):
        mock_c = MagicMock()
        mock_c.FPDFText_GetFontSize.return_value = 0
        assert _fetch_char_size(MagicMock(), 0, mock_c) == 0.0

    def test_negative_size_returns_zero(self):
        mock_c = MagicMock()
        mock_c.FPDFText_GetFontSize.return_value = -1
        assert _fetch_char_size(MagicMock(), 0, mock_c) == 0.0


# ===========================================================================
# _fetch_font_name_and_flags
# ===========================================================================


class TestFetchFontNameAndFlags:
    def test_no_font_name_buf_len_zero(self):
        mock_c = MagicMock()
        mock_c.FPDFText_GetFontInfo.return_value = 1
        name, flags = _fetch_font_name_and_flags(MagicMock(), 0, mock_c)
        assert name == ""

    def test_returns_decoded_name(self):
        mock_c = MagicMock()
        flags_holder = ctypes.c_int(0)

        def fake_font_info(textpage, idx, buf, buf_len, flags_ptr):
            if buf is None:
                return 9  # length of b"Helvetica" + NUL
            buf.value = b"Helvetica"
            return 9

        mock_c.FPDFText_GetFontInfo.side_effect = fake_font_info

        with (
            patch.object(text_provider_mod.ctypes, "c_int", return_value=flags_holder),
            patch.object(text_provider_mod.ctypes, "byref", return_value=None),
            patch.object(
                text_provider_mod.ctypes,
                "create_string_buffer",
                side_effect=ctypes.create_string_buffer,
            ),
        ):
            name, flags = _fetch_font_name_and_flags(MagicMock(), 0, mock_c)
        assert name == "Helvetica"


# ===========================================================================
# _fetch_font_weight
# ===========================================================================


class TestFetchFontWeight:
    def test_returns_weight(self):
        mock_c = MagicMock()
        mock_c.FPDFText_GetFontWeight.return_value = 700
        assert _fetch_font_weight(MagicMock(), 0, mock_c) == 700


# ===========================================================================
# _detect_bold / _detect_italic
# ===========================================================================


class TestDetectBold:
    def test_weight_high(self):
        assert _detect_bold("Regular", 700) is True

    def test_weight_low(self):
        assert _detect_bold("Regular", 400) is False

    def test_weight_boundary_450(self):
        assert _detect_bold("Regular", 450) is True

    def test_weight_zero_falls_back_to_name(self):
        assert _detect_bold("Arial-Bold", 0) is True

    def test_weight_negative_falls_back_to_name(self):
        assert _detect_bold("Helvetica-Black", -1) is True

    def test_name_heavy(self):
        assert _detect_bold("MyHeavyFont", 0) is True

    def test_name_demi(self):
        assert _detect_bold("DemiSerif", 0) is True

    def test_plain_name_not_bold(self):
        assert _detect_bold("Arial", 0) is False

    def test_tex_bold_bx(self):
        assert _detect_bold("someBXfont", 0) is True

    def test_tex_bold_b(self):
        assert _detect_bold("Arial-Bold", 0) is True

    def test_subset_prefix_stripped(self):
        assert _detect_bold("ABCDEF+MyBoldFont", 0) is True


class TestDetectItalic:
    def test_flag_bit6_set(self):
        assert _detect_italic("Regular", 1 << 6) is True

    def test_flag_bit6_not_set(self):
        assert _detect_italic("Regular", 0) is False

    def test_name_italic(self):
        assert _detect_italic("Times-Italic", 0) is True

    def test_name_oblique(self):
        assert _detect_italic("Helvetica-Oblique", 0) is True

    def test_name_slanted(self):
        assert _detect_italic("MySlanted", 0) is True

    def test_tex_italic_i(self):
        assert _detect_italic("Times-Italic", 0) is True

    def test_tex_italic_sl(self):
        assert _detect_italic("MySlanted", 0) is True

    def test_plain_not_italic(self):
        assert _detect_italic("Arial", 0) is False


# ===========================================================================
# _normalize_tex_font_name
# ===========================================================================


class TestNormalizeTexFontName:
    def test_all_upper(self):
        assert _normalize_tex_font_name("CMBX10") == "CMBX"

    def test_subset_prefix(self):
        assert _normalize_tex_font_name("ABC+CMBX10") == "CMBX"

    def test_not_all_upper_returns_none(self):
        assert _normalize_tex_font_name("Arial") is None

    def test_mixed_case_returns_none(self):
        assert _normalize_tex_font_name("ArialBold") is None


# ===========================================================================
# _detect_bold_tex_font / _detect_italic_tex_font
# ===========================================================================


class TestDetectBoldTexFont:
    def test_bx_suffix(self):
        assert _detect_bold_tex_font("CMBX10") is True

    def test_b_suffix(self):
        assert _detect_bold_tex_font("CMB10") is True

    def test_no_match(self):
        assert _detect_bold_tex_font("CMR10") is False

    def test_none_from_normalize(self):
        assert _detect_bold_tex_font("Arial") is False


class TestDetectItalicTexFont:
    def test_i_suffix(self):
        assert _detect_italic_tex_font("CMTI10") is True

    def test_sl_suffix(self):
        assert _detect_italic_tex_font("CMSL10") is True

    def test_no_match(self):
        assert _detect_italic_tex_font("CMR10") is False

    def test_none_from_normalize(self):
        assert _detect_italic_tex_font("arial") is False


# ===========================================================================
# TextProvider — construction and __len__
# ===========================================================================


class TestTextProviderConstruction:
    @patch("pypdfium2.PdfDocument")
    def test_opens_pdf_from_path_when_no_doc_provided(self, mock_pdfium_doc):
        mock_doc = MagicMock()
        mock_pdfium_doc.return_value = mock_doc
        tp = TextProvider("some.pdf")
        mock_pdfium_doc.assert_called_once_with("some.pdf")
        assert tp.pdf is mock_doc

    def test_uses_provided_doc(self):
        doc = MagicMock()
        tp = TextProvider("", opened_pdfium_doc=doc)
        assert tp.pdf is doc

    def test_len(self):
        doc = MagicMock()
        doc.__len__ = MagicMock(return_value=7)
        tp = TextProvider("", opened_pdfium_doc=doc)
        assert len(tp) == 7


# ===========================================================================
# TextProvider — _load_page / get_page_data / get_text
# ===========================================================================


class TestLoadPage:
    def _make_tp(self, text: str, num_chars: int | None = None):
        textpage = _make_textpage(text, num_chars)
        page = _make_page(textpage)
        pdf = _make_pdf([page])
        tp = TextProvider("", opened_pdfium_doc=pdf)
        tp.pdfium_c = MagicMock()  # Inject mock for pdfium_c natively
        return tp, textpage

    def test_identity_mapping_when_lengths_equal(self):
        tp, _ = self._make_tp("hello")
        data = tp.get_page_data(0)
        assert data["text"] == "hello"
        assert data["py_to_pdf"] is None

    def test_crlf_normalised_to_lf(self):
        tp, _ = self._make_tp("line1\r\nline2", num_chars=12)
        tp.pdfium_c.FPDFText_GetCharIndexFromTextIndex.side_effect = lambda _tp, i: i
        data = tp.get_page_data(0)
        assert "\r" not in data["text"]
        assert data["text"] == "line1\nline2"

    def test_bare_cr_normalised_to_lf(self):
        tp, _ = self._make_tp("a\rb")
        data = tp.get_page_data(0)
        assert data["text"] == "a\nb"

    def test_result_cached_on_second_call(self):
        tp, _ = self._make_tp("abc")
        d1 = tp.get_page_data(0)
        d2 = tp.get_page_data(0)
        assert d1 is d2

    def test_get_text_returns_string(self):
        tp, _ = self._make_tp("hello world")
        assert tp.get_text(0) == "hello world"

    def test_index_map_built_when_lengths_differ(self):
        tp, textpage = self._make_tp("a\r\nb", num_chars=4)
        tp.pdfium_c.FPDFText_GetCharIndexFromTextIndex.side_effect = lambda _tp, i: i
        tp._page_cache.clear()
        data = tp.get_page_data(0)

        assert data["py_to_pdf"] is not None

    def test_index_map_falls_back_to_none_on_exception(self):
        tp, textpage = self._make_tp("a\r\nb", num_chars=4)
        tp.pdfium_c.FPDFText_GetCharIndexFromTextIndex.side_effect = OSError("boom")
        tp._page_cache.clear()
        data = tp.get_page_data(0)
        assert data["py_to_pdf"] is None


# ===========================================================================
# TextProvider — clear_page_cache
# ===========================================================================


class TestClearPageCache:
    def test_clears_existing_page(self):
        textpage = _make_textpage("hi")
        page = _make_page(textpage)
        pdf = _make_pdf([page])
        tp = TextProvider("", opened_pdfium_doc=pdf)
        tp.get_page_data(0)
        assert 0 in tp._page_cache
        tp.clear_page_cache(0)
        assert 0 not in tp._page_cache

    def test_clear_nonexistent_page_is_noop(self):
        pdf = MagicMock()
        tp = TextProvider("", opened_pdfium_doc=pdf)
        tp.clear_page_cache(99)  # should not raise


# ===========================================================================
# TextProvider — close
# ===========================================================================


class TestClose:
    def test_close_calls_pdf_close(self):
        doc = MagicMock()
        tp = TextProvider("", opened_pdfium_doc=doc)
        tp.close()
        doc.close.assert_called_once()

    def test_close_clears_cache(self):
        textpage = _make_textpage("x")
        page = _make_page(textpage)
        pdf = _make_pdf([page])
        tp = TextProvider("", opened_pdfium_doc=pdf)
        tp.get_page_data(0)
        tp.close()
        assert tp._page_cache == {}

    def test_close_without_pdf_attribute_is_safe(self):
        tp = TextProvider.__new__(TextProvider)
        tp._page_cache = {}
        tp.close()  # should not raise


# ===========================================================================
# TextProvider — get_bboxes_for_lines / get_bbox
# ===========================================================================


def _charbox_factory(boxes: dict):
    """Return a get_charbox side_effect using *boxes* dict keyed by pdf_idx."""

    def _get(idx):
        return boxes.get(idx, (0.0, 0.0, 0.0, 0.0))

    return _get


class TestGetBboxesForLines:
    def _tp_with_text(self, text: str):
        textpage = _make_textpage(text)
        page = _make_page(textpage)
        pdf = _make_pdf([page])
        tp = TextProvider("", opened_pdfium_doc=pdf)
        return tp, textpage

    def test_zero_rect_when_start_equals_end(self):
        tp, _ = self._tp_with_text("hello")
        result = tp.get_bboxes_for_lines(0, 2, 2)
        assert result == [[0.0, 0.0, 0.0, 0.0]]

    def test_zero_rect_on_exception_from_charbox(self):
        tp, textpage = self._tp_with_text("hello")
        textpage.get_charbox = MagicMock(side_effect=OSError("fail"))
        result = tp.get_bboxes_for_lines(0, 0, 5)
        assert result == [[0.0, 0.0, 0.0, 0.0]]

    def test_single_line_single_char(self):
        tp, textpage = self._tp_with_text("A")
        textpage.get_charbox = _charbox_factory({0: (10.0, 20.0, 20.0, 30.0)})
        result = tp.get_bboxes_for_lines(0, 0, 1)
        assert result == [[10.0, 20.0, 20.0, 30.0]]

    def test_single_line_multiple_chars(self):
        tp, textpage = self._tp_with_text("AB")
        textpage.get_charbox = _charbox_factory(
            {
                0: (0.0, 0.0, 5.0, 10.0),
                1: (5.0, 0.0, 10.0, 10.0),
            }
        )
        result = tp.get_bboxes_for_lines(0, 0, 2)
        assert len(result) == 1
        assert result[0] == [0.0, 0.0, 10.0, 10.0]

    def test_two_lines_separated_vertically(self):
        tp, textpage = self._tp_with_text("AB")
        textpage.get_charbox = _charbox_factory(
            {
                0: (0.0, 700.0, 5.0, 710.0),  # line 1, cy≈705
                1: (0.0, 100.0, 5.0, 110.0),  # line 2, cy≈105
            }
        )
        result = tp.get_bboxes_for_lines(0, 0, 2)
        assert len(result) == 2

    def test_clamps_start_below_zero(self):
        tp, textpage = self._tp_with_text("ABC")
        textpage.get_charbox = _charbox_factory({0: (0, 0, 1, 1)})
        result = tp.get_bboxes_for_lines(0, -5, 1)
        assert result == [[0.0, 0.0, 1.0, 1.0]]

    def test_clamps_end_beyond_length(self):
        tp, textpage = self._tp_with_text("A")
        textpage.get_charbox = _charbox_factory({0: (0, 0, 5, 5)})
        result = tp.get_bboxes_for_lines(0, 0, 999)
        assert result == [[0.0, 0.0, 5.0, 5.0]]

    def test_with_py_to_pdf_map_deduplicates(self):
        textpage = _make_textpage("AB", num_chars=3)
        page = _make_page(textpage)
        pdf = _make_pdf([page])
        tp = TextProvider("", opened_pdfium_doc=pdf)

        tp._page_cache[0] = {
            "textpage": textpage,
            "text": "AB",
            "py_to_pdf": [0, 0],
        }
        textpage.get_charbox = _charbox_factory({0: (1, 1, 2, 2)})
        result = tp.get_bboxes_for_lines(0, 0, 2)
        assert result == [[1.0, 1.0, 2.0, 2.0]]


class TestGetBbox:
    def test_unified_bbox_across_two_lines(self):
        tp = TextProvider.__new__(TextProvider)
        tp._page_cache = {
            0: {
                "textpage": MagicMock(),
                "text": "AB",
                "py_to_pdf": None,
            }
        }
        boxes = {
            0: (0.0, 10.0, 5.0, 20.0),
            1: (3.0, 50.0, 8.0, 60.0),
        }
        tp._page_cache[0]["textpage"].get_charbox = _charbox_factory(boxes)
        result = tp.get_bbox(0, 0, 2)
        assert result == [0.0, 10.0, 8.0, 60.0]


# ===========================================================================
# TextProvider — get_font_info
# ===========================================================================


class TestGetFontInfo:
    def _inject_page(self, tp, text, py_to_pdf=None):
        textpage = _make_textpage(text)
        tp._page_cache[0] = {
            "textpage": textpage,
            "text": text,
            "py_to_pdf": py_to_pdf,
        }
        return textpage

    def test_empty_range_returns_defaults(self):
        tp = TextProvider.__new__(TextProvider)
        tp._page_cache = {}
        textpage = _make_textpage("")
        tp._page_cache[0] = {"textpage": textpage, "text": "", "py_to_pdf": None}
        result = tp.get_font_info(0, 0, 0)
        assert result == {"font_name": "", "font_size": 0.0, "bold": False, "italic": False}

    def test_normal_font_info(self):
        mock_c = MagicMock()
        mock_c.FPDFText_GetFontSize.return_value = 12.0
        mock_c.FPDFText_GetFontWeight.return_value = 400

        def fake_font_info(tp, idx, buf, buf_len, flags_ptr):
            if buf is None:
                return 8
            buf.value = b"Arial"
            return 8

        mock_c.FPDFText_GetFontInfo.side_effect = fake_font_info

        tp = TextProvider.__new__(TextProvider)
        tp.pdfium_c = mock_c
        tp._page_cache = {}
        textpage = _make_textpage("hello")
        tp._page_cache[0] = {"textpage": textpage, "text": "hello", "py_to_pdf": None}

        flags_obj = ctypes.c_int(0)
        with (
            patch.object(text_provider_mod.ctypes, "c_int", return_value=flags_obj),
            patch.object(text_provider_mod.ctypes, "byref", return_value=None),
            patch.object(
                text_provider_mod.ctypes,
                "create_string_buffer",
                side_effect=ctypes.create_string_buffer,
            ),
        ):
            result = tp.get_font_info(0, 0, 5)

        assert result["font_size"] == 12.0
        assert result["bold"] is False

    def test_no_sizes_collected_gives_zero_avg(self):
        mock_c = MagicMock()
        mock_c.FPDFText_GetFontSize.return_value = 0
        mock_c.FPDFText_GetFontWeight.return_value = -1

        def fake_font_info(tp, idx, buf, buf_len, flags_ptr):
            return 1  # buf_len <= 1 → name = ""

        mock_c.FPDFText_GetFontInfo.side_effect = fake_font_info

        tp = TextProvider.__new__(TextProvider)
        tp.pdfium_c = mock_c
        tp._page_cache = {}
        textpage = _make_textpage("hello")
        tp._page_cache[0] = {"textpage": textpage, "text": "hello", "py_to_pdf": None}

        flags_obj = ctypes.c_int(0)
        with (
            patch.object(text_provider_mod.ctypes, "c_int", return_value=flags_obj),
            patch.object(text_provider_mod.ctypes, "byref", return_value=None),
        ):
            result = tp.get_font_info(0, 0, 5)

        assert result["font_size"] == 0.0


# ===========================================================================
# _group_into_lines edge cases
# ===========================================================================


class TestGroupIntoLines:
    def _tp(self):
        tp = TextProvider.__new__(TextProvider)
        tp._page_cache = {}
        return tp

    def test_empty_indices_returns_zero_rect(self):
        tp = self._tp()
        result = tp._group_into_lines(lambda i: (0, 0, 0, 0), [])
        assert result == [[0.0, 0.0, 0.0, 0.0]]

    def test_zero_height_char_uses_one_as_divisor(self):
        tp = self._tp()
        charboxes = {0: (5.0, 10.0, 10.0, 10.0)}  # zero height
        result = tp._group_into_lines(lambda i: charboxes[i], [0])
        assert result == [[5.0, 10.0, 10.0, 10.0]]
