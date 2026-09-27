# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/round_text_positions.py

"""Shorten text positioning numbers, moving no glyph more than a tolerance."""

from __future__ import annotations

import logging
import math
import zlib
from dataclasses import dataclass, field
from typing import Any

import pdftl.core.constants as c
from pdftl.core.core_types import HelpExample, OpResult
from pdftl.core.registry import register_operation
from pdftl.exceptions import InvalidArgumentError
from pdftl.utils.keyval_parser import parse_keyval_list
from pdftl.utils.page_specs import page_numbers_matching_page_spec
from pdftl.utils.pdf_resources import get_resources, walk_content_streams
from pdftl.utils.round_text import IDENTITY, Linear, RoundTextStats, round_text_positions

logger = logging.getLogger(__name__)

DEFAULT_TOLERANCE = 0.005
_MAX_FORM_DEPTH = 12
_MAX_FORM_USES = 256  # past this, a form is left alone rather than tracked further

_LONG_DESC = """
The `round_text_positions` operation makes content streams smaller by
writing text positions with fewer digits. It rounds the kerns inside `TJ`
arrays and the operands of `Td`, and drops kerns too small to matter,
carrying each rounding error forward so that no glyph ever moves more
than `tolerance` points from where it was.

TeX output through dvips and Ghostscript is the main beneficiary: it
positions nearly every glyph with a five-decimal kern.

* `tolerance=<pt>` (default 0.005) -- the most any glyph may move, in
  points on the page. 0.005 pt is 1/14400 inch.

Text, fonts, colours and everything else stay as they are. Tools that
guess word breaks from the gaps between glyphs may, rarely, split or join
a word differently where a gap sits right at their threshold. A content
stream is rewritten only if the result is smaller. Form XObjects are
rewritten when every place they are drawn is on the chosen pages;
forms used by annotations, patterns or soft masks are left alone, as are
text objects containing operators this operation does not model.
"""

_EXAMPLES = [
    HelpExample(
        desc="Round text positions on every page.",
        cmd="in.pdf round_text_positions output out.pdf",
    ),
    HelpExample(
        desc="Allow glyphs to move up to 0.02 pt, on pages 1 to 5.",
        cmd="in.pdf round_text_positions 1-5 tolerance=0.02 output out.pdf",
    ),
]


@dataclass
class _Uses:
    """Where one Form XObject is drawn."""

    matrices: list[Linear] = field(default_factory=list)
    horizontal_scales: set[float | None] = field(default_factory=set)
    eligible: bool = True
    resources: Any = None


@dataclass
class _Totals:
    streams_rewritten: int = 0
    streams_kept: int = 0  # the rewrite was not smaller
    text: RoundTextStats = field(default_factory=RoundTextStats)


@register_operation(
    "round_text_positions",
    tags=["in_place", "content_stream", "text"],
    type="single input operation",
    desc="Shorten text positioning numbers, moving no glyph more than a tolerance",
    long_desc=_LONG_DESC,
    usage="<input> round_text_positions [<pages>...] [tolerance=<pt>] output <output>",
    examples=_EXAMPLES,
    args=([c.INPUT_PDF, c.OPERATION_ARGS], {}),
)
def round_text_positions_op(pdf, args) -> OpResult:
    """Rewrite the chosen pages, then the forms drawn only on them."""
    pages, tolerance = _parse_args(args or [], len(pdf.pages))
    totals = _Totals()
    forms = _collect_form_uses(pdf, set(pages))
    for page_num in pages:
        page = pdf.pages[page_num - 1]
        if "/Contents" in page.obj:
            _rewrite(
                page,
                page.obj,
                [IDENTITY],
                1.0,
                get_resources(page),
                tolerance,
                totals,
                is_page=True,
            )
    for form, uses in forms.values():
        if uses.eligible:
            scales = uses.horizontal_scales
            th = next(iter(scales)) if len(scales) == 1 else None
            _rewrite(form, form, uses.matrices, th, uses.resources, tolerance, totals)
    summary = (
        f"round_text_positions: rewrote {totals.streams_rewritten} stream(s), kept "
        f"{totals.streams_kept} unchanged; {totals.text.numbers_shortened} number(s) shortened"
    )
    logger.info(summary)
    return OpResult(success=True, pdf=pdf, summary=summary)


def _parse_args(args: list[str], num_pages: int) -> tuple[list[int], float]:
    bare: list[str] = []
    kv = parse_keyval_list(
        args, bare_tokens=bare, allowed_keys=["tolerance"], context="round_text_positions"
    )
    tolerance = DEFAULT_TOLERANCE
    if "tolerance" in kv:
        try:
            tolerance = float(kv["tolerance"])
        except ValueError:
            tolerance = math.nan
        if not (math.isfinite(tolerance) and tolerance > 0):
            raise InvalidArgumentError(
                "round_text_positions: tolerance must be a positive number, "
                f"got '{kv['tolerance']}'"
            )
    pages: set[int] = set()
    for spec in bare or ["1-end"]:
        pages.update(page_numbers_matching_page_spec(spec, num_pages))
    return sorted(pages), tolerance


def _font_is_vertical(resources: Any):
    from pdftl.fonts.widths_utils import is_vertical_writing_mode

    fonts = resources.get("/Font") if resources is not None else None

    def is_vertical(name: str) -> bool | None:
        if fonts is None or name not in fonts:
            return None
        return is_vertical_writing_mode(fonts[name])

    return is_vertical


def _rewrite(owner, stream, uses, th, resources, tolerance, totals, is_page=False) -> None:
    import pikepdf

    from pdftl.utils.compact_content import parsed_completely

    try:
        instructions = list(pikepdf.parse_content_stream(owner))
    except pikepdf.PdfError as exc:
        logger.warning("round_text_positions: cannot parse content stream: %s", exc)
        return
    if not parsed_completely(
        b"\n".join(s.read_bytes() for s in _content_streams(stream)), instructions
    ):
        logger.debug("round_text_positions: content stream has unparseable parts; left alone")
        return
    is_vertical = _font_is_vertical(resources)
    best = None  # (compressed size, bytes, stats)
    first_stats = None
    for trial in trial_tolerances(tolerance):
        stats = RoundTextStats()
        new = round_text_positions(
            instructions,
            tolerance=trial,
            is_vertical=is_vertical,
            uses=uses,
            horizontal_scale=th,
            stats=stats,
        )
        first_stats = first_stats or stats
        if not stats.numbers_shortened:
            continue
        new_bytes = pikepdf.unparse_content_stream(new)
        size = len(zlib.compress(new_bytes, 6))
        if best is None or size < best[0]:
            best = (size, new_bytes, stats)
    if best is None:
        _add_stats(totals.text, first_stats)
        return
    size, new_bytes, stats = best
    _add_stats(totals.text, stats)
    old_bytes = b"".join(s.read_bytes() for s in _content_streams(stream))
    if size >= len(zlib.compress(old_bytes, 6)):
        totals.streams_kept += 1
        return
    if is_page:
        pikepdf.Page(owner).contents_coalesce()
        owner.obj.Contents.write(new_bytes)
    else:
        stream.write(new_bytes)
    totals.streams_rewritten += 1


def trial_tolerances(tolerance: float) -> list[float]:
    """The tolerance, then its halvings down to the default.

    A looser bound drops more kerns, but the survivors vary more from line to
    line and can compress worse: the smallest result of these is kept.
    """
    trials = [tolerance]
    while trials[-1] / 2 >= DEFAULT_TOLERANCE - 1e-12:
        trials.append(trials[-1] / 2)
    return trials


def _add_stats(total: RoundTextStats, part: RoundTextStats) -> None:
    for name in vars(part):
        setattr(total, name, getattr(total, name) + getattr(part, name))


def _content_streams(obj: Any) -> list[Any]:
    import pikepdf

    contents = obj.get("/Contents") if not isinstance(obj, pikepdf.Stream) else obj
    if isinstance(contents, pikepdf.Array):
        return list(contents)
    return [contents]


# -- where forms are drawn --


def _collect_form_uses(pdf, chosen: set[int]) -> dict[tuple[int, int], tuple[Any, _Uses]]:
    """Every Form XObject drawn from a page, with the matrix of each use.

    A form is eligible only if every use is on a chosen page and has a
    known matrix, and it is not reachable from an annotation, pattern or
    soft mask, whose drawing matrix this does not follow.
    """
    forms: dict[tuple[int, int], tuple[Any, _Uses]] = {}
    for page_num, page in enumerate(pdf.pages, start=1):
        if "/Contents" in page.obj:
            _Collector(forms, page_num in chosen).walk(page, get_resources(page), IDENTITY, 0)
    for objgen in _forms_outside_page_content(pdf):
        if objgen in forms:
            forms[objgen][1].eligible = False
    return forms


class _Collector:
    def __init__(self, forms, on_chosen_page: bool) -> None:
        self._forms = forms
        self._chosen = on_chosen_page

    def walk(
        self, owner, resources, ctm: Linear | None, depth: int, th: float | None = 1.0
    ) -> None:
        import pikepdf

        try:
            instructions = pikepdf.parse_content_stream(owner)
        except pikepdf.PdfError:
            return
        stack: list[tuple[Linear | None, float | None]] = []
        for operands, op in instructions:
            name = str(op)
            if name == "q":
                stack.append((ctm, th))
            elif name == "Q":
                ctm, th = stack.pop() if stack else (None, None)
            elif name == "cm":
                ctm = _compose(operands, ctm)
            elif name == "Tz":
                th = _scale(operands)
            elif name == "Do" and operands:
                self._use(resources, str(operands[0]), ctm, th, depth)

    def _use(self, resources, name, ctm, th, depth) -> None:
        xobjects = resources.get("/XObject") if resources is not None else None
        if xobjects is None or name not in xobjects:
            return
        form = xobjects[name]
        if form.get("/Subtype") != "/Form":
            return
        _, uses = self._forms.setdefault(form.objgen, (form, _Uses()))
        own = form.get("/Resources")
        uses.resources = own if own is not None else resources
        effective = _compose(list(form.get("/Matrix", [1, 0, 0, 1, 0, 0])), ctm)
        if not self._chosen or effective is None:
            uses.eligible = False
        if effective in uses.matrices and th in uses.horizontal_scales:
            return  # the same use again: nothing new inside
        if effective is not None:
            uses.matrices.append(effective)
        uses.horizontal_scales.add(th)
        if depth >= _MAX_FORM_DEPTH or len(uses.matrices) > _MAX_FORM_USES:
            uses.eligible = False
            return
        self.walk(form, uses.resources, effective, depth + 1, th)


def _compose(operands: list[Any], ctm: Linear | None) -> Linear | None:
    try:
        a, b, cc, d = (float(x) for x in operands[:4])
    except (TypeError, ValueError):
        return None
    if ctm is None or len(operands) != 6:
        return None
    a2, b2, c2, d2 = ctm
    return (a * a2 + b * c2, a * b2 + b * d2, cc * a2 + d * c2, cc * b2 + d * d2)


def _scale(operands: list[Any]) -> float | None:
    try:
        return float(operands[0]) / 100.0
    except (IndexError, TypeError, ValueError):
        return None


def _forms_outside_page_content(pdf) -> set[tuple[int, int]]:
    """Forms reachable from annotation appearances, patterns or soft masks."""
    roots = [
        stream
        for stream, ctx in walk_content_streams(pdf)
        if ctx.kind in ("annotation", "pattern", "smask")
    ]
    found: set[tuple[int, int]] = set()
    pending = list(roots)
    while pending:
        stream = pending.pop()
        resources = stream.get("/Resources")
        xobjects = resources.get("/XObject") if resources is not None else None
        for xobj in (xobjects or {}).values():
            if xobj.get("/Subtype") == "/Form" and xobj.objgen not in found:
                found.add(xobj.objgen)
                pending.append(xobj)
    return found
