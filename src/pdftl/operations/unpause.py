# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/upause.py

"""Remove intermediate 'pause' frames from PDF slide decks"""

import logging

import pdftl.core.constants as c
from pdftl.core.core_types import HelpExample, OpResult
from pdftl.core.registry import register_operation
from pdftl.utils.dependencies import ensure_dependencies
from pdftl.utils.page_images import iter_pages_as_pil

logger = logging.getLogger(__name__)

_UNPAUSE_LONG_DESC = """
The `unpause` operation removes intermediate animation frames from PDF
slide decks, such as those produced by LaTeX Beamer with `\\pause` or
`\\uncover` directives.

The algorithm renders each page at low resolution and checks whether all
ink pixels from the previous page are still present on the current page.
If they are, the previous page is considered an intermediate animation
frame and is discarded. Only pages where ink disappears or moves are
kept, plus the final page.

The `dpi=<val>` argument controls render resolution for comparison
(default: 72). Higher values are slower but more accurate for fine detail.

The `ink=<val>` argument is the pixel darkness threshold (0-255) below
which a pixel is considered ink (default: auto). In auto mode, Otsu's
method is used per page.

The `survival=<val>` argument is the minimum fraction (0.0-1.0) of ink
pixels from the previous page that must survive on the current page for
it to be considered a continuation (default: 0.98). Genuine Beamer
transitions produce survival=1.00; new slides typically produce <0.20.
"""

_UNPAUSE_EXAMPLES = [
    HelpExample(
        desc="Remove animation frames from a Beamer PDF",
        cmd="slides.pdf unpause output stripped.pdf",
    ),
    HelpExample(
        desc="Use stricter survival threshold",
        cmd="slides.pdf unpause survival=0.99 output stripped.pdf",
    ),
    HelpExample(
        desc="Higher resolution comparison for fine detail",
        cmd="slides.pdf unpause dpi=150 output stripped.pdf",
    ),
]


def _parse_arg(arg, name, cast, valid):
    from pdftl.exceptions import InvalidArgumentError

    raw = arg.split("=", 1)[1]
    try:
        value = cast(raw)
        if not valid(value):
            raise ValueError
        return value
    except ValueError as exc:
        raise InvalidArgumentError(f"'unpause': invalid {name} '{raw}'.") from exc


def _parse_unpause_args(args):
    dpi = 72.0
    ink = "auto"
    survival_ratio = 0.98

    for arg in args:
        if arg.startswith("dpi="):
            dpi = _parse_arg(arg, "dpi", float, lambda v: v > 0)
        elif arg.startswith("ink="):
            raw = arg.split("=", 1)[1]
            ink = "auto" if raw == "auto" else _parse_arg(arg, "ink", int, lambda v: 0 < v <= 255)
        elif arg.startswith("survival="):
            survival_ratio = _parse_arg(arg, "survival", float, lambda v: 0.0 < v <= 1.0)

    return dpi, ink, survival_ratio


def _otsu_threshold(pixels):
    """Compute Otsu's binarisation threshold from a grayscale numpy array."""
    import numpy

    hist, _ = numpy.histogram(pixels.ravel(), bins=256, range=(0, 256))
    total = pixels.size
    sum_total = numpy.dot(numpy.arange(256), hist)
    sum_bg, weight_bg, max_var, threshold = 0.0, 0, 0.0, 128
    for t in range(256):
        weight_bg += hist[t]
        if weight_bg == 0:
            continue
        weight_fg = total - weight_bg
        if weight_fg == 0:
            break
        sum_bg += t * hist[t]
        mean_bg = sum_bg / weight_bg
        mean_fg = (sum_total - sum_bg) / weight_fg
        var = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if var > max_var:
            max_var, threshold = var, t
    return threshold


def _ink_threshold_for(pixels, ink):
    """Return the ink threshold to use for this page's pixel array."""
    return _otsu_threshold(pixels) if ink == "auto" else ink


def _is_consecutive(last_pixels, pixels, last_threshold, survival_ratio):
    """
    Return True if `pixels` looks like a reveal continuation of `last_pixels`.

    A page is a continuation if (nearly) all ink pixels on the previous page
    are still inked on the current page — i.e. no ink was lost.
    """

    ink_mask = last_pixels < last_threshold
    ink_count = ink_mask.sum()

    if ink_count == 0:
        return True  # blank previous page: treat as continuation, discard it

    survived = (pixels[ink_mask] < last_threshold).mean()
    logger.debug("survival=%.2f", survived)
    return survived >= survival_ratio


def _find_pages_to_keep(pdf, dpi, ink, survival_ratio):
    """
    Returns a list of 0-based page indices to keep.

    A page is discarded only if it is a pure visual continuation of the
    previous page — i.e. all its ink survived onto the next page.
    The final page is always kept.
    """
    import numpy

    n_pages = len(pdf.pages)
    if n_pages == 0:
        return []

    keep = []
    last_pixels = None
    last_threshold = None

    for page_idx, image in iter_pages_as_pil(pdf, dpi, page_indices=list(range(n_pages))):
        pixels = numpy.array(image.convert("L"))

        if last_pixels is not None:
            if not _is_consecutive(last_pixels, pixels, last_threshold, survival_ratio):
                logger.debug("Not keeping page %s", page_idx - 1)
                keep.append(page_idx - 1)

        last_pixels = pixels
        last_threshold = _ink_threshold_for(pixels, ink)

    keep.append(n_pages - 1)
    return keep


@register_operation(
    "unpause",
    tags=["pages", "images", "slides"],
    type="single input operation",
    desc="Remove 'pause' frames from a slide deck",
    long_desc=_UNPAUSE_LONG_DESC,
    examples=_UNPAUSE_EXAMPLES,
    usage="<input> unpause [dpi=<val>] [ink=<val>|auto] [survival=<val>] output <file>",
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def unpause_pdf(input_pdf, args) -> OpResult:
    ensure_dependencies("unpause", ["pypdfium2", "PIL", "numpy"], "render")

    dpi, ink, survival_ratio = _parse_unpause_args(args)

    pages_to_keep = _find_pages_to_keep(input_pdf, dpi, ink, survival_ratio)

    n_original = len(input_pdf.pages)
    n_removed = n_original - len(pages_to_keep)
    logger.info(
        "unpause: keeping %s of %s pages, removed %s animation frame(s)",
        len(pages_to_keep),
        n_original,
        n_removed,
    )

    pages_to_delete = sorted(
        set(range(n_original)) - set(pages_to_keep),
        reverse=True,
    )

    from pdftl.operations.delete import del_page

    for page_idx in pages_to_delete:
        del_page(input_pdf, page_idx + 1)  # del_page expects 1-based

    return OpResult(success=True, pdf=input_pdf)
