# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/output/recompress.py

"""Recompress Flate streams losslessly, keeping any predictor, and compress
unfiltered ones the same way.

qpdf's own recompression decodes a stream's PNG predictor and writes it
back without one, which can make a predicted image many times larger.
Here only the zlib layer is redone, so predictors survive. 8-bit images
also try PNG row filters (and oxipng, when installed). Deflate is zlib at
level 9, or zopfli inside `zopfli_deflate(True)`. A stream is rewritten only if
the result is smaller. Streams are compressed in parallel.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import logging
import os
import shutil
import struct
import subprocess
import tempfile
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

logger = logging.getLogger(__name__)

TRIAL_LEVEL = 6  # candidates are ranked at this level, the winner compressed at 9
OXIPNG_MIN_BYTES = 2048  # smaller images are not worth a subprocess
RANK_SAMPLE_BYTES = 1 << 20  # larger images rank filters on evenly spaced row bands
RANK_BAND_ROWS = 32
_PNG_COLOR_TYPE = {1: 0, 3: 2}  # components -> PNG colour type (grey, RGB)
BATCH_BYTES = 64 << 20  # decoded data held for the worker threads at once
ZOPFLI_MAX_BYTES = 8 << 20  # larger streams take too long for ~5% gain
ZOPFLI_FULL_BYTES = 1 << 20  # up to this size, 5 iterations; above, 1


@dataclass
class RecompressStats:
    streams: int = 0
    rewritten: int = 0
    bytes_saved: int = 0


def recompress_streams(pdf) -> RecompressStats:
    """Recompress every single-Flate stream in `pdf`, and compress every
    unfiltered one but XMP metadata; see the module docstring."""
    import pikepdf

    stats = RecompressStats()
    batch: list = []
    held = 0
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        for obj in pdf.objects:
            if not isinstance(obj, pikepdf.Stream):
                continue
            inputs = _stream_inputs(obj)
            if inputs is None:
                continue
            raw, data, parms = inputs
            stats.streams += 1
            image = _image_inputs(obj, parms)
            batch.append((obj, raw, parms, pool.submit(_best_encoding, data, image)))
            held += len(data) + (len(image[0]) if image else 0)
            if held >= BATCH_BYTES:
                _commit(batch, stats)
                batch, held = [], 0
        _commit(batch, stats)
    logger.debug("recompress: %s", stats)
    return stats


def _stream_inputs(obj):
    """(stored bytes, decoded bytes, DecodeParms) of a stream to encode, or None."""
    filt = obj.get("/Filter")
    if _is_flate(filt):
        raw = obj.read_raw_bytes()
        try:
            return raw, zlib.decompress(raw), obj.get("/DecodeParms")
        except zlib.error:
            return None  # damaged or truncated: leave it as it is
    if _unfiltered(filt) and obj.get("/Type") != "/Metadata":  # XMP is left to compress_xmp
        raw = obj.read_raw_bytes()
        return raw, raw, None
    return None


def _best_encoding(data: bytes, image) -> tuple[bytes, tuple[int, int] | None]:
    """(encoded, (columns, colors) if PNG-predicted else None)."""
    best, predicted = deflate(data), None
    if image is not None:
        samples, width, height, colors = image
        encoded = _encode_image(samples, width, height, colors)
        if len(encoded) < len(best):
            best, predicted = encoded, (width, colors)
    return best, predicted


def _commit(batch: list, stats: RecompressStats) -> None:
    import pikepdf

    for obj, raw, parms, future in batch:
        best, predicted = future.result()
        if len(best) >= len(raw):
            continue
        if predicted is not None:
            parms = _png_parms(*predicted)
        obj.write(best, filter=pikepdf.Name.FlateDecode, decode_parms=parms)
        stats.rewritten += 1
        stats.bytes_saved += len(raw) - len(best)


def _png_parms(columns: int, colors: int):
    import pikepdf

    return pikepdf.Dictionary(Predictor=15, Colors=colors, BitsPerComponent=8, Columns=columns)


@functools.lru_cache(maxsize=1)
def _zopfli():
    try:
        import zopfli.zlib
    except ImportError:
        return None
    return zopfli.zlib


_USE_ZOPFLI = False  # module-wide, as worker threads do not inherit context
_ZOPFLI_MEMO: dict[bytes, bytes] = {}  # inside the block: input digest -> zopfli output


@contextlib.contextmanager
def zopfli_deflate(enabled: bool):
    """Make `deflate` also try zopfli inside the block, if `enabled`."""
    global _USE_ZOPFLI
    if not enabled:
        yield
        return
    require_zopfli()
    _USE_ZOPFLI = True
    try:
        yield
    finally:
        _USE_ZOPFLI = False
        _ZOPFLI_MEMO.clear()


def require_zopfli() -> None:
    """Raise PackageError unless zopfli is installed."""
    from pdftl.exceptions import PackageError

    if _zopfli() is None:
        raise PackageError("deflate=zopfli needs zopfli: pip install pdftl[zopfli]")


def deflate(data: bytes) -> bytes:
    """The smallest zlib stream for `data`: zlib level 9's, or zopfli's if smaller."""
    best = zlib.compress(data, 9)
    zopfli = _zopfli() if _USE_ZOPFLI else None
    if zopfli is not None and len(data) <= ZOPFLI_MAX_BYTES:
        # A pass that weighs a stream and the save that writes it deflate the same bytes.
        key = hashlib.blake2b(data, digest_size=16).digest()
        candidate = _ZOPFLI_MEMO.get(key)
        if candidate is None:
            iterations = 5 if len(data) <= ZOPFLI_FULL_BYTES else 1
            candidate = _ZOPFLI_MEMO[key] = zopfli.compress(data, numiterations=iterations)
        if len(candidate) < len(best):
            best = candidate
    return best


def _unfiltered(filt) -> bool:
    import pikepdf

    return filt is None or (isinstance(filt, pikepdf.Array) and len(filt) == 0)


def _is_flate(filt) -> bool:
    import pikepdf

    if isinstance(filt, pikepdf.Array):
        return len(filt) == 1 and filt[0] == pikepdf.Name.FlateDecode
    return filt == pikepdf.Name.FlateDecode


def _image_inputs(obj, parms):
    """(samples, width, height, colors) of an 8-bit image PNG filters can model, or None."""
    import pikepdf

    if obj.get("/Subtype") != "/Image" or obj.get("/BitsPerComponent") != 8:
        return None
    if isinstance(parms, pikepdf.Array):
        return None
    try:
        width, height = int(obj.Width), int(obj.Height)
        samples = obj.read_bytes()  # predictor, if any, undone
    except (AttributeError, TypeError, ValueError, pikepdf.PdfError):
        return None
    if width <= 0 or height <= 0 or len(samples) % (width * height):
        return None
    return samples, width, height, len(samples) // (width * height)


def _encode_image(samples: bytes, width: int, height: int, colors: int) -> bytes:
    filtered = best_png_filtering(samples, width, height, colors)
    oxi = (
        _oxipng_idat(samples, width, height, colors) if len(samples) >= OXIPNG_MIN_BYTES else None
    )
    if oxi is None:
        return deflate(filtered)
    # oxipng's own deflate search already beats zopfli on filtered rows.
    encoded = zlib.compress(filtered, 9)
    return oxi if len(oxi) < len(encoded) else encoded


def _image_candidate(obj, parms):
    """(bytes, DecodeParms) of the best PNG-predicted encoding of an 8-bit image, or None."""
    image = _image_inputs(obj, parms)
    if image is None:
        return None
    samples, width, height, colors = image
    return _encode_image(samples, width, height, colors), _png_parms(width, colors)


# --- PNG row filters ---


def _filtered_rows(rows, bpp: int):
    """Each PNG filter (0-4) applied to every row: an array of shape (5, height, row bytes)."""
    import numpy as np

    x = rows.astype(np.int16)
    left = np.zeros_like(x)
    left[:, bpp:] = x[:, :-bpp]
    up = np.zeros_like(x)
    up[1:] = x[:-1]
    up_left = np.zeros_like(x)
    up_left[1:, bpp:] = x[:-1, :-bpp]
    p = left + up - up_left
    pa, pb, pc = np.abs(p - left), np.abs(p - up), np.abs(p - up_left)
    paeth = np.where((pa <= pb) & (pa <= pc), left, np.where(pb <= pc, up, up_left))
    predictions = (np.zeros_like(x), left, up, (left + up) // 2, paeth)
    return np.stack([(x - pred) % 256 for pred in predictions]).astype(np.uint8)


def _with_filter_bytes(filtered, choice) -> bytes:
    import numpy as np

    height = filtered.shape[1]
    rows = filtered[choice, np.arange(height)]
    return np.hstack([choice.astype(np.uint8)[:, None], rows]).tobytes()


def best_png_filtering(samples: bytes, width: int, height: int, colors: int) -> bytes:
    """PNG-filtered rows (filter byte + data) that compress smallest among the
    five fixed filters and the per-row minimum-sum-of-deviations heuristic."""
    import numpy as np

    rows = np.frombuffer(samples, dtype=np.uint8).reshape(height, width * colors)
    filtered = _filtered_rows(rows, colors)
    signed = filtered.astype(np.int16)
    deviation = np.minimum(signed, 256 - signed).sum(axis=2)
    choices = [np.full(height, k) for k in range(5)] + [deviation.argmin(axis=0)]
    rows_used = _sample_rows(height, width * colors)
    sample = filtered[:, rows_used]
    best = min(
        choices,
        key=lambda c: len(zlib.compress(_with_filter_bytes(sample, c[rows_used]), TRIAL_LEVEL)),
    )
    return _with_filter_bytes(filtered, best)


def _sample_rows(height: int, row_bytes: int):
    """Row indices to rank on: all of them, or bands spread over the image."""
    import numpy as np

    if height * row_bytes <= RANK_SAMPLE_BYTES:
        return np.arange(height)
    bands = max(1, RANK_SAMPLE_BYTES // (RANK_BAND_ROWS * row_bytes))
    starts = np.linspace(0, max(0, height - RANK_BAND_ROWS), bands).astype(int)
    return np.unique(
        np.concatenate([np.arange(s, min(height, s + RANK_BAND_ROWS)) for s in starts])
    )


# --- oxipng ---


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def _oxipng_idat(samples: bytes, width: int, height: int, colors: int) -> bytes | None:
    """oxipng's IDAT for this image, keeping colour type and depth; None if unavailable."""
    exe = shutil.which("oxipng")
    color_type = _PNG_COLOR_TYPE.get(colors)
    if exe is None or color_type is None:
        return None
    header = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    plain = b"".join(
        b"\x00" + samples[r * width * colors : (r + 1) * width * colors] for r in range(height)
    )
    png = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(plain, 1))
    )
    png += _png_chunk(b"IEND", b"")
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        f.write(png)
        f.flush()
        proc = subprocess.run(
            [exe, "-o", "4", "--nx", "--strip", "all", "-q", f.name],
            capture_output=True,
            check=False,
        )
        if proc.returncode:
            return None
        return _idat_if_same_format(open(f.name, "rb").read(), header)


def _idat_if_same_format(png: bytes, header: bytes) -> bytes | None:
    """Concatenated IDAT data, provided the IHDR is unchanged (same size, type, depth)."""
    out, i, same = b"", 8, False
    while i + 8 <= len(png):
        (length,) = struct.unpack(">I", png[i : i + 4])
        kind, data = png[i + 4 : i + 8], png[i + 8 : i + 8 + length]
        if kind == b"IHDR":
            same = data == header
        elif kind == b"IDAT":
            out += data
        i += 12 + length
    return out if same and out else None
