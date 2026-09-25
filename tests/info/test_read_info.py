import io

import pikepdf

from pdftl.info.read_info import pdf_id_metadata_as_strings


def test_pdf_id_metadata_empty_without_trailer_id():
    with pikepdf.new() as pdf:
        assert pdf_id_metadata_as_strings(pdf) == []


def test_pdf_id_metadata_hex_strings_from_trailer_id():
    buf = io.BytesIO()
    with pikepdf.new() as pdf:
        pdf.save(buf)
    with pikepdf.open(buf) as pdf:
        pdf.trailer.ID = pikepdf.Array([b"\x01\xab", b"\xff"])
        assert pdf_id_metadata_as_strings(pdf) == ["01ab", "ff"]
