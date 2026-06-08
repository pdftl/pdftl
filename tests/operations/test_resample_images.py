# tests/operations/test_resample_image.py

import io
import zlib
from unittest.mock import MagicMock, patch

import pikepdf
import pytest
from PIL import Image

from pdftl.core.core_types import OpResult
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.resample_images import (
    _apply_metadata_updates,
    _get_orig_stream_size,
    _get_resample_dims,
    _get_resized_pil_image,
    _parse_args,
    _resample_single_image,
    _resize_soft_mask,
    _validate_dpi,
    _validate_quality,
    resample_images,
)

# --- Argument Validation Tests ---


def test_validate_dpi():
    assert _validate_dpi("150") == 150
    with pytest.raises(InvalidArgumentError):
        _validate_dpi("0")
    with pytest.raises(InvalidArgumentError):
        _validate_dpi("abc")


def test_validate_quality():
    assert _validate_quality("75") == 75
    with pytest.raises(InvalidArgumentError):
        _validate_quality("0")
    with pytest.raises(InvalidArgumentError):
        _validate_quality("101")


def test_parse_args():
    # Defaults
    dpi, q, upscale, grow, force, specs = _parse_args([])
    assert (
        dpi == 150
        and q == 75
        and upscale is False
        and grow is False
        and force is False
        and specs == []
    )

    # Custom args (all explicit)
    dpi, q, upscale, grow, force, specs = _parse_args(
        ["dpi=72", "quality=50", "allow_upscale=1", "allow_growth=0", "force=yes", "1-5"]
    )
    assert (
        dpi == 72
        and q == 50
        and upscale is True
        and grow is False
        and force is True
        and specs == ["1-5"]
    )

    # Test the fallback logic: allow_growth should inherit True if allow_upscale is True
    dpi, q, upscale, grow, force, specs = _parse_args(["allow_upscale=yes"])
    assert upscale is True and grow is True


# --- Geometry & Math Tests ---


def test_get_resample_dims():
    img = {
        "width_px": 300,
        "height_px": 300,
        "bbox": [0, 0, 72, 72],  # 1x1 inch box
    }

    # Standard shrink: Target 150 DPI for a 1x1 box -> 150x150 pixels
    assert _get_resample_dims(img, dpi=150, allow_upscale=False) == (150, 150)

    # Micro-pixel Guard (Tolerance): Image is already exactly at target resolution
    img["width_px"] = 150
    img["height_px"] = 150
    assert _get_resample_dims(img, dpi=150, allow_upscale=False) is None

    # Upscale Guard: Target is 300 DPI (300x300px), but image is 150x150px
    # If allow_upscale is False, it should abort and return None
    assert _get_resample_dims(img, dpi=300, allow_upscale=False) is None

    # If allow_upscale is True, it should return the new upscaled dimensions
    assert _get_resample_dims(img, dpi=300, allow_upscale=True) == (300, 300)

    # Zero dimension -> skip
    img["bbox"] = [0, 0, 0, 72]
    assert _get_resample_dims(img, dpi=150, allow_upscale=False) is None


# --- Real Pikepdf Object Fixtures ---


@pytest.fixture
def real_pdf():
    """Provides a fresh, real PDF object in memory."""
    with pikepdf.Pdf.new() as pdf:
        yield pdf


def make_real_image_dict(
    pdf,
    name="/Im1",
    width=400,
    height=400,
    mode="RGB",
    format="flatedecode",
    bbox=(0, 0, 96, 96),  # 1.33x1.33 inches
    is_bitonal=False,
    add_smask=False,
    exotic_cs=False,
):
    """Generates a fully valid, real pikepdf.Stream containing an image."""
    pil_mode = "1" if is_bitonal else "RGB"
    img = Image.new(pil_mode, (width, height), color=0)

    # Prepare valid encoded data payload for JPEG/Flate so pikepdf decodes cleanly
    if format == "dctdecode":
        out_buf = io.BytesIO()
        img.save(out_buf, format="JPEG")
        stream_data = out_buf.getvalue()
    elif format == "flatedecode":
        stream_data = zlib.compress(img.tobytes())
    else:
        stream_data = img.tobytes()

    stream = pdf.make_stream(stream_data)
    stream.Type = pikepdf.Name("/XObject")
    stream.Subtype = pikepdf.Name("/Image")
    stream.Width = width
    stream.Height = height
    stream.BitsPerComponent = 1 if is_bitonal else 8

    if format == "flatedecode":
        stream.Filter = pikepdf.Name("/FlateDecode")
    elif format == "dctdecode":
        stream.Filter = pikepdf.Name("/DCTDecode")

    if exotic_cs:
        # Create a real array-based indexed colorspace so pikepdf parses it into PIL mode 'P'
        stream.ColorSpace = pikepdf.Array(
            [pikepdf.Name("/Indexed"), pikepdf.Name("/DeviceRGB"), 255, b"\x00" * 768]
        )
    elif pil_mode == "RGB":
        stream.ColorSpace = pikepdf.Name("/DeviceRGB")
    elif pil_mode == "1":
        stream.ColorSpace = pikepdf.Name("/DeviceGray")
        stream.ImageMask = True

    if add_smask:
        smask_bytes = zlib.compress(b"\xff" * (width * height))
        smask_stream = pdf.make_stream(smask_bytes)
        smask_stream.Type = pikepdf.Name("/XObject")
        smask_stream.Subtype = pikepdf.Name("/Image")
        smask_stream.Width = width
        smask_stream.Height = height
        smask_stream.BitsPerComponent = 8
        smask_stream.ColorSpace = pikepdf.Name("/DeviceGray")
        smask_stream.Filter = pikepdf.Name("/FlateDecode")
        stream.SMask = smask_stream

    return {
        "name": name,
        "xobj": stream,
        "width_px": width,
        "height_px": height,
        "bbox": bbox,
        "format": format,
        "page": 1,
    }


# --- Pipeline Tests using Real Streams ---


def test_resample_standard_image(real_pdf):
    img = make_real_image_dict(real_pdf)
    seen = set()
    result = _resample_single_image(img, 150, 75, False, False, seen)
    assert result in (True, False)
    assert img["xobj"].objgen in seen


def test_resample_jpeg_image(real_pdf):
    img = make_real_image_dict(real_pdf, format="dctdecode")
    seen = set()
    _resample_single_image(img, 150, 75, False, False, seen)


def test_resample_bitonal_image(real_pdf):
    img = make_real_image_dict(real_pdf, is_bitonal=True)
    seen = set()
    _resample_single_image(img, 150, 75, False, False, seen)


def test_resample_smask_image(real_pdf):
    img = make_real_image_dict(real_pdf, add_smask=True)
    seen = set()
    _resample_single_image(img, 150, 75, False, False, seen)


def test_resample_exotic_colorspace(real_pdf):
    # 1. Without force (should safely abort & skip)
    img1 = make_real_image_dict(real_pdf, exotic_cs=True)
    seen1 = set()
    assert _resample_single_image(img1, 150, 75, False, False, seen1, force=False) is False

    # 2. With force
    img2 = make_real_image_dict(real_pdf, exotic_cs=True)
    img2["xobj"]["/DecodeParms"] = pikepdf.Dictionary()
    seen2 = set()
    assert _resample_single_image(img2, 150, 75, False, False, seen2, force=True) is True


def test_resample_skip_conditions(real_pdf):
    img = make_real_image_dict(real_pdf)
    seen = set()

    # Skip if already seen
    seen.add(img["xobj"].objgen)
    assert _resample_single_image(img, 150, 75, False, False, seen) is False

    # Skip if upscaling is required but `allow_upscale` is False
    # Target DPI 600 means it needs 800px width. Image only has 400px.
    seen.clear()
    assert _resample_single_image(img, 600, 75, False, False, seen) is False


@patch("pdftl.operations.resample_images._get_orig_stream_size")
def test_resample_growth_guard(mock_orig_size, real_pdf):
    img = make_real_image_dict(real_pdf)
    seen = set()

    # Force the original size to be 1 byte to trigger the growth guard branch
    mock_orig_size.return_value = 1
    assert _resample_single_image(img, 150, 75, False, False, seen) is False


def test_exception_handling(real_pdf):
    assert _get_orig_stream_size(None) == 999_999_999

    # Test main pipeline execution crash safety by deleting required structural keys
    img = make_real_image_dict(real_pdf)
    del img["xobj"].Width
    assert _resample_single_image(img, 150, 75, False, False, set()) is False


# --- Targeted Unit Coverage ---


@patch("pikepdf.models.PdfImage")
def test_get_resized_pil_image_force_convert_fallback(mock_pdf_image):
    mock_instance = MagicMock()
    mock_instance.as_pil_image.return_value = Image.new("RGBA", (100, 100))
    mock_pdf_image.return_value = mock_instance

    fake_xobj = MagicMock()
    res = _get_resized_pil_image(fake_xobj, is_bitonal=False, width=50, height=50, force=True)
    assert res is not None
    assert res.mode == "RGB"


@patch("pikepdf.models.PdfImage")
def test_resize_soft_mask_exception(mock_pdf_image, real_pdf):
    mock_pdf_image.side_effect = ValueError("Simulated soft mask internal payload error")
    smask_stream = real_pdf.make_stream(b"malformed_payload")
    xobj = real_pdf.make_stream(b"")
    xobj["/SMask"] = smask_stream

    s_obj, s_bytes = _resize_soft_mask(xobj, 50, 50, 1)
    assert s_obj is None
    assert s_bytes is None


def test_apply_metadata_updates_modes(real_pdf):
    # Covers line 275-276 (del xobj["/DecodeParms"] explicitly)
    stream_with_params = real_pdf.make_stream(b"")
    stream_with_params["/DecodeParms"] = pikepdf.Dictionary()
    _apply_metadata_updates(stream_with_params, "RGB", is_bitonal=False, force=False)
    assert "/DecodeParms" not in stream_with_params

    # Covers line 277-278 (is_bitonal condition block explicitly)
    stream_bitonal = real_pdf.make_stream(b"")
    _apply_metadata_updates(stream_bitonal, "1", is_bitonal=True, force=False)
    assert stream_bitonal.BitsPerComponent == 1

    # Covers lines 279-281 (ColorSpace update for mode 'RGB')
    stream_rgb = real_pdf.make_stream(b"")
    _apply_metadata_updates(stream_rgb, "RGB", is_bitonal=False, force=True)
    assert stream_rgb.ColorSpace == pikepdf.Name("/DeviceRGB")

    # Covers lines 282-283 (ColorSpace update for mode 'L')
    stream_l = real_pdf.make_stream(b"")
    _apply_metadata_updates(stream_l, "L", is_bitonal=False, force=True)
    assert stream_l.ColorSpace == pikepdf.Name("/DeviceGray")

    # Covers lines 284-285 (ColorSpace update for mode 'CMYK')
    stream_cmyk = real_pdf.make_stream(b"")
    _apply_metadata_updates(stream_cmyk, "CMYK", is_bitonal=False, force=True)
    assert stream_cmyk.ColorSpace == pikepdf.Name("/DeviceCMYK")


# --- Main Wrapper Integration ---


@patch("pdftl.operations.resample_images.extract_pdf_images")
def test_resample_images_main(mock_extract, real_pdf):
    real_pdf.add_blank_page(page_size=(612, 792))

    mock_extract.return_value = [
        make_real_image_dict(
            real_pdf, name="/Im1", width=400, height=400
        ),  # High resolution: Downsamples
        make_real_image_dict(real_pdf, name="/Im2", width=50, height=50),  # Low resolution: Skips
    ]

    res = resample_images(real_pdf, ["dpi=150", "allow_growth=yes"])
    assert isinstance(res, OpResult)
    assert mock_extract.called
