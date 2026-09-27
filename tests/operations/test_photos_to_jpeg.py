# tests/operations/test_photos_to_jpeg.py
#
# Ground truth: pdfium renders of the page before and after, and the image
# statistics computed here with numpy, independently of the operation.

import io
import zlib

import numpy as np
import pikepdf
import pytest
from PIL import Image, ImageDraw

import pdftl.operations.photos_to_jpeg as pj
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.photos_to_jpeg import photos_to_jpeg

W, H = 320, 240


def _photo(mode="RGB", seed=1) -> Image.Image:
    """Smooth random field plus sensor noise: many colours, few equal neighbours."""
    rng = np.random.default_rng(seed)
    coarse = rng.integers(0, 256, (6, 8, 3), dtype=np.uint8)
    smooth = np.asarray(Image.fromarray(coarse).resize((W, H), Image.BICUBIC), dtype=np.int16)
    noisy = np.clip(smooth + rng.normal(0, 4, smooth.shape), 0, 255).astype(np.uint8)
    img = Image.fromarray(noisy, "RGB")
    return img.convert("L") if mode == "L" else img


def _graphic() -> Image.Image:
    """Screenshot-like: flat panels, antialiased text, a gradient bar."""
    img = Image.new("RGB", (W, H), (240, 240, 240))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, W, 40], fill=(30, 60, 120))
    for y in range(60, H, 18):
        draw.text((10, y), "Quarterly totals 12,345 and 67,890 widgets", fill=(0, 0, 0))
    for x in range(W):
        draw.line([(x, 220), (x, 239)], fill=(x * 255 // W, 100, 200))
    return img


def _add_image(pdf, pil, **extra):
    colour_space = pikepdf.Name.DeviceGray if pil.mode == "L" else pikepdf.Name.DeviceRGB
    xobj = pdf.make_stream(
        zlib.compress(pil.tobytes(), 9),
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=pil.width,
        Height=pil.height,
        BitsPerComponent=8,
        ColorSpace=colour_space,
        Filter=pikepdf.Name.FlateDecode,
    )
    for key, value in extra.items():
        xobj["/" + key] = value
    return xobj


def _pdf_with(*images, pdf=None):
    pdf = pdf if pdf is not None else pikepdf.new()
    for img in images:
        page = pdf.add_blank_page(page_size=(W, H))
        xobj = img if isinstance(img, pikepdf.Stream) else _add_image(pdf, img)
        page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=xobj))
        page.Contents = pdf.make_stream(f"q {W} 0 0 {H} 0 0 cm /Im0 Do Q".encode())
    return pdf


def _render(pdf, index=0):
    import pypdfium2

    buf = io.BytesIO()
    pdf.save(buf)
    doc = pypdfium2.PdfDocument(buf.getvalue())
    try:
        return np.asarray(doc[index].render(scale=1).to_pil().convert("RGB"), dtype=np.float64)
    finally:
        doc.close()


def _psnr(a, b):
    mse = ((a - b) ** 2).mean()
    return float("inf") if mse == 0 else 10 * np.log10(255**2 / mse)


def _filter(pdf, index=0):
    return str(pdf.pages[index].Resources.XObject.Im0.Filter)


# --- what converts ---


@pytest.mark.parametrize("mode", ["RGB", "L"])
def test_photo_becomes_a_jpeg_that_renders_close_to_the_original(mode):
    pdf = _pdf_with(_photo(mode))
    before = _render(pdf)
    raw = len(pdf.pages[0].Resources.XObject.Im0.read_raw_bytes())
    result = photos_to_jpeg(pdf, [])
    assert result.data == {"images": 1}
    xobj = pdf.pages[0].Resources.XObject.Im0
    assert _filter(pdf) == "/DCTDecode"
    assert len(xobj.read_raw_bytes()) <= raw / 2
    assert Image.open(io.BytesIO(xobj.read_raw_bytes())).mode == mode
    assert 30 < _psnr(before, _render(pdf)) < 99


def test_icc_based_rgb_photo_converts_and_keeps_its_colour_space():
    from PIL import ImageCms

    pdf = pikepdf.new()
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    icc = pdf.make_stream(profile, N=3)
    cs = pikepdf.Array([pikepdf.Name.ICCBased, icc])
    pdf = _pdf_with(_add_image(pdf, _photo(), ColorSpace=cs), pdf=pdf)
    photos_to_jpeg(pdf, ["quality=80"])
    xobj = pdf.pages[0].Resources.XObject.Im0
    assert _filter(pdf) == "/DCTDecode"
    assert str(xobj.ColorSpace[0]) == "/ICCBased"


def test_stale_decode_parms_are_removed():
    pdf = pikepdf.new()
    xobj = _add_image(pdf, _photo(), DecodeParms=pikepdf.Dictionary(Predictor=1))
    pdf = _pdf_with(xobj, pdf=pdf)
    photos_to_jpeg(pdf, [])
    assert "/DecodeParms" not in pdf.pages[0].Resources.XObject.Im0


def test_shared_image_is_converted_once():
    pdf = pikepdf.new()
    xobj = _add_image(pdf, _photo())
    pdf = _pdf_with(xobj, xobj, pdf=pdf)
    assert photos_to_jpeg(pdf, []).data == {"images": 1}


def test_page_specs_limit_the_pages():
    pdf = _pdf_with(_photo(seed=1), _photo(seed=2))
    photos_to_jpeg(pdf, ["2"])
    assert (_filter(pdf, 0), _filter(pdf, 1)) == ("/FlateDecode", "/DCTDecode")


# --- what does not ---


def test_graphics_are_left_lossless():
    pdf = _pdf_with(_graphic())
    before = pdf.pages[0].Resources.XObject.Im0.read_raw_bytes()
    assert photos_to_jpeg(pdf, []).data == {"images": 0}
    assert pdf.pages[0].Resources.XObject.Im0.read_raw_bytes() == before


def test_few_colours_are_left_lossless():
    rng = np.random.default_rng(3)
    palette = rng.integers(0, 256, (200, 3), dtype=np.uint8)
    pixels = palette[rng.integers(0, 200, (H, W))]
    pdf = _pdf_with(Image.fromarray(pixels, "RGB"))
    assert photos_to_jpeg(pdf, []).data == {"images": 0}


def test_no_large_win_no_conversion():
    photo = _photo()
    buf = io.BytesIO()
    photo.save(buf, format="JPEG", quality=75, optimize=True)
    jpeg_size = len(buf.getvalue())
    assert pj.photo_jpeg(photo, 75, 2 * jpeg_size - 1) is None
    assert pj.photo_jpeg(photo, 75, 2 * jpeg_size) == buf.getvalue()


def test_colour_noise_fails_the_quality_floor():
    # JPEG's chroma subsampling cannot keep per-pixel colour noise.
    noise = np.random.default_rng(4).integers(0, 256, (H, W, 3), dtype=np.uint8)
    pdf = _pdf_with(Image.fromarray(noise, "RGB"))
    assert photos_to_jpeg(pdf, ["quality=100"]).data == {"images": 0}


def test_flat_graphic_with_many_colours_is_left_lossless():
    img = _graphic()
    img.paste(_photo().crop((0, 0, 110, 90)), (W - 110, H - 90))  # 13% of the area
    assert img.getcolors(pj.MIN_COLOURS) is None
    flat = (np.diff(np.asarray(img.convert("L"), dtype=np.int16), axis=1) == 0).mean()
    assert flat > pj.MAX_FLAT
    assert photos_to_jpeg(_pdf_with(img), []).data == {"images": 0}


def _decoded_psnr(pil, encoded: bytes) -> float:
    a = np.asarray(pil, dtype=np.float64)
    b = np.asarray(Image.open(io.BytesIO(encoded)).convert(pil.mode), dtype=np.float64)
    return _psnr(a, b)


def _jpeg(pil, quality, subsampling) -> bytes:
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=quality, subsampling=subsampling)
    return buf.getvalue()


@pytest.mark.parametrize("mode", ["RGB", "L"])
def test_a_quality_too_low_is_raised_until_the_jpeg_is_faithful(mode):
    photo = _photo(mode)
    assert _decoded_psnr(photo, _jpeg(photo, 1, 2)) < 30  # quality 1 alone would fail
    pdf = _pdf_with(photo)
    assert photos_to_jpeg(pdf, ["quality=1"]).data == {"images": 1}
    assert _decoded_psnr(photo, pdf.pages[0].Resources.XObject.Im0.read_raw_bytes()) >= 30


def test_without_guard_fidelity_a_failing_quality_is_kept_not_raised():
    photo = _photo()
    assert _decoded_psnr(photo, _jpeg(photo, 1, 2)) < 30  # ground truth: quality 1 fails PSNR
    pdf = _pdf_with(photo)
    assert photos_to_jpeg(pdf, ["quality=1"], guard_fidelity=False).data == {"images": 1}
    encoded = pdf.pages[0].Resources.XObject.Im0.read_raw_bytes()
    assert _decoded_psnr(photo, encoded) < 30  # kept despite failing the PSNR floor


def test_without_guard_fidelity_the_size_budget_still_applies():
    photo = _photo()
    buf = io.BytesIO()
    photo.save(buf, format="JPEG", quality=75, optimize=True)
    jpeg_size = len(buf.getvalue())
    assert pj.photo_jpeg(photo, 75, 2 * jpeg_size - 1, guard_fidelity=False) is None
    assert pj.photo_jpeg(photo, 75, 2 * jpeg_size, guard_fidelity=False) == buf.getvalue()


def _colour_grain() -> Image.Image:
    """A smooth field under strong independent noise in each colour band."""
    rng = np.random.default_rng(7)
    base = np.asarray(_photo(), dtype=np.float64)
    return Image.fromarray(np.clip(base + rng.normal(0, 12, base.shape), 0, 255).astype(np.uint8))


def test_colour_grain_is_kept_by_full_resolution_chroma():
    from PIL import JpegImagePlugin

    grain = _colour_grain()
    assert _decoded_psnr(grain, _jpeg(grain, 95, 2)) < 30  # 4:2:0 fails at any quality
    pdf = _pdf_with(grain)
    assert photos_to_jpeg(pdf, ["max_ratio=0.8"]).data == {"images": 1}
    encoded = pdf.pages[0].Resources.XObject.Im0.read_raw_bytes()
    assert JpegImagePlugin.get_sampling(Image.open(io.BytesIO(encoded))) == 0  # 4:4:4
    assert _decoded_psnr(grain, encoded) >= 30


def test_an_encoder_failure_during_the_search_counts_as_a_miss(monkeypatch):
    real = pj._encode

    def failing(pil_img, quality, subsampling=None):
        if subsampling is not None:
            raise OSError("broken data stream when writing image file")
        return real(pil_img, quality, subsampling)

    monkeypatch.setattr(pj, "_encode", failing)
    pdf = _pdf_with(_photo())
    assert photos_to_jpeg(pdf, ["quality=1"]).data == {"images": 0}
    assert _filter(pdf) == "/FlateDecode"


@pytest.mark.parametrize(
    "extra",
    [
        {"BitsPerComponent": 1},
        {"ImageMask": True},
        {"Decode": pikepdf.Array([1, 0, 1, 0, 1, 0])},
        {"Mask": pikepdf.Array([0, 10, 0, 10, 0, 10])},
        {"Filter": pikepdf.Name.DCTDecode},
        {"Width": 10, "Height": 10},
        {"ColorSpace": pikepdf.Name.DeviceCMYK},
        {"ColorSpace": pikepdf.Array([pikepdf.Name.Indexed, pikepdf.Name.DeviceCMYK, 0, b"abcd"])},
        {"ColorSpace": pikepdf.Array([pikepdf.Name.Indexed, pikepdf.Name.DeviceRGB, 0])},
        {"Width": pikepdf.Name.Bad},
    ],
)
def test_dictionaries_that_rule_out_jpeg(extra):
    pdf = pikepdf.new()
    assert not pj.is_candidate(_add_image(pdf, _photo(), **extra))


def test_icc_based_components():
    pdf = pikepdf.new()
    for n, ok in [(1, True), (3, True), (4, False)]:
        cs = pikepdf.Array([pikepdf.Name.ICCBased, pdf.make_stream(b"", N=n)])
        assert pj.is_candidate(_add_image(pdf, _photo(), ColorSpace=cs)) is ok
    no_n = pikepdf.Array([pikepdf.Name.ICCBased, pdf.make_stream(b"")])
    assert not pj.is_candidate(_add_image(pdf, _photo(), ColorSpace=no_n))
    filters = pikepdf.Array([pikepdf.Name.FlateDecode])
    assert pj.is_candidate(_add_image(pdf, _photo(), Filter=filters))
    unfiltered = _add_image(pdf, _photo())
    del unfiltered["/Filter"]
    assert pj.is_candidate(unfiltered)


def test_modes_jpeg_cannot_take_are_left_alone():
    assert pj.photo_jpeg(_photo().convert("CMYK"), 75, 10**9) is None


def test_undecodable_and_inline_images_are_skipped():
    pdf = pikepdf.new()
    broken = _add_image(pdf, _photo())
    broken.write(b"not zlib", filter=pikepdf.Name.FlateDecode)
    pdf = _pdf_with(broken, pdf=pdf)
    assert photos_to_jpeg(pdf, []).data == {"images": 0}
    assert pj._prepare({"inline": True, "xobj": broken}, set(), 75) is None
    assert pj._prepare({}, set(), 75) is None


@pytest.mark.parametrize(
    "arg", ["quality=0", "quality=101", "quality=x", "threads=0", "threads=many"]
)
def test_invalid_arguments(arg):
    with pytest.raises(InvalidArgumentError, match="photos_to_jpeg"):
        photos_to_jpeg(_pdf_with(_photo()), [arg])


def test_threads_argument_is_accepted():
    assert photos_to_jpeg(_pdf_with(_photo()), ["threads=2"]).data == {"images": 1}


def test_max_ratio_trades_a_smaller_saving_for_the_loss():
    photo = _photo()
    buf = io.BytesIO()
    photo.save(buf, format="JPEG", quality=75, optimize=True)
    stored = int(len(buf.getvalue()) / 0.7)  # JPEG is 70% of the stored size
    assert pj.photo_jpeg(photo, 75, stored) is None  # default: at most half
    assert pj.photo_jpeg(photo, 75, stored, max_ratio=0.8) == buf.getvalue()


def test_small_photos_qualify_with_a_scaled_colour_threshold():
    # 2304 pixels: more than 576 colours are asked for, not the full-size 1024.
    crop = np.asarray(_photo().crop((0, 0, 48, 48)))
    small = Image.fromarray((crop // 6 * 6).astype(np.uint8))
    assert 576 < len(small.getcolors(2304)) <= 1024
    assert photos_to_jpeg(_pdf_with(small), ["max_ratio=1"]).data == {"images": 1}
    assert not pj.is_candidate(_add_image(pikepdf.new(), _photo().crop((0, 0, 30, 30))))


@pytest.mark.parametrize("value", ["0", "1.5", "-0.2", "half"])
def test_invalid_max_ratio(value):
    with pytest.raises(InvalidArgumentError, match="max_ratio"):
        photos_to_jpeg(_pdf_with(_photo()), [f"max_ratio={value}"])


def _indexed_image(pdf, pil):
    """A palette image stored as /Indexed over DeviceRGB (independent of pdftl's writer)."""
    quantized = pil.quantize(256, dither=Image.Dither.FLOYDSTEINBERG)
    palette = bytes(quantized.getpalette()[: 3 * 256])
    return pdf.make_stream(
        zlib.compress(quantized.tobytes(), 9),
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=pil.width,
        Height=pil.height,
        BitsPerComponent=8,
        ColorSpace=pikepdf.Array([pikepdf.Name.Indexed, pikepdf.Name.DeviceRGB, 255, palette]),
        Filter=pikepdf.Name.FlateDecode,
    )


def test_palette_photo_becomes_an_rgb_jpeg():
    pdf = pikepdf.new()
    pdf = _pdf_with(_indexed_image(pdf, _photo()), pdf=pdf)
    before = _render(pdf)
    assert photos_to_jpeg(pdf, []).data == {"images": 1}
    xobj = pdf.pages[0].Resources.XObject.Im0
    assert _filter(pdf) == "/DCTDecode"
    assert xobj.ColorSpace == pikepdf.Name.DeviceRGB and int(xobj.BitsPerComponent) == 8
    assert 30 < _psnr(before, _render(pdf)) < 99


def test_palette_graphic_is_left_lossless():
    pdf = pikepdf.new()
    pdf = _pdf_with(_indexed_image(pdf, _graphic()), pdf=pdf)
    assert photos_to_jpeg(pdf, []).data == {"images": 0}
    assert _filter(pdf) == "/FlateDecode"
