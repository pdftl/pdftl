# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/simplify_vectors.py

"""Simplify and optimize heavy vector graphic paths in PDF content streams.

Pipeline:
    Stage 1 — parse:     content stream bytes → raw instruction list
    Stage 2 — segment:   instruction list → list[Path | passthrough]
    Stage 3 — simplify:  Path → SimplifiedPath  (pure, no I/O)
    Stage 4 — serialize: mixed list → bytes

See simplify_vectors_spec.md for the full design contract.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pikepdf as pikepdf_t

import pdftl.core.constants as c
from pdftl.exceptions import InvalidArgumentError
from pdftl.core.registry import register_operation
from pdftl.core.core_types import OpResult
from pdftl.utils.dependencies import ensure_dependencies
from pdftl.utils.page_specs import page_numbers_matching_page_spec
from pdftl.utils.keyval_parser import parse_keyval_list
from pdftl.utils.path_geometry import simplify_path
from pdftl.utils.path_types import Path, SimplifiedPath, SimplifyConfig
from pdftl.utils.pdf_resources import get_resources
from pdftl.utils.path_segmentation import segment, serialize

logger = logging.getLogger(__name__)


_ALLOWED_KEYS = {
    "tolerance",
    "curves",
    "lines",
    "clip_paths",
    "min_points",
    "max_error_scale",
    "pages",
    "coalesce_strokes",
}


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------


@dataclass
class SimplifyStats:
    """Metrics collected across a full document pass."""

    streams_processed: int = 0
    paths_total: int = 0
    paths_optimised: int = 0
    paths_fallback: int = 0
    paths_bypassed: int = 0
    paths_clipping: int = 0
    subpaths_curved: int = 0
    subpaths_linear: int = 0
    points_before: int = 0
    points_after: int = 0
    operators_before: int = 0
    operators_after: int = 0


# ---------------------------------------------------------------------------
# Operation registration
# ---------------------------------------------------------------------------


_EXAMPLES = [
    {
        "cmd": "in.pdf simplify_vectors output out.pdf",
        "desc": "Simplify all pages with default tolerance",
    },
    {
        "cmd": "in.pdf simplify_vectors 2-3 tolerance=0.5 curves=true lines=false output out.pdf",
        "desc": "Aggressive simplification of curved paths on pages 2-3",
    },
    {
        "cmd": "in.pdf simplify_vectors clip_paths=true tolerance=0.1 output out.pdf",
        "desc": "Simplify clipping paths (use with care)",
    },
]


_LONG_DESC = """
The `simplify_vectors` operation reduces the complexity of vector paths in PDF
content streams, lowering file size and rendering time while attempting to
preserve visual appearance.

## How It Works

The operation processes content streams in four stages:

1. Parse PDF content streams into drawing instructions.
2. Segment instructions into individual paths and subpaths.
3. Simplify geometry:
   - Curved paths are fitted with cubic Bézier curves using Schneider's
     Graphics Gems algorithm.
   - Linear paths are simplified using the
     Ramer-Douglas-Peucker (RDP) algorithm.
4. Serialize the simplified paths back into PDF content streams.

The current transformation matrix (CTM) is tracked throughout processing so
that tolerances are applied consistently in device space, even under nested
graphics-state transforms.

Form XObjects and Tiling Patterns are processed recursively.

If simplification would increase operator count, exceed error limits, or
cannot be performed safely, the original path is preserved unchanged.
                                                |
## Configuration Parameters

**`tolerance`** (float, default: `0.15`)

* Maximum permitted geometric deviation in device-space points.
* Larger values produce more aggressive simplification.
* Must be greater than zero.

**`curves`** (bool, default: `true`)

* Enable Schneider cubic Bézier fitting for curved paths.

**`lines`** (bool, default: `true`)

* Enable Ramer-Douglas-Peucker simplification for linear paths.

**`coalesce_strokes`** (bool, default: `true`)

* Automatically merge shattered/fragmented continuous strokes into single paths
  before simplification, smoothing over minor line width (`w`) fluctuations.

**`clip_paths`** (bool, default: `false`)

* Allow simplification of clipping paths (`W` / `W*`).
* Use with care because clipping paths affect subsequent rendering.

**`min_points`** (int, default: `4`)

* Minimum number of sampled points required before simplification is attempted on a subpath.

**`max_error_scale`** (float, default: `4.0`)

* Error multiplier used by curve-fitting heuristics when attempting iterative refinement before
  subdivision.

## Notes

- Tolerances are specified in device-space points, not PDF user-space units.
- Graphics-state transforms (`cm`, `q`, `Q`) are tracked automatically.
- Form XObjects and Tiling Patterns are processed recursively.
- When simplification is not beneficial, the original path is emitted unchanged.
"""


@register_operation(
    "simplify_vectors",
    tags=["in_place", "content_stream", "custom"],
    type="single input operation",
    desc="Reduce vector path complexity",
    long_desc=_LONG_DESC,
    usage="<input> simplify_vectors [<pages>] [key=val ...] output <output>",
    examples=_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS_EXPANDED], {}),
)
def simplify_vectors_in_content_streams(pdf, args) -> OpResult:
    """Entry point registered with the pdftl operation registry."""
    ensure_dependencies(
        feature_name="simplify_vectors",
        dependencies=["numpy", "numba"],
        extra_tag="simplify-vectors",
    )

    args = args or []
    specs = _parse_args(args)
    stats = SimplifyStats()

    for page_spec, config in specs:
        processor = _StreamProcessor(pdf, config, stats)
        _apply_to_pages(pdf, page_spec, processor)

    _log_stats(stats)
    return OpResult(success=True, pdf=pdf)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _parse_args(args: list[str]) -> list[tuple[str, SimplifyConfig]]:
    if not args:
        return [("-", SimplifyConfig())]
    return [_parse_single(arg) for arg in args]


def _parse_single(arg: str) -> tuple[str, SimplifyConfig]:
    if "(" in arg:
        page_spec, _, rest = arg.partition("(")
        tokens = [t.strip() for t in rest.rstrip(")").split(",") if t.strip()]
    else:
        page_spec = arg
        tokens = []

    kwargs = parse_keyval_list(
        tokens, allowed_keys=list(_ALLOWED_KEYS), context="simplify_vectors"
    )
    config = _build_config(kwargs)
    return page_spec or "-", config


def _build_config(kw: dict[str, str]) -> SimplifyConfig:
    def _float(key, default):
        raw = kw.get(key, str(default))
        try:
            return float(raw)
        except ValueError:
            raise InvalidArgumentError(f"simplify_vectors: invalid value for '{key}': '{raw}'")

    def _bool(key, default):
        raw = kw.get(key, str(default)).lower()
        if raw in ("true", "1", "yes"):
            return True
        if raw in ("false", "0", "no"):
            return False
        raise InvalidArgumentError(f"simplify_vectors: '{key}' must be true or false, got '{raw}'")

    def _int(key, default):
        raw = kw.get(key, str(default))
        try:
            return int(raw)
        except ValueError:
            raise InvalidArgumentError(f"simplify_vectors: invalid value for '{key}': '{raw}'")

    tol = _float("tolerance", 0.15)
    if tol <= 0:
        raise InvalidArgumentError(f"simplify_vectors: tolerance must be positive, got '{tol}'")

    return SimplifyConfig(
        tolerance=tol,
        curves=_bool("curves", True),
        lines=_bool("lines", True),
        clip_paths=_bool("clip_paths", False),
        min_points=_int("min_points", 4),
        max_error_scale=_float("max_error_scale", 4.0),
        coalesce_strokes=_bool("coalesce_strokes", True),
    )


# ---------------------------------------------------------------------------
# Page dispatch
# ---------------------------------------------------------------------------


def _apply_to_pages(pdf, spec: str, processor: _StreamProcessor) -> None:
    num_pages = len(pdf.pages)
    for page_num in page_numbers_matching_page_spec(spec, num_pages):
        processor.apply_to_page(page_num)


# ---------------------------------------------------------------------------
# Stream processor
# ---------------------------------------------------------------------------


class _StreamProcessor:
    """Applies the four-stage pipeline to pages and XObjects, tracking visited streams."""

    def __init__(self, pdf, config: SimplifyConfig, stats: SimplifyStats) -> None:
        self._pdf = pdf
        self._config = config
        self._stats = stats
        self._processed: set[tuple[int, int]] = set()  # objgen deduplication
        self._pikepdf: pikepdf_t | None = None

    # -- lazy pikepdf import --

    @property
    def _pk(self) -> pikepdf_t:
        if self._pikepdf is None:
            import pikepdf

            self._pikepdf = pikepdf
        return self._pikepdf

    # -- public entry points --

    def apply_to_page(self, page_num: int) -> None:
        page = self._pdf.pages[page_num - 1]
        if page.get("/Contents") is None:
            logger.debug("Page %d has no content stream; skipping.", page_num)
            return

        logger.debug("Processing page %d — coalescing content stream.", page_num)
        self._pk.Page(page).contents_coalesce()
        stream = page["/Contents"]

        if stream.objgen in self._processed:
            logger.debug("Stream %s already processed; skipping.", stream.objgen)
            return

        self._process_stream(stream)
        self._processed.add(stream.objgen)

        resources = get_resources(page)
        if resources is not None:
            self._recurse_resources(resources)

    # -- stream processing --

    def _process_stream(self, stream_obj: Any) -> None:
        """Run all four pipeline stages on one content stream."""
        # Stage 1: parse
        try:
            instructions = self._pk.parse_content_stream(stream_obj)
        except self._pikepdf.PdfError as exc:
            logger.warning("Failed to parse content stream %s: %s", stream_obj.objgen, exc)
            return

        self._stats.streams_processed += 1
        self._stats.operators_before += len(instructions)

        # Stage 2: segment
        mixed = segment(instructions, self._config)

        # Stage 3: simplify each Path
        results: list[Any] = []
        for item in mixed:
            if isinstance(item, Path):
                self._stats.paths_total += 1
                self._tally_subpaths(item)
                sp = simplify_path(item, self._config)
                self._tally_result(item, sp)
                results.append((item, sp))
            else:
                results.append(item)

        # Stage 4: serialize
        new_instructions = serialize(results)
        self._stats.operators_after += len(new_instructions)

        try:
            stream_obj.write(self._pk.unparse_content_stream(new_instructions))
        except self._pikepdf.PdfError as exc:
            logger.warning("Failed to write simplified stream: %s", exc)

    # -- resource recursion --

    def _recurse_resources(self, resources: Any) -> None:
        self._recurse_xobjects(resources)
        self._recurse_patterns(resources)

    def _recurse_xobjects(self, resources: Any) -> None:
        if "/XObject" not in resources:
            return
        for name, xobj in resources.XObject.items():
            if xobj.objgen in self._processed:
                continue
            if xobj.get("/Subtype") != "/Form":
                continue
            logger.debug("Recursing into Form XObject %s (%s)", name, xobj.objgen)
            self._processed.add(xobj.objgen)
            self._process_stream(xobj)
            if "/Resources" in xobj:
                self._recurse_resources(xobj.Resources)

    def _recurse_patterns(self, resources: Any) -> None:
        if "/Pattern" not in resources:
            return
        for name, pat in resources.Pattern.items():
            if pat.objgen in self._processed:
                continue
            try:
                if int(pat.get("/PatternType", 0)) != 1:
                    continue
            except (TypeError, ValueError):
                continue
            logger.debug("Recursing into Tiling Pattern %s (%s)", name, pat.objgen)
            self._processed.add(pat.objgen)
            self._process_stream(pat)
            if "/Resources" in pat:
                self._recurse_resources(pat.Resources)

    # -- stats helpers --

    def _tally_subpaths(self, path: Path) -> None:
        for sp in path.subpaths:
            self._stats.points_before += len(sp.points)
            if sp.has_curves:
                self._stats.subpaths_curved += 1
            else:
                self._stats.subpaths_linear += 1
        if path.is_clipping:
            self._stats.paths_clipping += 1

    def _tally_result(self, path: Path, sp: SimplifiedPath) -> None:
        if sp.fell_back:
            self._stats.paths_fallback += 1
            for sub in path.subpaths:
                self._stats.points_after += len(sub.points)
        else:
            self._stats.paths_optimised += 1
            # Count output points from the simplified instructions
            for ops, operator in sp.subpath_instructions:
                if operator == "m":
                    self._stats.points_after += 1
                elif operator == "l":
                    self._stats.points_after += 1
                elif operator == "c":
                    self._stats.points_after += 3


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _log_stats(stats: SimplifyStats) -> None:
    if stats.streams_processed == 0:
        logger.info("simplify_vectors: no streams processed.")
        return

    pts_saved = stats.points_before - stats.points_after
    pts_pct = (pts_saved / stats.points_before * 100.0) if stats.points_before > 0 else 0.0
    ops_saved = stats.operators_before - stats.operators_after
    ops_pct = (ops_saved / stats.operators_before * 100.0) if stats.operators_before > 0 else 0.0

    logger.info(
        "\n"
        "=================================================================\n"
        "                VECTOR SIMPLIFICATION SUMMARY                   \n"
        "=================================================================\n"
        " Streams Processed:          %d\n"
        "-----------------------------------------------------------------\n"
        " Paths Total:                %d\n"
        "   Optimised:                %d\n"
        "   Fallback:                 %d\n"
        "   Bypassed:                 %d\n"
        "   Clipping:                 %d\n"
        "-----------------------------------------------------------------\n"
        " Subpaths:\n"
        "   Curved  (Schneider fit):  %d\n"
        "   Linear  (RDP):            %d\n"
        "-----------------------------------------------------------------\n"
        " Coordinate Points:\n"
        "   Before:                   %d\n"
        "   After:                    %d\n"
        "   Reduction:                %d (%.1f%%)\n"
        "-----------------------------------------------------------------\n"
        " Content Stream Operators:\n"
        "   Before:                   %d\n"
        "   After:                    %d\n"
        "   Reduction:                %d (%.1f%%)\n"
        "=================================================================",
        stats.streams_processed,
        stats.paths_total,
        stats.paths_optimised,
        stats.paths_fallback,
        stats.paths_bypassed,
        stats.paths_clipping,
        stats.subpaths_curved,
        stats.subpaths_linear,
        stats.points_before,
        stats.points_after,
        pts_saved,
        pts_pct,
        stats.operators_before,
        stats.operators_after,
        ops_saved,
        ops_pct,
    )
