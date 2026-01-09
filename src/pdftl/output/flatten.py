import io
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)


def flatten_pdf(pikepdf_doc: "pikepdf.Pdf") -> "pikepdf.Pdf":
    """
    Flattens a pikepdf object.

    Strategy:
    1. Try to use 'pypdfium2' (Google PDFium) to render and burn text.
    2. If 'pypdfium2' crashes (often on malformed PDFs) or is missing,
       fallback to 'pikepdf' structural flatten.
    """

    # Lazy Import: pikepdf
    import pikepdf

    # 1. Check for pypdfium2 availability
    has_renderer = False
    try:
        import pypdfium2 as pdfium

        has_renderer = True
    except ImportError:
        pass

    # 2. Strategy A: High-Fidelity Rendering (If installed)
    if has_renderer:
        try:
            # Save pikepdf state to buffer
            in_buffer = io.BytesIO()
            pikepdf_doc.save(in_buffer)
            in_buffer.seek(0)

            # Load into Renderer
            pdfium_doc = pdfium.PdfDocument(in_buffer)

            # Initialize form environment
            # If the PDF is malformed, this might not set the internal state correctly.
            pdfium_doc.init_forms()

            # Render & Flatten
            for page in pdfium_doc:
                page.flatten(flag=0)

            # Save back to buffer
            out_buffer = io.BytesIO()
            pdfium_doc.save(out_buffer)

            # Clean up C++ resources
            pdfium_doc.close()

            # Re-open as pikepdf
            out_buffer.seek(0)
            return pikepdf.Pdf.open(out_buffer)

        except RuntimeError as e:
            # This catches the RuntimeError from pypdfium2 when init_forms()
            # "bails out" on weird PDFs.
            logger.warning(
                f"pypdfium2 flattening failed (falling back to structural flattening): {e}"
            )

    # 3. Strategy B: Fallback (Structural Flattening)
    if not has_renderer:
        logger.debug(
            "pypdfium2 not found; falling back to structural flattening. "
            "To fix: install pdftl[flatten] or pdftl[full]"
        )

    # Attempt to generate appearances for simple shapes (Checkbox/Radio)
    if "/AcroForm" in pikepdf_doc.Root:
        pikepdf_doc.Root.AcroForm.NeedAppearances = True
        try:
            pikepdf_doc.generate_appearance_streams()
        except pikepdf.PdfError as e:
            logger.warning(f"Could not generate appearance streams: {e}")

    # Flatten annotations
    pikepdf_doc.flatten_annotations(mode="all")

    return pikepdf_doc
