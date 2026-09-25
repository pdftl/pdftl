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

# Processing passes, in the order they run, then save-time passes.
STEP_PASSES = (
    "resample_images",
    "optimize_images",
    "simplify_vectors",
    "subset_fonts",
    "deduplicate_fonts",
    "deduplicate_images",
    "deduplicate_icc_profiles",
)
SAVE_PASSES = (
    "prune_resources",
    "recompress",
    "drop_xmp_streams",
    "drop_meta",
    "drop_vendor_extensions",
)
PASSES = STEP_PASSES + SAVE_PASSES

_LOSSLESS = {
    "subset_fonts",
    "deduplicate_fonts",
    "deduplicate_images",
    "deduplicate_icc_profiles",
    "prune_resources",
    "recompress",
}
LEVEL_PASSES = {
    "lossless": _LOSSLESS,
    "balanced": _LOSSLESS | {"resample_images", "optimize_images", "drop_xmp_streams"},
    "max": _LOSSLESS
    | {
        "resample_images",
        "optimize_images",
        "simplify_vectors",
        "drop_meta",
        "drop_vendor_extensions",
    },
}

# (dpi, JPEG quality, optimize_images strength); other levels use balanced's.
_IMAGE_DEFAULTS = {"max": (150, 75, "medium")}
_BALANCED_IMAGE_DEFAULTS = (200, 85, "low")
# Bitonal text loses legibility below ~300 dpi (OCR word agreement fell 3-9% at 200).
_DEFAULT_MONO_DPI = 300
# Below ~1.5x the target, lowering JPEG quality beats downsampling at equal size.
_DEFAULT_THRESHOLD = 1.5

# Parameter -> passes, any one of which must run for it to have an effect.
_PARAM_PASSES = {
    "dpi": ("resample_images",),
    "mono_dpi": ("resample_images",),
    "threshold": ("resample_images",),
    "quality": ("resample_images", "optimize_images"),
    "tolerance": ("simplify_vectors",),
    "max_stream_size": ("simplify_vectors",),
}

_SHRINK_LONG_DESC = """
The `shrink` operation makes a PDF smaller by running pdftl's size-reduction
passes in one go, in an order chosen by benchmarking against other tools.
Each pass keeps its own changes only where they help (fonts are replaced only
by smaller subsets, images only by smaller encodings), so the result is never
worse than running the passes by hand.

### Levels

* `lossless` (default) -- nothing visible changes. Subsets and deduplicates
  fonts, deduplicates images and ICC profiles, removes unused resources, and
  saves with maximum Flate compression.
* `balanced` -- also downsamples images to 200 dpi (JPEG quality 85;
  bitonal images to 300 dpi), re-encodes bitonal images losslessly
  (CCITT/JBIG2 generic, needs the `optimize-images` extra), and drops XMP
  metadata streams.
* `max` -- downsamples to 150 dpi (JPEG quality 75; bitonal images to
  300 dpi), allows lossy image optimisation, simplifies vector paths
  (handwriting and plots shrink most; needs the `simplify-vectors` extra),
  and drops all metadata and private vendor data from the catalog.

Images are downsampled only when drawn at more than 1.5 times the target,
as a slight downsample blurs more than it saves.

Tagged structure, forms, annotations, links and bookmarks are never removed.
Signatures are invalidated by any rewrite; pdftl warns when saving.

### Passes

Each level is a set of named passes, which `skip=` and `include=` adjust:

| pass | lossless | balanced | max |
|---|---|---|---|
| `resample_images` | | yes | yes |
| `optimize_images` | | yes (lossless) | yes (lossy) |
| `simplify_vectors` | | | yes |
| `subset_fonts`, `deduplicate_fonts` | yes | yes | yes |
| `deduplicate_images`, `deduplicate_icc_profiles` | yes | yes | yes |
| `prune_resources`, `recompress` | yes | yes | yes |
| `drop_xmp_streams` | | yes | |
| `drop_meta`, `drop_vendor_extensions` | | | yes |

The last five act when saving, as the output options of the same names do.

### Parameters

* `skip=<pass>[,<pass>...]` -- leave out passes the level runs, for example
  `max skip=simplify_vectors` or `max skip=drop_meta,drop_vendor_extensions`.
* `include=<pass>[,<pass>...]` -- add passes the level does not run, for
  example `balanced include=simplify_vectors`. Image passes added to
  `lossless` use `balanced`'s settings.
* `dpi=<n>`, `quality=<n>` -- the image target: downsample images drawn at
  more than 1.5 times `dpi` to `dpi`, and re-encode JPEGs at `quality`.
* `mono_dpi=<n>` (default 300) -- the target for bitonal (1-bit) images,
  whose thin strokes break up at lower resolutions.
* `threshold=<x>` (default 1.5) -- downsample only images drawn at more than
  `x` times the target; `1` downsamples any excess.
* `tolerance=<pt>`, `max_stream_size=<size>` -- passed to `simplify_vectors`:
  its maximum path deviation (default 0.15) and the content stream size above
  which it skips a stream (default 16MB, as its memory use grows with size).

A parameter is an error unless a pass that uses it runs, and so is naming a
pass the level already runs (`include=`) or does not run (`skip=`).

Output options given with `output` still apply, and `uncompress` or `fast`
override `recompress`.
"""

_SHRINK_EXAMPLES = [
    HelpExample(desc="Shrink losslessly.", cmd="in.pdf shrink output out.pdf"),
    HelpExample(desc="Shrink for on-screen reading.", cmd="in.pdf shrink balanced output out.pdf"),
    HelpExample(
        desc="Shrink as far as possible, targeting 120 dpi images.",
        cmd="in.pdf shrink max dpi=120 output out.pdf",
    ),
    HelpExample(
        desc="Shrink as far as possible but keep the document's metadata.",
        cmd="in.pdf shrink max skip=drop_meta,drop_vendor_extensions output out.pdf",
    ),
    HelpExample(
        desc="Shrink handwritten notes without touching their images.",
        cmd="in.pdf shrink include=simplify_vectors tolerance=0.3 output out.pdf",
    ),
]


@dataclass
class ShrinkPlan:
    level: str = "lossless"
    passes: set[str] = field(default_factory=set)
    dpi: int | None = None
    mono_dpi: int | None = None
    threshold: float | None = None
    quality: int | None = None
    optimize_strength: str | None = None
    tolerance: str | None = None  # passed to simplify_vectors as given
    max_stream_size: str | None = None  # passed to simplify_vectors as given
    ran: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def _parse_int(raw: str, key: str, lo: int, hi: int) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise InvalidArgumentError(f"shrink: '{key}' must be an integer, got '{raw}'") from None
    if not lo <= value <= hi:
        raise InvalidArgumentError(f"shrink: '{key}' must be between {lo} and {hi}")
    return value


def _parse_float(raw: str, key: str, lo: float, *, strict: bool = False) -> float:
    try:
        value = float(raw)
    except ValueError:
        value = float("nan")
    if not (value > lo if strict else value >= lo):
        bound = "above" if strict else "of at least"
        raise InvalidArgumentError(f"shrink: '{key}' must be a number {bound} {lo:g}")
    return value


def _pass_list(raw: str | None, key: str) -> set[str]:
    if raw is None:
        return set()
    names = {n.strip() for n in raw.split(",") if n.strip()}
    unknown = sorted(names - set(PASSES))
    if unknown or not names:
        raise InvalidArgumentError(
            f"shrink: {key}= takes pass names out of {', '.join(PASSES)}"
            + (f"; unknown: {', '.join(unknown)}" if unknown else "")
        )
    return names


def _resolve_passes(level: str, kv: dict) -> set[str]:
    base = LEVEL_PASSES[level]
    skip, include = _pass_list(kv.get("skip"), "skip"), _pass_list(kv.get("include"), "include")
    for name in sorted(skip & include):
        raise InvalidArgumentError(f"shrink: '{name}' is in both skip= and include=")
    for name in sorted(skip - base):
        raise InvalidArgumentError(f"shrink: skip={name} has no effect: '{level}' does not run it")
    for name in sorted(include & base):
        raise InvalidArgumentError(f"shrink: include={name} has no effect: '{level}' runs it")
    return (base - skip) | include


def _reject_unused_params(passes: set[str], kv: dict) -> None:
    for key, users in _PARAM_PASSES.items():
        if key in kv and not passes.intersection(users):
            raise InvalidArgumentError(
                f"shrink: '{key}' has no effect without {' or '.join(users)}"
            )


def _parse_args(args: list[str]) -> ShrinkPlan:
    bare: list[str] = []
    kv = parse_keyval_list(
        args,
        bare_tokens=bare,
        allowed_keys=["skip", "include", *_PARAM_PASSES],
        context="shrink",
    )
    if len(bare) > 1 or (bare and bare[0] not in LEVELS):
        raise InvalidArgumentError(f"shrink: expected one level out of {', '.join(LEVELS)}")
    level = bare[0] if bare else "lossless"
    plan = ShrinkPlan(level=level, passes=_resolve_passes(level, kv))
    _reject_unused_params(plan.passes, kv)
    _parse_image_settings(plan, kv)
    if "tolerance" in kv:
        _parse_float(kv["tolerance"], "tolerance", 0.0, strict=True)
        plan.tolerance = kv["tolerance"]
    if "max_stream_size" in kv:
        parse_size_to_bytes(kv["max_stream_size"], context="max_stream_size")  # validate early
        plan.max_stream_size = kv["max_stream_size"]
    return plan


def _parse_image_settings(plan: ShrinkPlan, kv: dict) -> None:
    if not plan.passes & {"resample_images", "optimize_images"}:
        return
    dpi, quality, strength = _IMAGE_DEFAULTS.get(plan.level, _BALANCED_IMAGE_DEFAULTS)
    plan.dpi = _parse_int(kv["dpi"], "dpi", 1, 10000) if "dpi" in kv else dpi
    plan.mono_dpi = (
        _parse_int(kv["mono_dpi"], "mono_dpi", 1, 10000) if "mono_dpi" in kv else _DEFAULT_MONO_DPI
    )
    plan.threshold = (
        _parse_float(kv["threshold"], "threshold", 1.0)
        if "threshold" in kv
        else _DEFAULT_THRESHOLD
    )
    plan.quality = _parse_int(kv["quality"], "quality", 1, 100) if "quality" in kv else quality
    plan.optimize_strength = strength


def _step_functions(plan: ShrinkPlan, output_filename: str) -> dict[str, Callable[[Pdf], object]]:
    from pdftl.operations.deduplicate_fonts import deduplicate_fonts
    from pdftl.operations.deduplicate_icc_profiles import deduplicate_icc_profiles
    from pdftl.operations.deduplicate_images import deduplicate_images
    from pdftl.operations.subset_fonts import subset_fonts

    funcs: dict[str, Callable[[Pdf], object]] = {
        "subset_fonts": lambda pdf: subset_fonts(pdf, []),
        "deduplicate_fonts": lambda pdf: deduplicate_fonts(pdf, []),
        "deduplicate_images": lambda pdf: deduplicate_images(pdf, []),
        "deduplicate_icc_profiles": lambda pdf: deduplicate_icc_profiles(pdf, []),
    }
    if "resample_images" in plan.passes:
        from pdftl.operations.resample_images import resample_images

        image_args = [
            f"dpi={plan.dpi}",
            f"mono_dpi={plan.mono_dpi}",
            f"threshold={plan.threshold}",
            f"quality={plan.quality}",
        ]
        funcs["resample_images"] = lambda pdf: resample_images(pdf, image_args)
    if "optimize_images" in plan.passes:
        from pdftl.operations.optimize_images import optimize_images_pdf

        opt_args = [plan.optimize_strength, f"jpeg_quality={plan.quality}"]
        funcs["optimize_images"] = lambda pdf: optimize_images_pdf(pdf, opt_args, output_filename)
    if "simplify_vectors" in plan.passes:
        from pdftl.operations.simplify_vectors import simplify_vectors_in_content_streams

        settings = [
            f"{key}={value}"
            for key, value in (
                ("tolerance", plan.tolerance),
                ("max_stream_size", plan.max_stream_size),
            )
            if value
        ]
        # simplify_vectors bounds its own memory and never grows a stream.
        vector_args = [f"({','.join(settings)})"] if settings else []
        funcs["simplify_vectors"] = lambda pdf: simplify_vectors_in_content_streams(
            pdf, vector_args
        )
    return funcs


def _steps(plan: ShrinkPlan, output_filename: str) -> list[tuple[str, Callable[[Pdf], object]]]:
    funcs = _step_functions(plan, output_filename)
    return [(name, funcs[name]) for name in STEP_PASSES if name in plan.passes]


def _run_step(plan: ShrinkPlan, name: str, func: Callable, pdf: Pdf) -> None:
    try:
        func(pdf)
    except Exception as exc:  # noqa: BLE001 - one failing pass must not lose the others' savings
        first_line = next(iter(str(exc).splitlines()), "")
        logger.warning("shrink: %s failed: %s %s", name, type(exc).__name__, first_line)
        plan.failed.append(name)
    else:
        plan.ran.append(name)


@register_operation(
    "shrink",
    tags=["in_place", "optimize", "images", "fonts"],
    type="single input operation",
    desc="Make a PDF smaller, losslessly or at a chosen quality level",
    long_desc=_SHRINK_LONG_DESC,
    usage=(
        "<input> shrink [lossless|balanced|max] [skip=<passes>] [include=<passes>]"
        " [dpi=<n>] [mono_dpi=<n>] [threshold=<x>] [quality=<n>] [tolerance=<pt>]"
        " [max_stream_size=<size>] output <output>"
    ),
    examples=_SHRINK_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS, c.OUTPUT], {}),
)
def shrink(pdf: Pdf, args: list[str], output_filename: str | None = None) -> OpResult:
    """Run the plan's passes in order, then ask the save to apply its save-time passes."""
    plan = _parse_args(args or [])
    for name, func in _steps(plan, output_filename or ""):
        _run_step(plan, name, func, pdf)

    hints = dict(getattr(pdf, c.PDFTL_SAVE_HINTS_ATTR, None) or {})
    hints.update({name: True for name in SAVE_PASSES if name in plan.passes})
    setattr(pdf, c.PDFTL_SAVE_HINTS_ATTR, hints)

    summary = f"shrink {plan.level}: ran {', '.join(plan.ran) or 'nothing'}"
    if plan.failed:
        summary += f"; failed {', '.join(plan.failed)}"
    logger.info(summary)
    return OpResult(success=True, pdf=pdf, summary=summary)
