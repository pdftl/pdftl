# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
# src/pdftl/operations/helpers/excise_stream.py
"""Per-content-stream walk/deletion engine shared by excise (and future
redact): resource resolution, the segment()-driven graphics-state walk,
Form XObject recursion, and image deletion.
"""

from __future__ import annotations

import logging
from typing import Any

from pdftl.utils.geometry import transform_rect_bbox
from pdftl.utils.graphics_state import GraphicsStateStack, multiply_matrices
from pdftl.utils.path_segmentation import segment
from pdftl.utils.path_types import Path, SimplifyConfig
from pdftl.operations.helpers.excise_types import ExciseRect, ExciseStats
from pdftl.operations.helpers.excise_geometry import (
    IDENTITY_CTM,
    filter_path,
    overlap_means_delete,
)
from pdftl.operations.helpers.excise_text import FontCache, rewrite_text_show

logger = logging.getLogger(__name__)

# excise's own SimplifyConfig for driving segment(): coalesce_strokes MUST
# stay False here (locked decision, see roadmap) -- the shattered-stroke
# merge feature changes what "one atomic subpath" means in a way excise must
# not inherit, since it changes deletion granularity underneath us.
TRIM_SEGMENT_CONFIG = SimplifyConfig(coalesce_strokes=False)

# Text-state operators that mutate GraphicsState's text fields but don't
# themselves paint anything -- tracked via GraphicsState.apply_text_op so
# subsequent Tj/TJ/'/" calls see correct font/position/spacing state.
TEXT_STATE_OPS = frozenset(
    {"BT", "ET", "Tm", "Td", "TD", "T*", "Tf", "Tc", "Tw", "Tz", "TL", "Ts"}
)

# Text-SHOWING operators -- these are where glyph-level deletion happens.
TEXT_SHOW_OPS = frozenset({"Tj", "TJ", "'", '"'})

_MAX_FORM_RECURSION_DEPTH = 12


def _copy_resource_dict(d: Any) -> Any:
    """Copies a /Resources dict AND each of its category sub-dicts
    (/XObject, /Font, /Pattern, /ExtGState, ...) one level deep.

    A shallow top-level-only copy still shares each category's nested
    dict BY REFERENCE with whatever it was copied from. Since callers
    later mutate a category in place (e.g. handle_form_do adding a new
    "_excise_..." key into resources["/XObject"]), a shallow copy lets
    that mutation leak sideways into every other Resources dict that
    happens to share the same nested category object -- which is
    exactly how two sibling Form XObjects (and the page) ended up with
    self-referential /XObject aliasing pointing at each other.
    """
    import pikepdf

    copied = pikepdf.Dictionary({})
    for key, val in d.items():
        # NOTE: isinstance check, not hasattr(val, "items") -- pikepdf's
        # Object wrapper exposes an .items attribute reflectively even on
        # QPDF object types that aren't actually dictionary-shaped, so
        # hasattr() alone doesn't guarantee val.items() is callable. Only
        # pikepdf.Dictionary (and pikepdf.Stream, a subclass) genuinely
        # support it.
        if isinstance(val, pikepdf.Dictionary):
            sub = pikepdf.Dictionary({})
            for k2, v2 in val.items():
                sub[k2] = v2
            copied[key] = sub
        else:
            copied[key] = val
    return copied


def resolve_resources(stream_obj: Any, page_resources: Any) -> Any:
    """Resolves the resource dictionary to use for a content stream.

    Merges the stream's own /Resources with the page's /Resources
    CATEGORY-BY-CATEGORY (e.g. /XObject, /Font, /Pattern, /ExtGState are
    each considered independently), rather than all-or-nothing. This
    matters because a Form XObject can legally carry a /Resources dict
    that only declares e.g. /Font, while still relying on an /XObject
    (image) it never redeclares -- an all-or-nothing "own wins if
    non-empty" fallback would silently hide that page-level /XObject
    entry from resolution, which is exactly the bug this replaced (an
    image referenced from inside such a Form was never seen as
    deletable). Own's entry for a given category always wins over the
    page's when both declare that category.

    KNOWN GAP: still a one-level fallback (stream -> page), not the
    fully general nested inheritance chain the spec technically allows
    (a Form XObject with no /Resources inherits from whatever resource
    dict was in scope at the point it was invoked, which could itself be
    another Form XObject's resources, not necessarily the page's). Since
    walk_content_streams doesn't currently thread a full parent-resources
    chain through StreamContext (only page_num/depth/kind), reconstructing
    that generally would require walk_content_streams itself to carry an
    inherited-resources chain. Deferred; page-level fallback covers the
    overwhelmingly common real-world case.

    Callers always pass a non-None page_resources -- since
    _unshare_page_resources guarantees page.Resources is at least an
    empty pikepdf.Dictionary, never None, by the time any content stream
    is processed. The page_resources-is-None case this function used to
    special-case is therefore unreachable from any current call site and
    has been removed rather than kept as untested dead code.
    """

    own = stream_obj.get("/Resources") if hasattr(stream_obj, "get") else None
    if own is None or len(own) == 0:
        # IMPORTANT: never return page_resources by reference here, and
        # never share its nested category dicts either -- see
        # _copy_resource_dict's docstring for why a shallow copy isn't
        # enough.
        return _copy_resource_dict(page_resources)
    merged = _copy_resource_dict(page_resources)
    for key, val in _copy_resource_dict(own).items():
        merged[key] = val
    return merged


def process_stream(
    pdf, stream_obj: Any, excise_rect: ExciseRect, stats: ExciseStats, page_resources: Any
) -> None:
    import pikepdf

    try:
        instructions = pikepdf.parse_content_stream(stream_obj)
    except pikepdf.PdfError as exc:
        logger.warning("excise: failed to parse content stream %s: %s", stream_obj.objgen, exc)
        return

    stats.streams_processed += 1

    own = stream_obj.get("/Resources") if hasattr(stream_obj, "get") else None
    if own is None or len(own) == 0:
        # Common case: a page content stream has no /Resources of its own
        # (per spec, only the page dictionary and Form XObjects legitimately
        # carry /Resources). page_resources here is ALREADY page.Resources
        # itself, made private/unshared by _unshare_page_resources before
        # this is ever called for a page's own content stream -- so using
        # it directly (by reference, not a copy) means any later mutation
        # (e.g. handle_form_do adding a new "_excise_..." key) lands
        # exactly where a real PDF renderer looks for it: the page's own
        # /Resources. Do NOT write this onto stream_obj["/Resources"] --
        # that's not a location a page-content-stream reader consults per
        # spec, and doing so was the root cause of a bug where a rewritten
        # Form XObject's Do operand pointed at a key that only existed on
        # the content-stream object, silently rendering as nothing in
        # real-world viewers (confirmed via poppler/evince).
        resources = page_resources
    else:
        # Non-standard but tolerated input: the stream object itself
        # already carries its own /Resources. Preserve the previous
        # merge-and-write-back-onto-stream_obj behavior for this edge
        # case. resolve_resources is guaranteed non-None here: own is
        # non-empty (this branch), and page_resources is guaranteed
        # non-None by _unshare_page_resources -- so the merge always
        # succeeds and the write-back is unconditional, not guarded.
        # non-None by _unshare_page_resources -- so the merge always
        # succeeds. stream_obj is always a pikepdf.Stream/Dictionary in
        # every live call site and always supports __setitem__, so the
        # write-back is unconditional -- no defensive hasattr guard.
        resources = resolve_resources(stream_obj, page_resources)
        stream_obj["/Resources"] = resources

    mixed = segment(instructions, TRIM_SEGMENT_CONFIG, track_instructions=True)
    font_cache = FontCache(resources)
    new_instructions = interpret_and_filter(pdf, mixed, resources, font_cache, excise_rect, stats)

    try:
        stream_obj.write(pikepdf.unparse_content_stream(new_instructions))
    except pikepdf.PdfError as exc:
        logger.warning("excise: failed to write filtered stream: %s", exc)


def _apply_graphics_op(op_str: str, operands: list[Any], gs_stack: GraphicsStateStack) -> None:
    """Handles the three graphics-state-stack operators (q/Q/cm) that
    interpret_and_filter needs to track but never deletes/rewrites."""
    if op_str == "q":
        gs_stack.push()
    elif op_str == "Q":
        gs_stack.pop()
    elif op_str == "cm":
        gs_stack.current.apply_cm([float(x) for x in operands])


def _handle_do(
    pdf: Any,
    operands: list[Any],
    resources: Any,
    ctm: tuple[float, ...],
    excise_rect: ExciseRect,
    stats: ExciseStats,
    depth: int,
) -> list[Any] | None:
    """Resolves one `Do` instruction's fate. Returns the instructions that
    should replace it (possibly empty, meaning dropped), or None to signal
    the caller should append the original (operands, operator) unchanged."""
    form_result = handle_form_do(pdf, operands, resources, ctm, excise_rect, stats, depth)
    if form_result is not None:
        return form_result
    if should_delete_image(operands, resources, ctm, excise_rect, stats):
        return []  # drop this instruction -- image deleted
    return None  # keep as-is


def interpret_and_filter(
    pdf: Any,
    mixed: list[Path | Any],
    resources: Any,
    font_cache: FontCache,
    excise_rect: ExciseRect,
    stats: ExciseStats,
    initial_ctm: tuple[float, ...] = IDENTITY_CTM,
    depth: int = 0,
) -> list[Any]:
    """Walks a segment()-produced mixed list with a GraphicsStateStack,
    dropping/rebuilding atomic units whose device-space bbox overlap with
    excise_rect.rect matches the deletion direction implied by excise_rect.keep.
    See excise.py's module docstring for the full pipeline description.
    """
    gs_stack = GraphicsStateStack()
    gs_stack.current.ctm = initial_ctm
    out: list[Any] = []

    for item in mixed:
        if isinstance(item, Path):
            out.extend(filter_path(item, excise_rect, stats, initial_ctm))
            continue

        operands, operator = item
        op_str = str(operator)

        if op_str in ("q", "Q", "cm"):
            _apply_graphics_op(op_str, operands, gs_stack)
        elif op_str == "Do":
            result = _handle_do(
                pdf, operands, resources, gs_stack.current.ctm, excise_rect, stats, depth
            )
            if result is not None:
                out.extend(result)
                continue
        elif op_str in TEXT_STATE_OPS:
            gs_stack.current.apply_text_op(op_str, operands)
        elif op_str in TEXT_SHOW_OPS:
            out.extend(
                rewrite_text_show(
                    op_str, operands, gs_stack.current, font_cache, excise_rect, stats
                )
            )
            continue

        out.append((operands, operator))

    return out


def handle_form_do(
    pdf: Any,
    operands: list[Any],
    resources: Any,
    ctm: tuple[float, ...],
    excise_rect: ExciseRect,
    stats: ExciseStats,
    depth: int,
) -> list[Any] | None:
    """Called for every `Do` operator to check whether it invokes a Form
    XObject (as opposed to an Image, handled by should_delete_image).
    See excise.py's original docstring (preserved in git history) for the
    full rationale on recursion, private-copy semantics, and CTM
    composition.
    """
    import pikepdf

    if not operands or resources is None or "/XObject" not in resources:
        return None
    xobj_name = str(operands[0])
    xobjects = resources["/XObject"]
    if xobj_name not in xobjects:
        return None
    xobj = xobjects[xobj_name]
    if xobj.get("/Subtype") != "/Form":
        return None  # not a Form -- let the caller handle it as an image Do

    if depth > _MAX_FORM_RECURSION_DEPTH:
        return [(operands, "Do")]

    try:
        form_matrix = tuple(float(x) for x in xobj.get("/Matrix", [1, 0, 0, 1, 0, 0]))
    except (TypeError, ValueError):
        form_matrix = IDENTITY_CTM
    effective_ctm = multiply_matrices(form_matrix, ctm)

    try:
        form_instructions = pikepdf.parse_content_stream(xobj)
    except pikepdf.PdfError:
        return [(operands, "Do")]  # unparseable Form content -- pass through untouched

    form_resources = resolve_resources(xobj, resources)
    xobj["/Resources"] = form_resources
    mixed = segment(form_instructions, TRIM_SEGMENT_CONFIG, track_instructions=True)
    font_cache = FontCache(form_resources)
    filtered = interpret_and_filter(
        pdf,
        mixed,
        form_resources,
        font_cache,
        excise_rect,
        stats,
        initial_ctm=effective_ctm,
        depth=depth + 1,
    )

    import zlib

    new_bytes = pikepdf.unparse_content_stream(filtered)
    if new_bytes == pikepdf.unparse_content_stream(list(form_instructions)):
        return [(operands, "Do")]  # nothing deleted inside -- keep the shared Form untouched

    compressed = zlib.compress(new_bytes, level=9)
    new_form = pdf.make_stream(compressed)
    for key, val in xobj.items():
        if key in ("/Length", "/Filter", "/DecodeParms"):
            continue
        new_form[key] = val
    new_form.Filter = pikepdf.Name("/FlateDecode")

    new_key = f"{xobj_name}_excise_{id(new_form)}"
    resources["/XObject"][new_key] = new_form
    return [([pikepdf.Name(new_key)], "Do")]


def should_delete_image(
    operands: list[Any],
    resources: Any,
    ctm: tuple[float, ...],
    excise_rect: ExciseRect,
    stats: ExciseStats,
) -> bool:
    """Resolves a `Do` operator's XObject name, confirms it's an Image,
    and tests its device-space bbox against excise_rect. Whole-XObject
    removal-on-any-overlap, never partial pixel-region blanking."""
    if not operands or resources is None or "/XObject" not in resources:
        return False

    xobj_name = str(operands[0])
    xobjects = resources["/XObject"]
    if xobj_name not in xobjects:
        return False

    xobj = xobjects[xobj_name]
    if xobj.get("/Subtype") != "/Image":
        return False  # Form XObjects are walked separately, not deleted here

    stats.images_total += 1

    bbox = transform_rect_bbox([0.0, 0.0, 1.0, 1.0], ctm)
    should_delete = overlap_means_delete(bbox, excise_rect)
    if should_delete:
        stats.images_deleted += 1
    return should_delete
