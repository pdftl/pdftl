# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/operations/test_mrc_compress.py

import io

import numpy as np
import pikepdf
import pytest
from PIL import Image, ImageDraw

from pdftl.operations.mrc_compress import mrc_compress
from pdftl.utils.mrc import MrcError, classify, codecs, segmentation
from pdftl.utils.page_images import render_page_to_pil

PAGE_W, PAGE_H = 612, 792
IMG_W, IMG_H = 850, 1100


def _scan_jpeg_bytes() -> bytes:
    img = Image.new("RGB", (IMG_W, IMG_H), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    for y in range(80, IMG_H - 80, 40):
        draw.rectangle([80, y, IMG_W - 80, y + 14], fill=(20, 20, 20))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _add_scan_page(pdf, *, visible_text=False, invisible_ocr=False):
    """Append a full-page scanned-image page to `pdf`, returning nothing --
    the new page is `pdf.pages[-1]`."""
    xobj = pikepdf.Stream(pdf, _scan_jpeg_bytes())
    xobj.Type = pikepdf.Name("/XObject")
    xobj.Subtype = pikepdf.Name("/Image")
    xobj.Width = IMG_W
    xobj.Height = IMG_H
    xobj.ColorSpace = pikepdf.Name("/DeviceRGB")
    xobj.BitsPerComponent = 8
    xobj.Filter = pikepdf.Name("/DCTDecode")
    xobj_ref = pdf.make_indirect(xobj)

    resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=xobj_ref))
    content = f"q {PAGE_W} 0 0 {PAGE_H} 0 0 cm /Im0 Do Q\n".encode()
    if visible_text or invisible_ocr:
        font = pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/Font"),
                Subtype=pikepdf.Name("/Type1"),
                BaseFont=pikepdf.Name("/Helvetica"),
            )
        )
        resources["/Font"] = pikepdf.Dictionary(F1=font)
    if visible_text:
        content += b"BT /F1 12 Tf 100 700 Td (hello) Tj ET\n"
    if invisible_ocr:
        content += b"BT 3 Tr /F1 12 Tf 100 650 Td (invisible ocr text) Tj ET\n"

    page_dict = pikepdf.Dictionary(
        Type=pikepdf.Name("/Page"),
        MediaBox=[0, 0, PAGE_W, PAGE_H],
        Resources=resources,
        Contents=pdf.make_stream(content),
    )
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page_dict)))


@pytest.fixture
def scan_pdf():
    pdf = pikepdf.Pdf.new()
    yield pdf
    pdf.close()


class TestClassification:
    def test_plain_scan_qualifies(self, scan_pdf):
        _add_scan_page(scan_pdf)
        candidate, reason = classify.classify_page(scan_pdf, scan_pdf.pages[0], 1)
        assert candidate is not None, reason

    def test_scan_with_invisible_ocr_text_still_qualifies(self, scan_pdf):
        _add_scan_page(scan_pdf, invisible_ocr=True)
        candidate, reason = classify.classify_page(scan_pdf, scan_pdf.pages[0], 1)
        assert candidate is not None, reason

    def test_scan_with_visible_text_does_not_qualify(self, scan_pdf):
        _add_scan_page(scan_pdf, visible_text=True)
        candidate, reason = classify.classify_page(scan_pdf, scan_pdf.pages[0], 1)
        assert candidate is None

    def test_blank_page_does_not_qualify(self, scan_pdf):
        scan_pdf.add_blank_page(page_size=(PAGE_W, PAGE_H))
        candidate, reason = classify.classify_page(scan_pdf, scan_pdf.pages[0], 1)
        assert candidate is None


class TestSurvivalAndSize:
    def test_round_trip_shrinks_preserves_annotation_and_renders(self, scan_pdf):
        _add_scan_page(scan_pdf, invisible_ocr=True)
        page = scan_pdf.pages[0]
        annot = pikepdf.Dictionary(
            Type=pikepdf.Name("/Annot"),
            Subtype=pikepdf.Name("/Text"),
            Rect=[10, 10, 50, 50],
            Contents="note",
        )
        page.obj["/Annots"] = pikepdf.Array([scan_pdf.make_indirect(annot)])

        before = io.BytesIO()
        scan_pdf.save(before)
        size_before = before.tell()

        result = mrc_compress(scan_pdf, [])
        assert result.success
        assert result.data["pages_mrc"] == 1, result.data["pages"]

        after = io.BytesIO()
        scan_pdf.save(after)
        assert after.tell() < size_before

        annots = scan_pdf.pages[0].obj["/Annots"]
        assert str(annots[0]["/Contents"]) == "note"

        rendered = render_page_to_pil(scan_pdf, 0, dpi=72)
        assert rendered.size == (PAGE_W, PAGE_H)

    def test_non_eligible_page_left_byte_identical(self, scan_pdf):
        _add_scan_page(scan_pdf, invisible_ocr=True)  # page 1: eligible
        _add_scan_page(scan_pdf, visible_text=True)  # page 2: not eligible

        original_contents = bytes(scan_pdf.pages[1].obj["/Contents"].read_bytes())

        result = mrc_compress(scan_pdf, [])
        assert result.data["pages_mrc"] == 1
        assert result.data["pages_untouched"] == 1
        assert result.data["pages"][1]["decision"] == "untouched"

        assert bytes(scan_pdf.pages[1].obj["/Contents"].read_bytes()) == original_contents

    def test_no_eligible_pages_reports_zero_without_raising(self, scan_pdf):
        _add_scan_page(scan_pdf, visible_text=True)
        result = mrc_compress(scan_pdf, [])
        assert result.success
        assert result.data["pages_mrc"] == 0


class TestArgs:
    def test_bg_div_and_fg_div_are_accepted(self, scan_pdf):
        _add_scan_page(scan_pdf)
        result = mrc_compress(scan_pdf, ["bg_div=4", "fg_div=6"])
        assert result.data["pages_mrc"] == 1

    def test_supersample_scales_stencil_resolution(self, scan_pdf):
        _add_scan_page(scan_pdf)
        result = mrc_compress(scan_pdf, ["supersample=3"])
        assert result.data["pages_mrc"] == 1
        mask = pikepdf.PdfImage(
            scan_pdf.pages[0].obj["/Resources"]["/XObject"]["/MRCFg0"]["/Mask"]
        )
        assert mask.width == IMG_W * 3
        assert mask.height == IMG_H * 3

    def test_supersample_1_keeps_stencil_at_native_resolution(self, scan_pdf):
        _add_scan_page(scan_pdf)
        result = mrc_compress(scan_pdf, ["supersample=1"])
        assert result.data["pages_mrc"] == 1
        mask = pikepdf.PdfImage(
            scan_pdf.pages[0].obj["/Resources"]["/XObject"]["/MRCFg0"]["/Mask"]
        )
        assert mask.width == IMG_W
        assert mask.height == IMG_H

    def test_default_supersample_is_1_not_2(self, scan_pdf):
        """Regression test: supersample defaults to off (1), not the earlier
        default of 2. Supersampling measurably inflates stroke area on a
        source with real scan noise -- see DEFAULT_STENCIL_SUPERSAMPLE's
        docstring for the measurement -- so the default must not silently
        bolden every document's text without the user asking for it."""
        _add_scan_page(scan_pdf)
        result = mrc_compress(scan_pdf, [])  # no explicit supersample=
        assert result.data["pages_mrc"] == 1
        mask = pikepdf.PdfImage(
            scan_pdf.pages[0].obj["/Resources"]["/XObject"]["/MRCFg0"]["/Mask"]
        )
        assert mask.width == IMG_W
        assert mask.height == IMG_H

    def test_invalid_supersample_raises(self, scan_pdf):
        from pdftl.exceptions import InvalidArgumentError

        _add_scan_page(scan_pdf)
        with pytest.raises(InvalidArgumentError):
            mrc_compress(scan_pdf, ["supersample=5"])

    def test_invalid_divisor_raises(self, scan_pdf):
        from pdftl.exceptions import InvalidArgumentError

        _add_scan_page(scan_pdf)
        with pytest.raises(InvalidArgumentError):
            mrc_compress(scan_pdf, ["bg_div=0"])

    def test_ink_percentile_is_accepted_and_changes_the_flat_swatch(self, scan_pdf):
        _add_scan_page(scan_pdf)
        low = mrc_compress(scan_pdf, ["ink_percentile=1"])
        assert low.data["pages_mrc"] == 1
        swatch_low = (
            pikepdf.PdfImage(scan_pdf.pages[0].obj["/Resources"]["/XObject"]["/MRCFg0"])
            .as_pil_image()
            .getpixel((0, 0))
        )

        pdf2 = pikepdf.Pdf.new()
        _add_scan_page(pdf2)
        high = mrc_compress(pdf2, ["ink_percentile=100"])
        assert high.data["pages_mrc"] == 1
        swatch_high = (
            pikepdf.PdfImage(pdf2.pages[0].obj["/Resources"]["/XObject"]["/MRCFg0"])
            .as_pil_image()
            .getpixel((0, 0))
        )
        pdf2.close()

        # A lower percentile picks a darker (or equal) core-ink estimate.
        assert swatch_low[0] <= swatch_high[0]

    def test_default_ink_percentile_is_100_not_10(self, scan_pdf):
        """Regression test: ink_percentile defaults to 100 (unfiltered mean
        over the whole ink set, matching Spectra-PDF's own masked_mean),
        not the earlier default of 10. A smaller percentile picks a
        darker/purer ink core that reads as artificially bold next to an
        identical stencil shape -- see DEFAULT_INK_PERCENTILE's docstring
        for the empirical comparison against Spectra's own fixtures."""
        _add_scan_page(scan_pdf)
        default_result = mrc_compress(scan_pdf, [])  # no explicit ink_percentile=
        assert default_result.data["pages_mrc"] == 1
        swatch_default = (
            pikepdf.PdfImage(scan_pdf.pages[0].obj["/Resources"]["/XObject"]["/MRCFg0"])
            .as_pil_image()
            .getpixel((0, 0))
        )

        pdf2 = pikepdf.Pdf.new()
        _add_scan_page(pdf2)
        explicit_100 = mrc_compress(pdf2, ["ink_percentile=100"])
        assert explicit_100.data["pages_mrc"] == 1
        swatch_100 = (
            pikepdf.PdfImage(pdf2.pages[0].obj["/Resources"]["/XObject"]["/MRCFg0"])
            .as_pil_image()
            .getpixel((0, 0))
        )
        pdf2.close()

        assert swatch_default == swatch_100

    def test_invalid_ink_percentile_raises(self, scan_pdf):
        from pdftl.exceptions import InvalidArgumentError

        _add_scan_page(scan_pdf)
        with pytest.raises(InvalidArgumentError):
            mrc_compress(scan_pdf, ["ink_percentile=0"])


class TestForegroundEdgeSeam:
    """Regression tests for MRC_PORT_SCOPING.md fix 5: a pixel just outside
    the ink mask at a photo's edge (the anti-aliased blend toward paper)
    must keep its own true colour, not a fallback pulled from the photo's
    darker interior -- confirmed to cause a visible seam/"ringing" right
    at a photo's boundary, present even at fg_div=1 so not a JPEG artifact."""

    def test_pixel_past_the_true_edge_matches_source_not_fallback(self):
        """Verified to actually distinguish pre/post-fix behaviour: with
        `dilate_bool` forced to a no-op (simulating the pre-fix bare `ink`
        keep-set), this same construction gives fg[5, 65] = (75, 50, 74) --
        wrong hue, wrong direction from the true (124, 119, 109) -- while
        the real (dilating) code gives (140, 130, 135), close to true."""
        from pdftl.operations.mrc_compress import _build_foreground

        h, w = 10, 200
        rgb = np.full((h, w, 3), 255, dtype=np.uint8)
        ink = np.zeros((h, w), dtype=bool)
        # A "photo": alternating ink columns 0..58, each a different hue so
        # the foreground doesn't collapse to a single flat swatch (that
        # check is on chroma, not luminance).
        for x in range(0, 60, 2):
            ink[:, x] = True
            rgb[:, x] = (60 + (x % 40), 50, 90 - (x % 40))
        # Anti-aliased blend, columns 60..79 -- NOT classified as ink (a
        # real Sauvola threshold only catches a stroke/edge's darkest
        # core, not this fainter tail), true colour ramping to white.
        for i, x in enumerate(range(60, 80)):
            t = i / 19
            val = int(80 + t * (250 - 80))
            rgb[:, x] = (val, val - 5, val - 15)
        # Genuine paper beyond the blend -- never classified as ink.
        rgb[:, 80:] = (255, 255, 255)

        gray = rgb.mean(axis=2).astype(np.uint8)
        fg = _build_foreground(rgb, ink, gray, (w, h), fg_div=1, ink_percentile=100)
        fg_arr = np.asarray(fg)

        # col65's true colour is (124, 119, 109); without the fix this pixel
        # falls back to a box/pyramid average pulled from the photo's
        # distant, differently-hued ink instead of its own true colour.
        got = fg_arr[5, 65].astype(int)
        assert abs(int(got[0]) - 124) < 30
        assert abs(int(got[1]) - 119) < 30
        assert abs(int(got[2]) - 109) < 30

    def test_dense_ink_block_is_not_diluted_by_the_edge_fix(self):
        """Regression test: an earlier version of the edge fix dilated the
        whole `keep` set passed to masked_mean unconditionally, which also
        faded ordinary body text wherever the page's foreground didn't
        collapse to a flat swatch (a photo elsewhere on the page raises
        overall chroma variance past the collapse threshold) -- measured
        directly on a real fixture: mean intensity of dark/ink pixels in a
        text crop rose from 52 to 104, i.e. visibly lighter ink. The fix
        must only ever substitute the dilated fallback into genuine holes
        (blocks with zero native ink coverage), never into a block that
        already has real ink."""
        from pdftl.operations.mrc_compress import _build_foreground

        h, w = 20, 240
        rgb = np.full((h, w, 3), 255, dtype=np.uint8)
        ink = np.zeros((h, w), dtype=bool)
        # A "photo": enough hue variety to keep the page off the flat-ink
        # swatch collapse path (see the edge test above).
        for x in range(0, 60, 2):
            ink[:, x] = True
            rgb[:, x] = (60 + (x % 40), 50, 90 - (x % 40))
        # A separate "text" stroke, thin enough (8px) that most of its own
        # width sits within the fix's dilation radius of its own boundary
        # -- exactly the geometry that faded real glyph strokes.
        ink[:, 150:158] = True
        rgb[:, 150:158] = (30, 30, 30)

        gray = rgb.mean(axis=2).astype(np.uint8)
        fg_div = 4
        fg_size = (w // fg_div, h // fg_div)
        fg = _build_foreground(rgb, ink, gray, (w, h), fg_div=fg_div, ink_percentile=100)
        fg_arr = np.asarray(fg)

        bare = segmentation.masked_mean(rgb, ink, fg_size)
        has_native_ink = segmentation._reduce(ink.astype(np.float32), fg_size) > segmentation._EPS

        assert np.allclose(fg_arr[has_native_ink], bare[has_native_ink], atol=1)

    def test_flat_black_text_still_collapses_to_a_single_swatch(self):
        """The dilation must not defeat the flat-ink swatch collapse for
        an ordinary single-colour-ink document (see flat_ink_chroma_variance)."""
        from pdftl.operations.mrc_compress import _build_foreground

        h, w = 10, 40
        rgb = np.full((h, w, 3), 255, dtype=np.uint8)
        ink = np.zeros((h, w), dtype=bool)
        ink[:, 10:20] = True
        rgb[ink] = (20, 20, 20)

        gray = rgb.mean(axis=2).astype(np.uint8)
        fg = _build_foreground(rgb, ink, gray, (w, h), fg_div=4, ink_percentile=100)
        assert fg.size == (1, 1)


class TestDegradeLadder:
    def test_falls_back_to_native_ccitt_or_flate_without_jbig2(self, monkeypatch):
        monkeypatch.setattr(codecs, "jbig2_binary", lambda: None)
        ink = np.zeros((200, 200), dtype=bool)
        for y in range(20, 180, 10):
            ink[y : y + 6, 20:180] = True
        mask = segmentation.mask_image(ink)
        stencil = codecs.encode_stencil(mask)
        assert stencil.codec in ("ccitt_g4", "flate")
        assert stencil.filter_name in ("/CCITTFaxDecode", "/FlateDecode")

    def test_uses_jbig2_when_a_binary_is_actually_available(self):
        if codecs.jbig2_binary() is None:
            pytest.skip("no jbig2enc-family binary available on this machine")
        ink = np.zeros((200, 200), dtype=bool)
        for y in range(20, 180, 10):
            ink[y : y + 6, 20:180] = True
        mask = segmentation.mask_image(ink)
        stencil = codecs.encode_stencil(mask)
        assert stencil.codec == "jbig2_generic"
        assert stencil.filter_name == "/JBIG2Decode"


# --- layer colour spaces, size guard, fallbacks ---


def _add_image_page(pdf, data: bytes, colour_space: str, filt: str):
    xobj = pikepdf.Stream(pdf, data)
    xobj.Type, xobj.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
    xobj.Width, xobj.Height, xobj.BitsPerComponent = IMG_W, IMG_H, 8
    xobj.ColorSpace, xobj.Filter = pikepdf.Name(colour_space), pikepdf.Name(filt)
    pdf.add_blank_page(page_size=(PAGE_W, PAGE_H))
    page = pdf.pages[-1]
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=pdf.make_indirect(xobj)))
    page.Contents = pdf.make_stream(f"q {PAGE_W} 0 0 {PAGE_H} 0 0 cm /Im0 Do Q".encode())


def _lines_image(mode: str, ink) -> Image.Image:
    img = Image.new(mode, (IMG_W, IMG_H), 255 if mode == "L" else (255, 255, 255))
    draw = ImageDraw.Draw(img)
    for y in range(80, IMG_H - 80, 40):
        draw.rectangle([80, y, IMG_W - 80, y + 14], fill=ink)
    return img


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _layers(pdf):
    """(background, foreground) image XObjects spliced onto page 1."""
    images = [x for x in pdf.pages[0].Resources.XObject.values() if x.get("/Subtype") == "/Image"]
    fg = [x for x in images if "/Mask" in x]
    bg = [x for x in images if "/Mask" not in x]
    assert len(fg) == 1 and len(bg) == 1
    return bg[0], fg[0]


class TestLayerColourSpace:
    def test_grayscale_scan_gets_grayscale_layers(self, scan_pdf):
        _add_image_page(scan_pdf, _jpeg(_lines_image("L", 20)), "/DeviceGray", "/DCTDecode")
        result = mrc_compress(scan_pdf, [])
        assert result.data["pages"][0]["gray"] is True
        bg, fg = _layers(scan_pdf)
        assert bg.ColorSpace == pikepdf.Name.DeviceGray
        assert fg.ColorSpace == pikepdf.Name.DeviceGray

    def test_gray_content_stored_as_rgb_gets_grayscale_layers(self, scan_pdf):
        _add_scan_page(scan_pdf)  # a gray-on-white page saved as an RGB JPEG
        mrc_compress(scan_pdf, [])
        bg, fg = _layers(scan_pdf)
        assert bg.ColorSpace == fg.ColorSpace == pikepdf.Name.DeviceGray

    def test_colour_scan_keeps_colour_layers_and_colour(self, scan_pdf):
        _add_image_page(
            scan_pdf, _jpeg(_lines_image("RGB", (200, 20, 20))), "/DeviceRGB", "/DCTDecode"
        )
        result = mrc_compress(scan_pdf, [])
        assert result.data["pages"][0]["gray"] is False
        bg, fg = _layers(scan_pdf)
        assert bg.ColorSpace == fg.ColorSpace == pikepdf.Name.DeviceRGB
        # A line's middle, independently: rendered red, not gray.
        px = render_page_to_pil(scan_pdf, 0, dpi=72).convert("RGB").getpixel((PAGE_W // 2, 63))
        assert px[0] > 150 and px[1] < 90 and px[2] < 90

    def test_grayscale_layers_are_smaller_than_colour_ones(self, scan_pdf):
        from pdftl.operations import mrc_compress as mod

        data = _jpeg(_lines_image("L", 20))
        _add_image_page(scan_pdf, data, "/DeviceGray", "/DCTDecode")
        gray = mrc_compress(scan_pdf, []).data["pages"][0]
        other = pikepdf.new()
        _add_image_page(other, data, "/DeviceGray", "/DCTDecode")
        mp = pytest.MonkeyPatch()
        mp.setattr(mod, "_looks_gray", lambda rgb: False)
        mp.setattr(
            mod,
            "_lift_pixels",
            lambda cand: pikepdf.PdfImage(cand.xobj).as_pil_image().convert("RGB"),
        )
        colour = mrc_compress(other, []).data["pages"][0]
        mp.undo()
        assert gray["bg_bytes"] < colour["bg_bytes"]


def _antialiased_scan(colours) -> bytes:
    """Strokes drawn at 4x and downsampled, so each has a light anti-aliased rim."""
    big = Image.new("RGB", (IMG_W * 4, IMG_H * 4), (255, 255, 255))
    draw = ImageDraw.Draw(big)
    for i, y in enumerate(range(320, IMG_H * 4 - 320, 96)):
        for x in range(320, IMG_W * 4 - 400, 60):
            draw.rectangle([x, y, x + 18, y + 56], fill=colours[i % len(colours)])
    return _jpeg(big.resize((IMG_W, IMG_H), Image.LANCZOS))


def _erode(mask):
    """Pixels whose 4-neighbours are all set; outside the image counts as unset."""
    p = np.pad(mask, 1)
    return p[1:-1, 1:-1] & p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]


class TestInkIsNotFaded:
    @pytest.mark.parametrize(
        "colours",
        [[(15, 15, 15)], [(170, 10, 10), (10, 10, 170)]],
        ids=["one ink (single swatch)", "two inks (per block)"],
    )
    def test_stroke_cores_keep_their_darkness(self, scan_pdf, colours):
        _add_image_page(scan_pdf, _antialiased_scan(colours), "/DeviceRGB", "/DCTDecode")
        before = io.BytesIO()
        scan_pdf.save(before)
        orig = np.asarray(render_page_to_pil(pikepdf.open(before), 0, dpi=150).convert("L"), float)
        assert mrc_compress(scan_pdf, []).data["pages_mrc"] == 1
        out = np.asarray(render_page_to_pil(scan_pdf, 0, dpi=150).convert("L"), float)
        cores = _erode(orig < 110)
        # Averaging the rims in made cores 30+ levels lighter.
        assert (out - orig)[cores].mean() < 10


class TestSizeGuard:
    def test_page_is_left_alone_when_layers_would_be_larger(self, scan_pdf):
        import zlib

        white = Image.new("L", (IMG_W, IMG_H), 255)
        _add_image_page(scan_pdf, zlib.compress(white.tobytes(), 9), "/DeviceGray", "/FlateDecode")
        before = scan_pdf.pages[0].Contents.read_bytes()
        result = mrc_compress(scan_pdf, [])
        assert result.data["pages_mrc"] == 0
        assert "not smaller" in result.data["pages"][0]["reason"]
        assert scan_pdf.pages[0].Contents.read_bytes() == before


class TestFallbacks:
    def test_undecodable_image_is_rasterized_instead(self, scan_pdf, monkeypatch):
        _add_scan_page(scan_pdf)

        def refuse(self):
            raise pikepdf.PdfError("no decoder")

        monkeypatch.setattr(pikepdf.PdfImage, "as_pil_image", refuse)
        assert mrc_compress(scan_pdf, []).data["pages_mrc"] == 1

    def test_failure_on_a_page_leaves_it_untouched(self, scan_pdf, monkeypatch, caplog):
        _add_scan_page(scan_pdf)
        before = scan_pdf.pages[0].Contents.read_bytes()

        def unverified(mask):
            raise MrcError("stencil verification failed")

        monkeypatch.setattr(codecs, "encode_stencil", unverified)
        result = mrc_compress(scan_pdf, [])
        assert result.data["pages"][0]["reason"] == "MRC failed: stencil verification failed"
        assert scan_pdf.pages[0].Contents.read_bytes() == before
        assert "failed MRC separation" in caplog.text


def test_crisp_scan_keeps_its_detail(scan_pdf):
    import pdftl.operations.mrc_compress as mc

    _add_scan_page(scan_pdf)
    page = mrc_compress(scan_pdf, []).data["pages"][0]
    assert page["decision"] == "mrc"
    assert page["edge_loss"] < mc.MAX_EDGE_LOSS


def test_page_whose_layers_would_blur_detail_is_left_alone(scan_pdf, monkeypatch):
    import pdftl.operations.mrc_compress as mc
    from pdftl.utils.mrc import fidelity

    monkeypatch.setattr(fidelity, "edge_loss", lambda *args: 0.57)
    _add_scan_page(scan_pdf)
    before = scan_pdf.pages[0].Contents.read_bytes()
    page = mrc_compress(scan_pdf, []).data["pages"][0]
    assert page == {
        "page": 1,
        "decision": "untouched",
        "reason": "MRC would blur detail (edge loss 0.57)",
        "edge_loss": 0.57,
    }
    assert 0.57 > mc.MAX_EDGE_LOSS
    assert scan_pdf.pages[0].Contents.read_bytes() == before


def test_without_guard_fidelity_a_high_edge_loss_page_is_still_mrc_layered(scan_pdf, monkeypatch):
    import pdftl.operations.mrc_compress as mc
    from pdftl.utils.mrc import fidelity

    monkeypatch.setattr(fidelity, "edge_loss", lambda *args: 0.57)  # would fail the guard
    _add_scan_page(scan_pdf)
    page = mrc_compress(scan_pdf, [], guard_fidelity=False).data["pages"][0]
    assert page["decision"] == "mrc"
    assert 0.57 > mc.MAX_EDGE_LOSS


def test_without_guard_fidelity_the_size_guard_still_applies(scan_pdf, monkeypatch):
    import zlib

    from pdftl.utils.mrc import fidelity

    monkeypatch.setattr(fidelity, "edge_loss", lambda *args: 0.57)
    white = Image.new("L", (IMG_W, IMG_H), 255)
    _add_image_page(scan_pdf, zlib.compress(white.tobytes(), 9), "/DeviceGray", "/FlateDecode")
    before = scan_pdf.pages[0].Contents.read_bytes()
    result = mrc_compress(scan_pdf, [], guard_fidelity=False)
    assert result.data["pages_mrc"] == 0
    assert "not smaller" in result.data["pages"][0]["reason"]
    assert scan_pdf.pages[0].Contents.read_bytes() == before


# 850 x 1100 RGB decodes to 2,805,000 bytes (2.7 MB); supersample=2 makes it 10.7 MB.
@pytest.mark.parametrize("limit_mb,args", [("2", []), ("8", ["supersample=2"])])
def test_scan_over_the_decode_ceiling_is_left_alone(scan_pdf, monkeypatch, limit_mb, args):
    monkeypatch.setenv("PDFTL_MAX_DECODED_MB", limit_mb)
    _add_scan_page(scan_pdf)
    before = scan_pdf.pages[0].Contents.read_bytes()
    result = mrc_compress(scan_pdf, args)
    assert result.data["pages"][0] == {
        "page": 1,
        "decision": "untouched",
        "reason": "image too large to decode",
    }
    assert scan_pdf.pages[0].Contents.read_bytes() == before
