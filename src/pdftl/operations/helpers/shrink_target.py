# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/operations/helpers/shrink_target.py

"""Shrink to a target size: the least lossy rung of a ladder of settings that fits."""

from __future__ import annotations

import io
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pdftl.core.constants as c
from pdftl.core.core_types import OpResult
from pdftl.exceptions import InvalidArgumentError
from pdftl.operations.delete_tags import logger as delete_tags_logger
from pdftl.operations.delete_tags import lost_conformance, warn_lost_conformance
from pdftl.operations.shrink import (
    _PARAM_PASSES,
    LEVEL_PASSES,
    LEVELS,
    ShrinkPlan,
    _parse_args,
    _pass_list,
    apply_plan,
)
from pdftl.utils.arg_helpers import parse_size_to_bytes
from pdftl.utils.keyval_parser import parse_keyval_list

if TYPE_CHECKING:
    from pikepdf import Pdf

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Rung:
    args: tuple[str, ...]
    mrc_args: tuple[str, ...] | None = None
    mrc_guard: bool = True

    @property
    def level(self) -> str:
        return self.args[0]

    def __str__(self) -> str:
        label = " ".join((*self.args, *(self.mrc_args or ())))
        return label if self.mrc_guard else f"{label} (MRC unchecked)"


_EXTREME = Rung(("extreme",))
# Least to most lossy: down the ladder no dpi or quality rises, no tolerance or
# MRC coarseness falls, and no guard comes back on. MRC keeps its edge-loss check
# until the last rung: unchecked, it layers photo pages into backgrounds a quarter
# of their resolution. Rungs past plain extreme are reachable only without a level.
LADDER = (
    Rung(("lossless",)),
    Rung(("balanced",)),
    Rung(("strong",)),
    Rung(("extreme", "quality=75")),
    _EXTREME,
    Rung(("extreme", "quality=45")),
    Rung(
        ("extreme", "dpi=120", "quality=45"),
        ("bg_div=6", "bg_quality=45", "fg_quality=25"),
    ),
    Rung(
        (
            "extreme",
            "dpi=100",
            "quality=35",
            "mono_dpi=200",
            "tolerance=0.3",
            "text_tolerance=0.1",
        ),
        ("bg_div=8", "bg_quality=40", "fg_quality=20"),
    ),
    Rung(
        ("extreme", "dpi=72", "quality=25", "mono_dpi=150", "tolerance=0.5", "text_tolerance=0.2"),
        ("bg_div=10", "bg_quality=30", "fg_quality=15"),
        mrc_guard=False,
    ),
)
_EXTREME_END = LADDER.index(_EXTREME) + 1
# Parameters the ladder sets itself.
_LADDER_KEYS = ("dpi", "mono_dpi", "threshold", "quality", "tolerance", "text_tolerance")
# Categories no rung makes smaller than lossless does.
_LEVERED = {"images", "content_streams", "metadata", "vendor_extension"}
# What rendering the pages to images discards.
_RASTER_DROPS = ("forms", "fonts", "annotations", "tagged_structure")


@dataclass
class Target:
    size: int | None  # bytes; None when given as a percentage
    percent: float | None
    rungs: tuple[Rung, ...]
    skip: set[str]
    include: set[str]
    extra: dict[str, str]  # deflate=, max_stream_size=


def parse_target(args: list[str]) -> Target | None:
    """Return the target request in `args`, or None if there is no `target=`."""
    bare: list[str] = []
    kv = parse_keyval_list(
        args,
        bare_tokens=bare,
        allowed_keys=["skip", "include", "target", *_PARAM_PASSES],
        context="shrink",
    )
    if "target" not in kv:
        return None
    if len(bare) > 1 or (bare and bare[0] not in LEVELS):
        raise InvalidArgumentError(f"shrink: expected one level out of {', '.join(LEVELS)}")
    fixed = [key for key in _LADDER_KEYS if key in kv]
    if fixed:
        raise InvalidArgumentError(
            f"shrink: target= chooses {', '.join(fixed)} itself; leave out {', '.join(fixed)}"
        )
    size, percent = _parse_target_size(kv["target"])
    return Target(
        size=size,
        percent=percent,
        rungs=_rungs_up_to(bare[0] if bare else None),
        skip=_pass_list(kv.get("skip"), "skip"),
        include=_pass_list(kv.get("include"), "include"),
        extra={key: kv[key] for key in ("deflate", "max_stream_size") if key in kv},
    )


def _parse_target_size(raw: str) -> tuple[int | None, float | None]:
    if raw.endswith("%"):
        try:
            percent = float(raw[:-1])
        except ValueError:
            percent = float("nan")
        if not 0 < percent <= 100:
            raise InvalidArgumentError(
                "shrink: a target percentage must be above 0 and at most 100"
            )
        return None, percent
    size = parse_size_to_bytes(raw, context="shrink: target")
    if size <= 0:
        raise InvalidArgumentError("shrink: target must be a positive size")
    return size, None


def _rungs_up_to(level: str | None) -> tuple[Rung, ...]:
    if level is None:
        return LADDER
    if level == "extreme":
        return LADDER[:_EXTREME_END]
    cap = LEVELS.index(level)
    return tuple(r for r in LADDER if LEVELS.index(r.level) <= cap)


def _rung_args(rung: Rung, target: Target) -> list[str]:
    base = LEVEL_PASSES[rung.level]
    passes = (base - target.skip) | target.include

    def used(arg: str) -> bool:
        key = arg.partition("=")[0]
        return key not in _PARAM_PASSES or bool(passes.intersection(_PARAM_PASSES[key]))

    extra = [f"{key}={value}" for key, value in target.extra.items()]
    args = [arg for arg in (*rung.args, *extra) if used(arg)]
    # Only the selections that change this rung, so no rung warns about the others.
    if skip := sorted(target.skip & base):
        args.append(f"skip={','.join(skip)}")
    if include := sorted(target.include - base):
        args.append(f"include={','.join(include)}")
    return args


@contextmanager
def _quiet(log: logging.Logger):
    """Hold back `log`'s warnings, which every probe would repeat."""
    level = log.level
    log.setLevel(logging.ERROR)
    try:
        yield
    finally:
        log.setLevel(level)


def _probe(source: bytes, hints: dict, rung: Rung, target: Target, out: str):
    """Shrink a fresh copy of `source` at `rung`; return its saved bytes and save hints."""
    import pikepdf

    from pdftl.output.save import save_pdf

    plan: ShrinkPlan = _parse_args(_rung_args(rung, target))
    if rung.mrc_args is not None:
        plan.mrc_args = list(rung.mrc_args)
    plan.mrc_guard = rung.mrc_guard
    with pikepdf.open(io.BytesIO(source)) as work, _quiet(delete_tags_logger):
        setattr(work, c.PDFTL_SAVE_HINTS_ATTR, dict(hints))
        apply_plan(work, plan, out)
        saved_hints = dict(getattr(work, c.PDFTL_SAVE_HINTS_ATTR))
        buf = io.BytesIO()
        save_pdf(work, buf, input_context=None)
    logger.info("shrink: target probe '%s': %d bytes", rung, buf.tell())
    return buf.getvalue(), saved_hints


def _category_bytes(saved: bytes) -> dict[str, int]:
    import pikepdf

    from pdftl.utils.space_usage import analyze_space_usage

    with pikepdf.open(io.BytesIO(saved)) as pdf:
        usage = analyze_space_usage(pdf, saved)
    return {row["id"]: row["bytes"] for row in usage["categories"]}


def _unreachable(saved: bytes, goal: int, target: Target) -> bool:
    """True if what no rung makes smaller already exceeds `goal`."""
    usage = _category_bytes(saved)
    fixed = sum(n for cid, n in usage.items() if cid not in _LEVERED)
    if _runs(target, "drop_xfa"):
        fixed -= _hybrid_xfa_bytes(saved)
    if _runs(target, "delete_tags"):
        fixed -= usage.get("tagged_structure", 0)
    return fixed > goal


def _hybrid_xfa_bytes(saved: bytes) -> int:
    """The stored size of a hybrid form's XFA packets, which drop_xfa removes."""
    import pikepdf

    from pdftl.output.save import has_acroform_widgets

    with pikepdf.open(io.BytesIO(saved)) as pdf:
        acro_form = pdf.Root.get("/AcroForm")
        if not isinstance(acro_form, pikepdf.Dictionary) or not has_acroform_widgets(pdf):
            return 0
        xfa = acro_form.get("/XFA")
        packets = xfa if isinstance(xfa, pikepdf.Array) else [xfa]
        return sum(len(p.read_raw_bytes()) for p in packets if isinstance(p, pikepdf.Stream))


def _passes(rung: Rung, target: Target) -> set[str]:
    return (LEVEL_PASSES[rung.level] - target.skip) | target.include


def _runs(target: Target, name: str) -> bool:
    """Whether some rung of `target` runs pass `name`."""
    return any(name in _passes(r, target) for r in target.rungs)


def _human(nbytes: int) -> str:
    from pdftl.operations.usage import _human_bytes

    return _human_bytes(nbytes)


def _warn_unreachable(goal: int, rung: Rung, data: bytes) -> None:
    usage = _category_bytes(data)
    top = max((cid for cid in usage if cid not in _LEVERED), key=usage.__getitem__)
    message = (
        f"shrink: cannot reach {_human(goal)}; the smallest output, at '{rung}',"
        f" is {_human(len(data))}, {usage[top] * 100 // len(data)}% of it"
        f" {top.replace('_', ' ')}, which no setting makes smaller"
    )
    if sum(usage[cid] for cid in _RASTER_DROPS) * 2 > len(data):
        message += (
            ". Rasterising the pages (render dpi=<n> output <file>, then shrink that file)"
            " goes further, at the cost of selectable text, form fields, links and tags"
        )
    logger.warning(message)


class _Search:
    """Probe rungs, remembering the least lossy fit and the smallest output."""

    def __init__(self, source: bytes, hints: dict, target: Target, goal: int, out: str):
        self.source, self.hints, self.target, self.goal, self.out = (
            source,
            hints,
            target,
            goal,
            out,
        )
        self.fit: tuple[int, bytes, dict] | None = None
        self.smallest: tuple[int, bytes, dict] | None = None

    def fits(self, index: int) -> bool:
        data, hints = _probe(
            self.source, self.hints, self.target.rungs[index], self.target, self.out
        )
        if self.smallest is None or len(data) < len(self.smallest[1]):
            self.smallest = (index, data, hints)
        if len(data) > self.goal:
            return False
        self.fit = (index, data, hints)  # bisection only probes less lossy rungs after a fit
        return True

    def run(self) -> None:
        if self.fits(0) or len(self.target.rungs) == 1:
            return
        last = len(self.target.rungs) - 1
        if _unreachable(self.smallest[1], self.goal, self.target):
            self.fits(last)
            return
        # Sizes fall roughly monotonically down the ladder: bisect for the first fit.
        lo, hi = 1, last
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.fits(mid):
                hi = mid - 1
            else:
                lo = mid + 1


def _source_bytes(pdf: Pdf) -> tuple[bytes, int]:
    """The document as it stands, and the input file's size (for a percentage target)."""
    import os

    buf = io.BytesIO()
    pdf.save(buf)
    data = buf.getvalue()
    name = getattr(pdf, "filename", None)
    try:
        size = os.path.getsize(name) if name else len(data)
    except OSError:
        size = len(data)
    return data, size


def shrink_to_target(pdf: Pdf, target: Target, output_filename: str) -> OpResult:
    """Return the least lossy of the target's rungs that saves within the target size."""
    import pikepdf

    hints = dict(getattr(pdf, c.PDFTL_SAVE_HINTS_ATTR, None) or {})
    source, input_size = _source_bytes(pdf)
    goal = target.size if target.size is not None else int(input_size * target.percent / 100)
    search = _Search(source, hints, target, goal, output_filename)
    search.run()
    chosen = search.fit or search.smallest
    index, data, saved_hints = chosen
    rung = target.rungs[index]
    if "delete_tags" in _passes(rung, target):
        warn_lost_conformance(lost_conformance(pdf))  # once, not per probe
    if search.fit is None:
        _warn_unreachable(goal, rung, data)
    elif index >= _EXTREME_END:
        logger.warning(
            "shrink: reaching %s took settings past extreme ('%s'); expect visible loss",
            _human(goal),
            rung,
        )
    result = pikepdf.open(io.BytesIO(data))
    # The save repeats the chosen rung's save passes; on saved output they change nothing.
    setattr(result, c.PDFTL_SAVE_HINTS_ATTR, saved_hints)
    summary = f"shrink target {goal} bytes: '{rung}' gives {len(data)} bytes"
    logger.info(summary)
    return OpResult(success=True, pdf=result, summary=summary)
