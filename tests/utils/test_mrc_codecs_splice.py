# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/utils/test_mrc_codecs_splice.py

import io
import subprocess
import zlib

import numpy as np
import pikepdf
import pypdfium2 as pdfium
import pytest
from PIL import Image

import pdftl.utils.images.pil_to_pdf as pil_to_pdf
import pdftl.utils.page_images as page_images
from pdftl.utils.mrc import MrcError, codecs, splice
from pdftl.utils.mrc.classify import Candidate

# --- helpers ---------------------------------------------------------------


def _mask(ink: np.ndarray) -> Image.Image:
    """Mode-"1" mask with 0 where `ink` is True."""
    return Image.fromarray(np.logical_not(ink))


def _lines_ink(h=64, w=64) -> np.ndarray:
    ink = np.zeros((h, w), dtype=bool)
    ink[10:20, 5:60] = True
    ink[30:34, 8:40] = True
    ink[50:60, 50:55] = True
    return ink


def _noise_ink(h=64, w=64) -> np.ndarray:
    return np.random.default_rng(0).random((h, w)) < 0.5


def _render(pdf, index=0) -> np.ndarray:
    """Render a page at 72 dpi with pypdfium2, returning an RGB array."""
    buf = io.BytesIO()
    pdf.save(buf)
    doc = pdfium.PdfDocument(buf.getvalue())
    try:
        img = doc[index].render(scale=1.0).to_pil().convert("RGB")
    finally:
        doc.close()
    return np.asarray(img)


def _render_stencil(stencil: codecs.StencilStream) -> np.ndarray:
    """Ink array obtained by painting `stencil` black on white at 1:1."""
    pdf = pikepdf.Pdf.new()
    w, h = stencil.width, stencil.height
    xobj = codecs.make_stencil_xobject(pdf, stencil)
    pdf.add_blank_page(page_size=(w, h))
    page = pdf.pages[0]
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(S=xobj))
    page.Contents = pdf.make_stream(f"0 g q {w} 0 0 {h} 0 0 cm /S Do Q".encode())
    rgb = _render(pdf)
    return rgb.mean(axis=2) < 128


class _Result:
    def __init__(self, returncode, stdout, stderr):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


@pytest.fixture
def no_jbig2(monkeypatch):
    monkeypatch.setattr(codecs, "jbig2_binary", lambda: None)


@pytest.fixture
def fake_jbig2(monkeypatch):
    """Pretend a jbig2 binary exists; tests install their own subprocess.run."""
    monkeypatch.setattr(codecs, "jbig2_binary", lambda: "/fake/jbig2")


# --- jbig2_binary ----------------------------------------------------------


class TestJbig2Binary:
    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        codecs.jbig2_binary.cache_clear()
        yield
        codecs.jbig2_binary.cache_clear()

    def test_first_name_found(self, monkeypatch):
        seen = []

        def which(name):
            seen.append(name)
            return "/opt/bin/jbig2" if name == "jbig2" else None

        monkeypatch.setattr(codecs.shutil, "which", which)
        assert codecs.jbig2_binary() == "/opt/bin/jbig2"
        assert seen == ["jbig2"]

    def test_falls_through_to_second_name(self, monkeypatch):
        seen = []

        def which(name):
            seen.append(name)
            return "/opt/bin/jbig2enc" if name == "jbig2enc" else None

        monkeypatch.setattr(codecs.shutil, "which", which)
        assert codecs.jbig2_binary() == "/opt/bin/jbig2enc"
        assert seen == ["jbig2", "jbig2enc"]

    def test_none_found(self, monkeypatch):
        monkeypatch.setattr(codecs.shutil, "which", lambda name: None)
        assert codecs.jbig2_binary() is None

    def test_result_is_cached(self, monkeypatch):
        calls = []
        monkeypatch.setattr(codecs.shutil, "which", lambda name: calls.append(name) or "/x")
        codecs.jbig2_binary()
        codecs.jbig2_binary()
        assert calls == ["jbig2"]


# --- mask_ink_fraction -----------------------------------------------------


class TestMaskInkFraction:
    @pytest.mark.parametrize("ink", [_lines_ink(), _noise_ink(), np.zeros((7, 5), bool)])
    def test_matches_pixel_count(self, ink):
        assert codecs.mask_ink_fraction(_mask(ink)) == pytest.approx(ink.sum() / ink.size)

    def test_all_ink(self):
        assert codecs.mask_ink_fraction(_mask(np.ones((3, 4), bool))) == 1.0


# --- _run_jbig2_generic ----------------------------------------------------


class TestRunJbig2Generic:
    def test_passes_png_of_mask_and_returns_stdout(self, monkeypatch):
        ink = _lines_ink()
        captured = {}

        def run(argv, **kwargs):
            captured["argv"] = list(argv)
            captured["kwargs"] = kwargs
            with Image.open(argv[2]) as im:
                captured["png"] = np.asarray(im.convert("1"))
                captured["format"] = im.format
            return _Result(0, b"JB2DATA", b"")

        monkeypatch.setattr(codecs.subprocess, "run", run)
        assert codecs._run_jbig2_generic("/fake/jbig2", _mask(ink)) == b"JB2DATA"
        assert captured["argv"][:2] == ["/fake/jbig2", "-p"]
        assert len(captured["argv"]) == 3
        assert "-r" not in captured["argv"]
        assert captured["format"] == "PNG"
        assert np.array_equal(~captured["png"], ink)
        assert captured["kwargs"]["capture_output"] is True
        assert captured["kwargs"]["check"] is False
        assert captured["kwargs"]["timeout"] == 120

    def test_nonzero_exit_reports_stderr(self, monkeypatch):
        monkeypatch.setattr(
            codecs.subprocess, "run", lambda *a, **k: _Result(3, b"x", b"  bad input \n")
        )
        with pytest.raises(MrcError, match=r"^the JBIG2 encoder failed: bad input$"):
            codecs._run_jbig2_generic("/fake/jbig2", _mask(_lines_ink()))

    def test_empty_output_without_stderr(self, monkeypatch):
        monkeypatch.setattr(codecs.subprocess, "run", lambda *a, **k: _Result(0, b"", None))
        with pytest.raises(MrcError, match=r"^the JBIG2 encoder failed: no output$"):
            codecs._run_jbig2_generic("/fake/jbig2", _mask(_lines_ink()))


# --- _verify_stencil_bytes -------------------------------------------------


def _packed(ink: np.ndarray, *, invert=False) -> bytes:
    """Row-padded 1-bit samples, 0 where inked (or 1 if `invert`)."""
    samples = ink if invert else ~ink
    return np.packbits(samples, axis=1).tobytes()


class TestVerifyStencilBytes:
    def test_natural_polarity(self):
        ink = _lines_ink()
        data = zlib.compress(_packed(ink))
        ok, decode = codecs._verify_stencil_bytes(data, "/FlateDecode", None, 64, 64, ink.mean())
        assert (ok, decode) == (True, None)

    def test_inverted_polarity(self):
        ink = _lines_ink()
        data = zlib.compress(_packed(ink, invert=True))
        ok, decode = codecs._verify_stencil_bytes(data, "/FlateDecode", None, 64, 64, ink.mean())
        assert (ok, decode) == (True, (1, 0))

    def test_neither_polarity_matches(self):
        # 50% ink renders as 50% either way; claim 10% instead.
        ink = np.zeros((64, 64), bool)
        ink[:, :32] = True
        data = zlib.compress(_packed(ink))
        assert codecs._verify_stencil_bytes(data, "/FlateDecode", None, 64, 64, 0.1) == (
            False,
            None,
        )

    def test_decode_parms_are_applied(self):
        # CCITT with BlackIs1=true stored the other way round would fail.
        ink = _lines_ink()
        data, filt, parms = pil_to_pdf.get_optimal_1bit_payload(_mask(ink))
        assert filt == "/CCITTFaxDecode"
        bare = {str(k).lstrip("/"): v for k, v in dict(parms).items()}
        ok, decode = codecs._verify_stencil_bytes(data, filt, bare, 64, 64, ink.mean())
        assert (ok, decode) == (True, None)

    def test_render_error_counts_as_no_match(self, monkeypatch):
        def boom(*a, **k):
            raise pdfium.PdfiumError("render failed")

        monkeypatch.setattr(page_images, "render_page_to_pil", boom)
        data = zlib.compress(_packed(_lines_ink()))
        assert codecs._verify_stencil_bytes(data, "/FlateDecode", None, 64, 64, 0.2) == (
            False,
            None,
        )

    def test_render_error_on_first_polarity_tries_second(self, monkeypatch):
        real = page_images.render_page_to_pil
        calls = []

        def flaky(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                raise pdfium.PdfiumError("render failed")
            return real(*a, **k)

        monkeypatch.setattr(page_images, "render_page_to_pil", flaky)
        ink = _lines_ink()
        data = zlib.compress(_packed(ink, invert=True))
        ok, decode = codecs._verify_stencil_bytes(data, "/FlateDecode", None, 64, 64, ink.mean())
        assert (ok, decode) == (True, (1, 0))
        assert len(calls) == 2


# --- encode_stencil --------------------------------------------------------


class TestEncodeStencil:
    def test_rejects_non_bilevel(self):
        with pytest.raises(ValueError, match="mode '1', got 'L'"):
            codecs.encode_stencil(Image.new("L", (4, 4)))

    @pytest.mark.parametrize(
        ("ink", "codec", "filt"),
        [
            (_lines_ink(), "ccitt_g4", "/CCITTFaxDecode"),
            (_noise_ink(), "flate", "/FlateDecode"),
        ],
    )
    def test_native_round_trip_is_pixel_exact(self, no_jbig2, ink, codec, filt):
        stencil = codecs.encode_stencil(_mask(ink))
        assert (stencil.codec, stencil.filter_name) == (codec, filt)
        assert (stencil.width, stencil.height) == (64, 64)
        assert np.array_equal(_render_stencil(stencil), ink)

    def test_ccitt_parms_have_bare_keys(self, no_jbig2):
        stencil = codecs.encode_stencil(_mask(_lines_ink(40, 72)))
        assert stencil.decode_parms == {"K": -1, "Columns": 72, "Rows": 40, "BlackIs1": False}

    def test_flate_samples_decode_to_mask(self, no_jbig2):
        ink = _noise_ink(30, 21)
        stencil = codecs.encode_stencil(_mask(ink))
        assert stencil.codec == "flate"
        assert stencil.decode_parms is None
        raw = zlib.decompress(stencil.data)
        samples = np.unpackbits(np.frombuffer(raw, np.uint8).reshape(30, -1), axis=1)[:, :21]
        painted = samples == (1 if stencil.decode == (1, 0) else 0)
        assert np.array_equal(painted, ink)

    def test_inverted_native_payload_gets_decode_array(self, no_jbig2, monkeypatch):
        ink = _lines_ink()
        payload = zlib.compress(_packed(ink, invert=True))
        monkeypatch.setattr(
            pil_to_pdf, "get_optimal_1bit_payload", lambda m: (payload, "/FlateDecode", None)
        )
        stencil = codecs.encode_stencil(_mask(ink))
        assert stencil.decode == (1, 0)
        assert stencil.codec == "flate"
        assert stencil.data == payload
        assert np.array_equal(_render_stencil(stencil), ink)

    def test_native_verification_failure_raises(self, no_jbig2, monkeypatch):
        # A blank payload cannot match a mask that is half ink.
        ink = np.zeros((64, 64), bool)
        ink[:, :16] = True
        payload = zlib.compress(np.full((64, 8), 0x55, np.uint8).tobytes())
        monkeypatch.setattr(
            pil_to_pdf, "get_optimal_1bit_payload", lambda m: (payload, "/FlateDecode", None)
        )
        with pytest.raises(MrcError, match="verification failed"):
            codecs.encode_stencil(_mask(ink))

    def test_jbig2_output_accepted_when_it_verifies(self, fake_jbig2, monkeypatch):
        import shutil

        real = next(filter(None, map(shutil.which, codecs._JBIG2_BINARY_NAMES)), None)
        if real is None:
            pytest.skip("no jbig2enc-family binary available")
        orig_run = subprocess.run

        def run(argv, **kwargs):
            return orig_run([real, *argv[1:]], **kwargs)

        monkeypatch.setattr(codecs.subprocess, "run", run)
        ink = _lines_ink()
        stencil = codecs.encode_stencil(_mask(ink))
        assert (stencil.codec, stencil.filter_name) == ("jbig2_generic", "/JBIG2Decode")
        assert stencil.decode_parms is None
        assert np.array_equal(_render_stencil(stencil), ink)

    def test_jbig2_accepted_path_without_real_binary(self, fake_jbig2, monkeypatch):
        monkeypatch.setattr(codecs, "_run_jbig2_generic", lambda exe, m: b"JB2")
        monkeypatch.setattr(codecs, "_verify_stencil_bytes", lambda *a, **k: (True, (1, 0)))
        stencil = codecs.encode_stencil(_mask(_lines_ink(5, 7)))
        assert stencil == codecs.StencilStream(
            data=b"JB2",
            codec="jbig2_generic",
            filter_name="/JBIG2Decode",
            width=7,
            height=5,
            decode=(1, 0),
            decode_parms=None,
        )

    def test_failing_jbig2_falls_back_to_native(self, fake_jbig2, monkeypatch):
        monkeypatch.setattr(codecs.subprocess, "run", lambda *a, **k: _Result(1, b"", b"segfault"))
        ink = _lines_ink()
        stencil = codecs.encode_stencil(_mask(ink))
        assert stencil.codec == "ccitt_g4"
        assert np.array_equal(_render_stencil(stencil), ink)

    def test_unverifiable_jbig2_falls_back_to_native(self, fake_jbig2, monkeypatch):
        monkeypatch.setattr(
            codecs.subprocess, "run", lambda *a, **k: _Result(0, b"\x00not jbig2\xff", b"")
        )
        ink = _lines_ink()
        stencil = codecs.encode_stencil(_mask(ink))
        assert stencil.codec == "ccitt_g4"
        assert np.array_equal(_render_stencil(stencil), ink)


# --- JPEG layers -----------------------------------------------------------


def _gradient_rgb(w=40, h=30) -> Image.Image:
    arr = np.zeros((h, w, 3), np.uint8)
    arr[..., 0] = np.linspace(0, 255, w, dtype=np.uint8)[None, :]
    arr[..., 1] = np.linspace(0, 255, h, dtype=np.uint8)[:, None]
    arr[..., 2] = 128
    return Image.fromarray(arr)


class TestJpegLayers:
    def test_colour_space(self):
        assert codecs.colour_space(True) == pikepdf.Name.DeviceGray
        assert codecs.colour_space(False) == pikepdf.Name.DeviceRGB

    @pytest.mark.parametrize(("gray", "mode"), [(False, "RGB"), (True, "L")])
    def test_foreground(self, gray, mode):
        src = _gradient_rgb()
        data = codecs.encode_foreground_jpeg(src, gray=gray)
        with Image.open(io.BytesIO(data)) as im:
            assert (im.format, im.mode, im.size) == ("JPEG", mode, (40, 30))
            assert "progressive" not in im.info
            expected = np.asarray(src.convert(mode), float)
            assert np.abs(np.asarray(im, float) - expected).mean() < 8

    def test_quality_changes_size(self):
        noisy = Image.fromarray(np.random.default_rng(1).integers(0, 256, (64, 64, 3), np.uint8))
        lo = codecs.encode_foreground_jpeg(noisy, 10)
        hi = codecs.encode_foreground_jpeg(noisy, 95)
        assert len(lo) < len(hi)
        assert codecs.encode_foreground_jpeg(noisy) == codecs.encode_foreground_jpeg(
            noisy, codecs.DEFAULT_FG_QUALITY
        )

    @pytest.mark.parametrize(
        ("gray", "mode", "cs"), [(False, "RGB", "/DeviceRGB"), (True, "L", "/DeviceGray")]
    )
    def test_background(self, gray, mode, cs):
        src = _gradient_rgb()
        data, filt, extra = codecs.encode_background(src, gray=gray)
        assert filt == "/DCTDecode"
        assert extra == {"ColorSpace": pikepdf.Name(cs), "BitsPerComponent": 8}
        with Image.open(io.BytesIO(data)) as im:
            assert (im.format, im.mode, im.size) == ("JPEG", mode, (40, 30))
        assert data == codecs.encode_background(src, codecs.DEFAULT_BG_JPEG_QUALITY, gray=gray)[0]


# --- XObject builders ------------------------------------------------------


class TestXObjects:
    def test_image_xobject_keys(self):
        pdf = pikepdf.Pdf.new()
        x = codecs.make_image_xobject(
            pdf, b"abc", 3.0, 2, "/DCTDecode", ColorSpace=pikepdf.Name.DeviceRGB, Foo=5
        )
        assert x.is_indirect
        assert sorted(x.keys()) == [
            "/ColorSpace",
            "/Filter",
            "/Foo",
            "/Height",
            "/Length",
            "/Subtype",
            "/Type",
            "/Width",
        ]
        assert x.Type == "/XObject" and x.Subtype == "/Image"
        assert x.Width == 3 and isinstance(x.Width, int)
        assert x.Height == 2 and x.Filter == "/DCTDecode" and x.Foo == 5
        assert x.read_raw_bytes() == b"abc"

    def _stencil(self, decode, parms):
        return codecs.StencilStream(b"xyz", "flate", "/FlateDecode", 9, 4, decode, parms)

    def test_stencil_minimal(self):
        pdf = pikepdf.Pdf.new()
        x = codecs.make_stencil_xobject(pdf, self._stencil(None, None))
        assert x.is_indirect
        assert sorted(x.keys()) == [
            "/Filter",
            "/Height",
            "/ImageMask",
            "/Interpolate",
            "/Length",
            "/Subtype",
            "/Type",
            "/Width",
        ]
        assert x.ImageMask is True and x.Interpolate is True
        assert (x.Width, x.Height, x.Filter) == (9, 4, "/FlateDecode")
        assert x.read_raw_bytes() == b"xyz"

    def test_stencil_empty_parms_omitted(self):
        pdf = pikepdf.Pdf.new()
        x = codecs.make_stencil_xobject(pdf, self._stencil(None, {}))
        assert "/DecodeParms" not in x

    def test_stencil_with_decode_and_parms(self):
        pdf = pikepdf.Pdf.new()
        parms = {"K": -1, "Columns": 9, "Rows": 4, "BlackIs1": False}
        x = codecs.make_stencil_xobject(pdf, self._stencil((1, 0), parms))
        assert list(x.Decode) == [1, 0]
        assert sorted(x.DecodeParms.keys()) == ["/BlackIs1", "/Columns", "/K", "/Rows"]
        assert x.DecodeParms.K == -1 and x.DecodeParms.Columns == 9
        assert x.DecodeParms.Rows == 4 and x.DecodeParms.BlackIs1 is False


# --- splice ----------------------------------------------------------------


def _solid_image(pdf, rgb, w=4, h=4, **extra):
    data = bytes(rgb) * (w * h)
    x = pikepdf.Stream(pdf, data)
    x.Type, x.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    x.Width, x.Height, x.BitsPerComponent = w, h, 8
    x.ColorSpace = pikepdf.Name.DeviceRGB
    for k, v in extra.items():
        x["/" + k] = v
    return pdf.make_indirect(x)


def _candidate(xobj=None, name="/Im0", page_number=1):
    return Candidate(
        page_number=page_number,
        xobj=xobj,
        xobj_name=name,
        rect=(0, 0, 1, 1),
        matrix=(1, 0, 0, 1, 0, 0),
        width=4,
        height=4,
        source_dpi=72,
    )


def _ops(page):
    return [
        (str(op), [str(o) if isinstance(o, pikepdf.Name) else float(o) for o in operands])
        for operands, op in pikepdf.parse_content_stream(page)
    ]


CONTENT = b"q 50 0 0 40 10 20 cm /Im0 Do Q 0 0 1 rg 0 0 5 5 re f"


def _page_with_image(pdf, content=CONTENT):
    scan = _solid_image(pdf, (255, 0, 0))
    pdf.add_blank_page(page_size=(100, 100))
    page = pdf.pages[-1]
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=scan))
    page.Contents = pdf.make_stream(content)
    return page, scan


class TestSplice:
    def test_replaces_do_and_rewrites_resources(self):
        pdf = pikepdf.Pdf.new()
        page, scan = _page_with_image(pdf)
        old_xobjects = page.Resources.XObject
        bg = _solid_image(pdf, (0, 0, 255))
        fg = _solid_image(pdf, (0, 255, 0))
        splice.splice_layers(pdf, page, _candidate(scan), bg, fg)

        assert _ops(page) == [
            ("q", []),
            ("cm", [50.0, 0.0, 0.0, 40.0, 10.0, 20.0]),
            ("Do", ["/MRCBg0"]),
            ("Do", ["/MRCFg0"]),
            ("Q", []),
            ("rg", [0.0, 0.0, 1.0]),
            ("re", [0.0, 0.0, 5.0, 5.0]),
            ("f", []),
        ]
        xo = page.Resources.XObject
        assert sorted(xo.keys()) == ["/MRCBg0", "/MRCFg0"]
        assert xo.MRCBg0.objgen == bg.objgen and xo.MRCFg0.objgen == fg.objgen
        assert xo.is_indirect
        assert sorted(old_xobjects.keys()) == ["/Im0"]

    def test_rendered_result(self):
        # fg is painted through a stencil that covers only its left half.
        pdf = pikepdf.Pdf.new()
        page, scan = _page_with_image(pdf)
        bg = _solid_image(pdf, (0, 0, 255))
        mask_bits = np.zeros((4, 4), bool)
        mask_bits[:, 2:] = True  # 1 = masked out
        stencil = pikepdf.Stream(pdf, np.packbits(mask_bits, axis=1).tobytes())
        stencil.Type, stencil.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
        stencil.Width, stencil.Height = 4, 4
        stencil.ImageMask = True
        fg = _solid_image(pdf, (0, 255, 0), Mask=pdf.make_indirect(stencil))
        splice.splice_layers(pdf, page, _candidate(scan), bg, fg)

        img = _render(pdf)
        assert img.shape == (100, 100, 3)
        # Placement is x 10..60, y 20..60 in PDF space; row = 100 - y.
        assert tuple(img[60, 20]) == (0, 255, 0)
        assert tuple(img[60, 50]) == (0, 0, 255)
        assert tuple(img[20, 20]) == (255, 255, 255)  # y=80: above the placement
        assert tuple(img[70, 70]) == (255, 255, 255)
        assert tuple(img[97, 2]) == (0, 0, 255)
        assert not (img == (255, 0, 0)).all(axis=2).any()

    def test_only_first_do_replaced(self):
        pdf = pikepdf.Pdf.new()
        page, scan = _page_with_image(pdf, b"/Im0 Do /Im0 Do")
        splice.splice_layers(pdf, page, _candidate(scan), scan, scan)
        assert _ops(page) == [
            ("Do", ["/MRCBg0"]),
            ("Do", ["/MRCFg0"]),
            ("Do", ["/Im0"]),
        ]

    def test_other_do_and_bare_do_kept(self):
        pdf = pikepdf.Pdf.new()
        page, scan = _page_with_image(pdf, b"/Other Do Do /Im0 Do")
        page.Resources.XObject.Other = scan
        splice.splice_layers(pdf, page, _candidate(scan), scan, scan)
        assert _ops(page) == [
            ("Do", ["/Other"]),
            ("Do", []),
            ("Do", ["/MRCBg0"]),
            ("Do", ["/MRCFg0"]),
        ]
        assert sorted(page.Resources.XObject.keys()) == ["/MRCBg0", "/MRCFg0", "/Other"]

    def test_fresh_names_skip_existing(self):
        pdf = pikepdf.Pdf.new()
        page, scan = _page_with_image(pdf)
        xo = page.Resources.XObject
        xo.MRCBg0 = scan
        xo.MRCBg1 = scan
        xo.MRCFg0 = scan
        splice.splice_layers(pdf, page, _candidate(scan), scan, scan)
        assert ("Do", ["/MRCBg2"]) in _ops(page)
        assert ("Do", ["/MRCFg1"]) in _ops(page)
        assert sorted(page.Resources.XObject.keys()) == [
            "/MRCBg0",
            "/MRCBg1",
            "/MRCBg2",
            "/MRCFg0",
            "/MRCFg1",
        ]

    def test_missing_placement_raises(self):
        pdf = pikepdf.Pdf.new()
        page, scan = _page_with_image(pdf, b"/Im1 Do")
        with pytest.raises(MrcError, match=r"'/Im0' in page 7's content stream"):
            splice.splice_layers(pdf, page, _candidate(scan, page_number=7), scan, scan)

    def test_inherited_resources_copied_onto_page(self):
        pdf = pikepdf.Pdf.new()
        scan = _solid_image(pdf, (255, 0, 0))
        pdf.add_blank_page(page_size=(100, 100))
        pdf.add_blank_page(page_size=(100, 100))
        for p in pdf.pages:
            del p.obj["/Resources"]
            p.Contents = pdf.make_stream(b"/Im0 Do")
        parent = pdf.pages[0].obj.Parent
        parent.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=scan))

        page = pdf.pages[0]
        splice.splice_layers(pdf, page, _candidate(scan), scan, scan)

        assert sorted(page.obj.Resources.XObject.keys()) == ["/MRCBg0", "/MRCFg0"]
        assert sorted(parent.Resources.XObject.keys()) == ["/Im0"]
        assert "/Resources" not in pdf.pages[1].obj

    def test_no_resources_anywhere(self):
        # Do names an image that is not in any resource dict.
        pdf = pikepdf.Pdf.new()
        pdf.add_blank_page(page_size=(100, 100))
        page = pdf.pages[0]
        del page.obj["/Resources"]
        page.Contents = pdf.make_stream(b"/Im0 Do")
        bg = _solid_image(pdf, (0, 0, 255))
        splice.splice_layers(pdf, page, _candidate(), bg, bg)
        assert _ops(page) == [("Do", ["/MRCBg0"]), ("Do", ["/MRCFg0"])]
        assert sorted(page.obj.Resources.keys()) == ["/XObject"]
        assert sorted(page.obj.Resources.XObject.keys()) == ["/MRCBg0", "/MRCFg0"]

    def test_resources_without_xobject(self):
        pdf = pikepdf.Pdf.new()
        pdf.add_blank_page(page_size=(100, 100))
        page = pdf.pages[0]
        page.Resources = pikepdf.Dictionary(ProcSet=pikepdf.Array([pikepdf.Name.PDF]))
        page.Contents = pdf.make_stream(b"/Im0 Do")
        bg = _solid_image(pdf, (0, 0, 255))
        splice.splice_layers(pdf, page, _candidate(), bg, bg)
        assert sorted(page.Resources.keys()) == ["/ProcSet", "/XObject"]
        assert sorted(page.Resources.XObject.keys()) == ["/MRCBg0", "/MRCFg0"]

    def test_shared_xobject_dict_not_mutated(self):
        pdf = pikepdf.Pdf.new()
        scan = _solid_image(pdf, (255, 0, 0))
        shared = pdf.make_indirect(pikepdf.Dictionary(Im0=scan))
        for _ in range(2):
            pdf.add_blank_page(page_size=(100, 100))
            p = pdf.pages[-1]
            p.Resources = pikepdf.Dictionary(XObject=shared)
            p.Contents = pdf.make_stream(b"/Im0 Do")
        splice.splice_layers(pdf, pdf.pages[0], _candidate(scan), scan, scan)
        assert sorted(shared.keys()) == ["/Im0"]
        assert pdf.pages[1].Resources.XObject.objgen == shared.objgen
