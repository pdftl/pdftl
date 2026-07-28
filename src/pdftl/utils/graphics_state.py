# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/graphics_state.py

"""
Lightweight PDF graphics state tracker for the subset of state that affects
geometric simplification: the current transformation matrix (CTM), current
line width, and whether the next path is a clipping path.

Only the operators that change these properties need to be tracked:
    q / Q   — save / restore
    cm      — concatenate matrix
    w       — line width
    W / W*  — next path is a clipping path (reset after painting op)

All other graphics state (colour, dash, blend mode, etc.) is ignored here;
it passes through the content stream unchanged.

Text-state tracking (opt-in, used by callers that need glyph-level
positioning, e.g. `trim`/`redact`) additionally covers:
    BT / ET               — text object bracketing (Tm/Tlm reset to identity)
    Tm                     — set text matrix and text line matrix directly
    Td / TD                — offset the text line matrix, then Tm := Tlm
    T*                     — equivalent to `0 -TL Td` using the current leading
    Tc / Tw / Tz / TL / Ts — character/word spacing, horizontal scale,
                              leading, and rise; all feed glyph-advance math
    Tf                     — current font resource name + size (needed by
                              callers to look up glyph widths)
    Tj / TJ / ' / "        — advance the text matrix by the shown string's
                              width; TJ additionally applies per-element
                              numeric kerning adjustments

Text-state operators do not by themselves change the CTM; glyph device-space
position is the composition of the text matrix with the current CTM, i.e.
``text_rendering_matrix = Tm x CTM`` (see `GraphicsState.text_render_matrix`).
Callers who only need vector/image geometry (e.g. `simplify_vectors`) are
unaffected: text handling is inert unless `apply_text_op` is called.

Public API
----------
GraphicsState           dataclass — carries CTM, line_width, is_clipping
GraphicsStateStack      thin wrapper around a list[GraphicsState]
ctm_scale(ctm)          approximate uniform scale of a 6-element CTM

Text-state additions
---------------------
GraphicsState.apply_text_op(op, operands, glyph_width_fn)
                         dispatch a single text-state or text-showing
                         operator against this state
GraphicsState.text_render_matrix
                         current Tm composed with CTM (device-space glyph
                         origin transform)
"""

from __future__ import annotations

import math
from copy import copy
from dataclasses import dataclass, field
from collections.abc import Callable

# PDF spec §8.3.4 — maximum graphics state stack depth
_MAX_STACK_DEPTH = 32

# Identity CTM
_IDENTITY_CTM: tuple[float, ...] = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)

# Text-showing operators that advance the text matrix.
_TEXT_SHOW_OPS = frozenset({"Tj", "TJ", "'", '"'})


def _multiply_ctm(m: tuple[float, ...], n: tuple[float, ...]) -> tuple[float, ...]:
    """Concatenate two 6-element affine matrices.

    PDF column-vector convention:
        [a b 0]   [a2 b2 0]
        [c d 0] × [c2 d2 0]  →  result
        [e f 1]   [e2 f2 1]
    """
    a, b, c, d, e, f = m
    a2, b2, c2, d2, e2, f2 = n
    return (
        a * a2 + b * c2,
        a * b2 + b * d2,
        c * a2 + d * c2,
        c * b2 + d * d2,
        e * a2 + f * c2 + e2,
        e * b2 + f * d2 + f2,
    )


# Alias exposed for callers that need to compose text/CTM matrices
# themselves (e.g. transforming a glyph-local bbox to device space).
multiply_matrices = _multiply_ctm


def ctm_scale(ctm: tuple[float, ...]) -> float:
    """Approximate the uniform scale factor of a CTM.

    Uses the RMS of the four linear components (a, b, c, d):
        scale ≈ sqrt((a² + b² + c² + d²) / 2)

    This is exact for uniform scales and rotations, and a reasonable
    approximation for mild non-uniform transforms.  For the purpose of
    mapping a device-space tolerance back to user space it is sufficient.

    Returns 1.0 if the matrix is degenerate (all linear components ≈ 0)
    to avoid division by zero at call sites.
    """
    a, b, c, d = ctm[0], ctm[1], ctm[2], ctm[3]
    rms = math.sqrt((a * a + b * b + c * c + d * d) / 2.0)
    return rms if rms > 1e-9 else 1.0


@dataclass
class GraphicsState:
    """The subset of PDF graphics state relevant to path simplification."""

    # 6-element row-major affine CTM: [a, b, c, d, e, f]
    ctm: tuple[float, ...] = field(default_factory=lambda: _IDENTITY_CTM)

    # Current line width in user space (set by the 'w' operator)
    line_width: float = 1.0

    # True when W or W* has been seen and the next painting op is a clip
    is_clipping: bool = False

    # ------------------------------------------------------------------
    # Text state (all optional; only populated if apply_text_op is used)
    # ------------------------------------------------------------------

    # Text matrix and text line matrix (reset to identity on BT, and on
    # every Td/TD/T*/Tm per spec §9.4.2). None outside a BT...ET block.
    text_matrix: tuple[float, ...] | None = None
    text_line_matrix: tuple[float, ...] | None = None

    # Character spacing (Tc), word spacing (Tw), horizontal scaling as a
    # fraction where 1.0 = 100% (Tz / 100), leading (TL), and rise (Ts).
    char_spacing: float = 0.0
    word_spacing: float = 0.0
    horizontal_scale: float = 1.0
    leading: float = 0.0
    text_rise: float = 0.0

    # Current font resource name (e.g. "/F1") and size, set by Tf. Callers
    # combine font_name with a font-widths lookup (see
    # pdftl.fonts.widths_utils.extract_font_widths) to get glyph advances;
    # this module has no knowledge of font resources itself.
    font_name: str | None = None
    font_size: float = 0.0

    # ------------------------------------------------------------------
    # Mutation helpers (return self for convenience)
    # ------------------------------------------------------------------

    def apply_cm(self, operands: list) -> None:
        """Concatenate a new matrix from the 'cm' operator's 6 operands."""
        new_m: tuple[float, ...] = tuple(float(x) for x in operands)  # type: ignore[assignment]
        self.ctm = _multiply_ctm(new_m, self.ctm)

    def set_line_width(self, operands: list) -> None:
        """Update line width from the 'w' operator's operand."""
        try:
            self.line_width = float(operands[0])
        except (IndexError, TypeError, ValueError):
            pass  # malformed operator — keep current width

    def mark_clipping(self) -> None:
        """Called when W or W* is encountered."""
        self.is_clipping = True

    def consume_clipping(self) -> bool:
        """Called after a painting operator. Returns and clears the flag."""
        was = self.is_clipping
        self.is_clipping = False
        return was

    # ------------------------------------------------------------------
    # Text state mutation
    # ------------------------------------------------------------------

    def apply_text_op(
        self,
        op: str,
        operands: list,
        glyph_width_fn: Callable[[str, int], float] | None = None,
    ) -> None:
        """Dispatch a single text-state/text-showing operator.

        Args:
            op: The operator string (e.g. "Tm", "Td", "Tj", "TJ").
            operands: The operator's raw operand list, as yielded by
                pikepdf.parse_content_stream.
            glyph_width_fn: Required only for Tj/TJ/'/" (text-showing
                operators); ignored otherwise. Called as
                glyph_width_fn(font_name, char_code) -> width in
                thousandths of text space units (the same convention as
                a PDF /Widths entry), and used to advance the text matrix
                by each shown glyph. If omitted, text-showing operators
                update font/positioning state as a no-op for position
                (i.e. the text matrix is left unchanged) — callers that
                only need e.g. Tf tracking (not glyph positions) can
                skip supplying it.

        Unrecognized operators are ignored. Malformed operands degrade to
        a no-op for that operator rather than raising, consistent with
        the other setters on this class (e.g. set_line_width).
        """
        handler = _TEXT_OP_HANDLERS.get(op)
        if handler is None:
            return
        handler(self, operands, glyph_width_fn)

    def _bt(self, _operands, _glyph_width_fn) -> None:
        self.text_matrix = _IDENTITY_CTM
        self.text_line_matrix = _IDENTITY_CTM

    def _et(self, _operands, _glyph_width_fn) -> None:
        self.text_matrix = None
        self.text_line_matrix = None

    def _tm(self, operands, _glyph_width_fn) -> None:
        try:
            m = tuple(float(x) for x in operands)
        except (TypeError, ValueError):
            return  # malformed Tm — leave existing matrices untouched
        self.text_matrix = m
        self.text_line_matrix = m

    def _td_common(self, operands) -> tuple[float, float] | None:
        try:
            tx, ty = float(operands[0]), float(operands[1])
        except (IndexError, TypeError, ValueError):
            return None
        return tx, ty

    def _td(self, operands, _glyph_width_fn) -> None:
        offs = self._td_common(operands)
        if offs is None or self.text_line_matrix is None:
            return
        tx, ty = offs
        translation = (1.0, 0.0, 0.0, 1.0, tx, ty)
        new_m = _multiply_ctm(translation, self.text_line_matrix)
        self.text_matrix = new_m
        self.text_line_matrix = new_m

    def _td_leading(self, operands, glyph_width_fn) -> None:
        offs = self._td_common(operands)
        if offs is None:
            return
        # TD sets leading to -ty before applying the same offset as Td.
        self.leading = -offs[1]
        self._td(operands, glyph_width_fn)

    def _t_star(self, _operands, _glyph_width_fn) -> None:
        self._td([0.0, -self.leading], None)

    def _tf(self, operands, _glyph_width_fn) -> None:
        try:
            name = str(operands[0])
            size = float(operands[1])
        except (IndexError, TypeError, ValueError):
            return  # malformed Tf — keep previous font/size, untouched
        self.font_name = name
        self.font_size = size

    def _tc(self, operands, _glyph_width_fn) -> None:
        try:
            self.char_spacing = float(operands[0])
        except (IndexError, TypeError, ValueError):
            pass

    def _tw(self, operands, _glyph_width_fn) -> None:
        try:
            self.word_spacing = float(operands[0])
        except (IndexError, TypeError, ValueError):
            pass

    def _tz(self, operands, _glyph_width_fn) -> None:
        try:
            self.horizontal_scale = float(operands[0]) / 100.0
        except (IndexError, TypeError, ValueError):
            pass

    def _tl(self, operands, _glyph_width_fn) -> None:
        try:
            self.leading = float(operands[0])
        except (IndexError, TypeError, ValueError):
            pass

    def _ts(self, operands, _glyph_width_fn) -> None:
        try:
            self.text_rise = float(operands[0])
        except (IndexError, TypeError, ValueError):
            pass

    def _advance_by_width_1000(self, width_1000: float) -> None:
        """Advance the text matrix by a glyph/adjustment width expressed in
        thousandths of text space units, per spec §9.4.3's displacement
        formula (word spacing only applies to single-byte code 32 in
        simple fonts; callers targeting composite fonts should fold that
        into their glyph_width_fn rather than rely on this helper)."""
        if self.text_matrix is None:
            return
        tx = (width_1000 / 1000.0 * self.font_size) * self.horizontal_scale
        translation = (1.0, 0.0, 0.0, 1.0, tx, 0.0)
        self.text_matrix = _multiply_ctm(translation, self.text_matrix)

    def _show_text_string(self, s: bytes | str, glyph_width_fn) -> None:
        if glyph_width_fn is None or self.font_name is None:
            return
        raw = s if isinstance(s, (bytes, bytearray)) else str(s).encode("latin-1", "replace")
        for code in raw:
            w = glyph_width_fn(self.font_name, code)
            w += self.char_spacing / self.font_size * 1000.0 if self.font_size else 0.0
            if code == 32:
                w += self.word_spacing / self.font_size * 1000.0 if self.font_size else 0.0
            self._advance_by_width_1000(w)

    def _tj(self, operands, glyph_width_fn) -> None:
        if not operands:
            return
        self._show_text_string(operands[0], glyph_width_fn)

    def _tj_array(self, operands, glyph_width_fn) -> None:
        if not operands:
            return
        for elem in operands[0]:
            try:
                adj = float(elem)
            except (TypeError, ValueError):
                self._show_text_string(elem, glyph_width_fn)
            else:
                # TJ numeric adjustments are subtractive and already in
                # thousandths of text space units — no per-glyph spacing
                # applies to a pure kerning adjustment.
                self._advance_by_width_1000(-adj)

    def _quote(self, operands, glyph_width_fn) -> None:
        # ' is `T* string Tj` — move to next line, then show.
        self._t_star(None, None)
        self._tj(operands, glyph_width_fn)

    def _dquote(self, operands, glyph_width_fn) -> None:
        # " is `aw ac string aw Tw ac Tc T* string Tj`.
        try:
            self.word_spacing = float(operands[0])
            self.char_spacing = float(operands[1])
        except (IndexError, TypeError, ValueError):
            pass
        self._t_star(None, None)
        self._tj(operands[2:], glyph_width_fn)

    # ------------------------------------------------------------------
    # Derived properties
    # ------------------------------------------------------------------

    @property
    def scale(self) -> float:
        """Approximate uniform scale factor of the current CTM."""
        return ctm_scale(self.ctm)

    @property
    def text_render_matrix(self) -> tuple[float, ...] | None:
        """Device-space matrix mapping glyph-local space to page space:
        Trm = [Tfs*Th, 0, 0, Tfs, 0, Trise] x Tm x CTM (spec §9.4.4).

        Returns None outside a BT...ET block (i.e. text_matrix is None).
        The font-size/horizontal-scale/rise scaling component is folded
        in here so callers get a single matrix to transform a glyph's
        nominal (0..1000-unit, /1000-scaled) bbox straight to device
        space, without re-deriving the per-glyph parameters themselves.
        """
        if self.text_matrix is None:
            return None
        param_m = (
            self.font_size * self.horizontal_scale,
            0.0,
            0.0,
            self.font_size,
            0.0,
            self.text_rise,
        )
        return _multiply_ctm(_multiply_ctm(param_m, self.text_matrix), self.ctm)

    def user_space_tolerance(self, device_tol: float) -> float:
        """Convert a device-space tolerance to user space.

        Args:
            device_tol: Tolerance in device (output) space points.

        Returns:
            Tolerance in the current user space, clamped to [0.01, 100.0].
        """
        s = self.scale
        result = device_tol / s if s > 1e-9 else device_tol
        return max(0.01, min(result, 100.0))

    # ------------------------------------------------------------------
    # Stack helpers
    # ------------------------------------------------------------------

    def clone(self) -> GraphicsState:
        """Return a shallow copy suitable for pushing onto the save stack."""
        return copy(self)


# Dispatch table for GraphicsState.apply_text_op. Defined after the class
# body so it can reference the bound methods by name; kept as a plain
# module-level dict (rather than a classmethod-decorated structure) to
# avoid the per-instance closure allocation cost simplify_vectors_stream's
# tight loop already goes out of its way to avoid via GraphicsStateStack.
_TEXT_OP_HANDLERS: dict[str, Callable] = {
    "BT": GraphicsState._bt,
    "ET": GraphicsState._et,
    "Tm": GraphicsState._tm,
    "Td": GraphicsState._td,
    "TD": GraphicsState._td_leading,
    "T*": GraphicsState._t_star,
    "Tf": GraphicsState._tf,
    "Tc": GraphicsState._tc,
    "Tw": GraphicsState._tw,
    "Tz": GraphicsState._tz,
    "TL": GraphicsState._tl,
    "Ts": GraphicsState._ts,
    "Tj": GraphicsState._tj,
    "TJ": GraphicsState._tj_array,
    "'": GraphicsState._quote,
    '"': GraphicsState._dquote,
}


class GraphicsStateStack:
    """A bounded stack of GraphicsState objects (mirrors q/Q operators).

    The *current* state is always ``stack.current``.  ``push()`` saves it;
    ``pop()`` restores the previous state.  Stack depth is capped at
    ``_MAX_STACK_DEPTH`` per the PDF specification.
    """

    def __init__(self) -> None:
        self._stack: list[GraphicsState] = []
        self.current: GraphicsState = GraphicsState()

    def push(self) -> None:
        """Save the current state (q operator)."""
        if len(self._stack) >= _MAX_STACK_DEPTH:
            # Spec says this is an error; we log and silently ignore.
            import logging

            logging.getLogger(__name__).warning(
                "Graphics state stack depth exceeded %d; ignoring 'q'.", _MAX_STACK_DEPTH
            )
            return
        self._stack.append(self.current.clone())

    def pop(self) -> None:
        """Restore the previous state (Q operator)."""
        if not self._stack:
            import logging

            logging.getLogger(__name__).warning("Graphics state stack underflow; ignoring 'Q'.")
            return
        self.current = self._stack.pop()

    def __len__(self) -> int:
        return len(self._stack)
