# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/redact.py

"""Search-and-destroy text redaction: find text matching a pattern,
physically remove the underlying content, and (optionally) paint a
solid box over the gap.

`redact` is deliberately a thin composition of two things that already
exist and are independently tested:

  * `grep`'s search machinery (TextProvider + regex compilation) locates
    every match and its per-line bounding boxes.
  * `excise`'s content-stream deletion engine (_process_page /
    process_stream / the whole ExciseRect-driven walk) does the actual
    removal -- images, vector paths, and glyphs overlapping the match
    boxes are deleted exactly as excise would delete them for a
    hand-specified rectangle. redact_geometry.merge_rects clusters a
    page's many individual match boxes into a small set of union
    regions first (see that module's docstring for why), and the
    resulting multi-box ExciseRect (rect + extra_rects) is handed
    straight to excise's page processor -- no separate deletion logic
    lives here.

The only genuinely new piece of work `redact` does is optionally
painting a box over each merged region afterwards (`box=true`, the
default) -- either burned directly into page content, or onto a named
Optional Content Group layer so it can be toggled/stripped later
(see modify_layers). See _draw_redaction_boxes.

The other genuinely new piece is `verify` (opt-in, default false):
after deleting content for a spec, re-extract text from the page(s)
just modified and re-run search_regex against it. If the pattern can
still match, the underlying text wasn't actually removed (a real
redaction failure -- box drawn over content that's still extractable)
and RedactionVerificationError is raised. See _verify_redaction.
"""

from __future__ import annotations

import logging
from typing import Any

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError, OperationError
from pdftl.utils.dependencies import ensure_dependencies
from pdftl.utils.keyval_parser import parse_keyval_string
from pdftl.utils.page_specs import page_numbers_matching_page_spec
from pdftl.utils.string_utils import split_escaped
from pdftl.utils.pdf_resources import ensure_page_resources, unshare_resources_key

from pdftl.operations.grep import _compile_regex, _parse_bool
from pdftl.operations.helpers.excise_types import ExciseRect, ExciseStats
from pdftl.operations.helpers.redact_geometry import (
    DEFAULT_MERGE_DISTANCE,
    DEFAULT_MERGE_RATIO,
    merge_rects,
)
import pdftl.operations.excise as _excise_mod

logger = logging.getLogger(__name__)

_REDACT_LONG_DESC = r"""
The `redact` operation searches the text content of a PDF for a pattern,
physically deletes the underlying page content (images, vector paths,
and glyphs) wherever it's found, and by default paints a solid box over
each redacted area -- the standard "blacked out" redaction look.

**Note**: This operation requires the 'pypdfium2' library. If not
installed, run: `pip install pdftl[highlight]` (redact shares grep's
and highlight's pypdfium2 dependency).

A redact specification (`<spec>`) has the same shape as `highlight`'s:

>  `[optional page range]<delimiter><regex><delimiter>[<options>]`

`<delimiter>` must be a single, non-alphanumeric character (e.g. `/`).

### Options
- `regex=<b>`: Treat the pattern as a regular expression (default: true).
  If false, matches the pattern as a plain literal string.
- `ignore_case=<b>`: Case-insensitive search (default: false).
- `pad=<F>`: Padding in points added around every matched line's bbox
  before merging/deletion (default: 1.0).
- `merge=<line|area|none>`: How nearby match boxes on the same page are
  combined before deletion (default: `line`).
    - `line`: only merges boxes that plausibly sit on the same printed
      line (y-ranges overlap) and are within `merge_distance` points of
      each other horizontally. Never merges across unrelated rows.
    - `area`: row-agnostic; merges whenever the merged box's area isn't
      much bigger than the sum of the source areas (see `merge_ratio`).
    - `none`: no merging -- one deletion/box per matched line.
- `merge_distance=<F>`: Horizontal gap threshold in points for `merge=line`
  (default: 6.0).
- `merge_ratio=<F>`: Union/sum area ratio ceiling for `merge=area`
  (default: 2.0).
- `box=<b>`: Paint a solid box over each redacted region (default: true).
- `color=<S>`: Space-separated RGB/gray/CMYK values for the box, matching
  `highlight`'s `color=` grammar (default: `0 0 0`, black).
- `opacity=<F>`: Box opacity, 0.0-1.0 (default: 1.0, fully opaque).
- `layer=<S>`: If given, boxes are drawn onto a new Optional Content Group
  (layer) with this name instead of being burned directly into page
  content -- toggleable/strippable afterwards via `modify_layers`.

### Verification
- `verify=<b>` (default: false): After deletion, re-extracts text from
  every page this spec touched and re-runs the same search against it.
  If the pattern still matches anywhere on those pages, the redaction
  is considered to have failed -- the underlying text was not actually
  removed from the content stream -- and `redact` raises
  `OperationError` instead of completing silently. This
  only confirms the pattern is no longer extractable as text; it does
  not inspect non-visible layers, revision history, or embedded files.
  Off by default because it re-serializes and re-scans the PDF, which
  roughly doubles this operation's cost.

### Partial-match redaction with capturing groups
By default, the whole regex match is redacted. Whether that default
applies is decided once per PATTERN (not per match): does the pattern
contain at least one NAMED capturing group whose name starts with
`redact`?

- **No** `redact*`-named group anywhere in the pattern: the whole
  match is always redacted, for every match, regardless of any
  groups `(...)` the pattern happens to use.
- **Yes**, the pattern has one or more `redact*`-named groups: only
  the text captured by those specific groups is redacted, for every
  match. Any other matched text, including other (non-`redact`)
  named groups and plain numbered groups, is left untouched. If none
  of the pattern's `redact*` groups participate in a given match, then
  nothing is redacted for that match. If combined with `verify=true`,
  this may result in a verification failure.

Example: For text "the wombat is a wombat" (using XXX for redacted content):
- `/the (wombat)/` redacts the whole match ("the wombat") -> "XXXXXXXXXX is a wombat".
- `/the (?P<redact_animal>wombat)/` redacts only "wombat" following "the " ->
  "the XXXXXX is a wombat".


### Examples of specifications:
- `/\d{3}-\d{2}-\d{4}/` -- redact every SSN-shaped string, everywhere.
- `1-3/CONFIDENTIAL/(box=false)` -- delete the text but draw no box.
- `/Jane Doe/(color=1 0 0, opacity=0.8, layer=Redactions)`
- `/the (?P<redact_animal>wombat)/` -- redact only "wombat", leaving "the" untouched.
- `/(?P<keep>000-00-0000)|(?P<redact_ssn>\d{3}-\d{2}-\d{4})/` -- redact any SSN except 000-00-0000.
"""

_REDACT_EXAMPLES = [
    {
        "cmd": r"in.pdf redact '/\d{3}-\d{2}-\d{4}/' output out.pdf",
        "desc": "Find and black out every SSN-shaped string in the document.",
    },
    {
        "cmd": "in.pdf redact '1-3/CONFIDENTIAL/(color=1 0 0)' output out.pdf",
        "desc": "Redact 'CONFIDENTIAL' on pages 1-3 with a red box instead of black.",
    },
]

_ALLOWED_OPTION_KEYS = [
    "regex",
    "ignore_case",
    "pad",
    "box_pad",
    "merge",
    "merge_distance",
    "merge_ratio",
    "box",
    "color",
    "opacity",
    "layer",
    "verify",
]


# ---------------------------------------------------------------------------
# Spec parsing
# ---------------------------------------------------------------------------


def _find_options_part(s: str) -> tuple[str, str]:
    """Extracts a balanced trailing (...) options block. Identical logic to
    highlight._find_options_part; duplicated rather than imported since
    that function is highlight-module-private and this is the one piece
    of highlight's parsing redact actually needs verbatim -- pulling in
    the rest of highlight.py for one helper isn't worth the coupling."""
    options_part = ""
    rest_of_spec = s
    if not s.endswith(")"):
        return options_part, rest_of_spec

    nest_level = 0
    split_pos = -1
    for i in range(len(s) - 1, -1, -1):
        char = s[i]
        if char == ")":
            nest_level += 1
        elif char == "(":
            nest_level -= 1

        if nest_level == 0 and char == "(":
            split_pos = i
            break

    if split_pos != -1:
        options_part = s[split_pos:].strip()
        rest_of_spec = s[:split_pos].strip()

    return options_part, rest_of_spec


def _parse_color(color_str: str) -> list[float]:
    try:
        parts = [float(v) for v in color_str.split()]
    except ValueError as exc:
        raise InvalidArgumentError(f"redact: invalid characters in color '{color_str}'") from exc
    if len(parts) not in (1, 3, 4):
        raise InvalidArgumentError(
            f"redact: color '{color_str}' must have 1, 3, or 4 space-separated numbers."
        )
    return parts


def _parse_positive_float(kv: dict, key: str, default: float) -> float:
    if key not in kv:
        return default
    try:
        val = float(kv[key])
        if val < 0:
            raise ValueError
        return val
    except ValueError as exc:
        raise InvalidArgumentError(f"redact: {key} must be a non-negative number.") from exc


def _parse_opacity(kv: dict) -> float:
    if "opacity" not in kv:
        return 1.0
    try:
        val = float(kv["opacity"])
    except ValueError as exc:
        raise InvalidArgumentError(
            "redact: opacity must be a number between 0.0 and 1.0."
        ) from exc
    if not (0.0 <= val <= 1.0):
        raise InvalidArgumentError("redact: opacity must be a number between 0.0 and 1.0.")
    return val


def _parse_merge_mode(kv: dict) -> str:
    mode = kv.get("merge", "line").lower()
    if mode not in ("line", "area", "none"):
        raise InvalidArgumentError(
            f"redact: merge must be 'line', 'area', or 'none', got '{mode}'."
        )
    return mode


class RedactOptions:
    """Parsed, typed options for one redact spec."""

    __slots__ = (
        "use_regex",
        "ignore_case",
        "pad",
        "box_pad",
        "merge_mode",
        "merge_distance",
        "merge_ratio",
        "draw_box",
        "color",
        "opacity",
        "layer",
        "verify",
    )

    def __init__(self, kv: dict) -> None:
        self.use_regex = _parse_bool(kv, "regex", True)
        self.ignore_case = _parse_bool(kv, "ignore_case", False)
        # `pad` controls the rect handed to excise for DELETION. Padding
        # this beyond the matched line's true bbox reaches into whatever
        # sits just past the match -- for tightly-kerned intra-word gaps
        # (e.g. the "i" in "their" right after a redacted "the") even
        # 1pt of pad is often enough to delete a neighboring, untouched
        # glyph. Deletion wants a tight (default zero) box; a generous
        # visual margin belongs to `box_pad` instead (see below), not here.
        self.pad = _parse_positive_float(kv, "pad", 0.0)
        # `box_pad` controls only the painted black-box rect, kept
        # separate from `pad` so the box can still fully cover
        # antialiasing fuzz / italic overhang / thin serifs without that
        # same margin being used to decide what content gets deleted.
        # Defaults to redact's old single `pad` default (1.0), since that
        # value was previously tuned for -- and only actually needed by
        # -- the box-drawing case.
        self.box_pad = _parse_positive_float(kv, "box_pad", 1.0)
        self.merge_mode = _parse_merge_mode(kv)
        self.merge_distance = _parse_positive_float(kv, "merge_distance", DEFAULT_MERGE_DISTANCE)
        self.merge_ratio = _parse_positive_float(kv, "merge_ratio", DEFAULT_MERGE_RATIO)
        self.draw_box = _parse_bool(kv, "box", True)
        self.color = _parse_color(kv.get("color", "0 0 0"))
        self.opacity = _parse_opacity(kv)
        self.layer = kv.get("layer")
        self.verify = _parse_bool(kv, "verify", False)


def _parse_options(options_part: str) -> RedactOptions:
    if not options_part:
        return RedactOptions({})
    content = options_part[1:-1].strip()
    kv = parse_keyval_string(content, allowed_keys=_ALLOWED_OPTION_KEYS, context="redact")
    return RedactOptions(kv)


def _parse_redact_spec(spec: str) -> tuple[str, str, RedactOptions]:
    """Splits one raw spec into (page_spec, regex_str, options). Mirrors
    highlight._parse_highlight_spec's grammar exactly (same delimiter
    syntax), but returns a typed RedactOptions instead of a raw dict."""
    if not spec:
        raise InvalidArgumentError("redact: empty specification.")

    options_part, rest_of_spec = _find_options_part(spec)
    if not rest_of_spec:
        raise InvalidArgumentError(f"redact: missing regex in spec '{spec}'")

    delim = rest_of_spec[-1]
    spec_parts = split_escaped(rest_of_spec, delim)
    if len(spec_parts) != 3:
        raise InvalidArgumentError(
            f"redact: specification '{rest_of_spec}' does not look correct. "
            "Expected format: [page_range]<delim><regex><delim>[options]"
        )

    page_spec = spec_parts[0] if spec_parts[0] else "1-end"
    regex_str = spec_parts[1]
    options = _parse_options(options_part)
    return page_spec, regex_str, options


# ---------------------------------------------------------------------------
# Match collection
# ---------------------------------------------------------------------------


def _pad_rect(rect: list[float], pad: float) -> list[float]:
    return [rect[0] - pad, rect[1] - pad, rect[2] + pad, rect[3] + pad]


def _match_target_spans(match: Any) -> list[tuple[int, int]]:
    """Determines the (start, end) text-offset span(s) to redact for a
    single regex match (see redact's long_desc). The rule is a
    PATTERN-level decision, not a per-match one:

      - If the PATTERN (match.re.groupindex, fixed for every match of
        this compiled regex) contains at least one group named with a
        'redact' prefix: ONLY spans of `redact*` groups that
        PARTICIPATED in this specific match are targeted. If none
        participated, this returns an EMPTY list -- deliberately NOT a
        whole-match fallback. Using `redact*` naming is an explicit
        opt-in to fine-grained targeting; a non-participating
        `redact*` group (e.g. the non-taken side of an alternation)
        means this match wasn't the kind meant to be targeted at all,
        not "redact everything instead."
      - Otherwise (no `redact*`-named group anywhere in the pattern --
        covers no groups at all, plain numbered groups, and any other
        named groups not prefixed `redact`): the whole match is always
        targeted. Plain numbered/other-named groups are NOT a
        narrowing signal by themselves.

    match.re.groupindex is a property of the compiled pattern, so
    checking it for a 'redact' prefix is the same for every match
    produced by this regex -- it is not itself a per-match test.
    """
    redact_group_names = [name for name in match.re.groupindex if name.startswith("redact")]
    if redact_group_names:
        return [match.span(name) for name in redact_group_names if match.group(name) is not None]

    return [match.span()]


def _collect_raw_match_rects(tp: Any, page_num: int, search_regex: Any) -> list[list[list[float]]]:
    """All UNPADDED per-line bboxes for every REDACTION TARGET SPAN of
    every match of search_regex on one 0-indexed page, GROUPED BY SPAN.
    A single match ordinarily produces one span (the whole match), but
    group-based targeting (_match_target_spans) can split one match
    into several independent spans -- e.g. two named `redact*` groups
    in one match yield two groups here, each covering only its own
    captured text, with any non-target text in between left alone.
    Each inner list is the (possibly multi-line) set of bboxes
    belonging to one such span -- callers must merge WITHIN a group
    only, never across groups, or unrelated spans/matches on nearby
    lines get fused into one box/deletion region that can swallow
    untouched text sitting between them. Callers pad each group
    independently for the deletion rect (options.pad) and the drawn
    box rect (options.box_pad), since those two purposes now use
    different pad values (see RedactOptions)."""
    text = tp.get_text(page_num)
    groups: list[list[list[float]]] = []
    for match in search_regex.finditer(text):
        for start, end in _match_target_spans(match):
            if start == end:
                continue
            groups.append(tp.get_bboxes_for_lines(page_num, start, end))
    return groups


# ---------------------------------------------------------------------------
# Box drawing
# ---------------------------------------------------------------------------


def _color_operator(color: list[float]) -> str:
    """Fill-color-setting operator string matching highlight's color
    grammar: 1 number = gray (g), 3 = RGB (rg), 4 = CMYK (k)."""
    if len(color) == 1:
        return f"{color[0]} g"
    if len(color) == 3:
        return f"{color[0]} {color[1]} {color[2]} rg"
    return f"{color[0]} {color[1]} {color[2]} {color[3]} k"


def _box_fill_snippet(rects: list[list[float]], opacity: float, gs_name: str | None) -> str:
    """Raw content-stream code painting solid-filled `rects`, wrapped in
    its own q/Q so it can't leak graphics state into surrounding content."""
    lines = ["q"]
    if gs_name is not None:
        lines.append(f"/{gs_name} gs")
    for x0, y0, x1, y1 in rects:
        w, h = x1 - x0, y1 - y0
        if w <= 0 or h <= 0:
            continue
        lines.append(f"{x0:.3f} {y0:.3f} {w:.3f} {h:.3f} re f")
    lines.append("Q")
    return "\n".join(lines)


def _ensure_ext_gstate(page: Any, opacity: float) -> str | None:
    """Registers a private /ExtGState entry for `opacity` on `page.Resources`
    (only if opacity < 1.0 -- fully opaque needs no ExtGState at all,
    matching stamp_fields._build_appearance_stream's same threshold) and
    returns its resource name, or None if no ExtGState is needed."""
    if opacity >= 1.0:
        return None
    import pikepdf

    resources = ensure_page_resources(page)
    ext_gstate = unshare_resources_key(resources, "/ExtGState")

    name = "RedactGS"
    suffix = 0
    while f"/{name}" in ext_gstate:
        suffix += 1
        name = f"RedactGS{suffix}"

    ext_gstate[f"/{name}"] = pikepdf.Dictionary(
        {"/Type": pikepdf.Name("/ExtGState"), "/ca": opacity, "/CA": opacity}
    )
    return name


def _draw_page_boxes(
    pdf: Any, page: Any, rects: list[list[float]], options: RedactOptions, ocg: Any
) -> None:
    """Appends box-fill content for `rects` to one page's content stream,
    honoring color/opacity/layer -- the tail end of redact's own work,
    everything upstream of this is either grep's or excise's machinery."""
    if not rects:
        return

    gs_name = _ensure_ext_gstate(page, options.opacity)
    color_op = _color_operator(options.color)
    fill_snippet = _box_fill_snippet(rects, options.opacity, gs_name)
    snippet = f"{color_op}\n{fill_snippet}"

    if options.layer is not None:
        prop_key = _register_layer_property(page, ocg)
        snippet = f"/OC /{prop_key} BDC\n{snippet}\nEMC"

    page.contents_add(snippet.encode("utf-8"), prepend=False)


def _register_layer_property(page: Any, ocg: Any) -> str:
    """Registers `ocg` under page.Resources /Properties (creating the
    dict if needed) and returns the resource key to reference it by."""

    resources = ensure_page_resources(page)
    properties = unshare_resources_key(resources, "/Properties")

    key = "RedactLayer"
    suffix = 0
    while f"/{key}" in properties:
        suffix += 1
        key = f"RedactLayer{suffix}"

    properties[f"/{key}"] = ocg
    return key


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def _verify_redaction(pdf: Any, target_pages: list[int], search_regex: Any) -> None:
    """Re-extracts text from `target_pages` on `pdf`'s CURRENT (post-deletion)
    state and re-runs `search_regex` against it. Raises OperationError if
    the pattern still matches anywhere.

    Deliberately does not track which specific spans were originally
    targeted (see module docstring) -- it relies on the pattern itself
    requiring the deleted text to exist. If a match survives, the
    underlying glyphs/content were not actually removed, regardless of
    whether a box was painted over them.

    The error message never includes the matched text itself, only the
    page number and match length -- printing the sensitive text in a
    "verification failed" error would defeat the point of this check.
    """
    tp = _build_text_provider(pdf)
    try:
        failures = []
        for page_1_indexed in target_pages:
            page_num = page_1_indexed - 1
            text = tp.get_text(page_num)
            for match in search_regex.finditer(text):
                failures.append((page_1_indexed, match.end() - match.start()))
    finally:
        tp.close()

    if failures:
        details = "; ".join(f"page {p} ({n} chars)" for p, n in failures)
        raise OperationError(
            f"redact: verification failed -- pattern still matches after "
            f"deletion on: {details}. Underlying content was not fully removed."
        )


# ---------------------------------------------------------------------------
# Main operation
# ---------------------------------------------------------------------------


@register_operation(
    "redact",
    tags=["in_place", "text", "content_stream", "custom"],
    type="single input operation",
    desc="Find and destroy text matching a pattern, optionally boxing it out",
    long_desc=_REDACT_LONG_DESC,
    usage="<input> redact <spec> output <o>",
    examples=_REDACT_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def redact(pdf: Any, specs: list[str]) -> OpResult:
    ensure_dependencies(feature_name="redact", dependencies=["pypdfium2"], extra_tag="highlight")

    if not specs:
        return OpResult(success=True, pdf=pdf)

    for spec in specs:
        _apply_redact_spec(pdf, spec)

    return OpResult(success=True, pdf=pdf)


def _build_text_provider(pdf: Any) -> Any:
    """Serializes `pdf`'s current in-memory state to bytes and opens it
    with pypdfium2, so grep's TextProvider searches reflect any edits
    made by earlier specs in this same `redact` call (rather than the
    original on-disk file)."""
    import io

    import pypdfium2 as pdfium
    from pdftl.utils.pdf_text.text_provider import TextProvider

    pdf_bytes = io.BytesIO()
    pdf.save(pdf_bytes)
    pdf_bytes.seek(0)
    return TextProvider(pdf_path="", opened_pdfium_doc=pdfium.PdfDocument(pdf_bytes))


def _merge_group_rects(
    match_rects: list[list[float]], options: RedactOptions
) -> tuple[list[list[float]], list[list[float]]]:
    """Pads and merges ONE match-target-span's line rects into its
    delete rect(s) and box rect(s), independently -- since `pad` and
    `box_pad` differ and merging must never cross group boundaries
    (see _collect_raw_match_rects). Returns (delete_rects, box_rects)
    for this one group; callers accumulate across all groups on a page."""
    delete_group = [_pad_rect(r, options.pad) for r in match_rects]
    box_group = [_pad_rect(r, options.box_pad) for r in match_rects]

    if options.merge_mode == "none":
        return delete_group, box_group

    merged_delete = merge_rects(
        delete_group,
        mode=options.merge_mode,
        merge_distance=options.merge_distance,
        merge_ratio=options.merge_ratio,
    )
    merged_box = merge_rects(
        box_group,
        mode=options.merge_mode,
        merge_distance=options.merge_distance,
        merge_ratio=options.merge_ratio,
    )
    return merged_delete, merged_box


def _process_redact_page(
    pdf: Any,
    tp: Any,
    page_1_indexed: int,
    search_regex: Any,
    options: RedactOptions,
    ocg: Any,
    stats: ExciseStats,
) -> None:
    """Finds, deletes, and (optionally) boxes every redaction target on
    one page. No-ops if the page has no matches."""
    page_num = page_1_indexed - 1
    raw_match_groups = _collect_raw_match_rects(tp, page_num, search_regex)
    tp.clear_page_cache(page_num)
    if not raw_match_groups:
        return

    merged_delete: list[list[float]] = []
    merged_box: list[list[float]] = []
    for match_rects in raw_match_groups:
        group_delete, group_box = _merge_group_rects(match_rects, options)
        merged_delete.extend(group_delete)
        merged_box.extend(group_box)

    excise_rect = ExciseRect(rect=merged_delete[0], extra_rects=merged_delete[1:] or None)
    _excise_mod._process_page(pdf, page_1_indexed, excise_rect, stats)

    if options.draw_box:
        _draw_page_boxes(pdf, pdf.pages[page_1_indexed - 1], merged_box, options, ocg)


def _apply_redact_spec(pdf: Any, spec: str) -> None:
    page_spec, regex_str, options = _parse_redact_spec(spec)
    search_regex = _compile_regex(regex_str, options.use_regex, options.ignore_case, True, False)

    num_pages = len(pdf.pages)
    target_pages = page_numbers_matching_page_spec(page_spec, num_pages)
    if not target_pages:
        return

    tp = _build_text_provider(pdf)
    ocg = _get_or_create_layer(pdf, options.layer) if options.layer is not None else None
    stats = ExciseStats()

    try:
        for page_1_indexed in target_pages:
            _process_redact_page(pdf, tp, page_1_indexed, search_regex, options, ocg, stats)
    finally:
        tp.close()

    if options.verify:
        _verify_redaction(pdf, target_pages, search_regex)

    logger.info(
        "redact: spec '%s' -- %d streams processed, %d images, %d paths, %d glyphs deleted",
        spec,
        stats.streams_processed,
        stats.images_deleted,
        stats.paths_deleted,
        stats.glyphs_deleted,
    )


def _get_or_create_layer(pdf: Any, layer_name: str) -> Any:
    from pdftl.utils.ocg import create_layer

    return create_layer(pdf, layer_name)
