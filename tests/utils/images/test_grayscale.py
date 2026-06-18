# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/utils/images/test_grayscale.py

import pytest
from types import SimpleNamespace
import pikepdf
from PIL import Image

import pdftl.utils.images.grayscale as mod
from pdftl.operations.helpers.image_processor import ImageContext


class DummyXObj:
    """Pure Python mock object bypassing the pikepdf binary-compiled initialization trap."""

    def __init__(self, dict_data=None, objgen=(1, 0)):
        self._dict = dict_data or {}
        self._objgen = objgen
        self.written_bytes = None
        self.written_filter = None
        self.ColorSpace = None

    @property
    def objgen(self):
        return self._objgen

    def write(self, data, filter=None):
        """Mocks the pikepdf Stream data write method."""
        self.written_bytes = data
        self.written_filter = filter

    def get(self, key, default=None):
        return self._dict.get(key, default)

    def __getitem__(self, key):
        return self._dict[key]

    def __setitem__(self, key, value):
        self._dict[key] = value

    def __delitem__(self, key):
        del self._dict[key]

    def __contains__(self, key):
        return key in self._dict


# ============================================================================
# _is_eligible_for_recolor
# ============================================================================


def test_is_eligible_valid():
    xobj = DummyXObj({"/ColorSpace": "/DeviceRGB", "/BitsPerComponent": 8})
    seen = set()
    assert mod._is_eligible_for_recolor(xobj, seen) is True
    assert xobj.objgen in seen


def test_is_eligible_skips_already_seen():
    xobj = DummyXObj(objgen=(5, 0))
    seen = {(5, 0)}
    assert mod._is_eligible_for_recolor(xobj, seen) is False


def test_is_eligible_skips_image_mask():
    xobj = DummyXObj({"/ImageMask": True, "/ColorSpace": "/DeviceRGB"})
    assert mod._is_eligible_for_recolor(xobj, set()) is False


@pytest.mark.parametrize("bits", [1, "1"])
def test_is_eligible_skips_low_bit_depth(bits):
    xobj = DummyXObj({"/BitsPerComponent": bits, "/ColorSpace": "/DeviceRGB"})
    assert mod._is_eligible_for_recolor(xobj, set()) is False


def test_is_eligible_requires_colorspace():
    xobj = DummyXObj({"/BitsPerComponent": 8})  # Missing /ColorSpace
    assert mod._is_eligible_for_recolor(xobj, set()) is False


# ============================================================================
# _extract_and_stage_pil
# ============================================================================


def test_extract_and_stage_pil_success(monkeypatch):
    xobj = DummyXObj({"/ColorSpace": "/DeviceRGB"})
    mock_pil = Image.new("RGB", (10, 10))

    monkeypatch.setattr(mod, "extract_to_pil", lambda x: mock_pil)
    monkeypatch.setattr(mod, "ensure_thread_safe", lambda img: None)

    res = mod._extract_and_stage_pil(xobj)
    assert res == mock_pil


def test_extract_and_stage_pil_failed_decode(monkeypatch):
    xobj = DummyXObj({"/ColorSpace": "/DeviceRGB"})
    monkeypatch.setattr(mod, "extract_to_pil", lambda x: None)

    assert mod._extract_and_stage_pil(xobj) is None


@pytest.mark.parametrize("mode", ["L", "1"])
def test_extract_and_stage_pil_already_grayscale(monkeypatch, mode):
    xobj = DummyXObj({"/ColorSpace": "/DeviceGray"})
    mock_pil = Image.new(mode, (10, 10))

    monkeypatch.setattr(mod, "extract_to_pil", lambda x: mock_pil)

    assert mod._extract_and_stage_pil(xobj) is None


def test_extract_and_stage_pil_missing_colorspace_invariant(monkeypatch):
    # Tests Guard 5: missing ColorSpace key mid-flight
    xobj = DummyXObj({})
    mock_pil = Image.new("RGB", (10, 10))

    monkeypatch.setattr(mod, "extract_to_pil", lambda x: mock_pil)

    assert mod._extract_and_stage_pil(xobj) is None


# ============================================================================
# worker_recolor_pixels
# ============================================================================


def test_worker_recolor_pixels_flate():
    img = Image.new("RGB", (5, 5), color=(255, 0, 0))
    payload = mod.RecolorPayload(pil_img=img, fmt="flatedecode", quality=75)

    res = mod.worker_recolor_pixels(payload)
    assert isinstance(res, mod.RecolorResult)
    assert len(res.compressed_bytes) > 0


def test_worker_recolor_pixels_dct():
    img = Image.new("RGB", (5, 5), color=(0, 255, 0))
    payload = mod.RecolorPayload(pil_img=img, fmt="dctdecode", quality=60)

    res = mod.worker_recolor_pixels(payload)
    assert isinstance(res, mod.RecolorResult)
    assert res.compressed_bytes.startswith(b"\xff\xd8")


# ============================================================================
# commit_recolored_stream
# ============================================================================


def test_commit_recolored_stream_mutations(minimal_pdf):
    """Verifies metadata deletion and new stream generation commits safely."""
    smask = minimal_pdf.make_stream(b"")
    smask["/ColorSpace"] = pikepdf.Name("/DeviceRGB")

    xobj = minimal_pdf.make_stream(b"")
    xobj["/ColorSpace"] = pikepdf.Name("/DeviceRGB")
    xobj["/Intent"] = pikepdf.Name("/Perceptual")
    xobj["/DecodeParms"] = pikepdf.Dictionary()
    xobj["/SMask"] = smask

    ctx = ImageContext(xobj=xobj, smask_xobj=None, orig_size=0, img_dict={}, page_num=1)
    payload = mod.RecolorPayload(pil_img=None, fmt="dctdecode", quality=75)
    result = mod.RecolorResult(compressed_bytes=b"fake_jpeg_bytes")

    status = mod.commit_recolored_stream(ctx, result, payload)

    assert status is True
    assert "/Intent" not in xobj
    assert "/DecodeParms" not in xobj
    assert xobj["/ColorSpace"] == pikepdf.Name("/DeviceGray")
    assert smask["/ColorSpace"] == pikepdf.Name("/DeviceGray")


# Append these tests to tests/utils/images/test_grayscale.py


# ============================================================================
# prepare_recolor_payload
# ============================================================================


def test_prepare_recolor_payload_skips_ineligible():
    """Covers lines 83-100 by forcing early exits on structural checks."""
    # Force _is_eligible_for_recolor to return False via an image mask
    ineligible_xobj = DummyXObj({"/ImageMask": True})
    img = {"xobj": ineligible_xobj}

    assert mod.prepare_recolor_payload(img, 75, set()) is None


def test_prepare_recolor_payload_skips_failed_pil_extraction(monkeypatch):
    """Covers lines 87-100 by forcing early exit on failed PIL extraction."""
    xobj = DummyXObj({"/ColorSpace": "/DeviceRGB", "/BitsPerComponent": 8})
    img = {"xobj": xobj}

    # Simulate a corrupted stream or unextractable structure
    monkeypatch.setattr(mod, "_extract_and_stage_pil", lambda x: None)

    assert mod.prepare_recolor_payload(img, 75, set()) is None


# ============================================================================
# worker_recolor_pixels Exception Handling
# ============================================================================


def test_worker_recolor_pixels_raises_runtime_error(monkeypatch):
    """Covers lines 110-111 by forcing a conversion ValueError within Pillow."""

    def mock_convert_raise(*args, **kwargs):
        raise ValueError("Simulated PIL internal conversion channel corruption.")

    img = Image.new("RGB", (5, 5))
    monkeypatch.setattr(img, "convert", mock_convert_raise)

    payload = mod.RecolorPayload(pil_img=img, fmt="dctdecode", quality=75)

    with pytest.raises(RuntimeError, match="PIL failed downsampling image channels"):
        mod.worker_recolor_pixels(payload)


# ============================================================================
# commit_recolored_stream Filter Formats
# ============================================================================


def test_commit_recolored_stream_flate_branch():
    """Covers lines 143-144 exercising the FlateDecode format write path."""
    xobj = DummyXObj({"/ColorSpace": "/DeviceRGB"})
    ctx = ImageContext(xobj=xobj, smask_xobj=None, orig_size=0, img_dict={}, page_num=1)

    payload = mod.RecolorPayload(pil_img=None, fmt="flatedecode", quality=75)
    result = mod.RecolorResult(compressed_bytes=b"deflated_bits")

    assert mod.commit_recolored_stream(ctx, result, payload) is True
    assert xobj.written_bytes == b"deflated_bits"
    assert xobj.written_filter == pikepdf.Name("/FlateDecode")
    assert xobj.ColorSpace == pikepdf.Name("/DeviceGray")


def test_prepare_recolor_payload_success(monkeypatch):
    """Exercises lines 91-101 to secure 100% coverage on payload staging."""
    xobj = DummyXObj({"/ColorSpace": "/DeviceRGB", "/BitsPerComponent": 8})
    img = {"xobj": xobj, "format": "FlateDecode", "page": 3}

    mock_pil = SimpleNamespace(mode="RGB")
    monkeypatch.setattr(mod, "extract_to_pil", lambda x: mock_pil)
    monkeypatch.setattr(mod, "ensure_thread_safe", lambda x: None)

    result = mod.prepare_recolor_payload(img, quality=85, seen_objgens=set())

    assert result is not None
    payload, ctx = result

    # Assert structural integrity and format standardization
    assert payload.pil_img == mock_pil
    assert payload.fmt == "flatedecode"
    assert payload.quality == 85
    assert ctx.page_num == 3


import io
import pytest

import pdftl.utils.images.grayscale as gray_mod

# ==============================================================================
# FIXTURES & HELPERS (Preserved from test_image_utils)
# ==============================================================================


@pytest.fixture
def empty_pdf():
    with pikepdf.Pdf.new() as pdf:
        yield pdf


def create_real_image_stream(pdf, mode="RGB", size=(10, 10), fmt="JPEG", extra_entries=None):
    img = Image.new(mode, size, color="red" if mode == "RGB" else 128)
    buf = io.BytesIO()

    if fmt == "JPEG":
        img.save(buf, format="JPEG")
        payload = buf.getvalue()
        filter_value = pikepdf.Name("/DCTDecode")
    else:
        payload = img.tobytes()
        filter_value = None

    stream = pdf.make_stream(payload)
    stream["/Type"] = pikepdf.Name("/XObject")
    stream["/Subtype"] = pikepdf.Name("/Image")
    stream["/Width"] = size[0]
    stream["/Height"] = size[1]
    stream["/BitsPerComponent"] = 8 if mode != "1" else 1
    stream["/ColorSpace"] = pikepdf.Name("/DeviceGray" if mode in ("L", "1") else "/DeviceRGB")

    if filter_value is not None:
        stream["/Filter"] = filter_value
    if extra_entries:
        for k, v in extra_entries.items():
            stream[k] = v
    return stream


# ==============================================================================
# SALVAGED & PORTED REGRESSION TESTS
# ==============================================================================


@pytest.mark.parametrize(
    "extra_entries",
    [
        {"/ImageMask": True},
        {"/BitsPerComponent": 1},
    ],
)
def test_prepare_payload_structural_guards(empty_pdf, extra_entries):
    """Ported from test_convert_grayscale_structural_guards."""
    img_xobj = create_real_image_stream(empty_pdf, extra_entries=extra_entries)
    img_meta = {"xobj": img_xobj, "format": "png", "page": 1}
    seen = set()

    # Phased orchestrator explicitly aborts stage 1 processing by returning None
    assert gray_mod.prepare_recolor_payload(img_meta, 75, seen) is None


def test_prepare_payload_already_grayscale(empty_pdf):
    """Ported from test_convert_grayscale_already_grayscale."""
    img_xobj = create_real_image_stream(empty_pdf, mode="L")
    img_meta = {"xobj": img_xobj, "format": "dctdecode", "page": 1}
    seen = set()

    assert gray_mod.prepare_recolor_payload(img_meta, 75, seen) is None


def test_prepare_payload_handles_missing_colorspace(empty_pdf):
    """Ported from test_neutralize_print_colorspace_edge_case1."""
    img_xobj = create_real_image_stream(empty_pdf)
    del img_xobj["/ColorSpace"]

    img_meta = {"xobj": img_xobj, "format": "dctdecode", "page": 1}
    seen = set()

    assert gray_mod.prepare_recolor_payload(img_meta, 75, seen) is None


def test_commit_strips_decode_parms(empty_pdf):
    """Ported from test_convert_grayscale_strip_decode_parms_unconditional."""
    img_xobj = create_real_image_stream(empty_pdf, mode="RGB", fmt="JPEG")
    img_xobj["/DecodeParms"] = pikepdf.Dictionary({"/Predictor": 15})
    img_meta = {"xobj": img_xobj, "format": "dctdecode", "page": 1}
    seen = set()

    prepared = gray_mod.prepare_recolor_payload(img_meta, 75, seen)
    payload, ctx = prepared
    result = gray_mod.worker_recolor_pixels(payload)

    assert gray_mod.commit_recolored_stream(ctx, result, payload) is True
    assert "/DecodeParms" not in img_xobj


def test_commit_handles_mutation_pdf_error(empty_pdf, monkeypatch):
    """Ported from test_convert_grayscale_flush_mutation_pdf_error."""
    img_xobj = create_real_image_stream(empty_pdf)
    img_meta = {"xobj": img_xobj, "format": "dctdecode", "page": 1}
    seen = set()

    prepared = gray_mod.prepare_recolor_payload(img_meta, 75, seen)
    payload, ctx = prepared
    result = gray_mod.worker_recolor_pixels(payload)

    original_name_init = pikepdf.Name

    def mock_name_constructor(value, *args, **kwargs):
        if value == "/DeviceGray":
            raise pikepdf.PdfError("Simulated write layout lock violation")
        return original_name_init(value, *args, **kwargs)

    monkeypatch.setattr(pikepdf, "Name", mock_name_constructor)

    # Commit phase should catch internal pikepdf errors and report failure safely
    assert gray_mod.commit_recolored_stream(ctx, result, payload) is False


def test_prepare_payload_handles_colorspace_mutation_failure():
    """Ported from test_core_lines_28_29_handles_colorspace_mutation_failure."""
    import pdftl.utils.images.grayscale as gray_mod

    class MockStreamWithBadArray:
        def __init__(self):
            self._cs = pikepdf.Array([pikepdf.Name("/Separation")])
            self.objgen = (99, 0)  # Added to satisfy the duplicate checker loop

        def __getitem__(self, key):
            if key == "/ColorSpace":
                return self._cs
            return 10

        def get(self, key, default=None):
            if key in ("/ImageMask", "/BitsPerComponent"):
                return default
            if key == "/ColorSpace":
                return self._cs
            return 10

        def __setitem__(self, key, value):
            if isinstance(key, pikepdf.Name) and key == pikepdf.Name("/ColorSpace"):
                raise pikepdf.PdfError("Simulated write block on stream dict")

        def read_bytes(self):
            return b""

    img_meta = {"xobj": MockStreamWithBadArray(), "format": "dctdecode", "page": 1}
    seen = set()

    assert gray_mod.prepare_recolor_payload(img_meta, 75, seen) is None


def test_worker_handles_pil_conversion_value_error(empty_pdf, monkeypatch):
    """Ported from test_convert_grayscale_pil_conversion_value_error."""
    from PIL import Image
    import pdftl.utils.images.grayscale as gray_mod

    def mock_convert(*args, **kwargs):
        raise ValueError("Matrix downsample channel mismatch conversion failure")

    monkeypatch.setattr(Image.Image, "convert", mock_convert)

    img_xobj = create_real_image_stream(empty_pdf)
    img_meta = {"xobj": img_xobj, "format": "dctdecode", "page": 1}
    seen = set()

    prepared = gray_mod.prepare_recolor_payload(img_meta, 75, seen)
    assert prepared is not None
    payload, ctx = prepared

    # The off-thread worker wraps this failure explicitly in a RuntimeError
    with pytest.raises(RuntimeError, match="PIL failed downsampling image channels"):
        gray_mod.worker_recolor_pixels(payload)


def test_commit_handles_nested_smask_neutralization(empty_pdf):
    """Ported from test_core_lines_59_61_neutralizes_nested_smask."""
    import pdftl.utils.images.grayscale as gray_mod

    smask_stream = empty_pdf.make_stream(b"")
    smask_stream.ColorSpace = pikepdf.Name("/DeviceRGB")

    # Fix: build a structurally valid image stream first, then inject the SMask key
    fake_xobj = create_real_image_stream(empty_pdf, mode="RGB", fmt="RAW")
    fake_xobj["/SMask"] = smask_stream

    img_meta = {"xobj": fake_xobj, "format": "dctdecode", "page": 1}
    seen = set()

    prepared = gray_mod.prepare_recolor_payload(img_meta, 75, seen)
    assert prepared is not None
    payload, ctx = prepared

    result = gray_mod.worker_recolor_pixels(payload)
    success = gray_mod.commit_recolored_stream(ctx, result, payload)

    assert success is True
    assert smask_stream.ColorSpace == pikepdf.Name("/DeviceGray")
