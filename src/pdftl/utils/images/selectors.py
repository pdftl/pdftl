import io
import logging
from typing import TYPE_CHECKING

from pdftl.utils.pikepdf_compatibility_utils import as_pil_image_compat, image_extraction_errors

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)


def extract_to_pil(xobj) -> "Image.Image | None":
    """Decodes an image XObject to visual pixels (/Decode applied), or None."""
    import pikepdf

    width = int(xobj["/Width"])
    height = int(xobj["/Height"])

    # Block A: Attempt high-level extraction
    try:
        pdf_img = pikepdf.PdfImage(xobj)
        return as_pil_image_compat(pdf_img)
    except (
        pikepdf.PdfError,
        pikepdf.DataDecodingError,
        NotImplementedError,
        ValueError,
        TypeError,
        AttributeError,
        RuntimeError,  # unmapped errors from pikepdf's C++ layer
        *image_extraction_errors(),
    ) as e:
        logger.debug(
            "High-level extraction failed (%s: %s). Trying low-level recovery.",
            type(e).__name__,
            e,
        )

    # Block B: Direct stream byte extraction fallback
    try:
        raw_bytes = xobj.read_bytes()
    except (pikepdf.PdfError, pikepdf.DataDecodingError) as e:
        logger.warning("Stream data is unfilterable (%s); recovery aborted.", e)
        return None

    return _assemble_fallback(xobj, width, height, raw_bytes)


def _assemble_fallback(xobj, width, height, raw_bytes) -> "Image.Image | None":
    """Block C: raw 8-bit RGB samples, or a self-describing encoded image."""
    from PIL import Image

    decode = xobj.get("/Decode")
    try:
        # frombytes accepts surplus data, which would misread encoded image bytes.
        if len(raw_bytes) != width * height * 3:
            raise ValueError("not raw 8-bit RGB")
        img = Image.frombytes("RGB", (width, height), raw_bytes)
    except ValueError:
        if decode is not None:
            logger.debug("Low-level recovery cannot honour /Decode for this image.")
            return None
        try:
            return Image.open(io.BytesIO(raw_bytes))
        except (OSError, ValueError, TypeError) as e:
            logger.debug("Low-level canvas assembly failed (%s).", e)
            return None
    return img if decode is None else _apply_decode(img, decode)


def _apply_decode(img: "Image.Image", decode) -> "Image.Image | None":
    """Map each band's samples through its /Decode range (ISO 32000-2 8.9.5.2)."""
    try:
        ranges = [float(v) for v in decode]
    except (TypeError, ValueError):
        return None
    bands = len(img.getbands())
    if len(ranges) != 2 * bands:
        logger.debug("Ignoring image: /Decode has %d entries for %d bands.", len(ranges), bands)
        return None
    table = []
    for band in range(bands):
        dmin, dmax = ranges[2 * band], ranges[2 * band + 1]
        for v in range(256):
            table.append(min(255, max(0, round(255 * (dmin + v / 255 * (dmax - dmin))))))
    return img.point(table)
