# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/modify_images.py
from __future__ import annotations
import logging
from typing import Any
from collections.abc import Callable

import pdftl.core.constants as c
from pdftl.core.core_types import HelpExample, OpResult
from pdftl.core.registry import register_operation, registry
from pdftl.exceptions import InvalidArgumentError, OperationError
from pdftl.operations.helpers.image_processor import (
    run_parallel_image_job,
    ImageContext,
    ensure_thread_safe,
    get_orig_stream_size,
    encode_and_update_pdf_image,
)
from pdftl.operations.parsers.modify_images_parser import parse_modify_images_args
from pdftl.utils.dependencies import ensure_dependencies
from pdftl.utils.images.finders import extract_pdf_images
from pdftl.utils.page_specs import page_numbers_matching_page_spec
from pdftl.utils.pikepdf_compatibility_utils import (
    as_pil_image_compat,
    image_extraction_errors,
)

logger = logging.getLogger(__name__)


##################################################
# We can try with this terminolgy:
##################################################
# pdftl operation name: modify_images
# Short description: Apply in-place image modifications and effects
# class name: ImageModifier
# help prefix: "mod:"
# in text: image modifier
##################################################


class LazyImageModifierHelpProxy:
    """Delays help generation until the exact moment someone runs `pdftl help`."""

    def __str__(self) -> str:
        header = """Processes bitmap page image assets using an ordered sequence of parallelized
point and kernel transformations.

Arguments:
  * `<specs>(...)`: The page specification selection followed immediately by
                     parenthesized pipeline instructions separated by semicolons.
                     Example: `1-5(contrast=1.3; sharpen=true)`
  * `threads=<n>`: Explicit parallel execution worker thread limit count.
  * `quality=<q>`: Output JPEG stream lossy compression value (1-100). Default: 75

Output Control (not pixel modifiers - control output encoding only):

  * `format=<codec>`: Forces the re-embedded image to be saved with a specific
                       codec, overriding automatic format selection. One of:

    - `png`  : lossless, raw pixel data (any color count)
    - `png8` : lossy, palette-quantized to <=256 colors (often
        much smaller than `png` for high-detail images)
    - `jpeg` : lossy DCT compression (ignored for 1-bit images)
        Example: `1-5(posterize=2; format=png8)`

Available Image Modifiers:"""

        image_modifiers_map = getattr(registry, "image_modifiers", {})
        if not image_modifiers_map:
            return f"{header}\n  (No image modifiers discovered.)"

        detail_lines = []
        table_lines = []
        for name in sorted(image_modifiers_map.keys()):
            plugin = image_modifiers_map[name]
            short_desc = plugin.desc.split("\n", 1)[0]
            syntax = f"`{name}=<{plugin.arg_type}>`"
            table_lines.append(f"| {plugin.title} | {syntax} | {short_desc} |")
            detail_lines.append(f"\n#### `{name}`: {plugin.title}\n")
            detail_lines.append(f"- {syntax}\n")
            detail_lines.append(f"- {plugin.desc}\n")

        return (
            f"{header}\n\n"
            + "| Image modifier | Syntax | Short description |\n"
            + "| --- | --- | --- |\n"
            + "\n".join(table_lines)
            + "\n### Image modifier details\n"
            + "\n".join(detail_lines)
        )


@register_operation(
    name="modify_images",
    tags=["in_place", "images", "modification"],
    type="single input operation",
    desc="Apply in-place image pixel modifications and effects",
    long_desc=LazyImageModifierHelpProxy(),
    usage="<input> modify_images <specs>(pipeline) output <file> [threads=<n>] [quality=<q>]",
    examples=[
        HelpExample(
            desc="Boost contrast and sharpen page image assets for pages 1 through 5.",
            cmd="in.pdf modify_images '1-5(contrast=1.3; sharpen=true)' output out.pdf",
        )
    ],
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def modify_images_operation(pdf: Any, args: list[str]) -> OpResult:
    """Applies parallelized bitmap image pipeline transformations to target pages.

    Raises InvalidArgumentError on unknown image modifiers.
    """

    ensure_dependencies("modify_images", ["PIL", "numpy"], "modify-images")

    if not args:
        raise InvalidArgumentError(
            "Missing execution payload statement. Expected format: `(image_modifier=value)`"
        )

    # 1. Parse operational arguments
    threads_val, quality, clean_args = _parse_operational_args(args)

    # 2. Parse out execution pipelines
    commands = parse_modify_images_args(clean_args)
    if not commands:
        raise InvalidArgumentError(
            "Missing execution payload statement. Expected format: `(image_modifier=value)`"
        )

    for cmd in commands:
        page_spec_str = cmd.page_spec

        # 3. Perform string-to-primitive casting natively on the main thread
        steps, have_image_modifiers, forced_codec = _compile_pipeline_steps(cmd)

        if not have_image_modifiers:
            logger.info("No image modifications requested; nothing to do.")
            continue

        total_pages = len(pdf.pages)
        target_pages = page_numbers_matching_page_spec(page_spec_str, total_pages)
        if not target_pages:
            continue

        # 4. Discover target image objects
        images_to_process = _discover_target_images(pdf, target_pages, total_pages)

        if not images_to_process:
            logger.info("No embedded image assets discovered on selected target pages.")
            continue

        # 5. Build local task callbacks leveraging the shared boilerplate helper
        prepare_cb, worker_cb, commit_cb = _build_callbacks(
            have_image_modifiers, steps, quality, forced_codec
        )

        logger.info("Initializing multi-threaded image pipeline engine processing job...")

        # 6. Dispatch tasks smoothly down to your untouched orchestrator
        run_parallel_image_job(
            images=images_to_process,
            threads=threads_val,
            prepare_func=prepare_cb,
            worker_func=worker_cb,
            commit_func=commit_cb,
        )

    return OpResult(success=True, pdf=pdf)


# --- PRIVATE HELPER FUNCTIONS ---


def _parse_operational_args(args: list[str]) -> tuple[int | None, int, list[str]]:
    """Extracts threading and quality limits, returning remaining clean arguments."""
    threads_val: int | None = None
    quality = 75
    clean_args = []

    for arg in args:
        if arg.startswith("threads="):
            try:
                threads_val = int(arg.split("=", 1)[1])
            except ValueError:
                pass  # ignore malformed threads= arg, fall through to default
        elif arg.startswith("quality="):
            try:
                quality = int(arg.split("=", 1)[1])
            except ValueError:
                pass  # ignore malformed quality= arg, fall through to default
        else:
            clean_args.append(arg)

    return threads_val, quality, clean_args


def _to_format(val: str) -> str:
    """Validates and normalizes an explicit output codec override."""
    v = val.strip().lower()
    if v not in ("jpeg", "jpg", "png", "png8"):
        raise InvalidArgumentError(f"Format '{val}' must be one of: png, png8, jpeg")
    return "jpeg" if v == "jpg" else v


def _compile_pipeline_steps(cmd: Any) -> tuple[list[tuple[str, Any]], bool, str | None]:
    """Validates commands, extracts an explicit format override if present, and
    identifies if any mutations are actively requested."""
    steps = []
    forced_codec: str | None = None
    for op in cmd.operations:
        if op.name == "format":
            forced_codec = _parse_format_op(op)
            continue

        steps.append(_compile_one_step(op))

    have_image_modifiers = forced_codec is not None
    if not have_image_modifiers:
        for name, val in steps:
            if isinstance(val, bool) and not val:
                continue
            have_image_modifiers = True
            break

    return steps, have_image_modifiers, forced_codec


def _op_value(op: Any, default: Any = None) -> Any:
    """An op's `value` param, or `default` if absent/blank."""
    raw = op.params.get("value")
    return default if raw is None or str(raw).strip() == "" else raw


def _parse_format_op(op: Any) -> str:
    """Validates and normalizes a `format=` directive's value."""
    raw_val = _op_value(op)
    if raw_val is None:
        raise InvalidArgumentError("Image modifier 'format': missing value")
    try:
        return _to_format(raw_val)
    except InvalidArgumentError as exc:
        raise InvalidArgumentError(f"Image modifier 'format': {exc}") from exc


def _compile_one_step(op: Any) -> tuple[str, Any]:
    """Validates a single pixel-modifier op against the registry and coerces
    its value via the plugin's validator."""
    if op.name not in registry.image_modifiers:
        raise InvalidArgumentError(f"Unknown image modifier plugin context: '{op.name}'")

    plugin = registry.image_modifiers[op.name]

    # Handle standalone flags! If no value is provided, treat it as "true"
    raw_val = _op_value(op, "true")

    try:
        coerced_val = plugin.validator(raw_val)
    except InvalidArgumentError as exc:
        raise InvalidArgumentError(f"Image modifier '{op.name}': {exc}") from exc

    return (op.name, coerced_val)


def _discover_target_images(pdf: Any, target_pages: list[int], total_pages: int) -> list[dict]:
    """Locates all image XObjects drawn on the target pages, including those
    reached through nested Form XObjects. Inline images are skipped (no
    stream object to rewrite)."""
    valid_pages = [p for p in target_pages if 1 <= p <= total_pages]
    images_to_process = []
    for meta in extract_pdf_images(pdf, valid_pages):
        xobj = meta.get("xobj")
        if xobj is None:  # inline image
            logger.debug(
                "Page %s: skipping inline image (no stream object to rewrite)", meta["page"]
            )
            continue
        images_to_process.append(
            {**meta, "xobj": xobj, "name": meta["name"], "page_num": meta["page"]}
        )
    return images_to_process


def _build_callbacks(
    have_image_modifiers: bool,
    steps: list[tuple[str, Any]],
    quality: int,
    forced_codec: str | None = None,
) -> tuple[Callable, Callable, Callable]:
    """Generates the three required orchestrator callbacks bound with local execution state."""
    import pikepdf
    from pikepdf import PdfImage

    def prepare_callback(img_item: dict, seen: set[str]) -> tuple[dict, ImageContext] | None:
        if not have_image_modifiers:
            logger.debug("Short circuit, nothing to do")
            return None

        xobj = img_item["xobj"]
        objgen = f"{xobj.objgen[0]}_{xobj.objgen[1]}"
        if objgen in seen:
            return None
        seen.add(objgen)

        try:
            pdf_img = PdfImage(xobj)
            pil_img = as_pil_image_compat(pdf_img)
            ensure_thread_safe(pil_img)
        except (pikepdf.PdfError, ValueError, *image_extraction_errors()) as err:
            logger.warning(
                "Page %s: Skipped unextractable asset '%s': %s",
                img_item["page_num"],
                img_item["name"],
                err,
            )
            return None

        ctx = ImageContext(
            xobj=xobj,
            smask_xobj=xobj.get("/SMask"),
            orig_size=get_orig_stream_size(xobj),
            img_dict=img_item,
            page_num=img_item["page_num"],
        )

        # ENHANCEMENT: Pass metadata forward to the worker payload for rich error contexts
        payload = {
            "pil_image": pil_img,
            "steps": steps,
            "page_num": img_item["page_num"],
            "name": img_item["name"],
        }
        return payload, ctx

    def worker_callback(payload: dict) -> Any:
        pil_img = payload["pil_image"]

        for name, clean_val in payload["steps"]:
            plugin = registry.image_modifiers[name]
            try:
                pil_img = plugin.function(pil_img, clean_val)
            except ValueError as err:
                # Catch the low-level PIL/plugin error and wrap it structurally
                raise OperationError(
                    f"Modifier '{name}' failed processing asset '{payload['name']}' "
                    f"on page {payload['page_num']}: {err}"
                ) from err

        return pil_img

    def commit_callback(ctx: ImageContext, result_pil_img: Any, payload: dict) -> bool:
        encode_and_update_pdf_image(ctx, result_pil_img, quality, forced_codec)
        return True

    return prepare_callback, worker_callback, commit_callback
