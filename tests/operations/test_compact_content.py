# tests/operations/test_compact_content.py
#
# Ground truth: pdfium renders before and after, and hand-written streams.

import io

import numpy as np
import pikepdf
import pytest

from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.compact_content import compact_content

LONG = b"".join(b"q 1.0 0 0 1.0 %d.00 0.50 cm 0 0 5.0 5.0 re f Q\n" % i for i in range(60))


def _render(pdf):
    import pypdfium2

    buf = io.BytesIO()
    pdf.save(buf)
    doc = pypdfium2.PdfDocument(buf.getvalue())
    try:
        return [np.asarray(p.render(scale=1).to_pil().convert("RGB")) for p in doc]
    finally:
        doc.close()


def _pdf(*contents):
    pdf = pikepdf.new()
    for content in contents:
        page = pdf.add_blank_page(page_size=(400, 100))
        page.Contents = (
            content if isinstance(content, pikepdf.Object) else pdf.make_stream(content)
        )
    return pdf


def test_page_and_form_are_compacted_and_render_the_same():
    pdf = _pdf(LONG)
    form = pdf.make_stream(
        LONG, Type=pikepdf.Name.XObject, Subtype=pikepdf.Name.Form, BBox=[0, 0, 400, 100]
    )
    pdf.pages[0].Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm0=form))
    pdf.pages[0].Contents = pdf.make_stream(LONG + b"q 1 0 0 1 0 20 cm /Fm0 Do Q")
    before = _render(pdf)
    result = compact_content(pdf, [])
    assert result.data == {"pages": 1, "forms": 1}
    assert b"1.0" not in form.read_bytes() and b".5 cm" in form.read_bytes()
    assert all((a == b).all() for a, b in zip(before, _render(pdf)))


def test_split_page_contents_become_one_stream():
    pdf = pikepdf.new()
    split = LONG.index(b"\n", 500) + 1  # streams may only divide between tokens
    parts = pikepdf.Array([pdf.make_stream(LONG[:split]), pdf.make_stream(LONG[split:])])
    page = pdf.add_blank_page(page_size=(400, 100))
    page.Contents = parts
    before = _render(pdf)
    assert compact_content(pdf, []).data["pages"] == 1
    assert isinstance(page.obj.Contents, pikepdf.Stream)
    assert all((a == b).all() for a, b in zip(before, _render(pdf)))


def test_already_compact_streams_are_left_alone():
    pdf = _pdf(b"q 1 0 0 1 0 0 cm Q")
    before = pdf.pages[0].Contents.read_bytes()
    assert compact_content(pdf, []).data == {"pages": 0, "forms": 0}
    assert pdf.pages[0].Contents.read_bytes() == before


def test_malformed_and_empty_content_is_left_alone():
    page_data, form_data = b"q (unterminated " + LONG, b"q BI /W 1 ID " + LONG
    pdf = _pdf(page_data)
    empty = pdf.add_blank_page()
    del empty.obj["/Contents"]
    form = pdf.make_stream(form_data, Subtype=pikepdf.Name.Form, BBox=[0, 0, 1, 1])
    pdf.pages[0].Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm0=form))
    assert compact_content(pdf, []).data == {"pages": 0, "forms": 0}
    assert pdf.pages[0].Contents.read_bytes() == page_data
    assert form.read_bytes() == form_data


def test_undecodable_streams_are_left_alone():
    pdf = _pdf(b"")
    for stream in (
        pdf.pages[0].Contents,
        pdf.make_stream(b"", Subtype=pikepdf.Name.Form, BBox=[0, 0, 1, 1]),
    ):
        stream.write(b"not zlib data", filter=pikepdf.Name.FlateDecode)
    assert compact_content(pdf, []).data == {"pages": 0, "forms": 0}


def test_every_stream_survives_batching(monkeypatch):
    import pdftl.operations.compact_content as op

    monkeypatch.setattr(op, "BATCH_BYTES", 1)  # a commit after every stream
    pdf = _pdf(LONG, LONG.replace(b"5.0", b"6.0"))
    before = _render(pdf)
    assert compact_content(pdf, []).data == {"pages": 2, "forms": 0}
    assert all((a == b).all() for a, b in zip(before, _render(pdf)))


def test_rejects_arguments():
    with pytest.raises(InvalidArgumentError, match="takes no arguments"):
        compact_content(_pdf(LONG), ["1-3"])
