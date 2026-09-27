# tests/output/test_drop_thumbnails.py
#
# Ground truth: the /Thumb entries in the saved file, and pdfium renders.

import zlib
from unittest.mock import MagicMock

import numpy as np
import pikepdf
import pytest

import pdftl.core.constants as c
from pdftl.operations.shrink import shrink
from pdftl.output.save import save_pdf


def _with_thumbnails(pages=2):
    pdf = pikepdf.new()
    for _ in range(pages):
        page = pdf.add_blank_page(page_size=(100, 100))
        page.Contents = pdf.make_stream(b"0 0 1 rg 10 10 80 80 re f")
        page.obj.Thumb = pdf.make_stream(
            zlib.compress(bytes(range(256)) * 12),
            Width=32,
            Height=32,
            BitsPerComponent=8,
            ColorSpace=pikepdf.Name.DeviceRGB,
            Filter=pikepdf.Name.FlateDecode,
        )
    return pdf


def _render(path):
    import pypdfium2

    doc = pypdfium2.PdfDocument(str(path))
    try:
        return [np.asarray(p.render().to_pil()) for p in doc]
    finally:
        doc.close()


@pytest.mark.parametrize("options", [{"drop_thumbnails": True}, {"drop_meta": True}])
def test_thumbnails_are_dropped_and_pages_unchanged(tmp_path, options):
    before, after = tmp_path / "before.pdf", tmp_path / "after.pdf"
    _with_thumbnails().save(before)
    save_pdf(_with_thumbnails(), str(after), input_context=MagicMock(), options=options)
    with pikepdf.open(after) as pdf:
        assert all("/Thumb" not in page.obj for page in pdf.pages)
    assert after.stat().st_size < before.stat().st_size
    assert all((a == b).all() for a, b in zip(_render(before), _render(after)))


def test_thumbnails_are_kept_by_default(tmp_path):
    out = tmp_path / "out.pdf"
    save_pdf(_with_thumbnails(), str(out), input_context=MagicMock(), options={})
    with pikepdf.open(out) as pdf:
        assert all("/Thumb" in page.obj for page in pdf.pages)


def test_shrink_balanced_drops_thumbnails():
    pdf = _with_thumbnails()
    shrink(pdf, ["balanced", "skip=mrc_compress"], "out.pdf")
    assert getattr(pdf, c.PDFTL_SAVE_HINTS_ATTR)["drop_thumbnails"] is True
