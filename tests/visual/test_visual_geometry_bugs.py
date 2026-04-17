from pathlib import Path

import pikepdf
import pytest

import pdftl.api


def inject_geometry(src_pdf, dest_path, mediabox=None, rotation=None, page_rotations=None):
    """
    Helper to inject tricky geometric edge-cases (non-zero origins, rotations)
    into an existing content-rich PDF fixture so visual diffs have features to compare.
    """
    pdf = pikepdf.Pdf.open(src_pdf)
    for i, page in enumerate(pdf.pages):
        if mediabox is not None:
            page.MediaBox = pikepdf.Array(mediabox)
        if rotation is not None:
            page.Rotate = rotation
        if page_rotations is not None and i in page_rotations:
            page.Rotate = page_rotations[i]

    pdf.save(dest_path)
    return str(dest_path)


# --- Visual Geometry Tests ---


def test_visual_chop_nonzero_y_origin(two_page_pdf, tmp_path, assert_pdf_match):
    in_pdf = inject_geometry(two_page_pdf, tmp_path / "in.pdf", mediabox=[0, 200, 400, 600])
    # This will generate: test_visual_chop_nonzero_y_origin_before.pdf
    assert_pdf_match(in_pdf, suffix="before")

    result = pdftl.api.chop(pdf=in_pdf, operation_args=["rows2"])

    # This will generate: test_visual_chop_nonzero_y_origin_after.pdf
    assert_pdf_match(result, suffix="after")


def test_visual_chop_nonzero_x_origin(two_page_pdf, tmp_path, assert_pdf_match):
    in_pdf = inject_geometry(two_page_pdf, tmp_path / "in.pdf", mediabox=[100, 0, 500, 400])
    assert_pdf_match(in_pdf, suffix="before")

    result = pdftl.api.chop(pdf=in_pdf, operation_args=["cols2"])
    assert_pdf_match(result, suffix="after")


def test_visual_chop_rotated_nonzero_origin(two_page_pdf, tmp_path, assert_pdf_match):
    in_pdf = inject_geometry(
        two_page_pdf, tmp_path / "in_rotated.pdf", mediabox=[100, 200, 600, 900], rotation=90
    )
    assert_pdf_match(in_pdf, suffix="before")

    result = pdftl.api.chop(pdf=in_pdf, operation_args=["cols2"])
    assert_pdf_match(result, suffix="after")


def test_visual_chop_rotation_180_and_270(two_page_pdf, tmp_path, assert_pdf_match):
    in_pdf = inject_geometry(
        two_page_pdf, tmp_path / "multi_rotation.pdf", page_rotations={0: 180, 1: 270}
    )
    assert_pdf_match(in_pdf, suffix="before")

    result = pdftl.api.chop(pdf=in_pdf, operation_args=["cols2"])
    assert_pdf_match(result, suffix="after")
