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

LEVELS = ("lossless", "balanced", "strong", "extreme")
# Levels whose image passes keep their fidelity guards (PSNR, faithful re-encodes).
# MRC keeps its edge-loss check at every level.
_GUARDED = {"lossless", "balanced", "strong"}

# Processing passes, in the order they run, then save-time passes.
STEP_PASSES = (
    "delete_tags",
    "mrc_compress",
    "resample_images",
    "photos_to_jpeg",
    "optimize_images",
    "simplify_vectors",
    "round_text_positions",
    "compact_content",
    "subset_fonts",
    "deduplicate_fonts",
    "merge_font_subsets",
    "deduplicate_images",
    "deduplicate_icc_profiles",
    "deduplicate_xobjects",
)
SAVE_PASSES = (
    "prune_resources",
    "recompress",
    "compress_xmp",
    "drop_xmp_streams",
    "drop_thumbnails",
    "drop_meta",
    "drop_vendor_extensions",
    "drop_xfa",
)
PASSES = STEP_PASSES + SAVE_PASSES
# Save hints other than True; an explicit output option still wins.
_HINT_VALUES = {"drop_xfa": "hybrid"}

_LOSSLESS = {
    "optimize_images",
    "compact_content",
    "subset_fonts",
    "deduplicate_fonts",
    "merge_font_subsets",
    "deduplicate_images",
    "deduplicate_icc_profiles",
    "deduplicate_xobjects",
    "prune_resources",
    "recompress",
    "compress_xmp",
}
LEVEL_PASSES = {
    "lossless": _LOSSLESS,
    "balanced": _LOSSLESS
    | {
        "mrc_compress",
        "resample_images",
        "photos_to_jpeg",
        "optimize_images",
        "round_text_positions",
        "drop_xmp_streams",
        "drop_thumbnails",
    },
    "strong": _LOSSLESS
    | {
        "mrc_compress",
        "resample_images",
        "photos_to_jpeg",
        "optimize_images",
        "simplify_vectors",
        "round_text_positions",
        "drop_meta",
        "drop_vendor_extensions",
        "drop_xfa",
    },
}
# extreme runs everything strong does, plus the save passes only reachable
# via include= at strong (redundant once drop_meta runs, but not skipped
# with it: skip=drop_meta then still drops the XMP streams and thumbnails),
# and delete_tags, the one pass that costs accessibility.
LEVEL_PASSES["extreme"] = LEVEL_PASSES["strong"] | {
    "drop_xmp_streams",
    "drop_thumbnails",
    "delete_tags",
}

# (dpi, JPEG quality, optimize_images strength); other levels use balanced's.
_IMAGE_DEFAULTS = {"strong": (150, 75, "medium"), "extreme": (150, 60, "high")}
_BALANCED_IMAGE_DEFAULTS = (200, 85, "low")
# Bitonal text loses legibility below ~300 dpi (OCR word agreement fell 3-9% at 200).
_DEFAULT_MONO_DPI = 300
# Below ~1.5x the target, lowering JPEG quality beats downsampling at equal size.
_DEFAULT_THRESHOLD = 1.5
# mrc_compress arguments; balanced's (its defaults) apply to other levels.
_MRC_ARGS = {
    "strong": ["bg_div=4", "bg_quality=60", "fg_quality=40"],
    "extreme": ["bg_div=4", "bg_quality=50", "fg_quality=30"],
}
# Converting Type 1 to CFF changes Poppler's glyph edges.
_SUBSET_FONTS_ARGS = {"lossless": ["keep_type1"]}
# The most a glyph may move, in points; balanced's applies to other levels.
# Past 0.02 pt, pdftotext starts reordering dense math (sub- and superscripts).
_TEXT_TOLERANCE = {"strong": 0.02, "extreme": 0.02}
# Largest JPEG, as a share of the stored photo, worth the loss; balanced's is the default.
_PHOTO_RATIO = {"strong": 0.8, "extreme": 0.8}
_BALANCED_TEXT_TOLERANCE = 0.005

# Parameter -> passes, any one of which must run for it to have an effect.
_PARAM_PASSES = {
    "dpi": ("resample_images",),
    "mono_dpi": ("resample_images",),
    "threshold": ("resample_images",),
    "quality": ("resample_images", "photos_to_jpeg", "optimize_images"),
    "tolerance": ("simplify_vectors",),
    "max_stream_size": ("simplify_vectors",),
    "text_tolerance": ("round_text_positions",),
    "deflate": ("recompress",),
}

_SHRINK_LONG_DESC = """
The `shrink` operation makes a PDF smaller by running pdftl's size-reduction
passes in one go, in an order chosen by benchmarking against other tools.
Each pass keeps its own changes only where they help (fonts are replaced only
by smaller subsets, images only by smaller encodings), so the result is never
worse than running the passes by hand.

### Levels

* `lossless` (default) -- nothing visible changes. Subsets, deduplicates
  and merges fonts, deduplicates images, ICC profiles, drawn form XObjects and link appearances
  (never other annotation appearances), writes content streams in their shortest equivalent form,
  re-encodes images losslessly (bitonal images as JBIG2 when a
  `jbig2` encoder is installed; needs the `optimize-images` extra), removes
  unused resources, and saves with maximum Flate compression, the document's
  XMP metadata included (`compress_xmp`; PDF/A-1 files excepted, as that
  standard forbids it). Classic Type 1 fonts are left as they are.
* `balanced` -- also converts classic Type 1 fonts to CFF as it subsets them
  (Poppler draws their glyph edges slightly differently), separates plain
  scanned pages into MRC layers (a full-resolution 1-bit text stencil over
  small colour layers; needs the `mrc-compress` extra), downsamples images
  to 200 dpi (JPEG quality 85; bitonal images to 300 dpi), re-encodes
  losslessly stored photographs as JPEG, re-encodes bitonal images
  losslessly (CCITT/JBIG2 generic, needs the `optimize-images` extra), and
  drops XMP metadata streams and embedded page thumbnails.
* `strong` -- uses coarser MRC colour layers, downsamples to 150 dpi (JPEG
  quality 75; bitonal images to 300 dpi), allows lossy image optimisation
  (keeping an image's re-encoding only where it stays faithful),
  simplifies vector paths
  (handwriting and plots shrink most; needs the `simplify-vectors` extra),
  drops all metadata and private vendor data from the catalog, and drops
  the XFA form data of a hybrid form (one whose pages also carry ordinary
  form fields, which every viewer but Adobe's uses; `drop_xfa`).
* `extreme` -- everything maxed out, with the image fidelity guards off:
  MRC layers are coarser still (JPEG quality 50/30), images downsample to
  150 dpi at JPEG quality 60, `optimize_images` allows its most aggressive
  re-encodes, and a lossy image re-encode is kept once it is smaller, with
  no PSNR check and no quality retry search. A re-encode that comes out
  larger than the original is still
  discarded (`extreme` never grows a file), and MRC still leaves alone a
  page whose edges it would blur, or that it would corrupt or cannot decode.
  It also deletes the tagged structure (`delete_tags`): pages look the
  same, but screen readers and reflow lose it, as does any PDF/UA or
  PDF/A level A claim (`shrink` warns when the file makes one).

`balanced`, `strong` and `extreme` also write text positions with fewer
digits, moving no glyph more than 0.005 pt (`balanced`) or 0.02 pt
(`strong`, `extreme`); TeX output via dvips shrinks most.

Images are downsampled only when drawn at more than 1.5 times the target,
as a slight downsample blurs more than it saves.

Form fields, annotations, links and bookmarks are never removed, and
tagged structure only by `extreme`. A form that exists only as XFA keeps it.
Signatures are invalidated by any rewrite; pdftl warns when saving. A pass
whose extra is not installed is skipped and reported.

### Passes

Each level is a set of named passes, which `skip=` and `include=` adjust:

| pass | lossless | balanced | strong | extreme |
|---|---|---|---|---|
| `delete_tags` | | | | yes |
| `mrc_compress` | | yes | yes (coarser) | yes (coarser still) |
| `resample_images` | | yes | yes | yes (lower dpi/quality) |
| `photos_to_jpeg` | | yes | yes | yes (no fidelity check) |
| `optimize_images` | yes (lossless) | yes (lossless) | yes (lossy) | yes (lossiest, unchecked) |
| `simplify_vectors` | | | yes | yes |
| `round_text_positions` | | yes | yes | yes (looser tolerance) |
| `compact_content` | yes | yes | yes | yes |
| `subset_fonts` | yes (Type 1 kept) | yes | yes | yes |
| `deduplicate_fonts`, `merge_font_subsets` | yes | yes | yes | yes |
| `deduplicate_images`, `deduplicate_icc_profiles` | yes | yes | yes | yes |
| `deduplicate_xobjects` | yes | yes | yes | yes |
| `prune_resources`, `recompress`, `compress_xmp` | yes | yes | yes | yes |
| `drop_xmp_streams`, `drop_thumbnails` | | yes | | yes |
| `drop_meta`, `drop_vendor_extensions` | | | yes | yes |
| `drop_xfa` (hybrid forms only) | | | yes | yes |

The last eight act when the result is saved, as the output options of the
same names do: by this stage's `output`, or by a later stage's in a
pipeline such as `shrink --- rotate right output out.pdf`. `shrink --- usage`
reports the sizes they produce. The `drop_xfa` pass drops XFA only from
hybrid forms; the `drop_xfa` output option drops it from any form.

### Target size

`target=<size>` (a byte count or a size such as `10MB`), or `target=<n>%`
of the input file, asks for the least lossy output that fits. `shrink`
tries a ladder of settings, from `lossless` through `balanced` and `strong`
to `extreme` and beyond it (lower dpi and JPEG quality, coarser MRC layers,
looser vector and text tolerances), and keeps the first that fits. Each try
shrinks the document afresh and saves it in memory, so a target costs a few
runs of `shrink`; the size measured is that of the file `output` writes
with no other output options.

A level caps how lossy the output may be: `strong target=5MB` goes no
further than `strong`, and `extreme target=5MB` no further than `extreme`.
With no level, the ladder continues past `extreme`, and `shrink` warns
when it needs those settings. MRC keeps its edge-loss
check on every setting but the last. `dpi`, `mono_dpi`, `threshold`,
`quality`, `tolerance` and `text_tolerance` are chosen by the ladder and
cannot be given with `target=`; `skip=`, `include=`, `max_stream_size=` and
`deflate=` apply to every setting tried.

If no setting fits, `shrink` writes the smallest output it found and warns,
naming what takes most of the remaining space. Fonts and form data are
not made smaller by any setting (tagged structure goes only at `extreme`
and past it); when they dominate, the
warning suggests rendering the pages to images (`render dpi=<n> output
<file>`, then shrinking that file), which loses selectable text, form
fields, links and tags.

### Parameters

* `skip=<pass>[,<pass>...]` -- leave out passes the level runs, for example
  `strong skip=simplify_vectors` or `extreme skip=drop_meta,drop_vendor_extensions`.
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
* `text_tolerance=<pt>` -- the most `round_text_positions` may move a glyph.
* `deflate=zopfli` -- `recompress` also tries zopfli on each stream, keeping
  the smaller: typically 5-9% smaller streams (a few percent of the file),
  at several times the run time. Needs the `zopfli` extra; without it
  `shrink` fails. The default, `deflate=zlib`, uses zlib alone.

A parameter no running pass uses is ignored with a warning, and so is naming
a pass the level already runs (`include=`) or does not run (`skip=`).

Output options given with `output` still apply. `uncompress` or `fast`
override `recompress` and `compress_xmp`, and `linearize`, encryption or
signing override `compress_xmp`.
"""

_SHRINK_EXAMPLES = [
    HelpExample(desc="Shrink losslessly.", cmd="in.pdf shrink output out.pdf"),
    HelpExample(desc="Shrink for on-screen reading.", cmd="in.pdf shrink balanced output out.pdf"),
    HelpExample(
        desc="Shrink hard, with fidelity guards still on, targeting 120 dpi images.",
        cmd="in.pdf shrink strong dpi=120 output out.pdf",
    ),
    HelpExample(
        desc="Shrink as far as possible, with every fidelity guard off.",
        cmd="in.pdf shrink extreme output out.pdf",
    ),
    HelpExample(
        desc="Shrink to at most 10 MB, as faithfully as that allows.",
        cmd="in.pdf shrink target=10MB output out.pdf",
    ),
    HelpExample(
        desc="Shrink to a quarter of the input size, with fidelity guards still on.",
        cmd="in.pdf shrink strong target=25% output out.pdf",
    ),
    HelpExample(
        desc="Shrink as far as possible but keep the document's metadata.",
        cmd="in.pdf shrink extreme skip=drop_meta,drop_vendor_extensions output out.pdf",
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
    text_tolerance: float | None = None
    deflate: str | None = None
    guard_fidelity: bool = True
    mrc_args: list[str] | None = None  # overrides the level's
    mrc_guard: bool | None = None  # False turns off mrc_compress's edge-loss check
    ran: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    unavailable: list[str] = field(default_factory=list)  # optional extras not installed


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
        logger.warning("shrink: skip=%s has no effect: '%s' does not run it", name, level)
    for name in sorted(include & base):
        logger.warning("shrink: include=%s has no effect: '%s' runs it", name, level)
    return (base - skip) | include


def _warn_unused_params(passes: set[str], kv: dict) -> None:
    for key, users in _PARAM_PASSES.items():
        if key in kv and not passes.intersection(users):
            logger.warning("shrink: '%s' has no effect without %s", key, " or ".join(users))


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
    plan = ShrinkPlan(
        level=level, passes=_resolve_passes(level, kv), guard_fidelity=level in _GUARDED
    )
    _warn_unused_params(plan.passes, kv)
    _parse_image_settings(plan, kv)
    _parse_text_tolerance(plan, kv)
    if "tolerance" in kv:
        _parse_float(kv["tolerance"], "tolerance", 0.0, strict=True)
        plan.tolerance = kv["tolerance"]
    if "max_stream_size" in kv:
        parse_size_to_bytes(kv["max_stream_size"], context="max_stream_size")  # validate early
        plan.max_stream_size = kv["max_stream_size"]
    if "deflate" in kv:
        plan.deflate = _parse_deflate(kv["deflate"], "recompress" in plan.passes)
    return plan


def _parse_deflate(raw: str, used: bool) -> str | None:
    from pdftl.output.recompress import require_zopfli
    from pdftl.output.save import DEFLATE_ENCODERS

    if raw not in DEFLATE_ENCODERS:
        raise InvalidArgumentError(
            f"shrink: 'deflate' must be one of {', '.join(DEFLATE_ENCODERS)}"
        )
    if not used:
        return None
    if raw == "zopfli":
        require_zopfli()  # fail before the passes run, not at the save
    return raw


def _parse_text_tolerance(plan: ShrinkPlan, kv: dict) -> None:
    if "round_text_positions" not in plan.passes:
        return
    if "text_tolerance" in kv:
        plan.text_tolerance = _parse_float(
            kv["text_tolerance"], "text_tolerance", 0.0, strict=True
        )
    else:
        plan.text_tolerance = _TEXT_TOLERANCE.get(plan.level, _BALANCED_TEXT_TOLERANCE)


def _parse_resample_targets(plan: ShrinkPlan, kv: dict, dpi: int) -> None:
    plan.dpi = _parse_int(kv["dpi"], "dpi", 1, 10000) if "dpi" in kv else dpi
    plan.mono_dpi = (
        _parse_int(kv["mono_dpi"], "mono_dpi", 1, 10000) if "mono_dpi" in kv else _DEFAULT_MONO_DPI
    )
    plan.threshold = (
        _parse_float(kv["threshold"], "threshold", 1.0)
        if "threshold" in kv
        else _DEFAULT_THRESHOLD
    )


def _parse_image_settings(plan: ShrinkPlan, kv: dict) -> None:
    resample = "resample_images" in plan.passes
    optimize = "optimize_images" in plan.passes
    photos = "photos_to_jpeg" in plan.passes
    if not (resample or optimize or photos):
        return
    dpi, quality, strength = _IMAGE_DEFAULTS.get(plan.level, _BALANCED_IMAGE_DEFAULTS)
    if optimize:
        plan.optimize_strength = strength
    if resample:
        _parse_resample_targets(plan, kv, dpi)
    # optimize_images at "low" is lossless and never re-encodes JPEGs.
    if resample or photos or (optimize and strength != "low"):
        plan.quality = _parse_int(kv["quality"], "quality", 1, 100) if "quality" in kv else quality
    elif "quality" in kv:
        logger.warning(
            "shrink: 'quality' has no effect: '%s' re-encodes images losslessly", plan.level
        )


def _step_functions(plan: ShrinkPlan, output_filename: str) -> dict[str, Callable[[Pdf], object]]:
    from pdftl.operations.deduplicate_fonts import deduplicate_fonts
    from pdftl.operations.compact_content import compact_content
    from pdftl.operations.delete_tags import delete_tags
    from pdftl.operations.deduplicate_xobjects import deduplicate_xobjects
    from pdftl.operations.merge_font_subsets import merge_font_subsets_op
    from pdftl.operations.deduplicate_icc_profiles import deduplicate_icc_profiles
    from pdftl.operations.deduplicate_images import deduplicate_images
    from pdftl.operations.subset_fonts import subset_fonts

    from pdftl.operations.mrc_compress import mrc_compress

    mrc_args = plan.mrc_args if plan.mrc_args is not None else _MRC_ARGS.get(plan.level, [])
    funcs: dict[str, Callable[[Pdf], object]] = {
        "delete_tags": lambda pdf: delete_tags(pdf, []),
        "mrc_compress": lambda pdf: mrc_compress(pdf, mrc_args, guard_fidelity=_mrc_guard(plan)),
        "subset_fonts": lambda pdf: subset_fonts(pdf, _SUBSET_FONTS_ARGS.get(plan.level, [])),
        "deduplicate_fonts": lambda pdf: deduplicate_fonts(pdf, []),
        "deduplicate_images": lambda pdf: deduplicate_images(pdf, []),
        "deduplicate_icc_profiles": lambda pdf: deduplicate_icc_profiles(pdf, []),
        "deduplicate_xobjects": lambda pdf: deduplicate_xobjects(pdf, []),
        "compact_content": lambda pdf: compact_content(pdf, []),
        "merge_font_subsets": lambda pdf: merge_font_subsets_op(pdf, []),
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
    if "photos_to_jpeg" in plan.passes:
        from pdftl.operations.photos_to_jpeg import photos_to_jpeg

        jpeg_args = [f"quality={plan.quality}"]
        if plan.level in _PHOTO_RATIO:
            jpeg_args.append(f"max_ratio={_PHOTO_RATIO[plan.level]}")
        funcs["photos_to_jpeg"] = lambda pdf: photos_to_jpeg(
            pdf, jpeg_args, guard_fidelity=plan.guard_fidelity
        )
    if "optimize_images" in plan.passes:
        from pdftl.operations.optimize_images import optimize_images_pdf

        opt_args = [plan.optimize_strength]
        if plan.quality is not None and plan.optimize_strength != "low":
            opt_args.append(f"jpeg_quality={plan.quality}")
        funcs["optimize_images"] = lambda pdf: optimize_images_pdf(
            pdf, opt_args, output_filename, keep_faithful=plan.guard_fidelity
        )
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
    if "round_text_positions" in plan.passes:
        from pdftl.operations.round_text_positions import round_text_positions_op

        text_args = [f"tolerance={plan.text_tolerance}"]
        funcs["round_text_positions"] = lambda pdf: round_text_positions_op(pdf, text_args)
    return funcs


def _mrc_guard(plan: ShrinkPlan) -> bool:
    return True if plan.mrc_guard is None else plan.mrc_guard


def _steps(plan: ShrinkPlan, output_filename: str) -> list[tuple[str, Callable[[Pdf], object]]]:
    funcs = _step_functions(plan, output_filename)
    return [(name, funcs[name]) for name in STEP_PASSES if name in plan.passes]


# Passes that need an optional extra; without it they are skipped, not failed.
_PASS_MODULES = {
    "optimize_images": ("ocrmypdf",),
    "mrc_compress": ("numpy", "numba"),
    "simplify_vectors": ("numpy", "numba"),
}


def _missing_modules(name: str) -> list[str]:
    import importlib.util

    return [m for m in _PASS_MODULES.get(name, ()) if importlib.util.find_spec(m) is None]


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
        "<input> shrink [lossless|balanced|strong|extreme] [skip=<passes>] [include=<passes>]"
        " [dpi=<n>] [mono_dpi=<n>] [threshold=<x>] [quality=<n>] [tolerance=<pt>]"
        " [max_stream_size=<size>] [text_tolerance=<pt>] [deflate=zlib|zopfli]"
        " [target=<size>|<n>%]"
        " output <output>"
    ),
    examples=_SHRINK_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS, c.OUTPUT], {}),
)
def shrink(pdf: Pdf, args: list[str], output_filename: str | None = None) -> OpResult:
    """Run the plan's passes in order, then ask the save to apply its save-time passes."""
    from pdftl.operations.helpers.shrink_target import parse_target, shrink_to_target

    target = parse_target(args or [])
    if target is not None:
        return shrink_to_target(pdf, target, output_filename or "")
    plan = _parse_args(args or [])
    apply_plan(pdf, plan, output_filename or "")
    summary = plan_summary(plan)
    logger.info(summary)
    return OpResult(success=True, pdf=pdf, summary=summary)


def apply_plan(pdf: Pdf, plan: ShrinkPlan, output_filename: str) -> None:
    """Run the plan's step passes on `pdf` and leave its save passes as save hints."""
    from pdftl.output.recompress import zopfli_deflate

    with zopfli_deflate(plan.deflate == "zopfli"):  # passes weigh sizes as the save will
        for name, func in _steps(plan, output_filename):
            missing = _missing_modules(name)
            if missing:
                logger.info("shrink: skipping %s: needs %s", name, " and ".join(missing))
                plan.unavailable.append(name)
                continue
            _run_step(plan, name, func, pdf)

    hints = dict(getattr(pdf, c.PDFTL_SAVE_HINTS_ATTR, None) or {})
    hints.update(
        {name: _HINT_VALUES.get(name, True) for name in SAVE_PASSES if name in plan.passes}
    )
    if plan.deflate:
        hints["deflate"] = plan.deflate
    setattr(pdf, c.PDFTL_SAVE_HINTS_ATTR, hints)


def plan_summary(plan: ShrinkPlan) -> str:
    summary = f"shrink {plan.level}: ran {', '.join(plan.ran) or 'nothing'}"
    if plan.unavailable:
        summary += f"; not installed {', '.join(plan.unavailable)}"
    if plan.failed:
        summary += f"; failed {', '.join(plan.failed)}"
    return summary
