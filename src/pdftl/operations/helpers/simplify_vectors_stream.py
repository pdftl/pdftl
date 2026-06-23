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
# Operator sets
# ---------------------------------------------------------------------------

_PATH_CONSTRUCTION_OPS = frozenset({"m", "l", "c", "v", "y", "h", "re"})
_PATH_PAINTING_OPS = frozenset({"S", "s", "f", "F", "f*", "B", "b", "B*", "b*", "n"})
_CLIPPING_OPS = frozenset({"W", "W*"})
_GS_OPS = frozenset({"q", "Q", "cm", "w"})


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
    return _Segmenter(config).process(instructions)


class _Segmenter:
    """State machine that segments a PDF instruction list into Path objects
    and pass-through instructions."""

    def __init__(self, config: SimplifyConfig) -> None:
        self._config = config
        self._gs_stack = GraphicsStateStack()
        self._out: list[Path | Any] = []
        # Current path accumulators
        self._current_path_ops: list[Any] = []
        self._subpaths: list[Subpath] = []
        self._current_pts: list[tuple[float, float]] = []
        self._current_has_curves: bool = False
        self._current_op_count: int = 0

    def process(self, instructions: list[Any]) -> list[Path | Any]:
        for operands, operator in instructions:
            self._dispatch(str(operator), operands, operator)
        # Flush any trailing unclosed path (malformed PDF safety)
        if self._current_path_ops or self._subpaths or self._current_pts:
            logger.debug("Trailing unclosed path at end of stream; falling back.")
            self._flush_path(None, None)
        return self._out

    def _dispatch(self, op: str, operands: Any, operator: Any) -> None:
        if op in _GS_OPS:
            self._handle_gs(op, operands, operator)
        elif op in _CLIPPING_OPS:
            self._handle_clipping(operands, operator)
        elif op in _PATH_CONSTRUCTION_OPS:
            self._handle_construction(op, operands, operator)
        elif op in _PATH_PAINTING_OPS:
            self._handle_painting(op, operands, operator)
        else:
            self._handle_passthrough(op, operands, operator)

    # ------------------------------------------------------------------
    # Operator handlers
    # ------------------------------------------------------------------

    def _handle_gs(self, op: str, operands: Any, operator: Any) -> None:
        """Handle q, Q, cm, w — update graphics state and pass through."""
        gs = self._gs_stack.current
        if op == "q":
            self._gs_stack.push()
        elif op == "Q":
            self._gs_stack.pop()
        elif op == "cm":
            gs.apply_cm([float(x) for x in operands])
        elif op == "w":
            gs.set_line_width(operands)

        # A gs op inside a path is unusual but legal; treat as interrupt
        if self._current_path_ops:
            self._flush_path(None, None)
        self._out.append((operands, operator))

    def _handle_clipping(self, operands: Any, operator: Any) -> None:
        """Handle W, W* — mark clipping state and pass through."""
        self._gs_stack.current.mark_clipping()
        if self._current_path_ops:
            self._current_path_ops.append((operands, operator))
        else:
            self._out.append((operands, operator))

    def _handle_construction(self, op: str, operands: Any, operator: Any) -> None:
        """Handle m, l, c, v, y, h, re — accumulate path geometry."""
        self._current_path_ops.append((operands, operator))

        if op == "m":
            self._flush_subpath(closed=False)
            self._current_pts = [(float(operands[0]), float(operands[1]))]
            self._current_op_count = 1

        elif op == "l":
            if self._current_pts:
                self._current_pts.append((float(operands[0]), float(operands[1])))
                self._current_op_count += 1

        elif op == "c":
            self._handle_cubic(operands)

        elif op == "v":
            # First control point equals current point
            self._handle_cubic_implicit_p1(operands)

        elif op == "y":
            # Last control point equals endpoint
            self._handle_cubic_implicit_p3(operands)

        elif op == "h":
            self._flush_subpath(closed=True)

        elif op == "re":
            self._handle_rect(operands)

    def _handle_painting(self, op: str, operands: Any, operator: Any) -> None:
        """Handle S, f, n, etc. — finalise and emit the current path."""
        if self._current_path_ops or self._subpaths or self._current_pts:
            self._flush_path(op, operands)
        else:
            self._out.append((operands, operator))

    def _handle_passthrough(self, op: str, operands: Any, operator: Any) -> None:
        """Handle everything else — pass through, interrupting any open path."""
        if self._current_path_ops:
            logger.debug(
                "Non-path operator '%s' encountered while building path; falling back.", op
            )
            self._flush_path(None, None)
        self._out.append((operands, operator))

    # ------------------------------------------------------------------
    # Bézier helpers
    # ------------------------------------------------------------------

    def _handle_cubic(self, operands: Any) -> None:
        """c operator — all six operands are explicit."""
        if not self._current_pts:
            return
        p0 = self._current_pts[-1]
        p1 = (float(operands[0]), float(operands[1]))
        p2 = (float(operands[2]), float(operands[3]))
        p3 = (float(operands[4]), float(operands[5]))
        self._sample_bezier(p0, p1, p2, p3)

    def _handle_cubic_implicit_p1(self, operands: Any) -> None:
        """v operator — first control point equals current point."""
        if not self._current_pts:
            return
        p0 = self._current_pts[-1]
        p2 = (float(operands[0]), float(operands[1]))
        p3 = (float(operands[2]), float(operands[3]))
        self._sample_bezier(p0, p0, p2, p3)

    def _handle_cubic_implicit_p3(self, operands: Any) -> None:
        """y operator — last control point equals endpoint."""
        if not self._current_pts:
            return
        p0 = self._current_pts[-1]
        p1 = (float(operands[0]), float(operands[1]))
        p2 = (float(operands[2]), float(operands[3]))
        self._sample_bezier(p0, p1, p2, p2)

    def _sample_bezier(
        self,
        p0: tuple[float, float],
        p1: tuple[float, float],
        p2: tuple[float, float],
        p3: tuple[float, float],
    ) -> None:
        tol = self._gs_stack.current.user_space_tolerance(self._config.tolerance)
        self._current_pts.extend(sample_cubic_bezier(p0, p1, p2, p3, tol))
        self._current_has_curves = True
        self._current_op_count += 1

    def _handle_rect(self, operands: Any) -> None:
        """re operator — expand to four corner points, or leave as-is if degenerate."""
        rx, ry = float(operands[0]), float(operands[1])
        rw, rh = float(operands[2]), float(operands[3])
        if rw == 0 or rh == 0:
            # Degenerate rectangle — treat as a no-op construction operator.
            # Already appended to current_path_ops above so it stays in
            # original_instructions and serializes correctly within the path.
            return
        self._flush_subpath(closed=False)
        self._current_pts = [
            (rx, ry),
            (rx + rw, ry),
            (rx + rw, ry + rh),
            (rx, ry + rh),
        ]
        self._current_has_curves = False
        self._current_op_count = 5  # m + 3×l + h

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
