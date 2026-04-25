# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/render.py

"""Render PDF pages to images"""

import logging
import os

import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import HelpExample, OpResult
from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.progress import get_track_progress

logger = logging.getLogger(__name__)

_RENDER_LONG_DESC = """
The `render` operation converts PDF pages into raster images.
It respects page rotation, cropping, and current pipeline modifications.

The optional `<dpi>` argument is the raster image
resolution, in dots per inch (default: 150). It must be a
positive number.

The default `<template>` is `page_%d.png`. The parameter
`%d` is replaced with the output page counter value,
starting at `1`. You can use standard formatting directives
like `%03d`, for example, to get `001`, `002`, ...

The output file format is be guessed from the `<template>`
file extension, if that extension is supported by the Python
`PIL` library. Valid extensions include `.png`, `.pdf`,
`.jpg`. If no extension is given, PNG formatted images will
be saved.

**Warning** This operation is liable to change, not least
  because we should support saving a page range in the future (TODO).

"""

_RENDER_EXAMPLES = [
    HelpExample(
        desc="Render all pages at 150 dpi to `page_1.png`, `page_2.png`, ...", cmd="in.pdf render"
    ),
    HelpExample(
        desc="Render all pages at 75 dpi to `out001.png`, `out002.png`, ...",
        cmd="in.pdf render 75 output out%03d.png",
    ),
]


def render_cli_hook(result: OpResult, _stage, _pipeline):
    """
    CLI-specific side effect: Writes the rendered images to disk.
    This function is only called by the CLI pipeline.
    """
    image_generator = result.data

    if not image_generator:
        return

    logger.info("Rendering pages to disk...")
    count = 0
    for filename, image in image_generator:
        _, extension = os.path.splitext(filename)
        if not extension:
            image.save(filename, format="PNG")
        else:
            fmt = extension.lstrip(".").upper()
            if fmt == "JPG":
                fmt = "JPEG"
            try:
                image.save(filename, format=fmt)
            except ValueError as exc:
                raise InvalidArgumentError(f"Invalid render output template. Details: {exc}")

        count += 1

    logger.info("Rendered %s images.", count)


@register_operation(
    "render",
    tags=["images", "experimental", "alpha", "TODO"],
    type="single input operation with optional output",
    desc="Render PDF pages as images",
    long_desc=_RENDER_LONG_DESC,
    examples=_RENDER_EXAMPLES,
    cli_hook=render_cli_hook,
    usage="<input> render [<dpi>] [output <template>]",
    args=(
        [c.INPUT_PDF, c.OPERATION_ARGS],
        {
            "output_pattern": c.OUTPUT_PATTERN,
        },
    ),
    skip_pipeline_save=True,
)
def render_pdf(input_pdf, args, output_pattern="page_%d.png") -> OpResult:
    if len(args) > 1:
        raise InvalidArgumentError(
            "'render' takes at most one argument, but you passed %s", len(args)
        )

    if not args:
        dpi = 150.0
    else:
        try:
            dpi = float(args[0])
            if not (dpi > 0):
                raise ValueError(f"dpi={dpi} should be positive")
        except (ValueError, AssertionError) as exc:
            raise InvalidArgumentError(
                f"'render': invalid dpi '{args[0]}' passed. Should be a positive number."
            ) from exc

    from pdftl.utils.dependencies import ensure_dependencies

    ensure_dependencies("render", ["pypdfium2", "PIL"], "render")

    track_progress = get_track_progress(interactive=True)

    def _render_generator():
        from pdftl.utils.page_images import iter_pages_as_pil

        pattern = output_pattern or "page_%d.png"

        for i, image in track_progress(
            iter_pages_as_pil(input_pdf, dpi), description="Rendering pages"
        ):
            page_number = i + 1
            try:
                filename = pattern % page_number
            except TypeError as exc:
                logger.warning(
                    f"Invalid pattern: '{pattern}'. Falling back to 'page_%d.png'. (Reason: {exc})"
                )
                filename = f"page_{page_number}.png"

            yield filename, image

    return OpResult(
        success=True,
        pdf=input_pdf,
        data=_render_generator(),
        is_discardable=True,
        meta={"output_pattern": output_pattern},
    )
