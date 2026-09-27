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
from pdftl.utils.keyval_parser import parse_keyval_list

logger = logging.getLogger(__name__)

_VALID_API_FORMATS = {"png", "jpg", "jpeg", "pdf"}
_DEFAULT_PATTERN = "page_%d.png"

_RENDER_LONG_DESC = """
The `render` operation converts PDF pages into raster images or a single PDF.
It respects page rotation, cropping, and current pipeline modifications.

You can specify a page range using standard page specifications (e.g., `1-5`, `even`).
If no pages are specified, all pages are rendered.

The `dpi=<val>` argument sets the raster image resolution, in dots per
inch (default: 150). It must be a positive number.

The `png_compression=<level>` argument sets the PNG compression, for PNG output.
It must be an integer between 1 and 9, where 9 is the highest compression level,
and the slowest. The default level is 9.

The default `<template>` is `page_%d.png`. The parameter `%d` is replaced
with the output page counter value, starting at `1`. Standard formatting
directives like `%03d` are supported.

**Over the API:** the server never honors client-supplied filesystem paths,
so `output <template>` has no effect there. Use `format=<png|jpg|pdf>`
instead to select the response shape: `png`/`jpg` return a zip of one image
per page, `pdf` returns a single combined PDF. `format=` is accepted (and
ignored by `output`-based CLI rendering) on the CLI too, for consistency.

**Single PDF Output:**
If the output template ends with `.pdf` and contains no `%` directive
(e.g., `output out.pdf`), all rendered pages will be combined into a
single PDF file, one page in memory at a time.

**Image Output:**
If rendering to images, the output format is guessed from the `<template>`
extension (e.g., `.png`, `.jpg`). If no extension is given, PNG is used.

**In a pipeline:**
When a later stage follows (`render dpi=100 --- shrink target=5MB output
out.pdf`), `render` hands it the rendered pages as an image-only PDF, and
writes files only if `output <template>` is given.
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
    HelpExample(
        desc="Rasterise at 100 dpi, then shrink the result to at most 2 MB",
        cmd="in.pdf render dpi=100 --- shrink target=2MB output out.pdf",
    ),
]


def _images_to_pdf(images, dest, dpi: float) -> int:
    """Write rendered pages to one PDF at `dest` (a path or binary file); return the count.

    Each page is encoded on its own and only its compressed form is kept, so
    memory stays at one decoded page however many there are.
    """
    import io

    import pikepdf

    out = pikepdf.new()
    sources = []  # page streams are copied lazily from these until the save
    for image in images:
        buf = io.BytesIO()
        image.save(buf, "PDF", resolution=dpi)
        sources.append(pikepdf.open(buf))
        out.pages.extend(sources[-1].pages)
    if sources:
        out.save(dest)
    return len(sources)


def _save_single_pdf(image_generator, filename: str, dpi: float) -> int:
    """Save all generated images into a single PDF."""
    try:
        return _images_to_pdf((img for _, img in image_generator), filename, dpi)
    except (OSError, ValueError) as exc:
        raise InvalidArgumentError(f"Failed to render single PDF. Details: {exc}") from exc


def _save_image(filename: str, image, png_compression: int) -> None:
    from PIL import Image

    Image.init()
    _, extension = os.path.splitext(filename)
    fmt = "PNG" if not extension else extension.lstrip(".").upper()
    if fmt == "JPG":
        fmt = "JPEG"

    save_kw_args = {"format": fmt}
    if fmt == "PNG":
        save_kw_args.update({"compress_level": png_compression})

    try:
        if fmt not in Image.SAVE:
            raise ValueError(
                f"Unsupported image format: {fmt}. Choose from {list(Image.SAVE.keys())}"
            )
        image.save(filename, **save_kw_args)
    except ValueError as exc:
        raise InvalidArgumentError(f"Invalid render output template. Details:\n  {exc}") from exc


def _save_multiple_images(image_generator, png_compression=9) -> int:
    """Helper to save generated images to individual files."""
    logger.debug("png_compression=%s", png_compression)
    count = 0
    for filename, image in image_generator:
        _save_image(filename, image, png_compression)
        count += 1
    return count


def _is_single_pdf(pattern: str) -> bool:
    return pattern.lower().endswith(".pdf") and "%" not in pattern


def _pipeline_pdf(image_generator, pattern: str | None, dpi: float, png_compression: int):
    """The rendered pages as one PDF for the next stage, also written to `pattern` if given."""
    import io

    import pikepdf

    def images():
        for filename, image in image_generator:
            if pattern and not _is_single_pdf(pattern):
                _save_image(filename, image, png_compression)
            yield image

    buf = io.BytesIO()
    try:
        _images_to_pdf(images(), buf, dpi)
    except (OSError, ValueError) as exc:
        raise InvalidArgumentError(f"Failed to render single PDF. Details: {exc}") from exc
    if pattern and _is_single_pdf(pattern):
        with open(pattern, "wb") as f:
            f.write(buf.getvalue())
    buf.seek(0)
    return pikepdf.open(buf)


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
    png_compression = meta.get("png_compression", 9)

    if _is_single_pdf(output_pattern):
        count = _save_single_pdf(image_generator, output_pattern, dpi)
        logger.info("Rendered %s pages into a single PDF: %s", count, output_pattern)
    else:
        count = _save_multiple_images(image_generator, png_compression=png_compression)
        logger.info("Rendered %s images.", count)


def _parse_render_args(args):
    """Separates the dpi= kwarg from page specifications."""
    dpi = 150.0
    page_specs = []
    png_compression = 9
    fmt = None

    try:
        parsed = parse_keyval_list(
            args, allowed_keys=["dpi", "png_compression", "format"], bare_tokens=page_specs
        )
    except InvalidArgumentError as exc:
        raise InvalidArgumentError(f"Could not parse `render` arguments {args}: {exc}")
    if "dpi" in parsed:
        val_str = parsed["dpi"]
        try:
            dpi = float(val_str)
            if dpi <= 0:
                raise ValueError("dpi should be positive")
        except ValueError as exc:
            raise InvalidArgumentError(
                f"'render': invalid dpi '{val_str}'. Should be a positive number."
            ) from exc
    if "png_compression" in parsed:
        val_str = parsed["png_compression"]
        try:
            png_compression = int(val_str)
            if not 1 <= png_compression <= 9:
                raise ValueError("png_compression should be between 1 and 9")
        except ValueError as exc:
            raise InvalidArgumentError(
                f"'render': invalid png_compression '{val_str}'. "
                "Should be an integer between 1 and 9."
            ) from exc
    if "format" in parsed:
        fmt = parsed["format"].lower()
        if fmt not in _VALID_API_FORMATS:
            raise InvalidArgumentError(
                f"'render': invalid format '{fmt}'. Choose from {sorted(_VALID_API_FORMATS)}."
            )

    if not page_specs:
        page_specs = ["1-end"]

    return dpi, page_specs, png_compression, fmt


def _pil_format_and_ext(fmt: str) -> tuple[str, str]:
    if fmt in ("jpg", "jpeg"):
        return "JPEG", "jpg"
    return fmt.upper(), fmt


def render_api_serializer(data, meta):
    """Serializes render's (filename, PIL.Image) generator for the API.

    Keyed off the explicit `format=` argument rather than sniffing a
    filesystem output path -- the server forbids clients from controlling
    output paths, so the CLI's "guess format from output template"
    convention (see render_cli_hook) can't apply here. Independent of
    render_cli_hook, which is unaffected and still used for CLI runs.
    """
    import io as _io
    import zipfile

    meta = meta or {}
    fmt = meta.get("format") or "png"
    dpi = meta.get("dpi", 150.0)
    png_compression = meta.get("png_compression", 9)

    if fmt == "pdf":
        buf = _io.BytesIO()
        if not _images_to_pdf((img for _, img in data), buf, dpi):
            return b"", {"kind": "empty"}
        return buf.getvalue(), {"kind": "pdf"}

    pil_format, ext = _pil_format_and_ext(fmt)

    buf = _io.BytesIO()
    count = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for idx, (_filename, image) in enumerate(data, start=1):
            img_buf = _io.BytesIO()
            save_kw = {"format": pil_format}
            if pil_format == "PNG":
                save_kw["compress_level"] = png_compression
            image.save(img_buf, **save_kw)
            zf.writestr(f"page_{idx}.{ext}", img_buf.getvalue())
            count += 1
    return buf.getvalue(), {"kind": "zip", "count": count}


@register_operation(
    "render",
    tags=["images", "from_scratch"],
    type="single input operation",
    desc="Render PDF pages as images or a single rasterized PDF",
    long_desc=_RENDER_LONG_DESC,
    examples=_RENDER_EXAMPLES,
    cli_hook=render_cli_hook,
    usage="<input> render [<page_specs>...] [dpi=<val>] [output <template>]",
    api_serializer=render_api_serializer,
    args=(
        [c.INPUT_PDF, c.OPERATION_ARGS],
        {
            "output_pattern": c.OUTPUT,
            "is_last_stage": c.IS_LAST_STAGE,
        },
    ),
    skip_pipeline_save=True,
)
def render_pdf(input_pdf, args, output_pattern=None, is_last_stage=True) -> OpResult:
    dpi, page_specs, png_compression, fmt = _parse_render_args(args)
    explicit_pattern = output_pattern
    output_pattern = output_pattern or _DEFAULT_PATTERN

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

    if not is_last_stage:
        rendered = _pipeline_pdf(_render_generator(), explicit_pattern, dpi, png_compression)
        return OpResult(
            success=True, pdf=rendered, summary=f"rendered {len(rendered.pages)} pages"
        )

    return OpResult(
        success=True,
        pdf=input_pdf,
        data=_render_generator(),
        is_discardable=True,
        meta={
            "output_pattern": output_pattern,
            "dpi": dpi,
            "png_compression": png_compression,
            "format": fmt,
        },
    )
