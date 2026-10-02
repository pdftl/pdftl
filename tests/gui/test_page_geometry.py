# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# tests/gui/test_page_geometry.py

"""Tests for page_geometry: Qt-free displayed-space transforms, dimension
string formatting, and geometry grouping.

Ground truth for the transform tests is independent of the module under
test: a PDF is built with pikepdf with a known MediaBox/CropBox and a small
filled square at a known user-space location, rendered with pypdfium2
directly (not through thumbs.py), and the dark pixels' centroid is located
with plain numpy. Only the pixel->user-space conversion goes through
page_geometry; nothing here assumes the module's own transform is correct.
"""

from pathlib import Path

import numpy as np
import pikepdf
import pypdfium2 as pdfium
import pytest

from pdftl.gui.page_geometry import (
    PageGeometry,
    _trim,
    group_by_geometry,
    pts_to_dim_str,
    read_page_geometries,
)

CM_PT = 72 / 2.54
MM_PT = 72 / 25.4


def _make_square_pdf(
    path: Path,
    mediabox: tuple[float, float, float, float],
    square: tuple[float, float, float, float],
    cropbox: tuple[float, float, float, float] | None = None,
    rotate: int = 0,
    password: str | None = None,
) -> Path:
    """One page with a black square filled at a known absolute user-space rect."""
    pdf = pikepdf.new()
    width = mediabox[2] - mediabox[0]
    height = mediabox[3] - mediabox[1]
    pdf.add_blank_page(page_size=(width, height))
    page = pdf.pages[0]
    page.MediaBox = pikepdf.Array(mediabox)
    if cropbox is not None:
        page.CropBox = pikepdf.Array(cropbox)
    if rotate:
        page.Rotate = rotate
    sx, sy, sw, sh = square
    content = f"0 0 0 rg {sx} {sy} {sw} {sh} re f".encode()
    page.Contents = pdf.make_stream(content)
    if password is not None:
        pdf.save(
            path,
            encryption=pikepdf.Encryption(owner="owner-pw", user=password, R=4),
        )
    else:
        pdf.save(path)
    return path


def _dark_pixel_centroid(
    path: Path, scale: float, password: str | None = None
) -> tuple[float, float]:
    """Renders page 1 and returns the dark pixels' centroid in pixel space
    (origin top-left, x right, y down) -- independent of page_geometry."""
    doc = pdfium.PdfDocument(str(path), password=password)
    try:
        page = doc[0]
        try:
            bitmap = page.render(scale=scale, rev_byteorder=True, fill_color=(255, 255, 255, 255))
            try:
                arr = bitmap.to_numpy()
            finally:
                bitmap.close()
        finally:
            page.close()
    finally:
        doc.close()
    channel_sum = arr[..., 0].astype(int) + arr[..., 1].astype(int) + arr[..., 2].astype(int)
    dark = channel_sum < 384
    ys, xs = np.nonzero(dark)
    assert xs.size > 0, "no dark pixels rendered"
    return float(xs.mean()), float(ys.mean())


SCALE = 4.0


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_rotation_centroid_matches_known_user_space_location(tmp_path, rotate):
    mediabox = (10.0, 20.0, 210.0, 120.0)  # origin (10,20), 200x100
    square = (60.0, 45.0, 20.0, 10.0)  # center (70, 50)
    path = _make_square_pdf(tmp_path / f"r{rotate}.pdf", mediabox, square, rotate=rotate)

    geometries = read_page_geometries(path, [1])
    geometry = geometries[1]
    assert geometry.rotation == rotate

    px, py = _dark_pixel_centroid(path, SCALE)
    dx, dy = geometry.pixels_to_displayed(px, py, SCALE)
    ux, uy = geometry.displayed_to_user(dx, dy)

    assert ux == pytest.approx(70.0, abs=1.0)
    assert uy == pytest.approx(50.0, abs=1.0)


@pytest.mark.parametrize("rotate", [0, 90, 180, 270])
def test_cropbox_offset_distinct_from_mediabox(tmp_path, rotate):
    mediabox = (0.0, 0.0, 300.0, 200.0)
    cropbox = (50.0, 40.0, 250.0, 140.0)  # 200x100, origin (50, 40)
    square = (110.0, 85.0, 20.0, 10.0)  # center (120, 90), inside cropbox
    path = _make_square_pdf(
        tmp_path / f"c{rotate}.pdf", mediabox, square, cropbox=cropbox, rotate=rotate
    )

    geometry = read_page_geometries(path, [1])[1]
    # pdfium renders the CropBox, so displayed size must match it, not the MediaBox.
    assert {geometry.displayed_width, geometry.displayed_height} == {200.0, 100.0}

    px, py = _dark_pixel_centroid(path, SCALE)
    dx, dy = geometry.pixels_to_displayed(px, py, SCALE)
    ux, uy = geometry.displayed_to_user(dx, dy)

    assert ux == pytest.approx(120.0, abs=1.0)
    assert uy == pytest.approx(90.0, abs=1.0)


def test_no_cropbox_falls_back_to_mediabox(tmp_path):
    mediabox = (0.0, 0.0, 150.0, 80.0)
    square = (10.0, 10.0, 10.0, 10.0)
    path = _make_square_pdf(tmp_path / "nocrop.pdf", mediabox, square)

    geometry = read_page_geometries(path, [1])[1]

    assert geometry.origin_x == 0.0
    assert geometry.origin_y == 0.0
    assert geometry.width == 150.0
    assert geometry.height == 80.0


def test_displayed_to_pixels_and_back(tmp_path):
    geometry = _geometry(200.0, 100.0)
    px, py = geometry.displayed_to_pixels(10.0, 20.0, scale=2.0)
    assert (px, py) == (20.0, 40.0)
    dx, dy = geometry.pixels_to_displayed(px, py, scale=2.0)
    assert (dx, dy) == (10.0, 20.0)


def test_trim_with_zero_decimals_has_no_decimal_point():
    assert _trim(5.0, 0) == "5"
    assert _trim(-0.0, 0) == "0"


def test_user_to_displayed_round_trips_through_displayed_to_user(tmp_path):
    mediabox = (5.0, 5.0, 205.0, 105.0)
    path = _make_square_pdf(tmp_path / "rt.pdf", mediabox, (10, 10, 5, 5), rotate=90)
    geometry = read_page_geometries(path, [1])[1]

    for x, y in [(5.0, 5.0), (205.0, 105.0), (110.0, 60.0)]:
        dx, dy = geometry.user_to_displayed(x, y)
        ux, uy = geometry.displayed_to_user(dx, dy)
        assert ux == pytest.approx(x)
        assert uy == pytest.approx(y)


def test_password_protected_pdf_needs_password(tmp_path):
    mediabox = (0.0, 0.0, 100.0, 100.0)
    path = _make_square_pdf(tmp_path / "enc.pdf", mediabox, (10, 10, 10, 10), password="secret")

    with pytest.raises(pikepdf.PasswordError):
        read_page_geometries(path, [1])

    geometries = read_page_geometries(path, [1], password="secret")
    assert geometries[1].width == 100.0


# --- pts_to_dim_str ---


@pytest.mark.parametrize(
    "value_pts, unit",
    [
        (0.0, "pt"),
        (72.0, "pt"),
        (100.5, "pt"),
        (72.0, "in"),
        (36.0, "in"),
        (CM_PT, "cm"),
        (MM_PT * 10, "mm"),
        (1.0, "pt"),
    ],
)
def test_pts_to_dim_str_round_trips(value_pts, unit):
    from pdftl.utils.dimensions import dim_str_to_pts

    text = pts_to_dim_str(value_pts, unit)
    assert text.endswith(unit)
    round_tripped = dim_str_to_pts(text)
    assert round_tripped == pytest.approx(value_pts, abs=0.05)


def test_pts_to_dim_str_percent_round_trips():
    from pdftl.utils.dimensions import dim_str_to_pts

    total = 400.0
    text = pts_to_dim_str(100.0, "%", total_dimension=total)
    assert text.endswith("%")
    round_tripped = dim_str_to_pts(text, total_dimension=total)
    assert round_tripped == pytest.approx(100.0, abs=0.5)


def test_pts_to_dim_str_no_trailing_zeros():
    assert pts_to_dim_str(72.0, "pt") == "72pt"
    assert pts_to_dim_str(0.0, "pt") == "0pt"


def test_pts_to_dim_str_negative_zero_normalizes():
    assert pts_to_dim_str(-0.0001, "pt") == "0pt"


def test_pts_to_dim_str_percent_without_total_raises():
    with pytest.raises(ValueError, match="total_dimension"):
        pts_to_dim_str(10.0, "%")


def test_pts_to_dim_str_unknown_unit_raises():
    with pytest.raises(ValueError, match="Unknown unit"):
        pts_to_dim_str(10.0, "furlong")


# --- grouping ---


def _geometry(width, height, rotation=0, origin=(0.0, 0.0)) -> PageGeometry:
    return PageGeometry(origin[0], origin[1], width, height, rotation)


def test_group_by_geometry_groups_matching_pages_in_first_appearance_order():
    geometries = {
        1: _geometry(200, 100),
        2: _geometry(300, 400),
        3: _geometry(200, 100),
        4: _geometry(200, 100, rotation=90),
    }
    groups = group_by_geometry(geometries)
    assert groups == [[1, 3], [2], [4]]


def test_group_by_geometry_distinguishes_origin():
    geometries = {
        1: _geometry(200, 100, origin=(0.0, 0.0)),
        2: _geometry(200, 100, origin=(5.0, 5.0)),
    }
    groups = group_by_geometry(geometries)
    assert groups == [[1], [2]]


def test_group_by_geometry_tolerates_tiny_float_noise():
    geometries = {
        1: _geometry(200.0, 100.0),
        2: _geometry(200.0 + 1e-6, 100.0 - 1e-6),
    }
    groups = group_by_geometry(geometries)
    assert groups == [[1, 2]]


def test_group_by_geometry_empty():
    assert group_by_geometry({}) == []
