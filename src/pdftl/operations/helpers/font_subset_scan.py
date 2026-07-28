# src/pdftl/operations/helpers/font_subset_scan.py

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

"""
Content-stream scanning helpers for subset_fonts: walks a set of pages'
content streams to determine which raw character codes are actually
painted under each font resource.

Kept separate from the subsetting mechanics themselves (pdftl.fonts.font_subsetting)
since this half only ever touches pikepdf content-stream operators, never
a font program's own bytes.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)


def _font_key(font_obj: Any, scope_key: Any = None) -> Any:
    """
    A stable identity key for a font resource dict entry.

    pikepdf returns a *new* Python wrapper object every time the same
    underlying indirect PDF object is accessed -- confirmed directly:
    `page["/Resources"]["/Font"]["/F1"] is page["/Resources"]["/Font"]["/F1"]`
    is False. Python's own `id()` therefore is *not* a stable identity
    for a pikepdf object across repeated lookups (e.g. once per `Tf`
    operator that re-selects the same font), and using it as a dict key
    silently fragments a single logical font into many spurious entries.
    `.objgen` (the PDF file's own `(object number, generation)` identity)
    is stable across accesses and is what must be used instead.

    A font dict entry is required by the PDF spec to be an indirect
    reference (ISO 32000-2 7.8.3), so `.objgen` is expected to always be
    available; the direct-object fallback to `id()` below only matters
    for a technically-nonconformant PDF -- but bare `id(font_obj)` is
    just as unstable for a DIRECT object as it was for an indirect one,
    since pikepdf still hands back a fresh wrapper on every dict access
    regardless of directness. `scope_key`, when given, is a
    caller-supplied identity that IS stable across repeated accesses
    within one scan (e.g. `(id(the_owning_dict), the_resource_name)`,
    valid for as long as that owning dict reference itself is held
    fixed by the caller) and is preferred over `id(font_obj)` whenever
    provided.
    """
    objgen = getattr(font_obj, "objgen", None)
    if objgen and objgen != (0, 0):
        return objgen
    if scope_key is not None:
        return scope_key
    return id(font_obj)


class _PageFontCodeScanner:
    """Stateful helper tracking the currently-selected font (Tf) while
    walking content streams and nested Form XObjects, recording which raw
    character codes are shown under each font."""

    def __init__(
        self,
        codes_by_font_id: dict[Any, set[int]],
        resolved: dict[Any, Any],
    ):
        self._codes_by_font_id = codes_by_font_id
        self._resolved = resolved
        self._resources: Any = None
        self._font_dict: Any = None
        self._xobject_dict: Any = None
        self._visited: set[Any] = set()
        self._current_font_obj: Any = None
        self._current_font_key: Any = None
        self._current_bytes_per_code = 1
        self._text_state_stack: list[tuple[Any, Any, int]] = []

    def push_state(self) -> None:
        self._text_state_stack.append(
            (self._current_font_obj, self._current_font_key, self._current_bytes_per_code)
        )

    def pop_state(self) -> None:
        # An unbalanced 'Q' in a malformed content stream shouldn't raise;
        # just leave the current text state as-is.
        if self._text_state_stack:
            (
                self._current_font_obj,
                self._current_font_key,
                self._current_bytes_per_code,
            ) = self._text_state_stack.pop()

    def set_font(self, name: pikepdf.Name) -> None:
        font_obj = self._font_dict.get(str(name))
        if font_obj is None:
            logger.debug("[SCANNER] Font '%s' NOT FOUND in active font dictionary", name)
            self._current_font_obj = None
            self._current_font_key = None
            return
        self._current_font_obj = font_obj
        self._current_font_key = _font_key(font_obj, scope_key=(id(self._font_dict), str(name)))
        subtype = str(font_obj.get("/Subtype", ""))
        self._current_bytes_per_code = 2 if subtype == "/Type0" else 1

    def record_string(self, data: bytes) -> None:
        if self._current_font_obj is None:
            logger.debug("[SCANNER] record_string called but no font is currently active")
            return
        font_key = self._current_font_key
        codes = self._codes_by_font_id[font_key]
        step = self._current_bytes_per_code
        for i in range(0, len(data) - step + 1, step):
            codes.add(int.from_bytes(data[i : i + step], "big"))
        self._resolved[font_key] = self._current_font_obj

    def handle_do(self, name: Any) -> None:
        if self._xobject_dict is None:
            return
        xobj = self._xobject_dict.get(str(name))
        if xobj is None:
            return
        subtype = str(xobj.get("/Subtype", ""))
        if subtype != "/Form":
            return
        obj_key = _font_key(xobj, scope_key=(id(self._xobject_dict), str(name)))
        if obj_key in self._visited:
            return
        self._visited.add(obj_key)
        form_resources = xobj.get("/Resources")
        # A Form XObject gets its own independent graphics-state stack,
        # initialized from the state at the point of invocation but not
        # sharing the caller's q/Q stack -- an unbalanced q or Q inside
        # the form must not corrupt (pop from, or leave stale pushes on)
        # the caller's own stack once control returns here.
        old_stack = self._text_state_stack
        self._text_state_stack = []
        try:
            _scan_stream(xobj, form_resources, self)
        finally:
            self._text_state_stack = old_stack


def _handle_tf_operator(scanner: _PageFontCodeScanner, operands: list, pikepdf_mod: Any) -> None:
    if operands:
        scanner.set_font(operands[0])


def _handle_tj_operator(scanner: _PageFontCodeScanner, operands: list, pikepdf_mod: Any) -> None:
    if operands:
        scanner.record_string(bytes(operands[0]))


def _handle_quote_operator(
    scanner: _PageFontCodeScanner, operands: list, pikepdf_mod: Any
) -> None:
    if operands:
        scanner.record_string(bytes(operands[0]))


def _handle_double_quote_operator(
    scanner: _PageFontCodeScanner, operands: list, pikepdf_mod: Any
) -> None:
    if len(operands) >= 3:
        scanner.record_string(bytes(operands[2]))


def _handle_TJ_operator(scanner: _PageFontCodeScanner, operands: list, pikepdf_mod: Any) -> None:
    if not operands:
        return
    for item in operands[0]:
        if isinstance(item, (pikepdf_mod.String, bytes)):
            scanner.record_string(bytes(item))


def _handle_do_operator(scanner: _PageFontCodeScanner, operands: list, pikepdf_mod: Any) -> None:
    if operands:
        scanner.handle_do(operands[0])


# Dispatch table keyed by PDF content-stream operator name -- keeps
# _apply_text_showing_operator itself a flat lookup-and-call rather than
# a long if/elif chain, since each operator's own arity/type quirks
# (Tj vs " vs TJ's array-of-mixed-items form) are real, unavoidable
# per-operator branching that's clearer isolated in its own handler.
def _handle_q_operator(scanner: _PageFontCodeScanner, operands: list, pikepdf_mod: Any) -> None:
    scanner.push_state()


def _handle_Q_operator(scanner: _PageFontCodeScanner, operands: list, pikepdf_mod: Any) -> None:
    scanner.pop_state()


_TEXT_SHOWING_OPERATOR_HANDLERS: dict[str, Any] = {
    "Tf": _handle_tf_operator,
    "Tj": _handle_tj_operator,
    "'": _handle_quote_operator,
    '"': _handle_double_quote_operator,
    "TJ": _handle_TJ_operator,
    "Do": _handle_do_operator,
    "q": _handle_q_operator,
    "Q": _handle_Q_operator,
}


def _apply_text_showing_operator(
    scanner: _PageFontCodeScanner, operator: str, operands: list, pikepdf_mod: Any
) -> None:
    handler = _TEXT_SHOWING_OPERATOR_HANDLERS.get(operator)
    if handler is not None:
        handler(scanner, operands, pikepdf_mod)


def _scan_stream(
    stream_obj: Any,
    resources: Any,
    scanner: _PageFontCodeScanner,
) -> None:
    import pikepdf

    old_resources = scanner._resources
    old_font_dict = scanner._font_dict
    old_xobject_dict = scanner._xobject_dict
    old_font_obj = scanner._current_font_obj
    old_font_key = scanner._current_font_key
    old_bytes_per_code = scanner._current_bytes_per_code

    if resources is not None:
        scanner._resources = resources
        scanner._font_dict = resources.get("/Font", old_font_dict)
        scanner._xobject_dict = resources.get("/XObject", old_xobject_dict)

    try:
        instructions = pikepdf.parse_content_stream(stream_obj)
        for operands, operator in instructions:
            # logger.debug("[AP STREAM OP] %s : %r", operator, operands)
            _apply_text_showing_operator(scanner, str(operator), list(operands), pikepdf)
    except (pikepdf.PdfError, ValueError, TypeError, AttributeError) as e:
        # A malformed content stream shouldn't abort scanning every other
        # stream or page; the fonts it references simply keep whatever
        # glyphs they already have.
        logger.debug("Failed to parse content stream: %s", e)
    finally:
        scanner._resources = old_resources
        scanner._font_dict = old_font_dict
        scanner._xobject_dict = old_xobject_dict
        scanner._current_font_obj = old_font_obj
        scanner._current_font_key = old_font_key
        scanner._current_bytes_per_code = old_bytes_per_code


def _iter_annotation_appearance_streams(annot: Any) -> list[Any]:
    """
    Returns every appearance-stream Form XObject an annotation can paint:
    /AP/N (normal), /AP/D (down), /AP/R (rollover). Each of /N, /D, /R can
    either be a stream directly, or a subdictionary keyed by appearance
    state (/AS) for stateful annotations like checkboxes/radio buttons --
    in the subdictionary case every state's stream is scanned, not just
    the currently-selected /AS, since any of them could be shown.
    """
    import pikepdf

    ap = annot.get("/AP")
    if ap is None:
        return []

    streams: list[Any] = []
    for key in ("/N", "/D", "/R"):
        entry = ap.get(key)
        if entry is None:
            continue
        logger.debug(
            "[SCANNER] Annot AP '%s' -> type: %s (is Stream: %s, is Dict: %s)",
            key,
            type(entry),
            isinstance(entry, pikepdf.Stream),
            isinstance(entry, pikepdf.Dictionary),
        )
        # A pikepdf.Stream ALSO implements the dict protocol (it has its
        # own .items()/.get()), so type must be checked before dict-ness:
        # otherwise a Form XObject missing an explicit /Subtype falls
        # into the state-subdictionary branch below and its own stream
        # dict entries (/Length, /BBox, /Resources, ...) get iterated as
        # if they were per-state sub-streams.
        if isinstance(entry, pikepdf.Stream):
            streams.append(entry)
        elif isinstance(entry, pikepdf.Dictionary):
            # Appearance-state subdictionary (e.g. /On, /Off streams).
            for _state, sub_entry in entry.items():
                logger.debug(
                    "[SCANNER] Annot AP sub-state '%s' -> type: %s (is Stream: %s)",
                    _state,
                    type(sub_entry),
                    isinstance(sub_entry, pikepdf.Stream),
                )
                if isinstance(sub_entry, pikepdf.Stream):
                    streams.append(sub_entry)
    logger.debug("[SCANNER] Total AP streams collected for annot: %d", len(streams))
    return streams


def _scan_page_annotations(
    page: Any, codes_by_font_id: dict[Any, set[int]], resolved: dict[Any, Any]
) -> None:
    """Scans every annotation's appearance stream(s) on a page, since a
    viewer paints these directly rather than via a `Do` operator in the
    page's own content stream -- so they're otherwise invisible to
    collect_used_codes and any font used only here gets incorrectly
    gutted by subset_fonts."""
    annots = page.get("/Annots")
    if annots is None:
        return
    logger.debug("[SCANNER] Found %d annotations on page", len(annots))
    page_resources = page.get("/Resources")
    for annot in annots:
        for stream_obj in _iter_annotation_appearance_streams(annot):
            scanner = _PageFontCodeScanner(codes_by_font_id, resolved)
            # Fall back to page resources if stream has no /Resources or missing /Font
            ap_resources = stream_obj.get("/Resources")
            if ap_resources is None or "/Font" not in ap_resources:
                ap_resources = page_resources
            logger.debug(
                "[SCANNER] Scanning AP stream %s (Resources: %s)", stream_obj, ap_resources
            )
            _scan_stream(stream_obj, ap_resources, scanner)


def _scan_page(page: Any, codes_by_font_id: dict[Any, set[int]], resolved: dict[Any, Any]) -> None:
    resources = page.get("/Resources")
    scanner = _PageFontCodeScanner(codes_by_font_id, resolved)
    _scan_stream(page, resources, scanner)
    _scan_page_annotations(page, codes_by_font_id, resolved)


def collect_used_codes(pages: list) -> tuple[dict[Any, set[int]], dict[Any, Any]]:
    """
    Scans the given pages' content streams for text-showing operators and
    returns:
      - {font identity: {used raw character codes}}
      - {font identity: font_obj}, so callers can resolve the
        identity-keyed codes map back to real font objects.

    "font identity" is each font resource's stable `.objgen` (see
    _font_key), not `id(font_obj)` -- necessary because pikepdf hands
    back a fresh Python wrapper object on every dict access, even for the
    same underlying indirect object.
    """
    codes_by_font_id: dict[Any, set[int]] = defaultdict(set)
    resolved: dict[Any, Any] = {}
    for page in pages:
        _scan_page(page, codes_by_font_id, resolved)
    return codes_by_font_id, resolved
