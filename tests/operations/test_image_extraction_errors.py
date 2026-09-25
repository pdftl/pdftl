# tests/operations/test_image_extraction_errors.py
#
# An image Pillow can't represent (here a 5-channel DeviceN "hi-fi" image) must be
# skipped, not abort the whole operation: other images still get processed.

import zlib

import pikepdf
from pikepdf import Array, Name

from pdftl.operations.modify_images import modify_images_operation
from pdftl.operations.resample_images import resample_images
from pdftl.utils import pikepdf_compatibility_utils as compat

SIZE = 400  # drawn at 1 inch: 400 dpi, so dpi=150 resamples it


def _image(pdf, raw, colorspace, w=SIZE, h=SIZE):
    img = pdf.make_stream(zlib.compress(raw))
    img.Type, img.Subtype = Name.XObject, Name.Image
    img.Width, img.Height, img.BitsPerComponent = w, h, 8
    img.ColorSpace, img.Filter = colorspace, Name.FlateDecode
    return img


def _hifi_colorspace(pdf):
    tint = pdf.make_stream(b"{pop pop pop pop pop 0 0 0 0}")
    tint.FunctionType, tint.Domain, tint.Range = 4, [0, 1] * 5, [0, 1] * 4
    inks = Array([Name("/C"), Name("/M"), Name("/Y"), Name("/K"), Name("/O")])
    return Array([Name.DeviceN, inks, Name.DeviceCMYK, tint])


def _pdf_with_hifi_and_gray():
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(144, 72))
    hifi = _image(pdf, bytes(5 * SIZE * SIZE), _hifi_colorspace(pdf))
    gray = _image(
        pdf, bytes(range(256)) * (SIZE * SIZE // 256) + bytes(SIZE * SIZE % 256), Name.DeviceGray
    )
    page = pdf.pages[0]
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Hi=hifi, Gr=gray))
    page.Contents = pdf.make_stream(b"q 72 0 0 72 0 0 cm /Hi Do Q q 72 0 0 72 72 0 cm /Gr Do Q")
    return pdf


def _xobjects(pdf):
    return pdf.pages[0].Resources.XObject


def test_hifi_image_really_is_unextractable():
    pdf = _pdf_with_hifi_and_gray()
    try:
        pikepdf.PdfImage(_xobjects(pdf).Hi).as_pil_image()
    except compat.image_extraction_errors():
        return
    raise AssertionError("fixture no longer exercises the unextractable path")


def test_resample_skips_hifi_image_and_still_resamples_others():
    pdf = _pdf_with_hifi_and_gray()
    resample_images(pdf, ["dpi=150", "allow_growth=yes"])
    assert int(_xobjects(pdf).Hi.Width) == SIZE
    assert int(_xobjects(pdf).Gr.Width) < SIZE


def test_modify_images_skips_hifi_image():
    pdf = _pdf_with_hifi_and_gray()
    modify_images_operation(pdf, ["(format=png)"])
    assert _xobjects(pdf).Hi.ColorSpace[0] == Name.DeviceN


def test_image_extraction_errors_cover_public_pikepdf_names():
    errors = compat.image_extraction_errors()
    for name in (
        "HifiPrintImageNotTranscodableError",
        "InvalidPdfImageError",
        "UnsupportedImageTypeError",
    ):
        assert getattr(pikepdf, name) in errors
    assert all(issubclass(e, Exception) for e in errors)


def test_image_extraction_errors_without_private_module(monkeypatch):
    # pikepdf before 10.11 has no pikepdf.models._image_exceptions.
    import sys

    import pikepdf.models

    monkeypatch.setitem(sys.modules, "pikepdf.models._image_exceptions", None)
    monkeypatch.delattr(pikepdf.models, "_image_exceptions", raising=False)
    errors = compat.image_extraction_errors()
    assert pikepdf.HifiPrintImageNotTranscodableError in errors
    assert pikepdf.UnsupportedImageTypeError in errors
