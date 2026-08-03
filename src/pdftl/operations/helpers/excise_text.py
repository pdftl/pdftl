# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
# src/pdftl/operations/helpers/excise_text.py
"""Glyph-level text deletion helpers for excise."""

from __future__ import annotations

from typing import Any

from pdftl.utils.geometry import transform_rect_bbox
from pdftl.operations.helpers.excise_types import ExciseRect, ExciseStats


class FontCache:
    """Per-stream cache resolving a text-showing operator's current font
    resource name to widths/vertical-metrics/composite-ness, built lazily
    and reused across every glyph shown in the stream -- avoids re-parsing
    a font's /Widths or /W2 table on every single glyph (same lesson
    fonts/widths_utils.VerticalMetricsLookup was built to fix for /W2)."""

    def __init__(self, resources: Any) -> None:
        self._resources = resources
        self._font_objs: dict[str, Any] = {}
        self._widths: dict[str, dict[str, float]] = {}
        self._default_widths: dict[str, float] = {}
        self._composite: dict[str, bool] = {}
        self._vertical: dict[str, bool] = {}
        self._vlookup: dict[str, Any] = {}

    def _font_obj(self, font_name: str) -> Any | None:
        if font_name in self._font_objs:
            return self._font_objs[font_name]
        font_obj = None
        if self._resources is not None and "/Font" in self._resources:
            fonts = self._resources["/Font"]
            if font_name in fonts:
                font_obj = fonts[font_name]
        self._font_objs[font_name] = font_obj
        return font_obj

    def is_composite(self, font_name: str) -> bool:
        if font_name not in self._composite:
            font_obj = self._font_obj(font_name)
            self._composite[font_name] = bool(
                font_obj is not None and str(font_obj.get("/Subtype", "")) == "/Type0"
            )
        return self._composite[font_name]

    def is_vertical(self, font_name: str) -> bool:
        if font_name not in self._vertical:
            from pdftl.fonts.widths_utils import is_vertical_writing_mode

            font_obj = self._font_obj(font_name)
            self._vertical[font_name] = bool(
                font_obj is not None and is_vertical_writing_mode(font_obj)
            )
        return self._vertical[font_name]

    def default_width(self, font_name: str) -> float:
        """The width to use for any code/CID this font's table doesn't
        cover -- see widths_utils.extract_default_width."""
        if font_name not in self._default_widths:
            from pdftl.fonts.widths_utils import extract_default_width

            font_obj = self._font_obj(font_name)
            self._default_widths[font_name] = (
                extract_default_width(font_obj) if font_obj is not None else 0.0
            )
        return self._default_widths[font_name]

    def glyph_width(self, font_name: str, code: int) -> float:
        if font_name not in self._widths:
            from pdftl.fonts.widths_utils import extract_font_widths

            font_obj = self._font_obj(font_name)
            self._widths[font_name] = extract_font_widths(font_obj) if font_obj is not None else {}
        cid_hex = f"{code:04X}" if self.is_composite(font_name) else f"{code:02X}"
        return self._widths[font_name].get(cid_hex, self.default_width(font_name))

    def vertical_metrics(self, font_name: str, code: int) -> tuple[float, float, float]:
        """Returns (w1y, vx, vy) for one CID -- see
        fonts.widths_utils.VerticalMetricsLookup.get for the exact
        spec-default derivation when no /W2 override exists."""
        if font_name not in self._vlookup:
            from pdftl.fonts.widths_utils import build_vertical_metrics_lookup

            font_obj = self._font_obj(font_name)
            self._vlookup[font_name] = (
                build_vertical_metrics_lookup(font_obj) if font_obj is not None else None
            )
        lookup = self._vlookup[font_name]
        if lookup is None:
            return (-1000.0, 0.0, 880.0)  # spec's own /DW2 default, no /W to derive vx from
        return lookup.get(f"{code:04X}")


def rewrite_text_show(
    op_str: str,
    operands: list[Any],
    gs: Any,  # GraphicsState
    font_cache: FontCache,
    excise_rect: ExciseRect,
    stats: ExciseStats,
) -> list[Any]:
    """Handles one text-showing operator (Tj/TJ/'/"), testing each decoded
    glyph's device-space position against excise_rect and rebuilding the
    operator as a TJ array with advance-preserving numeric placeholders
    for any deleted glyphs. Runs with no deletions are re-emitted as a
    plain Tj, per the locked spec (see roadmap): "runs with no deletions
    stay plain Tj".

    ' and " are first reduced to their spec-equivalent state ops (T*, and
    Tw/Tc for ") plus a plain string operand, so only one show-handling
    path is needed -- the equivalent state ops are explicitly emitted
    ahead of the (possibly rewritten) show instruction, keeping the
    content stream semantically identical either way.
    """
    prefix: list[Any] = []
    show_operands = operands

    if op_str == '"':
        try:
            aw, ac = float(operands[0]), float(operands[1])
            gs.word_spacing = aw
            gs.char_spacing = ac
            prefix.append(([aw], "Tw"))
            prefix.append(([ac], "Tc"))
        except (IndexError, TypeError, ValueError):
            pass
        show_operands = operands[2:]

    if op_str in ("'", '"'):
        gs.apply_text_op("T*", [])
        prefix.append(([], "T*"))

    if gs.font_name is None or not show_operands:
        fallback_op = "TJ" if op_str == "TJ" else "Tj"
        return prefix + [(show_operands, fallback_op)]

    elements = list(show_operands[0]) if op_str == "TJ" else [show_operands[0]]
    rebuilt, any_deleted = filter_show_elements(elements, gs, font_cache, excise_rect, stats)

    if not any_deleted:
        fallback_op = "TJ" if op_str == "TJ" else "Tj"
        return prefix + [(show_operands, fallback_op)]

    import pikepdf

    return prefix + [([pikepdf.Array(rebuilt)], "TJ")]


def _as_adjustment(el: Any) -> float | None:
    """Returns el as a float TJ kerning adjustment, or None if el isn't
    numeric (i.e. it's a string element instead)."""
    try:
        return float(el)
    except (TypeError, ValueError):
        return None


def _glyph_advance_and_test(
    code: int,
    gs: Any,
    font_cache: FontCache,
    font_name: str,
    is_composite: bool,
    is_vertical: bool,
    excise_rect: ExciseRect,
) -> tuple[bool, float]:
    """Computes one glyph's render matrix + 1000-unit advance, and tests it
    against excise_rect. Returns (should_delete, advance_1000)."""
    if is_vertical:
        w1y, vx, vy = font_cache.vertical_metrics(font_name, code)
        trm = gs.vertical_render_matrix(vx, vy)
        should_delete = glyph_should_delete(trm, w1y, True, excise_rect)
        return should_delete, w1y
    else:
        trm = gs.text_render_matrix
        raw_w0 = font_cache.glyph_width(font_name, code)
        w0 = raw_w0
        if gs.font_size:
            w0 += gs.char_spacing / gs.font_size * 1000.0
            if code == 32 and not is_composite:
                w0 += gs.word_spacing / gs.font_size * 1000.0
        should_delete = glyph_should_delete(trm, raw_w0, False, excise_rect)
        return should_delete, w0


def _process_show_string(
    raw: bytes,
    gs: Any,
    font_cache: FontCache,
    font_name: str,
    is_composite: bool,
    is_vertical: bool,
    excise_rect: ExciseRect,
    stats: ExciseStats,
    rebuilt: list[Any],
    kept_bytes: bytearray,
) -> tuple[bytearray, bool]:
    """Decodes and tests every glyph in one string element, mutating
    `rebuilt`/advancing `gs` in place. Returns (new kept_bytes, any_deleted)."""
    codes = gs.decode_text_codes(raw, is_composite)
    step = 2 if is_composite else 1
    stats.glyphs_total += len(codes)
    any_deleted = False

    for i, code in enumerate(codes):
        should_delete, advance_1000 = _glyph_advance_and_test(
            code, gs, font_cache, font_name, is_composite, is_vertical, excise_rect
        )
        if should_delete:
            any_deleted = True
            stats.glyphs_deleted += 1
            if kept_bytes:
                rebuilt.append(_pikepdf_string(bytes(kept_bytes)))
                kept_bytes = bytearray()
            rebuilt.append(-advance_1000)
        else:
            kept_bytes.extend(raw[i * step : i * step + step])

        if is_vertical:
            gs.advance_vertical_by_1000(advance_1000)
        else:
            gs.advance_horizontal_by_1000(advance_1000)

    return kept_bytes, any_deleted


def _pikepdf_string(data: bytes) -> Any:
    import pikepdf

    return pikepdf.String(data)


def filter_show_elements(
    elements: list[Any],
    gs: Any,
    font_cache: FontCache,
    excise_rect: ExciseRect,
    stats: ExciseStats,
) -> tuple[list[Any], bool]:
    """Decodes every glyph across a Tj/TJ operand's elements, testing and
    advancing one at a time, and returns (new_TJ_elements, any_deleted)."""

    font_name = gs.font_name
    is_composite = font_cache.is_composite(font_name)
    is_vertical = font_cache.is_vertical(font_name)

    rebuilt: list[Any] = []
    kept_bytes = bytearray()
    any_deleted = False

    def _flush_kept() -> None:
        nonlocal kept_bytes
        if kept_bytes:
            rebuilt.append(_pikepdf_string(bytes(kept_bytes)))
            kept_bytes = bytearray()

    for el in elements:
        adj = _as_adjustment(el)
        if adj is not None:
            gs.advance_horizontal_by_1000(-adj)
            _flush_kept()
            rebuilt.append(adj)
            continue

        raw = el if isinstance(el, (bytes, bytearray)) else str(el).encode("latin-1", "replace")
        kept_bytes, deleted_here = _process_show_string(
            raw,
            gs,
            font_cache,
            font_name,
            is_composite,
            is_vertical,
            excise_rect,
            stats,
            rebuilt,
            kept_bytes,
        )
        any_deleted = any_deleted or deleted_here

    _flush_kept()
    return rebuilt, any_deleted


def glyph_should_delete(
    trm: tuple[float, ...] | None,
    advance_1000: float,
    is_vertical: bool,
    excise_rect: ExciseRect,
) -> bool:
    """Tests a glyph's nominal box against excise_rect, sized along its
    advance axis to the glyph's ACTUAL advance width (advance_1000/1000)
    rather than a fixed full em. A fixed 1-em box overstates the true
    footprint of any narrow glyph (e.g. 'i', '.', ',', a space) or any
    tightly-kerned pair, letting that oversized box reach into a nearby
    redaction rectangle -- and get deleted -- even though the glyph's
    real ink and advance never touched it. This under- or over-shoots on
    whichever side of the box the glyph sits, which is why over-deletion
    was showing up on both the left and right of the visible black box.

    Uses CENTER-POINT containment, not any-overlap, to decide the glyph's
    fate -- unlike overlap_means_delete's ANY-overlap policy used for
    paths/images. A glyph's box here is built from its font's nominal
    /advance width, while the drawn/deletion rect comes from pdfium's
    INK-based per-line bbox (see redact.py/grep) -- two different
    measurements of "where the glyph is" that routinely disagree by a
    point or so right at a boundary, especially with side bearings.
    Any-overlap treats that sub-pixel disagreement as a full match,
    silently deleting a glyph adjacent to (but visually outside) the
    drawn box -- e.g. redacting "ather" out of "gathering" also ate the
    "a", even though the drawn box never covered it. Center-point
    containment tolerates that boundary noise: a glyph only gets deleted
    when its own midpoint genuinely falls inside (or outside, for
    delete="outside") the target region, matching what a human looking
    at the drawn box would expect.

    advance_1000 is expected to already exclude char/word spacing (pass
    the raw glyph width, not the spacing-augmented pen advance) -- the
    bbox represents the glyph's own footprint, not the pen movement.
    """
    if trm is None:
        return False
    # Clamp away from exactly 0 so a genuinely zero-width glyph (e.g. a
    # combining mark) still tests against a thin sliver at its origin
    # rather than a degenerate empty box that can never overlap anything.
    advance = advance_1000 / 1000.0
    if advance >= 0:
        advance = max(advance, 0.01)
    else:
        advance = min(advance, -0.01)
    if is_vertical:
        bbox_local = [0.0, -advance, 1.0, 0.0] if advance >= 0 else [0.0, 0.0, 1.0, -advance]
    else:
        bbox_local = [0.0, 0.0, advance, 1.0] if advance >= 0 else [advance, 0.0, 0.0, 1.0]
    bbox = transform_rect_bbox(bbox_local, trm)
    return _center_means_delete(bbox, excise_rect)


def _center_means_delete(bbox: list[float], excise_rect: ExciseRect) -> bool:
    """Center-point variant of excise_geometry.overlap_means_delete, used
    ONLY for glyph deletion (see glyph_should_delete's docstring for why
    any-overlap is the wrong test there). Applies the same delete
    inside/outside direction as overlap_means_delete, but the underlying
    "is this unit inside the region" test is midpoint containment against
    the region's rects union, not overlap -- `partial` has no meaning
    here (a point either is or isn't inside a rect) and is ignored."""
    cx = (bbox[0] + bbox[2]) / 2.0
    cy = (bbox[1] + bbox[3]) / 2.0
    is_inside = any(r[0] <= cx <= r[2] and r[1] <= cy <= r[3] for r in excise_rect.rects)
    return is_inside if excise_rect.delete == "inside" else not is_inside
