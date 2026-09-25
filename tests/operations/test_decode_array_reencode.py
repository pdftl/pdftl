# tests/operations/test_decode_array_reencode.py
#
# Regression: re-encoding an image with a non-default /Decode must not invert it.
# Expected appearance is computed from the PDF spec's Decode mapping
# (value = Dmin + sample * (Dmax - Dmin)), not from pikepdf's decoder.

import zlib

import pikepdf
import pytest
from pikepdf import Array, Name

from pdftl.operations.modify_images import modify_images_operation
from pdftl.operations.recolor_images import recolor_images
from pdftl.operations.resample_images import resample_images
from pdftl.utils.pikepdf_compatibility_utils import drop_stale_decode_array

SIZE = 400  # drawn at 1 inch, so 400 dpi: resample_images dpi=150 must act


def _pack_bits(bits):
    out = bytearray()
    for row in bits:
        for i in range(0, len(row), 8):
            byte = 0
            for j, b in enumerate(row[i : i + 8]):
                byte |= b << (7 - j)
            out.append(byte)
    return bytes(out)


def _halves(left, right, w=SIZE, h=SIZE):
    return [[left if x < w // 2 else right for x in range(w)] for _ in range(h)]


def _pdf_with_image(raw, bpc, colorspace, decode, w=SIZE, h=SIZE):
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(144, 144))
    img = pdf.make_stream(zlib.compress(raw))
    img.Type = Name.XObject
    img.Subtype = Name.Image
    img.Width = w
    img.Height = h
    img.BitsPerComponent = bpc
    img.ColorSpace = colorspace
    img.Filter = Name.FlateDecode
    img.Decode = Array(decode)
    page = pdf.pages[0]
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=img))
    page.Contents = pdf.make_stream(b"q 72 0 0 72 0 0 cm /Im0 Do Q")
    return pdf


def _only_image(pdf):
    return next(iter(pdf.pages[0].Resources.XObject.values()))


def _visual(img, x, y):
    """Visual component values in [0, 1] at (x, y), per ISO 32000 8.9.5.2."""
    assert img.Filter == Name.FlateDecode
    data = img.read_bytes()
    w, bpc = int(img.Width), int(img.BitsPerComponent)
    ncomp = {"/DeviceGray": 1, "/DeviceRGB": 3}[str(img.ColorSpace)]
    maxval = (1 << bpc) - 1
    decode = [float(v) for v in img.get("/Decode", [0, 1] * ncomp)]
    row_bytes = (w * ncomp * bpc + 7) // 8
    out = []
    for c in range(ncomp):
        bit = (x * ncomp + c) * bpc
        byte = data[y * row_bytes + bit // 8]
        sample = (byte >> (8 - bpc - bit % 8)) & maxval
        dmin, dmax = decode[2 * c], decode[2 * c + 1]
        out.append(dmin + sample / maxval * (dmax - dmin))
    return out


def _probe_points(img):
    w, h = int(img.Width), int(img.Height)
    return (w // 4, h // 2), (3 * w // 4, h // 2)


def _bitonal_pdf():
    # Raw 1 on the left; with /Decode [1 0] that is black.
    return _pdf_with_image(_pack_bits(_halves(1, 0)), 1, Name.DeviceGray, [1, 0])


def _assert_bitonal_black_left(pdf):
    img = _only_image(pdf)
    left, right = _probe_points(img)
    assert _visual(img, *left) == [0.0]
    assert _visual(img, *right) == [1.0]


def test_resample_bitonal_decode_not_inverted():
    pdf = _bitonal_pdf()
    resample_images(pdf, ["dpi=150", "allow_growth=yes"])
    assert int(_only_image(pdf).Width) < SIZE
    _assert_bitonal_black_left(pdf)


def test_modify_images_png_bitonal_decode_not_inverted():
    pdf = _bitonal_pdf()
    modify_images_operation(pdf, ["(format=png)"])
    _assert_bitonal_black_left(pdf)


def test_resample_gray8_decode_not_inverted():
    raw = bytes(v for row in _halves(30, 220) for v in row)
    pdf = _pdf_with_image(raw, 8, Name.DeviceGray, [1, 0])
    resample_images(pdf, ["dpi=150", "allow_growth=yes"])
    img = _only_image(pdf)
    assert int(img.Width) < SIZE
    left, right = _probe_points(img)
    assert _visual(img, *left)[0] == pytest.approx(1 - 30 / 255, abs=2 / 255)
    assert _visual(img, *right)[0] == pytest.approx(1 - 220 / 255, abs=2 / 255)


def test_recolor_rgb_decode_not_inverted():
    # Raw (240, 240, 240) under an inverting /Decode is near-black.
    rows = _halves((240, 240, 240), (20, 20, 20))
    raw = bytes(c for row in rows for px in row for c in px)
    pdf = _pdf_with_image(raw, 8, Name.DeviceRGB, [1, 0, 1, 0, 1, 0])
    recolor_images(pdf, [])
    img = _only_image(pdf)
    if img.Filter != Name.FlateDecode:
        pytest.skip("recolor chose a lossy codec; covered by the Flate cases")
    assert str(img.ColorSpace) == "/DeviceGray"
    left, right = _probe_points(img)
    assert _visual(img, *left)[0] == pytest.approx(15 / 255, abs=3 / 255)
    assert _visual(img, *right)[0] == pytest.approx(235 / 255, abs=3 / 255)


def _stream(pdf, colorspace, decode):
    s = pdf.make_stream(b"")
    s.ColorSpace = colorspace
    s.Decode = Array(decode)
    return s


def test_drop_stale_decode_array_keeps_indexed_to_indexed():
    pdf = pikepdf.new()
    indexed = Array([Name.Indexed, Name.DeviceRGB, 1, pikepdf.String(b"\x00" * 6)])
    s = _stream(pdf, indexed, [1, 0])
    drop_stale_decode_array(s, source_was_indexed=True)
    assert list(s.Decode) == [1, 0]


def test_drop_stale_decode_array_drops_when_source_not_indexed():
    pdf = pikepdf.new()
    indexed = Array([Name.Indexed, Name.DeviceRGB, 1, pikepdf.String(b"\x00" * 6)])
    s = _stream(pdf, indexed, [1, 0])
    drop_stale_decode_array(s, source_was_indexed=False)
    assert "/Decode" not in s


def test_drop_stale_decode_array_drops_for_device_space():
    pdf = pikepdf.new()
    s = _stream(pdf, Name.DeviceGray, [1, 0])
    drop_stale_decode_array(s, source_was_indexed=True)
    assert "/Decode" not in s
