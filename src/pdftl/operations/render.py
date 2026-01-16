# src/pdftl/operations/render.py

"""Render PDF pages to images"""

import io
import logging
import os

import pdftl.core.constants as c
from pdftl.core.registry import register_operation
from pdftl.core.types import HelpExample, OpResult
from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.dependencies import ensure_dependencies

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


def render_cli_hook(result: OpResult, _stage):
    """
    CLI-specific side effect: Writes the rendered images to disk.
    This function is only called by the CLI pipeline.
    """
    # The generator yields (filename, pil_image) tuples
    image_generator = result.data

    if not image_generator:
        return

    logger.info("Rendering pages to disk...")
    count = 0
    for filename, image in image_generator:
        _, extension = os.path.splitext(filename)
        if not extension:
            image.save(filename, format="png")
        else:
            try:
                image.save(filename)
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
            assert dpi > 0
        except (ValueError, AssertionError) as exc:
            raise InvalidArgumentError(
                f"'render': invalid dpi '{args[0]}' passed. " f"Should be a positive number."
            ) from exc

    ensure_dependencies("render", ["pypdfium2", "PIL"], "render")

    import pypdfium2 as pdfium

    # We define the generator here to capture the dependencies and settings
    def _render_generator():
        # Create a separate buffer for PDFium so we don't consume/close the main input_pdf
        # which needs to pass down the pipeline.
        pdf_buffer = io.BytesIO()
        input_pdf.save(pdf_buffer)
        pdf_buffer.seek(0)

        ui_pdf = None
        try:
            ui_pdf = pdfium.PdfDocument(pdf_buffer)
            scale = dpi / 72.0

            # Use provided pattern or default
            pattern = output_pattern or "page_%d.png"
            page_counter = 0

            for page in ui_pdf:
                page_counter += 1
                try:
                    filename = pattern % page_counter
                except TypeError as exc:
                    # Fallback if pattern is invalid
                    logger.warning(
                        f"Invalid pattern: '{pattern}'. Falling back to 'page_%d.png'. "
                        f"(Reason: {exc})"
                    )
                    filename = f"page_{page_counter}.png"

                bitmap = page.render(scale=scale)
                yield (filename, bitmap.to_pil())
        finally:
            # Clean up the PDFium resources and the specific buffer we created.
            # We do NOT close input_pdf here, as it is returned in OpResult.
            if ui_pdf:
                ui_pdf.close()
            pdf_buffer.close()
            logger.debug("Render generator finished: Cleaned up temporary buffer.")

    return OpResult(
        success=True,
        pdf=input_pdf,  # Pass the PDF down the pipeline
        data=_render_generator(),  # API users get the generator; CLI hook consumes it
        is_discardable=True,
        meta={
            "output_pattern": output_pattern,
        },
    )
