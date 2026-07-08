# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/helpers/simplify_vectors_stream.py

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

from pdftl.utils.graphics_state import GraphicsStateStack
from pdftl.utils.path_geometry import sample_cubic_bezier
from pdftl.utils.path_types import Path, SimplifiedPath, SimplifyConfig, Subpath

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stage 2: Segment
# ---------------------------------------------------------------------------


def segment(instructions: list[Any], config: SimplifyConfig) -> list[Path | Any]:
    """Convert a raw instruction list into a mixed list of Path objects and
    pass-through instructions.

    Graphics state operators (q, Q, cm, w, W, W*) are tracked to keep the
    current CTM and line width accurate; they still appear as pass-through
    instructions in the output so the content stream remains valid.
    """
    mixed = _Segmenter(config).process(instructions)
    if config.coalesce_strokes:
        mixed = _coalesce_strokes(mixed)
    return mixed


class _Segmenter:
    """State machine that segments a PDF instruction list into Path objects
    and pass-through instructions."""

    def __init__(self, config: SimplifyConfig) -> None:
        self._config = config
        self._gs_stack = GraphicsStateStack()
        # Cache tolerance locally; only update when CTM changes via q/Q/cm
        self._current_tolerance = self._gs_stack.current.user_space_tolerance(
            self._config.tolerance
        )

        self._out: list[Path | Any] = []
        # Current path accumulators
        self._current_path_ops: list[Any] = []
        self._subpaths: list[Subpath] = []
        self._current_pts: list[tuple[float, float]] = []
        self._current_has_curves: bool = False
        self._current_op_count: int = 0

        # O(1) dictionary dispatch mapping
        self._handlers = {
            "q": self._handle_q,
            "Q": self._handle_Q,
            "cm": self._handle_cm,
            "w": self._handle_w,
            "W": self._handle_clipping,
            "W*": self._handle_clipping,
            "m": self._handle_m,
            "l": self._handle_l,
            "c": self._handle_cubic,
            "v": self._handle_v,
            "y": self._handle_y,
            "h": self._handle_h,
            "re": self._handle_re,
        }
        for op in ("S", "s", "f", "F", "f*", "B", "b", "B*", "b*", "n"):
            self._handlers[op] = self._handle_painting

    def process(self, instructions: list[Any]) -> list[Path | Any]:
        # Cache to locals for tight loop performance
        handlers = self._handlers
        passthrough = self._handle_passthrough

        for operands, operator in instructions:
            op_str = str(operator)
            handlers.get(op_str, passthrough)(op_str, operands, operator)

        # Flush any trailing unclosed path (malformed PDF safety)
        if self._current_path_ops or self._subpaths or self._current_pts:
            logger.debug("Trailing unclosed path at end of stream; falling back.")
            self._flush_path(None, None)
        return self._out

    # ------------------------------------------------------------------
    # Operator handlers
    # ------------------------------------------------------------------

    def _handle_q(self, op: str, operands: Any, operator: Any) -> None:
        self._gs_stack.push()
        self._current_tolerance = self._gs_stack.current.user_space_tolerance(
            self._config.tolerance
        )
        if self._current_path_ops:
            self._flush_path(None, None)
        self._out.append((operands, operator))

    def _handle_Q(self, op: str, operands: Any, operator: Any) -> None:
        self._gs_stack.pop()
        self._current_tolerance = self._gs_stack.current.user_space_tolerance(
            self._config.tolerance
        )
        if self._current_path_ops:
            self._flush_path(None, None)
        self._out.append((operands, operator))

    def _handle_cm(self, op: str, operands: Any, operator: Any) -> None:
        self._gs_stack.current.apply_cm([float(x) for x in operands])
        self._current_tolerance = self._gs_stack.current.user_space_tolerance(
            self._config.tolerance
        )
        if self._current_path_ops:
            self._flush_path(None, None)
        self._out.append((operands, operator))

    def _handle_w(self, op: str, operands: Any, operator: Any) -> None:
        self._gs_stack.current.set_line_width(operands)
        if self._current_path_ops:
            self._flush_path(None, None)
        self._out.append((operands, operator))

    def _handle_clipping(self, op: str, operands: Any, operator: Any) -> None:
        self._gs_stack.current.mark_clipping()
        if self._current_path_ops:
            self._current_path_ops.append((operands, operator))
        else:
            self._out.append((operands, operator))

    def _handle_m(self, op: str, operands: Any, operator: Any) -> None:
        self._current_path_ops.append((operands, operator))
        self._flush_subpath(closed=False)
        self._current_pts = [(float(operands[0]), float(operands[1]))]
        self._current_op_count = 1

    def _handle_l(self, op: str, operands: Any, operator: Any) -> None:
        self._current_path_ops.append((operands, operator))
        if self._current_pts:
            self._current_pts.append((float(operands[0]), float(operands[1])))
            self._current_op_count += 1

    def _handle_cubic(self, op: str, operands: Any, operator: Any) -> None:
        self._current_path_ops.append((operands, operator))
        if not self._current_pts:
            return
        p0 = self._current_pts[-1]
        p1 = (float(operands[0]), float(operands[1]))
        p2 = (float(operands[2]), float(operands[3]))
        p3 = (float(operands[4]), float(operands[5]))
        self._sample_bezier(p0, p1, p2, p3)

    def _handle_v(self, op: str, operands: Any, operator: Any) -> None:
        self._current_path_ops.append((operands, operator))
        if not self._current_pts:
            return
        p0 = self._current_pts[-1]
        p2 = (float(operands[0]), float(operands[1]))
        p3 = (float(operands[2]), float(operands[3]))
        self._sample_bezier(p0, p0, p2, p3)

    def _handle_y(self, op: str, operands: Any, operator: Any) -> None:
        self._current_path_ops.append((operands, operator))
        if not self._current_pts:
            return
        p0 = self._current_pts[-1]
        p1 = (float(operands[0]), float(operands[1]))
        p2 = (float(operands[2]), float(operands[3]))
        self._sample_bezier(p0, p1, p2, p2)

    def _handle_h(self, op: str, operands: Any, operator: Any) -> None:
        self._current_path_ops.append((operands, operator))
        self._flush_subpath(closed=True)

    def _handle_re(self, op: str, operands: Any, operator: Any) -> None:
        self._current_path_ops.append((operands, operator))
        rx, ry = float(operands[0]), float(operands[1])
        rw, rh = float(operands[2]), float(operands[3])
        if rw == 0 or rh == 0:
            return
        self._flush_subpath(closed=False)
        self._current_pts = [
            (rx, ry),
            (rx + rw, ry),
            (rx + rw, ry + rh),
            (rx, ry + rh),
        ]
        self._current_has_curves = False
        self._current_op_count = 5

    def _handle_painting(self, op: str, operands: Any, operator: Any) -> None:
        if self._current_path_ops or self._subpaths or self._current_pts:
            self._flush_path(op, operands)
        else:
            self._out.append((operands, operator))

    def _handle_passthrough(self, op: str, operands: Any, operator: Any) -> None:
        if self._current_path_ops:
            logger.debug(
                "Non-path operator '%s' encountered while building path; falling back.", op
            )
            self._flush_path(None, None)
        self._out.append((operands, operator))

    # ------------------------------------------------------------------
    # Bézier helpers
    # ------------------------------------------------------------------

    def _sample_bezier(
        self,
        p0: tuple[float, float],
        p1: tuple[float, float],
        p2: tuple[float, float],
        p3: tuple[float, float],
    ) -> None:
        self._current_pts.extend(sample_cubic_bezier(p0, p1, p2, p3, self._current_tolerance))
        self._current_has_curves = True
        self._current_op_count += 1

    # ------------------------------------------------------------------
    # Flush helpers
    # ------------------------------------------------------------------

    def _flush_subpath(self, closed: bool) -> None:
        if self._current_pts:
            self._subpaths.append(
                Subpath(
                    points=self._current_pts,
                    closed=closed,
                    has_curves=self._current_has_curves,
                    ctm_scale=self._gs_stack.current.scale,
                    original_op_count=self._current_op_count,
                )
            )
        self._current_pts = []
        self._current_has_curves = False
        self._current_op_count = 0

    def _flush_path(self, paint_op: str | None, operands: Any) -> None:
        self._flush_subpath(closed=False)
        is_clip = self._gs_stack.current.consume_clipping()
        path = Path(
            subpaths=list(self._subpaths),
            paint_op=paint_op,
            original_instructions=list(self._current_path_ops)
            + ([(operands, paint_op)] if paint_op else []),
            is_clipping=is_clip,
            state_snapshot=self._gs_stack.current.clone(),
        )
        self._out.append(path)
        self._current_path_ops.clear()
        self._subpaths.clear()


# ---------------------------------------------------------------------------
# Path Coalescing (Shattered Stroke Fix)
# ---------------------------------------------------------------------------


def _is_stroke_path(item: Any) -> bool:
    return isinstance(item, Path) and item.paint_op in ("S", "s") and not item.is_clipping


def _is_w_op(item: Any) -> bool:
    if isinstance(item, tuple) and len(item) == 2:
        return str(item[1]) == "w"
    return False


def _connects(path_a: Path, path_b: Path) -> bool:
    """True if path_a seamlessly flows into path_b in device-space."""
    if not path_a.subpaths or not path_b.subpaths:
        return False
    sp_a = path_a.subpaths[-1]
    sp_b = path_b.subpaths[0]
    if not sp_a.points or not sp_b.points:
        return False

    p1 = sp_a.points[-1]
    p2 = sp_b.points[0]
    dx = p1[0] - p2[0]
    dy = p1[1] - p2[1]

    # Endpoint coordinates are raw, pre-CTM user-space units, so the matching
    # tolerance must scale with the CTM in effect at each subpath, or it will
    # be too strict/loose depending on the zoom/scale of the content.
    scale = max(sp_a.ctm_scale, sp_b.ctm_scale, 1e-9)
    tolerance = 1e-4 * scale
    return (dx * dx + dy * dy) < tolerance * tolerance


def _merge_stroke_group(
    paths: list[Path], widths: list[Any]
) -> tuple[Path, Any | None, Any | None]:
    """Combines a contiguous chain of strokes into a single Path structure."""
    initial_w = None
    final_w = None
    if widths:
        try:
            # Capture the average width to preserve the visual weight of the tapered stroke
            valid_widths = [float(w[0][0]) for w in widths]
            if valid_widths:
                avg_w = sum(valid_widths) / len(valid_widths)
                initial_w = ([avg_w], widths[0][1])
                final_w = widths[-1]
        except (IndexError, ValueError, TypeError):
            pass

    merged_subpaths = []

    # Prime the merged list with the first path's subpaths
    for sp in paths[0].subpaths:
        merged_subpaths.append(
            Subpath(
                points=list(sp.points),
                closed=sp.closed,
                has_curves=sp.has_curves,
                ctm_scale=sp.ctm_scale,
                original_op_count=sp.original_op_count,
            )
        )

    # Start with the original ops, skipping the terminal 'S'
    merged_orig_ops = list(paths[0].original_instructions[:-1])

    for p in paths[1:]:
        first_sp = p.subpaths[0]
        last_merged_sp = merged_subpaths[-1]

        # Splice the connecting point sequences
        last_merged_sp.points.extend(first_sp.points[1:])
        last_merged_sp.has_curves = last_merged_sp.has_curves or first_sp.has_curves
        # Add op count but discount the dropped 'm' operator
        last_merged_sp.original_op_count += max(0, first_sp.original_op_count - 1)

        # Append any subsequent subpaths verbatim
        for sp in p.subpaths[1:]:
            merged_subpaths.append(
                Subpath(
                    points=list(sp.points),
                    closed=sp.closed,
                    has_curves=sp.has_curves,
                    ctm_scale=sp.ctm_scale,
                    original_op_count=sp.original_op_count,
                )
            )

        # Merge original instructions, carefully discarding the intermediate 'm'
        ops_to_add = p.original_instructions[:-1]
        if ops_to_add and str(ops_to_add[0][1]) == "m":
            ops_to_add = ops_to_add[1:]
        merged_orig_ops.extend(ops_to_add)

    # Cap it off with the paint operator
    merged_orig_ops.append(paths[0].original_instructions[-1])

    merged_path = Path(
        subpaths=merged_subpaths,
        paint_op=paths[0].paint_op,
        original_instructions=merged_orig_ops,
        is_clipping=False,
        state_snapshot=paths[0].state_snapshot,
    )

    return merged_path, initial_w, final_w


def _check_width_variance(
    w_op: Any, group_min: float, group_max: float
) -> tuple[bool, float, float]:
    """Verifies if a new width operator exceeds the allowed variance thresholds."""
    try:
        w_val = float(w_op[0][0])
        new_min = min(group_min, w_val)
        new_max = max(group_max, w_val)

        if new_min < float("inf"):
            # Bound variance: total spread must not exceed 10% OR 0.05 points
            if (new_max - new_min) > max(0.05, new_min * 0.10):
                return True, group_min, group_max
        return False, new_min, new_max
    except (IndexError, ValueError, TypeError):
        # Suppress parsing or indexing errors on malformed width operators
        # and allow coalescing to proceed safely without breaking the chain.
        return False, group_min, group_max


def _collect_stroke_group(
    mixed: list[Path | Any], start_idx: int, n: int
) -> tuple[list[Path], list[Any], int]:
    """Collects a contiguous sequence of stroke paths and intervening width operations."""
    group_paths = [mixed[start_idx]]
    group_widths = []

    # Seed variance tracking with the width already in effect for the first
    # stroke (set upstream of the group), rather than an unbounded sentinel —
    # otherwise a large width jump right at the group boundary goes undetected.
    try:
        initial_width = float(mixed[start_idx].state_snapshot.line_width)
        group_min_w = initial_width
        group_max_w = initial_width
    except (AttributeError, TypeError, ValueError):
        group_min_w = float("inf")
        group_max_w = 0.0

    j = start_idx + 1
    while j < n:
        next_item = mixed[j]
        if _is_w_op(next_item):
            should_break, new_min, new_max = _check_width_variance(
                next_item, group_min_w, group_max_w
            )
            if should_break:
                break
            group_min_w = new_min
            group_max_w = new_max
            group_widths.append(next_item)
            j += 1
        elif _is_stroke_path(next_item) and _connects(group_paths[-1], next_item):
            group_paths.append(next_item)
            j += 1
        else:
            break
    return group_paths, group_widths, j


def _append_final_width_if_different(out: list[Any], initial_w: Any, final_w: Any) -> None:
    """Appends final_w only if it leaves the stream state materially different."""
    try:
        if abs(float(initial_w[0][0]) - float(final_w[0][0])) > 1e-4:
            out.append(final_w)
    except (IndexError, ValueError, TypeError):
        # Suppress float conversion or index errors on malformed width ops,
        # appending the final width as a safe fallback.
        out.append(final_w)


def _emit_coalesced_group(
    out: list[Any], group_paths: list[Path], group_widths: list[Any]
) -> None:
    """Helper to serialize and append a validated, merged stroke group to the output list."""
    merged_path, initial_w, final_w = _merge_stroke_group(group_paths, group_widths)

    if initial_w is not None:
        out.append(initial_w)

    out.append(merged_path)

    if final_w is not None:
        _append_final_width_if_different(out, initial_w, final_w)


def _coalesce_strokes(mixed: list[Path | Any]) -> list[Path | Any]:
    """Scans for and merges contiguous fractured strokes separated only by 'w' ops."""
    out = []
    i = 0
    n = len(mixed)

    while i < n:
        item = mixed[i]

        if not _is_stroke_path(item):
            out.append(item)
            i += 1
            continue

        # Found start of a potential group
        group_paths, group_widths, j = _collect_stroke_group(mixed, i, n)

        if len(group_paths) == 1:
            # Nothing to coalesce
            out.extend(mixed[i:j])
        else:
            _emit_coalesced_group(out, group_paths, group_widths)

        i = j

    return out


# ---------------------------------------------------------------------------
# Stage 4: Serialize
# ---------------------------------------------------------------------------


def serialize(mixed: list[Path | Any]) -> list[Any]:
    """Convert mixed list back to a flat pikepdf-compatible instruction list."""
    out: list[Any] = []
    for item in mixed:
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], Path):
            path, sp = item
            _emit_path(sp, path, out)
        else:
            out.append(item)
    return out


def _emit_path(sp: SimplifiedPath, path: Path, out: list) -> None:
    """Append either the simplified or original path instructions to out."""
    if sp.fell_back or not sp.subpath_instructions:
        out.extend(path.original_instructions)
        return

    for operands, op_str in sp.subpath_instructions:
        out.append((operands, op_str))

    # Re-emit the paint operator, preserving the original pikepdf operator object
    for orig_ops, orig_op in path.original_instructions:
        if str(orig_op) == sp.paint_op:
            out.append((orig_ops, orig_op))
            return

    # Fallback: emit as plain string (pikepdf handles this)
    out.append(([], sp.paint_op))
