# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/excise.py

"""Remove (redact-adjacent) page content that overlaps a rectangle.

`excise` deletes whole atomic content units -- images, fill paths, stroke
subpaths, and individual glyphs -- that overlap a per-page
rectangle, rather than merely cropping the page's visible boundary (that's
`rebox`). This is the geometry/deletion engine `redact` will be built on
top of; `excise` itself does no text-search, metadata, or OCR sweeping.

Pipeline per page:
    1. coalesce page contents (pikepdf.Page.contents_coalesce())
    2. walk_content_streams() -> every content stream reachable from this
       page (page /Contents, Form XObjects, tiling Patterns, ExtGState
       /SMask groups, Annotation /AP streams)
    3. for each stream: parse -> segment(coalesce_strokes=False,
       track_instructions=True) -> GraphicsStateStack-driven walk,
       testing each atomic unit's device-space bbox against the page's
       rect and deleting (or keeping, depending on `keep=`) on overlap
    4. serialize the surviving instructions back into the stream

Status: image, path (fill/stroke), and text (glyph-level, including
vertical writing mode) deletion are all implemented -- see
_rewrite_text_show / _FontCache for the text piece.
"""

from __future__ import annotations

import logging
from typing import Any

import pdftl.core.constants as c
from pdftl.exceptions import InvalidArgumentError
from pdftl.core.registry import register_operation
from pdftl.core.core_types import OpResult
from pdftl.utils.dimensions import dim_str_to_pts
from pdftl.utils.page_specs import page_numbers_matching_page_spec
from pdftl.utils.pdf_resources import walk_content_streams
from pdftl.utils.path_types import SimplifyConfig
from pdftl.operations.helpers.excise_types import ExciseRect, ExciseStats
from pdftl.operations.helpers.excise_geometry import (
    overlap_means_delete as _overlap_means_delete,
)
from pdftl.operations.helpers.excise_stream import (
    _copy_resource_dict as _copy_resource_dict,
)
from pdftl.operations.helpers.excise_stream import (
    process_stream as _process_stream,
)

logger = logging.getLogger(__name__)

# excise's own SimplifyConfig for driving segment(): coalesce_strokes MUST
# stay False here (locked decision, see roadmap) -- the shattered-stroke
# merge feature changes what "one atomic subpath" means in a way excise must
# not inherit, since it changes deletion granularity underneath us.
_TRIM_SEGMENT_CONFIG = SimplifyConfig(coalesce_strokes=False)

# Text-state operators that mutate GraphicsState's text fields but don't
# themselves paint anything -- tracked via GraphicsState.apply_text_op so
# subsequent Tj/TJ/'/" calls see correct font/position/spacing state.
_TEXT_STATE_OPS = frozenset(
    {"BT", "ET", "Tm", "Td", "TD", "T*", "Tf", "Tc", "Tw", "Tz", "TL", "Ts"}
)

# Text-SHOWING operators -- these are where glyph-level deletion happens.
_TEXT_SHOW_OPS = frozenset({"Tj", "TJ", "'", '"'})


# ---------------------------------------------------------------------------
# Operation registration
# ---------------------------------------------------------------------------


_EXAMPLES = [
    {
        "cmd": "in.pdf excise 1-5(abs,10pt,10pt,200pt,100pt) output out.pdf",
        "desc": "Delete content overlapping the box on pages 1-5",
    },
    {
        "cmd": "in.pdf excise 1(abs,0,0,300pt,300pt,delete=outside) output out.pdf",
        "desc": "Keep ONLY content inside the box on page 1, delete everything outside",
    },
]

_LONG_DESC = """
The `excise` operation deletes atomic page-content units (images, vector
paths, and eventually individual text glyphs) that overlap a per-page
rectangle.

Unlike `rebox`, which changes the page's visible boundary without
touching content, `excise` mutates the content stream itself: overlapping
units are physically removed from the PDF.

## Specification format for `<spec>`

    <pages>(abs,<x0>,<y0>,<x1>,<y1>[,delete=inside|outside][,partial=inside|outside])

- `<pages>` follows the same page-spec syntax as other operations
  (e.g. `1-5`, `1,3,5-end`).
- `<x0>,<y0>,<x1>,<y1>` are absolute coordinates in unrotated PDF
  user-space points (dimension suffixes like `pt`/`in`/`cm`/`mm` are
  accepted, matching `rebox`'s dimension parsing).
- `delete` defines which units are deleted: either those classified as
  inside the box, or those classified as outside the box. Units
  partially inside and partially outside the box (those straddling the
  box boundary) are classifed according to the value of `partial`,
  see below.
- `partial` defines how a partially-overlapping unit is treated:
  with `partial=inside` (default) -- such units are treated the same as
  units that are fully inside the box, whereas with `partial=outside`
  they are treated the same as units that are fully outside the box.
- `delete` and `partial` combine independently. For example:
  `delete=inside,partial=inside` (the default) deletes anything
  touching the box at all. `delete=inside,partial=outside` deletes
  only units entirely inside the box, leaving straddlers and units
  outside untouched. `delete=outside,partial=inside` keeps anything
  touching the box at all. `delete=outside,partial=outside` keeps
  only units entirely inside the box, deleting everything else,
  including straddlers.

## Current limitations

Image, vector path (fill + stroke), and text (glyph-level) deletion are
all implemented, including vertical writing mode. Clipping paths are
never deletion candidates regardless of overlap. Glyph deletion uses each
glyph's nominal 1-em bounding box, not exact outline geometry -- see
_glyph_should_delete's docstring for the tradeoff.
"""


@register_operation(
    "excise",
    tags=["in_place", "content_stream", "custom"],
    type="single input operation",
    desc="Delete page content inside or outside a rectangle",
    long_desc=_LONG_DESC,
    usage="<input> excise <spec> output <output>",
    examples=_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS_EXPANDED], {}),
)
def excise_content(pdf, args) -> OpResult:
    """Entry point registered with the pdftl operation registry."""
    args = args or []
    if not args:
        raise InvalidArgumentError("excise: at least one <pages>(abs,...) spec is required")

    page_rects = _parse_args(args, len(pdf.pages))
    stats = ExciseStats()

    for page_num, excise_rect in page_rects.items():
        _process_page(pdf, page_num, excise_rect, stats)

    _log_stats(stats)
    return OpResult(success=True, pdf=pdf)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def _parse_args(args: list[str], total_pages: int) -> dict[int, ExciseRect]:
    """Parses excise's spec strings into a {page_num (1-based): ExciseRect} map.

    Later specs overwrite earlier ones for the same page number (last
    spec wins), matching the general "later args override" convention
    used by rebox_parser.specs_to_page_rules.
    """
    page_rects: dict[int, ExciseRect] = {}
    for spec in args:
        page_range_str, excise_rect = _parse_single_spec(spec, total_pages)
        for page_num in page_numbers_matching_page_spec(page_range_str, total_pages):
            page_rects[page_num] = excise_rect
    return page_rects


def _parse_single_spec(spec: str, total_pages: int) -> tuple[str, ExciseRect]:
    if "(" not in spec or not spec.endswith(")"):
        raise InvalidArgumentError(
            f"excise: invalid spec '{spec}'. "
            "Expected format like '1-5(abs,10pt,10pt,200pt,100pt)'."
        )
    page_range_str, _, content_str = spec.partition("(")
    content_str = content_str[:-1]  # strip trailing ')'
    parts = [p.strip() for p in content_str.split(",")]

    if not parts or parts[0].lower() != "abs":
        raise InvalidArgumentError(
            f"excise: content '{content_str}' in spec '{spec}' must start with 'abs'."
        )

    delete = "inside"
    partial = "inside"
    numeric_parts = []
    for p in parts[1:]:
        if p.lower().startswith("delete="):
            delete = p.split("=", 1)[1].strip().lower()
        elif p.lower().startswith("partial="):
            partial = p.split("=", 1)[1].strip().lower()
        else:
            numeric_parts.append(p)

    if delete not in ("inside", "outside"):
        raise InvalidArgumentError(
            f"excise: 'delete' must be 'inside' or 'outside', got '{delete}' in spec '{spec}'."
        )
    if partial not in ("inside", "outside"):
        raise InvalidArgumentError(
            f"excise: 'partial' must be 'inside' or 'outside', got '{partial}' in spec '{spec}'."
        )

    if len(numeric_parts) != 4:
        raise InvalidArgumentError(
            f"excise: expected 4 coordinates (x0,y0,x1,y1) in spec '{spec}', "
            f"got {len(numeric_parts)}."
        )

    # Coordinates are absolute in both axes; dim_str_to_pts's second
    # argument (the reference dimension for percentage-based specs) is
    # irrelevant here since excise doesn't support percentage coordinates,
    # so a fixed dummy reference is fine -- percentage values simply
    # aren't part of excise's supported grammar.
    try:
        x0 = dim_str_to_pts(numeric_parts[0], 0.0)
        y0 = dim_str_to_pts(numeric_parts[1], 0.0)
        x1 = dim_str_to_pts(numeric_parts[2], 0.0)
        y1 = dim_str_to_pts(numeric_parts[3], 0.0)
    except (ValueError, TypeError) as e:
        raise InvalidArgumentError(f"excise: invalid coordinate in spec '{spec}': {e}") from e

    rect = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
    return page_range_str or "-", ExciseRect(rect=rect, delete=delete, partial=partial)


# ---------------------------------------------------------------------------
# Page processing
# ---------------------------------------------------------------------------


def _process_page(pdf, page_num: int, excise_rect: ExciseRect, stats: ExciseStats) -> None:
    import pikepdf

    page = pdf.pages[page_num - 1]
    _filter_annots(page, excise_rect, stats)

    if "/Contents" not in page:
        return

    pikepdf.Page(page).contents_coalesce()
    _unshare_page_contents(page, pdf)
    _unshare_page_resources(page, pdf)

    # page.Resources is now a private, unshared dict (see
    # _unshare_page_resources). Any new resource entries excise creates
    # while processing this page's own content stream (e.g.
    # handle_form_do's private filtered Form copies) get written
    # DIRECTLY into this dict -- see process_stream's own-resources-empty
    # branch -- so they land exactly where a real PDF renderer looks for
    # them: the page's own /Resources. Previously such entries were only
    # ever attached to the content STREAM object (stream_obj["/Resources"]),
    # a location page-content-stream readers don't consult per spec --
    # this silently orphaned rewritten Form XObject Do operands, causing
    # real-world viewers (poppler/evince) to render nothing for content
    # that should have partially survived. See git history / roadmap notes
    # for the repro.
    page_resources = page.Resources

    for stream_obj, stream_ctx in walk_content_streams(pdf, page_indices=[page_num]):
        if stream_ctx.kind == "form":
            # Forms are now handled INLINE, recursively, from the actual
            # 'Do' call site inside _interpret_and_filter -- see
            # _handle_form_do. Processing them again here, structurally
            # and at identity CTM, would test their content against the
            # wrong coordinate space (the bug this replaces) and double-
            # process them. Skip entirely.
            continue
        _process_stream(pdf, stream_obj, excise_rect, stats, page_resources)


def _filter_annots(page: Any, excise_rect: ExciseRect, stats: ExciseStats) -> None:
    """Deletes whole Annotations (links, highlights, form fields, etc.)
    whose /Rect overlaps excise_rect, per the same keep=inside/outside
    direction used for page content. Distinct from -- and in addition to
    -- walk_content_streams' handling of an annotation's /AP appearance
    stream CONTENT (which is filtered like any other content stream);
    this instead decides whether the annotation itself should exist at
    all, since a redaction box should remove the underlying link/field,
    not just blank its visible appearance.

    Runs unconditionally (even on a page with no /Contents), since an
    annotation's existence is independent of page content.
    """
    import pikepdf

    if "/Annots" not in page:
        return

    kept: list[Any] = []
    for annot in page.Annots:
        if not isinstance(annot, pikepdf.Object) or "/Rect" not in annot:
            kept.append(annot)
            continue

        stats.annots_total += 1
        try:
            r = [float(x) for x in annot.Rect]
        except (TypeError, ValueError):
            kept.append(annot)
            continue
        bbox = [min(r[0], r[2]), min(r[1], r[3]), max(r[0], r[2]), max(r[1], r[3])]

        if _overlap_means_delete(bbox, excise_rect):
            stats.annots_deleted += 1
            continue
        kept.append(annot)

    page.Annots = pikepdf.Array(kept)


def _unshare_page_contents(page: Any, pdf: Any) -> None:
    """Replaces page.Contents with a freshly allocated stream object
    carrying identical bytes, breaking any incidental sharing with other
    pages' /Contents.

    Real-world PDFs can legitimately have multiple pages reference the
    SAME indirect /Contents stream object (e.g. pages produced by
    duplicate/"chop"-style operations that intentionally share content to
    save space). Since excise mutates a stream in place
    (_process_stream's stream_obj.write(...)), processing one such page
    would otherwise silently corrupt every other page sharing that
    object -- and since only the page actually named in the excise spec
    gets its own private copy here, any other page still sharing the
    ORIGINAL object is left completely untouched, which is the correct
    behavior (only the requested page's content should ever change).
    """
    contents = page.Contents
    raw_bytes = contents.read_bytes()
    page.Contents = pdf.make_stream(raw_bytes)


def _unshare_page_resources(page: Any, pdf: Any) -> None:
    """Replaces page.Resources with a freshly allocated, private dict
    (copied one category-level deep via _copy_resource_dict), breaking
    any incidental sharing with other pages' /Resources.

    Mirrors _unshare_page_contents' rationale, but for /Resources:
    handle_form_do writes new "_excise_..." XObject entries for private,
    filtered Form copies directly into whatever resources dict it's
    handed, and that dict must be page.Resources itself -- not a copy
    that's discarded after this page is processed -- or the new entry is
    invisible to any real PDF renderer resolving this page's own Do
    operators against this page's own /Resources. Since real-world PDFs
    can have multiple pages share the SAME /Resources dict object
    (analogous to the /Contents sharing _unshare_page_contents guards
    against), mutating page.Resources in place without first privatizing
    it here would leak new entries sideways into every other page sharing
    that object.
    """
    import pikepdf

    existing = page.get("/Resources")
    page.Resources = pikepdf.Dictionary({}) if existing is None else _copy_resource_dict(existing)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _log_stats(stats: ExciseStats) -> None:
    logger.info(
        "excise: %d streams processed, %d/%d images, %d/%d annots, %d/%d paths, %d/%d subpaths, "
        "%d/%d glyphs deleted",
        stats.streams_processed,
        stats.images_deleted,
        stats.images_total,
        stats.annots_deleted,
        stats.annots_total,
        stats.paths_deleted,
        stats.paths_total,
        stats.subpaths_deleted,
        stats.paths_total,  # subpaths don't have a separate "total" tally
        stats.glyphs_deleted,
        stats.glyphs_total,
    )
