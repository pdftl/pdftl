# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/mrc/codecs.py

"""Per-layer encoding for MRC compression.

Stencils are encoded with JBIG2 generic mode when a jbig2enc-family binary
is on PATH, else with the native CCITT G4 / Flate 1-bit encoder.

Every stencil is rendered back through pypdfium2 and its ink coverage
compared with the source mask. Both polarities are tried, so the correct
`/Decode` array is found empirically.
"""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image

from pdftl.utils.mrc import MrcError

logger = logging.getLogger(__name__)

#: Candidate binary names for a jbig2enc-family encoder, PATH-detected.
_JBIG2_BINARY_NAMES = ("jbig2", "jbig2enc")

#: Allowed ink-coverage difference after the round-trip. The codecs are
#: lossless; this only absorbs rendering noise.
VERIFY_TOLERANCE = 0.01

DEFAULT_FG_QUALITY = 45
DEFAULT_BG_JPEG_QUALITY = 75


@dataclass(frozen=True)
class StencilStream:
    """An encoded stencil plus everything its PDF image dictionary needs."""

    data: bytes
    codec: str  # "jbig2_generic" | "ccitt_g4" | "flate"
    filter_name: str  # "/JBIG2Decode" | "/CCITTFaxDecode" | "/FlateDecode"
    width: int
    height: int
    decode: tuple[int, int] | None
    decode_parms: dict | None


@lru_cache(maxsize=1)
def jbig2_binary() -> str | None:
    """Path of a jbig2enc-family binary on PATH, or None."""
    for name in _JBIG2_BINARY_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return None


def mask_ink_fraction(mask: Image.Image) -> float:
    """Fraction of a mode-"1" mask that is ink (0 is ink, 1 is paper)."""
    hist = mask.convert("L").histogram()
    return sum(hist[:128]) / float(mask.width * mask.height)


def _run_jbig2_generic(exe: str, mask: Image.Image) -> bytes:
    """Encode `mask` in JBIG2 generic mode as PDF-embeddable bytes (`-p`).

    Refinement coding (`-r`) is not used: some readers crash on it.
    """
    with tempfile.TemporaryDirectory(prefix="pdftl_mrc_jbig2_") as work:
        png = Path(work) / "mask.png"
        mask.save(png, format="PNG")
        result = subprocess.run(
            [exe, "-p", str(png)], capture_output=True, timeout=120, check=False
        )
        if result.returncode != 0 or not result.stdout:
            detail = (result.stderr or b"").decode("utf-8", "replace").strip()
            raise MrcError(f"the JBIG2 encoder failed: {detail or 'no output'}")
        return result.stdout


def _verify_stencil_bytes(
    data: bytes,
    filter_name: str,
    decode_parms: dict | None,
    width: int,
    height: int,
    source_ink_fraction: float,
    tolerance: float = VERIFY_TOLERANCE,
) -> tuple[bool, tuple[int, int] | None]:
    """Render `data` as an image mask and compare its ink coverage.

    Tries no `/Decode`, then `/Decode [1 0]`. Returns `(True, decode)` for
    the first that matches, else `(False, None)`.
    """
    import pikepdf
    import pypdfium2
    from pikepdf import Dictionary, Name

    from pdftl.utils.page_images import render_page_to_pil

    for decode in (None, (1, 0)):
        pdf = pikepdf.Pdf.new()
        try:
            stream = pikepdf.Stream(pdf, data)
            stream["/Type"] = Name("/XObject")
            stream["/Subtype"] = Name("/Image")
            stream["/Width"] = int(width)
            stream["/Height"] = int(height)
            stream["/ImageMask"] = True
            stream["/Filter"] = Name(filter_name)
            if decode is not None:
                stream["/Decode"] = pikepdf.Array(list(decode))
            if decode_parms:
                stream["/DecodeParms"] = Dictionary(**decode_parms)
            xobj = pdf.make_indirect(stream)
            content = (
                f"q 1 1 1 rg 0 0 {width} {height} re f Q\n"
                f"q 0 g {width} 0 0 {height} 0 0 cm /Im0 Do Q"
            ).encode()
            page = pikepdf.Dictionary(
                Type=Name("/Page"),
                MediaBox=[0, 0, width, height],
                Resources=Dictionary(XObject=Dictionary(Im0=xobj)),
                Contents=pdf.make_stream(content),
            )
            pdf.pages.append(pikepdf.Page(pdf.make_indirect(page)))

            rendered = render_page_to_pil(pdf, 0, dpi=72.0)
            got = mask_ink_fraction(rendered.convert("L"))
        except (pikepdf.PdfError, pypdfium2.PdfiumError):
            logger.debug("MRC stencil verification render failed", exc_info=True)
            continue
        finally:
            pdf.close()

        if abs(got - source_ink_fraction) <= tolerance:
            return True, decode
    return False, None


def encode_stencil(mask: Image.Image) -> StencilStream:
    """Encode a mode-"1" mask (0=ink, 1=paper) as a verified PDF stencil.

    Uses JBIG2 if available and it verifies, else CCITT G4 or Flate.
    """
    if mask.mode != "1":
        raise ValueError(f"a mask must be Pillow mode '1', got {mask.mode!r}")
    source_ink_fraction = mask_ink_fraction(mask)

    exe = jbig2_binary()
    if exe:
        try:
            data = _run_jbig2_generic(exe, mask)
        except (OSError, subprocess.SubprocessError, MrcError):
            logger.debug(
                "MRC: jbig2 encoder invocation failed; falling back to CCITT", exc_info=True
            )
        else:
            ok, decode = _verify_stencil_bytes(
                data, "/JBIG2Decode", None, mask.width, mask.height, source_ink_fraction
            )
            if ok:
                return StencilStream(
                    data=data,
                    codec="jbig2_generic",
                    filter_name="/JBIG2Decode",
                    width=mask.width,
                    height=mask.height,
                    decode=decode,
                    decode_parms=None,
                )
            logger.debug("MRC: jbig2 stencil failed verification; falling back to CCITT")

    from pdftl.utils.images.pil_to_pdf import get_optimal_1bit_payload

    data, filt, parms = get_optimal_1bit_payload(mask)
    # Keys arrive as "/K" etc.; Dictionary(**d) would make them "//K".
    parms_dict = (
        {str(k).lstrip("/"): v for k, v in dict(parms).items()} if parms is not None else None
    )
    ok, decode = _verify_stencil_bytes(
        data, filt, parms_dict, mask.width, mask.height, source_ink_fraction
    )
    if not ok:
        raise MrcError("MRC stencil verification failed for both CCITT/Flate polarities")
    codec_name = "ccitt_g4" if filt == "/CCITTFaxDecode" else "flate"
    return StencilStream(
        data=data,
        codec=codec_name,
        filter_name=filt,
        width=mask.width,
        height=mask.height,
        decode=decode,
        decode_parms=parms_dict,
    )


def colour_space(gray: bool):
    from pikepdf import Name

    return Name("/DeviceGray") if gray else Name("/DeviceRGB")


def _jpeg(image: Image.Image, quality: int, gray: bool) -> bytes:
    buf = io.BytesIO()
    image.convert("L" if gray else "RGB").save(
        buf, format="JPEG", quality=quality, progressive=False
    )
    return buf.getvalue()


def encode_foreground_jpeg(
    image: Image.Image, quality: int = DEFAULT_FG_QUALITY, *, gray: bool = False
) -> bytes:
    """`/DCTDecode` bytes for the foreground (ink colour) layer."""
    return _jpeg(image, quality, gray)


def encode_background(
    image: Image.Image, quality: int = DEFAULT_BG_JPEG_QUALITY, *, gray: bool = False
) -> tuple[bytes, str, dict]:
    """Background layer bytes, filter name, and extra XObject dict keys.

    JPEG rather than JPEG 2000, which tiles visibly on flat paper at
    useful rates.
    """
    return (
        _jpeg(image, quality, gray),
        "/DCTDecode",
        {"ColorSpace": colour_space(gray), "BitsPerComponent": 8},
    )


def make_image_xobject(pdf, data: bytes, width: int, height: int, filt: str, **extra):
    """Build a plain Image XObject stream, made indirect in `pdf`."""
    import pikepdf
    from pikepdf import Name

    stream = pikepdf.Stream(pdf, data)
    stream["/Type"] = Name("/XObject")
    stream["/Subtype"] = Name("/Image")
    stream["/Width"] = int(width)
    stream["/Height"] = int(height)
    stream["/Filter"] = Name(filt)
    for key, value in extra.items():
        stream["/" + key] = value
    return pdf.make_indirect(stream)


def make_stencil_xobject(pdf, stencil: StencilStream):
    """Build the stencil as an `/ImageMask` XObject, made indirect in `pdf`."""
    import pikepdf

    # Interpolate: otherwise some viewers draw each stencil pixel as a hard
    # square when zoomed in.
    extra: dict = {"ImageMask": True, "Interpolate": True}
    if stencil.decode is not None:
        extra["Decode"] = pikepdf.Array(list(stencil.decode))
    if stencil.decode_parms:
        extra["DecodeParms"] = pikepdf.Dictionary(**stencil.decode_parms)
    return make_image_xobject(
        pdf, stencil.data, stencil.width, stencil.height, stencil.filter_name, **extra
    )
