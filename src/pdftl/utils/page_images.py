# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/page_images.py

"""Shared utility for rendering PDF pages to PIL images via pypdfium2."""

import io
import logging

logger = logging.getLogger(__name__)


def render_page_to_pil(pdf, page_index: int, dpi: float):
    """
    Render a single page (0-based index) of a pikepdf PDF to a PIL Image.

    Creates a temporary in-memory copy of the PDF for pypdfium2 so the
    original pikepdf object is not consumed or closed.

    Prefer iter_pages_as_pil when rendering multiple pages — it serialises
    the PDF only once regardless of how many pages are rendered.

    Args:
        pdf:        An open pikepdf.Pdf object.
        page_index: 0-based page index.
        dpi:        Render resolution in dots per inch.

    Returns:
        A PIL Image object.
    """

    pdfium = _get_pdfium()
    pdf_buffer = _get_open_pdf_buffer(pdf)
    ui_pdf = None
    try:
        ui_pdf = pdfium.PdfDocument(pdf_buffer)
        scale = dpi / 72.0
        page = ui_pdf[page_index]
        bitmap = page.render(scale=scale)
        return bitmap.to_pil()
    finally:
        if ui_pdf:
            ui_pdf.close()
        pdf_buffer.close()


def iter_pages_as_pil(pdf, dpi: float, page_indices: set[int] | None = None):
    """
    Yield (page_index, PIL Image) for pages in the PDF.

    Serialises the PDF exactly once regardless of how many pages are rendered.
    Pages whose 0-based index is not in `page_indices` are skipped entirely
    (not rendered). If `page_indices` is None, all pages are rendered.

    Args:
        pdf:          An open pikepdf.Pdf object.
        dpi:          Render resolution in dots per inch.
        page_indices: Optional set of 0-based page indices to render.
                      Pages outside this set are iterated past but not rendered.

    Yields:
        (int, PIL.Image.Image) tuples — 0-based index and rendered image.
    """
    pdfium = _get_pdfium()
    pdf_buffer = _get_open_pdf_buffer(pdf)
    ui_pdf = None
    try:
        ui_pdf = pdfium.PdfDocument(pdf_buffer)
        scale = dpi / 72.0
        for i, page in enumerate(ui_pdf):
            if page_indices is not None and i not in page_indices:
                continue
            bitmap = page.render(scale=scale)
            yield i, bitmap.to_pil()
    finally:
        if ui_pdf:
            ui_pdf.close()
        pdf_buffer.close()


def _get_pdfium():
    from pdftl.utils.dependencies import ensure_dependencies

    ensure_dependencies("page_images", ["pypdfium2", "PIL"], "render")
    import pypdfium2 as pdfium

    return pdfium


def _get_open_pdf_buffer(pdf):
    pdf_buffer = io.BytesIO()
    pdf.save(pdf_buffer)
    pdf_buffer.seek(0)
    return pdf_buffer
