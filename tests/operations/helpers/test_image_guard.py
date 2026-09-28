# tests/operations/helpers/test_image_guard.py
#
# Correctness is measured by rendering with pdfium before and after: the
# pixels on the page must not change.

import io
import zlib

import numpy as np
import pikepdf
import pypdfium2 as pdfium
import pytest
from PIL import Image

import pdftl.operations.helpers.image_guard as guard
import pdftl.utils.mrc.codecs as codecs
from pdftl.operations.helpers.image_guard import (
    jbig2_encode_inverted_bitonal,
    restore_unless_smaller,
    snapshot_images,
)
from pdftl.utils.images.pil_to_pdf import get_optimal_1bit_payload

W, H = 400, 200


def _ink() -> np.ndarray:
    ink = np.zeros((H, W), dtype=bool)
    for y in range(10, H - 20, 24):
        for x in range(10, W - 10, 14):
            ink[y : y + 12, x : x + 3 + (x * 7 + y) % 6] = True
    return ink


def _page(stream_setup) -> pikepdf.Pdf:
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(W, H))
    img = pdf.make_stream(b"")
    img.Type, img.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    img.Width, img.Height, img.BitsPerComponent = W, H, 1
    img.ColorSpace = pikepdf.Name.DeviceGray
    stream_setup(img)
    pdf.pages[0].Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=img))
    pdf.pages[0].Contents = pdf.make_stream(b"q %d 0 0 %d 0 0 cm /Im0 Do Q" % (W, H))
    return pdf


def _render(pdf) -> np.ndarray:
    buf = io.BytesIO()
    pdf.save(buf)
    page = pdfium.PdfDocument(buf.getvalue())[0]
    return np.asarray(page.render(scale=1).to_pil().convert("L"), int)


def _flate_inverted(img):
    # Stored as "1 = ink", drawn through /Decode [1 0] so ink paints black.
    img.write(
        zlib.compress(np.packbits(_ink(), axis=1).tobytes()), filter=pikepdf.Name.FlateDecode
    )
    img.Decode = [1, 0]


def _raw_inverted(img):
    img.write(np.packbits(_ink(), axis=1).tobytes())
    img.Decode = [1, 0]


def _ccitt_inverted(img):
    pil = Image.fromarray(~_ink()).convert("1")  # PIL mode "1": 0 = black
    data, filt, parms = get_optimal_1bit_payload(pil)
    assert filt == "/CCITTFaxDecode"
    img.write(data, filter=pikepdf.Name(filt), decode_parms=parms)
    # Swap how samples map to ink twice over: toggle BlackIs1 in the data's own
    # sense, then undo it with /Decode, so the page shows the same picture.
    img.DecodeParms.BlackIs1 = not bool(img.DecodeParms.get("/BlackIs1", False))
    img.Decode = [1, 0]


def _fake_binary_present(monkeypatch, path="/fake/jbig2"):
    monkeypatch.setattr(codecs, "jbig2_binary", lambda: path)


def _fail_if_reencoded(monkeypatch):
    monkeypatch.setattr(
        guard, "_jbig2_reencode_one", lambda *a: pytest.fail("should not have been reencoded")
    )


# --- real jbig2enc round-trip: skipped in CI, where no binary is installed ---


@pytest.mark.parametrize("setup", [_flate_inverted, _raw_inverted, _ccitt_inverted])
def test_jbig2_encoding_an_inverted_image_leaves_the_page_unchanged(setup):
    if codecs.jbig2_binary() is None:
        pytest.skip("no jbig2enc-family binary available on this machine")
    pdf = _page(setup)
    before = _render(pdf)
    jbig2_encode_inverted_bitonal(pdf)
    img = pdf.pages[0].Resources.XObject.Im0
    assert list(img.Decode) == [1, 0]  # kept, unlike the old normalizer
    assert np.array_equal(_render(pdf), before)
    assert before.min() == 0 and before.max() == 255  # there is ink and paper


@pytest.mark.parametrize("setup", [_raw_inverted, _ccitt_inverted])
def test_jbig2_encoding_shrinks_an_uncompressed_or_ccitt_image(setup):
    if codecs.jbig2_binary() is None:
        pytest.skip("no jbig2enc-family binary available on this machine")
    pdf = _page(setup)
    img = pdf.pages[0].Resources.XObject.Im0
    before_len = len(img.read_raw_bytes())
    assert jbig2_encode_inverted_bitonal(pdf) == 1
    assert img.Filter == pikepdf.Name.JBIG2Decode
    assert "/DecodeParms" not in img
    assert len(img.read_raw_bytes()) < before_len


def test_ccitt_with_array_decodeparms_is_left_alone(monkeypatch):
    # Array-wrapped /DecodeParms is malformed; PdfImage can decode it without
    # raising but produces the wrong pixels, so it must not be trusted.
    _fake_binary_present(monkeypatch)
    pdf = _page(_ccitt_inverted)
    img = pdf.pages[0].Resources.XObject.Im0
    img.DecodeParms = pikepdf.Array([img.DecodeParms])
    before = (img.read_raw_bytes(), dict(img.items()))
    assert jbig2_encode_inverted_bitonal(pdf) == 0
    assert (img.read_raw_bytes(), dict(img.items())) == before


# --- _is_inverted_bitonal filtering: no encoder call is even attempted ---


@pytest.mark.parametrize(
    "change",
    [
        lambda img: setattr(img, "ImageMask", True),
        lambda img: setattr(img, "Decode", [0, 1]),  # not inverted
        lambda img: setattr(img, "Decode", [1]),  # malformed
        lambda img: setattr(img, "Decode", [pikepdf.Name.X, 0]),
        lambda img: setattr(img, "BitsPerComponent", 8),
        lambda img: setattr(img, "ColorSpace", pikepdf.Name.DeviceRGB),
    ],
)
def test_images_excluded_by_is_inverted_bitonal_are_left_alone(monkeypatch, change):
    _fake_binary_present(monkeypatch)
    _fail_if_reencoded(monkeypatch)
    pdf = _page(_flate_inverted)
    img = pdf.pages[0].Resources.XObject.Im0
    change(img)
    before = (img.read_raw_bytes(), dict(img.items()))
    assert jbig2_encode_inverted_bitonal(pdf) == 0
    assert (img.read_raw_bytes(), dict(img.items())) == before


def test_an_already_jbig2_image_is_left_alone(monkeypatch):
    _fake_binary_present(monkeypatch)
    _fail_if_reencoded(monkeypatch)
    pdf = _page(_flate_inverted)
    img = pdf.pages[0].Resources.XObject.Im0
    img.Filter = pikepdf.Name.JBIG2Decode  # already JBIG2; re-encoding buys nothing
    before = (img.read_raw_bytes(), dict(img.items()))
    assert jbig2_encode_inverted_bitonal(pdf) == 0
    assert (img.read_raw_bytes(), dict(img.items())) == before


@pytest.mark.parametrize(
    "change",
    [
        lambda img: setattr(img, "Filter", pikepdf.Name.DCTDecode),
        lambda img: setattr(img, "DecodeParms", pikepdf.Dictionary(Predictor=15)),
    ],
)
def test_an_undecodable_inverted_image_is_left_alone(monkeypatch, change):
    # _is_inverted_bitonal accepts these (it does not look at /Filter or
    # /DecodeParms), but PdfImage cannot decode the resulting garbage.
    _fake_binary_present(monkeypatch)
    pdf = _page(_flate_inverted)
    img = pdf.pages[0].Resources.XObject.Im0
    change(img)
    assert guard._is_inverted_bitonal(img)
    before = (img.read_raw_bytes(), dict(img.items()))
    assert jbig2_encode_inverted_bitonal(pdf) == 0
    assert (img.read_raw_bytes(), dict(img.items())) == before


def test_an_undecodable_inverted_image_without_jbig2dec_is_left_alone(monkeypatch):
    # Without jbig2dec, pikepdf reports an unfilterable stream as DependencyError.
    import pikepdf.jbig2

    monkeypatch.setattr(pikepdf.jbig2.get_decoder(), "available", lambda: False)
    _fake_binary_present(monkeypatch)
    pdf = _page(_flate_inverted)
    img = pdf.pages[0].Resources.XObject.Im0
    img.Filter = pikepdf.Name.DCTDecode
    before = (img.read_raw_bytes(), dict(img.items()))
    assert jbig2_encode_inverted_bitonal(pdf) == 0
    assert (img.read_raw_bytes(), dict(img.items())) == before


def test_bitonal_image_with_no_colour_space_is_left_alone(monkeypatch):
    _fake_binary_present(monkeypatch)
    pdf = _page(_flate_inverted)
    img = pdf.pages[0].Resources.XObject.Im0
    del img["/ColorSpace"]
    assert guard._is_inverted_bitonal(img)
    before = (img.read_raw_bytes(), dict(img.items()))
    assert jbig2_encode_inverted_bitonal(pdf) == 0
    assert (img.read_raw_bytes(), dict(img.items())) == before


# --- absent-encoder and not-smaller branches, monkeypatched ---


def test_no_jbig2_binary_leaves_images_untouched(monkeypatch):
    monkeypatch.setattr(codecs, "jbig2_binary", lambda: None)
    _fail_if_reencoded(monkeypatch)
    pdf = _page(_flate_inverted)
    img = pdf.pages[0].Resources.XObject.Im0
    before = (img.read_raw_bytes(), dict(img.items()))
    assert jbig2_encode_inverted_bitonal(pdf) == 0
    assert (img.read_raw_bytes(), dict(img.items())) == before


def test_a_jbig2_stream_that_is_not_smaller_is_rejected(monkeypatch):
    _fake_binary_present(monkeypatch)
    pdf = _page(_flate_inverted)
    img = pdf.pages[0].Resources.XObject.Im0
    original = img.read_raw_bytes()
    monkeypatch.setattr(guard, "_run_jbig2_generic", lambda exe, mask: b"x" * len(original))
    before = (original, dict(img.items()))
    assert jbig2_encode_inverted_bitonal(pdf) == 0
    assert (img.read_raw_bytes(), dict(img.items())) == before


def test_a_failed_encoder_invocation_is_rejected(monkeypatch):
    _fake_binary_present(monkeypatch)
    pdf = _page(_flate_inverted)
    img = pdf.pages[0].Resources.XObject.Im0
    before = (img.read_raw_bytes(), dict(img.items()))
    monkeypatch.setattr(guard, "_run_jbig2_generic", lambda exe, mask: None)
    assert jbig2_encode_inverted_bitonal(pdf) == 0
    assert (img.read_raw_bytes(), dict(img.items())) == before


@pytest.mark.parametrize("setup", [_flate_inverted, _ccitt_inverted])
def test_a_smaller_jbig2_stream_replaces_the_image(monkeypatch, setup):
    _fake_binary_present(monkeypatch)
    pdf = _page(setup)
    img = pdf.pages[0].Resources.XObject.Im0
    monkeypatch.setattr(guard, "_run_jbig2_generic", lambda exe, mask: b"x")
    assert jbig2_encode_inverted_bitonal(pdf) == 1
    assert img.Filter == pikepdf.Name.JBIG2Decode
    assert img.read_raw_bytes() == b"x"
    assert "/DecodeParms" not in img
    assert list(img.Decode) == [1, 0]


# --- _run_jbig2_generic itself ---


def test_run_jbig2_generic_returns_stdout_on_success(monkeypatch):
    class FakeResult:
        returncode = 0
        stdout = b"jbig2 bytes"

    monkeypatch.setattr(guard.subprocess, "run", lambda *a, **k: FakeResult())
    assert guard._run_jbig2_generic("jbig2", Image.new("1", (4, 4))) == b"jbig2 bytes"


def test_run_jbig2_generic_returns_none_on_nonzero_exit(monkeypatch):
    class FakeResult:
        returncode = 1
        stdout = b""

    monkeypatch.setattr(guard.subprocess, "run", lambda *a, **k: FakeResult())
    assert guard._run_jbig2_generic("jbig2", Image.new("1", (4, 4))) is None


def test_run_jbig2_generic_returns_none_on_empty_stdout(monkeypatch):
    class FakeResult:
        returncode = 0
        stdout = b""

    monkeypatch.setattr(guard.subprocess, "run", lambda *a, **k: FakeResult())
    assert guard._run_jbig2_generic("jbig2", Image.new("1", (4, 4))) is None


def test_run_jbig2_generic_returns_none_on_subprocess_failure(monkeypatch):
    def raise_oserror(*a, **k):
        raise OSError("no such binary")

    monkeypatch.setattr(guard.subprocess, "run", raise_oserror)
    assert guard._run_jbig2_generic("jbig2", Image.new("1", (4, 4))) is None


def test_restore_puts_back_an_image_that_grew_and_keeps_one_that_shrank():
    pdf = pikepdf.new()
    grew = pdf.make_stream(
        zlib.compress(b"x" * 100),
        Type=pikepdf.Name.XObject,
        Subtype=pikepdf.Name.Image,
        Width=1,
        Filter=pikepdf.Name.FlateDecode,  # restored by write(), not key by key
    )
    shrank = pdf.make_stream(
        b"y" * 100, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image, Width=2
    )
    grew_bytes, grew_items = grew.read_raw_bytes(), dict(grew.items())
    snapshot = snapshot_images(pdf)

    grew.write(b"z" * 500, filter=pikepdf.Name.FlateDecode)
    grew.Extra = 1
    del grew["/Width"]
    shrank.write(b"w" * 10)

    assert restore_unless_smaller(pdf, snapshot) == 1
    assert grew.read_raw_bytes() == grew_bytes
    assert {k: v for k, v in grew.items() if k != "/Length"} == {
        k: v for k, v in grew_items.items() if k != "/Length"
    }
    assert shrank.read_raw_bytes() == b"w" * 10


# --- keep_faithful: pixels compared before and after, PSNR measured here ---


def _psnr(a, b) -> float:
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    mse = ((a - b) ** 2).mean()
    return float("inf") if mse == 0 else 10 * np.log10(255**2 / mse)


def _photo() -> Image.Image:
    rng = np.random.default_rng(2)
    coarse = rng.integers(0, 256, (6, 8, 3), dtype=np.uint8)
    smooth = np.asarray(Image.fromarray(coarse).resize((W, H), Image.BICUBIC), dtype=np.int16)
    return Image.fromarray(
        np.clip(smooth + rng.normal(0, 4, smooth.shape), 0, 255).astype(np.uint8)
    )


def _pdf_with_image(data: bytes, **entries):
    pdf = pikepdf.new()
    img = pdf.make_stream(data, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image, **entries)
    return pdf, img


def _photo_pdf():
    photo = _photo()
    pdf, img = _pdf_with_image(
        zlib.compress(photo.tobytes(), 9),
        Width=W,
        Height=H,
        BitsPerComponent=8,
        ColorSpace=pikepdf.Name.DeviceRGB,
        Filter=pikepdf.Name.FlateDecode,
    )
    return pdf, img, photo


def _jpeg(pil, quality) -> bytes:
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _reencode(img, data):
    img.write(data, filter=pikepdf.Name.DCTDecode)


@pytest.mark.parametrize("keep_faithful", [True, False])
def test_a_damaging_reencode_is_restored_only_when_asked(keep_faithful):
    pdf, img, photo = _photo_pdf()
    original = img.read_raw_bytes()
    damaged = _jpeg(photo, 2)
    assert len(damaged) < len(original)
    assert _psnr(photo, Image.open(io.BytesIO(damaged)).convert("RGB")) < 30
    snapshot = snapshot_images(pdf)
    _reencode(img, damaged)
    kept = restore_unless_smaller(pdf, snapshot, keep_faithful=keep_faithful)
    assert kept == (0 if keep_faithful else 1)
    assert img.read_raw_bytes() == (original if keep_faithful else damaged)
    assert img.Filter == (pikepdf.Name.FlateDecode if keep_faithful else pikepdf.Name.DCTDecode)


def test_a_faithful_reencode_is_kept():
    pdf, img, photo = _photo_pdf()
    good = _jpeg(photo, 90)
    assert len(good) < len(img.read_raw_bytes())
    assert _psnr(photo, Image.open(io.BytesIO(good)).convert("RGB")) >= 30
    snapshot = snapshot_images(pdf)
    _reencode(img, good)
    assert restore_unless_smaller(pdf, snapshot, keep_faithful=True) == 1
    assert img.read_raw_bytes() == good
    assert img.Filter == pikepdf.Name.DCTDecode


def test_an_undecodable_reencode_is_restored():
    pdf, img, _ = _photo_pdf()
    original = img.read_raw_bytes()
    snapshot = snapshot_images(pdf)
    _reencode(img, b"not a jpeg")
    assert restore_unless_smaller(pdf, snapshot, keep_faithful=True) == 0
    assert img.read_raw_bytes() == original


def test_a_resized_reencode_is_restored():
    pdf, img, photo = _photo_pdf()
    original = img.read_raw_bytes()
    snapshot = snapshot_images(pdf)
    _reencode(img, _jpeg(photo.resize((W // 2, H // 2)), 95))
    img.Width, img.Height = W // 2, H // 2
    assert restore_unless_smaller(pdf, snapshot, keep_faithful=True) == 0
    assert img.read_raw_bytes() == original
    assert (int(img.Width), int(img.Height)) == (W, H)


def _stripes(offset: int) -> bytes:
    values = np.array([50, 100, 150, 200], dtype=np.uint8) + offset
    return np.repeat(values, W * H // 4).tobytes()


@pytest.mark.parametrize("palette", [True, False])
def test_the_palette_floor_applies_to_palette_images(palette):
    # Every sample off by 9: 29.0 dB, above the palette floor and below the other.
    assert 27 < 20 * np.log10(255 / 9) < 30
    pdf, img = _pdf_with_image(
        _stripes(0), Width=W, Height=H, BitsPerComponent=8, ColorSpace=pikepdf.Name.DeviceGray
    )
    snapshot = snapshot_images(pdf)
    if palette:
        lookup = bytes(v + 9 for v in range(247)) + bytes(9)
        indexed = pikepdf.Array([pikepdf.Name.Indexed, pikepdf.Name.DeviceGray, 255, lookup])
        img.write(zlib.compress(_stripes(0), 9), filter=pikepdf.Name.FlateDecode)
        img.ColorSpace = indexed
    else:
        img.write(zlib.compress(_stripes(9), 9), filter=pikepdf.Name.FlateDecode)
    assert restore_unless_smaller(pdf, snapshot, keep_faithful=True) == (1 if palette else 0)


def test_an_unchanged_image_is_not_decoded(monkeypatch):
    import pdftl.operations.helpers.image_guard as guard

    pdf, _, _ = _photo_pdf()
    snapshot = snapshot_images(pdf)
    monkeypatch.setattr(guard, "_stays_faithful", lambda *a: pytest.fail("decoded"))
    assert restore_unless_smaller(pdf, snapshot, keep_faithful=True) == 0  # not smaller
