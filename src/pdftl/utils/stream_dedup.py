# src/pdftl/utils/stream_dedup.py
"""Generic stream-deduplication machinery: given a list of candidate
streams, find groups that are structurally equivalent (per
check_object_equivalence) and produce a replacement map, then rewrite
every reference to a merged-away stream throughout a document.

Shared by deduplicate_images (image XObjects) and deduplicate_fonts
(embedded FontFile* programs) -- nothing here is specific to either.
Candidate discovery (what counts as a "candidate" stream, and what
scope boundary must never be crossed when merging) stays in each
feature's own module; only the generic sort/compare/rewrite mechanics
live here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pdftl.utils.object_equivalence import check_object_equivalence

if TYPE_CHECKING:
    import pikepdf


def stream_length(obj: pikepdf.Object) -> int:
    """The stream's declared /Length, falling back to the actual raw
    byte count when /Length isn't populated yet.

    Correction to an earlier assumption: /Length being unwritable once
    *present* on a real Stream (pikepdf raises "/Length may not be
    deleted/modified") does NOT mean /Length is always present -- a
    Stream constructed directly via pikepdf.Stream(pdf, data, **kwargs)
    (rather than via pdf.make_stream()) has been observed (pikepdf
    10.11.0) to have no /Length key at all until the Pdf is saved.
    Confirmed via a real test failure, not a theoretical case."""
    declared = obj.get("/Length")
    if declared is not None:
        return int(declared)
    return len(obj.read_raw_bytes())


def rewrite_indirect_ref_or_recurse(container, key_or_idx, val, replacements) -> None:
    """Shared step for one dict-value or array-element slot: swap it for
    its replacement if it's an indirect reference we're merging away,
    otherwise recurse into it if it's a direct nested container."""
    import pikepdf

    if isinstance(val, pikepdf.Object) and val.is_indirect:
        og = val.objgen
        if og in replacements:
            container[key_or_idx] = replacements[og]
    elif isinstance(val, (pikepdf.Dictionary, pikepdf.Stream, pikepdf.Array)):
        rewrite_references(val, replacements)


def rewrite_references(node: pikepdf.Object, replacements: dict) -> None:
    """Replace any indirect reference in `node` matching a key in
    `replacements` with its mapped master object. Recurses into direct
    (non-indirect) nested dictionaries/arrays only -- an indirect
    sub-object is visited separately, at the top level, since the
    caller walks every indirect object in the document."""
    import pikepdf

    if isinstance(node, (pikepdf.Dictionary, pikepdf.Stream)):
        for key in list(node.keys()):
            rewrite_indirect_ref_or_recurse(node, key, node[key], replacements)
    elif isinstance(node, pikepdf.Array):
        for idx in range(len(node)):
            rewrite_indirect_ref_or_recurse(node, idx, node[idx], replacements)


def find_duplicates_of(master, master_len, remaining_candidates, replacements) -> int:
    """Scan `remaining_candidates` (already sorted ascending by length,
    all with index greater than master's) for streams equivalent to
    `master`, recording each as a replacement. Returns bytes reclaimed."""
    bytes_saved = 0
    for candidate in remaining_candidates:
        cand_len = stream_length(candidate)
        if cand_len != master_len:
            break  # sorted ascending -- no further candidate can match
        cand_og = candidate.objgen
        if cand_og in replacements:
            continue
        if check_object_equivalence(candidate, master):
            replacements[cand_og] = master
            # cand_len == master_len here (guaranteed by the sorted
            # same-length scan above), so either would do.
            bytes_saved += cand_len
    return bytes_saved


def build_replacement_map(candidates: list, threshold: int) -> tuple[dict, int]:
    """Walk the length-sorted candidate list once, grouping equivalent
    streams and choosing the first (smallest, or tied-smallest) of each
    group as the surviving master. Returns (replacements, bytes_saved)."""
    replacements: dict[tuple, pikepdf.Object] = {}
    bytes_saved = 0
    for i, master in enumerate(candidates):
        master_len = stream_length(master)
        if master_len < threshold:
            continue
        if master.objgen in replacements:
            continue  # already folded into an earlier master itself
        bytes_saved += find_duplicates_of(master, master_len, candidates[i + 1 :], replacements)
    return replacements, bytes_saved


def apply_replacements(pdf: pikepdf.Pdf, replacements: dict) -> None:
    """Walk every indirect object in the document once, rewriting any
    reference that matches a key in `replacements`."""
    import pikepdf

    seen = set()
    for obj in pdf.objects:
        if not isinstance(obj, pikepdf.Object):
            continue  # e.g. a bare indirect scalar has no .objgen to walk
        og = obj.objgen
        if og in seen:
            continue
        seen.add(og)
        rewrite_references(obj, replacements)
