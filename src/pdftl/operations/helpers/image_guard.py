# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/helpers/image_guard.py

"""Keep an image re-encoding only where it made the image smaller.

`snapshot_images` records every image XObject's stored bytes and dictionary;
`restore_unless_smaller` puts back any image whose new stream is not smaller,
or, if asked, whose new pixels are not faithful to the old.
`normalize_inverted_bitonal` rewrites 1-bit images drawn through
`/Decode [1 0]` to store their pixels the other way up, with no `/Decode`,
which looks identical and lets encoders that skip inverted images (as
ocrmypdf's JBIG2 step does) consider them.
"""

from __future__ import annotations

import zlib
from typing import Any

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


def normalize_inverted_bitonal(pdf) -> int:
    """Store inverted 1-bit images uninverted (CCITT, Flate or unfiltered); return the count."""
    import pikepdf

    changed = 0
    for img in _images(pdf):
        if not _is_inverted_bitonal(img):
            continue
        filt = img.get("/Filter")
        parms = img.get("/DecodeParms")
        if filt == pikepdf.Name.CCITTFaxDecode and not isinstance(parms, pikepdf.Array):
            parms = pikepdf.Dictionary(parms or {})
            parms.BlackIs1 = not bool(parms.get("/BlackIs1", False))  # the decoder inverts
            img.DecodeParms = parms
        elif filt in (None, pikepdf.Name.FlateDecode) and parms is None:
            inverted = bytes(b ^ 0xFF for b in img.read_bytes())
            img.write(zlib.compress(inverted, 9), filter=pikepdf.Name.FlateDecode)
        else:
            continue
        del img["/Decode"]
        changed += 1
    return changed
