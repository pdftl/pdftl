# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/utils/images/test_embedder.py

from __future__ import annotations

import zlib
from unittest.mock import MagicMock, patch

import pytest
import pikepdf
from PIL import Image

from pdftl.utils.images.embedder import create_image_xobject


@pytest.fixture
def empty_pdf():
    """Provides a fresh in-memory PDF document for creating streams."""
    with pikepdf.Pdf.new() as pdf:
        yield pdf


def test_embed_jpeg(tmp_path, empty_pdf):
    """Verifies that non-alpha JPEGs follow the zero-re-encoding raw fast path."""
    img_path = tmp_path / "test.jpg"
    img = Image.new("RGB", (20, 15), color="red")
    img.save(img_path, format="JPEG")

    xobj = create_image_xobject(empty_pdf, img_path)

    assert xobj.Type == pikepdf.Name("/XObject")
    assert xobj.Subtype == pikepdf.Name("/Image")
    assert xobj.Width == 20
    assert xobj.Height == 15
    assert xobj.Filter == pikepdf.Name("/DCTDecode")
    assert xobj.ColorSpace == pikepdf.Name("/DeviceRGB")
    assert xobj.BitsPerComponent == 8
    assert "/SMask" not in xobj


def test_embed_jpeg2000(tmp_path, empty_pdf):
    """Verifies that JPEG2000 formats follow their corresponding direct fast path."""
    img_path = tmp_path / "test.jp2"

    # Since writing JPEG2000 natively in Pillow can sometimes fail if openjpeg
    # is missing in the testing environment, we mock PIL's format response.
    img = Image.new("RGB", (10, 10), color="blue")
    img.save(img_path, format="PNG")  # Write as PNG temporarily

    with patch("PIL.Image.open") as mock_open:
        mock_img = MagicMock()
        mock_img.format = "JPEG2000"
        mock_img.mode = "RGB"
        mock_img.width = 10
        mock_img.height = 10
        mock_open.return_value = mock_img

        xobj = create_image_xobject(empty_pdf, img_path)

        assert xobj.Filter == pikepdf.Name("/JPXDecode")
        assert xobj.Width == 10
        assert xobj.Height == 10


def test_embed_rgba_transparency(tmp_path, empty_pdf):
    """Verifies that RGBA images split the alpha channel into a secondary Soft Mask stream."""
    img_path = tmp_path / "transparent.png"
    # Create an RGBA image with a transparent gradient
    img = Image.new("RGBA", (12, 12), color=(255, 0, 0, 128))
    img.save(img_path, format="PNG")

    xobj = create_image_xobject(empty_pdf, img_path)

    # Check Base Image characteristics
    assert xobj.Filter == pikepdf.Name("/FlateDecode")
    assert xobj.ColorSpace == pikepdf.Name("/DeviceRGB")
    assert xobj.BitsPerComponent == 8
    assert "/SMask" in xobj

    # Check Soft Mask characteristics
    smask = xobj.SMask
    assert smask.Type == pikepdf.Name("/XObject")
    assert smask.Subtype == pikepdf.Name("/Image")
    assert smask.Width == 12
    assert smask.Height == 12
    assert smask.ColorSpace == pikepdf.Name("/DeviceGray")
    assert smask.BitsPerComponent == 8
    assert smask.Filter == pikepdf.Name("/FlateDecode")

    # Decompress the alpha channel and check that it matches our expected 128 alpha value
    raw_alpha_bytes = zlib.decompress(smask.read_raw_bytes())
    assert len(raw_alpha_bytes) == 12 * 12
    assert all(b == 128 for b in raw_alpha_bytes)


def test_embed_paletted_transparency(tmp_path, empty_pdf):
    """Verifies that palette (P) mode images with transparency info are extracted into a Soft Mask."""
    img_path = tmp_path / "paletted_trans.png"
    img = Image.new("P", (8, 8))
    # Fill palette and set color index 0 as transparent (0 opacity)
    img.info["transparency"] = 0
    img.save(img_path, format="PNG")

    xobj = create_image_xobject(empty_pdf, img_path)

    assert "/SMask" in xobj
    assert xobj.SMask.ColorSpace == pikepdf.Name("/DeviceGray")


def test_embed_1bit_monochrome(tmp_path, empty_pdf):
    """Verifies that 1-bit monochrome images route through the optimal CCITT/Flate logic."""
    img_path = tmp_path / "mono.png"
    # Large 1-bit block of black should compress wonderfully with CCITT Group 4
    img = Image.new("1", (128, 128), color=0)
    img.save(img_path, format="PNG")

    xobj = create_image_xobject(empty_pdf, img_path)

    assert xobj.Type == pikepdf.Name("/XObject")
    assert xobj.Subtype == pikepdf.Name("/Image")
    assert xobj.Width == 128
    assert xobj.Height == 128
    assert xobj.BitsPerComponent == 1
    assert xobj.ColorSpace == pikepdf.Name("/DeviceGray")
    assert xobj.Filter == pikepdf.Name("/CCITTFaxDecode")
    assert isinstance(xobj.DecodeParms, pikepdf.Dictionary)


def test_embed_rgb_fallback(tmp_path, empty_pdf):
    """Verifies standard RGB PNGs correctly trigger the lossless Flate fallback path."""
    img_path = tmp_path / "rgb_fallback.png"
    img = Image.new("RGB", (16, 16), color="green")
    img.save(img_path, format="PNG")

    xobj = create_image_xobject(empty_pdf, img_path)

    assert xobj.Type == pikepdf.Name("/XObject")
    assert xobj.Subtype == pikepdf.Name("/Image")
    assert xobj.Filter == pikepdf.Name("/FlateDecode")
    assert xobj.ColorSpace == pikepdf.Name("/DeviceRGB")
    assert xobj.BitsPerComponent == 8
    assert "/SMask" not in xobj
