# tests/output/test_uncompress.py
#
# `uncompress` must store Flate streams as plain bytes, including ones that
# were already compressed in the input, while leaving image codecs alone.

import zlib
from unittest.mock import MagicMock

import pikepdf
from pikepdf import Name

from pdftl.output.save import save_pdf

CONTENT = b"0 0 m 100 100 l S\n" * 200
JPEG = b"\xff\xd8 not decoded by uncompress"


def _pdf():
    pdf = pikepdf.new()
    pdf.add_blank_page()
    contents = pdf.make_stream(zlib.compress(CONTENT))
    contents.Filter = Name.FlateDecode
    pdf.pages[0].Contents = contents
    jpeg = pdf.make_stream(JPEG)
    jpeg.Filter = Name.DCTDecode
    pdf.Root.Jpeg = jpeg
    return pdf


def test_uncompress_decompresses_existing_flate_streams(tmp_path):
    out = tmp_path / "out.pdf"
    save_pdf(
        _pdf(), output_filename=str(out), input_context=MagicMock(), options={"uncompress": True}
    )
    with pikepdf.open(out) as saved:
        contents = saved.pages[0].Contents
        assert "/Filter" not in contents
        assert contents.read_raw_bytes() == CONTENT
        assert saved.Root.Jpeg.Filter == Name.DCTDecode
        assert saved.Root.Jpeg.read_raw_bytes() == JPEG
