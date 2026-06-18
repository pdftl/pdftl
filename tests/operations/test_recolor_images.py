# tests/operations/test_recolor_images.py

import pytest
from types import SimpleNamespace

import pdftl.operations.recolor_images as mod
from pdftl.core.core_types import OpResult
from pdftl.exceptions import InvalidArgumentError


# ============================================================================
# _validate_int
# ============================================================================


def test_validate_int_success():
    assert mod._validate_int("75", "quality", 1, 100) == 75
    assert mod._validate_int("4", "threads", 1) == 4


@pytest.mark.parametrize("bad", ["0", "101", "-1", "abc"])
def test_validate_int_invalid_bounds(bad):
    with pytest.raises(InvalidArgumentError):
        mod._validate_int(bad, "quality", 1, 100)


# ============================================================================
# _parse_args
# ============================================================================


def test_parse_args_default(monkeypatch):
    monkeypatch.setattr(mod, "parse_keyval_list", lambda *a, **k: {})

    q, threads, specs = mod._parse_args([])
    assert q == 75
    assert threads is None
    assert specs == []


def test_parse_args_overrides(monkeypatch):
    def fake_parse_keyval_list(*args, **kwargs):
        bare_tokens = kwargs["bare_tokens"]
        bare_tokens.append("1-5")
        return {"quality": "88", "threads": "4"}

    monkeypatch.setattr(mod, "parse_keyval_list", fake_parse_keyval_list)

    q, threads, specs = mod._parse_args(["1-5"])

    assert q == 88
    assert threads == 4
    assert specs == ["1-5"]


# ============================================================================
# recolor_images core behavior
# ============================================================================


class DummyXObj:
    def __init__(self, objgen=(1, 0)):
        self.objgen = objgen


def make_img(xobj):
    return {
        "xobj": xobj,
        "format": "png",
        "obj": "img",
        "page": 1,
    }


def test_recolor_images_no_images(monkeypatch):
    pdf = SimpleNamespace(pages=[1, 2, 3])

    monkeypatch.setattr(mod, "_parse_args", lambda args: (75, None, []))
    monkeypatch.setattr(mod, "extract_pdf_images", lambda pdf, pages: [])
    monkeypatch.setattr(mod, "run_parallel_image_job", lambda **k: 0)

    result = mod.recolor_images(pdf, [])

    assert isinstance(result, OpResult)
    assert result.success is True


def test_recolor_images_orchestration_success(monkeypatch):
    pdf = SimpleNamespace(pages=[1])
    xobj = DummyXObj()

    monkeypatch.setattr(mod, "_parse_args", lambda args: (75, 2, []))
    monkeypatch.setattr(mod, "extract_pdf_images", lambda pdf, pages: [make_img(xobj)])

    def fake_run_parallel(images, threads, prepare_func, worker_func, commit_func):
        assert threads == 2
        assert len(images) == 1
        return 1

    monkeypatch.setattr(mod, "run_parallel_image_job", fake_run_parallel)

    result = mod.recolor_images(pdf, [])
    assert result.success is True


def test_recolor_images_page_specs(monkeypatch):
    pdf = SimpleNamespace(pages=[1, 2, 3])

    monkeypatch.setattr(mod, "_parse_args", lambda args: (75, None, ["1-2"]))
    monkeypatch.setattr(mod, "page_numbers_matching_page_specs", lambda specs, n: {1, 2})

    captured = {}

    def fake_extract(pdf, pages):
        captured["pages"] = pages
        return []

    monkeypatch.setattr(mod, "extract_pdf_images", fake_extract)
    monkeypatch.setattr(mod, "run_parallel_image_job", lambda **k: 0)

    result = mod.recolor_images(pdf, ["1-2"])

    assert sorted(captured["pages"]) == [1, 2]
    assert result.success is True


def test_convert_flate_decode_rgb_image_to_grayscale(minimal_pdf):
    """Verifies that lossless FlateDecode RGB images are successfully converted
    to a single-channel DeviceGray layout using the modern phased pipeline.
    """
    import pikepdf
    from PIL import Image
    import zlib
    import pdftl.utils.images.grayscale as mod

    # 1. Synthesize raw uncompressed pixel payload
    img = Image.new("RGB", (10, 10), color=(255, 0, 0))
    raw_bytes = img.tobytes()

    # 2. Build an empty stream wrapper cleanly first
    img_stream = minimal_pdf.make_stream(b"")

    # 3. Direct dictionary mapping assignment
    img_stream["/Width"] = 10
    img_stream["/Height"] = 10
    img_stream["/BitsPerComponent"] = 8
    img_stream["/ColorSpace"] = pikepdf.Name("/DeviceRGB")
    img_stream["/Subtype"] = pikepdf.Name("/Image")
    img_stream["/Type"] = pikepdf.Name("/XObject")

    # 4. Explicitly push compressed zlib data bytes to fit the /FlateDecode filter profile
    compressed_bytes = zlib.compress(raw_bytes)
    img_stream.write(compressed_bytes, filter=pikepdf.Name("/FlateDecode"))

    img_meta = {"xobj": img_stream, "format": "flatedecode", "page": 0}

    # 5. Execute target function via the updated pipeline functions
    seen_objgens = set()
    prepared = mod.prepare_recolor_payload(img_meta, quality=75, seen_objgens=seen_objgens)
    assert prepared is not None

    payload, ctx = prepared
    result = mod.worker_recolor_pixels(payload)
    status = mod.commit_recolored_stream(ctx, result, payload)

    # 6. Structural verification assertions
    assert status is True
    assert img_stream["/ColorSpace"] == pikepdf.Name("/DeviceGray")
