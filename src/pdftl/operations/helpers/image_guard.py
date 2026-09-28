# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/helpers/image_guard.py

"""Keep an image re-encoding only where it made the image smaller.

`snapshot_images` records every image XObject's stored bytes and dictionary;
`restore_unless_smaller` puts back any image whose new stream is not smaller,
or, if asked, whose new pixels are not faithful to the old.
`jbig2_encode_inverted_bitonal` re-encodes 1-bit images drawn through
`/Decode [1 0]` as JBIG2 generic region, keeping that `/Decode` array so the
image's polarity and appearance are unchanged.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

Snapshot = dict[tuple[int, int], tuple[bytes, dict]]


def _images(pdf) -> list[Any]:
    import pikepdf

    return [
        obj
        for obj in pdf.objects
        if isinstance(obj, pikepdf.Stream) and obj.get("/Subtype") == "/Image"
    ]


def snapshot_images(pdf) -> Snapshot:
    return {img.objgen: (img.read_raw_bytes(), dict(img.items())) for img in _images(pdf)}


def _put(img, raw: bytes, items: dict) -> None:
    img.write(raw, filter=items.get("/Filter"), decode_parms=items.get("/DecodeParms"))
    for key in [k for k in img.keys() if k not in items and k != "/Length"]:
        del img[key]
    for key, value in items.items():
        if key not in ("/Length", "/Filter", "/DecodeParms"):  # set by write()
            img[key] = value


def _pixels(img):
    """`img` decoded to gray or RGB, or None if it cannot be decoded."""
    import pikepdf

    from pdftl.utils.pikepdf_compatibility_utils import as_pil_image_compat, image_decode_errors

    try:
        pil_img = as_pil_image_compat(pikepdf.PdfImage(img))
        return pil_img.convert("L" if pil_img.mode in ("1", "L") else "RGB")
    except image_decode_errors():
        return None


def _stays_faithful(img, raw: bytes, items: dict) -> bool:
    """Whether `img` still shows what its snapshot did; if not, it is left restored."""
    from pdftl.utils.images.similarity import faithful
    from pdftl.utils.pikepdf_compatibility_utils import is_indexed_image

    new_raw, new_items, palette = img.read_raw_bytes(), dict(img.items()), is_indexed_image(img)
    after = _pixels(img)
    _put(img, raw, items)
    before = _pixels(img)
    if before is None or after is None or before.size != after.size:
        return False
    if after.mode != before.mode:
        before, after = before.convert("RGB"), after.convert("RGB")
    if not faithful(before, after, palette or is_indexed_image(img)):
        return False
    _put(img, new_raw, new_items)
    return True


def restore_unless_smaller(pdf, snapshot: Snapshot, keep_faithful: bool = False) -> int:
    """Restore every image not now smaller than in `snapshot`; return how many were kept.

    With `keep_faithful`, also restore every changed image whose pixels are not
    faithful to the snapshot's.
    """
    kept = 0
    for objgen, (raw, items) in snapshot.items():
        img = pdf.get_object(objgen)
        new_raw = img.read_raw_bytes()
        if len(new_raw) >= len(raw):
            _put(img, raw, items)
        elif not keep_faithful or new_raw == raw or _stays_faithful(img, raw, items):
            kept += 1
    return kept


def _is_inverted_bitonal(img) -> bool:
    import pikepdf

    decode = img.get("/Decode")
    if img.get("/ImageMask") or decode is None or len(decode) != 2:
        return False
    try:
        bits = int(img.get("/BitsPerComponent", 0))
        flipped = [float(v) for v in decode] == [1.0, 0.0]
    except (TypeError, ValueError):
        return False
    return bits == 1 and flipped and img.get("/ColorSpace") in (None, pikepdf.Name.DeviceGray)


def _run_jbig2_generic(exe: str, mask) -> bytes | None:
    """Encode a Pillow mode-"1" image as a PDF-embeddable JBIG2 generic region stream."""
    with tempfile.TemporaryDirectory(prefix="pdftl_img_guard_jbig2_") as work:
        png = Path(work) / "mask.png"
        mask.save(png, format="PNG")
        try:
            result = subprocess.run(
                [exe, "-p", str(png)], capture_output=True, timeout=120, check=False
            )
        except (OSError, subprocess.SubprocessError):
            return None
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def _jbig2_reencode_one(exe: str, img) -> bool:
    """Re-encode one inverted bitonal image as JBIG2; keep it only if smaller."""
    import pikepdf

    from pdftl.utils.pikepdf_compatibility_utils import as_pil_image_compat, image_decode_errors

    parms = img.get("/DecodeParms")
    if parms is not None and not isinstance(parms, pikepdf.Dictionary):
        return False  # malformed; PdfImage may decode it wrongly without raising
    original = img.read_raw_bytes()
    try:
        appearance = as_pil_image_compat(pikepdf.PdfImage(img)).convert("1")
    except image_decode_errors():
        return False
    # White where the stored sample bit is 1.
    stored = ImageOps.invert(appearance.convert("L")).convert("1", dither=Image.Dither.NONE)
    data = _run_jbig2_generic(exe, stored)
    if data is None or len(data) >= len(original):
        return False
    img.write(data, filter=pikepdf.Name.JBIG2Decode)  # clears any stale /DecodeParms
    return True


def jbig2_encode_inverted_bitonal(pdf) -> int:
    """Re-encode inverted 1-bit images (CCITT, Flate or unfiltered) as JBIG2 generic
    region, keeping their `/Decode [1 0]`; return how many were changed.

    A no-op without a jbig2enc-family binary on PATH, or where the JBIG2
    stream is not smaller than the image's current stream.
    """
    import pikepdf

    from pdftl.utils.mrc.codecs import jbig2_binary

    exe = jbig2_binary()
    if exe is None:
        return 0
    changed = 0
    for img in _images(pdf):
        if not _is_inverted_bitonal(img) or img.get("/Filter") == pikepdf.Name.JBIG2Decode:
            continue
        if _jbig2_reencode_one(exe, img):
            changed += 1
    return changed
