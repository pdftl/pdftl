import io
import struct
import zlib
import pytest
from unittest.mock import MagicMock, patch

import pikepdf
from PIL import Image

from pdftl.operations.helpers.image_processor import (
    ImageContext,
    ensure_thread_safe,
    get_orig_stream_size,
    run_parallel_image_job,
    encode_and_update_pdf_image,
    _handle_1bit_optimized_encode,
    _handle_dct_encode,
    _handle_jpx_encode,
    _handle_flate_fallback,
)


# --- 1. Thread Safety & Extraction Tests ---


def test_ensure_thread_safe_logic():
    # Case 1: Already loaded (fp is None)
    img1 = MagicMock()
    img1.fp = None
    ensure_thread_safe(img1)
    img1.load.assert_not_called()

    # Case 2: Backed by thread-safe pure Python memory buffer
    img2 = MagicMock()
    img2.fp = io.BytesIO()
    ensure_thread_safe(img2)
    img2.load.assert_not_called()

    # Case 3: Backed by an unsafe live file descriptor (or C++ proxy stream)
    img3 = MagicMock()
    img3.fp = "some_unsafe_file_pointer"
    ensure_thread_safe(img3)
    img3.load.assert_called_once()


def test_get_orig_stream_size():
    # Null or invalid handling
    assert get_orig_stream_size(None) == 999_999_999

    stream_mock = MagicMock()
    stream_mock.read_raw_bytes.return_value = b"12345"
    assert get_orig_stream_size(stream_mock) == 5

    # Exception handling (PdfError from Pikepdf binding)
    stream_mock.read_raw_bytes.side_effect = pikepdf.PdfError("Mock error")
    assert get_orig_stream_size(stream_mock) == 999_999_999

    # Exception handling (AttributeError)
    stream_mock.read_raw_bytes.side_effect = AttributeError("Missing method")
    assert get_orig_stream_size(stream_mock) == 999_999_999


# --- 2. Parallel Orchestrator Tests ---


def test_run_parallel_image_job_success():
    """Validates successful parallel processing and prepare skipping."""
    mock_images = [{"name": "img1"}, {"name": "img2"}]

    def mock_prepare(img_dict, seen_set):
        if img_dict["name"] == "img2":
            return None  # Simulate an extraction skip
        ctx = ImageContext(
            xobj=MagicMock(), smask_xobj=None, orig_size=100, img_dict=img_dict, page_num=1
        )
        return "payload", ctx

    def mock_worker(payload):
        return "result"

    def mock_commit(ctx, result, payload):
        return True

    resample_count = run_parallel_image_job(
        images=mock_images,
        threads=2,
        prepare_func=mock_prepare,
        worker_func=mock_worker,
        commit_func=mock_commit,
    )
    assert resample_count == 1


def test_run_parallel_image_job_worker_exception():
    """Validates that a thread crash appropriately bubbles up via future.result()."""

    def mock_prepare(img_dict, seen_set):
        return "payload", MagicMock()

    def mock_worker(payload):
        raise RuntimeError("Simulated thread crash")

    with pytest.raises(RuntimeError, match="Simulated thread crash"):
        run_parallel_image_job(
            images=[{"name": "img1"}],
            threads=2,
            prepare_func=mock_prepare,
            worker_func=mock_worker,
            commit_func=lambda c, r, p: True,
        )


@patch("os.cpu_count", return_value=8)
def test_run_parallel_image_job_default_threads(mock_cpu_count):
    """Validates that missing thread counts fallback to system defaults."""
    run_parallel_image_job(
        images=[],
        threads=0,
        prepare_func=lambda img, seen: None,
        worker_func=lambda p: p,
        commit_func=lambda c, r, p: True,
    )
    mock_cpu_count.assert_called_once()


# --- 3. Encode Routing Tests ---


@patch("pdftl.operations.helpers.image_processor._handle_1bit_optimized_encode")
@patch("pdftl.operations.helpers.image_processor._handle_dct_encode")
@patch("pdftl.operations.helpers.image_processor._handle_jpx_encode")
@patch("pdftl.operations.helpers.image_processor._handle_flate_fallback")
def test_encode_routing(mock_flate, mock_jpx, mock_dct, mock_ccitt):
    ctx = MagicMock()
    pil_img = MagicMock()
    pil_img.width = 100
    pil_img.height = 200

    # 1. Mode modifications map testing (RGB to DeviceRGB)
    pil_img.mode = "RGB"
    ctx.xobj.get.return_value = pikepdf.Name("/DCTDecode")
    encode_and_update_pdf_image(ctx, pil_img, 75)
    assert ctx.xobj.ColorSpace == pikepdf.Name("/DeviceRGB")
    assert ctx.xobj.BitsPerComponent == 8
    mock_dct.assert_called_once_with(ctx, pil_img, 75)

    # 2. Mode L mapping & Filter Array Parsing
    mock_dct.reset_mock()
    pil_img.mode = "L"
    ctx.xobj.get.return_value = pikepdf.Array([pikepdf.Name("/DCTDecode")])
    encode_and_update_pdf_image(ctx, pil_img, 75)
    assert ctx.xobj.ColorSpace == pikepdf.Name("/DeviceGray")
    mock_dct.assert_called_once_with(ctx, pil_img, 75)

    # 3. Route JPX (requires JPEG2000 in Image.SAVE)
    mock_jpx.reset_mock()
    ctx.xobj.get.return_value = pikepdf.Name("/JPXDecode")
    with patch.dict(Image.SAVE, {"JPEG2000": MagicMock()}):
        encode_and_update_pdf_image(ctx, pil_img, 75)
        mock_jpx.assert_called_once_with(ctx, pil_img)

    # 4. Route CCITT (requires mode "1")
    mock_ccitt.reset_mock()
    pil_img.mode = "1"
    ctx.xobj.get.return_value = pikepdf.Name("/CCITTFaxDecode")
    encode_and_update_pdf_image(ctx, pil_img, 75)
    mock_ccitt.assert_called_once_with(ctx, pil_img)

    # 5. Fallbacks (CCITT raises OSError -> Flate fallback)
    mock_flate.reset_mock()
    mock_ccitt.side_effect = OSError("Corrupted struct")
    encode_and_update_pdf_image(ctx, pil_img, 75)
    mock_flate.assert_called_once_with(ctx, pil_img)

    # 6. Fallbacks (CCITT raises struct.error -> Flate fallback)
    mock_flate.reset_mock()
    mock_ccitt.side_effect = struct.error("Bad pack")
    encode_and_update_pdf_image(ctx, pil_img, 75)
    mock_flate.assert_called_once_with(ctx, pil_img)

    # 7. Fallbacks (JPX raises OSError -> Flate fallback)
    mock_flate.reset_mock()
    pil_img.mode = "RGB"
    ctx.xobj.get.return_value = pikepdf.Name("/JPXDecode")
    mock_jpx.side_effect = OSError("Library missing")
    with patch.dict(Image.SAVE, {"JPEG2000": MagicMock()}):
        encode_and_update_pdf_image(ctx, pil_img, 75)
        mock_flate.assert_called_once_with(ctx, pil_img)

    # 8. Unhandled filters fallback to Flate
    mock_flate.reset_mock()
    ctx.xobj.get.return_value = pikepdf.Name("/LZWDecode")
    encode_and_update_pdf_image(ctx, pil_img, 75)
    mock_flate.assert_called_once_with(ctx, pil_img)


# --- 4. Deep Encoding Function Tests ---


def test_handle_1bit_optimized_encode():
    ctx = MagicMock()
    pil_img = MagicMock()
    pil_img.width = 100
    pil_img.height = 200
    pil_img.mode = "1"
    pil_img.getcolors.return_value = [(15000, 255), (5000, 0)]  # Mostly white background
    pil_img.tobytes.return_value = b"\x00" * 1000

    def mock_save(io_obj, **kwargs):
        io_obj.write(b"fake_tiff_data")

    pil_img.save.side_effect = mock_save

    # Patch both the extraction utility AND ImageOps.invert
    with (
        patch(
            "pdftl.utils.images.pil_to_pdf._extract_raw_ccitt_from_tiff",
        ) as mock_extract,
        patch("PIL.ImageOps.invert", return_value=pil_img) as mock_invert,
    ):
        mock_extract.return_value = b"raw_ccitt"

        _handle_1bit_optimized_encode(ctx, pil_img)

        # Assertions
        mock_invert.assert_called_once_with(pil_img)
        mock_extract.assert_called_once_with(b"fake_tiff_data")
        ctx.xobj.write.assert_called_once_with(
            b"raw_ccitt", filter=pikepdf.Name("/CCITTFaxDecode")
        )
        assert "/Columns" in ctx.xobj.DecodeParms


def test_handle_dct_encode():
    ctx = MagicMock()
    ctx.xobj = MockXObjDict()
    ctx.xobj["/DecodeParms"] = "to_be_deleted"

    pil_img = MagicMock()

    def mock_save(io_obj, **kwargs):
        io_obj.write(b"jpeg_data")

    pil_img.save.side_effect = mock_save

    _handle_dct_encode(ctx, pil_img, 85)

    assert ctx.xobj.data == b"jpeg_data"
    assert ctx.xobj.filter == pikepdf.Name("/DCTDecode")
    assert "/DecodeParms" not in ctx.xobj


def test_handle_jpx_encode():
    ctx = MagicMock()
    ctx.xobj = MockXObjDict()
    ctx.xobj["/DecodeParms"] = "to_be_deleted"

    pil_img = MagicMock()

    def mock_save(io_obj, **kwargs):
        io_obj.write(b"jpx_data")

    pil_img.save.side_effect = mock_save

    _handle_jpx_encode(ctx, pil_img)

    assert ctx.xobj.data == b"jpx_data"
    assert ctx.xobj.filter == pikepdf.Name("/JPXDecode")
    assert "/DecodeParms" not in ctx.xobj


def test_handle_flate_fallback():
    ctx = MagicMock()
    ctx.xobj = MockXObjDict()
    ctx.xobj["/DecodeParms"] = "to_be_deleted"

    pil_img = MagicMock()
    pil_img.tobytes.return_value = b"raw_pixels"

    _handle_flate_fallback(ctx, pil_img)

    expected_data = zlib.compress(b"raw_pixels", level=9)
    assert ctx.xobj.data == expected_data
    assert ctx.xobj.filter == pikepdf.Name("/FlateDecode")
    assert "/DecodeParms" not in ctx.xobj


from unittest.mock import patch


# noqa py:missing-equals
class MockXObjDict(dict):
    """Dict that also records .write() calls and supports DecodeParms assignment,
    mirroring the pikepdf Stream object's dual dict-like/attribute-like interface
    used elsewhere in this test suite.
    """

    def write(self, data, filter=None):
        self.data = data
        self.filter = filter

    def __setattr__(self, name, value):
        if name == "DecodeParms":
            self["/DecodeParms"] = value
        else:
            object.__setattr__(self, name, value)

    def __getattr__(self, name):
        if name == "DecodeParms":
            return self.get("/DecodeParms")
        raise AttributeError(name)


def _make_ctx_and_img(ccitt_payload, raw_bytes):
    """Builds a MagicMock ctx/pil_img pair such that:
    - raw_size = len(raw_bytes)
    - ccitt_size = len(ccitt_payload) (forced via patched _extract_raw_ccitt_from_tiff)
    - ccitt_size >= raw_size * 0.25, so we fall into the `else` (Flate-vs-CCITT) branch.
    """
    ctx = MagicMock()
    ctx.xobj = MockXObjDict()

    pil_img = MagicMock()
    pil_img.width = 10
    pil_img.height = 10
    pil_img.mode = "1"
    pil_img.getcolors.return_value = [(80, 255), (20, 0)]  # dominant white -> invert=True
    pil_img.tobytes.return_value = raw_bytes

    def mock_save(io_obj, **kwargs):
        io_obj.write(b"fake_tiff_data")

    pil_img.save.side_effect = mock_save

    return ctx, pil_img, ccitt_payload


def test_handle_1bit_encode_flate_wins_suspicious_ratio():
    """Covers lines 212-221: ccitt_size >= 25% of raw_size triggers the Flate
    comparison branch, and Flate actually compresses smaller than CCITT, so
    /FlateDecode is selected as best_filter.
    """
    # Raw pixel bytes that compress very well under zlib (highly repetitive).
    raw_bytes = b"\x00" * 2000
    flate_size_expected = len(zlib.compress(raw_bytes, level=9))

    # Force ccitt_size to satisfy two constraints simultaneously:
    #   1) ccitt_size >= raw_size * 0.25  (so we enter the "suspicious ratio" else-branch)
    #   2) ccitt_size > flate_size_expected  (so Flate wins the subsequent comparison)
    raw_size = len(raw_bytes)
    ccitt_size = max(int(raw_size * 0.25) + 1, flate_size_expected + 50)
    assert ccitt_size >= raw_size * 0.25
    assert ccitt_size > flate_size_expected
    ccitt_payload = b"\xff" * ccitt_size

    ctx, pil_img, _ = _make_ctx_and_img(ccitt_payload, raw_bytes)
    # Pre-existing DecodeParms from a prior CCITT encode, to verify cleanup at 236-237.
    ctx.xobj["/DecodeParms"] = "stale_ccitt_params"

    with (
        patch(
            "pdftl.utils.images.pil_to_pdf._extract_raw_ccitt_from_tiff",
            return_value=ccitt_payload,
        ),
        patch("PIL.ImageOps.invert", return_value=pil_img),
    ):
        _handle_1bit_optimized_encode(ctx, pil_img)

    # Flate must have won and been written with /FlateDecode
    assert str(ctx.xobj.filter) == "/FlateDecode"
    assert ctx.xobj.data == zlib.compress(raw_bytes, level=9)

    # DecodeParms cleanup (lines 236-237) must have removed the stale entry
    assert "/DecodeParms" not in ctx.xobj


def test_handle_1bit_encode_ccitt_barely_wins_suspicious_ratio():
    """Covers the 'CCITT barely wins' sub-path within the same suspicious-ratio
    branch (lines 212-225): ccitt_size >= 25% of raw_size, but CCITT is still
    smaller than the Flate-compressed alternative.
    """
    # Raw pixel bytes that do NOT compress well under zlib: random-ish, non-repetitive
    # content defeats DEFLATE's LZ77 matching, keeping the compressed size close to raw_size.
    import random

    rng = random.Random(42)
    raw_bytes = bytes(rng.getrandbits(8) for _ in range(2000))
    flate_size_expected = len(zlib.compress(raw_bytes, level=9))
    raw_size = len(raw_bytes)

    # ccitt_size must be: >= 25% of raw_size (to enter the suspicious/else branch)
    # AND smaller than flate_size_expected (so CCITT wins the comparison at line 215).
    ccitt_size = int(raw_size * 0.25) + 1
    assert ccitt_size < flate_size_expected, (
        f"test setup invariant violated: ccitt_size={ccitt_size} "
        f"flate_size_expected={flate_size_expected}"
    )
    ccitt_payload = b"\xab" * ccitt_size

    ctx, pil_img, _ = _make_ctx_and_img(ccitt_payload, raw_bytes)
    ctx.xobj["/DecodeParms"] = "stale_ccitt_params"

    with (
        patch(
            "pdftl.utils.images.pil_to_pdf._extract_raw_ccitt_from_tiff",
            return_value=ccitt_payload,
        ),
        patch("PIL.ImageOps.invert", return_value=pil_img),
    ):
        _handle_1bit_optimized_encode(ctx, pil_img)

    assert str(ctx.xobj.filter) == "/CCITTFaxDecode"
    assert ctx.xobj.data == ccitt_payload

    # CCITT won, so DecodeParms should be (re)written with CCITT params, not deleted.
    assert ctx.xobj.DecodeParms is not None
    assert "/Columns" in ctx.xobj.DecodeParms


def test_p_mode_image_writes_indexed_colorspace():
    """
    Ensures that when a 'P' (palette) mode image is encoded, the PDF
    XObject dictionary is correctly updated with an /Indexed ColorSpace array
    and an embedded binary lookup table.
    """
    # 1. Spin up an empty PDF and a blank XObject stream in memory
    pdf = pikepdf.Pdf.new()
    xobj_stream = pdf.make_stream(b"dummy_data_to_be_overwritten")
    xobj_stream.Type = pikepdf.Name("/XObject")
    xobj_stream.Subtype = pikepdf.Name("/Image")

    # 2. Setup the ImageContext exactly as the orchestrator would
    ctx = ImageContext(xobj=xobj_stream, smask_xobj=None, orig_size=1024, img_dict={}, page_num=1)

    # 3. Create a deterministic "P" mode PIL image
    img = Image.new("P", (10, 10))

    # Create a predictable palette: Red, Green, Blue, and pad the rest with black
    # 3 colors * 3 bytes = 9 bytes. Total palette must be 768 bytes (256 * 3)
    test_palette = [255, 0, 0, 0, 255, 0, 0, 0, 255] + [0] * (253 * 3)
    img.putpalette(test_palette)

    # 4. Route it through your processor
    encode_and_update_pdf_image(ctx, img, quality=90)

    # 5. Assert the PDF dictionary structurally matches the PDF specification
    assert "/ColorSpace" in ctx.xobj, "ColorSpace was not written to the XObject."
    cs = ctx.xobj.ColorSpace

    # Must be a 4-element Array
    assert isinstance(cs, pikepdf.Array), "ColorSpace must be an Array for Indexed images."
    assert len(cs) == 4, "Indexed ColorSpace Array must have exactly 4 elements."

    # Validate Array elements
    assert cs[0] == pikepdf.Name("/Indexed")
    assert cs[1] == pikepdf.Name("/DeviceRGB")
    assert cs[2] == 255  # Highest index for a 256-color palette

    # Validate the raw bytes of the palette made it in intact
    # pikepdf exposes PDF strings/hex as bytes when cast
    assert bytes(cs[3]) == bytes(test_palette), "Embedded palette bytes do not match original."

    # Verify structural integrity
    assert ctx.xobj.BitsPerComponent == 8
    assert ctx.xobj.Width == 10
    assert ctx.xobj.Height == 10
    assert ctx.xobj.Filter == pikepdf.Name("/FlateDecode")
