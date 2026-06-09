import io
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)


def extract_to_pil(xobj) -> "Image.Image | None":
    """Decodes and extracts standard pixel channels out of an XObject stream."""
    import pikepdf
    from PIL import Image

    width = int(xobj["/Width"])
    height = int(xobj["/Height"])

    # Block A: Attempt high-level extraction
    try:
        pdf_img = pikepdf.PdfImage(xobj)
        return pdf_img.as_pil_image()
    except (
        pikepdf.PdfError,
        pikepdf.DataDecodingError,
        NotImplementedError,
        ValueError,
        TypeError,
        AttributeError,
    ) as e:
        logger.debug("High-level native extraction failed (%s). Trying low-level recovery.", e)
    except Exception as e:
        exc_name = type(e).__name__

        # Safe isolation layer exclusively for unmapped binary-compiled C++ runtime failures
        if "HifiPrintImage" in exc_name or "RuntimeError" in exc_name:
            logger.debug(
                (
                    "Binding or HiFi print profile error encountered (%s: %s). "
                    "Cascading to low-level."
                ),
                exc_name,
                e,
            )
        else:
            logger.error(
                "Unexpected system exception trapped in image extractor (%s: %s). Re-raising.",
                exc_name,
                e,
            )
            raise

    # Block B: Direct stream byte extraction fallback
    try:
        raw_bytes = xobj.read_bytes()
    except (pikepdf.PdfError, pikepdf.DataDecodingError) as e:
        logger.warning("Stream data is unfilterable (%s); recovery aborted.", e)
        return None

    # Block C: Canvas assembly fallback
    try:
        return Image.frombytes("RGB", (width, height), raw_bytes)
    except ValueError:
        try:
            return Image.open(io.BytesIO(raw_bytes))
        except (OSError, ValueError, TypeError) as e:
            logger.debug("Low-level canvas assembly failed (%s).", e)
            return None
