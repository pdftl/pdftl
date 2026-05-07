import io
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pikepdf

from pdftl.core.registry import register_option

logger = logging.getLogger(__name__)


@register_option("flatten", desc="Flatten annotations and form fields", type="flag")
def flatten_pdf(pikepdf_doc: "pikepdf.Pdf") -> "pikepdf.Pdf":
    """
    Flattens a pikepdf object.

    Strategy:
    1. Try to use 'pypdfium2' (Google PDFium) to render and burn text.
    2. If 'pypdfium2' crashes (often on malformed PDFs) or is missing,
       fallback to 'pikepdf' structural flatten.
    """

    # 1. Check for pypdfium2 availability
    has_renderer = False
    try:
        import pypdfium2 as pdfium
        import pypdfium2.raw as pdfium_c

        has_renderer = True
    except ImportError:
        pass  # with has_renderer = False

    # 2. Strategy A: High-Fidelity Rendering (If installed)
    if has_renderer:
        try:
            return _flatten_pdf_strategy_a(pikepdf_doc, pdfium, pdfium_c)

        except RuntimeError as e:
            # This catches the RuntimeError from pypdfium2 when init_forms()
            # "bails out" on weird PDFs.
            logger.warning(
                "pypdfium2 flattening failed (falling back to structural flattening): %s", e
            )

    # 3. Strategy B: Fallback (Structural Flattening)
    if not has_renderer:
        logger.debug(
            "pypdfium2 not found; falling back to structural flattening. "
            "To fix: install pdftl[flatten] or pdftl[full]"
        )

    import pikepdf

    # Attempt to generate appearances for simple shapes (Checkbox/Radio)
    if "/AcroForm" in pikepdf_doc.Root:
        pikepdf_doc.Root.AcroForm.NeedAppearances = True
        try:
            pikepdf_doc.generate_appearance_streams()
        except pikepdf.PdfError as e:
            logger.warning("Could not generate appearance streams: %s", e)

    # Flatten annotations
    pikepdf_doc.flatten_annotations(mode="all")

    return pikepdf_doc


def _flatten_pdf_strategy_a(pikepdf_doc, pdfium, pdfium_c):
    import pikepdf

    # Save pikepdf state to buffer
    in_buffer = io.BytesIO()
    pikepdf_doc.save(in_buffer)
    in_buffer.seek(0)

    # Load into Renderer
    pdfium_doc = pdfium.PdfDocument(in_buffer)
    del in_buffer
    pdfium_doc.init_forms()

    # Initialize form environment
    # If the PDF is malformed, this might not set the internal state correctly.

    # Render & Flatten
    page_num = 0
    for page in pdfium_doc:
        page_num = page_num + 1
        try:
            page.flatten()
        except RuntimeError as re:
            # Bypass pypdfium2 wrapper bug: page.flatten() raises this if the PDF
            # has no AcroForms, even if init_forms() was successfully called.
            if "before page retrieval" in str(re):
                pdfium_c.FPDFPage_Flatten(page, pdfium_c.FLAT_NORMALDISPLAY)
            else:
                raise re

    # Save back to buffer
    out_buffer = io.BytesIO()
    pdfium_doc.save(out_buffer)

    # Clean up C++ resources
    pdfium_doc.close()

    # Re-open as pikepdf
    out_buffer.seek(0)
    return pikepdf.Pdf.open(out_buffer)
