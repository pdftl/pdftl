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
# Below this mean SSIM, JPEG has swapped local structure for new structure of
# its own that PSNR (an average over the whole image) does not see even
# though PSNR still passes: a synthetic gradient-plus-noise photograph whose
# only PSNR-passing JPEG is blocky tops out at 0.9871 (any quality up to 95,
# any chroma setting); real photographs top out at 0.9965 and above in a
# sample corpus. A 15x15 window is what separates the two; 7x7 and narrower
# windows, and a worst-tile variant, do not (a real photograph's sharpest
# edges score below the noise photograph's uniformly-mediocre score).
MIN_SSIM = 0.99

_SSIM_WINDOW = 15
_C1 = (0.01 * 255) ** 2
_C2 = (0.03 * 255) ** 2


def psnr(a, b) -> float:
    """Peak signal-to-noise ratio of `b` against `a`, in dB, over all bands."""
    from PIL import ImageChops, ImageStat

    rms = ImageStat.Stat(ImageChops.difference(a, b)).rms
    mse = sum(r * r for r in rms) / len(rms)
    return math.inf if mse == 0 else 10 * math.log10(255**2 / mse)


def ssim(a, b) -> float:
    """Mean windowed SSIM of `b` against `a`, on luma.

    skimage's `structural_similarity` formula (uniform window, sample
    covariance) over every whole window; an image smaller than the window
    is one window.
    """
    import numpy as np

    x = np.asarray(a.convert("L"), dtype=np.float64)
    y = np.asarray(b.convert("L"), dtype=np.float64)
    win = min(_SSIM_WINDOW, *x.shape)

    def box(z):
        s = np.pad(z, ((1, 0), (1, 0))).cumsum(0).cumsum(1)  # exact: integer sums
        return (s[win:, win:] - s[:-win, win:] - s[win:, :-win] + s[:-win, :-win]) / win**2

    ux, uy = box(x), box(y)
    n = win**2
    norm = n / max(n - 1, 1)
    vx = norm * (box(x * x) - ux * ux)
    vy = norm * (box(y * y) - uy * uy)
    vxy = norm * (box(x * y) - ux * uy)
    num = (2 * ux * uy + _C1) * (2 * vxy + _C2)
    den = (ux**2 + uy**2 + _C1) * (vx + vy + _C2)
    return float((num / den).mean())


def faithful(a, b, palette: bool = False, structural: bool = False) -> bool:
    """Whether `b` keeps every pixel of `a` close; `palette` if `a` was dithered.

    With `structural`, also require `b` to keep `a`'s local structure: PSNR
    alone passes a JPEG that turns per-pixel noise into new, DCT-block
    structure of its own, since that stays close in an average over the
    whole image. Skipped for a palette image: its dither is itself noise
    with no structure of its own to keep, the same reason its PSNR floor is
    lower.
    """
    floor = MIN_PSNR_PALETTE if palette else MIN_PSNR
    if psnr(a, b) < floor:
        return False
    return not structural or palette or ssim(a, b) >= MIN_SSIM
