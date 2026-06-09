import logging
from typing import TYPE_CHECKING
from pdftl.utils.images.selectors import extract_to_pil
from pdftl.utils.images.encoders import serialize_grayscale_stream

if TYPE_CHECKING:
    pass


logger = logging.getLogger(__name__)


def convert_image_dict_to_grayscale(img: dict, quality: int) -> bool:
    xobj = img["xobj"]

    # 1. Structural Validation Guards (strict)
    if xobj.get("/ImageMask") or int(xobj.get("/BitsPerComponent", 8)) == 1:
        return False

    # 2. MUST have ColorSpace (strict rule from tests)
    cs = xobj.get("/ColorSpace")
    if cs is None:
        return False

    # 3. Decode attempt
    pil_img = extract_to_pil(xobj)
    decode_failed = pil_img is None

    # If we couldn't decode and didn't fix anything → fail
    if decode_failed:
        return False

    # 4. Already grayscale images are not processed
    if pil_img.mode in ("L", "1"):
        return False

    # 5. Must still have valid ColorSpace after all checks
    if "/ColorSpace" not in xobj:
        return False

    # 6. Clean transparency artifacts
    _neutralize_nested_masks(xobj)

    # 7. Write grayscale stream
    fmt = img.get("format", "dctdecode").lower()
    return serialize_grayscale_stream(xobj, pil_img, fmt, quality)


def _neutralize_nested_masks(xobj) -> None:
    """Forces nested transparency channels to drop color intent bounds."""
    import pikepdf

    if "/SMask" in xobj:
        smask = xobj["/SMask"]
        if isinstance(smask, pikepdf.Stream) and "/ColorSpace" in smask:
            smask.ColorSpace = pikepdf.Name("/DeviceGray")
