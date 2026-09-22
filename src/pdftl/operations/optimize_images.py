# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/optimize_images.py

# Copyright (c) 2025 The pdftl developers

# Portions of this file are adapted from optimize.py in the ocrmypdf project,
# available at https://github.com/ocrmypdf/OCRmyPDF
# under the MPL-2.0 with SPDX-FileCopyrightText: 2022 James R. Barlow

"""Optimize images in a PDF using ocrmypdf"""

import logging
import tempfile

import pdftl.core.constants as c
from pdftl.core.core_types import Compatibility, FeatureType, OpResult, Status
from pdftl.core.registry import register_operation
from pdftl.exceptions import OperationError, InvalidArgumentError, PackageError

logger = logging.getLogger(__name__)

# NOTE: Heavy imports (pikepdf, ocrmypdf) are moved inside the function
# to prevent startup performance regression.

# Static defaults for help text generation to avoid importing ocrmypdf
DEFAULT_JPEG_QUALITY_STR = "75"
DEFAULT_PNG_QUALITY_STR = "70"
DEFAULT_JBIG2_GROUP_SIZE_STR = "10"
_JBIG2_GROUP_SIZE_ALL = object()  # sentinel

_OPTIMIZE_IMAGES_LONG_DESC_MD = f"""

The operation **optimize_images** optimizes images in a PDF file.

> **Note:** This feature requires `ocrmypdf` to be installed.

### Valid Optimization Options

These options can be passed as arguments following `optimize_images`.

* **low** (aliases: `lossless`, `safe`):
    * Apply lossless optimizations only.

* **medium** (default; aliases: `lossy_medium`, `lossy`):
    * Also allow some lossy optimizations.

* **high** (aliases: `aggressive`, `lossy_high`):
    * Also allow more aggressive lossy optimizations.

* **jbig2_lossy**:
    * Enable JBIG2 lossy mode (see ocrmypdf documentation).
    * This is independent of the preceding options.

* **jbig2_group_size=**`<n>` (default: {DEFAULT_JBIG2_GROUP_SIZE_STR},
  only applies when `jbig2_lossy` is set)
    * Number of pages to batch into a single shared JBIG2 symbol dictionary.
    * Larger values allow more cross-page symbol sharing (smaller output) at
      the cost of higher peak memory/CPU during encoding.
    * `<n>` must be a positive integer, or **all** / **infinity** to force a
      single, document-wide shared dictionary regardless of page count.

> **Known limitation:** `jbig2_lossy` (and `jbig2_group_size`) only apply to
> images that are **not already** JBIG2-encoded. This comes from ocrmypdf's
> own image selection logic (`extract_image_jbig2`), which explicitly skips
> any image whose current filter is already `/JBIG2Decode` -- on the
> assumption that ocrmypdf itself produced it and it's already optimized.
> If your input PDF already has per-page JBIG2 images (e.g. from a scanner
> or a different tool), `optimize_images jbig2_lossy` will report success
> but leave those images completely untouched, even with `jbig2_group_size`
> set.
>
> **Workaround:** decode the images to a different format first, so they no
> longer look "already JBIG2" to ocrmypdf, then re-encode:
> ```
> in.pdf modify_images '(format=png)' --- \
>   optimize_images jbig2_lossy jbig2_group_size=all output out.pdf
> ```
> This costs an extra decode/re-encode pass (PNG round-trip) and is
> noticeably slower on large documents, but produces a genuine
> document-wide shared JBIG2 symbol dictionary. A lower-overhead fix
> (decoding straight to a raw bitmap and skipping the PNG stage) is
> possible but more invasive, and is not implemented yet.

* **all** (aliases: `full`):
    * Use all of the above.

* **jpeg_quality=**`<n>` (default: {DEFAULT_JPEG_QUALITY_STR})
* **png_quality=**`<n>` (default: {DEFAULT_PNG_QUALITY_STR})
* **quality=**`<n>`
    * Set JPEG and/or PNG quality to `<n>`.
    * `<n>` must be an integer between 0 and 100.
    * 0 means use the default quality.
    * 1 is the lowest possible quality.
    * 100 is the highest possible quality.

* **jobs=**`<n>` (default: 0)
    * Use parallel processing with `<n>` jobs.
    * If `<n>` is 0, this is set automatically.
"""

_OPTIMIZE_IMAGES_EXAMPLES = [
    {
        "cmd": "in.pdf optimize_images output out.pdf",
        "desc": "Optimize the images in the file in.pdf",
    }
]


# pylint: disable=too-many-arguments, too-many-positional-arguments
# pylint: disable=too-few-public-methods, too-many-instance-attributes
class OptimizeOptions:
    """Emulate ocrmypdf's options."""

    def __init__(self, jobs, optimize, jpeg_quality, png_quality, jb2lossy, jbig2_group_size=None):
        self.jobs = jobs
        self.optimize = optimize
        self.jpeg_quality = jpeg_quality
        self.png_quality = png_quality
        self.jbig2_lossy = jb2lossy
        self.jbig2_threshold = 0.85
        self.quiet = True
        self.progress_bar = False
        if jbig2_group_size is not None:
            self.jbig2_page_group_size = jbig2_group_size
        else:
            self.jbig2_page_group_size = 10 if jb2lossy else 1


_COMPATIBILITY_INFO = Compatibility(
    type=FeatureType.PDFTL_EXTENSION,
    status=Status.BETA,
    description="Optimize images",
    notes="Requires ocrmypdf to be installed.",
    enhancements=["Provides interface to ocrmypdf optimization features"],
)


@register_operation(
    "optimize_images",
    tags=["in_place"],
    type="single input operation",
    desc="Optimize images",
    long_desc=_OPTIMIZE_IMAGES_LONG_DESC_MD,
    usage="<input> optimize_images [<optimize_option>...] output <file> [<option...>]",
    examples=_OPTIMIZE_IMAGES_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS, c.OUTPUT], {}),
    compatibility=_COMPATIBILITY_INFO,
)
def optimize_images_pdf(pdf, operation_args: list, output_filename: str) -> OpResult:
    """
    Optimize images in the given PDF.
    """
    # pylint: disable=import-outside-toplevel

    if logger.getEffectiveLevel() == logging.DEBUG:
        logging.getLogger("ocrmypdf").setLevel(logging.DEBUG)
        logging.getLogger("ocrmypdf.optimize").setLevel(logging.DEBUG)

    try:
        from ocrmypdf.optimize import DEFAULT_EXECUTOR  # FLATE_JPEG_THRESHOLD,
        from ocrmypdf.optimize import (
            DEFAULT_JPEG_QUALITY,
            DEFAULT_PNG_QUALITY,
            convert_to_jbig2,
            deflate_jpegs,
            extract_images_generic,
            extract_images_jbig2,
            png_name,
            transcode_jpegs,
            transcode_pngs,
        )
        from ocrmypdf.exceptions import MissingDependencyError, SubprocessOutputError
    except ImportError as exc:
        raise PackageError(
            "Loading OCRmyPDF failed.\n pip install pdftl.[optimize-images] to fix this."
        ) from exc

    # Optimization options for ocrmypdf:
    #   Control how the PDF is optimized after OCR

    #   -O {0,1,2,3}, --optimize {0,1,2,3}
    #                             Control how PDF is optimized after processing:0 - do not
    #                             optimize; 1 - do safe, lossless optimizations (default); 2 - do
    #                             lossy JPEG and JPEG2000 optimizations; 3 - do more aggressive
    #                             lossy JPEG and JPEG2000 optimizations. To enable lossy JBIG2,
    #                             see --jbig2-lossy.
    #   --jpeg-quality Q          Adjust JPEG quality level for JPEG optimization. 100 is best
    #                             quality and largest output size; 1 is lowest quality and
    #                             smallest output; 0 uses the default.
    #   --png-quality Q           Adjust PNG quality level to use when quantizing PNGs. Values
    #                             have same meaning as with --jpeg-quality
    #   --jbig2-lossy             Enable JBIG2 lossy mode (better compression, not suitable for
    #                             some use cases - see documentation). Only takes effect if
    #                             --optimize 1 or higher is also enabled.
    #   --jbig2-threshold T       Adjust JBIG2 symbol code classification threshold (default
    #                             0.85), range 0.4 to 0.9.

    options = _parse_args_to_options(operation_args)
    optimize, jpeg_quality, png_quality, jbig2_lossy, jobs, jbig2_group_size = options

    jpeg_quality = jpeg_quality or DEFAULT_JPEG_QUALITY
    png_quality = png_quality or DEFAULT_PNG_QUALITY

    if jbig2_group_size is _JBIG2_GROUP_SIZE_ALL:
        jbig2_group_size = len(pdf.pages)

    logger.debug(
        "optimize, jpeg_quality, png_quality, jbig2_lossy, jobs, jbig2_group_size = "
        "%s, %s, %s, %s, %s, %s",
        optimize,
        jpeg_quality,
        png_quality,
        jbig2_lossy,
        jobs,
        jbig2_group_size,
    )

    options = OptimizeOptions(
        jobs=jobs,
        optimize=optimize,
        jpeg_quality=jpeg_quality,
        png_quality=png_quality,
        jb2lossy=jbig2_lossy,
        jbig2_group_size=jbig2_group_size,
    )
    from pathlib import Path

    with tempfile.TemporaryDirectory(prefix="pdftl_opt_img_") as tmp_dir:
        root = Path(tmp_dir)
        executor = DEFAULT_EXECUTOR
        try:
            jpegs, pngs = extract_images_generic(pdf, root, options)
            transcode_jpegs(pdf, jpegs, root, options, executor)
            deflate_jpegs(pdf, root, options, executor)
            transcode_pngs(pdf, pngs, png_name, root, options, executor)

            jbig2_groups = extract_images_jbig2(pdf, root, options)
            convert_to_jbig2(pdf, jbig2_groups, root, options, executor)
        except MissingDependencyError as exc:
            raise OperationError(
                f"An external dependency required by OCRmyPDF is missing: {exc}"
            ) from exc
        except SubprocessOutputError as exc:
            raise OperationError(f"An external tool executed by OCRmyPDF failed: {exc}") from exc
        except FileNotFoundError as exc:
            raise OperationError(f"Failed to execute an underlying system tool: {exc}") from exc
    return OpResult(success=True, pdf=pdf)


def _raise_for_invalid_keyword(arg):
    raise InvalidArgumentError(f"Unrecognized keyword given for 'optimize' operation: '{arg}'")


def _parse_args_to_options(operation_args):
    # defaults
    optimize = 2  # medium optimization by default
    jpeg_quality = 0
    png_quality = 0
    jbig2_lossy = False
    jobs = 0
    jbig2_group_size = None

    for arg in operation_args:
        clean_arg = arg.strip().lower()
        if clean_arg in ("low", "lossless", "safe"):
            optimize = 1
        elif clean_arg in ("medium", "lossy_medium"):
            optimize = 2
        elif clean_arg in ("high", "aggressive", "lossy_high"):
            optimize = 3
        elif clean_arg in ("jbig2_lossy", "jb2lossy", "jb2_lossy"):
            jbig2_lossy = True
        elif clean_arg in ("all", "full", "lossy_full"):
            jbig2_lossy = True
            optimize = 3
        elif "=" in clean_arg:
            # next method raises on invalid keyval arguments
            var, val = _parse_keyval_option(clean_arg, arg)
            if var in ("jpeg_quality", "jpg_quality"):
                jpeg_quality = val
            elif var == "png_quality":
                png_quality = val
            elif var == "quality":
                jpeg_quality = val
                png_quality = val
            elif var == "jobs":
                jobs = val
            else:  # jbig2_group_size (only other key _parse_keyval_option returns)
                jbig2_group_size = val
        else:
            _raise_for_invalid_keyword(arg)

    return optimize, jpeg_quality, png_quality, jbig2_lossy, jobs, jbig2_group_size


def _parse_keyval_option(clean_arg, original_arg):
    key, val = (x.strip() for x in clean_arg.split("=", 1))

    if key == "jbig2_group_size" and val in ("all", "infinity", "inf"):
        return key, _JBIG2_GROUP_SIZE_ALL

    try:
        val_int = int(val)
    except ValueError as exc:
        raise InvalidArgumentError(
            f"Could not convert '{val}' to an integer in 'optimize_images' option: "
            f"'{original_arg}'. Conversion error: {exc}"
        ) from exc

    if key == "jobs":
        njobs = val_int
        if njobs < 0:
            raise InvalidArgumentError(f"jobs value '{njobs}' cannot be negative.")
        return key, njobs

    if key == "jbig2_group_size":
        if val_int < 1:
            raise InvalidArgumentError(
                f"jbig2_group_size value '{val_int}' must be a positive integer."
            )
        return key, val_int

    if key not in ("quality", "jpeg_quality", "jpg_quality", "png_quality"):
        _raise_for_invalid_keyword(key)

    # now we have a quality keyword
    quality = val_int
    if not 0 <= quality <= 100:
        raise InvalidArgumentError(
            f"Quality value '{quality}' must be an integer between 0 and 100."
        )
    return key, quality
