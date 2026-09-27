# tests/utils/images/test_similarity.py
#
# Ground truth: images differing by a known constant, whose PSNR is
# 10 log10(255^2 / d^2) by hand.

import math

import pytest
from PIL import Image

from pdftl.utils.images.similarity import MIN_PSNR, MIN_PSNR_PALETTE, faithful, psnr


def _pair(mode, offset):
    a = Image.new(mode, (8, 8), (100,) * (1 if mode == "L" else 3))
    b = Image.new(mode, (8, 8), (100 + offset,) * (1 if mode == "L" else 3))
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
