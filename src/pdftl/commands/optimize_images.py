# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/commands/optimize_images.py

# Copyright (c) 2025 The pdftl developers

# Portions of this file are adapted from optimize.py in the ocrmypdf project,
# available at https://github.com/ocrmypdf/OCRmyPDF
# under the MPL-2.0 with SPDX-FileCopyrightText: 2022 James R. Barlow

"""Optimize images in a PDF using ocrmypdf"""

import logging

from pdftl.core.registry import register_operation
from pdftl.core.types import Compatibility, FeatureType, Status
from pdftl.exceptions import InvalidArgumentError, PackageError

logger = logging.getLogger(__name__)

# NOTE: Heavy imports (pikepdf, ocrmypdf) are moved inside the function
# to prevent startup performance regression.

# Static defaults for help text generation to avoid importing ocrmypdf
DEFAULT_JPEG_QUALITY_STR = "75"
DEFAULT_PNG_QUALITY_STR = "70"

_OPTIMIZE_IMAGES_LONG_DESC_MD = f"""

The operation **optimize_images** optimizes images in a PDF file.

> **Note:** This feature requires `ocrmypdf` to be installed.

### Valid Optimization Options

These options can be passed as arguments following `optimize_images`.

* **low** (aliases: `lossless`, `safe`):
    * Apply lossless optimizations only.

* **medium** (default; aliases: `lossy_medium`, `lossy`):
    * Also allow some lossy optimizations.

* **high** (aliases: `aggressive`, `high`, `lossy_high`):
    * Also allow more aggressive lossy optimizations.

* **jbig2_lossy**:
    * Enable JBIG2 lossy mode (see ocrmypdf documentation).
    * This is independent of the preceding options.

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

_OPTIMIZE_IMAGES_LONG_DESC = f"""

The operation 'optimize_images' optimizes images in a PDF file.

This features requires 'ocrmypdf' to be installed.

Valid optimize_options:

low (aliases: lossless, safe):
  apply lossless optimizations only

medium (aliases: lossy_medium, lossy):
  also allow some lossy optimizations

high (aliases: aggressive, high, lossy_high):
  also allow more aggressive lossy optimizations

jbig2_lossy:
  JBIG2 lossy mode (see ocrmypdf documentation)
  This is independent of the preceding options

all (aliases: full):
  use all of the above

jpeg_quality=<n> (default: {DEFAULT_JPEG_QUALITY_STR})
png_quality=<n>  (default: {DEFAULT_PNG_QUALITY_STR})
quality=<n>
  set JPEG and/or PNG quality to <n>.
  <n> must be an integer between 0 and 100.
  0 means use the default quality.
  1 is the lowest possible quality.
  100 is the highest possible quality.

jobs=<n> (default: 0)
  use parallel processing with <n> jobs.
  if <n> is 0, this is set automatically.
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

    def __init__(self, jobs, optimize, jpeg_quality, png_quality, jb2lossy):
        self.jobs = jobs
        self.optimize = optimize
        self.jpeg_quality = jpeg_quality
        self.png_quality = png_quality
        self.jbig2_page_group_size = 0
        self.jbig2_lossy = jb2lossy
        self.jbig2_threshold = 0.85
        self.quiet = True
        self.progress_bar = False
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
    args=(["input_pdf", "operation_args", "output"], {}),
    compatibility=_COMPATIBILITY_INFO,
)
def optimize_images_pdf(pdf, operation_args: list, output_filename: str):
    """
    Optimize images in the given PDF.
    """
    # pylint: disable=import-outside-toplevel

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
    optimize, jpeg_quality, png_quality, jbig2_lossy, jobs = options

    jpeg_quality = jpeg_quality or DEFAULT_JPEG_QUALITY
    png_quality = png_quality or DEFAULT_PNG_QUALITY

    logger.debug(
        "optimize, jpeg_quality, png_quality, jbig2_lossy, jobs = %s, %s, %s, %s, %s",
        optimize,
        jpeg_quality,
        png_quality,
        jbig2_lossy,
        jobs,
    )

    options = OptimizeOptions(
        jobs=jobs,
        optimize=optimize,
        jpeg_quality=jpeg_quality,
        png_quality=png_quality,
        jb2lossy=jbig2_lossy,
    )
    from pathlib import Path

    root = Path(output_filename).parent / "images"
    root.mkdir(exist_ok=True)
    executor = DEFAULT_EXECUTOR
    jpegs, pngs = extract_images_generic(pdf, root, options)
    transcode_jpegs(pdf, jpegs, root, options, executor)
    deflate_jpegs(pdf, root, options, executor)
    # if options.optimize >= 2:
    # # Try pngifying the jpegs
    #      transcode_pngs(pdf, jpegs, jpg_name, root, options, executor)
    transcode_pngs(pdf, pngs, png_name, root, options, executor)

    jbig2_groups = extract_images_jbig2(pdf, root, options)
    convert_to_jbig2(pdf, jbig2_groups, root, options, executor)

    return pdf


def _raise_for_invalid_keyword(arg):
    raise InvalidArgumentError(f"Unrecognized keyword given for 'optimize' operation: '{arg}'")


def _parse_args_to_options(operation_args):
    # defaults
    optimize = 2  # medium optimization by default
    jpeg_quality = 0
    png_quality = 0
    jbig2_lossy = False
    jobs = 0

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
            if var == "jpeg_quality":
                jpeg_quality = val
            elif var == "png_quality":
                png_quality = val
            elif var == "quality":
                jpeg_quality = val
                png_quality = val
            elif var == "jobs":
                jobs = val
        else:
            _raise_for_invalid_keyword(arg)

    return optimize, jpeg_quality, png_quality, jbig2_lossy, jobs


def _parse_keyval_option(clean_arg, original_arg):
    key, val = (x.strip() for x in clean_arg.split("=", 1))

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

    if key not in ("quality", "jpeg_quality", "jpg_quality", "png_quality"):
        _raise_for_invalid_keyword(key)

    # now we have a quality keyword
    quality = val_int
    if not 0 <= quality <= 100:
        raise InvalidArgumentError(
            f"Quality value '{quality}' must be an integer between 0 and 100."
        )
    return key, quality
