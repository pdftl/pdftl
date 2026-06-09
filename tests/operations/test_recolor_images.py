# tests/operations/test_recolor_images.py

import pytest
from types import SimpleNamespace

import pdftl.operations.recolor_images as mod
from pdftl.core.core_types import OpResult
from pdftl.exceptions import InvalidArgumentError


# ============================================================================
# _validate_quality
# ============================================================================


def test_validate_quality_default():
    assert mod._validate_quality("75") == 75


@pytest.mark.parametrize("bad", ["0", "101", "-1", "abc", "999"])
def test_validate_quality_invalid(bad):
    with pytest.raises(InvalidArgumentError):
        mod._validate_quality(bad)


# ============================================================================
# _parse_args
# ============================================================================


def test_parse_args_default(monkeypatch):
    monkeypatch.setattr(mod, "parse_keyval_list", lambda *a, **k: {})

    q, specs = mod._parse_args([])
    assert q == 75
    assert specs == []


def test_parse_args_quality_override(monkeypatch):
    def fake_parse_keyval_list(*args, **kwargs):
        bare_tokens = kwargs["bare_tokens"]
        bare_tokens.append("1-5")  # simulate real behavior
        return {"quality": "88"}

    monkeypatch.setattr(mod, "parse_keyval_list", fake_parse_keyval_list)

    q, specs = mod._parse_args(["1-5"])

    assert q == 88
    assert specs == ["1-5"]


# ============================================================================
# recolor_images core behavior
# ============================================================================


class DummyXObj:
    def __init__(self, objgen=(1, 0)):
        self.objgen = objgen


def make_img(xobj, seen=False):
    return {
        "xobj": xobj,
        "format": "png",
        "obj": "img",
    }


def test_recolor_images_no_pages(monkeypatch):
    pdf = SimpleNamespace(pages=[1, 2, 3])

    monkeypatch.setattr(mod, "_parse_args", lambda args: (75, []))
    monkeypatch.setattr(mod, "extract_pdf_images", lambda pdf, pages: [])

    result = mod.recolor_images(pdf, [])

    assert isinstance(result, OpResult)
    assert result.success is True


def test_recolor_images_single_image_success(monkeypatch):
    pdf = SimpleNamespace(pages=[1])

    xobj = DummyXObj()

    monkeypatch.setattr(mod, "_parse_args", lambda args: (75, []))
    monkeypatch.setattr(
        mod,
        "extract_pdf_images",
        lambda pdf, pages: [make_img(xobj)],
    )

    monkeypatch.setattr(mod, "convert_image_dict_to_grayscale", lambda img, q: True)

    result = mod.recolor_images(pdf, [])

    assert result.success is True


def test_recolor_images_skips_duplicate_objgen(monkeypatch):
    pdf = SimpleNamespace(pages=[1])

    xobj = DummyXObj(objgen=(1, 0))

    calls = {"count": 0}

    def fake_convert(img, q):
        calls["count"] += 1
        return True

    monkeypatch.setattr(mod, "_parse_args", lambda args: (75, []))

    monkeypatch.setattr(
        mod,
        "extract_pdf_images",
        lambda pdf, pages: [make_img(xobj), make_img(xobj)],
    )

    monkeypatch.setattr(mod, "convert_image_dict_to_grayscale", fake_convert)

    mod.recolor_images(pdf, [])

    assert calls["count"] == 1


def test_recolor_images_page_specs(monkeypatch):
    pdf = SimpleNamespace(pages=[1, 2, 3])

    monkeypatch.setattr(mod, "_parse_args", lambda args: (75, ["1-2"]))

    monkeypatch.setattr(mod, "page_numbers_matching_page_specs", lambda specs, n: {1, 2})

    captured = {}

    def fake_extract(pdf, pages):
        captured["pages"] = pages
        return []

    monkeypatch.setattr(mod, "extract_pdf_images", fake_extract)

    result = mod.recolor_images(pdf, ["1-2"])

    assert sorted(captured["pages"]) == [1, 2]
    assert result.success is True


def test_convert_flate_decode_rgb_image_to_grayscale(minimal_pdf):
    """Verifies that lossless FlateDecode RGB images (e.g., diagrams, shapes)
    are successfully converted to a single-channel DeviceGray layout.
    """
    import pikepdf
    from PIL import Image

    # 1. Synthesize raw uncompressed pixel payload
    img = Image.new("RGB", (10, 10), color=(255, 0, 0))  # Bright Red canvas
    raw_bytes = img.tobytes()

    # 2. Build an empty stream wrapper cleanly first
    img_stream = minimal_pdf.make_stream(b"")

    # 3. Direct dictionary mapping assignment (100% type safe string keys)
    img_stream["/Width"] = 10
    img_stream["/Height"] = 10
    img_stream["/BitsPerComponent"] = 8
    img_stream["/ColorSpace"] = pikepdf.Name("/DeviceRGB")
    img_stream["/Subtype"] = pikepdf.Name("/Image")
    img_stream["/Type"] = pikepdf.Name("/XObject")

    # 4. Explicitly push the raw un-deflated bytes with the exact desired filter attribute.
    # This prevents pikepdf from attempting to auto-compress the buffer implicitly.
    # 4. Explicitly push compressed zlib data bytes to fit the /FlateDecode filter profile
    import zlib

    compressed_bytes = zlib.compress(raw_bytes)
    img_stream.write(compressed_bytes, filter=pikepdf.Name("/FlateDecode"))

    img_meta = {"xobj": img_stream, "format": "flatedecode"}

    # 5. Execute target function
    from pdftl.utils.images import convert_image_dict_to_grayscale

    assert convert_image_dict_to_grayscale(img_meta, quality=75) is True

    # 6. Assert structural changes
    assert img_stream["/ColorSpace"] == pikepdf.Name("/DeviceGray")
    assert img_stream["/Filter"] == pikepdf.Name("/FlateDecode")
