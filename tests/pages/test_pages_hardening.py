from pathlib import Path

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name

from pdftl.pages.add_pages import add_pages
from pdftl.utils.page_specs import PageTransform


@pytest.fixture
def scaled_link_pdf():
    src = pikepdf.new()
    W, H = 400, 600
    p1 = src.add_blank_page(page_size=(W, H))
    p2 = src.add_blank_page(page_size=(W, H))

    # /XYZ link coordinates
    x0, y0 = 50, 450
    link = Dictionary(
        Type=Name.Annot,
        Subtype=Name.Link,
        Rect=[0, 0, 100, 100],
        A=Dictionary(S=Name.GoTo, D=[p2.obj, Name.XYZ, x0, y0, 0]),
    )
    p1.Annots = Array([src.make_indirect(link)])

    scale = 2.0
    transforms = [
        PageTransform(src, 0, (90, True), scale),
        PageTransform(src, 1, (90, True), scale),
    ]

    out = pikepdf.new()
    add_pages(out, [src], transforms)
    return out, x0, y0, scale


def test_link_transform_is_idempotent():
    """
    Applying the same transform twice must produce identical results.
    """
    import hashlib
    import io

    import pikepdf

    def pdf_hash(pdf):
        with io.BytesIO() as buffer:
            data = pdf.save(buffer)
            return hashlib.sha256(buffer.getvalue()).hexdigest()

    src = pikepdf.new()
    src.add_blank_page()
    src.add_blank_page()

    transforms = [
        PageTransform(src, 0, (90, True), 1.5),
        PageTransform(src, 1, (90, True), 1.5),
    ]

    out1 = pikepdf.new()
    add_pages(out1, [src], transforms)

    out2 = pikepdf.new()
    add_pages(out2, [src], transforms)

    assert pdf_hash(out1) == pdf_hash(out2)


def test_link_scaled_correctly():
    import math

    import pdftl

    def get_rect(pdf, idx=0):
        annots = pdftl.dump_annots(pdf)
        return list(map(float, annots[idx]["Properties"]["/Rect"]))

    pdf_in = Path("tests/files/pdfs/link.pdf")
    rect_orig = get_rect(pdf_in)
    pdf_scaled = pdftl.cat(pdf_in, operation_args=["x2"])
    rect_scaled = get_rect(pdf_scaled)

    assert all(math.isclose(2 * a, b) for a, b in zip(rect_orig, rect_scaled))

    pdf_scaled_rotated = pdftl.cat(pdf_scaled, operation_args=["right"])
    rect_scaled_rotated = get_rect(pdf_scaled_rotated)
    # rotation should not change the coordinates reported
    assert all(math.isclose(2 * a, b) for a, b in zip(rect_orig, rect_scaled_rotated))

    pdf_scaled_rotated2 = pdftl.cat(pdf_in, operation_args=["x2right"])
    rect_scaled_rotated2 = get_rect(pdf_scaled_rotated2)
    # rotation should not change the coordinates reported
    assert all(math.isclose(2 * a, b) for a, b in zip(rect_orig, rect_scaled_rotated2))

    pdf_rotated = pdftl.cat(pdf_in, operation_args=["right"])
    rect_rotated = get_rect(pdf_rotated)
    pdf_rotated_scaled = pdftl.cat(pdf_rotated, operation_args=["x.5"])
    rect_rotated_scaled = get_rect(pdf_rotated_scaled)
    assert all(math.isclose(a, b) for a, b in zip(rect_orig, rect_rotated))
    assert all(math.isclose(0.5 * a, b) for a, b in zip(rect_orig, rect_rotated_scaled))
