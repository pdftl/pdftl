# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/images/stamper.py

"""
Low-level PDF image stamping business logic.

This module houses the core PDF editing and graphic stream modification logic
independent of CLI registration. It resolves target page indices, translates
visual parameters into native page coordinates accounting for layout rotation,
and injects the corresponding PDF graphics operators cleanly.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pikepdf

from pdftl.utils.dimensions import dim_str_to_pts
from pdftl.utils.page_specs import page_numbers_matching_page_spec
from pdftl.utils.images.embedder import create_image_xobject
from pdftl.utils.images.placement import calculate_placement_matrix

logger = logging.getLogger(__name__)


def _resolve_target_pages(pdf: pikepdf.Pdf, pages: str) -> list[int]:
    """Resolves target 1-based page specifications to 0-based page indices."""
    total_pages = len(pdf.pages)
    matched_page_nums = page_numbers_matching_page_spec(pages, total_pages)
    return [num - 1 for num in matched_page_nums]


def _parse_dimensions(
    width: str | None, height: str | None, total_w: float, total_h: float
) -> tuple[float | None, float | None]:
    """Translates CLI width and height dimensions into PDF points."""
    req_width = (
        dim_str_to_pts(width, axis="width", total_dimension=total_w) if width is not None else None
    )
    req_height = (
        dim_str_to_pts(height, axis="height", total_dimension=total_h)
        if height is not None
        else None
    )
    return req_width, req_height


def _parse_offset_dim(offset_str: str, axis: str, total_dim: float) -> float:
    """Translates a single offset dimension string into displacement points."""
    if not offset_str:
        return 0.0
    return dim_str_to_pts(offset_str.strip(), axis=axis, total_dimension=total_dim)


def _embed_single_image(
    pdf: pikepdf.Pdf, img_path: str | Path
) -> tuple[pikepdf.Stream, tuple[float, float]]:
    """Embeds a single image file as a PDF XObject and extracts pixel dimensions."""
    from PIL import Image

    path = Path(img_path)
    file_exists = path.exists()
    if not file_exists:
        raise FileNotFoundError(f"Image file not found: {path}")

    xobj = create_image_xobject(pdf, path)
    pil_img = Image.open(path)
    img_size = (float(pil_img.width), float(pil_img.height))
    pil_img.close()
    return xobj, img_size


def _embed_images(
    pdf: pikepdf.Pdf, images: list[str | Path]
) -> list[tuple[pikepdf.Stream, tuple[float, float]]]:
    """Loads and embeds raw images as native PDF XObjects with size metadata."""
    return [_embed_single_image(pdf, img_path) for img_path in images]


def _get_page_geometry(
    page: pikepdf.Page,
) -> tuple[tuple[float, float, float, float], int]:
    """Safely extracts native CropBox coordinates and normalized rotation value."""
    box = page.cropbox
    cx1, cy1, cx2, cy2 = float(box[0]), float(box[1]), float(box[2]), float(box[3])
    rotation = int(page.rotation) % 360
    return (cx1, cy1, cx2, cy2), rotation


def _get_layout_box_bounds(
    cx1: float, cy1: float, cx2: float, cy2: float, rotation: int
) -> tuple[float, float, float, float]:
    """Adjusts boundary mapping boxes for rotation visual orientation shifts."""
    native_w = cx2 - cx1
    native_h = cy2 - cy1
    is_rotated = rotation in (90, 270)
    if is_rotated:
        return (0.0, 0.0, native_h, native_w)
    return (0.0, 0.0, native_w, native_h)


def _map_display_rotation_matrix(
    rotation: int,
    a_v: float,
    d_v: float,
    e_v: float,
    f_v: float,
    coords: tuple[float, float, float, float],
) -> tuple[float, float, float, float, float, float]:
    """Translates standard visual placement matrix back into native PDF coordinates."""
    cx1, cy1, cx2, cy2 = coords

    if rotation == 90:
        return 0.0, a_v, -d_v, 0.0, -f_v + cx2, e_v + cy1
    if rotation == 180:
        return -a_v, 0.0, 0.0, -d_v, -e_v + cx2, -f_v + cy2
    if rotation == 270:
        return 0.0, -a_v, d_v, 0.0, f_v + cx1, -e_v + cy2

    return a_v, 0.0, 0.0, d_v, e_v + cx1, f_v + cy1


def _build_graphics_operators(
    pdf: pikepdf.Pdf,
    page: pikepdf.Page,
    img_name: pikepdf.Name,
    matrix: tuple[float, float, float, float, float, float],
    opacity: float,
) -> bytes:
    """Generates byte-level PDF graphics operators (soft masks, transformations, drawings)."""
    import pikepdf

    operators = [b"q\n"]
    has_opacity = 0.0 <= opacity < 1.0
    if has_opacity:
        extgstate = pikepdf.Dictionary(Type=pikepdf.Name("/ExtGState"), ca=opacity, CA=opacity)
        gs_name = page.add_resource(extgstate, pikepdf.Name("/ExtGState"), pikepdf.Name("/GS"))
        operators.append(f"{gs_name} gs\n".encode())

    a, b, c, d, e, f = matrix
    operators.append(f"{a:.4f} {b:.4f} {c:.4f} {d:.4f} {e:.4f} {f:.4f} cm\n".encode())
    operators.append(f"{img_name} Do\n".encode())
    operators.append(b"Q\n")

    return b"".join(operators)


def _inject_to_array(
    page: pikepdf.Page, contents: pikepdf.Array, new_stream: pikepdf.Stream, underlay: bool
) -> None:
    """Prepends or appends a stream to a target page contents array."""
    import pikepdf

    if underlay:
        page.Contents = pikepdf.Array([new_stream] + list(contents))
        return
    page.Contents.append(new_stream)


def _inject_to_single_stream(
    page: pikepdf.Page, contents: pikepdf.Stream, new_stream: pikepdf.Stream, underlay: bool
) -> None:
    """Wraps an existing single content stream and a new stream in an array."""
    import pikepdf

    if underlay:
        page.Contents = pikepdf.Array([new_stream, contents])
        return
    page.Contents = pikepdf.Array([contents, new_stream])


def _inject_stream(pdf: pikepdf.Pdf, page: pikepdf.Page, cmd_bytes: bytes, underlay: bool) -> None:
    """Inserts a freshly compiled stream as either a PDF overlay or underlay."""
    import pikepdf

    new_stream = pdf.make_stream(cmd_bytes)
    contents = page.get("/Contents")

    if contents is None:
        page.Contents = new_stream
        return

    is_array = isinstance(contents, pikepdf.Array)
    if is_array:
        _inject_to_array(page, contents, new_stream, underlay)
        return

    _inject_to_single_stream(page, contents, new_stream, underlay)


def _stamp_single_item(
    pdf: pikepdf.Pdf,
    page: pikepdf.Page,
    item: tuple[pikepdf.Stream, tuple[float, float]],
    box_bounds: tuple[float, float, float, float],
    req_width: float | None,
    req_height: float | None,
    dx: float,
    dy: float,
    scale_mode: str,
    position: str,
    coords: tuple[float, float, float, float],
    rotation: int,
    opacity: float,
    underlay: bool,
) -> None:
    """Renders a single image item onto the page with absolute spatial transformation."""
    import pikepdf

    xobj, img_size = item
    img_name = page.add_resource(xobj, pikepdf.Name("/XObject"), pikepdf.Name("/Im"))

    v_matrix = calculate_placement_matrix(
        img_size=img_size,
        box_bounds=box_bounds,
        requested_size=(req_width, req_height),
        scale_mode=scale_mode,
        anchor=position,  # Map position to anchor parameter
        offset=(dx, dy),
    )

    a_v, _, _, d_v, e_v, f_v = v_matrix
    final_matrix = _map_display_rotation_matrix(rotation, a_v, d_v, e_v, f_v, coords)
    cmd_bytes = _build_graphics_operators(pdf, page, img_name, final_matrix, opacity)
    _inject_stream(pdf, page, cmd_bytes, underlay)


def _stamp_page_with_images(
    pdf: pikepdf.Pdf,
    page_idx: int,
    xobjects: list[tuple[pikepdf.Stream, tuple[float, float]]],
    req_width: float | None,
    req_height: float | None,
    dx: float,
    dy: float,
    scale_mode: str,
    position: str,
    opacity: float,
    underlay: bool,
) -> None:
    """Handles extracting page layout contexts and iterates over our embedded image stubs."""
    page = pdf.pages[page_idx]
    coords, rotation = _get_page_geometry(page)
    box_bounds = _get_layout_box_bounds(*coords, rotation=rotation)

    for item in xobjects:
        _stamp_single_item(
            pdf,
            page,
            item,
            box_bounds,
            req_width,
            req_height,
            dx,
            dy,
            scale_mode,
            position,
            coords,
            rotation,
            opacity,
            underlay,
        )


def stamp_images_on_pdf(
    pdf: pikepdf.Pdf,
    images: list[str | Path],
    pages: str = "1-end",
    underlay: bool = False,
    scale_mode: str = "none",
    position: str = "bottom-left",
    width: str | None = None,
    height: str | None = None,
    offset_x: str = "0",
    offset_y: str = "0",
    opacity: float = 1.0,
) -> None:
    """Stamps the requested images onto the resolved pages of the PDF.

    This function utilizes native pikepdf modifications to inject graphic state
    saving and restoring operators safely surrounding our new image drawing operations,
    strictly protecting existing graphics streams on the target page. It is fully
    rotation-aware, correctly translating visual alignment properties to native PDF
    coordinates if a target page has a /Rotate parameter.
    """
    target_page_indices = _resolve_target_pages(pdf, pages)
    if not target_page_indices:
        logger.warning("No pages matched the page specification: '%s'", pages)
        return

    xobjects = _embed_images(pdf, images)

    for page_idx in target_page_indices:
        page = pdf.pages[page_idx]
        coords, rotation = _get_page_geometry(page)
        box_bounds = _get_layout_box_bounds(*coords, rotation=rotation)
        v_width = box_bounds[2] - box_bounds[0]
        v_height = box_bounds[3] - box_bounds[1]

        req_width, req_height = _parse_dimensions(width, height, v_width, v_height)
        dx = _parse_offset_dim(offset_x, axis="width", total_dim=v_width)
        dy = _parse_offset_dim(offset_y, axis="height", total_dim=v_height)

        _stamp_page_with_images(
            pdf,
            page_idx,
            xobjects,
            req_width,
            req_height,
            dx,
            dy,
            scale_mode,
            position,
            opacity,
            underlay,
        )
