# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/render.py

"""Render PDF pages to images"""

import logging
import os

import pdftl.core.constants as c
from pdftl.core.core_types import HelpExample, OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.dependencies import ensure_dependencies
from pdftl.utils.page_images import iter_pages_as_pil
from pdftl.utils.page_specs import expand_specs_to_pages
from pdftl.utils.progress import get_track_progress

logger = logging.getLogger(__name__)

_RENDER_LONG_DESC = """
The `render` operation converts PDF pages into raster images or a single PDF.
It respects page rotation, cropping, and current pipeline modifications.

You can specify a page range using standard page specifications (e.g., `1-5`, `even`).
If no pages are specified, all pages are rendered.

The `dpi=<val>` argument sets the raster image resolution, in dots per
inch (default: 150). It must be a positive number.

The default `<template>` is `page_%d.png`. The parameter `%d` is replaced
with the output page counter value, starting at `1`. Standard formatting
directives like `%03d` are supported.

**Single PDF Output:**
If the output template ends with `.pdf` and contains no `%` directive
(e.g., `output out.pdf`), all rendered pages will be combined into a
single PDF file. Note: This keeps all page images in memory until saved.

**Image Output:**
If rendering to images, the output format is guessed from the `<template>`
extension (e.g., `.png`, `.jpg`). If no extension is given, PNG is used.
"""

_RENDER_EXAMPLES = [
    HelpExample(
        desc="Render all pages at 150 dpi to `page_1.png`, `page_2.png`, ...", cmd="in.pdf render"
    ),
    HelpExample(
        desc="Render pages 1 to 5 at 300 dpi to `out001.png`, ...",
        cmd="in.pdf render 1-5 dpi=300 output out%03d.png",
    ),
    HelpExample(
        desc="Render odd pages into a single PDF document at 150 dpi",
        cmd="in.pdf render odd output rasterized.pdf",
    ),
]


def _save_single_pdf(image_generator, filename: str, dpi: float) -> int:
    """Helper to save all generated images into a single PDF in memory."""
    images = [img for _, img in image_generator]
    if not images:
        return 0

    try:
        images[0].save(filename, "PDF", resolution=dpi, save_all=True, append_images=images[1:])
        return len(images)
    except (OSError, ValueError) as exc:
        raise InvalidArgumentError(f"Failed to render single PDF. Details: {exc}") from exc


def _save_multiple_images(image_generator) -> int:
    """Helper to save generated images to individual files."""
    from PIL import Image

    Image.init()
    count = 0
    for filename, image in image_generator:
        _, extension = os.path.splitext(filename)
        fmt = "PNG" if not extension else extension.lstrip(".").upper()
        if fmt == "JPG":
            fmt = "JPEG"

        try:
            if fmt not in Image.SAVE:
                raise ValueError(
                    f"Unsupported image format: {fmt}. Choose from {list(Image.SAVE.keys())}"
                )
            image.save(filename, format=fmt)
        except ValueError as exc:
            raise InvalidArgumentError(
                f"Invalid render output template. Details:\n  {exc}"
            ) from exc
        count += 1
    return count


def render_cli_hook(result: OpResult, stage, _pipeline):
    """
    CLI-specific side effect: Writes the rendered images to disk.
    This function is only called by the CLI pipeline.
    """
    image_generator = result.data
    if not image_generator:
        return

    logger.info("Rendering pages to disk...")

    meta = result.meta or {}
    output_pattern = meta.get("output_pattern", "")
    dpi = meta.get("dpi", 150.0)

    is_single_pdf = output_pattern.lower().endswith(".pdf") and "%" not in output_pattern

    if is_single_pdf:
        count = _save_single_pdf(image_generator, output_pattern, dpi)
        logger.info("Rendered %s pages into a single PDF: %s", count, output_pattern)
    else:
        count = _save_multiple_images(image_generator)
        logger.info("Rendered %s images.", count)


def _parse_render_args(args):
    """Separates the dpi= kwarg from page specifications."""
    dpi = 150.0
    page_specs = []
    found_page_spec = False

    for arg in args:
        if arg.startswith("dpi="):
            try:
                dpi = float(arg.split("=", 1)[1])
                if dpi <= 0:
                    raise ValueError("should be positive")
            except ValueError as exc:
                raise InvalidArgumentError(
                    f"'render': invalid dpi '{arg}'. Should be a positive number."
                ) from exc
        else:
            found_page_spec = True
            page_specs.append(arg)

    if not found_page_spec:
        page_specs = ["1-end"]

    return dpi, page_specs


@register_operation(
    "render",
    tags=["images", "experimental", "alpha"],
    type="single input operation with optional output",
    desc="Render PDF pages as images or a single rasterized PDF",
    long_desc=_RENDER_LONG_DESC,
    examples=_RENDER_EXAMPLES,
    cli_hook=render_cli_hook,
    usage="<input> render [<page_specs>...] [dpi=<val>] [output <template>]",
    args=(
        [c.INPUT_PDF, c.OPERATION_ARGS],
        {
            "output_pattern": c.OUTPUT_PATTERN,
        },
    ),
    skip_pipeline_save=True,
)
def render_pdf(input_pdf, args, output_pattern="page_%d.png") -> OpResult:
    dpi, page_specs = _parse_render_args(args)

    ensure_dependencies("render", ["pypdfium2", "PIL"], "render")

    page_transforms = expand_specs_to_pages(page_specs, opened_pdfs=[input_pdf]) or []
    page_indices = [pt.index for pt in page_transforms]

    track_progress = get_track_progress(interactive=True)

    def _render_generator():
        # iter_pages_as_pil now efficiently fetches only requested pages in order
        page_iterator = iter_pages_as_pil(input_pdf, dpi, page_indices=page_indices)

        for output_idx, (_pdf_page_idx, image) in enumerate(
            track_progress(page_iterator, description="Rendering pages")
        ):
            try:
                # output_idx is 0-based, so +1 gives us sequential 1, 2, 3...
                filename = output_pattern % (output_idx + 1)
            except TypeError:
                filename = output_pattern

            yield filename, image

    return OpResult(
        success=True,
        pdf=input_pdf,
        data=_render_generator(),
        is_discardable=True,
        meta={"output_pattern": output_pattern, "dpi": dpi},
    )
