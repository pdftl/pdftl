# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/pdf_text/text_provider.py


"""
text_provider.py
================
Low-level PDFium text extraction, caching, and coordinate mapping.

Provides a unified interface for extracting text, computing bounding boxes,
and fetching font metadata.

Handles the PDFium UTF-16 index drift: PDFium's get_text_range() can return
2-Python-char strings for characters outside the BMP (e.g. surrogate sequences).
To prevent Python string indices from diverging from PDFium character indices,
we call the raw PDFium API FPDFText_GetCharIndexFromTextIndex() to build a
``py_to_pdf`` array that mathematically maps every Python string position back
to its true PDFium char index.
"""

from __future__ import annotations

import ctypes
import logging


logger = logging.getLogger(__name__)

_ZERO_RECT: list[float] = [0.0, 0.0, 0.0, 0.0]


class TextProvider:
    """
    Handles pdfium document lifetime, text extraction, and bbox calculation.

    All page data is cached on first access so that multiple consumers
    never re-extract or re-map the same page.
    """

    def __init__(self, pdf_path: str, opened_pdfium_doc=None):
        import pypdfium2 as pdfium
        import pypdfium2.raw as pdfium_c

        self.pdfium_c = pdfium_c
        self.pdf_path = pdf_path
        self.pdf = opened_pdfium_doc or pdfium.PdfDocument(pdf_path)
        self._page_cache: dict[int, dict] = {}

    def __len__(self) -> int:
        return len(self.pdf)

    # ------------------------------------------------------------------ #
    # Page data                                                          #
    # ------------------------------------------------------------------ #

    def get_page_data(self, page_num: int) -> dict:
        """
        Returns a dict with keys: textpage, text, py_to_pdf.

        ``py_to_pdf`` is a list of length len(text) where
        ``py_to_pdf[py_idx]`` is the PDFium char index to pass to
        get_charbox(). Result is cached.
        """
        if page_num not in self._page_cache:
            self._page_cache[page_num] = self._load_page(page_num)
        return self._page_cache[page_num]

    def _load_page(self, page_num: int) -> dict:
        """Extract text and build the py→pdf index map for one page."""
        page = self.pdf[page_num]
        textpage = page.get_textpage()
        num_chars = textpage.count_chars()
        full_text = textpage.get_text_range(0, num_chars).replace("\r\n", "\n").replace("\r", "\n")
        py_len = len(full_text)

        if py_len == num_chars:
            py_to_pdf = None  # fast path: identity mapping
        else:
            py_to_pdf = self._build_index_map_pdfium(textpage, num_chars, py_len, page_num)

        return {
            "textpage": textpage,
            "text": full_text,
            "py_to_pdf": py_to_pdf,
        }

    def _build_index_map_pdfium(
        self, textpage, num_chars: int, py_len: int, page_num: int
    ) -> list[int] | None:
        """
        Build py→pdf map using PDFium's own text-index→char-index API.
        """
        try:
            _get_char_idx = self.pdfium_c.FPDFText_GetCharIndexFromTextIndex
            py_to_pdf_list = []
            utf16_offset = 0

            for ch in textpage.get_text_range(0, num_chars):
                char_idx = _get_char_idx(textpage, utf16_offset)
                py_to_pdf_list.append(max(char_idx, 0))  # -1 means no mapping, clamp to 0

                # Advance by UTF-16 code units: non-BMP chars are 2 units
                utf16_offset += 2 if ord(ch) > 0xFFFF else 1

            return py_to_pdf_list
        except (OSError, ctypes.ArgumentError, AttributeError, OverflowError) as e:
            logger.debug("Page %d: index map construction failed: %s", page_num, e)
            return None

    def get_text(self, page_num: int) -> str:
        return self.get_page_data(page_num)["text"]

    def clear_page_cache(self, page_num: int) -> None:
        """Free memory for a processed page (useful for massive PDFs)."""
        self._page_cache.pop(page_num, None)

    # ------------------------------------------------------------------ #
    # BBox calculation                                                   #
    # ------------------------------------------------------------------ #

    def get_bbox(self, page_num: int, start: int, end: int) -> list[float]:
        """Returns one unified bbox for the range [start, end) in Python indices."""
        rects = self.get_bboxes_for_lines(page_num, start, end)
        return [
            min(r[0] for r in rects),
            min(r[1] for r in rects),
            max(r[2] for r in rects),
            max(r[3] for r in rects),
        ]

    def get_bboxes_for_lines(
        self, page_num: int, start_idx: int, end_idx: int
    ) -> list[list[float]]:
        """
        Return per-line bounding boxes for the Python string slice [start_idx, end_idx).
        """
        data = self.get_page_data(page_num)
        text_len = len(data["text"])
        start_idx = max(0, min(start_idx, text_len))
        end_idx = max(start_idx, min(end_idx, text_len))

        if start_idx == end_idx:
            return [_ZERO_RECT]

        pdf_indices = self._resolve_pdf_indices(data, start_idx, end_idx)

        try:
            return self._group_into_lines(data["textpage"].get_charbox, pdf_indices)
        except (OSError, ctypes.ArgumentError, AttributeError, OverflowError):
            return [_ZERO_RECT]

    def _resolve_pdf_indices(self, data: dict, start_idx: int, end_idx: int) -> range | list[int]:
        """Translate Python string indices to PDFium char indices."""
        py_to_pdf = data["py_to_pdf"]
        if py_to_pdf is None:
            return range(start_idx, end_idx)

        seen: set[int] = set()
        result: list[int] = []
        for py_idx in range(start_idx, end_idx):
            pdf_idx = py_to_pdf[py_idx]
            if pdf_idx not in seen:
                seen.add(pdf_idx)
                result.append(pdf_idx)
        return result

    def _group_into_lines(self, get_charbox, pdf_indices: range | list[int]) -> list[list[float]]:
        """
        Group charboxes by visual line using vertical centre proximity.
        Returns one [x0, y0, x1, y1] rect per line.
        """
        result: list[list[float]] = []
        cx0 = cy0 = cx1 = cy1 = 0.0
        prev_cy = -1e9
        first = True

        for i in pdf_indices:
            b = get_charbox(i)
            bx0, by0, bx1, by1 = b[0], b[1], b[2], b[3]
            cy = (by0 + by1) * 0.5
            h = (by1 - by0) or 1.0

            if first or abs(cy - prev_cy) >= h * 0.6:
                if not first:
                    result.append([cx0, cy0, cx1, cy1])
                cx0, cy0, cx1, cy1 = bx0, by0, bx1, by1
                first = False
            else:
                cx0 = min(cx0, bx0)
                cy0 = min(cy0, by0)
                cx1 = max(cx1, bx1)
                cy1 = max(cy1, by1)

            prev_cy = cy

        if first:
            return [_ZERO_RECT]

        result.append([cx0, cy0, cx1, cy1])
        return result

    # ------------------------------------------------------------------ #
    # Cleanup                                                            #
    # ------------------------------------------------------------------ #

    def close(self):
        if hasattr(self, "pdf") and self.pdf:
            self.pdf.close()
        self._page_cache.clear()

    # ------------------------------------------------------------------ #
    # Font Info Extraction                                               #
    # ------------------------------------------------------------------ #

    def get_font_info(self, page_num: int, start: int, end: int) -> dict:
        """
        Returns dominant font name, size, bold and italic for [start, end).
        Samples up to 5 characters evenly; cheap — no per-char loop.
        """
        data = self.get_page_data(page_num)
        pdf_indices = list(self._resolve_pdf_indices(data, start, end))
        samples = _sample_pdf_indices(pdf_indices)
        if not samples:
            return {"font_name": "", "font_size": 0.0, "bold": False, "italic": False}

        textpage = data["textpage"]
        sizes = []
        font_name = ""
        bold = italic = False
        weight = -1

        for pdf_idx in samples:
            size = _fetch_char_size(textpage, pdf_idx, self.pdfium_c)
            if size > 0:
                sizes.append(size)
            if not font_name:
                font_name, flags = _fetch_font_name_and_flags(textpage, pdf_idx, self.pdfium_c)
                weight = _fetch_font_weight(textpage, pdf_idx, self.pdfium_c)
                bold = _detect_bold(font_name, weight)
                italic = _detect_italic(font_name, flags)

        avg_size = round(sum(sizes) / len(sizes), 2) if sizes else 0.0
        return {
            "font_name": font_name,
            "font_size": avg_size,
            "bold": bold,
            "italic": italic,
            "weight": weight,
        }


# --- Low-level C-API Helpers --- #


def _sample_pdf_indices(indices: list, max_samples: int = 5) -> list:
    """Return up to max_samples evenly-spaced indices."""
    if not indices:
        return []
    step = max(1, len(indices) // max_samples)
    return indices[::step][:max_samples]


def _fetch_char_size(textpage, pdf_idx: int, pdfium_c) -> float:
    size = pdfium_c.FPDFText_GetFontSize(textpage, pdf_idx)
    return float(size) if size > 0 else 0.0


def _fetch_font_name_and_flags(textpage, pdf_idx: int, pdfium_c) -> tuple[str, int]:
    flags = ctypes.c_int()
    buf_len = pdfium_c.FPDFText_GetFontInfo(textpage, pdf_idx, None, 0, ctypes.byref(flags))
    if buf_len <= 1:
        return "", flags.value
    buf = ctypes.create_string_buffer(buf_len)
    pdfium_c.FPDFText_GetFontInfo(textpage, pdf_idx, buf, buf_len, ctypes.byref(flags))
    return buf.value.decode("utf-8", errors="replace"), flags.value


def _fetch_font_weight(textpage, pdf_idx: int, pdfium_c) -> int:
    """Returns font weight (400=normal, 700=bold), or -1 on failure."""
    return pdfium_c.FPDFText_GetFontWeight(textpage, pdf_idx)


_BOLD_MARKERS = ("bold", "black", "heavy", "demi", "bx")


def _detect_bold(font_name: str, weight: int) -> bool:
    if weight > 0:
        return weight >= 450
    name = font_name.lower().split("+")[-1]
    return any(m in name for m in _BOLD_MARKERS) or _detect_bold_tex_font(name)


_ITALIC_MARKERS = ("italic", "oblique", "slanted")


def _detect_italic(font_name: str, flags: int) -> bool:
    # PDFium sets this flag reliably from ItalicAngle < 0
    if flags & (1 << 6):
        return True
    name = font_name.lower().split("+")[-1]
    return any(m in name for m in _ITALIC_MARKERS) or _detect_italic_tex_font(name)


def _normalize_tex_font_name(font_name: str) -> str | None:
    stripped = font_name.split("+")[-1].rstrip("0123456789")
    if not stripped.isupper():
        return None
    return stripped


def _detect_bold_tex_font(font_name: str) -> bool:
    f = _normalize_tex_font_name(font_name)
    return f is not None and f.endswith(("BX", "B"))


def _detect_italic_tex_font(font_name: str) -> bool:
    f = _normalize_tex_font_name(font_name)
    return f is not None and f.endswith(("I", "SL"))
