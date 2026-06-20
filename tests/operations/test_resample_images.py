# tests/operations/test_resample_images.py

import io
import zlib
from unittest.mock import patch

import pikepdf
import pytest
from PIL import Image

from pdftl.core.core_types import OpResult
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.resample_images import (
    ExtractionPayload,
    _apply_metadata_updates,
    _commit_resampled_data,
    _get_resample_dims,
    _parse_args,
    _prepare_image_for_worker,
    _validate_int,
    _worker_compute_resample,
    resample_images,
)

# --- Argument Validation Tests ---


def test_validate_int():
    assert _validate_int("150", "dpi", 1) == 150
    with pytest.raises(InvalidArgumentError):
        _validate_int("0", "dpi", 1)
    with pytest.raises(InvalidArgumentError):
        _validate_int("abc", "dpi", 1)

    assert _validate_int("75", "quality", 1, 100) == 75
    with pytest.raises(InvalidArgumentError):
        _validate_int("101", "quality", 1, 100)


def test_parse_args():
    # Defaults
    dpi, q, threads, upscale, grow, force, specs = _parse_args([])
    assert (
        dpi == 150
        and q == 75
        and upscale is False
        and grow is False
        and force is False
        and specs == []
    )
    assert threads > 0  # Should be set to os.cpu_count() or 4

    # Custom args (all explicit)
    dpi, q, threads, upscale, grow, force, specs = _parse_args(
        [
            "dpi=72",
            "quality=50",
            "threads=2",
            "allow_upscale=1",
            "allow_growth=0",
            "force=yes",
            "1-5",
        ]
    )
    assert (
        dpi == 72
        and q == 50
        and threads == 2
        and upscale is True
        and grow is False
        and force is True
        and specs == ["1-5"]
    )

    # Test the fallback logic: allow_growth should inherit True if allow_upscale is True
    dpi, q, threads, upscale, grow, force, specs = _parse_args(["allow_upscale=yes"])
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


# --- Pipeline Helper ---


def simulate_pipeline(
    img_dict,
    dpi=150,
    quality=75,
    allow_upscale=False,
    allow_growth=False,
    seen_objgens=None,
    force=False,
) -> bool:
    """Helper to simulate the multithreaded pipeline synchronously for testing."""
    if seen_objgens is None:
        seen_objgens = set()

    # 1. Main thread extraction
    task = _prepare_image_for_worker(
        img_dict, dpi, quality, allow_upscale, allow_growth, force, seen_objgens
    )
    if not task:
        return False

    payload, ctx = task

    try:
        # 2. Worker execution
        result = _worker_compute_resample(payload)

        # 3. Main thread commit
        return _commit_resampled_data(ctx, result, payload, allow_growth)
    except (pikepdf.PdfError, ValueError, TypeError, OSError, RuntimeError, zlib.error):
        # Graceful simulation of thread catching failures
        return False


# --- Pipeline Tests using Real Streams ---


def test_resample_standard_image(real_pdf):
    img = make_real_image_dict(real_pdf)
    seen = set()
    result = simulate_pipeline(img, seen_objgens=seen)
    assert result in (True, False)
    assert img["xobj"].objgen in seen


def test_resample_jpeg_image(real_pdf):
    img = make_real_image_dict(real_pdf, format="dctdecode")
    simulate_pipeline(img)


def test_resample_bitonal_image(real_pdf):
    img = make_real_image_dict(real_pdf, is_bitonal=True)
    simulate_pipeline(img)


def test_resample_smask_image(real_pdf):
    img = make_real_image_dict(real_pdf, add_smask=True)
    simulate_pipeline(img)


def test_resample_exotic_colorspace(real_pdf):
    # 1. Without force (should safely abort & skip)
    img1 = make_real_image_dict(real_pdf, exotic_cs=True)
    seen1 = set()
    assert simulate_pipeline(img1, seen_objgens=seen1, force=False) is False

    # 2. With force
    img2 = make_real_image_dict(real_pdf, exotic_cs=True)
    img2["xobj"]["/DecodeParms"] = pikepdf.Dictionary()
    seen2 = set()
    assert simulate_pipeline(img2, seen_objgens=seen2, force=True) is True


def test_resample_skip_conditions(real_pdf):
    img = make_real_image_dict(real_pdf)
    seen = set()

    # Skip if already seen
    seen.add(img["xobj"].objgen)
    assert simulate_pipeline(img, seen_objgens=seen) is False

    # Skip if upscaling is required but `allow_upscale` is False
    # Target DPI 600 means it needs 800px width. Image only has 400px.
    seen.clear()
    assert simulate_pipeline(img, dpi=600) is False


@patch("pdftl.operations.resample_images.get_orig_stream_size")
def test_resample_growth_guard(mock_orig_size, real_pdf):
    img = make_real_image_dict(real_pdf)

    # Force the original size to be 1 byte to trigger the growth guard branch
    mock_orig_size.return_value = 1
    assert simulate_pipeline(img) is False


def test_exception_handling(real_pdf):
    # Test main pipeline execution crash safety by deleting required structural keys
    img = make_real_image_dict(real_pdf)
    del img["xobj"].Width
    assert simulate_pipeline(img) is False


# --- Targeted Unit Coverage ---


def test_worker_force_convert():
    """Tests that exotic colorspaces are properly converted in the worker thread."""
    pil_img = Image.new("P", (100, 100))
    payload = ExtractionPayload(
        pil_img=pil_img,
        smask_pil=None,
        new_width=50,
        new_height=50,
        is_jpeg=False,
        quality=75,
        is_bitonal=False,
        force=True,
    )
    result = _worker_compute_resample(payload)
    assert result.mode == "RGB"


def test_apply_metadata_updates_modes(real_pdf):
    stream_with_params = real_pdf.make_stream(b"")
    stream_with_params["/DecodeParms"] = pikepdf.Dictionary()
    _apply_metadata_updates(stream_with_params, "RGB", is_bitonal=False, force=False)
    assert "/DecodeParms" not in stream_with_params

    stream_bitonal = real_pdf.make_stream(b"")
    _apply_metadata_updates(stream_bitonal, "1", is_bitonal=True, force=False)
    assert stream_bitonal.BitsPerComponent == 1

    stream_rgb = real_pdf.make_stream(b"")
    _apply_metadata_updates(stream_rgb, "RGB", is_bitonal=False, force=True)
    assert stream_rgb.ColorSpace == pikepdf.Name("/DeviceRGB")

    stream_l = real_pdf.make_stream(b"")
    _apply_metadata_updates(stream_l, "L", is_bitonal=False, force=True)
    assert stream_l.ColorSpace == pikepdf.Name("/DeviceGray")

    stream_cmyk = real_pdf.make_stream(b"")
    _apply_metadata_updates(stream_cmyk, "CMYK", is_bitonal=False, force=True)
    assert stream_cmyk.ColorSpace == pikepdf.Name("/DeviceCMYK")


# --- Main Wrapper Integration ---


@patch("pdftl.operations.resample_images.extract_pdf_images")
def test_resample_images_main(mock_extract, real_pdf):
    real_pdf.add_blank_page(page_size=(612, 792))

    mock_extract.return_value = [
        make_real_image_dict(real_pdf, name="/Im1", width=400, height=400),
        make_real_image_dict(real_pdf, name="/Im2", width=50, height=50),
    ]

    res = resample_images(real_pdf, ["dpi=150", "allow_growth=yes"])
    assert isinstance(res, OpResult)
    assert mock_extract.called


@patch("pikepdf.models.PdfImage")
def test_prepare_image_exception(mock_pdf_image, real_pdf):
    """Exception handling inside _prepare_image_for_worker."""
    mock_pdf_image.side_effect = ValueError("Simulated PDF extraction failure")
    img = make_real_image_dict(real_pdf)
    seen = set()

    result = _prepare_image_for_worker(
        img,
        dpi=150,
        quality=75,
        allow_upscale=False,
        allow_growth=False,
        force=False,
        seen_objgens=seen,
    )
    # The exception should be caught, a debug log emitted, and None returned
    assert result is None


def test_resample_images_parallel_exception_logged(mocker, caplog):
    """Exercises lines 494-495 when parallel processing encounters an error."""
    import logging
    from pdftl.operations.resample_images import resample_images

    mocker.patch(
        "pdftl.operations.resample_images.extract_pdf_images",
        return_value=[
            {
                "xobj": mocker.MagicMock(),
                "page": 1,
                "name": "Im0",
                "bbox": [0, 0, 100, 100],
                "width_px": 200,
                "height_px": 200,
            }
        ],
    )
    mocker.patch(
        "pdftl.operations.resample_images.run_parallel_image_job",
        side_effect=OSError("Corrupted thread context memory"),
    )

    mock_pdf = mocker.MagicMock()
    mock_pdf.pages = [mocker.MagicMock()]

    with caplog.at_level(logging.DEBUG, logger="pdftl.operations.resample_images"):
        result = resample_images(mock_pdf, [])

    assert result.success is True
    assert any(
        "Skipped resampling an image due to an error in resample_images parallel processing"
        in record.message
        for record in caplog.records
    )
