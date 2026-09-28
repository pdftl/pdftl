# tests/output/test_recompress.py
#
# The `recompress` output option, checked against zlib directly: a stream
# stored at level 1 must come out as zlib level 9 would compress it. Below
# the save_pdf-level tests, the module's internals (image path, oxipng,
# PNG filters, batching, zopfli) are exercised directly against ground
# truth: qpdf's own predictor decoder (stream.read_bytes) must give back
# the original samples, and sizes are compared with zlib directly.

import io
import sys
import zlib
import subprocess
from unittest.mock import MagicMock

import numpy as np
import pikepdf
import pytest
from PIL import Image

import pdftl.output.recompress as rc
from pdftl.output.save import _RECOMPRESS, _build_save_options, save_pdf

DATA = b"".join(
    b"line %d of some fairly repetitive page content\n" % (i % 97) for i in range(4000)
)

W, H = 120, 80


def _pdf_with_stream(raw, **filt):
    pdf = pikepdf.new()
    pdf.add_blank_page()
    stream = pdf.make_stream(raw)
    for k, v in filt.items():
        stream["/" + k] = v
    pdf.Root.Payload = stream
    return pdf


def _saved_payload_raw(pdf, tmp_path, options):
    out = tmp_path / "out.pdf"
    save_pdf(pdf, output_filename=str(out), input_context=MagicMock(), options=options)
    with pikepdf.open(out) as saved:
        payload = saved.Root.Payload
        assert payload.read_bytes() == DATA
        return payload.read_raw_bytes()


def _gradient(colors=3) -> bytes:
    y, x = np.mgrid[0:H, 0:W]
    planes = [(x * 2 + y) % 256, (x + y * 3) % 256, (x * y) % 256, (x + 7) % 256][:colors]
    return np.stack(planes, -1).astype(np.uint8).tobytes()


def _noise(colors=3) -> bytes:
    return np.random.default_rng(5).integers(0, 256, W * H * colors, dtype=np.uint8).tobytes()


def _image(pdf, data, colors=3, **extra):
    cs = {1: pikepdf.Name.DeviceGray, 3: pikepdf.Name.DeviceRGB, 4: pikepdf.Name.DeviceCMYK}[
        colors
    ]
    img = pdf.make_stream(data, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Image)
    img.Width, img.Height, img.BitsPerComponent, img.ColorSpace = W, H, 8, cs
    for key, value in extra.items():
        img["/" + key] = value
    return img


def _png_predicted(samples: bytes, colors: int) -> bytes:
    """Flate data with PNG predictors, as Pillow writes them (independent encoder)."""
    mode = {1: "L", 3: "RGB"}[colors]
    shape = (H, W) if colors == 1 else (H, W, colors)
    buf = io.BytesIO()
    Image.fromarray(np.frombuffer(samples, np.uint8).reshape(shape), mode).save(buf, "PNG")
    return rc._idat_if_same_format(buf.getvalue(), buf.getvalue()[16:29])


def test_default_save_keeps_existing_flate_bytes(tmp_path):
    level1 = zlib.compress(DATA, 1)
    pdf = _pdf_with_stream(level1, Filter=pikepdf.Name.FlateDecode)
    assert _saved_payload_raw(pdf, tmp_path, {}) == level1


def test_recompress_uses_max_level(tmp_path):
    level1 = zlib.compress(DATA, 1)
    level9 = zlib.compress(DATA, 9)
    assert len(level9) < len(level1)
    pdf = _pdf_with_stream(level1, Filter=pikepdf.Name.FlateDecode)
    assert len(_saved_payload_raw(pdf, tmp_path, {"recompress": True})) == len(level9)


def test_recompress_restores_default_level_afterwards(tmp_path):
    pdf = _pdf_with_stream(zlib.compress(DATA, 1), Filter=pikepdf.Name.FlateDecode)
    _saved_payload_raw(pdf, tmp_path, {"recompress": True})

    uncompressed = _pdf_with_stream(DATA)
    raw = _saved_payload_raw(uncompressed, tmp_path, {})
    assert len(raw) == len(zlib.compress(DATA, zlib.Z_DEFAULT_COMPRESSION))


@pytest.mark.parametrize("conflict", ["fast", "uncompress"])
def test_recompress_ignored_with_conflicting_option(conflict, caplog):
    opts = _build_save_options({"recompress": True, conflict: True}, MagicMock())
    assert "recompress_flate" not in opts and _RECOMPRESS not in opts
    assert "Ignoring 'recompress'" in caplog.text


def test_recompress_is_done_by_pdftl_not_by_qpdf():
    # qpdf's recompress_flate drops PNG predictors, so it must never be set.
    opts = _build_save_options({"recompress": True}, MagicMock())
    assert opts[_RECOMPRESS] is True
    assert "recompress_flate" not in opts


def test_recompress_option_is_registered():
    from pdftl.core.registry import registry
    from pdftl.output.save import _recompress_option

    _recompress_option()
    assert "recompress" in registry.options


def test_deflate_option_is_registered():
    from pdftl.core.registry import registry
    from pdftl.output.save import _deflate_option

    _deflate_option()
    assert "deflate <encoder>" in registry.options


class _RecordingZopfli:
    def __init__(self):
        self.sizes = []

    def compress(self, data, numiterations):
        self.sizes.append(len(data))
        return zlib.compress(data, 9)


@pytest.fixture
def fake_zopfli(monkeypatch):
    fake = _RecordingZopfli()
    monkeypatch.setattr(rc, "_zopfli", lambda: fake)
    return fake


def test_deflate_zopfli_implies_recompress(tmp_path, fake_zopfli):
    pdf = _pdf_with_stream(zlib.compress(DATA, 1), Filter=pikepdf.Name.FlateDecode)
    raw = _saved_payload_raw(pdf, tmp_path, {"deflate": "zopfli"})
    assert len(raw) == len(zlib.compress(DATA, 9))
    assert len(DATA) in fake_zopfli.sizes


def test_recompress_alone_does_not_use_zopfli(tmp_path, fake_zopfli):
    pdf = _pdf_with_stream(zlib.compress(DATA, 1), Filter=pikepdf.Name.FlateDecode)
    _saved_payload_raw(pdf, tmp_path, {"recompress": True, "deflate": "zlib"})
    assert fake_zopfli.sizes == []


def test_deflate_rejects_unknown_encoders_and_missing_zopfli(tmp_path, monkeypatch):
    from pdftl.exceptions import InvalidArgumentError, PackageError

    pdf = _pdf_with_stream(DATA)
    with pytest.raises(InvalidArgumentError, match="zlib, zopfli"):
        _saved_payload_raw(pdf, tmp_path, {"deflate": "lzma"})
    monkeypatch.setattr(rc, "_zopfli", lambda: None)
    with pytest.raises(PackageError):
        _saved_payload_raw(pdf, tmp_path, {"deflate": "zopfli"})
    assert not (tmp_path / "out.pdf").exists()


# --- the qpdf bug this replaces ---


def test_predicted_image_keeps_its_predictor_and_never_grows(tmp_path, monkeypatch):
    monkeypatch.setattr(rc.shutil, "which", lambda name: None)
    samples = _gradient()
    pdf = pikepdf.new()
    pdf.add_blank_page()
    data = _png_predicted(samples, 3)
    parms = pikepdf.Dictionary(Predictor=15, Colors=3, BitsPerComponent=8, Columns=W)
    pdf.Root.Img = _image(pdf, data, Filter=pikepdf.Name.FlateDecode, DecodeParms=parms)
    out = tmp_path / "out.pdf"
    save_pdf(pdf, str(out), input_context=MagicMock(), options={"recompress": True})
    with pikepdf.open(out) as saved:
        img = saved.Root.Img
        assert img.read_bytes() == samples
        assert len(img.read_raw_bytes()) <= len(data)
        assert len(img.read_raw_bytes()) < len(zlib.compress(samples, 9)) / 5  # predictor kept


# --- images ---


@pytest.mark.parametrize("colors", [1, 3, 4])
def test_png_filters_used_when_they_win(monkeypatch, colors):
    monkeypatch.setattr(rc.shutil, "which", lambda name: None)
    samples = _gradient(colors)
    pdf = pikepdf.new()
    img = _image(pdf, zlib.compress(samples, 1), colors, Filter=pikepdf.Name.FlateDecode)
    stats = rc.recompress_streams(pdf)
    assert stats.rewritten == 1
    assert img.read_bytes() == samples
    assert int(img.DecodeParms.Predictor) == 15 and int(img.DecodeParms.Colors) == colors
    assert len(img.read_raw_bytes()) < len(zlib.compress(samples, 9))


def test_plain_zlib_kept_when_filters_do_not_help(monkeypatch):
    monkeypatch.setattr(rc.shutil, "which", lambda name: None)
    samples = _noise()
    pdf = pikepdf.new()
    img = _image(pdf, zlib.compress(samples, 1), Filter=pikepdf.Name.FlateDecode)
    rc.recompress_streams(pdf)
    assert img.read_bytes() == samples
    assert "/DecodeParms" not in img
    assert img.read_raw_bytes() == zlib.compress(samples, 9) or len(img.read_raw_bytes()) <= len(
        zlib.compress(samples, 1)
    )


def test_already_optimal_stream_is_left_untouched(monkeypatch):
    monkeypatch.setattr(rc.shutil, "which", lambda name: None)
    pdf = pikepdf.new()
    best = zlib.compress(b"", 9)
    stream = pdf.make_stream(best, Filter=pikepdf.Name.FlateDecode)
    stats = rc.recompress_streams(pdf)
    assert (stats.streams, stats.rewritten) == (1, 0)
    assert stream.read_raw_bytes() == best


@pytest.mark.parametrize(
    "extra",
    [
        {"BitsPerComponent": 1},
        {"Width": 0},
        {"Width": W + 1},  # samples do not fill the stated size
        {"DecodeParms": pikepdf.Array([pikepdf.Dictionary()])},
        {"Width": pikepdf.Name.X},
    ],
)
def test_image_path_skipped_for_images_it_cannot_model(monkeypatch, extra):
    monkeypatch.setattr(rc.shutil, "which", lambda name: None)
    pdf = pikepdf.new()
    img = _image(pdf, zlib.compress(_gradient(), 1), Filter=pikepdf.Name.FlateDecode, **extra)
    assert rc._image_candidate(img, img.get("/DecodeParms")) is None


def test_non_flate_and_damaged_streams_are_left_alone():
    pdf = pikepdf.new()
    dct = pdf.make_stream(b"not really jpeg", Filter=pikepdf.Name.DCTDecode)
    chain = pdf.make_stream(
        b"x", Filter=pikepdf.Array([pikepdf.Name.ASCIIHexDecode, pikepdf.Name.FlateDecode])
    )
    broken = pdf.make_stream(b"\x78\x9cnot zlib", Filter=pikepdf.Name.FlateDecode)
    single = pdf.make_stream(
        zlib.compress(b"a" * 1000, 0), Filter=pikepdf.Array([pikepdf.Name.FlateDecode])
    )
    before = [s.read_raw_bytes() for s in (dct, chain, broken)]
    stats = rc.recompress_streams(pdf)
    assert [s.read_raw_bytes() for s in (dct, chain, broken)] == before
    assert single.read_bytes() == b"a" * 1000 and stats.rewritten == 1


# --- oxipng ---


def test_oxipng_success_mocked(monkeypatch):
    """Tests the successful oxipng execution path when oxipng is not installed in CI."""
    monkeypatch.setattr(rc.shutil, "which", lambda name: "/usr/bin/oxipng")

    def mock_run(cmd, capture_output=True, check=False):
        return subprocess.CompletedProcess(cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(rc.subprocess, "run", mock_run)

    samples = _gradient()
    result = rc._oxipng_idat(samples, W, H, 3)
    assert result is not None


def test_oxipng_result_used_when_smaller(monkeypatch):
    samples = _gradient()
    monkeypatch.setattr(rc, "OXIPNG_MIN_BYTES", 0)
    monkeypatch.setattr(rc, "_oxipng_idat", lambda s, w, h, c: b"tiny")
    pdf = pikepdf.new()
    img = _image(pdf, zlib.compress(samples, 1), Filter=pikepdf.Name.FlateDecode)
    encoded, parms = rc._image_candidate(img, None)
    assert encoded == b"tiny" and int(parms.Predictor) == 15


def test_real_oxipng_is_lossless(monkeypatch):
    if rc.shutil.which("oxipng") is None:
        pytest.skip("oxipng not installed")
    samples = _gradient()
    idat = rc._oxipng_idat(samples, W, H, 3)
    pdf = pikepdf.new()
    parms = pikepdf.Dictionary(Predictor=15, Colors=3, BitsPerComponent=8, Columns=W)
    img = _image(pdf, idat, Filter=pikepdf.Name.FlateDecode, DecodeParms=parms)
    assert img.read_bytes() == samples


def test_oxipng_absent_unsupported_or_failing(monkeypatch):
    samples = _gradient(4)
    assert rc._oxipng_idat(samples, W, H, 4) is None  # CMYK has no PNG colour type
    monkeypatch.setattr(rc.shutil, "which", lambda name: None)
    assert rc._oxipng_idat(_gradient(), W, H, 3) is None
    monkeypatch.setattr(rc.shutil, "which", lambda name: sys.executable)  # rejects oxipng's flags
    assert rc._oxipng_idat(_gradient(), W, H, 3) is None


def test_changed_png_format_is_rejected():
    header = rc.struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)
    other = rc.struct.pack(">IIBBBBB", W, H, 8, 3, 0, 0, 0)  # became a palette image
    png = b"\x89PNG\r\n\x1a\n" + rc._png_chunk(b"IHDR", other) + rc._png_chunk(b"IDAT", b"xx")
    assert rc._idat_if_same_format(png, header) is None
    same = b"\x89PNG\r\n\x1a\n" + rc._png_chunk(b"IHDR", header) + rc._png_chunk(b"IDAT", b"xx")
    assert rc._idat_if_same_format(same, header) == b"xx"


def test_sample_rows():
    assert list(rc._sample_rows(10, 100)) == list(range(10))  # small: every row
    rows = rc._sample_rows(4000, 1024)  # 4 MB: 32 bands of 32 rows
    assert len(rows) == (1 << 20) // 1024
    assert rows[0] == 0 and rows[-1] == 3999
    assert list(rows) == sorted(set(rows))
    assert list(rc._sample_rows(50, 1 << 20)) == list(range(32))  # one band


def test_large_image_ranked_on_samples_decodes_losslessly(monkeypatch):
    monkeypatch.setattr(rc.shutil, "which", lambda name: None)
    monkeypatch.setattr(rc, "RANK_SAMPLE_BYTES", 4096)
    samples = _gradient()
    pdf = pikepdf.new()
    img = _image(pdf, zlib.compress(samples, 1), Filter=pikepdf.Name.FlateDecode)
    rc.recompress_streams(pdf)
    assert img.read_bytes() == samples
    assert len(img.read_raw_bytes()) < len(zlib.compress(samples, 9))


def test_small_images_skip_oxipng(monkeypatch):
    calls = []
    monkeypatch.setattr(rc, "OXIPNG_MIN_BYTES", 10**9)
    monkeypatch.setattr(rc, "_oxipng_idat", lambda *a: calls.append(a))
    pdf = pikepdf.new()
    img = _image(pdf, zlib.compress(_gradient(), 1), Filter=pikepdf.Name.FlateDecode)
    assert rc._image_candidate(img, None) is not None
    assert calls == []


# --- batching and zopfli ---


def test_every_stream_survives_batching(monkeypatch):
    monkeypatch.setattr(rc.shutil, "which", lambda name: None)
    monkeypatch.setattr(rc, "BATCH_BYTES", 1)  # a commit after every stream
    pdf = pikepdf.new()
    payloads = [(b"stream %d " % i) * 500 for i in range(5)]
    streams = [
        pdf.make_stream(zlib.compress(p, 1), Filter=pikepdf.Name.FlateDecode) for p in payloads
    ]
    stats = rc.recompress_streams(pdf)
    assert stats.rewritten == 5
    assert [s.read_bytes() for s in streams] == payloads


class _FakeZopfli:
    def __init__(self):
        self.calls = []

    def compress(self, data, numiterations):
        self.calls.append((len(data), numiterations))
        return b"z"  # shorter than any zlib stream


def test_deflate_prefers_zopfli_and_scales_its_effort(monkeypatch):
    fake = _FakeZopfli()
    monkeypatch.setattr(rc, "_zopfli", lambda: fake)
    monkeypatch.setattr(rc, "ZOPFLI_FULL_BYTES", 100)
    monkeypatch.setattr(rc, "ZOPFLI_MAX_BYTES", 1000)
    with rc.zopfli_deflate(True):
        assert rc.deflate(b"a" * 50) == b"z"
        assert rc.deflate(b"a" * 500) == b"z"
        assert rc.deflate(b"a" * 5000) == zlib.compress(b"a" * 5000, 9)  # too big for zopfli
    assert fake.calls == [(50, 5), (500, 1)]


def test_zopfli_only_inside_the_block(monkeypatch):
    fake = _FakeZopfli()
    monkeypatch.setattr(rc, "_zopfli", lambda: fake)
    assert rc.deflate(b"a" * 50) == zlib.compress(b"a" * 50, 9)
    with rc.zopfli_deflate(False):
        rc.deflate(b"a" * 50)
    with pytest.raises(RuntimeError), rc.zopfli_deflate(True):
        raise RuntimeError
    assert rc.deflate(b"a" * 50) == zlib.compress(b"a" * 50, 9)
    assert fake.calls == []


def test_zopfli_block_fails_cleanly_without_zopfli(monkeypatch):
    from pdftl.exceptions import PackageError

    monkeypatch.setattr(rc, "_zopfli", lambda: None)
    with pytest.raises(PackageError, match=r"pip install pdftl\[zopfli\]"):
        with rc.zopfli_deflate(True):
            pass  # pragma: no cover
    assert not rc._USE_ZOPFLI


def test_zlib_wins_when_zopfli_is_not_smaller(monkeypatch):
    class Worse:
        def compress(self, data, numiterations):
            return zlib.compress(data, 9) + b"extra"

    monkeypatch.setattr(rc, "_zopfli", lambda: Worse())
    with rc.zopfli_deflate(True):
        assert rc.deflate(b"abc" * 100) == zlib.compress(b"abc" * 100, 9)


def test_zopfli_is_found_when_installed(monkeypatch):
    import types

    module, inner = types.ModuleType("zopfli"), types.ModuleType("zopfli.zlib")
    module.zlib = inner
    monkeypatch.setitem(sys.modules, "zopfli", module)
    monkeypatch.setitem(sys.modules, "zopfli.zlib", inner)
    rc._zopfli.cache_clear()
    try:
        assert rc._zopfli() is inner
        monkeypatch.setitem(sys.modules, "zopfli", None)
        rc._zopfli.cache_clear()
        assert rc._zopfli() is None
    finally:
        rc._zopfli.cache_clear()


def test_real_zopfli_output_is_a_valid_zlib_stream():
    pytest.importorskip("zopfli")
    rc._zopfli.cache_clear()
    data = b"".join(b"%d 0 0 %d re f\n" % (i, i * 7 % 13) for i in range(2000))
    with rc.zopfli_deflate(True):
        assert zlib.decompress(rc.deflate(data)) == data
        assert len(rc.deflate(data)) <= len(zlib.compress(data, 9))


# --- unfiltered streams ---


def test_unfiltered_streams_are_compressed_but_not_xmp(monkeypatch):
    monkeypatch.setattr(rc.shutil, "which", lambda name: None)
    pdf = pikepdf.new()
    text = b"".join(b"%d 0 0 %d re f\n" % (i, i % 13) for i in range(500))
    content = pdf.make_stream(text)
    image = _image(pdf, _gradient())
    xmp = pdf.make_stream(
        b"<x:xmpmeta>" + b" " * 2000 + b"</x:xmpmeta>", Type=pikepdf.Name.Metadata
    )
    noise = pdf.make_stream(_noise(1)[:3000])  # no encoding is smaller
    empty_array = pdf.make_stream(text, Filter=pikepdf.Array())
    stats = rc.recompress_streams(pdf)
    assert stats.rewritten == 3
    assert content.Filter == pikepdf.Name.FlateDecode and content.read_bytes() == text
    assert len(content.read_raw_bytes()) == len(zlib.compress(text, 9))
    assert empty_array.Filter == pikepdf.Name.FlateDecode and empty_array.read_bytes() == text
    assert image.DecodeParms.Predictor == 15 and image.read_bytes() == _gradient()
    assert len(image.read_raw_bytes()) < len(zlib.compress(_gradient(), 9))
    assert "/Filter" not in xmp and "/Filter" not in noise


def test_zopfli_runs_once_per_input_inside_the_block(monkeypatch):
    fake = _FakeZopfli()
    monkeypatch.setattr(rc, "_zopfli", lambda: fake)
    with rc.zopfli_deflate(True):
        assert rc.deflate(b"a" * 50) == rc.deflate(b"a" * 50) == b"z"
        rc.deflate(b"b" * 50)
    with rc.zopfli_deflate(True):
        rc.deflate(b"a" * 50)
    assert fake.calls == [(50, 5), (50, 5), (50, 5)]
