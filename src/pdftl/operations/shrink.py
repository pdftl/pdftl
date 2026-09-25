# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/shrink.py

"""Shrink a PDF by running pdftl's size-reduction passes in a tested order."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.core_types import HelpExample, OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.arg_helpers import parse_size_to_bytes
from pdftl.utils.keyval_parser import parse_keyval_list

if TYPE_CHECKING:
    from pikepdf import Pdf

logger = logging.getLogger(__name__)

LEVELS = ("lossless", "balanced", "max")
_LEVEL_IMAGE_DEFAULTS = {"balanced": (200, 85), "max": (150, 75)}
_LEVEL_SAVE_HINTS = {
    "lossless": {"prune_resources": True, "recompress": True},
    "balanced": {"prune_resources": True, "recompress": True, "drop_xmp_streams": True},
    "max": {
        "prune_resources": True,
        "recompress": True,
        "drop_meta": True,
        "drop_vendor_extensions": True,
    },
}
# simplify_vectors' memory grows steeply with page content; an 82 MB page needed over 3 GB.
_DEFAULT_VECTOR_PAGE_LIMIT = 8 * 1024 * 1024

_SHRINK_LONG_DESC = """
The `shrink` operation makes a PDF smaller by running pdftl's size-reduction
passes in one go, in an order chosen by benchmarking against other tools.
Each pass keeps its own changes only where they help (fonts are replaced only
by smaller subsets, images only by smaller encodings), so the result is never
worse than running the passes by hand.

### Levels

* `lossless` (default) -- nothing visible changes. Subsets and deduplicates
  fonts, deduplicates images and ICC profiles, removes unused resources, and
  saves with maximum Flate compression (as the `recompress` and
  `prune_resources` output options would).
* `balanced` -- also downsamples images drawn above 200 dpi (JPEG quality
  85), re-encodes bitonal images losslessly (CCITT/JBIG2 generic, needs the
  `optimize-images` extra), and drops XMP metadata streams.
* `max` -- downsamples to 150 dpi (JPEG quality 75), allows lossy image
  optimisation, simplifies vector paths (handwriting and plots shrink most;
  needs the `simplify-vectors` extra), and drops all metadata and private
  vendor data from the catalog.

Tagged structure, forms, annotations, links and bookmarks are never removed.
Signatures are invalidated by any rewrite; pdftl warns when saving.

### Parameters

* `dpi=<n>`, `quality=<n>` -- override the level's image target.
* `vector_page_limit=<size>` (default 8MB) -- `max` skips vector
  simplification on pages whose content exceeds this, as it needs a lot of
  memory on very large pages.

Explicit output options win over the level's choices, for example
`output out.pdf uncompress`.
"""

_SHRINK_EXAMPLES = [
    HelpExample(desc="Shrink losslessly.", cmd="in.pdf shrink output out.pdf"),
    HelpExample(desc="Shrink for on-screen reading.", cmd="in.pdf shrink balanced output out.pdf"),
    HelpExample(
        desc="Shrink as far as possible, targeting 120 dpi images.",
        cmd="in.pdf shrink max dpi=120 output out.pdf",
    ),
]


@dataclass
class ShrinkPlan:
    level: str = "lossless"
    dpi: int | None = None
    quality: int | None = None
    vector_page_limit: int = _DEFAULT_VECTOR_PAGE_LIMIT
    ran: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _parse_int(raw: str, key: str, lo: int, hi: int) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise InvalidArgumentError(f"shrink: '{key}' must be an integer, got '{raw}'") from None
    if not lo <= value <= hi:
        raise InvalidArgumentError(f"shrink: '{key}' must be between {lo} and {hi}")
    return value


def _parse_args(args: list[str]) -> ShrinkPlan:
    bare: list[str] = []
    kv = parse_keyval_list(
        args,
        bare_tokens=bare,
        allowed_keys=["dpi", "quality", "vector_page_limit"],
        context="shrink",
    )
    if len(bare) > 1 or (bare and bare[0] not in LEVELS):
        raise InvalidArgumentError(f"shrink: expected one level out of {', '.join(LEVELS)}")
    plan = ShrinkPlan(level=bare[0] if bare else "lossless")
    default_dpi, default_quality = _LEVEL_IMAGE_DEFAULTS.get(plan.level, (None, None))
    plan.dpi = _parse_int(kv["dpi"], "dpi", 1, 10000) if "dpi" in kv else default_dpi
    plan.quality = (
        _parse_int(kv["quality"], "quality", 1, 100) if "quality" in kv else default_quality
    )
    if "vector_page_limit" in kv:
        plan.vector_page_limit = parse_size_to_bytes(
            kv["vector_page_limit"], context="vector_page_limit"
        )
    return plan


def _page_content_bytes(page) -> int:
    import pikepdf

    contents = page.obj.get("/Contents")
    if contents is None:
        streams = []
    elif isinstance(contents, pikepdf.Array):
        streams = list(contents)
    else:
        streams = [contents]
    total = sum(len(s.read_bytes()) for s in streams)
    for xobj in (page.Resources.get("/XObject") or {}).values():
        if xobj.get("/Subtype") == "/Form":
            total += len(xobj.read_bytes())
    return total


def _page_ranges(pages: list[int]) -> list[str]:
    ranges, start = [], None
    for prev, cur in zip([None, *pages], [*pages, None]):
        if start is None:
            start = cur
        elif cur is None or cur != prev + 1:
            ranges.append(str(start) if start == prev else f"{start}-{prev}")
            start = cur
    return ranges


def _simplifiable_page_specs(pdf: Pdf, limit: int) -> list[str]:
    small = [i for i, page in enumerate(pdf.pages, 1) if _page_content_bytes(page) <= limit]
    if len(small) < len(pdf.pages):
        logger.info(
            "shrink: skipping vector simplification on %d page(s) over %d bytes of content.",
            len(pdf.pages) - len(small),
            limit,
        )
    return _page_ranges(small)


def _content_streams(pdf: Pdf):
    import pikepdf

    for page in pdf.pages:
        contents = page.obj.get("/Contents")
        if isinstance(contents, pikepdf.Array):
            yield from contents
        elif contents is not None:
            yield contents
    for obj in pdf.objects:
        if isinstance(obj, pikepdf.Stream) and (
            obj.get("/Subtype") == "/Form" or obj.get("/PatternType") == 1
        ):
            yield obj


def _keeping_smaller_content(pdf: Pdf, transform: Callable[[Pdf], object]) -> None:
    """Run transform, then restore any content stream it did not make smaller."""
    import zlib

    before = {}
    for stream in _content_streams(pdf):
        if stream.objgen not in before:
            before[stream.objgen] = (stream, stream.read_bytes())
    transform(pdf)
    for stream, old in before.values():
        new = stream.read_bytes()
        if new != old and len(zlib.compress(new, 9)) >= len(zlib.compress(old, 9)):
            stream.write(old)


def _steps(plan: ShrinkPlan, output_filename: str) -> list[tuple[str, Callable[[Pdf], object]]]:
    from pdftl.operations.deduplicate_fonts import deduplicate_fonts
    from pdftl.operations.deduplicate_icc_profiles import deduplicate_icc_profiles
    from pdftl.operations.deduplicate_images import deduplicate_images
    from pdftl.operations.subset_fonts import subset_fonts

    steps: list[tuple[str, Callable[[Pdf], object]]] = []
    if plan.level != "lossless":
        from pdftl.operations.optimize_images import optimize_images_pdf
        from pdftl.operations.resample_images import resample_images

        image_args = [f"dpi={plan.dpi}", f"quality={plan.quality}"]
        opt_level = "low" if plan.level == "balanced" else "medium"
        steps += [
            ("resample_images", lambda pdf: resample_images(pdf, image_args)),
            (
                "optimize_images",
                lambda pdf: optimize_images_pdf(
                    pdf, [opt_level, f"jpeg_quality={plan.quality}"], output_filename
                ),
            ),
        ]
    if plan.level == "max":
        from pdftl.operations.simplify_vectors import simplify_vectors_in_content_streams

        def _simplify(pdf):
            specs = _simplifiable_page_specs(pdf, plan.vector_page_limit)
            if specs:
                _keeping_smaller_content(
                    pdf, lambda p: simplify_vectors_in_content_streams(p, specs)
                )

        steps.append(("simplify_vectors", _simplify))
    steps += [
        ("subset_fonts", lambda pdf: subset_fonts(pdf, [])),
        ("deduplicate_fonts", lambda pdf: deduplicate_fonts(pdf, [])),
        ("deduplicate_images", lambda pdf: deduplicate_images(pdf, [])),
        ("deduplicate_icc_profiles", lambda pdf: deduplicate_icc_profiles(pdf, [])),
    ]
    return steps


def _run_step(plan: ShrinkPlan, name: str, func: Callable, pdf: Pdf) -> None:
    try:
        func(pdf)
    except Exception as exc:  # noqa: BLE001 - one failing pass must not lose the others' savings
        first_line = next(iter(str(exc).splitlines()), "")
        logger.warning("shrink: skipped %s: %s %s", name, type(exc).__name__, first_line)
        plan.skipped.append(name)
    else:
        plan.ran.append(name)


@register_operation(
    "shrink",
    tags=["in_place", "optimize", "images", "fonts"],
    type="single input operation",
    desc="Make a PDF smaller, losslessly or at a chosen quality level",
    long_desc=_SHRINK_LONG_DESC,
    usage="<input> shrink [lossless|balanced|max] [dpi=<n>] [quality=<n>] output <output>",
    examples=_SHRINK_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS, c.OUTPUT], {}),
)
def shrink(pdf: Pdf, args: list[str], output_filename: str | None = None) -> OpResult:
    """Run the level's passes in order, then ask the save to prune and recompress."""
    plan = _parse_args(args or [])
    for name, func in _steps(plan, output_filename or ""):
        _run_step(plan, name, func, pdf)

    hints = dict(getattr(pdf, c.PDFTL_SAVE_HINTS_ATTR, None) or {})
    hints.update(_LEVEL_SAVE_HINTS[plan.level])
    setattr(pdf, c.PDFTL_SAVE_HINTS_ATTR, hints)

    summary = f"shrink {plan.level}: ran {', '.join(plan.ran) or 'nothing'}"
    if plan.skipped:
        summary += f"; skipped {', '.join(plan.skipped)}"
    logger.info(summary)
    return OpResult(success=True, pdf=pdf, summary=summary)
