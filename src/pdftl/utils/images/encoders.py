import io
import logging
import zlib
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from PIL import Image


def serialize_grayscale_stream(
    xobj, pil_img: "Image.Image", fmt: str, quality: int, zlib_compression_level=6
) -> bool:
    """Downsamples a PIL layout to monochrome and writes it back into the stream."""
    import pikepdf

    if pil_img is None:
        logger.debug(
            "PIL image is None; "
            "pixel data cannot be decoded, enforcing grayscale metadata conversion only."
        )
        # We cannot decode pixels, but we still enforce grayscale metadata conversion
        try:
            xobj[pikepdf.Name("/ColorSpace")] = pikepdf.Name("/DeviceGray")
            return True
        except pikepdf.PdfError as exc:
            logger.debug("pikepdf.PdfError: %s", exc)
            return False
    try:
        gray_pil = pil_img.convert("L")
    except ValueError:
        logger.warning("PIL failed downsampling image channels to monochrome.")
        return False

    # Block A: Clean PDF layout tables using strict pikepdf.Name references
    try:
        for key_str in ("ColorSpace", "Intent", "DecodeParms"):
            name_key = pikepdf.Name(f"/{key_str}")
            if name_key in xobj:
                del xobj[name_key]
        xobj[pikepdf.Name("/ColorSpace")] = pikepdf.Name("/DeviceGray")
    except pikepdf.PdfError as exc:
        logger.warning("PDF stream metadata dictionary mutation rejected.")
        logger.debug("Rejection reason: %s", exc)
        return False

    # Block B: Final compression stream commit
    logger.debug("Final compression stream commit, fmt=%s", fmt)
    try:
        if fmt in ("flatedecode", "png"):
            compressed_bytes = zlib.compress(gray_pil.tobytes(), level=zlib_compression_level)
            xobj.write(compressed_bytes, filter=pikepdf.Name("/FlateDecode"))
            xobj.ColorSpace = pikepdf.Name("/DeviceGray")
        else:
            output_io = io.BytesIO()
            gray_pil.save(output_io, format="JPEG", quality=quality)
            xobj.write(output_io.getvalue(), filter=pikepdf.Name("/DCTDecode"))
        return True
    except (RuntimeError, OSError, ValueError, pikepdf.PdfError):
        logger.warning("Failed writing compressed monochrome buffer back to PDF stream slot.")
        return False
