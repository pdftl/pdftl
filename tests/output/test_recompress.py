# tests/output/test_recompress.py
#
# The `recompress` output option, checked against zlib directly: a stream
# stored at level 1 must come out as zlib level 9 would compress it.

import zlib
from unittest.mock import MagicMock

import pikepdf
import pytest

from pdftl.output.save import _RECOMPRESS, _build_save_options, save_pdf

DATA = b"".join(
    b"line %d of some fairly repetitive page content\n" % (i % 97) for i in range(4000)
)


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
    from pdftl.output import recompress as rc

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
    from pdftl.output import recompress as rc

    pdf = _pdf_with_stream(DATA)
    with pytest.raises(InvalidArgumentError, match="zlib, zopfli"):
        _saved_payload_raw(pdf, tmp_path, {"deflate": "lzma"})
    monkeypatch.setattr(rc, "_zopfli", lambda: None)
    with pytest.raises(PackageError):
        _saved_payload_raw(pdf, tmp_path, {"deflate": "zopfli"})
    assert not (tmp_path / "out.pdf").exists()
