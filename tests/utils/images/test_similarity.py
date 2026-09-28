# tests/utils/images/test_similarity.py
#
# Ground truth: images differing by a known constant, whose PSNR is
# 10 log10(255^2 / d^2) by hand, and whose SSIM (flat images have zero
# local variance everywhere, so SSIM reduces to its luminance term alone)
# is (2*ux*uy + C1) / (ux^2 + uy^2 + C1) by hand. Where available,
# skimage's own structural_similarity is an independent cross-check.

import io
import math

import numpy as np
import pytest
from PIL import Image

from pdftl.utils.images.similarity import (
    MIN_PSNR,
    MIN_PSNR_PALETTE,
    MIN_SSIM,
    _SSIM_WINDOW,
    faithful,
    psnr,
    ssim,
)

_C1 = (0.01 * 255) ** 2


def _pair(mode, offset, size=(8, 8)):
    a = Image.new(mode, size, (100,) * (1 if mode == "L" else 3))
    b = Image.new(mode, size, (100 + offset,) * (1 if mode == "L" else 3))
    return a, b


@pytest.mark.parametrize("mode", ["L", "RGB"])
@pytest.mark.parametrize("offset", [1, 5, 10, 20])
def test_psnr_of_a_constant_difference(mode, offset):
    a, b = _pair(mode, offset)
    assert psnr(a, b) == pytest.approx(20 * math.log10(255 / offset))


def test_identical_images_have_infinite_psnr():
    a, _ = _pair("RGB", 1)
    assert psnr(a, a) == math.inf


def test_one_band_differing_counts_a_third_of_its_error():
    a = Image.new("RGB", (8, 8), (100, 100, 100))
    b = Image.new("RGB", (8, 8), (110, 100, 100))
    assert psnr(a, b) == pytest.approx(10 * math.log10(255**2 / (100 / 3)))


def test_faithful_uses_the_palette_floor_only_for_palette_images():
    # An offset of 9 gives 29.0 dB: between the two floors.
    a, b = _pair("RGB", 9)
    assert MIN_PSNR_PALETTE < 20 * math.log10(255 / 9) < MIN_PSNR
    assert not faithful(a, b)
    assert faithful(a, b, palette=True)


# --- ssim() ---


def test_ssim_of_identical_images_is_one():
    a, _ = _pair("L", 1)
    assert ssim(a, a) == 1.0


@pytest.mark.parametrize("offset", [1, 5, 20, 50])
def test_ssim_of_a_flat_constant_offset(offset):
    # Flat images have zero local variance everywhere, so the contrast and
    # structure terms are both exactly 1 (0 + C2 over 0 + C2); only the
    # luminance term is left.
    a, b = _pair("L", offset)
    expected = (2 * 100 * (100 + offset) + _C1) / (100**2 + (100 + offset) ** 2 + _C1)
    assert ssim(a, b) == pytest.approx(expected)


def test_ssim_below_the_window_size_is_one_window():
    # 8x8 is smaller than the 15x15 window, so the whole image is one
    # window; still the flat-image formula.
    a, b = _pair("L", 5, size=(8, 8))
    expected = (2 * 100 * 105 + _C1) / (100**2 + 105**2 + _C1)
    assert ssim(a, b) == pytest.approx(expected)


def test_ssim_reads_luma_only_ignoring_chroma():
    size = (4, 4)
    first = (100, 150, 200)
    same_luma = Image.new("RGB", size, first).convert("L").getpixel((0, 0))
    # Two different colours that PIL's RGB -> L happens to map to the same
    # grey level (searched, not guessed).
    second = next(
        (r, g, b)
        for r in range(0, 256, 17)
        for g in range(0, 256, 17)
        for b in range(0, 256, 17)
        if (r, g, b) != first
        and Image.new("RGB", (1, 1), (r, g, b)).convert("L").getpixel((0, 0)) == same_luma
    )
    a, b = Image.new("RGB", size, first), Image.new("RGB", size, second)
    assert ssim(a, b) == 1.0


def test_ssim_drops_when_local_structure_is_scrambled():
    rng = np.random.default_rng(0)
    checker = (np.indices((40, 40)).sum(axis=0) % 2) * 255
    a = Image.fromarray(checker.astype(np.uint8), "L")
    shuffled = checker.flatten()
    rng.shuffle(shuffled)
    b = Image.fromarray(shuffled.reshape(40, 40).astype(np.uint8), "L")
    assert ssim(a, b) < 0.2  # same pixel values, same global mean, no shared layout


def test_ssim_against_skimage_structural_similarity():
    skimage_metrics = pytest.importorskip("skimage.metrics")
    rng = np.random.default_rng(1)
    base = rng.integers(0, 256, (60, 60), dtype=np.uint8)
    noisy = np.clip(base.astype(np.int16) + rng.normal(0, 10, base.shape), 0, 255).astype(np.uint8)
    a, b = Image.fromarray(base, "L"), Image.fromarray(noisy, "L")
    theirs = skimage_metrics.structural_similarity(
        np.asarray(a), np.asarray(b), win_size=_SSIM_WINDOW, data_range=255
    )
    assert ssim(a, b) == pytest.approx(theirs, abs=1e-3)


# --- faithful(): every branch of the palette/structural gate ---


def _photo_like(seed=0, size=(64, 64)):
    """A smooth field plus mild, spatially-correlated noise: keeps its structure."""
    rng = np.random.default_rng(seed)
    coarse = rng.integers(0, 256, (4, 4), dtype=np.uint8)
    smooth = np.asarray(Image.fromarray(coarse).resize(size, Image.BICUBIC), dtype=np.int16)
    noisy = np.clip(smooth + rng.normal(0, 1.5, smooth.shape), 0, 255).astype(np.uint8)
    return Image.fromarray(noisy, "L")


def _noise_torture(seed=0, size=(128, 128)):
    """A smooth field plus strong independent per-pixel noise: JPEG cannot keep it."""
    rng = np.random.default_rng(seed)
    coarse = rng.integers(0, 256, (4, 4), dtype=np.uint8)
    smooth = np.asarray(Image.fromarray(coarse).resize(size, Image.BICUBIC), dtype=np.int16)
    noisy = np.clip(smooth + rng.normal(0, 15, smooth.shape), 0, 255).astype(np.uint8)
    return Image.fromarray(noisy, "L")


def _jpeg(pil, quality):
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=quality)
    return Image.open(io.BytesIO(buf.getvalue())).convert("L")


def test_faithful_rejects_too_low_psnr_before_any_structural_check():
    a, b = _pair("L", 40)  # 16.1 dB: well under either floor
    assert not faithful(a, b)
    assert not faithful(a, b, structural=True)
    assert not faithful(a, b, palette=True, structural=True)


def test_faithful_without_structural_only_checks_psnr():
    a = _photo_like()
    b = _jpeg(a, 75)
    assert psnr(a, b) >= MIN_PSNR
    assert faithful(a, b)  # structural defaults to off


def test_faithful_structural_passes_a_photo_that_keeps_its_structure():
    a = _photo_like()
    b = _jpeg(a, 90)
    assert psnr(a, b) >= MIN_PSNR
    assert ssim(a, b) >= MIN_SSIM
    assert faithful(a, b, structural=True)


def test_faithful_structural_rejects_a_photo_that_loses_its_structure():
    a = _noise_torture()
    b = _jpeg(a, 90)
    assert psnr(a, b) >= MIN_PSNR  # PSNR alone would accept this
    assert ssim(a, b) < MIN_SSIM
    assert not faithful(a, b, structural=True)


def test_faithful_structural_is_skipped_for_a_palette_image():
    # Same noise-torture pair the unqualified structural check rejects;
    # dither is deliberate noise with no structure to keep, so `palette`
    # keeps this to a PSNR-only check.
    a = _noise_torture()
    b = _jpeg(a, 90)
    assert psnr(a, b) >= MIN_PSNR_PALETTE
    assert ssim(a, b) < MIN_SSIM
    assert faithful(a, b, palette=True, structural=True)


def test_ssim_of_a_single_pixel_is_the_luminance_term():
    a, b = _pair("L", 5, size=(1, 1))
    expected = (2 * 100 * 105 + _C1) / (100**2 + 105**2 + _C1)
    assert ssim(a, b) == pytest.approx(expected)
