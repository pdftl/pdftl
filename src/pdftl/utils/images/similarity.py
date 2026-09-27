# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/images/similarity.py

"""Whether a lossy re-encoding of an image stays faithful to it."""

from __future__ import annotations

import math

MIN_PSNR = 30.0
# Dither in a palette image is noise JPEG smooths away, not detail it loses.
MIN_PSNR_PALETTE = 27.0


def psnr(a, b) -> float:
    """Peak signal-to-noise ratio of `b` against `a`, in dB, over all bands."""
    from PIL import ImageChops, ImageStat

    rms = ImageStat.Stat(ImageChops.difference(a, b)).rms
    mse = sum(r * r for r in rms) / len(rms)
    return math.inf if mse == 0 else 10 * math.log10(255**2 / mse)


def faithful(a, b, palette: bool = False) -> bool:
    """Whether `b` keeps every pixel of `a` close; `palette` if `a` was dithered."""
    return psnr(a, b) >= (MIN_PSNR_PALETTE if palette else MIN_PSNR)
