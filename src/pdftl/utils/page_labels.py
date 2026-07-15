# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

# src/pdftl/utils/page_labels.py

"""Utility functions for preserving and absolute-mapping page labels across modifications."""

import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    import pikepdf

logger = logging.getLogger(__name__)


def get_all_page_label_dicts(pdf: "pikepdf.Pdf") -> list[dict[str, Any] | None]:
    """Resolve the exact absolute label dictionary for every single page index in the PDF.

    Flattens the structural NumberTree into a single sequential list aligned with the page array.
    """
    import pikepdf

    total_pages = len(pdf.pages)
    if "/PageLabels" not in pdf.Root:
        return [None] * total_pages

    try:
        nt = pikepdf.NumberTree(pdf.Root.PageLabels)
        old_rules = {int(k): v for k, v in nt.items()}
    except (pikepdf.PdfError, AttributeError, KeyError, ValueError) as e:
        # Fall back to empty markers if the underlying structural lookup fails or is corrupt
        logger.debug("Failed to parse PageLabels NumberTree: %s", e)
        return [None] * total_pages

    return _flatten_rules(total_pages, old_rules)


def _flatten_rules(total_pages: int, old_rules: dict[int, Any]) -> list[dict[str, Any] | None]:
    """Helper to cleanly flatten individual rules into page-by-page absolute states."""
    import bisect

    sorted_keys = sorted(old_rules.keys())
    page_dicts: list[dict[str, Any] | None] = []

    for i in range(total_pages):
        idx = bisect.bisect_right(sorted_keys, i)
        if idx == 0:
            page_dicts.append(None)
            continue

        active_key = sorted_keys[idx - 1]
        orig_rule = old_rules[active_key]

        orig_start = int(orig_rule.get("/St", 1))
        d: dict[str, Any] = {"St": orig_start + (i - active_key)}
        if "/P" in orig_rule:
            d["P"] = str(orig_rule["/P"])
        if "/S" in orig_rule:
            d["S"] = orig_rule["/S"]
        page_dicts.append(d)

    return page_dicts


def remap_page_labels(
    src_pdf: "pikepdf.Pdf",
    dst_pdf: "pikepdf.Pdf",
    new_to_old: list[Any],
    inherit_style: bool = False,
) -> None:
    """Remap PageLabels ensuring all surviving pages keep their original logical numbers.

    If src_pdf is dst_pdf (the common in-place case), this must be called
    *before* pages are deleted/inserted/moved — this function reads the
    current page count and label tree from src_pdf, so any prior mutation
    of src_pdf's page structure will produce an incorrect mapping.

    Args:
        src_pdf: The source pikepdf.Pdf instance to pull original labels from.
        dst_pdf: The target pikepdf.Pdf instance to write the updated labels to.
        new_to_old: A list where an integer denotes a surviving source page index,
            and any other type (or None) represents an inserted page.
        inherit_style: If True, inserted ("foreign") pages continue the label
            style/sequence of the preceding surviving page. If False (default),
            inserted pages get plain sequential decimal labels independent of
            surrounding context.
    """

    if src_pdf is dst_pdf:
        n = len(src_pdf.pages)
        bad = [x for x in new_to_old if isinstance(x, int) and not (0 <= x < n)]
        if bad:
            raise ValueError(
                f"new_to_old contains indices out of range for src_pdf's current "
                f"page count ({n}); remap_page_labels must be called before "
                f"mutating src_pdf when src_pdf is dst_pdf."
            )

    src_page_dicts = get_all_page_label_dicts(src_pdf)
    # If the source document has no page labels at all, there's no existing
    # scheme to preserve or extend for surviving pages, and no context to
    # borrow for inserted pages either — leave dst without page labels
    # rather than synthesizing one out of thin air.
    if all(d is None for d in src_page_dicts):
        _write_page_labels(dst_pdf, {})
        return

    dst_page_dicts = _build_dst_page_dicts(src_page_dicts, new_to_old, inherit_style)
    new_rules = _compress_rules(dst_page_dicts)

    _write_page_labels(dst_pdf, new_rules)


def _build_dst_page_dicts(
    src_page_dicts: list[dict[str, Any] | None],
    new_to_old: list[Any],
    inherit_style: bool = False,
) -> list[dict[str, Any] | None]:
    """Maps destination indices to their corresponding page label definitions."""
    dst_page_dicts: list[dict[str, Any] | None] = []
    for new_idx, item in enumerate(new_to_old):
        if isinstance(item, int) and 0 <= item < len(src_page_dicts):
            dst_page_dicts.append(src_page_dicts[item])
        else:
            dst_page_dicts.append(None)  # placeholder for inserted page, filled below

    if inherit_style:
        _fill_inserted_inherited(dst_page_dicts, new_to_old, src_page_dicts)
    else:
        _fill_inserted_plain(dst_page_dicts, new_to_old, src_page_dicts)

    return dst_page_dicts


def _fill_inserted_plain(
    dst_page_dicts: list[dict[str, Any] | None],
    new_to_old: list[Any],
    src_page_dicts: list[dict[str, Any] | None],
) -> None:
    """Option 2 (default): inserted pages get plain sequential decimal labels,
    independent of any surrounding style. No /S key means default decimal style."""
    for new_idx, item in enumerate(new_to_old):
        if isinstance(item, int) and 0 <= item < len(src_page_dicts):
            continue  # real surviving page, already filled
        dst_page_dicts[new_idx] = {"St": new_idx + 1}


def _find_next_labeled_dict(
    new_to_old: list[Any],
    src_page_dicts: list[dict[str, Any] | None],
    start: int,
) -> dict[str, Any] | None:
    """Looks forward from `start` in new_to_old for the next surviving page
    that has a resolved source label dict."""
    for i in new_to_old[start:]:
        if isinstance(i, int) and 0 <= i < len(src_page_dicts) and src_page_dicts[i] is not None:
            return src_page_dicts[i]
    return None


def _inherited_label_for(
    new_idx: int,
    prev: dict[str, Any] | None,
    nxt: dict[str, Any] | None,
) -> dict[str, Any]:
    """Computes the inherited label dict for one inserted page, given its
    resolved previous and next neighbor label dicts."""
    if prev is not None:
        d: dict[str, Any] = {"St": prev["St"] + 1}
        if "P" in prev:
            d["P"] = prev["P"]
        if "S" in prev:
            d["S"] = prev["S"]
        return d

    if nxt is not None:
        d = {"St": 1}
        if "S" in nxt:
            d["S"] = nxt["S"]
        return d

    return {"St": new_idx + 1}


def _fill_inserted_inherited(
    dst_page_dicts: list[dict[str, Any] | None],
    new_to_old: list[Any],
    src_page_dicts: list[dict[str, Any] | None],
) -> None:
    """Option 1: inserted pages continue the label sequence/style of the
    preceding surviving page (or borrow the following page's style if
    inserted at the very start)."""
    for new_idx, item in enumerate(new_to_old):
        if isinstance(item, int) and 0 <= item < len(src_page_dicts):
            continue  # real page, already filled

        prev = dst_page_dicts[new_idx - 1] if new_idx > 0 else None
        nxt = (
            None
            if prev is not None
            else _find_next_labeled_dict(new_to_old, src_page_dicts, new_idx)
        )

        dst_page_dicts[new_idx] = _inherited_label_for(new_idx, prev, nxt)


def _compress_rules(dst_page_dicts: list[dict[str, Any] | None]) -> dict[int, Any]:
    """Consolidates consecutive, incrementing absolute labels
    back into a sparse layout dictionary."""
    import pikepdf

    new_rules = {}
    current_style: tuple[str | None, Any | None] = (None, None)
    expected_next_st = None
    has_active_style = False

    for i, d in enumerate(dst_page_dicts):
        if d is None:
            if has_active_style:
                new_rules[i] = pikepdf.Dictionary()
                current_style = (None, None)
                expected_next_st = None
                has_active_style = False
            continue

        style_key = (d.get("P"), d.get("S"))
        current_st = d["St"]

        if not has_active_style or style_key != current_style or current_st != expected_next_st:
            rule_args = {}
            if "P" in d:
                rule_args["P"] = d["P"]
            if "S" in d:
                rule_args["S"] = d["S"]
            rule_args["St"] = current_st

            new_rules[i] = pikepdf.Dictionary(**rule_args)
            current_style = style_key
            has_active_style = True

        expected_next_st = current_st + 1

    return new_rules


def _write_page_labels(dst_pdf: "pikepdf.Pdf", new_rules: dict[int, Any]) -> None:
    """Commit the built number tree rules into the destination document root."""
    import pikepdf

    if new_rules:
        # Build a flat leaf NumberTree node manually to avoid the high overhead
        # of item-by-item setter updates in pikepdf.NumberTree.
        nums = []
        for k, v in sorted(new_rules.items()):
            nums.append(k)
            nums.append(v)
        dst_pdf.Root.PageLabels = dst_pdf.make_indirect(
            pikepdf.Dictionary(Nums=pikepdf.Array(nums))
        )
    else:
        if "/PageLabels" in dst_pdf.Root:
            del dst_pdf.Root.PageLabels
